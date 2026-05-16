from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any, Callable, Sequence

from bindings import build  # type: ignore[attr-defined]

TRACE = 5
logging.addLevelName(TRACE, "TRACE")
LOGGER = logging.getLogger("pywl")

ffi, lib, listen, add_timer = build()

TAG_COUNT = 9
TAG_MASK = (1 << TAG_COUNT) - 1
MOD = lib.WLR_MODIFIER_ALT
BUTTON_MASK = lib.WLR_MODIFIER_ALT | lib.WLR_MODIFIER_SHIFT | lib.WLR_MODIFIER_CTRL | lib.WLR_MODIFIER_LOGO

Color = tuple[float, float, float, float]
Box = tuple[int, int, int, int]


class CursorMode(Enum):
    NORMAL = "normal"
    PRESSED = "pressed"
    MOVE = "move"
    RESIZE = "resize"


class ClientType(Enum):
    XDG_SHELL = "xdg_shell"
    LAYER_SHELL = "layer_shell"
    X11 = "x11"


class Layer(IntEnum):
    BACKGROUND = 0
    BOTTOM = 1
    TILE = 2
    FLOAT = 3
    FULLSCREEN = 4
    TOP = 5
    OVERLAY = 6
    BLOCK = 7


@dataclass(frozen=True)
class Layout:
    symbol: str
    arrange: Callable[[Server, Monitor], None] | None


@dataclass(frozen=True)
class Key:
    mod: int
    sym: int
    func: Callable[[Server, Any], None]
    arg: Any = None


@dataclass(frozen=True)
class Button:
    mod: int
    button: int
    func: Callable[[Server, Any], None]
    arg: Any = None


@dataclass(frozen=True)
class Rule:
    app_id: str | None = None
    title: str | None = None
    tags: int = 0
    floating: bool = False
    monitor: int = -1


@dataclass(frozen=True)
class MonitorRule:
    name: str | None = None
    x: int = -1
    y: int = -1
    scale: float = 1.0
    transform: int = 0
    master_factor: float = 0.55
    num_master: int = 1


@dataclass
class Config:
    border_width: int
    border_color: Color
    focus_color: Color
    urgent_color: Color
    root_color: Color
    repeat_rate: int
    repeat_delay: int
    xkb_rules: dict[str, str | None]
    term_cmd: tuple[str, ...]
    startup_cmd: tuple[str, ...] | None
    keys: list[Key] = field(default_factory=list)
    buttons: list[Button] = field(default_factory=list)
    rules: list[Rule] = field(default_factory=list)
    monitor_rules: list[MonitorRule] = field(default_factory=list)
    layouts: list[Layout] = field(default_factory=list)


@dataclass
class Monitor:
    output: Any
    scene_output: Any
    layout_output: Any
    fullscreen_bg: Any
    num: int
    monitor_box: Box = (0, 0, 0, 0)
    window_box: Box = (0, 0, 0, 0)
    tagsets: list[int] = field(default_factory=lambda: [1, 1])
    selected_tags: int = 0
    layouts: list[Layout] = field(default_factory=list)
    selected_layout: int = 0
    master_factor: float = 0.55
    num_master: int = 1
    layers: list[list[Any]] = field(default_factory=lambda: [[] for _ in range(4)])
    listeners: list[Any] = field(default_factory=list)


@dataclass
class Client:
    xdg_surface: Any
    toplevel: Any
    scene_tree: Any
    scene_surface: Any
    client_type: ClientType
    monitor: Monitor | None
    geometry: Box = (0, 0, 100, 100)
    previous: Box = (0, 0, 100, 100)
    bounds: Box = (0, 0, 0, 0)
    tags: int = 1
    floating: bool = False
    fullscreen: bool = False
    urgent: bool = False
    mapped: bool = False
    resizing: int = 0
    borders: list[Any] = field(default_factory=list)
    listeners: list[Any] = field(default_factory=list)


@dataclass
class Server:
    config: Config
    display: Any = ffi.NULL
    event_loop: Any = ffi.NULL
    backend: Any = ffi.NULL
    session: Any = ffi.NULL
    renderer: Any = ffi.NULL
    allocator: Any = ffi.NULL
    compositor: Any = ffi.NULL
    subcompositor: Any = ffi.NULL
    data_device_manager: Any = ffi.NULL
    output_layout: Any = ffi.NULL
    scene: Any = ffi.NULL
    scene_layout: Any = ffi.NULL
    xdg_shell: Any = ffi.NULL
    seat: Any = ffi.NULL
    cursor: Any = ffi.NULL
    cursor_mgr: Any = ffi.NULL
    keyboard_group: Any = ffi.NULL
    root_bg: Any = ffi.NULL
    locked_bg: Any = ffi.NULL
    drag_icon: Any = ffi.NULL
    layers: dict[Layer, Any] = field(default_factory=dict)
    monitors: list[Monitor] = field(default_factory=list)
    clients: list[Client] = field(default_factory=list)
    fstack: list[Client] = field(default_factory=list)
    listeners: list[Any] = field(default_factory=list)
    client_by_xdg: dict[int, Client] = field(default_factory=dict)
    popup_listeners: dict[int, Any] = field(default_factory=dict)
    running: bool = True
    selected_monitor: Monitor | None = None
    cursor_mode: CursorMode = CursorMode.NORMAL
    grabbed_client: Client | None = None
    grab_cursor: tuple[int, int] = (0, 0)
    repeat_timer: Any = None
    repeat_key: Key | None = None


def default_config() -> Config:
    config = Config(
        border_width=2,
        border_color=(0.25, 0.25, 0.25, 1.0),
        focus_color=(0.3, 0.5, 0.9, 1.0),
        urgent_color=(0.9, 0.2, 0.2, 1.0),
        root_color=(0.1, 0.1, 0.1, 1.0),
        repeat_rate=25,
        repeat_delay=600,
        xkb_rules={"rules": None, "model": None, "layout": None, "variant": None, "options": None},
        term_cmd=(os.environ.get("TERMINAL") or "foot",),
        startup_cmd=None,
        layouts=[],
    )
    config.layouts = [Layout("[]=" , tile), Layout("><>", None), Layout("[M]", monocle)]
    config.keys = make_default_keys(config)
    config.buttons = [
        Button(MOD, lib.BTN_LEFT, begin_move, None),
        Button(MOD, lib.BTN_RIGHT, begin_resize, None),
    ]
    config.monitor_rules = [MonitorRule()]
    return config


def make_default_keys(config: Config) -> list[Key]:
    keys = [
        key(MOD | lib.WLR_MODIFIER_SHIFT, "Return", spawn, config.term_cmd),
        key(MOD | lib.WLR_MODIFIER_SHIFT, "q", quit, None),
        key(MOD, "j", focus_stack, 1),
        key(MOD, "k", focus_stack, -1),
        key(MOD, "h", set_master_factor, -0.05),
        key(MOD, "l", set_master_factor, 0.05),
        key(MOD, "i", inc_num_master, 1),
        key(MOD, "d", inc_num_master, -1),
        key(MOD, "space", set_layout, 0),
        key(MOD | lib.WLR_MODIFIER_SHIFT, "space", toggle_floating, None),
        key(MOD, "f", toggle_fullscreen, None),
        key(MOD, "Tab", zoom, None),
    ]
    for index in range(TAG_COUNT):
        mask = 1 << index
        name = str(index + 1)
        keys.extend([
            key(MOD, name, view, mask),
            key(MOD | lib.WLR_MODIFIER_CTRL, name, toggle_view, mask),
            key(MOD | lib.WLR_MODIFIER_SHIFT, name, tag, mask),
            key(MOD | lib.WLR_MODIFIER_CTRL | lib.WLR_MODIFIER_SHIFT, name, toggle_tag, mask),
        ])
    return [item for item in keys if item is not None]


def key(mod: int, name: str, func: Callable[[Server, Any], None], arg: Any) -> Key | None:
    sym = lib.xkb_keysym_from_name(name.encode(), 0)
    return None if sym == 0 else Key(mod, sym, func, arg)


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=TRACE, format="%(levelname)s:%(name)s:%(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("-s", "--startup", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    config = default_config()
    config.startup_cmd = tuple(args.startup) if args.startup else None
    server = Server(config=config)
    setup(server)
    if config.startup_cmd:
        spawn(server, config.startup_cmd)
    lib.wl_display_run(server.display)
    cleanup(server)
    return 0


def setup(server: Server) -> None:
    install_signal_handlers(server)
    lib.wlr_log_init(2, ffi.NULL)
    server.display = lib.wl_display_create()
    server.event_loop = lib.wl_display_get_event_loop(server.display)
    session_ptr = ffi.new("struct wlr_session **")
    server.backend = lib.wlr_backend_autocreate(server.event_loop, session_ptr)
    server.session = session_ptr[0]
    server.renderer = lib.wlr_renderer_autocreate(server.backend)
    if not server.renderer or not lib.wlr_renderer_init_wl_shm(server.renderer, server.display):
        raise RuntimeError("failed to initialize renderer")
    server.allocator = lib.wlr_allocator_autocreate(server.backend, server.renderer)
    server.compositor = lib.wlr_compositor_create(server.display, 6, server.renderer)
    server.subcompositor = lib.wlr_subcompositor_create(server.display)
    server.data_device_manager = lib.wlr_data_device_manager_create(server.display)
    setup_scene(server)
    setup_outputs(server)
    setup_xdg_shell(server)
    setup_input(server)
    server.listeners.append(listen(lib.pywl_renderer_lost_signal(server.renderer), lambda _data: rebuild_renderer(server)))
    socket = lib.wl_display_add_socket_auto(server.display)
    if socket == ffi.NULL:
        raise RuntimeError("failed to add Wayland socket")
    os.environ["WAYLAND_DISPLAY"] = ffi.string(socket).decode()
    if not lib.wlr_backend_start(server.backend):
        raise RuntimeError("failed to start backend")
    print(f"WAYLAND_DISPLAY={os.environ['WAYLAND_DISPLAY']}", flush=True)


def setup_scene(server: Server) -> None:
    server.output_layout = lib.wlr_output_layout_create(server.display)
    server.scene = lib.wlr_scene_create()
    server.scene_layout = lib.wlr_scene_attach_output_layout(server.scene, server.output_layout)
    server.root_bg = lib.wlr_scene_rect_create(ffi.addressof(server.scene.tree), 0, 0, color_array(server.config.root_color))
    parent = ffi.addressof(server.scene.tree)
    for layer in Layer:
        server.layers[layer] = lib.wlr_scene_tree_create(parent)
    server.drag_icon = lib.wlr_scene_tree_create(parent)
    lib.wlr_scene_node_place_below(ffi.addressof(server.drag_icon.node), ffi.addressof(server.layers[Layer.BLOCK].node))
    server.locked_bg = lib.wlr_scene_rect_create(server.layers[Layer.BLOCK], 0, 0, color_array((0, 0, 0, 1)))
    lib.wlr_scene_node_set_enabled(lib.pywl_scene_rect_node(server.locked_bg), False)


def setup_outputs(server: Server) -> None:
    server.listeners.append(listen(lib.pywl_backend_new_output(server.backend), lambda data: create_monitor(server, data)))
    server.listeners.append(listen(lib.pywl_output_layout_change(server.output_layout), lambda _data: update_monitors(server)))


def setup_xdg_shell(server: Server) -> None:
    server.xdg_shell = lib.wlr_xdg_shell_create(server.display, 6)
    server.listeners.append(listen(lib.pywl_xdg_shell_new_toplevel(server.xdg_shell), lambda data: create_client(server, data)))
    server.listeners.append(listen(lib.pywl_xdg_shell_new_popup(server.xdg_shell), lambda data: create_popup(server, data)))


def setup_input(server: Server) -> None:
    server.seat = lib.wlr_seat_create(server.display, b"seat0")
    lib.wlr_seat_set_capabilities(server.seat, lib.WL_SEAT_CAPABILITY_POINTER | lib.WL_SEAT_CAPABILITY_KEYBOARD)
    server.cursor = lib.wlr_cursor_create()
    lib.wlr_cursor_attach_output_layout(server.cursor, server.output_layout)
    server.cursor_mgr = lib.wlr_xcursor_manager_create(ffi.NULL, 24)
    lib.wlr_xcursor_manager_load(server.cursor_mgr, 1.0)
    lib.wlr_cursor_set_xcursor(server.cursor, server.cursor_mgr, b"default")
    server.keyboard_group = lib.wlr_keyboard_group_create()
    keyboard = lib.pywl_keyboard_group_keyboard(server.keyboard_group)
    set_keymap(server, keyboard)
    lib.wlr_keyboard_set_repeat_info(keyboard, server.config.repeat_rate, server.config.repeat_delay)
    lib.wlr_seat_set_keyboard(server.seat, keyboard)
    server.listeners.extend([
        listen(lib.pywl_backend_new_input(server.backend), lambda data: new_input(server, data)),
        listen(lib.pywl_cursor_motion(server.cursor), lambda data: cursor_motion(server, data)),
        listen(lib.pywl_cursor_motion_absolute(server.cursor), lambda data: cursor_motion_absolute(server, data)),
        listen(lib.pywl_cursor_button(server.cursor), lambda data: cursor_button(server, data)),
        listen(lib.pywl_cursor_axis(server.cursor), lambda data: cursor_axis(server, data)),
        listen(lib.pywl_cursor_frame(server.cursor), lambda _data: lib.wlr_seat_pointer_notify_frame(server.seat)),
        listen(lib.pywl_keyboard_key_signal(keyboard), lambda data: keyboard_key(server, data)),
        listen(lib.pywl_keyboard_modifiers_signal(keyboard), lambda _data: keyboard_modifiers(server)),
        listen(lib.pywl_seat_request_set_cursor(server.seat), lambda data: request_set_cursor(server, data)),
        listen(lib.pywl_seat_request_set_selection(server.seat), lambda data: request_set_selection(server, data)),
        listen(lib.pywl_seat_request_set_primary_selection(server.seat), lambda data: request_set_primary_selection(server, data)),
    ])
    server.repeat_timer = add_timer(server.event_loop, lambda: repeat_binding(server))


def install_signal_handlers(server: Server) -> None:
    signal.signal(signal.SIGPIPE, signal.SIG_IGN)
    signal.signal(signal.SIGINT, lambda _sig, _frame: terminate(server))
    signal.signal(signal.SIGTERM, lambda _sig, _frame: terminate(server))
    signal.signal(signal.SIGCHLD, lambda _sig, _frame: reap_children())


def cleanup(server: Server) -> None:
    cleanup_listeners(server.listeners)
    if server.repeat_timer is not None:
        server.repeat_timer.remove()
        server.repeat_timer = None
    if server.display != ffi.NULL:
        lib.wl_display_destroy_clients(server.display)
    if server.cursor_mgr != ffi.NULL:
        lib.wlr_xcursor_manager_destroy(server.cursor_mgr)
    if server.keyboard_group != ffi.NULL:
        lib.wlr_keyboard_group_destroy(server.keyboard_group)
    if server.backend != ffi.NULL:
        lib.wlr_backend_destroy(server.backend)
    if server.display != ffi.NULL:
        lib.wl_display_destroy(server.display)
    if server.scene != ffi.NULL:
        lib.wlr_scene_node_destroy(ffi.addressof(server.scene.tree.node))


def cleanup_listeners(listeners: list[Any]) -> None:
    for handle in listeners:
        handle.remove()
    listeners.clear()


def terminate(server: Server) -> None:
    server.running = False
    if server.display != ffi.NULL:
        lib.wl_display_terminate(server.display)


def rebuild_renderer(server: Server) -> None:
    new_renderer = lib.wlr_renderer_autocreate(server.backend)
    if not new_renderer or not lib.wlr_renderer_init_wl_shm(new_renderer, server.display):
        return
    new_allocator = lib.wlr_allocator_autocreate(server.backend, new_renderer)
    if not new_allocator:
        lib.wlr_renderer_destroy(new_renderer)
        return
    old_renderer, old_allocator = server.renderer, server.allocator
    server.renderer, server.allocator = new_renderer, new_allocator
    lib.wlr_compositor_set_renderer(server.compositor, new_renderer)
    for monitor in server.monitors:
        lib.wlr_output_init_render(monitor.output, new_allocator, new_renderer)
    lib.wlr_allocator_destroy(old_allocator)
    lib.wlr_renderer_destroy(old_renderer)


def create_monitor(server: Server, data: Any) -> None:
    output = ffi.cast("struct wlr_output *", data)
    if not lib.wlr_output_init_render(output, server.allocator, server.renderer):
        return
    state = lib.pywl_output_state_new()
    rule = monitor_rule_for(server, output)
    monitor = Monitor(
        output=output,
        scene_output=ffi.NULL,
        layout_output=ffi.NULL,
        fullscreen_bg=ffi.NULL,
        num=len(server.monitors),
        layouts=server.config.layouts,
        master_factor=rule.master_factor,
        num_master=rule.num_master,
    )
    monitor.tagsets = [1, 1]
    try:
        lib.wlr_output_state_set_scale(state, rule.scale)
        lib.wlr_output_state_set_transform(state, rule.transform)
        mode = lib.wlr_output_preferred_mode(output)
        if mode != ffi.NULL:
            lib.wlr_output_state_set_mode(state, mode)
        monitor.listeners.extend([
            listen(lib.pywl_output_frame(output), lambda _data: output_frame(server, monitor)),
            listen(lib.pywl_output_destroy_signal(output), lambda _data: cleanup_monitor(server, monitor)),
            listen(lib.pywl_output_request_state(output), lambda data: output_request_state(server, monitor, data)),
        ])
        lib.wlr_output_state_set_enabled(state, True)
        lib.wlr_output_commit_state(output, state)
    finally:
        lib.pywl_output_state_free(state)

    server.monitors.append(monitor)
    server.selected_monitor = server.selected_monitor or monitor
    print_status(server)

    monitor.fullscreen_bg = lib.wlr_scene_rect_create(
        server.layers[Layer.FULLSCREEN],
        0,
        0,
        color_array((0.0, 0.0, 0.0, 1.0)),
    )
    lib.wlr_scene_node_set_enabled(
        lib.pywl_scene_rect_node(monitor.fullscreen_bg),
        False,
    )

    monitor.scene_output = lib.wlr_scene_output_create(server.scene, output)
    if rule.x == -1 and rule.y == -1:
        monitor.layout_output = lib.wlr_output_layout_add_auto(server.output_layout, output)
    else:
        lib.wlr_output_layout_add(server.output_layout, output, rule.x, rule.y)
        monitor.layout_output = lib.wlr_output_layout_get(server.output_layout, output)
    lib.wlr_scene_output_layout_add_output(
        server.scene_layout,
        monitor.layout_output,
        monitor.scene_output,
    )


def cleanup_monitor(server: Server, monitor: Monitor) -> None:
    if monitor not in server.monitors:
        return
    cleanup_listeners(monitor.listeners)
    lib.wlr_output_layout_remove(server.output_layout, monitor.output)
    if monitor.scene_output != ffi.NULL:
        lib.wlr_scene_output_destroy(monitor.scene_output)
        monitor.scene_output = ffi.NULL

    server.monitors.remove(monitor)
    close_monitor(server, monitor)

    if monitor.fullscreen_bg != ffi.NULL:
        lib.wlr_scene_node_destroy(lib.pywl_scene_rect_node(monitor.fullscreen_bg))
        monitor.fullscreen_bg = ffi.NULL


def close_monitor(server: Server, monitor: Monitor) -> None:
    if not server.monitors:
        server.selected_monitor = None
    elif server.selected_monitor is monitor:
        server.selected_monitor = next(
            (item for item in server.monitors if item.output.enabled),
            None,
        )

    for client in server.clients:
        if client.floating and client.geometry[0] > monitor.monitor_box[2]:
            x, y, width, height = client.geometry
            resize(
                server,
                client,
                (x - monitor.window_box[2], y, width, height),
                False,
            )
        if client.monitor is monitor:
            set_monitor(server, client, server.selected_monitor, client.tags)

    if server.selected_monitor:
        arrange(server, server.selected_monitor)
    focus_client(
        server,
        top_client(server, server.selected_monitor) if server.selected_monitor else None,
        True,
    )
    print_status(server)


def update_monitors(server: Server) -> None:
    for monitor in server.monitors:
        if monitor.output.enabled:
            continue
        lib.wlr_output_layout_remove(server.output_layout, monitor.output)
        close_monitor(server, monitor)
        monitor.monitor_box = (0, 0, 0, 0)
        monitor.window_box = (0, 0, 0, 0)

    for monitor in server.monitors:
        if monitor.output.enabled and lib.wlr_output_layout_get(
                server.output_layout,
                monitor.output,
        ) == ffi.NULL:
            monitor.layout_output = lib.wlr_output_layout_add_auto(
                server.output_layout,
                monitor.output,
            )

    full = ffi.new("struct wlr_box *")
    lib.wlr_output_layout_get_box(server.output_layout, ffi.NULL, full)
    lib.wlr_scene_node_set_position(lib.pywl_scene_rect_node(server.root_bg), full.x, full.y)
    lib.wlr_scene_rect_set_size(server.root_bg, full.width, full.height)
    lib.wlr_scene_node_set_position(lib.pywl_scene_rect_node(server.locked_bg), full.x, full.y)
    lib.wlr_scene_rect_set_size(server.locked_bg, full.width, full.height)

    for monitor in server.monitors:
        if not monitor.output.enabled:
            continue
        box = ffi.new("struct wlr_box *")
        lib.wlr_output_layout_get_box(server.output_layout, monitor.output, box)
        monitor.monitor_box = (box.x, box.y, box.width, box.height)
        monitor.window_box = monitor.monitor_box
        lib.wlr_scene_output_set_position(monitor.scene_output, box.x, box.y)
        if monitor.fullscreen_bg != ffi.NULL:
            lib.wlr_scene_node_set_position(
                lib.pywl_scene_rect_node(monitor.fullscreen_bg),
                box.x,
                box.y,
            )
            lib.wlr_scene_rect_set_size(
                monitor.fullscreen_bg,
                box.width,
                box.height,
            )
        arrange(server, monitor)
        fullscreen = top_client(server, monitor)
        if fullscreen and fullscreen.fullscreen:
            resize(server, fullscreen, monitor.monitor_box, False)
        if server.selected_monitor is None:
            server.selected_monitor = monitor

    if server.selected_monitor and server.selected_monitor.output.enabled:
        for client in server.clients:
            if client.monitor is None and client.mapped:
                set_monitor(server, client, server.selected_monitor, client.tags)
        focus_client(server, top_client(server, server.selected_monitor), True)

    lib.wlr_cursor_move(server.cursor, ffi.NULL, 0, 0)


def output_frame(server: Server, monitor: Monitor) -> None:
    should_commit = not any(
        client.resizing
        and not client.floating
        and visible_on(client, monitor)
        for client in server.clients
    )
    if should_commit:
        lib.wlr_scene_output_commit(monitor.scene_output, ffi.NULL)

    now = ffi.new("struct timespec *")
    seconds = time.time()
    now.tv_sec = int(seconds)
    now.tv_nsec = int((seconds - int(seconds)) * 1_000_000_000)
    lib.wlr_scene_output_send_frame_done(monitor.scene_output, now)


def output_request_state(server: Server, monitor: Monitor, data: Any) -> None:
    event = ffi.cast("struct wlr_output_event_request_state *", data)
    lib.wlr_output_commit_state(monitor.output, event.state)
    update_monitors(server)


def create_client(server: Server, data: Any) -> None:
    toplevel = ffi.cast("struct wlr_xdg_toplevel *", data)
    xdg_surface = toplevel.base
    client = Client(
        xdg_surface,
        toplevel,
        ffi.NULL,
        ffi.NULL,
        ClientType.XDG_SHELL,
        None,
    )
    server.client_by_xdg[ptr(xdg_surface)] = client
    client.listeners.extend([
        listen(lib.pywl_surface_commit(xdg_surface.surface), lambda _data: commit_client(server, client)),
        listen(lib.pywl_surface_map(xdg_surface.surface), lambda _data: map_client(server, client)),
        listen(lib.pywl_surface_unmap(xdg_surface.surface), lambda _data: unmap_client(server, client)),
        listen(lib.pywl_xdg_toplevel_destroy(toplevel), lambda _data: cleanup_client(server, client)),
        listen(lib.pywl_xdg_toplevel_set_title(toplevel), lambda _data: update_title(server, client)),
        listen(lib.pywl_xdg_toplevel_set_app_id(toplevel), lambda _data: update_app_id(server, client)),
        listen(lib.pywl_xdg_toplevel_request_maximize(toplevel), lambda _data: request_maximize(client)),
        listen(
            lib.pywl_xdg_toplevel_request_fullscreen(toplevel),
            lambda _data: set_fullscreen(server, client, client.toplevel.requested.fullscreen),
        ),
    ])


def cleanup_client(server: Server, client: Client) -> None:
    cleanup_listeners(client.listeners)
    server.client_by_xdg.pop(ptr(client.xdg_surface), None)


def destroy_client_scene(client: Client) -> None:
    if client.scene_tree != ffi.NULL:
        lib.wlr_scene_node_destroy(ffi.addressof(client.scene_tree.node))
        client.scene_tree = ffi.NULL
        client.scene_surface = ffi.NULL
    client.borders.clear()


def commit_client(server: Server, client: Client) -> None:
    if client.xdg_surface.initial_commit:
        apply_rules(server, client)
        set_monitor(server, client, None, 0)
        lib.wlr_xdg_toplevel_set_wm_capabilities(
            client.toplevel,
            lib.WLR_XDG_TOPLEVEL_WM_CAPABILITIES_FULLSCREEN,
        )
        set_client_size(client, 0, 0)
        return

    resize(server, client, client.geometry, client.floating and not client.fullscreen)
    if client.resizing and client.resizing <= client.xdg_surface.current.configure_serial:
        client.resizing = 0


def map_client(server: Server, client: Client) -> None:
    parent = parent_client(server, client)

    client.scene_tree = lib.wlr_scene_tree_create(server.layers[Layer.TILE])
    client.xdg_surface.surface.data = client.scene_tree
    lib.wlr_scene_node_set_enabled(ffi.addressof(client.scene_tree.node), False)
    client.scene_surface = lib.wlr_scene_xdg_surface_create(
        client.scene_tree,
        client.xdg_surface,
    )

    geometry = client.xdg_surface.geometry
    client.geometry = (geometry.x, geometry.y, geometry.width, geometry.height)

    make_borders(server, client)
    lib.wlr_xdg_toplevel_set_tiled(
        client.toplevel,
        lib.WLR_EDGE_TOP | lib.WLR_EDGE_BOTTOM | lib.WLR_EDGE_LEFT | lib.WLR_EDGE_RIGHT,
    )
    border = server.config.border_width
    x, y, width, height = client.geometry
    client.geometry = (x, y, width + 2 * border, height + 2 * border)

    if client not in server.clients:
        server.clients.insert(0, client)
    if client not in server.fstack:
        server.fstack.insert(0, client)

    client.mapped = True
    if parent:
        client.floating = True
        set_monitor(server, client, parent.monitor, parent.tags)
    else:
        apply_rules(server, client)
    print_status(server)
    clear_obscured_fullscreen_clients(server, client, parent)


def unmap_client(server: Server, client: Client) -> None:
    client.mapped = False
    if server.grabbed_client is client:
        server.cursor_mode = CursorMode.NORMAL
        server.grabbed_client = None

    if client in server.clients:
        server.clients.remove(client)
    set_monitor(server, client, None, 0)
    if client in server.fstack:
        server.fstack.remove(client)

    destroy_client_scene(client)
    print_status(server)
    process_cursor_motion(server, 0)


def create_popup(server: Server, data: Any) -> None:
    popup = ffi.cast("struct wlr_xdg_popup *", data)
    handle = listen(
        lib.pywl_surface_commit(popup.base.surface),
        lambda _data: commit_popup(server, popup),
    )
    server.popup_listeners[ptr(popup)] = handle


def commit_popup(server: Server, popup: Any) -> None:
    if not popup.base.initial_commit:
        return
    parent_client_value = popup_parent_client(server, popup)
    if popup.parent == ffi.NULL or parent_client_value is None:
        return
    popup.base.surface.data = lib.wlr_scene_xdg_surface_create(
        ffi.cast("struct wlr_scene_tree *", popup.parent.data),
        popup.base,
    )
    if parent_client_value.monitor is None:
        return

    box = ffi.new("struct wlr_box *")
    monitor_x, monitor_y, monitor_width, monitor_height = parent_client_value.monitor.window_box
    client_x, client_y, _client_width, _client_height = parent_client_value.geometry
    box.x = monitor_x - client_x
    box.y = monitor_y - client_y
    box.width = monitor_width
    box.height = monitor_height
    lib.wlr_xdg_popup_unconstrain_from_box(popup, box)

    handle = server.popup_listeners.pop(ptr(popup), None)
    if handle:
        handle.remove()


def popup_parent_client(server: Server, popup: Any) -> Client | None:
    root = lib.wlr_surface_get_root_surface(popup.base.surface)
    toplevel = lib.wlr_xdg_toplevel_try_from_wlr_surface(root)
    if toplevel == ffi.NULL:
        parent = lib.wlr_surface_get_root_surface(popup.parent)
        toplevel = lib.wlr_xdg_toplevel_try_from_wlr_surface(parent)
    if toplevel == ffi.NULL:
        return None
    return server.client_by_xdg.get(ptr(toplevel.base))


def apply_rules(server: Server, client: Client) -> None:
    monitor = server.selected_monitor
    new_tags = 0
    for rule in server.config.rules:
        if rule.app_id and rule.app_id not in client_app_id(client):
            continue
        if rule.title and rule.title not in client_title(client):
            continue
        client.floating = rule.floating
        new_tags |= rule.tags & TAG_MASK
        if 0 <= rule.monitor < len(server.monitors):
            monitor = server.monitors[rule.monitor]

    set_monitor(server, client, monitor, new_tags)


def set_monitor(server: Server, client: Client, monitor: Monitor | None, new_tags: int) -> None:
    old_monitor = client.monitor
    if old_monitor is monitor:
        if monitor and new_tags:
            client.tags = new_tags
        return
    client.monitor = monitor
    client.previous = client.geometry

    if old_monitor:
        arrange(server, old_monitor)
    if monitor:
        resize(server, client, client.geometry, False)
        client.tags = new_tags if new_tags else active_tags(monitor)
        set_fullscreen(server, client, client.fullscreen)
        set_floating(server, client, client.floating)
    focus_client(server, top_client(server, server.selected_monitor), True)


def make_borders(server: Server, client: Client) -> None:
    for _ in range(4):
        rect = lib.wlr_scene_rect_create(
            client.scene_tree,
            0,
            0,
            color_array(server.config.border_color),
        )
        client.borders.append(rect)
        lib.wlr_scene_node_set_enabled(lib.pywl_scene_rect_node(rect), False)


def resize(server: Server, client: Client, geometry: Box, interactive: bool) -> None:
    if client.monitor is None or not client.mapped:
        return
    x, y, width, height = geometry
    border = server.config.border_width if should_draw_border(server, client) else 0
    width = max(1 + 2 * border, width)
    height = max(1 + 2 * border, height)
    client.geometry = apply_bounds(
        (x, y, width, height),
        client.monitor.monitor_box if interactive else client.monitor.window_box,
    )
    x, y, width, height = client.geometry

    set_client_bounds(client, width, height)
    lib.wlr_scene_node_set_position(ffi.addressof(client.scene_tree.node), x, y)
    if client.scene_surface != ffi.NULL:
        lib.wlr_scene_node_set_position(
            ffi.addressof(client.scene_surface.node),
            border,
            border,
        )
    set_borders(client, border)
    client.resizing = set_client_size(client, width - 2 * border, height - 2 * border)
    if client.scene_surface != ffi.NULL:
        clip = ffi.new("struct wlr_box *")
        clip.x = client.xdg_surface.geometry.x
        clip.y = client.xdg_surface.geometry.y
        clip.width = width - border
        clip.height = height - border
        lib.wlr_scene_subsurface_tree_set_clip(
            ffi.addressof(client.scene_surface.node),
            clip,
        )


def apply_bounds(geometry: Box, bounds: Box) -> Box:
    x, y, width, height = geometry
    bx, by, bwidth, bheight = bounds
    if x >= bx + bwidth:
        x = bx + bwidth - width
    if y >= by + bheight:
        y = by + bheight - height
    if x + width <= bx:
        x = bx
    if y + height <= by:
        y = by
    return x, y, width, height


def set_client_bounds(client: Client, width: int, height: int) -> int:
    if client.bounds[2] == width and client.bounds[3] == height:
        return 0
    client.bounds = (0, 0, width, height)
    return lib.wlr_xdg_toplevel_set_bounds(client.toplevel, width, height)


def set_client_size(client: Client, width: int, height: int) -> int:
    if client.toplevel.current.width == width and client.toplevel.current.height == height:
        return 0
    return lib.wlr_xdg_toplevel_set_size(client.toplevel, width, height)


def set_borders(client: Client, border: int) -> None:
    _x, _y, width, height = client.geometry
    sizes = [
        (width, border),
        (width, border),
        (border, height - 2 * border),
        (border, height - 2 * border),
    ]
    positions = [
        (0, 0),
        (0, height - border),
        (0, border),
        (width - border, border),
    ]
    for rect, (rw, rh), (rx, ry) in zip(client.borders, sizes, positions):
        node = lib.pywl_scene_rect_node(rect)
        lib.wlr_scene_node_set_enabled(
            node,
            border > 0 and client.mapped and visible_on(client, client.monitor),
        )
        lib.wlr_scene_node_set_position(node, rx, ry)
        lib.wlr_scene_rect_set_size(rect, max(0, rw), max(0, rh))


def arrange(server: Server, monitor: Monitor | None) -> None:
    if monitor is None or not monitor.output.enabled:
        return
    for client in server.clients:
        if client.monitor is monitor and client.scene_tree != ffi.NULL:
            lib.wlr_scene_node_set_enabled(
                ffi.addressof(client.scene_tree.node),
                visible_on(client, monitor),
            )

    focused = top_client(server, monitor)
    if monitor.fullscreen_bg != ffi.NULL:
        lib.wlr_scene_node_set_enabled(
            lib.pywl_scene_rect_node(monitor.fullscreen_bg),
            bool(focused and focused.fullscreen),
        )

    layout = monitor.layouts[monitor.selected_layout]
    for client in server.clients:
        if (client.monitor is not monitor or client.scene_tree == ffi.NULL
                or client.scene_tree.node.parent == server.layers[Layer.FULLSCREEN]):
            continue
        target_layer = client.scene_tree.node.parent
        if layout.arrange is None and client.floating:
            target_layer = server.layers[Layer.TILE]
        elif layout.arrange is not None and client.floating:
            target_layer = server.layers[Layer.FLOAT]
        if client.scene_tree.node.parent != target_layer:
            lib.wlr_scene_node_reparent(
                ffi.addressof(client.scene_tree.node),
                target_layer,
            )

    if layout.arrange:
        layout.arrange(server, monitor)
    process_cursor_motion(server, 0)


def tile(server: Server, monitor: Monitor) -> None:
    tiled = [client for client in clients_on(server, monitor) if not client.floating and not client.fullscreen and visible_on(client, monitor)]
    if not tiled:
        return
    x, y, width, height = monitor.window_box
    master_count = min(monitor.num_master, len(tiled))
    master_width = width if len(tiled) <= master_count else int(width * monitor.master_factor)
    stack_width = width - master_width
    master_y = stack_y = y
    for index, client in enumerate(tiled):
        if index < master_count:
            remaining = master_count - index
            client_height = (y + height - master_y) // remaining
            resize(server, client, (x, master_y, master_width, client_height), False)
            master_y += client_height
        else:
            remaining = len(tiled) - index
            client_height = (y + height - stack_y) // remaining
            resize(server, client, (x + master_width, stack_y, stack_width, client_height), False)
            stack_y += client_height


def monocle(server: Server, monitor: Monitor) -> None:
    for client in clients_on(server, monitor):
        if not client.floating and not client.fullscreen and visible_on(client, monitor):
            resize(server, client, monitor.window_box, False)


def focus_client(server: Server, client: Client | None, lift: bool) -> None:
    old_surface = server.seat.keyboard_state.focused_surface

    if client and lift:
        lib.wlr_scene_node_raise_to_top(ffi.addressof(client.scene_tree.node))

    if client and client.xdg_surface.surface == old_surface:
        return

    old_client = client_from_surface(server, old_surface)

    if client and client.mapped:
        if client in server.fstack:
            server.fstack.remove(client)
        server.fstack.insert(0, client)
        server.selected_monitor = client.monitor
        client.urgent = False

    if old_surface != ffi.NULL and (client is None or client.xdg_surface.surface != old_surface):
        if old_client:
            lib.wlr_xdg_toplevel_set_activated(old_client.toplevel, False)

    print_status(server)

    if client is None or not client.mapped:
        lib.wlr_seat_keyboard_clear_focus(server.seat)
        update_border_colors(server)
        return

    process_cursor_motion(server, 0)

    keyboard = lib.wlr_seat_get_keyboard(server.seat)
    lib.wlr_seat_keyboard_notify_enter(
        server.seat,
        client.xdg_surface.surface,
        keyboard.keycodes,
        keyboard.num_keycodes,
        ffi.addressof(keyboard, "modifiers"),
    )
    lib.wlr_xdg_toplevel_set_activated(client.toplevel, True)
    update_border_colors(server)


def top_client(server: Server, monitor: Monitor) -> Client | None:
    return next((client for client in server.fstack if client.mapped and visible_on(client, monitor)), None)


def focus_stack(server: Server, direction: int) -> None:
    monitor = server.selected_monitor
    if monitor is None:
        return
    selected = top_client(server, monitor)
    if selected is None or selected.fullscreen:
        return
    visible = [
        client for client in server.clients
        if client.mapped and visible_on(client, monitor)
    ]
    if not visible:
        return
    selected_index = visible.index(selected) if selected in visible else 0
    step = 1 if direction > 0 else -1
    target = visible[(selected_index + step) % len(visible)]
    focus_client(server, target, True)


def update_border_colors(server: Server) -> None:
    focused = focused_client(server)
    for client in server.clients:
        color = server.config.focus_color if client is focused else server.config.urgent_color if client.urgent else server.config.border_color
        for rect in client.borders:
            lib.wlr_scene_rect_set_color(rect, color_array(color))


def clear_obscured_fullscreen_clients(
        server: Server,
        client: Client,
        parent: Client | None,
) -> None:
    monitor = client.monitor or monitor_at(server, client.geometry[0], client.geometry[1])
    for other in server.clients:
        if (other is not client and other is not parent and other.fullscreen
                and other.monitor is monitor and other.tags & client.tags):
            set_fullscreen(server, other, False)


def monitor_at(server: Server, x: int, y: int) -> Monitor | None:
    return next(
        (
            monitor for monitor in server.monitors
            if contains_point(monitor.monitor_box, x, y)
        ),
        server.selected_monitor,
    )


def contains_point(box: Box, x: int, y: int) -> bool:
    box_x, box_y, width, height = box
    return box_x <= x < box_x + width and box_y <= y < box_y + height


def parent_client(server: Server, client: Client) -> Client | None:
    parent = client.toplevel.parent
    if parent == ffi.NULL:
        return None
    return server.client_by_xdg.get(ptr(parent.base))


def client_from_surface(server: Server, surface: Any) -> Client | None:
    if surface == ffi.NULL:
        return None
    toplevel = lib.wlr_xdg_toplevel_try_from_wlr_surface(surface)
    if toplevel == ffi.NULL:
        return None
    return server.client_by_xdg.get(ptr(toplevel.base))


def focused_client(server: Server) -> Client | None:
    surface = server.seat.keyboard_state.focused_surface
    if surface == ffi.NULL:
        return None
    toplevel = lib.wlr_xdg_toplevel_try_from_wlr_surface(surface)
    return None if toplevel == ffi.NULL else server.client_by_xdg.get(ptr(toplevel.base))


def view(server: Server, mask: int) -> None:
    monitor = server.selected_monitor
    if monitor and mask & TAG_MASK and mask != active_tags(monitor):
        monitor.selected_tags ^= 1
        monitor.tagsets[monitor.selected_tags] = mask & TAG_MASK
        arrange(server, monitor)
        focus_client(server, top_client(server, monitor), True)


def toggle_view(server: Server, mask: int) -> None:
    monitor = server.selected_monitor
    if monitor:
        new_tags = active_tags(monitor) ^ (mask & TAG_MASK)
        if new_tags:
            monitor.tagsets[monitor.selected_tags] = new_tags
            arrange(server, monitor)
            focus_client(server, top_client(server, monitor), True)


def tag(server: Server, mask: int) -> None:
    client = focused_client(server)
    if client and mask & TAG_MASK:
        client.tags = mask & TAG_MASK
        arrange(server, client.monitor)


def toggle_tag(server: Server, mask: int) -> None:
    client = focused_client(server)
    if client:
        new_tags = client.tags ^ (mask & TAG_MASK)
        if new_tags:
            client.tags = new_tags
            arrange(server, client.monitor)


def set_layout(server: Server, index: int) -> None:
    monitor = server.selected_monitor
    if monitor:
        monitor.selected_layout = (monitor.selected_layout + 1) % len(monitor.layouts) if index == 0 else index % len(monitor.layouts)
        arrange(server, monitor)


def inc_num_master(server: Server, delta: int) -> None:
    monitor = server.selected_monitor
    if monitor:
        monitor.num_master = max(0, monitor.num_master + delta)
        arrange(server, monitor)


def set_master_factor(server: Server, delta: float) -> None:
    monitor = server.selected_monitor
    if monitor:
        monitor.master_factor = min(0.95, max(0.05, monitor.master_factor + delta))
        arrange(server, monitor)


def zoom(server: Server, _arg: Any = None) -> None:
    client = focused_client(server)
    if client and not client.floating and client in server.clients:
        server.clients.remove(client)
        server.clients.insert(0, client)
        arrange(server, client.monitor)


def toggle_floating(server: Server, _arg: Any = None) -> None:
    client = focused_client(server)
    if client:
        set_floating(server, client, not client.floating)


def set_floating(server: Server, client: Client, floating: bool) -> None:
    client.floating = floating
    if (client.monitor is None or not client.mapped
            or client.monitor.layouts[client.monitor.selected_layout].arrange is None):
        return
    parent = parent_client(server, client)
    layer = (
        Layer.FULLSCREEN if client.fullscreen or (parent and parent.fullscreen)
        else Layer.FLOAT if floating
        else Layer.TILE
    )
    lib.wlr_scene_node_reparent(ffi.addressof(client.scene_tree.node), server.layers[layer])
    arrange(server, client.monitor)
    print_status(server)


def toggle_fullscreen(server: Server, _arg: Any = None) -> None:
    client = focused_client(server)
    if client:
        set_fullscreen(server, client, not client.fullscreen)


def request_maximize(client: Client) -> None:
    if client.xdg_surface.initialized:
        lib.wlr_xdg_surface_schedule_configure(client.xdg_surface)


def set_fullscreen(server: Server, client: Client, fullscreen: bool) -> None:
    client.fullscreen = fullscreen
    if client.monitor is None or not client.mapped:
        return
    lib.wlr_xdg_toplevel_set_fullscreen(client.toplevel, fullscreen)
    lib.wlr_scene_node_reparent(
        ffi.addressof(client.scene_tree.node),
        server.layers[
            Layer.FULLSCREEN if client.fullscreen
            else Layer.FLOAT if client.floating
            else Layer.TILE
        ],
    )

    if fullscreen:
        client.previous = client.geometry
        resize(server, client, client.monitor.monitor_box, False)
    else:
        resize(server, client, client.previous, False)
    arrange(server, client.monitor)
    print_status(server)


def focus_monitor(server: Server, direction: int) -> None:
    if not server.monitors:
        return
    current = server.selected_monitor or server.monitors[0]
    server.selected_monitor = server.monitors[(server.monitors.index(current) + direction) % len(server.monitors)]
    focus_client(server, top_client(server, server.selected_monitor), True)


def tag_monitor(server: Server, direction: int) -> None:
    client = focused_client(server)
    if client and server.monitors:
        set_monitor(server, client, monitor_in_direction(server, direction), 0)


def monitor_in_direction(server: Server, direction: int) -> Monitor | None:
    if not server.monitors:
        return None
    current = server.selected_monitor or server.monitors[0]
    return server.monitors[(server.monitors.index(current) + direction) % len(server.monitors)]


def spawn(_server: Server, argv: Sequence[str]) -> None:
    if not argv:
        LOGGER.log(TRACE, "spawn ignored: empty argv")
        return
    LOGGER.log(TRACE, "spawn requested: %r", tuple(argv))
    pid = os.fork()
    if pid != 0:
        LOGGER.log(TRACE, "spawn forked child pid=%d", pid)
        return
    if pid == 0:
        os.setsid()
        os.dup2(1, 2)
        try:
            os.execvp(argv[0], list(argv))
        except OSError as error:
            print(f"exec {argv[0]} failed: {error}", file=sys.stderr)
            os._exit(127)


def quit(server: Server, _arg: Any = None) -> None:
    terminate(server)


def new_input(server: Server, data: Any) -> None:
    device = ffi.cast("struct wlr_input_device *", data)
    if device.type == lib.WLR_INPUT_DEVICE_KEYBOARD:
        keyboard = lib.wlr_keyboard_from_input_device(device)
        group_keyboard = lib.pywl_keyboard_group_keyboard(server.keyboard_group)
        if group_keyboard.keymap != ffi.NULL:
            lib.wlr_keyboard_set_keymap(keyboard, group_keyboard.keymap)
        lib.wlr_keyboard_group_add_keyboard(server.keyboard_group, keyboard)
    elif device.type == lib.WLR_INPUT_DEVICE_POINTER:
        lib.wlr_cursor_attach_input_device(server.cursor, device)

    capabilities = lib.WL_SEAT_CAPABILITY_POINTER
    if lib.pywl_keyboard_group_keyboard(server.keyboard_group).keymap != ffi.NULL:
        capabilities |= lib.WL_SEAT_CAPABILITY_KEYBOARD
    lib.wlr_seat_set_capabilities(server.seat, capabilities)


def keyboard_key(server: Server, data: Any) -> None:
    event = ffi.cast("struct wlr_keyboard_key_event *", data)
    keyboard = lib.pywl_keyboard_group_keyboard(server.keyboard_group)
    sym = lib.pywl_keyboard_keysym(keyboard, event.keycode)
    modifiers = lib.wlr_keyboard_get_modifiers(keyboard)
    pressed = event.state == lib.WL_KEYBOARD_KEY_STATE_PRESSED

    handled = pressed and key_binding(server, sym, modifiers)

    repeat_delay = keyboard.repeat_info.delay
    if handled and repeat_delay > 0:
        server.repeat_timer.update(repeat_delay)
    else:
        server.repeat_key = None
        server.repeat_timer.update(0)

    if handled:
        return

    lib.wlr_seat_set_keyboard(server.seat, keyboard)
    lib.wlr_seat_keyboard_notify_key(server.seat, event.time_msec, event.keycode, event.state)


def key_binding(server: Server, sym: int, modifiers: int) -> bool:
    binding_mods = modifiers & BUTTON_MASK
    for binding in server.config.keys:
        if binding.sym == sym and binding.mod == binding_mods:
            binding.func(server, binding.arg)
            server.repeat_key = binding
            return True
    return False


def keyboard_modifiers(server: Server) -> None:
    keyboard = lib.pywl_keyboard_group_keyboard(server.keyboard_group)
    lib.wlr_seat_set_keyboard(server.seat, keyboard)
    lib.wlr_seat_keyboard_notify_modifiers(server.seat, ffi.addressof(keyboard, "modifiers"))


def repeat_binding(server: Server) -> None:
    if server.repeat_key is not None:
        server.repeat_key.func(server, server.repeat_key.arg)
        if server.config.repeat_rate > 0:
            server.repeat_timer.update(int(1000 / server.config.repeat_rate))


def set_keymap(server: Server, keyboard: Any) -> None:
    context = lib.xkb_context_new(0)
    names = ffi.new("struct xkb_rule_names *")
    encoded = []
    for field_name in ("rules", "model", "layout", "variant", "options"):
        value = server.config.xkb_rules.get(field_name)
        encoded.append(ffi.new("char[]", value.encode()) if value else ffi.NULL)
    names.rules, names.model, names.layout, names.variant, names.options = encoded
    keymap = lib.xkb_keymap_new_from_names(context, names, 0)
    if keymap != ffi.NULL:
        lib.wlr_keyboard_set_keymap(keyboard, keymap)
        lib.xkb_keymap_unref(keymap)
    lib.xkb_context_unref(context)


def cursor_motion(server: Server, data: Any) -> None:
    event = ffi.cast("struct wlr_pointer_motion_event *", data)
    device = ffi.addressof(event.pointer.base)
    process_cursor_motion(
        server, event.time_msec, device, event.delta_x, event.delta_y,
    )


def cursor_motion_absolute(server: Server, data: Any) -> None:
    event = ffi.cast("struct wlr_pointer_motion_absolute_event *", data)
    device = ffi.addressof(event.pointer.base)
    if event.time_msec == 0:
        lib.wlr_cursor_warp_absolute(server.cursor, device, event.x, event.y)
    lx = ffi.new("double *")
    ly = ffi.new("double *")
    lib.wlr_cursor_absolute_to_layout_coords(
        server.cursor, device, event.x, event.y, lx, ly,
    )
    process_cursor_motion(
        server, event.time_msec, device,
        lx[0] - server.cursor.x, ly[0] - server.cursor.y,
    )


def cursor_button(server: Server, data: Any) -> None:
    event = ffi.cast("struct wlr_pointer_button_event *", data)

    if event.state == lib.WL_POINTER_BUTTON_STATE_PRESSED:
        server.cursor_mode = CursorMode.PRESSED
        new_monitor = monitor_at(server, int(server.cursor.x), int(server.cursor.y))
        if new_monitor:
            server.selected_monitor = new_monitor

        hit = node_at(server, server.cursor.x, server.cursor.y)
        if hit and hit[3] is not None:
            focus_client(server, hit[3], True)

        keyboard = lib.wlr_seat_get_keyboard(server.seat)
        modifiers = lib.wlr_keyboard_get_modifiers(keyboard) if keyboard != ffi.NULL else 0
        for binding in server.config.buttons:
            if (binding.button == event.button
                    and binding.mod == (modifiers & BUTTON_MASK)):
                binding.func(server, binding.arg)
                return
    elif server.cursor_mode not in (CursorMode.NORMAL, CursorMode.PRESSED):
        lib.wlr_cursor_set_xcursor(server.cursor, server.cursor_mgr, b"default")
        server.cursor_mode = CursorMode.NORMAL
        new_monitor = monitor_at(server, int(server.cursor.x), int(server.cursor.y))
        if new_monitor:
            server.selected_monitor = new_monitor
        if server.grabbed_client:
            set_monitor(server, server.grabbed_client, server.selected_monitor, 0)
        server.grabbed_client = None
        return
    else:
        server.cursor_mode = CursorMode.NORMAL

    lib.wlr_seat_pointer_notify_button(
        server.seat, event.time_msec, event.button, event.state,
    )


def cursor_axis(server: Server, data: Any) -> None:
    event = ffi.cast("struct wlr_pointer_axis_event *", data)
    lib.wlr_seat_pointer_notify_axis(server.seat, event.time_msec, event.orientation, event.delta, event.delta_discrete, event.source, event.relative_direction)


def process_cursor_motion(
        server: Server,
        time_msec: int = 0,
        device: Any = ffi.NULL,
        dx: float = 0.0,
        dy: float = 0.0,
) -> None:
    hit = node_at(server, server.cursor.x, server.cursor.y)
    surface = hit[0] if hit else ffi.NULL
    sx = hit[1] if hit else 0.0
    sy = hit[2] if hit else 0.0
    client = hit[3] if hit else None

    pointer_focused = server.seat.pointer_state.focused_surface
    if server.cursor_mode == CursorMode.PRESSED and surface != pointer_focused:
        focused_client = client_from_surface(server, pointer_focused)
        if focused_client and focused_client.scene_tree != ffi.NULL:
            client = focused_client
            surface = pointer_focused
            sx = server.cursor.x - focused_client.scene_tree.node.x
            sy = server.cursor.y - focused_client.scene_tree.node.y

    if time_msec:
        lib.wlr_cursor_move(server.cursor, device, dx, dy)
        new_monitor = monitor_at(server, int(server.cursor.x), int(server.cursor.y))
        if new_monitor:
            server.selected_monitor = new_monitor

    if server.cursor_mode == CursorMode.MOVE and server.grabbed_client:
        offset_x, offset_y = server.grab_cursor
        _gx, _gy, width, height = server.grabbed_client.geometry
        resize(
            server,
            server.grabbed_client,
            (round(server.cursor.x) - offset_x, round(server.cursor.y) - offset_y, width, height),
            True,
        )
        return
    if server.cursor_mode == CursorMode.RESIZE and server.grabbed_client:
        x, y, _width, _height = server.grabbed_client.geometry
        resize(
            server,
            server.grabbed_client,
            (x, y, round(server.cursor.x) - x, round(server.cursor.y) - y),
            True,
        )
        return

    if surface == ffi.NULL:
        lib.wlr_cursor_set_xcursor(server.cursor, server.cursor_mgr, b"default")

    pointer_focus(server, client, surface, sx, sy, time_msec)


def pointer_focus(
        server: Server,
        _client: Client | None,
        surface: Any,
        sx: float,
        sy: float,
        time_msec: int,
) -> None:
    if surface == ffi.NULL:
        lib.wlr_seat_pointer_clear_focus(server.seat)
        return
    lib.wlr_seat_pointer_notify_enter(server.seat, surface, sx, sy)
    lib.wlr_seat_pointer_notify_motion(server.seat, time_msec, sx, sy)


def begin_move(server: Server, _arg: Any = None) -> None:
    begin_move_resize(server, CursorMode.MOVE)


def begin_resize(server: Server, _arg: Any = None) -> None:
    begin_move_resize(server, CursorMode.RESIZE)


def begin_move_resize(server: Server, mode: CursorMode) -> None:
    if server.cursor_mode not in (CursorMode.NORMAL, CursorMode.PRESSED):
        return
    hit = node_at(server, server.cursor.x, server.cursor.y)
    client = hit[3] if hit else None
    if client is None or client.fullscreen:
        return

    set_floating(server, client, True)
    server.cursor_mode = mode
    server.grabbed_client = client
    if mode == CursorMode.MOVE:
        server.grab_cursor = (
            round(server.cursor.x) - client.geometry[0],
            round(server.cursor.y) - client.geometry[1],
        )
        lib.wlr_cursor_set_xcursor(server.cursor, server.cursor_mgr, b"all-scroll")
    else:
        x, y, width, height = client.geometry
        lib.wlr_cursor_warp_closest(
            server.cursor, ffi.NULL, x + width, y + height,
        )
        lib.wlr_cursor_set_xcursor(server.cursor, server.cursor_mgr, b"se-resize")


def node_at(server: Server, x: float, y: float) -> tuple[Any, float, float, Client | None] | None:
    nx = ffi.new("double *")
    ny = ffi.new("double *")
    node = lib.wlr_scene_node_at(ffi.addressof(server.scene.tree.node), x, y, nx, ny)
    if node == ffi.NULL or node.type != lib.WLR_SCENE_NODE_BUFFER:
        return None
    buffer = lib.wlr_scene_buffer_from_node(node)
    scene_surface = lib.wlr_scene_surface_try_from_buffer(buffer)
    if scene_surface == ffi.NULL:
        return None
    surface = scene_surface.surface
    root = lib.wlr_surface_get_root_surface(surface)
    toplevel = lib.wlr_xdg_toplevel_try_from_wlr_surface(root)
    client = None if toplevel == ffi.NULL else server.client_by_xdg.get(ptr(toplevel.base))
    return surface, nx[0], ny[0], client


def request_set_cursor(server: Server, data: Any) -> None:
    event = ffi.cast("struct wlr_seat_pointer_request_set_cursor_event *", data)
    if event.seat_client == lib.pywl_seat_pointer_focused_client(server.seat):
        lib.wlr_cursor_set_surface(server.cursor, event.surface, event.hotspot_x, event.hotspot_y)


def request_set_selection(server: Server, data: Any) -> None:
    event = ffi.cast("struct wlr_seat_request_set_selection_event *", data)
    lib.wlr_seat_set_selection(server.seat, event.source, event.serial)


def request_set_primary_selection(server: Server, data: Any) -> None:
    event = ffi.cast("struct wlr_seat_request_set_primary_selection_event *", data)
    lib.wlr_seat_set_primary_selection(server.seat, event.source, event.serial)


def active_tags(monitor: Monitor | None) -> int:
    return 1 if monitor is None else monitor.tagsets[monitor.selected_tags]


def visible_on(client: Client, monitor: Monitor | None) -> bool:
    return client.monitor is monitor and bool(client.tags & active_tags(monitor))


def clients_on(server: Server, monitor: Monitor) -> list[Client]:
    return [client for client in server.clients if client.monitor is monitor and client.mapped]


def should_draw_border(server: Server, client: Client) -> bool:
    return server.config.border_width > 0 and not client.fullscreen


def initial_client_geometry(client: Client) -> Box:
    monitor = client.monitor
    if monitor is None:
        return (0, 0, 100, 100)
    x, y, width, height = monitor.window_box
    return (x + width // 4, y + height // 4, width // 2 or 100, height // 2 or 100)


def update_title(server: Server, client: Client) -> None:
    if focused_client(server) is client:
        print_status(server)


def update_app_id(server: Server, client: Client) -> None:
    if focused_client(server) is client:
        print_status(server)


def client_title(client: Client) -> str:
    return cstr(client.toplevel.title)


def client_app_id(client: Client) -> str:
    return cstr(client.toplevel.app_id)


def print_status(server: Server) -> None:
    monitor = server.selected_monitor
    client = focused_client(server)
    tags = active_tags(monitor) if monitor else 0
    layout = monitor.layouts[monitor.selected_layout].symbol if monitor and monitor.layouts else ""
    title = client_title(client) if client else ""
    app_id = client_app_id(client) if client else ""
    mon_name = cstr(monitor.output.name) if monitor else ""
    print(f"selmon={mon_name}\ttitle={title}\tappid={app_id}\ttags={tags:#x}\tlayout={layout}", flush=True)


def monitor_rule_for(server: Server, output: Any) -> MonitorRule:
    name = cstr(output.name)
    return next((rule for rule in server.config.monitor_rules if rule.name in (None, name)), MonitorRule())


def color_array(color: Color) -> Any:
    return ffi.new("float[4]", color)


def cstr(value: Any) -> str:
    return "" if value == ffi.NULL else ffi.string(value).decode(errors="replace")


def ptr(value: Any) -> int:
    return int(ffi.cast("uintptr_t", value))


def reap_children() -> None:
    while True:
        try:
            pid, _status = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        if pid == 0:
            return


if __name__ == "__main__":
    raise SystemExit(main())

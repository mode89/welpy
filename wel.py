"""Wayland compositor."""

from __future__ import annotations

import enum
import functools
import importlib.util
import logging
import os
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import bindings


logger = logging.getLogger(__name__)


BORDER_WIDTH = 2
BORDER_COLOR_ACTIVE = (0.0, 0.5, 1.0, 1.0)
BORDER_COLOR_INACTIVE = (0.3, 0.3, 0.3, 1.0)


class Layer(enum.Enum):
    """Z-ordered scene layers; later members render above earlier ones."""
    BACKGROUND = enum.auto()
    BOTTOM = enum.auto()
    TILE = enum.auto()
    FLOAT = enum.auto()
    TOP = enum.auto()
    FULLSCREEN = enum.auto()
    OVERLAY = enum.auto()


# Indexed by zwlr_layer_shell_v1 layer values (0..3) to map them to Layer.
SHELL_LAYERS = (Layer.BACKGROUND, Layer.BOTTOM, Layer.TOP, Layer.OVERLAY)


@dataclass(frozen=True)
class Rect:
    """Axis-aligned rectangle in layout coordinates."""
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True)
class Grab:
    """Active mouse-driven interaction on a window. In both kinds,
    `cursor - (x, y)` is the value drag_client preserves under motion --
    the window origin for "move", the window size for "resize"."""
    kind: str
    x: int
    y: int


@dataclass
class Monitor:
    """Physical screen."""
    output: Any              # wlr_output: the physical screen
    scene_output: Any        # per-screen render state inside the scene graph
    layers: dict             # {Layer: list[LayerSurface]} per shell layer
    window_area: Rect        # screen rect minus shell exclusive zones
    fullscreen: Any          # the Client occupying this screen, or None
    listeners: list[Any]


@dataclass
class LayerSurface:
    """Shell-component window (bar, wallpaper, launcher) anchored to a
    screen edge via the layer-shell protocol."""
    layer_surface: Any       # wlr_layer_surface_v1
    scene_layer: Any         # wlr_scene_layer_surface_v1: anchor/zone geometry
    scene_tree: Any          # scene_layer.tree, cached for reparenting
    popups_tree: Any         # parent scene tree for popups from this surface
    monitor: Monitor
    focused: bool            # True while this surface holds the keyboard
    listeners: list[Any]


@dataclass
class Client: # pylint: disable=too-many-instance-attributes
    """Application window."""
    toplevel: Any            # the window (xdg_toplevel role on a wl_surface)
    scene_tree: Any          # wrapper tree: xdg subtree + the four border rects
    xdg_tree: Any            # xdg subtree; inset within the wrapper by border
    borders: tuple           # (top, bottom, left, right) wlr_scene_rect handles
    focus_order: int         # bumped on each focus; higher = more recent
    grab: Grab | None        # active mouse drag (move/resize), or None
    floating_geom: Rect | None  # the float's rect; None means tiled
    monitor: Any
    listeners: list[Any]
    pending_serial: int | None   # configure serial; None when client caught up
    decoration: Any          # wlr_xdg_toplevel_decoration_v1, if any
    handle: Any              # ffi.new_handle: backs toplevel.base.data


@dataclass
class Cursor:
    """Mouse pointer."""
    cursor: Any              # wlr_cursor: tracks pointer position
    xcursor_manager: Any     # loads themed cursor images from disk
    listeners: list[Any]


@dataclass
class KeyboardGroup:
    """Every physical keyboard funneled into one logical keyboard, so apps
    see a single source of key events no matter how many keyboards are
    plugged in."""
    group: Any               # wlr_keyboard_group: combines member keyboards
    keymap: Any              # xkb_keymap: layout shared by every member
    xkb_context: Any         # xkb_context: owns the keymap
    listeners: list[Any]


@dataclass
class Server: # pylint: disable=too-many-instance-attributes
    """The compositor's long-lived state."""
    ffi: Any
    lib: Any
    listen: Any
    add_signal: Any
    display: Any
    event_loop: Any
    backend: Any
    renderer: Any
    allocator: Any
    compositor: Any
    output_layout: Any       # geometric arrangement of physical screens
    scene: Any               # root of the scene graph -- everything to draw
    scene_layout: Any        # bridges scene_outputs with output_layout
    xdg_shell: Any
    layer_shell: Any
    seat: Any
    cursor: Cursor
    keyboard_group: KeyboardGroup
    monitors: list[Monitor]
    clients: list[Client]
    layers: dict             # scene tree per Layer; key order = z order
    keycode: dict            # sym-name -> evdev-keycode
    bindings: dict           # (mods, code) -> action(server)
    listeners: list[Any]


def main():
    """Bring the compositor online and run until terminated."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s")

    load_config()

    server = setup()
    ffi, lib = server.ffi, server.lib

    install_signals(server)

    socket = ffi.string(lib.wl_display_add_socket_auto(server.display)).decode()
    os.environ["WAYLAND_DISPLAY"] = socket

    if not lib.wlr_backend_start(server.backend):
        lib.wlr_backend_destroy(server.backend)
        lib.wl_display_destroy(server.display)
        raise RuntimeError("failed to start backend")

    autostart(server)

    lib.wl_display_run(server.display)

    teardown(server)


def load_config(path=None) -> None:
    """Run the user's `config.py` if present."""
    if path is None:
        base = os.environ.get("XDG_CONFIG_HOME")
        config_dir = Path(base) if base else Path.home() / ".config"
        path = config_dir / "welpy" / "config.py"
    else:
        path = Path(path)
    if not path.exists():
        logger.info("no config found at %s", path)
        return
    logger.info("loading config from %s", path)
    # Alias so config's `import wel` finds us instead of a second instance.
    sys.modules.setdefault("wel", sys.modules[__name__])
    # Enable loading modules from the config's directory
    sys.path.append(str(path.parent))
    spec = importlib.util.spec_from_file_location("welpy_config", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)


def setup() -> Server: # pylint: disable=too-many-locals
    """Build everything wlroots needs to render and expose Wayland: backend +
    renderer, the scene graph, the protocol globals apps look for (compositor,
    xdg-shell, data device, ...), and the input seat."""
    ffi, lib, listen, _add_timer, add_signal = bindings.build()

    display = lib.wl_display_create()
    event_loop = lib.wl_display_get_event_loop(display)
    backend = lib.wlr_backend_autocreate(event_loop, ffi.NULL)
    renderer = lib.wlr_renderer_autocreate(backend)
    lib.wlr_renderer_init_wl_display(renderer, display)
    allocator = lib.wlr_allocator_autocreate(backend, renderer)

    compositor = lib.wlr_compositor_create(display, 6, renderer)
    lib.wlr_subcompositor_create(display)
    lib.wlr_data_device_manager_create(display)

    # Two parallel hierarchies + a bridge: output_layout positions physical
    # screens in 2D, scene holds the drawable content, scene_layout pairs
    # each scene_output to its layout entry so they move together.
    output_layout = lib.wlr_output_layout_create(display)
    scene = lib.wlr_scene_create()
    scene_layout = lib.wlr_scene_attach_output_layout(scene, output_layout)

    # Layer's declaration order is the intended z-order under scene_root.
    scene_root = ffi.addressof(scene.tree)
    layers = {layer: lib.wlr_scene_tree_create(scene_root) for layer in Layer}

    xdg_shell = lib.wlr_xdg_shell_create(display, 3)
    layer_shell = lib.wlr_layer_shell_v1_create(display, 3)
    # Negotiate server-side decorations so we own the chrome (border, sizing)
    # and apps don't draw their own title bar/shadow on top of ours.
    lib.wlr_server_decoration_manager_set_default_mode(
        lib.wlr_server_decoration_manager_create(display),
        lib.WLR_SERVER_DECORATION_MANAGER_MODE_SERVER)
    xdg_decoration_mgr = lib.wlr_xdg_decoration_manager_v1_create(display)

    lib.wlr_xdg_output_manager_v1_create(display, output_layout)

    seat = lib.wlr_seat_create(display, b"seat0")
    lib.wlr_seat_set_capabilities(seat,
        lib.WL_SEAT_CAPABILITY_POINTER | lib.WL_SEAT_CAPABILITY_KEYBOARD)

    server = Server(
        ffi=ffi, lib=lib, listen=listen,
        add_signal=lambda signum, cb: add_signal(event_loop, signum, cb),
        display=display, event_loop=event_loop, backend=backend,
        renderer=renderer, allocator=allocator,
        compositor=compositor,
        output_layout=output_layout,
        scene=scene, scene_layout=scene_layout,
        xdg_shell=xdg_shell, layer_shell=layer_shell, seat=seat,
        cursor=None, keyboard_group=None,
        monitors=[], clients=[],
        layers=layers,
        keycode={}, bindings={}, listeners=[],
    )

    server.cursor = create_cursor(server)
    server.keyboard_group = create_keyboard_group(server)
    server.keycode = build_keycode_map(lib, ffi, server.keyboard_group.keymap)
    server.bindings = key_bindings(server)

    server.listeners.extend([
        listen(lib.welpy_backend_new_output(backend),
            lambda data: monitor_new(server, data)),
        listen(lib.welpy_xdg_shell_new_toplevel(xdg_shell),
            lambda data: client_new(server, data)),
        listen(lib.welpy_xdg_shell_new_popup(xdg_shell),
            lambda data: popup_new(server, data)),
        listen(lib.welpy_backend_new_input(backend),
            lambda data: input_new(server, data)),
        listen(lib.welpy_output_layout_change(output_layout),
            lambda _data: update_monitors(server)),
        listen(lib.welpy_xdg_decoration_manager_new(xdg_decoration_mgr),
            lambda data: decoration_new(server, data)),
        listen(lib.welpy_layer_shell_new_surface(layer_shell),
            lambda data: layer_surface_new(server, data)),
    ])

    return server


def install_signals(server: Server) -> None:
    """Make SIGINT/SIGTERM exit cleanly, reap zombie children on SIGCHLD, and
    ignore SIGPIPE so a disconnecting app can't kill us mid-write."""

    def reap(_signum):
        try:
            while os.waitpid(-1, os.WNOHANG)[0] > 0:
                pass
        except ChildProcessError:
            pass

    server.listeners.extend([
        server.add_signal(signal.SIGINT, lambda _signum: terminate(server)),
        server.add_signal(signal.SIGTERM, lambda _signum: terminate(server)),
        server.add_signal(signal.SIGCHLD, reap),
        server.add_signal(signal.SIGPIPE, lambda _signum: None),
    ])


def autostart(_server: Server) -> None:
    """Launch initial program(s) once the compositor is online."""
    spawn("foot")


def spawn(*argv: str) -> subprocess.Popen:
    """Launch a program with no inherited compositor state."""
    # pylint: disable=consider-using-with,subprocess-popen-preexec-fn
    return subprocess.Popen(
        argv, start_new_session=True,
        # Without this, children inherit our blocked signal mask (we use
        # signalfd-based handlers, so SIGCHLD/SIGINT/SIGTERM/SIGPIPE are
        # blocked) and our session/ctty. Alacritty's pty event loop livelocks
        # on shutdown when SIGCHLD is blocked at start; mirror dwl's spawn() to
        # avoid the trap.
        # preexec_fn is documented as thread-unsafe; welpy is single-threaded.
        preexec_fn=lambda: signal.pthread_sigmask(signal.SIG_SETMASK, set()))


def teardown(server: Server) -> None:
    """Take the compositor down."""
    lib = server.lib
    for listener in server.listeners:
        listener.remove()
    server.listeners.clear()
    destroy_keyboard_group(lib, server.keyboard_group)
    destroy_cursor(lib, server.cursor)
    lib.wl_display_destroy_clients(server.display)
    lib.wlr_backend_destroy(server.backend)
    lib.wl_display_destroy(server.display)


def terminate(server):
    """Terminate event loop of wlroots."""
    server.lib.wl_display_terminate(server.display)


def close_window(server: Server) -> None:
    """Ask the focused app to close its window."""
    client = top_client(server, selected_monitor(server))
    if client is None:
        return
    server.lib.wlr_xdg_toplevel_send_close(client.toplevel)


def monitor_new(server: Server, data) -> None:
    """Fires when the backend reports a screen (at startup or hot-plug). Brings
    it online: pick a mode, place it in the layout, attach a render target,
    start its frame loop."""
    ffi, lib, listen = server.ffi, server.lib, server.listen
    output = ffi.cast("struct wlr_output *", data)
    lib.wlr_output_init_render(output, server.allocator, server.renderer)

    state = lib.welpy_output_state_new()
    lib.wlr_output_state_set_enabled(state, True)
    mode = lib.wlr_output_preferred_mode(output)
    if mode != ffi.NULL:
        lib.wlr_output_state_set_mode(state, mode)
    lib.wlr_output_commit_state(output, state)
    lib.welpy_output_state_free(state)

    # Place the screen in the geometric layout, give the scene a render
    # target for it, and pair the two so layout changes auto-reposition the
    # render target.
    layout_output = lib.wlr_output_layout_add_auto(server.output_layout, output)
    scene_output = lib.wlr_scene_output_create(server.scene, output)
    lib.wlr_scene_output_layout_add_output(
        server.scene_layout, layout_output, scene_output)

    monitor = Monitor(
        output=output, scene_output=scene_output,
        layers={layer: [] for layer in SHELL_LAYERS},
        window_area=Rect(0, 0, 0, 0),
        fullscreen=None,
        listeners=[])
    server.monitors.append(monitor)
    monitor.window_area = monitor_box(server, monitor)
    monitor.listeners.extend([
        listen(lib.welpy_output_frame(output),
            lambda data: monitor_render(server, monitor, data)),
        listen(lib.welpy_output_request_state(output),
            lambda data: monitor_request_state(server, monitor, data)),
        listen(lib.welpy_output_destroy_signal(output),
            lambda data: monitor_cleanup(server, monitor, data)),
    ])
    update_monitors(server)


def monitor_request_state(server: Server, monitor: Monitor, data) -> None:
    """Fires when the backend asks to reconfigure a screen."""
    ffi, lib = server.ffi, server.lib
    event = ffi.cast("struct wlr_output_event_request_state *", data)
    lib.wlr_output_commit_state(monitor.output, event.state)
    update_monitors(server)


def monitor_render(server: Server, monitor: Monitor, _data) -> None:
    """Fires once per refresh of this screen. Paints a frame and tells the apps
    visible on it to start producing the next, keeping them in sync with this
    screen's vsync."""
    ffi, lib = server.ffi, server.lib
    # Hold the paint until compositor-driven size changes are acked, so
    # borders and content land together. Floats opt in only while resizing.
    held = any(
        c.pending_serial is not None
        for c in clients_on(server, monitor)
        if client_layer(c) != Layer.FLOAT
        or (c.grab is not None and c.grab.kind == "resize")
    )
    if not held:
        lib.wlr_scene_output_commit(monitor.scene_output, ffi.NULL)
    ts = ffi.new("struct timespec *")
    lib.wlr_scene_output_send_frame_done(monitor.scene_output, ts)


def monitor_cleanup(server: Server, monitor: Monitor, _data) -> None:
    """Fires when a screen goes away -- unplugged, or the backend is shutting
    down. Detaches our listeners and drops the monitor."""
    # Each destroy callback mutates the bucket, so iterate a snapshot.
    for ls in [s for bucket in monitor.layers.values() for s in bucket]:
        server.lib.wlr_layer_surface_v1_destroy(ls.layer_surface)
    for listener in monitor.listeners:
        listener.remove()
    monitor.listeners.clear()
    server.monitors.remove(monitor)
    set_fullscreen(server, monitor, None)
    for c in clients_on(server, monitor):
        c.monitor = None
    update_monitors(server)


def update_monitors(server: Server) -> None:
    """Called whenever the output layout changes: adding or removing a monitor,
    changing an output's mode or position, etc. This is where the change
    officially happens and we update geometry, window positions, focus, etc."""
    selected = selected_monitor(server)
    if selected is not None:
        for c in clients_on(server, None):
            c.monitor = selected
    for m in server.monitors:
        arrange_layers(server, m)
        apply_geometry(server, m)
    apply_focus(server)


def selected_monitor(server: Server):
    """The monitor new windows go to and orphaned windows are adopted by;
    by convention the first entry of `Server.monitors`."""
    return server.monitors[0] if server.monitors else None


def monitor_box(server: Server, monitor: Monitor) -> Rect:
    """This monitor's extent in layout coordinates."""
    ffi, lib = server.ffi, server.lib
    box = ffi.new("struct wlr_box *")
    lib.wlr_output_layout_get_box(server.output_layout, monitor.output, box)
    return Rect(box.x, box.y, box.width, box.height)


def client_new(server: Server, data) -> None:
    """Fires when an app creates a new window. Attaches lifecycle
    listeners; the scene tree, borders, and layout entry are added at map
    time so creation lands in a single frame."""
    ffi, lib, listen = server.ffi, server.lib, server.listen
    toplevel = ffi.cast("struct wlr_xdg_toplevel *", data)
    client = Client(
        toplevel=toplevel, scene_tree=None, xdg_tree=None, borders=(),
        focus_order=0, grab=None, floating_geom=None,
        monitor=None, listeners=[], pending_serial=None,
        decoration=None, handle=None)
    # Back-pointer toplevel -> Client, so per-surface protocols (xdg-decoration
    # etc.) can resolve the owning client before it joins server.clients at map.
    client.handle = ffi.new_handle(client)
    toplevel.base.data = client.handle
    client.listeners.extend([
        listen(lib.welpy_surface_commit(toplevel.base.surface),
            lambda data: client_commit(server, client, data)),
        listen(lib.welpy_surface_map(toplevel.base.surface),
            lambda data: client_map(server, client, data)),
        listen(lib.welpy_surface_unmap(toplevel.base.surface),
            lambda data: client_unmap(server, client, data)),
        listen(lib.welpy_xdg_toplevel_destroy(toplevel),
            lambda data: client_cleanup(server, client, data)),
        listen(lib.welpy_xdg_toplevel_request_fullscreen(toplevel),
            lambda data: client_request_fullscreen(server, client, data)),
    ])


def client_commit(server: Server, client: Client, _data) -> None:
    """Fires every time the app commits new state for its window."""
    if client.toplevel.base.initial_commit:
        # Empty configure; real tile size is sent from client_map.
        set_size(server, client, 0, 0)
        return
    # Release the screen hold once the client has caught up.
    if client.pending_serial is not None:
        acked = client.toplevel.base.current.configure_serial
        if acked >= client.pending_serial:
            client.pending_serial = None


def client_map(server: Server, client: Client, _data) -> None:
    """Fires the first time the window has a buffer to show. Builds the
    window's scene tree, joins the layout, and shifts focus -- all in one
    event so the new window, sibling reflow, and focus highlight land in
    a single frame."""
    ffi, lib = server.ffi, server.lib
    # Wrap the xdg subtree alongside four edge rects, inset so they frame it.
    # The inset is reapplied per-resize so fullscreen can collapse it to 0.
    client.scene_tree = lib.wlr_scene_tree_create(server.layers[Layer.TILE])
    client.xdg_tree = lib.wlr_scene_xdg_surface_create(
        client.scene_tree, client.toplevel.base)
    # Anchor for popups: wlr_xdg_popup.parent points at this wlr_surface,
    # and popup_new reads .data to find the parent scene tree.
    client.toplevel.base.surface.data = ffi.cast("void *", client.scene_tree)
    color = ffi.new("float[4]", BORDER_COLOR_INACTIVE)
    client.borders = tuple(
        lib.wlr_scene_rect_create(client.scene_tree, 0, 0, color)
        for _ in range(4))
    client.monitor = selected_monitor(server)
    # Un-fullscreen any window already fullscreen on this monitor so the new
    # window isn't buried under it.
    if client.monitor is not None and client.monitor.fullscreen is not None:
        set_fullscreen(server, client.monitor, None)
    # Insert at the front so the newest window becomes the master tile.
    server.clients.insert(0, client)
    set_tiled(
        server, client,
        lib.WLR_EDGE_TOP | lib.WLR_EDGE_BOTTOM
        | lib.WLR_EDGE_LEFT | lib.WLR_EDGE_RIGHT)
    if client.toplevel.requested.fullscreen and client.monitor is not None:
        # Honor a pre-map or initial-commit fullscreen request.
        set_fullscreen(server, client.monitor, client)
    focus_client(server, client)
    apply_tree(server)
    if client.monitor is not None:
        apply_geometry(server, client.monitor)
    apply_focus(server)
    # The decoration request may have arrived before the initial configure;
    # now that the surface is initialized, set_mode is safe.
    apply_decoration(server)


def client_unmap(server: Server, client: Client, _data) -> None:
    """Fires when a window stops showing (close or voluntary hide). Tears
    down the window's scene tree, leaves the layout, reflows siblings,
    and shifts focus -- in one event so removal lands in a single frame."""
    ffi, lib = server.ffi, server.lib
    client.grab = None
    monitor = client.monitor
    if monitor is not None and monitor.fullscreen is client:
        monitor.fullscreen = None
    server.clients.remove(client)
    # Wrapper isn't tied to the xdg role's lifetime
    lib.wlr_scene_node_destroy(ffi.addressof(client.scene_tree.node))
    client.toplevel.base.surface.data = ffi.NULL
    client.scene_tree = None
    client.borders = ()
    client.monitor = None
    candidates = clients_on(server, monitor) if monitor else []
    if candidates:
        focus_client(server, max(candidates, key=lambda c: c.focus_order))
    if monitor is not None and monitor in server.monitors:
        apply_geometry(server, monitor)
    apply_focus(server)


def client_request_fullscreen(
        server: Server, client: Client, _data) -> None:
    """An app asked to enter or leave fullscreen."""
    if client.scene_tree is None:
        # Pre-map: client_map reads the same flag once the tree exists.
        return
    monitor = client.monitor
    if monitor is None:
        return
    wants = bool(client.toplevel.requested.fullscreen)
    if wants and monitor.fullscreen is not client:
        set_fullscreen(server, monitor, client)
    elif not wants and monitor.fullscreen is client:
        set_fullscreen(server, monitor, None)
    apply_tree(server)
    apply_geometry(server, monitor)
    apply_focus(server)


def client_cleanup(_server: Server, client: Client, _data) -> None:
    """Fires when an app closes a window (or its connection drops). The
    visible work happened in unmap (if the window was ever mapped); this
    just detaches listeners."""
    for listener in client.listeners:
        listener.remove()
    client.listeners.clear()


def decoration_new(server: Server, data) -> None:
    """Fires when an app announces an xdg-toplevel decoration object so we
    can negotiate its mode. Welpy draws its own border and forces every
    window onto server-side decorations."""
    ffi, lib, listen = server.ffi, server.lib, server.listen
    deco = ffi.cast("struct wlr_xdg_toplevel_decoration_v1 *", data)
    if deco.toplevel.base.data == ffi.NULL:
        return
    client = ffi.from_handle(deco.toplevel.base.data)
    client.decoration = deco

    def on_destroy(_data):
        client.decoration = None
        for l in listeners:
            l.remove()

    listeners = [
        listen(lib.welpy_xdg_decoration_request_mode(deco),
            lambda _data: apply_decoration(server)),
        listen(lib.welpy_xdg_decoration_destroy(deco), on_destroy),
    ]

    apply_decoration(server)


def apply_decoration(server: Server) -> None:
    """Force server-side decoration on every window that announced an
    xdg-toplevel decoration and is past the initial configure."""
    lib = server.lib
    for client in server.clients:
        # Setting the mode schedules a configure; before the surface is
        # initialized that would be a protocol error.
        if (client.decoration is not None
                and client.toplevel.base.initialized):
            lib.wlr_xdg_toplevel_decoration_v1_set_mode(
                client.decoration,
                lib.WLR_XDG_TOPLEVEL_DECORATION_V1_MODE_SERVER_SIDE)


def apply_tree(server: Server) -> None:
    """Parent each window's scene node under the layer it belongs to."""
    ffi, lib = server.ffi, server.lib

    for client in server.clients:
        if client.scene_tree is not None:
            node = ffi.addressof(client.scene_tree.node)
            target = server.layers[client_layer(client)]
            if node.parent != target:
                lib.wlr_scene_node_reparent(node, target)

    for monitor in server.monitors:
        for layer, bucket in monitor.layers.items():
            target = server.layers[layer]
            # BG/BOTTOM popups lift into TOP so a bar can't bury them.
            popups_target = server.layers[
                Layer.TOP if layer in (Layer.BACKGROUND, Layer.BOTTOM)
                else layer]
            for ls in bucket:
                node = ffi.addressof(ls.scene_tree.node)
                if node.parent != target:
                    lib.wlr_scene_node_reparent(node, target)
                popups_node = ffi.addressof(ls.popups_tree.node)
                if popups_node.parent != popups_target:
                    lib.wlr_scene_node_reparent(popups_node, popups_target)


def popup_new(server: Server, data) -> None:
    """Fires when an app creates a transient sub-window (menu, tooltip,
    autocomplete). The scene node is deferred to the popup's first commit
    so the parent surface is fully set up first."""
    ffi, lib, listen = server.ffi, server.lib, server.listen
    popup = ffi.cast("struct wlr_xdg_popup *", data)

    def on_commit(_data):
        if not popup.base.initial_commit:
            return
        if popup.parent == ffi.NULL:
            cleanup()
            return
        parent_scene = (
            ffi.cast("struct wlr_scene_tree *", popup.parent.data)
            if popup.parent.data != ffi.NULL else None)
        if parent_scene is None:
            cleanup()
            return
        scene = lib.wlr_scene_xdg_surface_create(parent_scene, popup.base)
        popup.base.surface.data = ffi.cast("void *", scene)
        root = lib.wlr_surface_get_root_surface(popup.parent)
        owner_node, owner_monitor = _popup_owner(server, root)
        if owner_monitor is not None and owner_node is not None:
            box = monitor_box(server, owner_monitor)
            wlr_box = ffi.new("struct wlr_box *",
                [box.x - owner_node.x, box.y - owner_node.y,
                 box.width, box.height])
            lib.wlr_xdg_popup_unconstrain_from_box(popup, wlr_box)
        cleanup()

    def cleanup():
        for l in listeners:
            l.remove()
        listeners.clear()

    listeners = [
        listen(lib.welpy_surface_commit(popup.base.surface), on_commit),
        listen(lib.welpy_xdg_popup_destroy(popup), lambda _data: cleanup()),
    ]


def _popup_owner(server: Server, root_surface):
    """The (parent-scene-node, monitor) for the window owning this surface,
    or (None, None) if no window claims it."""
    for c in server.clients:
        if (c.toplevel.base.surface == root_surface
                and c.scene_tree is not None):
            return c.scene_tree.node, c.monitor
    for m in server.monitors:
        for bucket in m.layers.values():
            for ls in bucket:
                if ls.layer_surface.surface == root_surface:
                    return ls.scene_tree.node, ls.monitor
    return None, None


def layer_surface_new(server: Server, data) -> None:
    """Fires when an app creates a shell-anchored window (bar, wallpaper,
    launcher). Sets up its scene tree; real geometry lands at first commit."""
    ffi, lib, listen = server.ffi, server.lib, server.listen
    layer_surface = ffi.cast("struct wlr_layer_surface_v1 *", data)
    if layer_surface.output == ffi.NULL:
        monitor = selected_monitor(server)
        if monitor is None:
            lib.wlr_layer_surface_v1_destroy(layer_surface)
            return
        layer_surface.output = monitor.output
    else:
        monitor = next(
            (m for m in server.monitors if m.output == layer_surface.output),
            None)
        if monitor is None:
            lib.wlr_layer_surface_v1_destroy(layer_surface)
            return
    layer = SHELL_LAYERS[layer_surface.pending.layer]
    scene_layer = lib.wlr_scene_layer_surface_v1_create(
        server.layers[layer], layer_surface)
    # Lift popups for BG/BOTTOM into TOP so they aren't buried by a bar.
    popups_parent = server.layers[
        Layer.TOP if layer in (Layer.BACKGROUND, Layer.BOTTOM) else layer]
    popups_tree = lib.wlr_scene_tree_create(popups_parent)
    ls = LayerSurface(
        layer_surface=layer_surface, scene_layer=scene_layer,
        scene_tree=scene_layer.tree, popups_tree=popups_tree,
        monitor=monitor, focused=False, listeners=[])
    # popup_new resolves the parent scene tree via surface.data.
    layer_surface.surface.data = ffi.cast("void *", popups_tree)
    monitor.layers[layer].append(ls)
    ls.listeners.extend([
        listen(lib.welpy_surface_commit(layer_surface.surface),
            lambda data: layer_surface_commit(server, ls, data)),
        listen(lib.welpy_surface_unmap(layer_surface.surface),
            lambda data: layer_surface_unmap(server, ls, data)),
        listen(lib.welpy_layer_surface_destroy(layer_surface),
            lambda data: layer_surface_cleanup(server, ls, data)),
    ])
    lib.wlr_surface_send_enter(layer_surface.surface, monitor.output)


def layer_surface_commit(server: Server, ls: LayerSurface, _data) -> None:
    """Fires every time the shell surface commits new state."""
    ffi = server.ffi
    layer_surface = ls.layer_surface
    monitor = ls.monitor
    if monitor is None:
        return
    if layer_surface.initial_commit:
        # Swap pending into current so the initial configure sees real size.
        size = ffi.sizeof("struct wlr_layer_surface_v1_state")
        saved = ffi.new("struct wlr_layer_surface_v1_state *")
        ffi.memmove(saved, ffi.addressof(layer_surface, "current"), size)
        ffi.memmove(ffi.addressof(layer_surface, "current"),
                    ffi.addressof(layer_surface, "pending"), size)
        arrange_layers(server, monitor)
        ffi.memmove(ffi.addressof(layer_surface, "current"), saved, size)
        return
    new_layer = SHELL_LAYERS[layer_surface.current.layer]
    if ls not in monitor.layers[new_layer]:
        for bucket in monitor.layers.values():
            if ls in bucket:
                bucket.remove(ls)
                break
        monitor.layers[new_layer].append(ls)
    arrange_layers(server, monitor)
    apply_tree(server)
    apply_geometry(server, monitor)
    apply_focus(server)


def layer_surface_unmap(server: Server, ls: LayerSurface, _data) -> None:
    """Fires when the shell surface stops showing; reclaims its space."""
    was_focused = ls.focused
    ls.focused = False
    if ls.monitor is not None:
        arrange_layers(server, ls.monitor)
    if was_focused:
        top = top_client(server, ls.monitor)
        if top is not None:
            focus_client(server, top)
    if ls.monitor is not None:
        apply_geometry(server, ls.monitor)
    apply_focus(server)


def layer_surface_cleanup(
        server: Server, ls: LayerSurface, _data) -> None:
    """Fires when a shell surface is destroyed (app close, output gone)."""
    ffi, lib = server.ffi, server.lib
    for listener in ls.listeners:
        listener.remove()
    ls.listeners.clear()
    monitor = ls.monitor
    if monitor is not None:
        for bucket in monitor.layers.values():
            if ls in bucket:
                bucket.remove(ls)
                break
        ls.monitor = None
    lib.wlr_scene_node_destroy(ffi.addressof(ls.scene_tree.node))
    lib.wlr_scene_node_destroy(ffi.addressof(ls.popups_tree.node))
    if monitor is not None and monitor in server.monitors:
        arrange_layers(server, monitor)
        apply_geometry(server, monitor)
    apply_focus(server)


def focus_client(server: Server, client: Client) -> None:
    """Mark `client` as most-recently-focused. The actual focus effects
    are emitted by apply_focus at the handler boundary."""
    previous = top_client(server, selected_monitor(server))
    client.focus_order = (previous.focus_order if previous else 0) + 1


def apply_focus(server: Server) -> None: # pylint: disable=too-many-branches
    """Reconcile keyboard focus and focus indicators to match current state.
    Picks the highest-priority TOP/OVERLAY shell surface that asks for the
    keyboard, else the most-recently-focused window on the selected screen,
    and emits only the effects needed to converge wlroots onto that target."""
    ffi, lib = server.ffi, server.lib
    none = lib.ZWLR_LAYER_SURFACE_V1_KEYBOARD_INTERACTIVITY_NONE

    def qualifies(ls):
        return (ls.layer_surface.surface.mapped
                and ls.layer_surface.current.keyboard_interactive != none)

    # Prefer the currently-focused layer surface if it still qualifies, so
    # arranging an unrelated screen doesn't steal the keyboard from it.
    target_ls = next((
        ls for m in server.monitors
        for bucket in m.layers.values()
        for ls in bucket
        if ls.focused and qualifies(ls)), None)
    if target_ls is None:
        for m in server.monitors:
            for layer in (Layer.OVERLAY, Layer.TOP):
                for ls in reversed(m.layers[layer]):
                    if qualifies(ls):
                        target_ls = ls
                        break
                if target_ls is not None:
                    break
            if target_ls is not None:
                break

    target_client = (top_client(server, selected_monitor(server))
                     if target_ls is None else None)
    target_surface = (
        target_ls.layer_surface.surface if target_ls is not None
        else target_client.toplevel.base.surface if target_client is not None
        else None)

    # ls.focused is a cache of what apply_focus last picked.
    for m in server.monitors:
        for bucket in m.layers.values():
            for ls in bucket:
                ls.focused = ls is target_ls

    current_surface = server.seat.keyboard_state.focused_surface
    if current_surface == ffi.NULL:
        current_surface = None
    current_client = next((
        c for c in server.clients
        if c.scene_tree is not None
        and c.toplevel.base.surface == current_surface), None)

    if (current_client is not None
            and current_client is not target_client):
        set_activated(server, current_client, False)
        set_border_color(server, current_client, BORDER_COLOR_INACTIVE)

    if target_client is not None and target_client is not current_client:
        lib.wlr_scene_node_raise_to_top(
            ffi.addressof(target_client.scene_tree.node))
        set_activated(server, target_client, True)
        set_border_color(server, target_client, BORDER_COLOR_ACTIVE)

    if target_surface is not current_surface:
        if target_surface is None:
            lib.wlr_seat_keyboard_clear_focus(server.seat)
        else:
            kb_group = lib.welpy_keyboard_group_keyboard(
                server.keyboard_group.group)
            lib.wlr_seat_keyboard_notify_enter(
                server.seat, target_surface,
                kb_group.keycodes, kb_group.num_keycodes,
                ffi.addressof(kb_group, "modifiers"))


def grabbed_client(server: Server):
    """Return the window currently being mouse-dragged, or None."""
    grabbed = [c for c in server.clients if c.grab is not None]
    if len(grabbed) > 1:
        logger.warning("multiple windows grabbed: %d", len(grabbed))
    return grabbed[0] if grabbed else None


def clients_on(server: Server, monitor):
    """Clients assigned to `monitor`, preserving `Server.clients` order. Pass
    `None` to get orphaned clients."""
    return [c for c in server.clients if c.monitor is monitor]


def top_client(server: Server, monitor):
    """The most-recently-focused mapped window on `monitor`, or None."""
    return max(
        (c for c in clients_on(server, monitor) if c.scene_tree is not None),
        key=lambda c: c.focus_order, default=None)


def cycle_focus(server: Server, direction: int) -> None:
    """Step focus through the selected monitor's windows in layout order,
    so repeated presses visit every window instead of toggling two.
    No-op while a fullscreen window owns the monitor -- focus is pinned to
    it until the user toggles fullscreen off."""
    monitor = selected_monitor(server)
    candidates = clients_on(server, monitor)
    if not candidates:
        return
    if monitor.fullscreen is not None:
        return
    index = candidates.index(top_client(server, monitor))
    focus_client(server, candidates[(index + direction) % len(candidates)])
    apply_focus(server)


def toggle_fullscreen(server: Server) -> None:
    """Flip the focused window into or out of fullscreen on its monitor.
    Exiting restores the prior floating geometry if there was one, else
    re-tiles."""
    monitor = selected_monitor(server)
    if monitor is None:
        return
    client = top_client(server, monitor)
    if client is None:
        return
    if monitor.fullscreen is client:
        set_fullscreen(server, monitor, None)
    else:
        set_fullscreen(server, monitor, client)
    apply_tree(server)
    apply_geometry(server, monitor)
    apply_focus(server)


def toggle_floating(server: Server) -> None:
    """Flip the focused window between tiled and floating. No-op while it
    is fullscreen."""
    monitor = selected_monitor(server)
    if monitor is None:
        return
    client = top_client(server, monitor)
    if client is None or monitor.fullscreen is client:
        return
    if client.floating_geom is None:
        # Seed the float at the current outer rect so it starts where it tiled.
        client.floating_geom = client_outer_rect(client)
    else:
        client.floating_geom = None
    apply_tree(server)
    apply_geometry(server, monitor)
    apply_focus(server)


def zoom(server: Server) -> None:
    """Promote the focused window to master, or, if it already is, toggle
    back to the previously displaced master."""
    monitor = selected_monitor(server)
    if monitor is None:
        return
    tiled = [c for c in clients_on(server, monitor)
             if client_layer(c) == Layer.TILE]
    if len(tiled) < 2:
        return
    focused = top_client(server, monitor)
    if focused is None or client_layer(focused) != Layer.TILE:
        return
    master = tiled[0]
    if focused is master:
        partner = max(
            (c for c in tiled if c is not master),
            key=lambda c: c.focus_order)
    else:
        partner = master
    i = server.clients.index(focused)
    j = server.clients.index(partner)
    server.clients[i], server.clients[j] = (
        server.clients[j], server.clients[i])
    if focused is master:
        focus_client(server, partner)
    else:
        # Tag the displaced master so the next zoom toggles back to it.
        partner.focus_order = focused.focus_order
        focused.focus_order += 1
    apply_geometry(server, monitor)
    apply_focus(server)


def arrange_layers(server: Server, monitor: Monitor) -> None:
    """Configure layer-shell surfaces on `monitor`, recompute its usable
    window area, and re-flow client windows if anchored bars changed it."""
    ffi, lib = server.ffi, server.lib
    full = monitor_box(server, monitor)
    full_box = ffi.new("struct wlr_box *",
        [full.x, full.y, full.width, full.height])
    usable = ffi.new("struct wlr_box *",
        [full.x, full.y, full.width, full.height])
    # Top-down so higher layers reserve their space first.
    for layer in reversed(SHELL_LAYERS):
        for ls in monitor.layers[layer]:
            if (ls.layer_surface.initialized
                    and ls.layer_surface.current.exclusive_zone > 0):
                lib.wlr_scene_layer_surface_v1_configure(
                    ls.scene_layer, full_box, usable)
    new_area = Rect(usable.x, usable.y, usable.width, usable.height)
    if new_area != monitor.window_area:
        monitor.window_area = new_area
    # Non-exclusive surfaces overlay the remaining area without shrinking it.
    for layer in reversed(SHELL_LAYERS):
        for ls in monitor.layers[layer]:
            if (ls.layer_surface.initialized
                    and ls.layer_surface.current.exclusive_zone <= 0):
                lib.wlr_scene_layer_surface_v1_configure(
                    ls.scene_layer, full_box, usable)
    for bucket in monitor.layers.values():
        for ls in bucket:
            # Popups live in a sibling tree; mirror their owner's position.
            lib.wlr_scene_node_set_position(
                ffi.addressof(ls.popups_tree.node),
                ls.scene_tree.node.x, ls.scene_tree.node.y)


def apply_geometry(server: Server, monitor: Monitor) -> None:
    """Lay out this monitor's tiled, fullscreen, and floating windows so
    each matches its dataclass-described rect."""
    clients = clients_on(server, monitor)
    tiled = [c for c in clients if client_layer(c) == Layer.TILE]
    full_box = monitor_box(server, monitor)
    if monitor.fullscreen is not None and monitor.fullscreen.scene_tree:
        resize_client(server, monitor.fullscreen, full_box)
    for c, rect in zip(
            tiled, master_stack(monitor.window_area, len(tiled))):
        if c.scene_tree is not None:
            resize_client(server, c, rect)
    for c in clients:
        if (client_layer(c) == Layer.FLOAT
                and c.scene_tree is not None):
            resize_client(server, c, c.floating_geom)


def master_stack(box: Rect, n: int) -> list[Rect]:
    """Split `box` into `n` tiles: one master on the left half, the rest
    stacked vertically on the right half with heights summing exactly to
    `box.height`."""
    if n == 1:
        return [box]
    master_w = box.width // 2
    stack_x = box.x + master_w
    stack_w = box.width - master_w
    rects = [Rect(box.x, box.y, master_w, box.height)]
    for i in range(1, n):
        sy = box.y + ((i - 1) * box.height) // (n - 1)
        sh = (box.y + (i * box.height) // (n - 1)) - sy
        rects.append(Rect(stack_x, sy, stack_w, sh))
    return rects


def resize_client(server: Server, client: Client, rect: Rect) -> None:
    """Move and resize a window so its outer wrapper matches `rect`. The
    inner surface is configured smaller to leave room for the border;
    fullscreen windows skip the border so the surface fills the rect."""
    ffi, lib = server.ffi, server.lib
    bw = 0 if client_layer(client) == Layer.FULLSCREEN else BORDER_WIDTH
    inner_w = max(rect.width - 2 * bw, 0)
    inner_h = max(rect.height - 2 * bw, 0)
    lib.wlr_scene_node_set_position(
        ffi.addressof(client.scene_tree.node), rect.x, rect.y)
    lib.wlr_scene_node_set_position(
        ffi.addressof(client.xdg_tree.node), bw, bw)
    set_size(server, client, inner_w, inner_h)
    top, bottom, left, right = client.borders
    lib.wlr_scene_rect_set_size(top, rect.width, bw)
    lib.wlr_scene_rect_set_size(bottom, rect.width, bw)
    lib.wlr_scene_rect_set_size(left, bw, inner_h)
    lib.wlr_scene_rect_set_size(right, bw, inner_h)
    lib.wlr_scene_node_set_position(
        lib.welpy_scene_rect_node(top), 0, 0)
    lib.wlr_scene_node_set_position(
        lib.welpy_scene_rect_node(bottom), 0, rect.height - bw)
    lib.wlr_scene_node_set_position(
        lib.welpy_scene_rect_node(left), 0, bw)
    lib.wlr_scene_node_set_position(
        lib.welpy_scene_rect_node(right), rect.width - bw, bw)


def set_border_color(server: Server, client: Client, color) -> None:
    """Paint every edge of this window's border with the same RGBA color."""
    color_arr = server.ffi.new("float[4]", color)
    for rect in client.borders:
        server.lib.wlr_scene_rect_set_color(rect, color_arr)


def set_size(
        server: Server, client: Client, width: int, height: int) -> None:
    """Tell this window what size to render at."""
    serial = server.lib.wlr_xdg_toplevel_set_size(
        client.toplevel, width, height)
    _track_configure(client, serial)


def set_activated(server: Server, client: Client, activated: bool) -> None:
    """Tell this window whether it has focus, so the app can render its
    focused state (title-bar styling, cursor blink, etc.)."""
    serial = server.lib.wlr_xdg_toplevel_set_activated(
        client.toplevel, activated)
    _track_configure(client, serial)


def set_tiled(server: Server, client: Client, edges: int) -> None:
    """Tell this window which of its edges are flush against neighbors or
    screen borders, so the app can suppress decorations on those edges."""
    serial = server.lib.wlr_xdg_toplevel_set_tiled(client.toplevel, edges)
    _track_configure(client, serial)


def set_fullscreen(
        server: Server, monitor: Monitor, client: Client | None) -> None:
    """Set this screen's fullscreen window (None to clear), notifying the
    affected apps so their xdg-toplevel state matches."""
    prev = monitor.fullscreen
    if prev is client:
        return
    monitor.fullscreen = client
    if prev is not None:
        serial = server.lib.wlr_xdg_toplevel_set_fullscreen(
            prev.toplevel, False)
        _track_configure(prev, serial)
    if client is not None:
        serial = server.lib.wlr_xdg_toplevel_set_fullscreen(
            client.toplevel, True)
        _track_configure(client, serial)


def _track_configure(client: Client, serial: int) -> None:
    """Remember the configure we sent so we can hold the screen until the
    client renders at the new state."""
    acked = client.toplevel.base.current.configure_serial
    if acked >= serial:
        # wlroots allocates a fresh monotonic serial per configure, so this
        # branch shouldn't be reachable; clear pending defensively.
        logger.warning(
            "configure serial %d already acked (current=%d)", serial, acked)
        client.pending_serial = None
        return
    client.pending_serial = serial


def client_layer(client: Client) -> Layer:
    """The z-bucket this window lives in, derived from its monitor's
    fullscreen pointer and its own floating rect."""
    if client.monitor is not None and client.monitor.fullscreen is client:
        return Layer.FULLSCREEN
    if client.floating_geom is not None:
        return Layer.FLOAT
    return Layer.TILE



def client_outer_rect(client: Client) -> Rect:
    """This window's current outer rectangle (the area it draws into,
    borders included) in layout coordinates."""
    geom = client.toplevel.base.geometry
    return Rect(
        client.scene_tree.node.x, client.scene_tree.node.y,
        geom.width + 2 * BORDER_WIDTH, geom.height + 2 * BORDER_WIDTH)


def surface_at(server: Server, lx: float, ly: float):
    """The `(surface, sx, sy)` under a layout point, or `(None, 0, 0)`.
    Resolves the deepest scene buffer node at that point back to its
    wlr_surface and surface-local coordinates."""
    ffi, lib = server.ffi, server.lib
    nx = ffi.new("double *")
    ny = ffi.new("double *")
    node = lib.wlr_scene_node_at(
        ffi.addressof(server.scene.tree.node), lx, ly, nx, ny)
    if node == ffi.NULL or node.type != lib.WLR_SCENE_NODE_BUFFER:
        return None, 0, 0
    scene_surface = lib.wlr_scene_surface_try_from_buffer(
        lib.wlr_scene_buffer_from_node(node))
    if scene_surface == ffi.NULL:
        return None, 0, 0
    return scene_surface.surface, nx[0], ny[0]


def client_at(server: Server, lx: float, ly: float):
    """Find the window covering the given layout point, or None. Walks up
    from the deepest scene node at that point to whichever ancestor tree we
    own as a window's root."""
    ffi, lib = server.ffi, server.lib
    nx = ffi.new("double *")
    ny = ffi.new("double *")
    node = lib.wlr_scene_node_at(
        ffi.addressof(server.scene.tree.node), lx, ly, nx, ny)
    if node == ffi.NULL:
        return None
    tree = node.parent
    while tree != ffi.NULL:
        for client in server.clients:
            if client.scene_tree == tree:
                return client
        tree = tree.node.parent
    return None


def create_cursor(server: Server) -> Cursor:
    """Build the mouse pointer and make it visible: a wlr_cursor positioned
    on the screen layout, drawn with the default xcursor image."""
    ffi, lib, listen = server.ffi, server.lib, server.listen
    cursor = lib.wlr_cursor_create()
    lib.wlr_cursor_attach_output_layout(cursor, server.output_layout)
    xcursor_manager = lib.wlr_xcursor_manager_create(ffi.NULL, 24)
    lib.wlr_cursor_set_xcursor(cursor, xcursor_manager, b"default")
    return Cursor(
        cursor=cursor, xcursor_manager=xcursor_manager,
        listeners=[
            listen(lib.welpy_cursor_motion(cursor),
                lambda data: cursor_motion(server, data)),
            listen(lib.welpy_cursor_motion_absolute(cursor),
                lambda data: cursor_motion_absolute(server, data)),
            listen(lib.welpy_cursor_button(cursor),
                lambda data: cursor_button(server, data)),
            listen(lib.welpy_cursor_axis(cursor),
                lambda data: cursor_axis(server, data)),
            listen(lib.welpy_cursor_frame(cursor),
                lambda data: cursor_frame(server, data)),
        ])


def destroy_cursor(lib, cursor: Cursor) -> None:
    """Tear down the mouse pointer; detach listeners first so they don't
    fire against freed objects."""
    for listener in cursor.listeners:
        listener.remove()
    cursor.listeners.clear()
    lib.wlr_cursor_destroy(cursor.cursor)
    lib.wlr_xcursor_manager_destroy(cursor.xcursor_manager)


def cursor_motion(server: Server, data) -> None:
    """Fires on relative mouse movement; slides the pointer by the delta,
    clamped to the screen layout."""
    ffi, lib = server.ffi, server.lib
    event = ffi.cast("struct wlr_pointer_motion_event *", data)
    lib.wlr_cursor_move(
        server.cursor.cursor, ffi.addressof(event.pointer.base),
        event.delta_x, event.delta_y)
    if grabbed := grabbed_client(server):
        drag_client(server, grabbed)
    else:
        forward_pointer_motion(server, event.time_msec)


def cursor_motion_absolute(server: Server, data) -> None:
    """Fires when a device reports an absolute position (touchscreens, tablets,
    nested-backend windows); warps the pointer there."""
    ffi, lib = server.ffi, server.lib
    event = ffi.cast("struct wlr_pointer_motion_absolute_event *", data)
    lib.wlr_cursor_warp_absolute(
        server.cursor.cursor, ffi.addressof(event.pointer.base),
        event.x, event.y)
    if grabbed := grabbed_client(server):
        drag_client(server, grabbed)
    else:
        forward_pointer_motion(server, event.time_msec)


def forward_pointer_motion(server: Server, time_msec: int) -> None:
    """Forward a pointer move to whatever surface sits under the cursor so
    apps see hovers and tooltips."""
    lib = server.lib
    cur = server.cursor.cursor
    surface, sx, sy = surface_at(server, cur.x, cur.y)
    if surface is None:
        lib.wlr_seat_pointer_clear_focus(server.seat)
        return
    lib.wlr_seat_pointer_notify_enter(server.seat, surface, sx, sy)
    lib.wlr_seat_pointer_notify_motion(server.seat, time_msec, sx, sy)


def cursor_button(server: Server, data) -> None:
    """Fires on mouse-button press/release."""
    ffi, lib = server.ffi, server.lib
    event = ffi.cast("struct wlr_pointer_button_event *", data)
    grabbed = grabbed_client(server)
    if event.state == lib.WL_POINTER_BUTTON_STATE_PRESSED:
        if grabbed is not None:
            return  # drag in progress consumes further presses
        client = client_at(
            server, server.cursor.cursor.x, server.cursor.cursor.y)
        if client is not None:
            focus_client(server, client)
        kb = lib.welpy_keyboard_group_keyboard(server.keyboard_group.group)
        mods = lib.wlr_keyboard_get_modifiers(kb)
        action = server.bindings.get((mods, event.button))
        if action is not None:
            action(server)
            return  # action self-reconciles
    elif grabbed is not None:
        grabbed.grab = None
        return  # release ended the drag, not the app's click
    lib.wlr_seat_pointer_notify_button(
        server.seat, event.time_msec, event.button, event.state)
    apply_focus(server)


def cursor_axis(server: Server, data) -> None:
    """Forward scroll/wheel events to the focused surface so apps can
    scroll."""
    ffi, lib = server.ffi, server.lib
    event = ffi.cast("struct wlr_pointer_axis_event *", data)
    lib.wlr_seat_pointer_notify_axis(
        server.seat, event.time_msec, event.orientation, event.delta,
        event.delta_discrete, event.source, event.relative_direction)


def cursor_frame(server: Server, _data) -> None:
    """Hardware frame boundary: tell the focused surface that a batch of
    pointer events is complete so it can act on them as one update."""
    server.lib.wlr_seat_pointer_notify_frame(server.seat)


def begin_dragging_client(server: Server) -> None:
    """Switch to drag-to-move on the window under the cursor. Snapshots the
    cursor->window offset so motion can preserve it and the drag doesn't
    snap the window under the pointer."""
    cur = server.cursor.cursor
    client = client_at(server, cur.x, cur.y)
    if client is None:
        return
    monitor = client.monitor
    if monitor is not None and monitor.fullscreen is client:
        set_fullscreen(server, monitor, None)
    if client.floating_geom is None:
        client.floating_geom = client_outer_rect(client)
    node = client.scene_tree.node
    client.grab = Grab("move", int(cur.x - node.x), int(cur.y - node.y))
    apply_tree(server)
    if monitor is not None:
        apply_geometry(server, monitor)
    apply_focus(server)


def begin_resizing_client(server: Server) -> None:
    """Switch to drag-to-resize on the window under the cursor. The top-left
    stays put; cursor delta is added to the original size."""
    cur = server.cursor.cursor
    client = client_at(server, cur.x, cur.y)
    if client is None:
        return
    monitor = client.monitor
    if monitor is not None and monitor.fullscreen is client:
        set_fullscreen(server, monitor, None)
    if client.floating_geom is None:
        client.floating_geom = client_outer_rect(client)
    rect = client.floating_geom
    client.grab = Grab(
        "resize", int(cur.x) - rect.width, int(cur.y) - rect.height)
    apply_tree(server)
    if monitor is not None:
        apply_geometry(server, monitor)
    apply_focus(server)


def drag_client(server: Server, grabbed: Client) -> None:
    """While dragging, keep the grabbed window tracking the cursor: move
    pins the captured offset; resize adds the cursor delta to the size.
    Updates floating_geom in step so apply_geometry stays a no-op mid-drag."""
    ffi, lib = server.ffi, server.lib
    cur = server.cursor.cursor
    grab = grabbed.grab
    if grab.kind == "move":
        nx = int(cur.x) - grab.x
        ny = int(cur.y) - grab.y
        lib.wlr_scene_node_set_position(
            ffi.addressof(grabbed.scene_tree.node), nx, ny)
        fg = grabbed.floating_geom
        grabbed.floating_geom = Rect(nx, ny, fg.width, fg.height)
    elif grab.kind == "resize":
        node = grabbed.scene_tree.node
        w = max(1, int(cur.x) - grab.x)
        h = max(1, int(cur.y) - grab.y)
        rect = Rect(node.x, node.y, w, h)
        resize_client(server, grabbed, rect)
        grabbed.floating_geom = rect
    else:
        logger.warning("unknown grab kind: %r", grab.kind)


def modkey(server: Server) -> int:
    """The modifier every compositor binding is gated on."""
    return server.lib.WLR_MODIFIER_LOGO


def key_bindings(server: Server) -> dict:
    """Built-in keybindings."""
    lib = server.lib
    mod = modkey(server)
    return {
        # pylint: disable=consider-using-with
        (mod, server.keycode["Return"]): lambda _: spawn("foot"),
        (mod | lib.WLR_MODIFIER_SHIFT, server.keycode["e"]): terminate,
        (mod | lib.WLR_MODIFIER_SHIFT, server.keycode["q"]): close_window,
        (mod, server.keycode["j"]): lambda s: cycle_focus(s, +1),
        (mod, server.keycode["k"]): lambda s: cycle_focus(s, -1),
        (mod, server.keycode["f"]): toggle_fullscreen,
        (mod | lib.WLR_MODIFIER_SHIFT, server.keycode["space"]):
            toggle_floating,
        (mod, server.keycode["z"]): zoom,
        (mod, lib.BTN_LEFT): begin_dragging_client,
        (mod, lib.BTN_RIGHT): begin_resizing_client,
    }


def build_keycode_map(lib, ffi, keymap) -> dict:
    """Resolve sym names to evdev keycodes once so bindings can use names."""
    result = {}
    syms_pp = ffi.new("const uint32_t **")
    name_buf = ffi.new("char[64]")
    for kc in range(lib.xkb_keymap_min_keycode(keymap),
                    lib.xkb_keymap_max_keycode(keymap) + 1):
        # Layout 0, level 0: strips Shift so "q" and "Q" don't collide.
        n = lib.xkb_keymap_key_get_syms_by_level(keymap, kc, 0, 0, syms_pp)
        for i in range(n):
            if lib.xkb_keysym_get_name(syms_pp[0][i], name_buf, 64) <= 0:
                continue
            # xkb keycodes are evdev + 8; store evdev to match event.keycode.
            result[ffi.string(name_buf).decode()] = kc - 8
    return result


def create_keyboard_group(server: Server) -> KeyboardGroup:
    """Build the combined keyboard, point the seat at it, and wire the
    listeners that forward its events."""
    ffi, lib, listen = server.ffi, server.lib, server.listen
    xkb_context = lib.xkb_context_new(0)
    keymap = lib.xkb_keymap_new_from_names(xkb_context, ffi.NULL, 0)
    group = lib.wlr_keyboard_group_create()
    kb_group = lib.welpy_keyboard_group_keyboard(group)
    lib.wlr_keyboard_set_keymap(kb_group, keymap)
    lib.wlr_keyboard_set_repeat_info(kb_group, 25, 600)
    lib.wlr_seat_set_keyboard(server.seat, kb_group)
    return KeyboardGroup(
        group=group, keymap=keymap, xkb_context=xkb_context,
        listeners=[
            listen(lib.welpy_keyboard_key_signal(kb_group),
                lambda data: keyboard_key(server, data)),
            listen(lib.welpy_keyboard_modifiers_signal(kb_group),
                lambda data: keyboard_modifiers(server, data)),
        ])


def destroy_keyboard_group(lib, keyboard_group: KeyboardGroup) -> None:
    """Tear down the combined keyboard: detach its listeners before the
    underlying objects go away, then release the xkb resources we own."""
    for listener in keyboard_group.listeners:
        listener.remove()
    keyboard_group.listeners.clear()
    lib.wlr_keyboard_group_destroy(keyboard_group.group)
    lib.xkb_keymap_unref(keyboard_group.keymap)
    lib.xkb_context_unref(keyboard_group.xkb_context)


def input_new(server: Server, data) -> None:
    """Fires when the backend reports a new keyboard, mouse, etc."""
    ffi, lib = server.ffi, server.lib
    device = ffi.cast("struct wlr_input_device *", data)
    if device.type == lib.WLR_INPUT_DEVICE_KEYBOARD:
        keyboard = lib.wlr_keyboard_from_input_device(device)
        lib.wlr_keyboard_set_keymap(keyboard, server.keyboard_group.keymap)
        lib.wlr_keyboard_group_add_keyboard(
            server.keyboard_group.group, keyboard)
    elif device.type == lib.WLR_INPUT_DEVICE_POINTER:
        lib.wlr_cursor_attach_input_device(server.cursor.cursor, device)


def keyboard_key(server: Server, data) -> None:
    """Fires when any keyboard in the group emits a key press/release."""
    ffi, lib = server.ffi, server.lib
    event = ffi.cast("struct wlr_keyboard_key_event *", data)
    # Edge-trigger bindings on press; the release still forwards, leaking
    # a stray key-up to the focused app, which most apps ignore.
    if event.state == lib.WL_KEYBOARD_KEY_STATE_PRESSED:
        kb = lib.welpy_keyboard_group_keyboard(server.keyboard_group.group)
        mods = lib.wlr_keyboard_get_modifiers(kb)
        action = server.bindings.get((mods, event.keycode))
        if action is not None:
            action(server)
            return  # action self-reconciles
    lib.wlr_seat_keyboard_notify_key(
        server.seat, event.time_msec, event.keycode, event.state)


def keyboard_modifiers(server: Server, _data) -> None:
    """Fires when any modifier (Shift/Ctrl/...) in the group changes state."""
    ffi, lib = server.ffi, server.lib
    kb_group = lib.welpy_keyboard_group_keyboard(server.keyboard_group.group)
    lib.wlr_seat_keyboard_notify_modifiers(
        server.seat, ffi.addressof(kb_group, "modifiers"))


def override(arg):
    """Replace a function, injecting the previous version as the first argument
    so overrides can chain. Used as ``@wel.override`` (replaces ``wel.<name>``)
    or ``@wel.override(target)`` (replaces ``target`` wherever it lives)."""
    if not callable(arg):
        raise TypeError(
            f"@wel.override expects a function; got {type(arg).__name__}")
    home = sys.modules.get(arg.__module__)
    if home is not None and getattr(home, arg.__name__, None) is arg:
        return lambda fn: _install(home, arg.__name__, arg, fn)
    wl_module = sys.modules[__name__]
    if not hasattr(wl_module, arg.__name__):
        raise AttributeError(
            f"wel has no attribute {arg.__name__!r}; for targets outside wel, "
            f"use the explicit form: @wel.override(<module>.{arg.__name__})")
    return _install(
        wl_module, arg.__name__, getattr(wl_module, arg.__name__), arg)


def _install(module, name, target, fn):
    """Install `fn` at `module.name`, with `target` curried as first arg."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return fn(target, *args, **kwargs)
    # Rewrite so a later @wel.override(wrapper) routes here, not to fn's
    # original module.
    wrapper.__module__ = module.__name__
    setattr(module, name, wrapper)
    return wrapper


if __name__ == "__main__":
    main()

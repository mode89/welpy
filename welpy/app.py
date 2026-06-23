"""Wayland compositor."""

from __future__ import annotations

import importlib.util
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from . import bindings
from . import ext_workspace
from . import focus
from . import geometry
from . import layer_shell
from . import layout
from . import libinput
from . import model
from . import session_lock
from . import windows
from . import xwayland
from .layout import Rect
from .model import (
    Client, Cursor, Grab, KeyboardGroup, Layer,
    Monitor, PointerConstraint, Server,
    SHELL_LAYERS, Workspace, X11Client, XdgClient,
)


LayerSurface = model.LayerSurface
LockSurface = model.LockSurface
SessionLock = model.SessionLock
Unmanaged = model.Unmanaged
logger = logging.getLogger(__name__)


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
    os.environ["DISPLAY"] = ffi.string(server.xwayland.display_name).decode()

    if not lib.wlr_backend_start(server.backend):
        lib.wlr_backend_destroy(server.backend)
        lib.wl_display_destroy(server.display)
        raise RuntimeError("failed to start backend")

    autostart(server)

    lib.wl_display_run(server.display)

    teardown(server)

    logger.info("compositor shut down cleanly")


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
    # Enable loading modules from the config's directory
    sys.path.append(str(path.parent))
    spec = importlib.util.spec_from_file_location("welpy_config", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)


def setup() -> Server: # pylint: disable=too-many-locals,too-many-statements
    """Build everything wlroots needs to render and expose Wayland: backend +
    renderer, the scene graph, the protocol globals apps look for (compositor,
    xdg-shell, data device, ...), and the input seat."""
    ffi, lib, listen, add_timer, add_signal = bindings.build()

    display = lib.wl_display_create()
    event_loop = lib.wl_display_get_event_loop(display)
    session = ffi.new("struct wlr_session **")
    backend = lib.wlr_backend_autocreate(event_loop, session)
    renderer = lib.wlr_renderer_autocreate(backend)
    lib.wlr_renderer_init_wl_shm(renderer, display)
    allocator = lib.wlr_allocator_autocreate(backend, renderer)

    compositor = lib.wlr_compositor_create(display, 6, renderer)
    lib.wlr_subcompositor_create(display)
    # Surface effects and frame timing the scene helper drives once these exist.
    lib.wlr_viewporter_create(display)
    lib.wlr_alpha_modifier_v1_create(display)
    lib.wlr_single_pixel_buffer_manager_v1_create(display)
    lib.wlr_presentation_create(display, backend, 2)
    lib.wlr_data_device_manager_create(display)
    lib.wlr_primary_selection_v1_device_manager_create(display)
    # Privileged clipboard access for managers/tools (wl-clipboard, history
    # daemons); both protocol versions cover tooling mid-migration.
    lib.wlr_data_control_manager_v1_create(display)
    lib.wlr_ext_data_control_manager_v1_create(display, 1)

    # Two parallel hierarchies + a bridge: output_layout positions physical
    # screens in 2D, scene holds the drawable content, scene_layout pairs
    # each scene_output to its layout entry so they move together.
    output_layout = lib.wlr_output_layout_create(display)
    scene = lib.wlr_scene_create()
    scene_layout = lib.wlr_scene_attach_output_layout(scene, output_layout)

    # Zero-copy GPU buffer sharing only makes sense on a real DRM device.
    drm_fd = lib.wlr_renderer_get_drm_fd(renderer)
    if drm_fd >= 0 and lib.wlr_renderer_get_texture_formats(
            renderer, lib.WLR_BUFFER_CAP_DMABUF):
        lib.wlr_drm_create(display, renderer)
        # Scene integration unlocks direct scan-out and dmabuf feedback.
        lib.wlr_scene_set_linux_dmabuf_v1(
            scene,
            lib.wlr_linux_dmabuf_v1_create_with_renderer(display, 5, renderer))
    if drm_fd >= 0 and lib.welpy_supports_timeline(renderer, backend):
        lib.wlr_linux_drm_syncobj_manager_v1_create(display, 1, drm_fd)

    # Layer's declaration order is the intended z-order under scene_root.
    scene_root = ffi.addressof(scene.tree)
    layers = {layer: lib.wlr_scene_tree_create(scene_root) for layer in Layer}
    lock_background = session_lock.create_lock_background(
        ffi, lib, layers[Layer.LOCK])

    xdg_shell = lib.wlr_xdg_shell_create(display, 7)
    layer_shell_server = lib.wlr_layer_shell_v1_create(display, 5)
    # lazy=False starts Xwayland now so DISPLAY is usable immediately.
    xwayland_server = lib.wlr_xwayland_create(display, compositor, False)
    if xwayland_server == ffi.NULL:
        raise RuntimeError("failed to create XWayland server")
    # Negotiate server-side decorations so we own the chrome (border, sizing)
    # and apps don't draw their own title bar/shadow on top of ours.
    lib.wlr_server_decoration_manager_set_default_mode(
        lib.wlr_server_decoration_manager_create(display),
        lib.WLR_SERVER_DECORATION_MANAGER_MODE_SERVER)
    xdg_decoration_mgr = lib.wlr_xdg_decoration_manager_v1_create(display)

    lib.wlr_xdg_output_manager_v1_create(display, output_layout)

    lib.wlr_fractional_scale_manager_v1_create(display, 1)

    xdg_activation = lib.wlr_xdg_activation_v1_create(display)

    session_lock_mgr = lib.wlr_session_lock_manager_v1_create(display)

    output_power_mgr = lib.wlr_output_power_manager_v1_create(display)

    pointer_constraints = lib.wlr_pointer_constraints_v1_create(display)
    relative_pointer_mgr = lib.wlr_relative_pointer_manager_v1_create(display)

    # Night-light tools set per-screen color curves; the scene applies the
    # LUTs to each output automatically on commit.
    lib.wlr_scene_set_gamma_control_manager_v1(
        scene, lib.wlr_gamma_control_manager_v1_create(display))

    seat = lib.wlr_seat_create(display, b"seat0")
    lib.wlr_seat_set_capabilities(seat,
        lib.WL_SEAT_CAPABILITY_POINTER | lib.WL_SEAT_CAPABILITY_KEYBOARD)

    server = Server(
        ffi=ffi, lib=lib, listen=listen,
        add_signal=lambda signum, cb: add_signal(event_loop, signum, cb),
        add_timer=lambda cb: add_timer(event_loop, cb),
        display=display, event_loop=event_loop, backend=backend,
        session=session[0],
        renderer=renderer, allocator=allocator, renderer_lost=None,
        compositor=compositor,
        output_layout=output_layout,
        scene=scene, scene_layout=scene_layout,
        xdg_shell=xdg_shell,
        layer_shell=layer_shell_server, xwayland=xwayland_server,
        seat=seat,
        cursor=None, keyboard_group=None,
        monitors=[], active_monitor=None, clients=[],
        workspaces=[
            Workspace(
                name=name, monitor=None, fullscreen=None,
                root=layout.Container(layout.ContainerLayout.HORIZONTAL, []))
            for name in model.WORKSPACE_NAMES
        ],
        previous_workspace=None,
        ext_workspace=None,
        layers=layers,
        lock_background=lock_background, session_lock=None, locked=False,
        unmanaged_focus=None,
        keycode={}, bindings={}, passthrough=False,
        pointer_constraints=pointer_constraints,
        relative_pointer_mgr=relative_pointer_mgr,
        active_constraint=None, constraints=[],
        listeners=[],
    )

    # Off server.listeners: re-bound on every GPU reset, torn down on its own.
    server.renderer_lost = listen(
        lib.welpy_renderer_lost_signal(renderer),
        lambda _data: renderer_lost(server))

    server.cursor = create_cursor(server)
    server.keyboard_group = create_keyboard_group(server)
    server.keycode = build_keycode_map(lib, ffi, server.keyboard_group.keymap)
    server.bindings = key_bindings(server)
    server.ext_workspace = ext_workspace.create(
        server,
        on_activate=lambda name: view_workspace(server, name),
        on_assign=lambda ws, target: assign_workspace_to_monitor(
            server, ws, target),
    )

    server.listeners.extend([
        listen(lib.welpy_backend_new_output(backend),
            lambda data: monitor_new(server, data)),
        listen(lib.welpy_xdg_shell_new_toplevel(xdg_shell),
            lambda data: windows.client_new(server, data)),
        listen(lib.welpy_xdg_shell_new_popup(xdg_shell),
            lambda data: windows.popup_new(server, data)),
        listen(lib.welpy_backend_new_input(backend),
            lambda data: input_new(server, data)),
        listen(lib.welpy_output_layout_change(output_layout),
            lambda _data: update_monitors(server)),
        listen(lib.welpy_xdg_decoration_manager_new(xdg_decoration_mgr),
            lambda data: geometry.decoration_new(server, data)),
        listen(lib.welpy_layer_shell_new_surface(layer_shell_server),
            lambda data: layer_shell.layer_surface_new(server, data)),
        listen(lib.welpy_seat_request_set_selection(seat),
            lambda data: seat_set_selection(server, data)),
        listen(lib.welpy_seat_request_set_primary_selection(seat),
            lambda data: seat_set_primary_selection(server, data)),
        listen(lib.welpy_seat_request_set_cursor(seat),
            lambda data: seat_set_cursor(server, data)),
        listen(lib.welpy_xdg_activation_request_activate(xdg_activation),
            lambda data: windows.client_request_activate(server, data)),
        listen(lib.welpy_session_lock_mgr_new_lock(session_lock_mgr),
            lambda data: session_lock.lock_new(server, data)),
        listen(lib.welpy_pointer_constraints_new_constraint(
                pointer_constraints),
            lambda data: constraint_new(server, data)),
        listen(lib.welpy_output_power_mgr_set_mode(output_power_mgr),
            lambda data: output_power_set_mode(server, data)),
        listen(lib.welpy_xwayland_new_surface(xwayland_server),
            lambda data: xwayland.x11_surface_new(server, data)),
        listen(lib.welpy_xwayland_ready(xwayland_server),
            lambda _data: xwayland.x11_ready(server)),
    ])

    return server


def seat_set_selection(server: Server, data) -> None:
    """Honor an app's request to put its copied data on the clipboard."""
    ffi, lib = server.ffi, server.lib
    event = ffi.cast("struct wlr_seat_request_set_selection_event *", data)
    lib.wlr_seat_set_selection(server.seat, event.source, event.serial)


def seat_set_primary_selection(server: Server, data) -> None:
    """Honor an app's request to set the middle-click paste selection."""
    ffi, lib = server.ffi, server.lib
    event = ffi.cast(
        "struct wlr_seat_request_set_primary_selection_event *", data)
    lib.wlr_seat_set_primary_selection(
        server.seat, event.source, event.serial)


def seat_set_cursor(server: Server, data) -> None:
    """Honor an app's request to set its own cursor image (I-beam, resize
    arrow) or hide it (NULL surface)."""
    ffi, lib = server.ffi, server.lib
    event = ffi.cast(
        "struct wlr_seat_pointer_request_set_cursor_event *", data)
    # Reject background apps (any client can ask) and keep our own image while
    # a mouse drag owns the cursor.
    focused = lib.welpy_seat_pointer_focused_client(server.seat)
    if focus.grabbed_client(server) is not None or event.seat_client != focused:
        return
    lib.wlr_cursor_set_surface(
        server.cursor.cursor, event.surface,
        event.hotspot_x, event.hotspot_y)


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
    server.renderer_lost.remove()
    lib.wlr_xwayland_destroy(server.xwayland)
    # Tearing down clients (surface unmap) and the backend (screen destroy)
    # both run handlers that reach back into the cursor, keyboard group, and
    # workspace state via apply_focus -> forward_pointer_motion. Keep those
    # alive until both are gone, then free them (dwl's cleanup order).
    lib.wl_display_destroy_clients(server.display)
    lib.wlr_backend_destroy(server.backend)
    ext_workspace.destroy(server.ext_workspace)
    destroy_keyboard_group(lib, server.keyboard_group)
    destroy_cursor(lib, server.cursor)
    lib.wl_display_destroy(server.display)


def terminate(server):
    """Terminate event loop of wlroots."""
    server.lib.wl_display_terminate(server.display)


def renderer_lost(server: Server) -> None:
    """Recover from a GPU reset by rebuilding the renderer and re-pointing
    every screen and the compositor at it."""
    lib = server.lib
    old_renderer, old_allocator = server.renderer, server.allocator

    renderer = lib.wlr_renderer_autocreate(server.backend)
    if renderer == server.ffi.NULL:
        raise RuntimeError("failed to recreate renderer after GPU reset")
    allocator = lib.wlr_allocator_autocreate(server.backend, renderer)
    if allocator == server.ffi.NULL:
        lib.wlr_renderer_destroy(renderer)
        raise RuntimeError("failed to recreate allocator after GPU reset")

    server.renderer_lost.remove()
    server.renderer_lost = server.listen(
        lib.welpy_renderer_lost_signal(renderer),
        lambda _data: renderer_lost(server))

    lib.wlr_compositor_set_renderer(server.compositor, renderer)
    for monitor in server.monitors:
        lib.wlr_output_init_render(monitor.output, allocator, renderer)

    lib.wlr_allocator_destroy(old_allocator)
    lib.wlr_renderer_destroy(old_renderer)
    server.renderer, server.allocator = renderer, allocator


def close_window(server: Server) -> None:
    """Ask the focused app to close its window."""
    client = focus.top_client(server, server.active_monitor)
    if client is None:
        return
    if isinstance(client, X11Client):
        server.lib.wlr_xwayland_surface_close(client.xsurface)
    else:
        server.lib.wlr_xdg_toplevel_send_close(client.toplevel)


def monitor_new(server: Server, data) -> None:
    """Fires when the backend reports a screen (at startup or hot-plug). Brings
    it online: pick a mode, place it in the layout, attach a render target,
    start its frame loop."""
    ffi, lib, listen = server.ffi, server.lib, server.listen
    output = ffi.cast("struct wlr_output *", data)
    name = ffi.string(output.name).decode()
    logger.info("new output: %s", name)
    lib.wlr_output_init_render(output, server.allocator, server.renderer)

    scale = model.OUTPUT_SCALE.get(name, model.DEFAULT_SCALE)

    state = lib.welpy_output_state_new()
    lib.wlr_output_state_set_enabled(state, True)
    lib.wlr_output_state_set_scale(state, scale)
    mode = lib.wlr_output_preferred_mode(output)
    if mode != ffi.NULL:
        lib.wlr_output_state_set_mode(state, mode)
    lib.wlr_output_commit_state(output, state)
    lib.welpy_output_state_free(state)

    # Pre-render the pointer at this screen's scale so it stays crisp on HiDPI.
    lib.wlr_xcursor_manager_load(server.cursor.xcursor_manager, scale)

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
        active_workspace=None,
        frame_timer=None,
        listeners=[])
    monitor.frame_timer = server.add_timer(
        lambda: monitor_force_paint(server, monitor))
    server.monitors.append(monitor)
    monitor.window_area = geometry.monitor_box(server, monitor)
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


def output_power_set_mode(server: Server, data) -> None:
    """Fires when a client (e.g. an idle daemon) asks to switch a screen on or
    off for power saving (DPMS). The screen keeps its place in the layout, so
    windows stay put for when it wakes."""
    ffi, lib = server.ffi, server.lib
    event = ffi.cast("struct wlr_output_power_v1_set_mode_event *", data)
    monitor = next(
        (m for m in server.monitors if m.output == event.output), None)
    if monitor is None:
        return
    state = lib.welpy_output_state_new()
    lib.wlr_output_state_set_enabled(state, bool(event.mode))
    lib.wlr_output_commit_state(monitor.output, state)
    lib.welpy_output_state_free(state)


def monitor_render(server: Server, monitor: Monitor, _data) -> None:
    """Fires once per refresh of this screen. Paints a frame and tells the apps
    visible on it to start producing the next, keeping them in sync with this
    screen's vsync."""
    ffi, lib = server.ffi, server.lib
    held = any(
        client_holds_paint(server, c)
        for c in model.clients_visible(server, monitor)
    )
    if not held:
        lib.wlr_scene_output_commit(monitor.scene_output, ffi.NULL)
    # Clients pace their next frame off this time (e.g. mpv video sync).
    now = time.clock_gettime_ns(time.CLOCK_MONOTONIC)
    ts = ffi.new("struct timespec *",
        [now // 1_000_000_000, now % 1_000_000_000])
    lib.wlr_scene_output_send_frame_done(monitor.scene_output, ts)
    # No commit = no future refresh events; the timer caps the freeze.
    monitor.frame_timer.update(100 if held else 0)


def client_holds_paint(server: Server, client: Client) -> bool:
    """Whether an unacked compositor-driven resize on this window should hold
    the screen paint, so its border and content land in the same frame."""
    return (
        isinstance(client, XdgClient)
        and client.pending_serial is not None
        # Floats opt out so interactive resize stays responsive.
        and geometry.client_layer(client) != Layer.FLOAT
        # On-screen only: an occluded peer never acks and would hold forever.
        and client_rendered(server, client))


def client_rendered(server: Server, client: Client) -> bool:
    """Whether the window's surface is currently shown on at least one screen
    (i.e. the scene reports it visible, not fully occluded)."""
    surface = geometry.client_surface(client)
    head = server.ffi.addressof(surface[0], "current_outputs")
    return any(
        bindings.wl_list_for_each(
            server.ffi, head, "struct wlr_surface_output", "link"))


def monitor_force_paint(server: Server, monitor: Monitor) -> None:
    """Timer callback: repaint this screen so its refresh loop resumes when
    an app is too slow to catch up."""
    server.lib.wlr_scene_output_commit(monitor.scene_output, server.ffi.NULL)


def monitor_cleanup(server: Server, monitor: Monitor, _data) -> None:
    """Fires when a screen goes away -- unplugged, or the backend is shutting
    down. Detaches our listeners and drops the monitor."""
    logger.info("removing output: %s",
        server.ffi.string(monitor.output.name).decode())
    # Each destroy callback mutates the bucket, so iterate a snapshot.
    for ls in [s for bucket in monitor.layers.values() for s in bucket]:
        server.lib.wlr_layer_surface_v1_destroy(ls.layer_surface)
    monitor.frame_timer.remove()
    for listener in monitor.listeners:
        listener.remove()
    monitor.listeners.clear()
    server.monitors.remove(monitor)
    update_monitors(server)


def update_monitors(server: Server) -> None:
    """Called whenever the output layout changes: adding or removing a
    monitor, changing an output's mode or position, etc. Repairs the
    workspace hierarchy, then re-flows visibility, geometry, and focus."""
    geometry.apply_hierarchy(server)
    geometry.apply_visibility(server)
    geometry.apply_tree(server)
    for m in server.monitors:
        geometry.arrange_layers(server, m)
        geometry.apply_geometry(server, m)
    session_lock.update_lock_background(server)
    session_lock.update_lock_surfaces(server)
    focus.apply_focus(server)
    if server.ext_workspace is not None:
        ext_workspace.publish(server)


def focus_direction(server: Server, direction: layout.Direction) -> None:
    """Shift focus to the tiled window structurally adjacent in `direction` on
    the current screen, landing on a group's most-recently-focused window.
    No-op at an edge, on a float, or while fullscreen."""
    client = focus.focused_tiled(server)
    if client is None:
        return
    monitor = server.active_monitor
    candidates = layout.adjacent_leaves(
        monitor.active_workspace.root, client, direction)
    if not candidates:
        return
    focus.focus_client(server, max(candidates, key=lambda c: c.focus_order))
    focus.apply_focus(server)


def move_direction(server: Server, direction: layout.Direction) -> None:
    """Relocate the focused window one step in `direction` within the tiled
    tree -- reorder, pop out of its group, or descend into an adjacent one.
    No-op at an edge, on a float, or while fullscreen."""
    client = focus.focused_tiled(server)
    if client is None:
        return
    monitor = server.active_monitor
    layout.move(monitor.active_workspace.root, client, direction)
    geometry.apply_geometry(server, monitor)
    focus.apply_focus(server)


def group_window(server: Server) -> None:
    """Wrap the focused window in its own group, split along the window's long
    side so the group has room to grow (mod+v). No-op when the window has no
    siblings to split off from."""
    found = focus.focused_container(server)
    if found is None:
        return
    monitor, client, parent = found
    if len(parent.children) == 1:
        return
    width, height = client.inner_size
    axis = (
        layout.ContainerLayout.HORIZONTAL
        if width >= height
        else layout.ContainerLayout.VERTICAL
    )
    layout.wrap(monitor.active_workspace.root, client, axis)
    geometry.apply_geometry(server, monitor)
    focus.apply_focus(server)


def cycle_layout(server: Server) -> None:
    """Flip the focused window's split between side-by-side and stacked
    (mod+e)."""
    found = focus.focused_container(server)
    if found is None:
        return
    monitor, _, parent = found
    layout.cycle_layout(parent)
    geometry.apply_geometry(server, monitor)
    focus.apply_focus(server)


def toggle_fullscreen(server: Server) -> None:
    """Flip the focused window into or out of fullscreen on its monitor.
    Exiting restores the prior floating geometry if there was one, else
    re-tiles."""
    monitor = server.active_monitor
    client = focus.top_client(server, monitor) if monitor is not None else None
    if client is not None:
        workspace = monitor.active_workspace
        if workspace.fullscreen is client:
            geometry.set_fullscreen(server, workspace, None)
        else:
            geometry.set_fullscreen(server, workspace, client)
        geometry.apply_tree(server)
        geometry.apply_geometry(server, monitor)
        focus.apply_focus(server)


def toggle_floating(server: Server) -> None:
    """Flip the focused window between tiled and floating. No-op while it
    is fullscreen."""
    monitor = server.active_monitor
    client = focus.top_client(server, monitor) if monitor is not None else None
    if (client is not None
            and monitor.active_workspace.fullscreen is not client):
        workspace = monitor.active_workspace
        if client.floating_geom is None:
            geometry.float_client(client)
        else:
            client.floating_geom = None
            layout.insert_sibling(
                workspace.root, focus.recent_tiled_leaf(workspace.root), client)
        geometry.apply_tree(server)
        geometry.apply_geometry(server, monitor)
        focus.apply_focus(server)


def view_workspace(server: Server, name: str) -> None:
    """Show workspace `name` on its monitor and shift focus there. Adopts
    the workspace onto `active_monitor` first if it was orphaned. Ends any
    in-progress mouse grabs since hidden windows can't be dragged."""
    target = next(
        (w for w in server.workspaces if w.name == name), None)
    if target is None or server.active_monitor is None:
        return
    current = server.active_monitor.active_workspace
    if current is not None and current is not target:
        server.previous_workspace = current.name
    if target.monitor is None:
        target.monitor = server.active_monitor
    target.monitor.active_workspace = target
    server.active_monitor = target.monitor
    for c in server.clients:
        c.grab = None
    geometry.apply_hierarchy(server)
    geometry.apply_visibility(server)
    geometry.apply_tree(server)
    for m in server.monitors:
        geometry.apply_geometry(server, m)
    focus.apply_focus(server)
    if server.ext_workspace is not None:
        ext_workspace.publish(server)


def view_previous_workspace(server: Server) -> None:
    """Switch back to the workspace shown before the current one."""
    if server.previous_workspace is not None:
        view_workspace(server, server.previous_workspace)


def move_client_to_workspace(server: Server, name: str) -> None:
    """Reassign the focused window to workspace `name`. Adopts the target
    workspace onto `active_monitor` first if it was orphaned. Focus stays on
    the source monitor."""
    target = next(
        (w for w in server.workspaces if w.name == name), None)
    if target is None or server.active_monitor is None:
        return
    client = focus.top_client(server, server.active_monitor)
    if client is None or client.workspace is target:
        return
    source = client.workspace
    if source is not None and source.fullscreen is client:
        geometry.set_fullscreen(server, source, None)
    if target.monitor is None:
        target.monitor = server.active_monitor
    if target.fullscreen is not None:
        geometry.set_fullscreen(server, target, None)
    if client.floating_geom is None:
        if source is not None:
            layout.remove(source.root, client)
        layout.insert_sibling(
            target.root, focus.recent_tiled_leaf(target.root), client)
    client.workspace = target
    geometry.apply_hierarchy(server)
    geometry.apply_visibility(server)
    geometry.apply_tree(server)
    for m in server.monitors:
        geometry.apply_geometry(server, m)
    focus.apply_focus(server)
    if server.ext_workspace is not None:
        ext_workspace.publish(server)


def assign_workspace_to_monitor(
        server: Server, workspace: Workspace, target: Monitor) -> None:
    """Move `workspace` onto `target`. Used by ext-workspace clients to
    drag a workspace between monitors from a bar."""
    workspace.monitor = target
    geometry.apply_hierarchy(server)
    geometry.apply_visibility(server)
    geometry.apply_tree(server)
    for m in server.monitors:
        geometry.apply_geometry(server, m)
    focus.apply_focus(server)
    if server.ext_workspace is not None:
        ext_workspace.publish(server)


def move_active_workspace_to_monitor(
        server: Server, direction: int) -> None:
    """Move the currently-shown workspace to the previous (-1) or next (+1)
    monitor with wraparound. No-op with fewer than two monitors."""
    if len(server.monitors) < 2 or server.active_monitor is None:
        return
    source = server.active_monitor
    workspace = source.active_workspace
    target = server.monitors[
        (server.monitors.index(source) + direction) % len(server.monitors)]
    workspace.monitor = target
    target.active_workspace = workspace
    server.active_monitor = target
    geometry.apply_hierarchy(server)
    geometry.apply_visibility(server)
    geometry.apply_tree(server)
    for m in server.monitors:
        geometry.apply_geometry(server, m)
    focus.apply_focus(server)
    if server.ext_workspace is not None:
        ext_workspace.publish(server)


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
    """Fires on relative mouse movement; runs the delta through the shared
    motion path (raw-motion streaming + pointer lock/confine)."""
    ffi = server.ffi
    event = ffi.cast("struct wlr_pointer_motion_event *", data)
    process_pointer_motion(
        server, ffi.addressof(event.pointer.base),
        (event.delta_x, event.delta_y),
        (event.unaccel_dx, event.unaccel_dy), event.time_msec)


def cursor_motion_absolute(server: Server, data) -> None:
    """Fires when a device reports an absolute position (touchscreens, tablets,
    nested-backend windows); converts it to a layout delta so the same motion
    path (raw-motion streaming + lock/confine) applies to every input source."""
    ffi, lib = server.ffi, server.lib
    event = ffi.cast("struct wlr_pointer_motion_absolute_event *", data)
    device = ffi.addressof(event.pointer.base)
    cur = server.cursor.cursor
    lx, ly = ffi.new("double *"), ffi.new("double *")
    lib.wlr_cursor_absolute_to_layout_coords(
        cur, device, event.x, event.y, lx, ly)
    delta = (lx[0] - cur.x, ly[0] - cur.y)
    process_pointer_motion(server, device, delta, delta, event.time_msec)


def process_pointer_motion(server: Server, device, delta, unaccel,
                           time_msec: int) -> None:
    """Shared pointer-motion path: stream the raw delta to relative-pointer
    clients, enforce any pointer lock/confine, then move the cursor."""
    lib = server.lib
    dx, dy = delta
    lib.wlr_relative_pointer_manager_v1_send_relative_motion(
        server.relative_pointer_mgr, server.seat,
        time_msec * 1000, dx, dy, *unaccel)
    if grabbed := focus.grabbed_client(server):
        lib.wlr_cursor_move(server.cursor.cursor, device, dx, dy)
        drag_client(server, grabbed)
        return
    moved = apply_pointer_constraint(server, dx, dy)
    if moved is None:
        return  # locked: client got the raw delta; cursor + focus stay put
    lib.wlr_cursor_move(server.cursor.cursor, device, *moved)
    focus.forward_pointer_motion(server, time_msec)


def apply_pointer_constraint(server: Server, dx: float, dy: float):
    """Resolve and enforce the pointer-focused surface's constraint; the
    override seam for relaxing locking. Returns the (possibly clamped) delta,
    or None when the pointer is locked and the cursor must stay pinned."""
    ffi, lib = server.ffi, server.lib
    if not server.constraints:
        return dx, dy
    focused = server.seat.pointer_state.focused_surface
    if focused == ffi.NULL:
        focused = None
    constraint = None
    if focused is not None:
        constraint = lib.wlr_pointer_constraints_v1_constraint_for_surface(
            server.pointer_constraints, focused, server.seat)
        if constraint == ffi.NULL:
            constraint = None
    set_active_constraint(server, constraint)
    if constraint is None:
        return dx, dy
    if constraint.type == lib.WLR_POINTER_CONSTRAINT_V1_LOCKED:
        return None
    return confine_delta(server, constraint, dx, dy)


def confine_delta(server: Server, constraint, dx: float, dy: float):
    """Clamp a motion delta so a confined pointer stays inside its region.
    A no-op unless the cursor is currently over the constrained surface."""
    ffi, lib = server.ffi, server.lib
    cur = server.cursor.cursor
    surface, sx, sy = focus.surface_at(server, cur.x, cur.y)
    if surface is None or surface != constraint.surface:
        return dx, dy
    x_out, y_out = ffi.new("double *"), ffi.new("double *")
    if not lib.welpy_constraint_confine(
            constraint, sx, sy, sx + dx, sy + dy, x_out, y_out):
        return dx, dy
    return x_out[0] - sx, y_out[0] - sy


def set_active_constraint(server: Server, constraint) -> None:
    """Switch which constraint is in effect, sending deactivate/activate as
    pointer focus moves between constrained surfaces."""
    lib = server.lib
    if constraint == server.active_constraint:
        return
    if server.active_constraint is not None:
        # send_deactivated may destroy the constraint, firing its destroy
        # handler (which clears active_constraint) before we reassign below.
        lib.wlr_pointer_constraint_v1_send_deactivated(server.active_constraint)
    server.active_constraint = constraint
    if constraint is not None:
        lib.wlr_pointer_constraint_v1_send_activated(constraint)


def constraint_new(server: Server, data) -> None:
    """A client asked to lock or confine the pointer; track it so we can clean
    up its listener. Enforcement happens lazily on the next motion."""
    ffi, lib, listen = server.ffi, server.lib, server.listen
    constraint = ffi.cast("struct wlr_pointer_constraint_v1 *", data)
    record = PointerConstraint(constraint=constraint, listeners=[])
    record.listeners.append(
        listen(lib.welpy_pointer_constraint_destroy(constraint),
            lambda _data: constraint_destroy(server, record)))
    server.constraints.append(record)


def constraint_destroy(server: Server, record: PointerConstraint) -> None:
    """A pointer constraint went away; detach its listener and, if it was in
    effect, restore the cursor to the client's hint before clearing it."""
    for listener in record.listeners:
        listener.remove()
    record.listeners.clear()
    if record in server.constraints:
        server.constraints.remove(record)
    if server.active_constraint == record.constraint:
        constraint_warp_to_hint(server, record.constraint)
        server.active_constraint = None


def constraint_warp_to_hint(server: Server, constraint) -> None:
    """If the released constraint set a cursor hint, move the cursor there so
    it reappears where the app expects."""
    ffi, lib = server.ffi, server.lib
    hx, hy = ffi.new("double *"), ffi.new("double *")
    if not lib.welpy_constraint_cursor_hint(constraint, hx, hy):
        return
    client = focus.client_for_surface(server, constraint.surface)
    if client is None or client.content_tree is None:
        return
    ox, oy = ffi.new("int *"), ffi.new("int *")
    if not lib.wlr_scene_node_coords(
            ffi.addressof(client.content_tree.node), ox, oy):
        return
    lib.wlr_cursor_warp(
        server.cursor.cursor, ffi.NULL, ox[0] + hx[0], oy[0] + hy[0])
    focus.forward_pointer_motion(server, 0)


def cursor_button(server: Server, data) -> None:
    """Fires on mouse-button press/release."""
    ffi, lib = server.ffi, server.lib
    event = ffi.cast("struct wlr_pointer_button_event *", data)
    grabbed = focus.grabbed_client(server)
    if event.state == lib.WL_POINTER_BUTTON_STATE_PRESSED and not server.locked:
        if grabbed is not None:
            return  # drag in progress consumes further presses
        client = focus.client_at(
            server, server.cursor.cursor.x, server.cursor.cursor.y)
        if client is not None:
            monitor = model.client_monitor(client)
            if monitor is not None:
                server.active_monitor = monitor
            focus.focus_client(server, client)
        kb = lib.welpy_keyboard_group_keyboard(server.keyboard_group.group)
        mods = lib.wlr_keyboard_get_modifiers(kb)
        action = lookup_binding(server, mods, event.button)
        if action is not None:
            action(server)
            return  # action self-reconciles
    elif grabbed is not None:
        grabbed.grab = None
        focus.forward_pointer_motion(server, event.time_msec)
        return  # release ended the drag, not the app's click
    # Land the button on whatever is under the cursor now, not on a surface
    # left focused before the scene last changed.
    focus.rebase_pointer(server, event.time_msec)
    lib.wlr_seat_pointer_notify_button(
        server.seat, event.time_msec, event.button, event.state)
    focus.apply_focus(server)


def cursor_axis(server: Server, data) -> None:
    """Forward scroll/wheel events to the focused surface so apps can
    scroll."""
    ffi, lib = server.ffi, server.lib
    event = ffi.cast("struct wlr_pointer_axis_event *", data)
    if focus.grabbed_client(server) is None:
        focus.rebase_pointer(server, event.time_msec)
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
    client = focus.client_at(server, cur.x, cur.y)
    if client is not None:
        monitor = model.client_monitor(client)
        workspace = client.workspace
        if workspace is not None and workspace.fullscreen is client:
            geometry.set_fullscreen(server, workspace, None)
        if client.floating_geom is None:
            geometry.float_client(client)
        node = client.scene_tree.node
        client.grab = Grab(
            "move", int(cur.x - node.x), int(cur.y - node.y))
        geometry.apply_tree(server)
        if monitor is not None:
            geometry.apply_geometry(server, monitor)
        focus.apply_focus(server)


def begin_resizing_client(server: Server) -> None:
    """Switch to drag-to-resize on the window under the cursor. The top-left
    stays put; cursor delta is added to the original size."""
    cur = server.cursor.cursor
    client = focus.client_at(server, cur.x, cur.y)
    if client is not None:
        monitor = model.client_monitor(client)
        workspace = client.workspace
        if workspace is not None and workspace.fullscreen is client:
            geometry.set_fullscreen(server, workspace, None)
        if client.floating_geom is None:
            geometry.float_client(client)
        rect = client.floating_geom
        client.grab = Grab(
            "resize", int(cur.x) - rect.width, int(cur.y) - rect.height)
        geometry.apply_tree(server)
        if monitor is not None:
            geometry.apply_geometry(server, monitor)
        focus.apply_focus(server)


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
        if isinstance(grabbed, X11Client) and grabbed.inner_size is not None:
            geometry.configure_x11(server, grabbed, *grabbed.inner_size)
    elif grab.kind == "resize":
        node = grabbed.scene_tree.node
        w = max(1, int(cur.x) - grab.x)
        h = max(1, int(cur.y) - grab.y)
        rect = Rect(node.x, node.y, w, h)
        geometry.resize_client(server, grabbed, rect)
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
    chvt = lib.WLR_MODIFIER_CTRL | lib.WLR_MODIFIER_ALT
    table = {
        # pylint: disable=consider-using-with
        (mod, server.keycode["Return"]): lambda _: spawn("foot"),
        (mod | lib.WLR_MODIFIER_SHIFT, server.keycode["e"]): terminate,
        (mod | lib.WLR_MODIFIER_SHIFT, server.keycode["q"]): close_window,
        (mod, server.keycode["h"]):
            lambda s: focus_direction(s, layout.Direction.LEFT),
        (mod, server.keycode["j"]):
            lambda s: focus_direction(s, layout.Direction.DOWN),
        (mod, server.keycode["k"]):
            lambda s: focus_direction(s, layout.Direction.UP),
        (mod, server.keycode["l"]):
            lambda s: focus_direction(s, layout.Direction.RIGHT),
        (mod | lib.WLR_MODIFIER_SHIFT, server.keycode["h"]):
            lambda s: move_direction(s, layout.Direction.LEFT),
        (mod | lib.WLR_MODIFIER_SHIFT, server.keycode["j"]):
            lambda s: move_direction(s, layout.Direction.DOWN),
        (mod | lib.WLR_MODIFIER_SHIFT, server.keycode["k"]):
            lambda s: move_direction(s, layout.Direction.UP),
        (mod | lib.WLR_MODIFIER_SHIFT, server.keycode["l"]):
            lambda s: move_direction(s, layout.Direction.RIGHT),
        (mod, server.keycode["f"]): toggle_fullscreen,
        (mod | lib.WLR_MODIFIER_SHIFT, server.keycode["p"]):
            toggle_passthrough,
        (mod | lib.WLR_MODIFIER_SHIFT, server.keycode["space"]):
            toggle_floating,
        (mod, server.keycode["v"]): group_window,
        (mod, server.keycode["e"]): cycle_layout,
        (mod | lib.WLR_MODIFIER_CTRL, server.keycode["h"]):
            lambda s: move_active_workspace_to_monitor(s, -1),
        (mod | lib.WLR_MODIFIER_CTRL, server.keycode["l"]):
            lambda s: move_active_workspace_to_monitor(s, +1),
        (mod, lib.BTN_LEFT): begin_dragging_client,
        (mod, lib.BTN_RIGHT): begin_resizing_client,
        (mod, server.keycode["Tab"]): view_previous_workspace,
    }
    for name in model.WORKSPACE_NAMES:
        key = name if name != "10" else "0"
        table[(mod, server.keycode[key])] = (
            lambda s, n=name: view_workspace(s, n))
        table[(mod | lib.WLR_MODIFIER_SHIFT, server.keycode[key])] = (
            lambda s, n=name: move_client_to_workspace(s, n))
    for i in range(1, 13):
        table[(chvt, server.keycode[f"F{i}"])] = (
            lambda s, n=i: change_vt(s, n))
    return table


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
            if lib.xkb_keysym_get_name(syms_pp[0][i], name_buf, 64) > 0:
                # xkb keycodes are evdev + 8; store evdev to match keycode.
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
        libinput.configure(server, device)
        lib.wlr_cursor_attach_input_device(server.cursor.cursor, device)


def keyboard_key(server: Server, data) -> None:
    """Fires when any keyboard in the group emits a key press/release."""
    ffi, lib = server.ffi, server.lib
    event = ffi.cast("struct wlr_keyboard_key_event *", data)
    # Edge-trigger bindings on press; the release still forwards, leaking
    # a stray key-up to the focused app, which most apps ignore. While
    # locked, bindings are suppressed so the locker can't be bypassed.
    if event.state == lib.WL_KEYBOARD_KEY_STATE_PRESSED and not server.locked:
        kb = lib.welpy_keyboard_group_keyboard(server.keyboard_group.group)
        mods = lib.wlr_keyboard_get_modifiers(kb)
        action = lookup_binding(server, mods, event.keycode)
        if action is not None:
            action(server)
            return  # action self-reconciles
    lib.wlr_seat_keyboard_notify_key(
        server.seat, event.time_msec, event.keycode, event.state)


def keyboard_modifiers(server: Server, _data) -> None:
    """Fires when any modifier (Shift/Ctrl/...) in the group changes state."""
    ffi, lib = server.ffi, server.lib
    if server.locked and (server.session_lock is None
                          or not server.session_lock.surfaces):
        # A real lock surface needs modifiers; without one, only stale app
        # focus could receive them.
        focus.focus_lock(server)
        return
    kb_group = lib.welpy_keyboard_group_keyboard(server.keyboard_group.group)
    lib.wlr_seat_keyboard_notify_modifiers(
        server.seat, ffi.addressof(kb_group, "modifiers"))


def lookup_binding(server: Server, mods: int, code: int):
    """Resolve a key/button press to its bound action, or None to forward it
    to the focused app. Override to layer modal submaps over the flat table."""
    action = server.bindings.get((mods, code))
    # Passthrough forwards everything but its own toggle to the focused app.
    if server.passthrough and action is not toggle_passthrough:
        return None
    return action


def toggle_passthrough(server: Server) -> None:
    """Toggle passthrough: send all keys to the focused app instead of firing
    keybindings. Handy for nested sessions; the toggle key still works."""
    server.passthrough = not server.passthrough


def change_vt(server: Server, n: int) -> None:
    """Switch the kernel to virtual terminal `n`. No-op under nested
    backends, where there is no session to act on."""
    if server.session != server.ffi.NULL:
        server.lib.wlr_session_change_vt(server.session, n)

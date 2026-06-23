"""Wayland compositor."""

from __future__ import annotations

import importlib.util
import logging
import os
import signal
import subprocess
import sys
from pathlib import Path

from . import bindings
from . import ext_workspace
from . import focus
from . import geometry
from . import input  # pylint: disable=redefined-builtin
from . import layer_shell
from . import layout
from . import model
from . import output
from . import session_lock
from . import windows
from . import xwayland
from .model import (
    Layer, Monitor, Server, Workspace, X11Client,
)


Cursor = model.Cursor
Grab = model.Grab
KeyboardGroup = model.KeyboardGroup
LayerSurface = model.LayerSurface
LockSurface = model.LockSurface
PointerConstraint = model.PointerConstraint
Rect = layout.Rect
SHELL_LAYERS = model.SHELL_LAYERS
SessionLock = model.SessionLock
Unmanaged = model.Unmanaged
XdgClient = model.XdgClient
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

    server.cursor = input.create_cursor(server)
    server.keyboard_group = input.create_keyboard_group(server)
    server.keycode = input.build_keycode_map(
        lib, ffi, server.keyboard_group.keymap)
    server.bindings = key_bindings(server)
    server.ext_workspace = ext_workspace.create(
        server,
        on_activate=lambda name: view_workspace(server, name),
        on_assign=lambda ws, target: assign_workspace_to_monitor(
            server, ws, target),
    )

    server.listeners.extend([
        listen(lib.welpy_backend_new_output(backend),
            lambda data: output.monitor_new(server, data)),
        listen(lib.welpy_xdg_shell_new_toplevel(xdg_shell),
            lambda data: windows.client_new(server, data)),
        listen(lib.welpy_xdg_shell_new_popup(xdg_shell),
            lambda data: windows.popup_new(server, data)),
        listen(lib.welpy_backend_new_input(backend),
            lambda data: input.input_new(server, data)),
        listen(lib.welpy_output_layout_change(output_layout),
            lambda _data: output.update_monitors(server)),
        listen(lib.welpy_xdg_decoration_manager_new(xdg_decoration_mgr),
            lambda data: geometry.decoration_new(server, data)),
        listen(lib.welpy_layer_shell_new_surface(layer_shell_server),
            lambda data: layer_shell.layer_surface_new(server, data)),
        listen(lib.welpy_seat_request_set_selection(seat),
            lambda data: input.seat_set_selection(server, data)),
        listen(lib.welpy_seat_request_set_primary_selection(seat),
            lambda data: input.seat_set_primary_selection(server, data)),
        listen(lib.welpy_seat_request_set_cursor(seat),
            lambda data: input.seat_set_cursor(server, data)),
        listen(lib.welpy_xdg_activation_request_activate(xdg_activation),
            lambda data: windows.client_request_activate(server, data)),
        listen(lib.welpy_session_lock_mgr_new_lock(session_lock_mgr),
            lambda data: session_lock.lock_new(server, data)),
        listen(lib.welpy_pointer_constraints_new_constraint(
                pointer_constraints),
            lambda data: input.constraint_new(server, data)),
        listen(lib.welpy_output_power_mgr_set_mode(output_power_mgr),
            lambda data: output.output_power_set_mode(server, data)),
        listen(lib.welpy_xwayland_new_surface(xwayland_server),
            lambda data: xwayland.x11_surface_new(server, data)),
        listen(lib.welpy_xwayland_ready(xwayland_server),
            lambda _data: xwayland.x11_ready(server)),
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
    server.renderer_lost.remove()
    lib.wlr_xwayland_destroy(server.xwayland)
    # Tearing down clients (surface unmap) and the backend (screen destroy)
    # both run handlers that reach back into the cursor, keyboard group, and
    # workspace state via apply_focus -> forward_pointer_motion. Keep those
    # alive until both are gone, then free them (dwl's cleanup order).
    lib.wl_display_destroy_clients(server.display)
    lib.wlr_backend_destroy(server.backend)
    ext_workspace.destroy(server.ext_workspace)
    input.destroy_keyboard_group(lib, server.keyboard_group)
    input.destroy_cursor(lib, server.cursor)
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
            input.toggle_passthrough,
        (mod | lib.WLR_MODIFIER_SHIFT, server.keycode["space"]):
            toggle_floating,
        (mod, server.keycode["v"]): group_window,
        (mod, server.keycode["e"]): cycle_layout,
        (mod | lib.WLR_MODIFIER_CTRL, server.keycode["h"]):
            lambda s: move_active_workspace_to_monitor(s, -1),
        (mod | lib.WLR_MODIFIER_CTRL, server.keycode["l"]):
            lambda s: move_active_workspace_to_monitor(s, +1),
        (mod, lib.BTN_LEFT): input.begin_dragging_client,
        (mod, lib.BTN_RIGHT): input.begin_resizing_client,
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
            lambda s, n=i: input.change_vt(s, n))
    return table

"""Screen (output) management: bring monitors online, run their per-frame
paint loop, and re-flow window geometry/focus when the screen layout changes."""

from __future__ import annotations

import logging
import time

from . import bindings
from . import geometry
from . import model
from . import reflow
from .layout import Rect
from .model import Client, Layer, Monitor, Server, SHELL_LAYERS, XdgClient

logger = logging.getLogger(__name__)


def on_create(server: Server, data) -> None:
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
        lambda: on_force_paint(server, monitor))
    server.monitors.append(monitor)
    monitor.window_area = geometry.monitor_box(server, monitor)
    monitor.listeners.extend([
        listen(lib.welpy_output_frame(output),
            lambda data: on_frame(server, monitor, data)),
        listen(lib.welpy_output_request_state(output),
            lambda data: on_request_state(server, monitor, data)),
        listen(lib.welpy_output_destroy_signal(output),
            lambda data: on_destroy(server, monitor, data)),
    ])
    reflow.outputs(server)


def on_request_state(server: Server, monitor: Monitor, data) -> None:
    """Fires when the backend asks to reconfigure a screen."""
    ffi, lib = server.ffi, server.lib
    event = ffi.cast("struct wlr_output_event_request_state *", data)
    lib.wlr_output_commit_state(monitor.output, event.state)
    reflow.outputs(server)


def on_destroy(server: Server, monitor: Monitor, _data) -> None:
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
    reflow.outputs(server)


def on_power_mode(server: Server, data) -> None:
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


def on_frame(server: Server, monitor: Monitor, _data) -> None:
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


def on_force_paint(server: Server, monitor: Monitor) -> None:
    """Timer callback: repaint this screen so its refresh loop resumes when
    an app is too slow to catch up."""
    server.lib.wlr_scene_output_commit(monitor.scene_output, server.ffi.NULL)

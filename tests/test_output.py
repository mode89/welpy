"""Unit tests for welpy.output: screen (output) management — bring-up, the
per-frame paint loop, paint-hold predicates, and output-layout re-flow."""

from unittest.mock import ANY, MagicMock, call, patch

from welpy import layout, model, output
from tests.helpers import (
    make_server, make_client, make_x11_client, make_monitor,
    make_workspace, make_layer_surface, make_session_lock, trigger,
)


def test_update_monitors_arranges_all():
    """update_monitors arranges every connected monitor."""
    m1 = MagicMock(name="m1", fullscreen=None)
    m2 = MagicMock(name="m2", fullscreen=None)
    server = make_server(monitors=[m1, m2])

    with patch("welpy.geometry.apply_geometry") as apply_geom:
        output.update_monitors(server)

    assert apply_geom.call_args_list == [call(server, m1), call(server, m2)]


def test_update_monitors_no_monitors():
    """With no monitors connected, apply_geometry isn't called."""
    server = make_server()

    with patch("welpy.geometry.apply_geometry") as apply_geom:
        output.update_monitors(server)

    apply_geom.assert_not_called()


def test_lock_surfaces_reconfigured():
    """Output layout changes resize and move active lock surfaces."""
    monitor = make_monitor()
    lock_surface = MagicMock(name="lock_surface")
    scene_tree = MagicMock(name="scene_tree")
    ls = model.LockSurface(
        lock_surface=lock_surface, monitor=monitor,
        scene_tree=scene_tree, listeners=[])
    server = make_server(
        monitors=[monitor], active_monitor=monitor, locked=True,
        session_lock=make_session_lock(surfaces=[ls]))
    server.ffi.addressof.side_effect = lambda obj, *args: ("ADDR", obj, *args)

    with patch("welpy.geometry.apply_hierarchy"), \
         patch("welpy.geometry.apply_visibility"), \
         patch("welpy.geometry.apply_tree"), \
         patch("welpy.geometry.arrange_layers"), \
         patch("welpy.geometry.apply_geometry"), \
         patch("welpy.focus.apply_focus"), \
         patch("welpy.geometry.monitor_box",
               return_value=layout.Rect(10, 20, 300, 200)):
        output.update_monitors(server)

    server.lib.wlr_scene_node_set_position.assert_any_call(
        ("ADDR", scene_tree.node), 10, 20)
    server.lib.wlr_session_lock_surface_v1_configure.assert_called_once_with(
        lock_surface, 300, 200)


def test_lock_surfaces_pruned():
    """A removed screen drops its stale lock surface from lock state."""
    removed = make_monitor()
    remaining = make_monitor()
    ls = model.LockSurface(
        lock_surface=MagicMock(), monitor=removed,
        scene_tree=MagicMock(), listeners=[])
    session_lock = make_session_lock(surfaces=[ls])
    server = make_server(
        monitors=[remaining], active_monitor=remaining, locked=True,
        session_lock=session_lock)

    with patch("welpy.geometry.apply_hierarchy"), \
         patch("welpy.geometry.apply_visibility"), \
         patch("welpy.geometry.apply_tree"), \
         patch("welpy.geometry.arrange_layers"), \
         patch("welpy.geometry.apply_geometry"), \
         patch("welpy.focus.apply_focus"):
        output.update_monitors(server)

    assert ls not in session_lock.surfaces


def test_monitor_new_order():
    """A new screen is fully configured (mode, enable, commit) before
    being placed in the layout and exposed to the scene."""
    server = make_server()

    output.monitor_new(server, "OUTPUT_DATA")

    names = [c[0] for c in server.lib.mock_calls]
    expected = [
        "wlr_output_init_render",
        "welpy_output_state_new",
        "wlr_output_state_set_enabled",
        "wlr_output_commit_state",
        "welpy_output_state_free",
        "wlr_output_layout_add_auto",
        "wlr_scene_output_create",
        "wlr_scene_output_layout_add_output",
    ]
    positions = [names.index(n) for n in expected]
    assert positions == sorted(positions)


def test_monitor_scale_configured():
    """A screen listed in OUTPUT_SCALE is committed at its configured scale."""
    server = make_server()
    server.ffi.string.return_value.decode.return_value = "eDP-1"

    with patch.dict(model.OUTPUT_SCALE, {"eDP-1": 2.0}, clear=True):
        output.monitor_new(server, "OUTPUT_DATA")

    server.lib.wlr_output_state_set_scale.assert_called_once_with(ANY, 2.0)


def test_monitor_scale_default():
    """A screen absent from OUTPUT_SCALE falls back to DEFAULT_SCALE."""
    server = make_server()
    server.ffi.string.return_value.decode.return_value = "HDMI-A-1"

    with patch.dict(model.OUTPUT_SCALE, {"eDP-1": 2.0}, clear=True):
        output.monitor_new(server, "OUTPUT_DATA")

    server.lib.wlr_output_state_set_scale.assert_called_once_with(
        ANY, model.DEFAULT_SCALE)


def test_monitor_new_appends():
    """Each new screen produces exactly one Monitor in server.monitors."""
    server = make_server()

    output.monitor_new(server, "OUTPUT_DATA")

    assert len(server.monitors) == 1


def test_monitor_new_frame():
    """The screen's frame signal drives monitor_render so painting happens
    once per refresh."""
    server = make_server()
    with patch("welpy.output.monitor_render") as render:
        output.monitor_new(server, "OUTPUT_DATA")
        trigger(server, server.lib.welpy_output_frame, "FRAME_DATA")
    render.assert_called_once_with(server, server.monitors[0], "FRAME_DATA")


def test_monitor_new_request_state():
    """The screen's request_state signal drives monitor_request_state so the
    nested-backend window can ask to resize the screen at runtime."""
    server = make_server()
    with patch("welpy.output.monitor_request_state") as handler:
        output.monitor_new(server, "OUTPUT_DATA")
        trigger(server, server.lib.welpy_output_request_state, "RS_DATA")
    handler.assert_called_once_with(server, server.monitors[0], "RS_DATA")


def test_monitor_new_destroy():
    """The screen's destroy signal triggers monitor_cleanup so an unplug
    self-cleans without leaks."""
    server = make_server()
    with patch("welpy.output.monitor_cleanup") as cleanup:
        output.monitor_new(server, "OUTPUT_DATA")
        trigger(server, server.lib.welpy_output_destroy_signal, "DESTROY_DATA")
    cleanup.assert_called_once_with(server, server.monitors[0], "DESTROY_DATA")


def test_monitor_new_timer():
    """A new screen gets a safety-valve timer wired to monitor_force_paint
    so its refresh loop can be unstuck if an app is slow to catch up."""
    server = make_server()

    with patch("welpy.output.monitor_force_paint") as forced:
        output.monitor_new(server, "OUTPUT_DATA")
        monitor = server.monitors[0]
        server.add_timer.assert_called_once()
        callback = server.add_timer.call_args.args[0]
        callback()

    forced.assert_called_once_with(server, monitor)
    assert monitor.frame_timer is server.add_timer.return_value


def test_monitor_new_updates():
    """monitor_new triggers update_monitors so the new monitor's box is
    picked up and orphans are adopted."""
    server = make_server()

    with patch("welpy.output.update_monitors") as upd:
        output.monitor_new(server, "OUTPUT_DATA")

    upd.assert_called_once_with(server)


def test_monitor_request_state_commits():
    """Applying the requested state means committing it on the output, which
    is what actually triggers the mode/size change."""
    server = make_server()
    monitor = make_monitor(output="OUT", scene_output="SO")
    event = server.ffi.cast.return_value
    event.state = "REQUESTED_STATE"

    output.monitor_request_state(server, monitor, "RS_DATA")

    server.lib.wlr_output_commit_state.assert_called_once_with(
        "OUT", "REQUESTED_STATE")


def test_monitor_request_state_updates():
    """A reconfigure may have changed the monitor's box, so all monitors
    re-flow."""
    server = make_server()
    monitor = make_monitor(output="OUT", scene_output="SO")

    with patch("welpy.output.update_monitors") as upd:
        output.monitor_request_state(server, monitor, "RS_DATA")

    upd.assert_called_once_with(server)


def test_monitor_cleanup_drops():
    """Cleanup detaches every listener and removes the monitor from the
    server's tracking list."""
    h1, h2 = MagicMock(name="h1"), MagicMock(name="h2")
    monitor = make_monitor(scene_output="SO", listeners=[h1, h2])
    server = make_server(monitors=[monitor])

    output.monitor_cleanup(server, monitor, None)

    h1.remove.assert_called_once()
    h2.remove.assert_called_once()
    assert not monitor.listeners
    assert not server.monitors


def test_monitor_cleanup_removes_timer():
    """Cleanup detaches the safety-valve timer alongside other listeners."""
    timer = MagicMock(name="frame_timer")
    monitor = make_monitor(scene_output="SO", frame_timer=timer)
    server = make_server(monitors=[monitor])

    output.monitor_cleanup(server, monitor, None)

    timer.remove.assert_called_once()


def test_monitor_cleanup_destroys_layers():
    """When a screen goes away its layer surfaces are destroyed first so
    wlroots doesn't try to render them against a freed output."""
    monitor = make_monitor()
    server = make_server(monitors=[monitor])
    ls = make_layer_surface(monitor=monitor)
    monitor.layers[model.Layer.TOP].append(ls)

    output.monitor_cleanup(server, monitor, None)

    server.lib.wlr_layer_surface_v1_destroy.assert_called_once_with(
        ls.layer_surface)


def test_monitor_cleanup_removes():
    """monitor_cleanup drops the monitor from server.monitors and triggers
    update_monitors so apply_hierarchy migrates its workspaces."""
    monitor = make_monitor(scene_output="SO")
    server = make_server(monitors=[monitor])

    with patch("welpy.output.update_monitors") as upd:
        output.monitor_cleanup(server, monitor, None)

    assert monitor not in server.monitors
    upd.assert_called_once_with(server)


def test_output_power_off():
    """A client turning a screen off commits a disabled state on it, leaving
    the screen in the layout."""
    server = make_server()
    server.monitors.append(make_monitor(output="OUT"))
    event = server.ffi.cast.return_value
    event.output = "OUT"
    event.mode = 0

    output.output_power_set_mode(server, "PWR_DATA")

    server.lib.wlr_output_state_set_enabled.assert_called_once_with(
        server.lib.welpy_output_state_new.return_value, False)
    server.lib.wlr_output_commit_state.assert_called_once_with(
        "OUT", server.lib.welpy_output_state_new.return_value)


def test_output_power_on():
    """A client turning a screen back on commits an enabled state on it."""
    server = make_server()
    server.monitors.append(make_monitor(output="OUT"))
    event = server.ffi.cast.return_value
    event.output = "OUT"
    event.mode = 1

    output.output_power_set_mode(server, "PWR_DATA")

    server.lib.wlr_output_state_set_enabled.assert_called_once_with(
        server.lib.welpy_output_state_new.return_value, True)


def test_output_power_unknown():
    """A set-mode request for a screen we don't track is ignored."""
    server = make_server()
    event = server.ffi.cast.return_value
    event.output = "GONE"
    event.mode = 0

    output.output_power_set_mode(server, "PWR_DATA")

    server.lib.welpy_output_state_new.assert_not_called()
    server.lib.wlr_output_commit_state.assert_not_called()


def test_monitor_render_order():
    """Each frame paints first, then notifies visible apps -- both calls
    targeting this monitor's own scene_output."""
    server = make_server()
    monitor = make_monitor(output="OUT", scene_output="SO_X")

    output.monitor_render(server, monitor, None)

    server.lib.wlr_scene_output_commit.assert_called_once_with(
        "SO_X", server.ffi.NULL)
    server.lib.wlr_scene_output_send_frame_done.assert_called_once()
    names = [c[0] for c in server.lib.mock_calls]
    assert names.index("wlr_scene_output_commit") < names.index(
        "wlr_scene_output_send_frame_done")


def test_monitor_render_holds():
    """A pending configure on a tiled window holds the screen paint, while
    frame-done still fires so the client keeps animating."""
    monitor = make_monitor(output="OUT", scene_output="SO_X")
    monitor.active_workspace = make_workspace(monitor=monitor)
    a = make_client(workspace=monitor.active_workspace, pending_serial=5)
    b = make_client(workspace=monitor.active_workspace)
    server = make_server(clients=[a, b])

    with patch("welpy.output.client_rendered", return_value=True):
        output.monitor_render(server, monitor, None)

    server.lib.wlr_scene_output_commit.assert_not_called()
    server.lib.wlr_scene_output_send_frame_done.assert_called_once()


def test_monitor_render_occluded():
    """A window with a pending configure that isn't shown on any screen (e.g.
    occluded behind a fullscreen peer) must not hold the paint: it gets no
    frame-done, never acks, and would otherwise freeze the screen."""
    monitor = make_monitor(output="OUT", scene_output="SO_X")
    monitor.active_workspace = make_workspace(monitor=monitor)
    full = make_client(workspace=monitor.active_workspace)
    monitor.active_workspace.fullscreen = full
    hidden = make_client(
        workspace=monitor.active_workspace,
        pending_serial=5,
    )
    server = make_server(clients=[full, hidden])

    with patch("welpy.output.client_rendered", return_value=False):
        output.monitor_render(server, monitor, None)

    server.lib.wlr_scene_output_commit.assert_called_once()


def test_monitor_render_fullscreen_holds():
    """Entering fullscreen holds the paint until the window renders at full
    size, so the switch lands in one frame instead of flashing the old size."""
    monitor = make_monitor(output="OUT", scene_output="SO_X")
    monitor.active_workspace = make_workspace(monitor=monitor)
    full = make_client(workspace=monitor.active_workspace, pending_serial=5)
    monitor.active_workspace.fullscreen = full
    server = make_server(clients=[full])

    with patch("welpy.output.client_rendered", return_value=True):
        output.monitor_render(server, monitor, None)

    server.lib.wlr_scene_output_commit.assert_not_called()


def test_monitor_render_floating():
    """A floating window's pending configure does not hold the screen --
    floating windows aren't synchronized with the layout."""
    monitor = make_monitor(output="OUT", scene_output="SO_X")
    monitor.active_workspace = make_workspace(monitor=monitor)
    a = make_client(
        workspace=monitor.active_workspace,
        floating_geom=layout.Rect(0, 0, 100, 100),
        pending_serial=5,
    )
    b = make_client(workspace=monitor.active_workspace)
    server = make_server(clients=[a, b])

    output.monitor_render(server, monitor, None)

    server.lib.wlr_scene_output_commit.assert_called_once()


def test_monitor_render_resizing():
    """A float being interactively resized does not hold the screen -- a slow
    client (e.g. Firefox) would otherwise stall the whole frame during drag."""
    monitor = make_monitor(output="OUT", scene_output="SO_X")
    monitor.active_workspace = make_workspace(monitor=monitor)
    client = make_client(
        workspace=monitor.active_workspace,
        floating_geom=layout.Rect(0, 0, 100, 100),
        pending_serial=5,
        grab=model.Grab("resize", 0, 0),
    )
    server = make_server(clients=[client])

    output.monitor_render(server, monitor, None)

    server.lib.wlr_scene_output_commit.assert_called_once()


def test_monitor_render_moving():
    """A float being interactively *moved* does not hold the screen -- move
    is a pure scene-graph reposition, no configure to wait on."""
    monitor = make_monitor(output="OUT", scene_output="SO_X")
    monitor.active_workspace = make_workspace(monitor=monitor)
    client = make_client(
        workspace=monitor.active_workspace,
        floating_geom=layout.Rect(0, 0, 100, 100),
        pending_serial=5,
        grab=model.Grab("move", 0, 0),
    )
    server = make_server(clients=[client])

    output.monitor_render(server, monitor, None)

    server.lib.wlr_scene_output_commit.assert_called_once()


def test_monitor_render_clear():
    """With every tiled window caught up to its latest configure, the paint
    runs normally."""
    monitor = make_monitor(output="OUT", scene_output="SO_X")
    monitor.active_workspace = make_workspace(monitor=monitor)
    a = make_client(workspace=monitor.active_workspace)
    b = make_client(workspace=monitor.active_workspace)
    server = make_server(clients=[a, b])

    output.monitor_render(server, monitor, None)

    server.lib.wlr_scene_output_commit.assert_called_once()
    server.lib.wlr_scene_output_send_frame_done.assert_called_once()


def test_monitor_render_arms_timer():
    """While holding a paint, monitor_render arms the safety-valve timer
    so the screen doesn't stay frozen if the app never catches up."""
    monitor = make_monitor(output="OUT", scene_output="SO_X")
    monitor.active_workspace = make_workspace(monitor=monitor)
    client = make_client(
        workspace=monitor.active_workspace,
        pending_serial=5,
    )
    server = make_server(clients=[client])

    with patch("welpy.output.client_rendered", return_value=True):
        output.monitor_render(server, monitor, None)

    monitor.frame_timer.update.assert_called_once_with(100)


def test_monitor_render_disarms_timer():
    """A clean paint disarms the safety-valve timer."""
    server = make_server()
    monitor = make_monitor(output="OUT", scene_output="SO_X")

    output.monitor_render(server, monitor, None)

    monitor.frame_timer.update.assert_called_once_with(0)


def test_xwayland_holds_paint():
    """X11 windows have no configure-ack, so they never hold the paint."""
    client = make_x11_client(workspace=make_workspace())
    assert output.client_holds_paint(make_server(), client) is False


def test_monitor_force_paint_commits():
    """The timer callback repaints the screen so its refresh loop resumes
    and monitor_render gets another shot at clearing the hold."""
    server = make_server()
    monitor = make_monitor(output="OUT", scene_output="SO_X")

    output.monitor_force_paint(server, monitor)

    server.lib.wlr_scene_output_commit.assert_called_once_with(
        "SO_X", server.ffi.NULL)

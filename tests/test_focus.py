"""Unit tests for welpy.focus: focus policy and indicators, the focus/tile
queries, and pointer-focus hit-testing."""

from unittest.mock import MagicMock, patch

import cffi

from welpy import focus, model
from welpy.layout import Rect
from tests.helpers import (
    make_server, make_client, make_x11_client, make_monitor, make_workspace,
    make_cursor, make_layer_surface, make_session_lock, make_unmanaged,
)


def test_focus_order_monotonic():
    """Each focus bumps the client's focus_order above every other client's,
    so the most-recently-focused window always has the highest value."""
    # pylint: disable=duplicate-code
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    a = make_client(workspace=m.active_workspace)
    b = make_client(workspace=m.active_workspace)
    server = make_server(monitors=[m], active_monitor=m, clients=[a, b])

    focus.bump_focus_order(server, a)
    focus.bump_focus_order(server, b)
    focus.bump_focus_order(server, a)

    assert a.focus_order > b.focus_order > 0


def test_focus_idle_noop():
    """No monitors, no clients, no focused surface -> nothing to do."""
    server = make_server()

    focus.reconcile(server)

    server.lib.wlr_seat_keyboard_notify_enter.assert_not_called()
    server.lib.wlr_seat_keyboard_clear_focus.assert_not_called()
    server.lib.wlr_xdg_toplevel_set_activated.assert_not_called()


def test_focus_window_activated():
    """With one client on the active monitor and nothing focused yet,
    activate it, raise it, and hand it the keyboard."""
    monitor = make_monitor()
    monitor.active_workspace = make_workspace(monitor=monitor)
    client = make_client(focus_order=1, workspace=monitor.active_workspace)
    server = make_server(
        monitors=[monitor], active_monitor=monitor, clients=[client])

    focus.reconcile(server)

    server.lib.wlr_xdg_toplevel_set_activated.assert_called_once_with(
        client.toplevel, True)
    server.lib.wlr_scene_node_raise_to_top.assert_called_once()
    server.lib.wlr_seat_keyboard_notify_enter.assert_called_once()
    enter_args = server.lib.wlr_seat_keyboard_notify_enter.call_args.args
    assert enter_args[1] is client.toplevel.base.surface


def test_focus_shell_outranks_window():
    """A mapped TOP/OVERLAY shell surface that wants the keyboard outranks
    any client."""
    monitor = make_monitor()
    monitor.active_workspace = make_workspace(monitor=monitor)
    client = make_client(focus_order=1, workspace=monitor.active_workspace)
    server = make_server(
        monitors=[monitor], active_monitor=monitor, clients=[client])
    ls = make_layer_surface(monitor=monitor)
    ls.layer_surface.surface.mapped = True
    ls.layer_surface.current.keyboard_interactive = 1
    monitor.layers[model.Layer.OVERLAY].append(ls)

    focus.reconcile(server)

    assert ls.focused
    server.lib.wlr_seat_keyboard_notify_enter.assert_called_once()
    enter_args = server.lib.wlr_seat_keyboard_notify_enter.call_args.args
    assert enter_args[1] is ls.layer_surface.surface
    server.lib.wlr_xdg_toplevel_set_activated.assert_not_called()


def test_focus_shell_takes_from_window():
    """When a shell surface takes the keyboard from a focused client,
    deactivate the client and route notify_enter to the shell surface."""
    monitor = make_monitor()
    monitor.active_workspace = make_workspace(monitor=monitor)
    client = make_client(focus_order=1, workspace=monitor.active_workspace)
    server = make_server(
        monitors=[monitor], active_monitor=monitor, clients=[client])
    server.seat.keyboard_state.focused_surface = client.toplevel.base.surface # pylint: disable=no-member
    ls = make_layer_surface(monitor=monitor)
    ls.layer_surface.surface.mapped = True
    ls.layer_surface.current.keyboard_interactive = 1
    monitor.layers[model.Layer.OVERLAY].append(ls)

    focus.reconcile(server)

    server.lib.wlr_xdg_toplevel_set_activated.assert_called_once_with(
        client.toplevel, False)
    assert ls.focused
    enter_args = server.lib.wlr_seat_keyboard_notify_enter.call_args.args
    assert enter_args[1] is ls.layer_surface.surface


def test_focus_cleared_when_empty():
    """A surface was focused, all candidates went away -> clear focus."""
    server = make_server()
    server.seat.keyboard_state.focused_surface = MagicMock(name="stale") # pylint: disable=no-member

    focus.reconcile(server)

    server.lib.wlr_seat_keyboard_clear_focus.assert_called_once_with(
        server.seat)
    server.lib.wlr_seat_keyboard_notify_enter.assert_not_called()


def test_focus_window_handoff():
    """Focus shifting from one client to another deactivates the previous
    one before activating the new one."""
    monitor = make_monitor()
    monitor.active_workspace = make_workspace(monitor=monitor)
    a = make_client(focus_order=1, workspace=monitor.active_workspace)
    b = make_client(focus_order=2, workspace=monitor.active_workspace)
    server = make_server(
        monitors=[monitor], active_monitor=monitor, clients=[a, b])
    server.seat.keyboard_state.focused_surface = a.toplevel.base.surface # pylint: disable=no-member

    focus.reconcile(server)

    server.lib.wlr_xdg_toplevel_set_activated.assert_any_call(
        a.toplevel, False)
    server.lib.wlr_xdg_toplevel_set_activated.assert_any_call(
        b.toplevel, True)
    enter_args = server.lib.wlr_seat_keyboard_notify_enter.call_args.args
    assert enter_args[1] is b.toplevel.base.surface


def test_focus_idempotent():
    """Re-running with the same desired state as wlroots already has emits
    no effects."""
    monitor = make_monitor()
    monitor.active_workspace = make_workspace(monitor=monitor)
    client = make_client(focus_order=1, workspace=monitor.active_workspace)
    server = make_server(
        monitors=[monitor], active_monitor=monitor, clients=[client])
    server.seat.keyboard_state.focused_surface = client.toplevel.base.surface # pylint: disable=no-member

    focus.reconcile(server)

    server.lib.wlr_seat_keyboard_notify_enter.assert_not_called()
    server.lib.wlr_seat_keyboard_clear_focus.assert_not_called()
    server.lib.wlr_xdg_toplevel_set_activated.assert_not_called()
    server.lib.wlr_scene_node_raise_to_top.assert_not_called()


def test_focus_no_resize_hold():
    """Focus changes do not create resize holds."""
    monitor = make_monitor()
    monitor.active_workspace = make_workspace(monitor=monitor)
    a = make_client(focus_order=1, workspace=monitor.active_workspace)
    b = make_client(focus_order=2, workspace=monitor.active_workspace)
    server = make_server(
        monitors=[monitor], active_monitor=monitor, clients=[a, b])
    server.seat.keyboard_state.focused_surface = a.toplevel.base.surface # pylint: disable=no-member
    server.lib.wlr_xdg_toplevel_set_activated.side_effect = [3, 7]

    focus.reconcile(server)

    assert a.pending_serial is None
    assert b.pending_serial is None


def test_focus_border_indicators():
    """apply_focus paints the new window's borders active and the previously
    focused window's borders inactive."""
    monitor = make_monitor()
    monitor.active_workspace = make_workspace(monitor=monitor)
    a = make_client(focus_order=1, workspace=monitor.active_workspace)
    b = make_client(focus_order=2, workspace=monitor.active_workspace)
    server = make_server(
        monitors=[monitor], active_monitor=monitor, clients=[a, b])
    server.seat.keyboard_state.focused_surface = a.toplevel.base.surface # pylint: disable=no-member

    focus.reconcile(server)

    color_args = [c.args for c in server.ffi.new.call_args_list]
    assert ("float[4]", model.BORDER_COLOR_ACTIVE) in color_args
    assert ("float[4]", model.BORDER_COLOR_INACTIVE) in color_args


def test_focus_shell_sticky():
    """A currently-focused shell surface keeps the keyboard when another
    qualifying surface appears, so arranging an unrelated screen doesn't
    steal focus from the launcher."""
    m1, m2 = make_monitor(), make_monitor()
    server = make_server(monitors=[m1, m2])
    focused = make_layer_surface(monitor=m1, focused=True)
    focused.layer_surface.surface.mapped = True
    focused.layer_surface.current.keyboard_interactive = 1
    m1.layers[model.Layer.OVERLAY].append(focused)
    contender = make_layer_surface(monitor=m2)
    contender.layer_surface.surface.mapped = True
    contender.layer_surface.current.keyboard_interactive = 1
    m2.layers[model.Layer.OVERLAY].append(contender)

    focus.reconcile(server)

    assert focused.focused
    assert not contender.focused


def test_focus_overlay_over_top():
    """With both TOP and OVERLAY surfaces wanting the keyboard, OVERLAY
    wins."""
    monitor = make_monitor()
    server = make_server(monitors=[monitor])
    top = make_layer_surface(monitor=monitor)
    top.layer_surface.surface.mapped = True
    top.layer_surface.current.keyboard_interactive = 1
    monitor.layers[model.Layer.TOP].append(top)
    overlay = make_layer_surface(monitor=monitor)
    overlay.layer_surface.surface.mapped = True
    overlay.layer_surface.current.keyboard_interactive = 1
    monitor.layers[model.Layer.OVERLAY].append(overlay)

    focus.reconcile(server)

    assert overlay.focused
    assert not top.focused
    enter_args = server.lib.wlr_seat_keyboard_notify_enter.call_args.args
    assert enter_args[1] is overlay.layer_surface.surface


def test_pointer_reconcile_repoints():
    """apply_focus re-points the pointer at the surface under the cursor, so a
    scene change doesn't leave a scroll/click landing on a hidden window."""
    server = make_server()

    with patch("welpy.focus.forward_pointer_motion") as fwd:
        focus.reconcile(server)

    fwd.assert_called_once_with(server, 0)


def test_pointer_reconcile_skips_grab():
    """A drag in progress suppresses the pointer reconcile apply_focus
    otherwise performs, so the grab keeps its captured surface."""
    idle = make_server()
    dragging = make_server(
        clients=[make_client(grab=model.Grab("move", 0, 0))])

    with patch("welpy.focus.forward_pointer_motion") as fwd_idle:
        focus.reconcile(idle)
    with patch("welpy.focus.forward_pointer_motion") as fwd_drag:
        focus.reconcile(dragging)

    assert fwd_idle.called
    assert not fwd_drag.called


def test_pointer_reconcile_skips_locked():
    """While locked, apply_focus routes only the keyboard to the locker and
    skips the pointer reconcile it otherwise performs."""
    unlocked = make_server()
    locked = make_server(locked=True)

    with patch("welpy.focus.forward_pointer_motion") as fwd_unlocked:
        focus.reconcile(unlocked)
    with patch("welpy.focus.forward_pointer_motion") as fwd_locked:
        focus.reconcile(locked)

    assert fwd_unlocked.called
    assert not fwd_locked.called


def test_lock_keyboard_to_lock_surface():
    """While locked, the keyboard goes to the lock surface on the active
    screen so the user can type their password."""
    monitor = make_monitor()
    lock_surface = MagicMock(name="lock_surface")
    ls = model.LockSurface(
        lock_surface=lock_surface, monitor=monitor,
        scene_tree=MagicMock(), listeners=[])
    session_lock = make_session_lock(surfaces=[ls])
    server = make_server(
        monitors=[monitor], active_monitor=monitor,
        session_lock=session_lock, locked=True)

    focus.activate_lock(server)

    server.lib.wlr_seat_keyboard_notify_enter.assert_called_once()
    assert server.lib.wlr_seat_keyboard_notify_enter.call_args.args[1] is (
        lock_surface.surface)


def test_lock_clears_keyboard():
    """With no lock surface yet, the keyboard focus is cleared so no window
    keeps receiving keys."""
    session_lock = make_session_lock(surfaces=[])
    server = make_server(session_lock=session_lock, locked=True)
    # A window still held the keyboard when the lock began.
    server.seat.keyboard_state.focused_surface = MagicMock(name="window")

    focus.activate_lock(server)

    server.lib.wlr_seat_keyboard_clear_focus.assert_called_once_with(
        server.seat)


def test_query_client_from_surface():
    """A mapped X11 window resolves from its inner wl_surface."""
    server = make_server()
    client = make_x11_client()
    server.clients = [client]

    assert focus.client_for_surface(
        server, client.xsurface.surface) is client


def test_ime_anchor_window():
    """ime_window_anchor returns the window's scene tree, its content top-left
    (scene coords offset by border minus CSD geometry), and its screen box."""
    monitor = make_monitor()
    monitor.active_workspace = make_workspace(monitor=monitor)
    client = make_x11_client(workspace=monitor.active_workspace)
    server = make_server(
        monitors=[monitor], active_monitor=monitor, clients=[client])
    server.ffi.new = cffi.FFI().new
    box = Rect(0, 0, 800, 600)

    def _coords(_node, lx, ly):
        lx[0], ly[0] = 50, 60
        return True
    server.lib.wlr_scene_node_coords.side_effect = _coords

    with patch("welpy.geometry.client_rect", return_value=Rect(5, 7, 0, 0)), \
            patch("welpy.geometry.client_layer",
                  return_value=model.Layer.TILE), \
            patch("welpy.geometry.monitor_box", return_value=box):
        scene_tree, origin, out = focus.ime_window_anchor(
            server, client.xsurface.surface)

    bw = model.BORDER_WIDTH
    assert scene_tree is client.scene_tree
    assert origin == (50 + bw - 5, 60 + bw - 7)
    assert out is box


def test_ime_anchor_non_window():
    """A surface backing no window (layer-shell/lock) has no popup anchor."""
    server = make_server(clients=[])

    assert focus.ime_window_anchor(server, MagicMock(name="surface")) is None


def test_query_grabbed_multiple_warns():
    """Only one window should be grabbed at a time; if two are, log a warning
    so the inconsistency doesn't go silent."""
    a = make_client(grab=model.Grab("move", 0, 0))
    b = make_client(grab=model.Grab("move", 0, 0))
    server = make_server(clients=[a, b])

    with patch("welpy.focus.logger") as log:
        focus.grabbed_client(server)

    log.warning.assert_called_once()


def test_query_top_per_monitor():
    """top_client picks the highest focus_order among clients visible on
    the given monitor, ignoring clients on other monitors."""
    m1 = make_monitor()
    m1.active_workspace = make_workspace(monitor=m1)
    m2 = make_monitor()
    m2.active_workspace = make_workspace(monitor=m2)
    a = make_client(toplevel="a", focus_order=1, workspace=m1.active_workspace)
    b = make_client(toplevel="b", focus_order=3, workspace=m1.active_workspace)
    c = make_client(toplevel="c", focus_order=2, workspace=m1.active_workspace)
    d = make_client(toplevel="d", focus_order=5, workspace=m2.active_workspace)
    server = make_server(clients=[a, b, c, d])

    assert focus.top_client(server, m1) is b
    assert focus.top_client(server, m2) is d


def test_query_top_empty():
    """top_client returns None when no clients are visible on the monitor."""
    server = make_server()
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)

    assert focus.top_client(server, m) is None


def test_pointer_forward_default_cursor():
    """Moving onto the background restores the default cursor image, so a
    cursor a client set earlier doesn't linger over empty space."""
    cur = MagicMock(name="cur")
    server = make_server(
        cursor=make_cursor(cursor=cur, xcursor_manager="XMGR"))
    with patch("welpy.focus.surface_at", return_value=(None, 0.0, 0.0)):
        focus.forward_pointer_motion(server, 123)

    server.lib.wlr_cursor_set_xcursor.assert_called_once_with(
        cur, "XMGR", b"default")
    server.lib.wlr_seat_pointer_clear_focus.assert_called_once_with(
        server.seat)


def test_pointer_forward_keeps_cursor():
    """Over a client surface the compositor leaves the cursor image alone so
    the app's own set-cursor request stands."""
    server = make_server(cursor=make_cursor(xcursor_manager="XMGR"))
    with patch("welpy.focus.surface_at", return_value=("SURF", 1.0, 2.0)):
        focus.forward_pointer_motion(server, 5)

    server.lib.wlr_cursor_set_xcursor.assert_not_called()
    server.lib.wlr_seat_pointer_notify_enter.assert_called_once()


def test_pointer_rebase_repoints():
    """When the surface under the cursor differs from the focused one (e.g. a
    window grew into fullscreen under a still cursor), rebase enters it so the
    next click/scroll lands there."""
    server = make_server(cursor=make_cursor(xcursor_manager="X"))
    server.seat.pointer_state.focused_surface = "OLD"
    with patch("welpy.focus.surface_at", return_value=("NEW", 3.0, 4.0)):
        focus.rebase_pointer(server, 7)

    server.lib.wlr_seat_pointer_notify_enter.assert_called_once_with(
        server.seat, "NEW", 3.0, 4.0)
    server.lib.wlr_seat_pointer_notify_motion.assert_called_once_with(
        server.seat, 7, 3.0, 4.0)


def test_pointer_rebase_noop_matched():
    """Rebase is a no-op when focus already points at the surface under the
    cursor, so a scroll in place doesn't emit a redundant motion."""
    server = make_server(cursor=make_cursor(xcursor_manager="X"))
    server.seat.pointer_state.focused_surface = "SURF"
    with patch("welpy.focus.surface_at", return_value=("SURF", 1.0, 2.0)):
        focus.rebase_pointer(server, 5)

    server.lib.wlr_seat_pointer_notify_enter.assert_not_called()
    server.lib.wlr_seat_pointer_notify_motion.assert_not_called()
    server.lib.wlr_seat_pointer_clear_focus.assert_not_called()


def test_pointer_rebase_clears():
    """With focus set but nothing under the cursor, rebase clears focus so a
    click on the background doesn't reach a stale surface."""
    server = make_server(cursor=make_cursor(xcursor_manager="X"))
    server.seat.pointer_state.focused_surface = "OLD"
    with patch("welpy.focus.surface_at", return_value=(None, 0.0, 0.0)):
        focus.rebase_pointer(server, 9)

    server.lib.wlr_seat_pointer_clear_focus.assert_called_once_with(
        server.seat)


def test_pointer_rebase_noop_empty():
    """No focus and nothing under the cursor: rebase does nothing rather than
    re-clearing an already-empty focus."""
    server = make_server(cursor=make_cursor(xcursor_manager="X"))
    server.seat.pointer_state.focused_surface = server.ffi.NULL
    with patch("welpy.focus.surface_at", return_value=(None, 0.0, 0.0)):
        focus.rebase_pointer(server, 1)

    server.lib.wlr_seat_pointer_clear_focus.assert_not_called()


def test_focus_clears_urgent():
    """Focusing an urgent window clears its urgent flag."""
    monitor = make_monitor()
    monitor.active_workspace = make_workspace(monitor=monitor)
    client = make_client(
        focus_order=1, urgent=True, workspace=monitor.active_workspace)
    server = make_server(
        ext_workspace=None, monitors=[monitor], active_monitor=monitor,
        clients=[client])

    focus.reconcile(server)

    assert not client.urgent


def test_focus_defers_to_unmanaged():
    """apply_focus keeps the keyboard on a focus-holding unmanaged surface and
    skips the normal window-focus path."""
    server = make_server()
    um = make_unmanaged()
    server.unmanaged_focus = um

    with patch("welpy.focus.top_client") as top:
        focus.reconcile(server)

    top.assert_not_called()
    server.lib.wlr_seat_keyboard_notify_enter.assert_called_once()
    assert (server.lib.wlr_seat_keyboard_notify_enter.call_args.args[1]
            is um.xsurface.surface)

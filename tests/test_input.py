"""Unit tests for welpy.input: the mouse pointer and pointer lock/confine,
drag-to-move/resize, keyboard + keybinding dispatch, and seat requests."""

from unittest.mock import MagicMock, patch

import cffi

from welpy import (  # pylint: disable=redefined-builtin
    input, layout, model)
from tests.helpers import (
    make_server, make_client, make_x11_client, make_cursor,
    make_keyboard_group, make_monitor, make_workspace,
    trigger,
)


def test_cursor_create_visible():
    """create_cursor wires the pointer to the screen layout and sets a default
    xcursor image -- the combination is what makes the cursor visible."""
    server = make_server()
    lib = server.lib

    cursor = input.create_cursor(server)

    lib.wlr_cursor_attach_output_layout.assert_called_once_with(
        lib.wlr_cursor_create.return_value, "OUTPUT_LAYOUT")
    lib.wlr_cursor_set_xcursor.assert_called_once_with(
        lib.wlr_cursor_create.return_value,
        lib.wlr_xcursor_manager_create.return_value,
        b"default")
    assert cursor.cursor is lib.wlr_cursor_create.return_value
    assert cursor.xcursor_manager is lib.wlr_xcursor_manager_create.return_value


def test_cursor_create_motion():
    """The cursor's relative-motion signal drives cursor_motion so a moving
    mouse actually moves the pointer."""
    server = make_server()
    with patch("welpy.input.cursor_motion") as handler:
        input.create_cursor(server)
        trigger(server, server.lib.welpy_cursor_motion, "MOTION_DATA")
    handler.assert_called_once_with(server, "MOTION_DATA")


def test_cursor_create_motion_absolute():
    """The cursor's absolute-motion signal drives cursor_motion_absolute so
    touchscreens / nested-backend events still position the pointer."""
    server = make_server()
    with patch("welpy.input.cursor_motion_absolute") as handler:
        input.create_cursor(server)
        trigger(
            server, server.lib.welpy_cursor_motion_absolute, "MA_DATA")
    handler.assert_called_once_with(server, "MA_DATA")


def test_cursor_create_axis():
    """The cursor's axis signal drives cursor_axis so scroll events reach
    apps."""
    server = make_server()
    with patch("welpy.input.cursor_axis") as handler:
        input.create_cursor(server)
        trigger(server, server.lib.welpy_cursor_axis, "AXIS_DATA")
    handler.assert_called_once_with(server, "AXIS_DATA")


def test_cursor_create_frame():
    """The cursor's frame signal drives cursor_frame so apps see a batch
    boundary after every grouped pointer update."""
    server = make_server()
    with patch("welpy.input.cursor_frame") as handler:
        input.create_cursor(server)
        trigger(server, server.lib.welpy_cursor_frame, "FRAME_DATA")
    handler.assert_called_once_with(server, "FRAME_DATA")


def test_cursor_create_button():
    """The cursor's button signal drives cursor_button so Alt+Left can start
    a drag-to-move and release can end it."""
    server = make_server()
    with patch("welpy.input.cursor_button") as handler:
        input.create_cursor(server)
        trigger(server, server.lib.welpy_cursor_button, "BUTTON_DATA")
    handler.assert_called_once_with(server, "BUTTON_DATA")


def test_cursor_destroy_releases():
    """destroy_cursor detaches its listeners and frees both the wlr cursor
    and its xcursor theme."""
    lib = MagicMock()
    h1, h2 = MagicMock(name="h1"), MagicMock(name="h2")
    cursor = make_cursor(
        cursor="CURSOR", xcursor_manager="XMGR", listeners=[h1, h2])

    input.destroy_cursor(lib, cursor)

    h1.remove.assert_called_once()
    h2.remove.assert_called_once()
    assert not cursor.listeners
    lib.wlr_cursor_destroy.assert_called_once_with("CURSOR")
    lib.wlr_xcursor_manager_destroy.assert_called_once_with("XMGR")


def test_cursor_motion_moves():
    """cursor_motion forwards the pointer device and delta to wlr_cursor,
    which clamps the new position to the screen layout."""
    cur = MagicMock(name="cur")
    server = make_server(cursor=make_cursor(cursor=cur, xcursor_manager="XMGR"))
    event = server.ffi.cast.return_value
    event.delta_x = 3.0
    event.delta_y = -2.5

    input.cursor_motion(server, "MOTION_DATA")

    server.lib.wlr_cursor_move.assert_called_once_with(
        cur, server.ffi.addressof.return_value, 3.0, -2.5)


def test_cursor_motion_absolute_converts():
    """cursor_motion_absolute converts an absolute position to a layout delta
    and moves through the shared path (no warp), so pointer lock/confine apply
    to touch / tablet / nested-backend devices too."""
    cur = MagicMock(name="cur")
    server = make_server(cursor=make_cursor(cursor=cur, xcursor_manager="XMGR"))
    event = server.ffi.cast.return_value
    event.x = 0.25
    event.y = 0.75

    input.cursor_motion_absolute(server, "MA_DATA")

    server.lib.wlr_cursor_absolute_to_layout_coords.assert_called_once()
    server.lib.wlr_cursor_move.assert_called_once()
    server.lib.wlr_cursor_warp_absolute.assert_not_called()


def test_cursor_motion_forwards():
    """A move over a surface forwards enter+motion so apps see hovers."""
    server = make_server(cursor=make_cursor(xcursor_manager="X"))
    server.lib.WLR_SCENE_NODE_BUFFER = "BUF"
    node = MagicMock(name="node", type="BUF")
    server.lib.wlr_scene_node_at.return_value = node

    input.cursor_motion(server, "MOTION_DATA")

    server.lib.wlr_seat_pointer_notify_enter.assert_called_once()
    server.lib.wlr_seat_pointer_notify_motion.assert_called_once()


def test_cursor_motion_empty_clears():
    """A move over empty space clears pointer focus so no app keeps
    thinking it's being hovered."""
    server = make_server(cursor=make_cursor(xcursor_manager="X"))
    server.lib.wlr_scene_node_at.return_value = server.ffi.NULL

    input.cursor_motion(server, "MOTION_DATA")

    server.lib.wlr_seat_pointer_clear_focus.assert_called_once_with(
        server.seat)
    server.lib.wlr_seat_pointer_notify_enter.assert_not_called()


def test_cursor_motion_grab_skips():
    """While dragging a window the pointer is captured -- motion isn't
    forwarded to surfaces."""
    client = make_client(
        grab=model.Grab("move", 0, 0),
        floating_geom=layout.Rect(0, 0, 100, 100),
    )
    server = make_server(
        clients=[client], cursor=make_cursor(xcursor_manager="X"))

    input.cursor_motion(server, "MOTION_DATA")

    server.lib.wlr_seat_pointer_notify_enter.assert_not_called()
    server.lib.wlr_seat_pointer_clear_focus.assert_not_called()


def test_cursor_motion_drags():
    """Motion during a grab repositions the grabbed window so it stays pinned
    to the cursor at the captured offset."""
    grabbed = make_client(
        grab=model.Grab("move", 10, 20),
        floating_geom=layout.Rect(0, 0, 100, 100),
    )
    server = make_server(
        clients=[grabbed], cursor=make_cursor(xcursor_manager="X"))
    server.cursor.cursor.x = 200.0
    server.cursor.cursor.y = 300.0

    input.cursor_motion(server, "MOTION_DATA")

    server.lib.wlr_scene_node_set_position.assert_called_once_with(
        server.ffi.addressof.return_value, 190, 280)
    assert grabbed.floating_geom == layout.Rect(190, 280, 100, 100)


def test_cursor_motion_resizes():
    """Motion during a resize grab moves the bottom-right corner by the
    cursor delta; top-left stays fixed."""
    grabbed = make_client(
        grab=model.Grab("resize", 200, 200),
        floating_geom=layout.Rect(100, 150, 100, 100),
    )
    grabbed.scene_tree.node.x = 100
    grabbed.scene_tree.node.y = 150
    server = make_server(
        clients=[grabbed], cursor=make_cursor(xcursor_manager="X"))
    server.cursor.cursor.x = 500.0
    server.cursor.cursor.y = 400.0

    with patch("welpy.geometry.resize_client") as rc:
        input.cursor_motion(server, "MOTION_DATA")

    rc.assert_called_once_with(server, grabbed, layout.Rect(100, 150, 300, 200))
    assert grabbed.floating_geom == layout.Rect(100, 150, 300, 200)


def test_cursor_motion_resize_min():
    """Resize clamps width/height to at least 1px so the window can't
    collapse to a degenerate zero-size rect."""
    grabbed = make_client(
        grab=model.Grab("resize", 200, 200),
        floating_geom=layout.Rect(100, 150, 100, 100),
    )
    grabbed.scene_tree.node.x = 100
    grabbed.scene_tree.node.y = 150
    server = make_server(
        clients=[grabbed], cursor=make_cursor(xcursor_manager="X"))
    server.cursor.cursor.x = 50.0
    server.cursor.cursor.y = 50.0

    with patch("welpy.geometry.resize_client") as rc:
        input.cursor_motion(server, "MOTION_DATA")

    rc.assert_called_once_with(server, grabbed, layout.Rect(100, 150, 1, 1))


def test_cursor_button_binding():
    """A press whose (mods, button) matches a binding runs the bound
    callable."""
    action = MagicMock()
    server = make_server(
        bindings={(0x8, 0x110): action},
        cursor=make_cursor(xcursor_manager="X"))
    server.lib.wlr_scene_node_at.return_value = server.ffi.NULL
    server.lib.wlr_keyboard_get_modifiers.return_value = 0x8
    event = server.ffi.cast.return_value
    event.button = 0x110
    event.state = server.lib.WL_POINTER_BUTTON_STATE_PRESSED

    input.cursor_button(server, "BUTTON_DATA")

    action.assert_called_once_with(server)


def test_cursor_button_focuses():
    """Pressing any mouse button over a window focuses it, so a single click
    is enough to direct keys to that window."""
    client = make_client()
    server = make_server(
        clients=[client], cursor=make_cursor(xcursor_manager="X"))
    node = MagicMock(name="node")
    node.parent = client.scene_tree
    server.lib.wlr_scene_node_at.return_value = node
    server.lib.wlr_keyboard_get_modifiers.return_value = 0
    event = server.ffi.cast.return_value
    event.button = "ANY_BUTTON"
    event.state = server.lib.WL_POINTER_BUTTON_STATE_PRESSED

    with patch("welpy.focus.focus_client") as focus_client:
        input.cursor_button(server, "BUTTON_DATA")

    focus_client.assert_called_once_with(server, client)


def test_cursor_button_active_monitor():
    """Clicking a window on another monitor makes that monitor active so
    keyboard focus follows the click."""
    # pylint: disable=duplicate-code
    m1 = make_monitor()
    m2 = make_monitor()
    ws1 = make_workspace(name="1", monitor=m1)
    ws2 = make_workspace(name="2", monitor=m2)
    m1.active_workspace = ws1
    m2.active_workspace = ws2
    client = make_client(workspace=ws2, focus_order=1)
    server = make_server(
        workspaces=[ws1, ws2], monitors=[m1, m2],
        active_monitor=m1, clients=[client],
        cursor=make_cursor(xcursor_manager="X"))
    server.lib.wlr_keyboard_get_modifiers.return_value = 0
    event = server.ffi.cast.return_value
    event.button = "ANY_BUTTON"
    event.state = server.lib.WL_POINTER_BUTTON_STATE_PRESSED

    with patch("welpy.focus.client_at", return_value=client):
        input.cursor_button(server, "BUTTON_DATA")

    assert server.active_monitor is m2
    server.lib.wlr_seat_keyboard_notify_enter.assert_called_once()
    assert server.lib.wlr_seat_keyboard_notify_enter.call_args.args[1] is (
        client.toplevel.base.surface)


def test_cursor_button_release_ends():
    """Releasing the mouse button clears the active grab."""
    client = make_client(grab=model.Grab("move", 0, 0))
    server = make_server(
        clients=[client], cursor=make_cursor(xcursor_manager="X"))
    event = server.ffi.cast.return_value
    event.state = "RELEASED"  # any sentinel != PRESSED

    input.cursor_button(server, "BUTTON_DATA")

    assert client.grab is None


def test_cursor_button_release_pointer():
    """Ending a drag re-points the pointer at whatever is now under the
    cursor, since focus was frozen on the grabbed window during the drag."""
    client = make_client(grab=model.Grab("move", 0, 0))
    server = make_server(
        clients=[client], cursor=make_cursor(xcursor_manager="X"))
    event = server.ffi.cast.return_value
    event.state = "RELEASED"  # any sentinel != PRESSED
    event.time_msec = 42

    with patch("welpy.focus.forward_pointer_motion") as fwd:
        input.cursor_button(server, "BUTTON_DATA")

    fwd.assert_called_once_with(server, 42)


def test_cursor_button_forwards():
    """A regular click forwards the button to the focused surface so apps
    see clicks."""
    client = make_client()
    server = make_server(
        clients=[client], cursor=make_cursor(xcursor_manager="X"))
    node = MagicMock(name="node")
    node.parent = client.scene_tree
    server.lib.wlr_scene_node_at.return_value = node
    server.lib.wlr_keyboard_get_modifiers.return_value = 0
    event = server.ffi.cast.return_value
    event.button = "BTN"
    event.state = server.lib.WL_POINTER_BUTTON_STATE_PRESSED
    event.time_msec = 42

    input.cursor_button(server, "BUTTON_DATA")

    server.lib.wlr_seat_pointer_notify_button.assert_called_once_with(
        server.seat, 42, "BTN", event.state)


def test_cursor_button_rebases():
    """A press re-points pointer focus before the button is delivered, so the
    first click after a scene change reaches the right surface."""
    client = make_client()
    server = make_server(
        clients=[client], cursor=make_cursor(xcursor_manager="X"))
    node = MagicMock(name="node")
    node.parent = client.scene_tree
    server.lib.wlr_scene_node_at.return_value = node
    server.lib.wlr_keyboard_get_modifiers.return_value = 0
    event = server.ffi.cast.return_value
    event.button = "BTN"
    event.state = server.lib.WL_POINTER_BUTTON_STATE_PRESSED
    event.time_msec = 8

    def rebase_first(*_a):
        server.lib.wlr_seat_pointer_notify_button.assert_not_called()

    with patch(
            "welpy.focus.rebase_pointer", side_effect=rebase_first) as rebase:
        input.cursor_button(server, "BUTTON_DATA")

    rebase.assert_called_once_with(server, 8)
    server.lib.wlr_seat_pointer_notify_button.assert_called_once()


def test_cursor_button_binding_norebase():
    """A bound press is consumed before dispatch, so it never re-points
    pointer focus."""
    action = MagicMock()
    server = make_server(
        bindings={(0x8, 0x110): action},
        cursor=make_cursor(xcursor_manager="X"))
    server.lib.wlr_scene_node_at.return_value = server.ffi.NULL
    server.lib.wlr_keyboard_get_modifiers.return_value = 0x8
    event = server.ffi.cast.return_value
    event.button = 0x110
    event.state = server.lib.WL_POINTER_BUTTON_STATE_PRESSED

    with patch("welpy.focus.rebase_pointer") as rebase:
        input.cursor_button(server, "BUTTON_DATA")

    rebase.assert_not_called()


def test_cursor_button_consumes():
    """A bound press is not forwarded to the focused surface."""
    server = make_server(
        bindings={(0x8, 0x110): lambda _: None},
        cursor=make_cursor(xcursor_manager="X"))
    server.lib.wlr_scene_node_at.return_value = server.ffi.NULL
    server.lib.wlr_keyboard_get_modifiers.return_value = 0x8
    event = server.ffi.cast.return_value
    event.button = 0x110
    event.state = server.lib.WL_POINTER_BUTTON_STATE_PRESSED

    input.cursor_button(server, "BUTTON_DATA")

    server.lib.wlr_seat_pointer_notify_button.assert_not_called()


def test_cursor_button_release_consumed():
    """Releasing to end a drag isn't forwarded; the app never saw the
    press, so it shouldn't see the release."""
    client = make_client(grab=model.Grab("move", 0, 0))
    server = make_server(
        clients=[client], cursor=make_cursor(xcursor_manager="X"))
    event = server.ffi.cast.return_value
    event.state = "RELEASED"

    input.cursor_button(server, "BUTTON_DATA")

    server.lib.wlr_seat_pointer_notify_button.assert_not_called()


def test_cursor_axis_forwards():
    """Scroll/wheel events forward to the focused surface so scrolling
    works inside apps."""
    server = make_server()
    event = server.ffi.cast.return_value
    event.time_msec = 17
    event.orientation = "V"
    event.delta = 1.0
    event.delta_discrete = 1
    event.source = "WHEEL"
    event.relative_direction = "NORMAL"

    input.cursor_axis(server, "AXIS_DATA")

    server.lib.wlr_seat_pointer_notify_axis.assert_called_once_with(
        server.seat, 17, "V", 1.0, 1, "WHEEL", "NORMAL")


def test_cursor_axis_rebases():
    """A scroll re-points pointer focus before forwarding the event, so it
    reaches a freshly-fullscreened window without a prior mouse move."""
    server = make_server(cursor=make_cursor(xcursor_manager="X"))
    event = server.ffi.cast.return_value
    event.time_msec = 17

    def rebase_first(*_a):
        server.lib.wlr_seat_pointer_notify_axis.assert_not_called()

    with patch(
            "welpy.focus.rebase_pointer", side_effect=rebase_first) as rebase:
        input.cursor_axis(server, "AXIS_DATA")

    rebase.assert_called_once_with(server, 17)
    server.lib.wlr_seat_pointer_notify_axis.assert_called_once()


def test_cursor_axis_grab():
    """While a window is being dragged, a scroll must not re-point focus off
    the grabbed window."""
    client = make_client(grab=model.Grab("move", 0, 0))
    server = make_server(
        clients=[client], cursor=make_cursor(xcursor_manager="X"))

    with patch("welpy.focus.rebase_pointer") as rebase:
        input.cursor_axis(server, "AXIS_DATA")

    rebase.assert_not_called()
    server.lib.wlr_seat_pointer_notify_axis.assert_called_once()


def test_cursor_frame_forwards():
    """The frame signal tells apps a batch of pointer events is complete."""
    server = make_server()

    input.cursor_frame(server, "FRAME_DATA")

    server.lib.wlr_seat_pointer_notify_frame.assert_called_once_with(
        server.seat)


def test_motion_relative_sent():
    """Every real pointer move streams the raw, unaccelerated delta to
    relative-pointer clients -- what games read for look/aim."""
    server = make_server(cursor=make_cursor(xcursor_manager="X"))
    event = server.ffi.cast.return_value
    event.delta_x, event.delta_y = 5.0, -1.0
    event.unaccel_dx, event.unaccel_dy = 4.0, -2.0
    event.time_msec = 2

    input.cursor_motion(server, "D")

    server.lib.wlr_relative_pointer_manager_v1_send_relative_motion.\
        assert_called_once_with(
            server.relative_pointer_mgr, server.seat, 2000,
            5.0, -1.0, 4.0, -2.0)


def test_constraint_activates():
    """A move while the pointer-focused window holds a constraint activates it,
    so the client learns the pointer is now locked/confined."""
    server = make_server(cursor=make_cursor(xcursor_manager="X"))
    server.lib.WLR_POINTER_CONSTRAINT_V1_LOCKED = "LOCKED"
    server.seat.pointer_state.focused_surface = "SURF"
    constraint = MagicMock(name="constraint", type="CONFINED")
    server.constraints = [model.PointerConstraint(constraint=constraint,
                                                listeners=[])]
    server.lib.wlr_pointer_constraints_v1_constraint_for_surface.\
        return_value = constraint

    input.cursor_motion(server, "D")

    server.lib.wlr_pointer_constraint_v1_send_activated.\
        assert_called_once_with(constraint)
    assert server.active_constraint is constraint


def test_constraint_deactivates():
    """When pointer focus leaves the constrained surface, the constraint is
    deactivated -- the focus-tied release that doubles as the unlock escape."""
    server = make_server(cursor=make_cursor(xcursor_manager="X"))
    old = MagicMock(name="old_constraint")
    server.active_constraint = old
    server.constraints = [model.PointerConstraint(constraint=old, listeners=[])]
    server.seat.pointer_state.focused_surface = server.ffi.NULL

    input.cursor_motion(server, "D")

    server.lib.wlr_pointer_constraint_v1_send_deactivated.\
        assert_called_once_with(old)
    assert server.active_constraint is None


def test_constraint_locked_pins():
    """A locked pointer is pinned: the cursor doesn't move, though the client
    still receives the raw delta."""
    server = make_server(cursor=make_cursor(xcursor_manager="X"))
    server.lib.WLR_POINTER_CONSTRAINT_V1_LOCKED = "LOCKED"
    server.seat.pointer_state.focused_surface = "SURF"
    constraint = MagicMock(name="constraint", type="LOCKED")
    server.constraints = [model.PointerConstraint(constraint=constraint,
                                                listeners=[])]
    server.lib.wlr_pointer_constraints_v1_constraint_for_surface.\
        return_value = constraint

    input.cursor_motion(server, "D")

    server.lib.wlr_cursor_move.assert_not_called()
    server.lib.wlr_relative_pointer_manager_v1_send_relative_motion.\
        assert_called_once()


def test_constraint_confined_clamps():
    """A confined pointer moves by the region-confined delta (confined
    destination minus the current surface-local position), not the raw delta."""
    real = cffi.FFI()
    server = make_server(cursor=make_cursor(xcursor_manager="X"))
    server.ffi.new.side_effect = real.new
    server.lib.WLR_POINTER_CONSTRAINT_V1_LOCKED = "LOCKED"
    server.seat.pointer_state.focused_surface = "SURF"
    constraint = MagicMock(name="constraint", type="CONFINED", surface="SURF")
    server.constraints = [model.PointerConstraint(constraint=constraint,
                                                listeners=[])]
    server.lib.wlr_pointer_constraints_v1_constraint_for_surface.\
        return_value = constraint

    def confine(_c, _x1, _y1, _x2, _y2, x_out, y_out):
        x_out[0], y_out[0] = 6.0, 2.0  # clamp the raw target (11, 2) to x=6
        return True
    server.lib.welpy_constraint_confine.side_effect = confine
    event = server.ffi.cast.return_value
    event.delta_x, event.delta_y = 10.0, 0.0

    with patch("welpy.focus.surface_at", return_value=("SURF", 1.0, 2.0)):
        input.cursor_motion(server, "D")

    # confined dest (6, 2) - surface-local start (1, 2) = delta (5, 0)
    server.lib.wlr_cursor_move.assert_called_once_with(
        server.cursor.cursor, server.ffi.addressof.return_value, 5.0, 0.0)


def test_constraint_grab_skips():
    """During a move/resize drag, constraints aren't enforced -- the drag owns
    the pointer."""
    client = make_client(
        grab=model.Grab("move", 0, 0),
        floating_geom=layout.Rect(0, 0, 100, 100))
    server = make_server(
        clients=[client], cursor=make_cursor(xcursor_manager="X"))
    server.seat.pointer_state.focused_surface = "SURF"
    constraint = MagicMock(name="constraint", type="LOCKED")
    server.lib.wlr_pointer_constraints_v1_constraint_for_surface.\
        return_value = constraint

    input.cursor_motion(server, "D")

    server.lib.wlr_pointer_constraint_v1_send_activated.assert_not_called()
    server.lib.wlr_cursor_move.assert_called_once()


def test_constraint_new_listens():
    """A new pointer-constraint request is tracked with a destroy listener so
    it can be cleaned up when it goes away."""
    server = make_server()
    constraint = server.ffi.cast.return_value

    input.constraint_new(server, "C_DATA")

    assert len(server.constraints) == 1
    assert server.constraints[0].constraint is constraint
    assert len(server.constraints[0].listeners) == 1


def test_constraint_destroy_clears():
    """Destroying the active constraint clears it and warps the cursor to the
    client's hint so the pointer reappears where the app expects."""
    server = make_server(cursor=make_cursor(xcursor_manager="X"))
    constraint = MagicMock(name="constraint")
    record = model.PointerConstraint(
        constraint=constraint, listeners=[MagicMock(name="handle")])
    server.constraints = [record]
    server.active_constraint = constraint

    with patch("welpy.input.constraint_warp_to_hint") as warp:
        input.constraint_destroy(server, record)

    warp.assert_called_once_with(server, constraint)
    assert server.active_constraint is None
    assert record not in server.constraints


def test_constraint_deactivate_destroys():
    """When focus switches to another constrained surface and deactivating the
    old (oneshot) constraint destroys it mid-call, active_constraint still ends
    on the new one -- no dangling reference, no double-activate."""
    server = make_server(cursor=make_cursor(xcursor_manager="X"))
    old = MagicMock(name="old")
    old_record = model.PointerConstraint(
        constraint=old, listeners=[MagicMock(name="handle")])
    server.constraints = [old_record]
    server.active_constraint = old
    new = MagicMock(name="new")
    server.lib.wlr_pointer_constraint_v1_send_deactivated.side_effect = \
        lambda _c: input.constraint_destroy(server, old_record)

    with patch("welpy.input.constraint_warp_to_hint") as warp:
        input.set_active_constraint(server, new)

    warp.assert_called_once_with(server, old)
    assert server.active_constraint is new
    server.lib.wlr_pointer_constraint_v1_send_activated.\
        assert_called_once_with(new)
    assert old_record not in server.constraints


def test_constraint_warp_to_hint():
    """On release the cursor warps to the client's hint, mapped from
    surface-local coords to layout via the window's content origin."""
    real = cffi.FFI()
    server = make_server(cursor=make_cursor(xcursor_manager="X"))
    server.ffi.new.side_effect = real.new
    constraint = MagicMock(name="constraint", surface="SURF")

    def hint(_c, x, y):
        x[0], y[0] = 3.0, 4.0
        return True
    server.lib.welpy_constraint_cursor_hint.side_effect = hint

    def coords(_node, ox, oy):
        ox[0], oy[0] = 100, 200
        return True
    server.lib.wlr_scene_node_coords.side_effect = coords

    with patch("welpy.focus.client_for_surface", return_value=make_client()):
        input.constraint_warp_to_hint(server, constraint)

    # content origin (100, 200) + surface-local hint (3, 4)
    server.lib.wlr_cursor_warp.assert_called_once_with(
        server.cursor.cursor, server.ffi.NULL, 103.0, 204.0)


def test_constraint_warp_no_hint():
    """With no hint set, releasing the constraint leaves the cursor where it
    is rather than warping."""
    server = make_server(cursor=make_cursor(xcursor_manager="X"))
    server.lib.welpy_constraint_cursor_hint.return_value = False

    input.constraint_warp_to_hint(server, MagicMock(name="constraint"))

    server.lib.wlr_cursor_warp.assert_not_called()


def test_begin_dragging_offset():
    """begin_dragging_client captures the cursor->window-origin offset as
    ints, which drag_client then subtracts from cursor position to
    reposition the window."""
    client = make_client()
    client.scene_tree.node.x = 100
    client.scene_tree.node.y = 150
    server = make_server(
        clients=[client], cursor=make_cursor(xcursor_manager="X"))
    server.cursor.cursor.x = 120.0
    server.cursor.cursor.y = 200.0
    node = MagicMock(name="node")
    node.parent = client.scene_tree
    server.lib.wlr_scene_node_at.return_value = node

    with patch("welpy.geometry.client_outer_rect",
               return_value=layout.Rect(100, 150, 200, 200)), \
         patch("welpy.geometry.apply_geometry"):
        input.begin_dragging_client(server)

    assert client.grab == model.Grab("move", 20, 50)


def test_begin_dragging_empty():
    """With no window under the cursor, begin_dragging_client is a no-op."""
    server = make_server(cursor=make_cursor(xcursor_manager="X"))
    server.lib.wlr_scene_node_at.return_value = server.ffi.NULL

    with patch("welpy.geometry.apply_geometry") as apply_geom:
        input.begin_dragging_client(server)

    apply_geom.assert_not_called()


def test_begin_resizing_anchor():
    """begin_resizing_client stores `cursor - current_size` so that on
    motion `cursor - grab` recovers the new size."""
    client = make_client()
    server = make_server(
        clients=[client], cursor=make_cursor(xcursor_manager="X"))
    server.cursor.cursor.x = 500.0
    server.cursor.cursor.y = 400.0
    node = MagicMock(name="node")
    node.parent = client.scene_tree
    server.lib.wlr_scene_node_at.return_value = node

    with patch("welpy.geometry.client_outer_rect",
               return_value=layout.Rect(100, 150, 300, 200)), \
         patch("welpy.geometry.apply_geometry"):
        input.begin_resizing_client(server)

    assert client.grab == model.Grab("resize", 200, 200)


def test_begin_resizing_empty():
    """With no window under the cursor, begin_resizing_client is a no-op."""
    server = make_server(cursor=make_cursor(xcursor_manager="X"))
    server.lib.wlr_scene_node_at.return_value = server.ffi.NULL

    with patch("welpy.geometry.apply_geometry") as apply_geom:
        input.begin_resizing_client(server)

    apply_geom.assert_not_called()


def test_xwayland_drag_move():
    """Dragging an X11 window sends a position-only configure so the X
    server's window coordinates follow the scene node."""
    server = make_server(cursor=make_cursor(xcursor_manager="X"))
    client = make_x11_client(
        grab=model.Grab("move", 10, 20),
        floating_geom=layout.Rect(0, 0, 204, 154),
        inner_size=(200, 150),
    )
    server.cursor.cursor.x = 200.0
    server.cursor.cursor.y = 300.0

    def record_position(_node, x, y):
        client.scene_tree.node.x = x
        client.scene_tree.node.y = y
    server.lib.wlr_scene_node_set_position.side_effect = record_position

    input.drag_client(server, client)

    server.lib.wlr_xwayland_surface_configure.assert_called_once_with(
        client.xsurface,
        190 + model.BORDER_WIDTH, 280 + model.BORDER_WIDTH, 200, 150)


def test_keyboard_create_wires_seat():
    """create_keyboard_group hands the seat the combined keyboard, so the
    seat routes key events to one shared keyboard object."""
    server = make_server()
    lib = server.lib

    kg = input.create_keyboard_group(server)

    lib.wlr_seat_set_keyboard.assert_called_once_with(
        server.seat, lib.welpy_keyboard_group_keyboard.return_value)
    assert kg.group is lib.wlr_keyboard_group_create.return_value
    assert kg.keymap is lib.xkb_keymap_new_from_names.return_value
    assert kg.xkb_context is lib.xkb_context_new.return_value


def test_keyboard_create_key():
    """The combined keyboard's key signal drives keyboard_key so presses
    on any physical keyboard reach the focused app."""
    server = make_server()
    with patch("welpy.input.keyboard_key") as handler:
        input.create_keyboard_group(server)
        trigger(server, server.lib.welpy_keyboard_key_signal, "KEY_DATA")
    handler.assert_called_once_with(server, "KEY_DATA")


def test_keyboard_create_modifiers():
    """The combined keyboard's modifiers signal drives keyboard_modifiers so
    shift-level changes reach the focused app."""
    server = make_server()
    with patch("welpy.input.keyboard_modifiers") as handler:
        input.create_keyboard_group(server)
        trigger(server, server.lib.welpy_keyboard_modifiers_signal, "MOD_DATA")
    handler.assert_called_once_with(server, "MOD_DATA")


def test_keyboard_destroy_releases():
    """destroy_keyboard_group detaches its listeners and releases both the
    wlr group and the xkb resources it owns."""
    lib = MagicMock()
    h1, h2 = MagicMock(name="h1"), MagicMock(name="h2")
    kg = make_keyboard_group(
        group="GROUP", keymap="KEYMAP", xkb_context="XKB",
        listeners=[h1, h2])

    input.destroy_keyboard_group(lib, kg)

    h1.remove.assert_called_once()
    h2.remove.assert_called_once()
    assert not kg.listeners
    lib.wlr_keyboard_group_destroy.assert_called_once_with("GROUP")
    lib.xkb_keymap_unref.assert_called_once_with("KEYMAP")
    lib.xkb_context_unref.assert_called_once_with("XKB")


def test_keycode_map_range():
    """build_keycode_map walks [min_keycode, max_keycode] inclusive, asking
    xkb for level-0 syms in layout 0."""
    lib = MagicMock()
    ffi = MagicMock()
    lib.xkb_keymap_min_keycode.return_value = 8
    lib.xkb_keymap_max_keycode.return_value = 10
    lib.xkb_keymap_key_get_syms_by_level.return_value = 0

    input.build_keycode_map(lib, ffi, "KEYMAP")

    calls = lib.xkb_keymap_key_get_syms_by_level.call_args_list
    assert [c.args[1] for c in calls] == [8, 9, 10]
    assert {c.args[2] for c in calls} == {0}
    assert {c.args[3] for c in calls} == {0}


def test_keycode_map_names():
    """Sym names from xkb_keysym_get_name become dict keys; values are
    evdev keycodes (xkb minus 8)."""
    lib = MagicMock()
    ffi = MagicMock()
    lib.xkb_keymap_min_keycode.return_value = 36
    lib.xkb_keymap_max_keycode.return_value = 36
    lib.xkb_keymap_key_get_syms_by_level.return_value = 1
    lib.xkb_keysym_get_name.return_value = 1
    ffi.string.return_value = b"j"

    result = input.build_keycode_map(lib, ffi, "KEYMAP")

    assert result == {"j": 28}


def test_keycode_map_unbound():
    """Keycodes with no level-0 syms are absent from the map."""
    lib = MagicMock()
    ffi = MagicMock()
    lib.xkb_keymap_min_keycode.return_value = 8
    lib.xkb_keymap_max_keycode.return_value = 8
    lib.xkb_keymap_key_get_syms_by_level.return_value = 0

    result = input.build_keycode_map(lib, ffi, "KEYMAP")

    assert not result


def test_input_new_keyboard():
    """A new keyboard joins the combined group so its events feed the seat
    alongside any other already-plugged-in keyboards."""
    server = make_server()
    device = server.ffi.cast.return_value
    device.type = server.lib.WLR_INPUT_DEVICE_KEYBOARD

    input.input_new(server, "DEVICE_DATA")

    server.lib.wlr_keyboard_group_add_keyboard.assert_called_once_with(
        "GROUP", server.lib.wlr_keyboard_from_input_device.return_value)


def test_input_new_keymap():
    """A new keyboard's keymap is aligned with the group's before it joins.
    wlroots rejects the join otherwise and key events never reach us."""
    server = make_server()
    device = server.ffi.cast.return_value
    device.type = server.lib.WLR_INPUT_DEVICE_KEYBOARD
    keyboard = server.lib.wlr_keyboard_from_input_device.return_value

    input.input_new(server, "DEVICE_DATA")

    server.lib.wlr_keyboard_set_keymap.assert_called_once_with(
        keyboard, "KEYMAP")
    names = [c[0] for c in server.lib.mock_calls]
    assert names.index("wlr_keyboard_set_keymap") < names.index(
        "wlr_keyboard_group_add_keyboard")


def test_input_new_other():
    """Non-keyboard devices (mice, touch, ...) are not added to the keyboard
    group -- the function silently ignores them for now."""
    server = make_server()
    device = server.ffi.cast.return_value
    device.type = "SOMETHING_ELSE"

    input.input_new(server, "DEVICE_DATA")

    server.lib.wlr_keyboard_group_add_keyboard.assert_not_called()


def test_input_new_pointer():
    """A new mouse / touchpad is attached to the cursor so its motion events
    actually move the on-screen pointer."""
    server = make_server(
        cursor=make_cursor(cursor="CURSOR", xcursor_manager="XMGR"))
    device = server.ffi.cast.return_value
    device.type = server.lib.WLR_INPUT_DEVICE_POINTER

    input.input_new(server, "DEVICE_DATA")

    server.lib.wlr_cursor_attach_input_device.assert_called_once_with(
        "CURSOR", device)


def test_keyboard_key_unbound():
    """An unbound press is forwarded to the seat, which routes it to
    whichever app currently has keyboard focus."""
    server = make_server()
    event = server.ffi.cast.return_value
    event.time_msec = 42
    event.keycode = 30
    event.state = 1

    input.keyboard_key(server, "KEY_DATA")

    server.lib.wlr_seat_keyboard_notify_key.assert_called_once_with(
        server.seat, 42, 30, 1)


def test_keyboard_key_binding():
    """A press whose (mods, keycode) matches a binding runs the bound
    callable exactly once."""
    action = MagicMock()
    server = make_server(bindings={(0x40, 28): action})
    server.lib.wlr_keyboard_get_modifiers.return_value = 0x40
    event = server.ffi.cast.return_value
    event.state = 1
    event.keycode = 28

    input.keyboard_key(server, "KEY_DATA")

    action.assert_called_once_with(server)


def test_keyboard_key_lookup_hook():
    """keyboard_key dispatches the action resolved by lookup_binding, so a
    config override (e.g. submaps) can reroute presses off the flat table."""
    action = MagicMock()
    server = make_server(bindings={})
    event = server.ffi.cast.return_value
    event.state = 1
    event.keycode = 28

    with patch("welpy.input.lookup_binding", return_value=action) as hook:
        input.keyboard_key(server, "KEY_DATA")

    hook.assert_called_once()
    action.assert_called_once_with(server)
    server.lib.wlr_seat_keyboard_notify_key.assert_not_called()


def test_keyboard_key_consumes():
    """A bound press is not forwarded to the focused app."""
    server = make_server(bindings={(0x40, 28): lambda _: None})
    server.lib.wlr_keyboard_get_modifiers.return_value = 0x40
    event = server.ffi.cast.return_value
    event.state = 1
    event.keycode = 28

    input.keyboard_key(server, "KEY_DATA")

    server.lib.wlr_seat_keyboard_notify_key.assert_not_called()


def test_keyboard_key_mods():
    """Same keycode under different mods does not match; press forwards."""
    # pylint: disable=duplicate-code
    action = MagicMock()
    server = make_server(bindings={(0x40, 28): action})
    server.lib.wlr_keyboard_get_modifiers.return_value = 0x0
    event = server.ffi.cast.return_value
    event.time_msec = 42
    event.state = 1
    event.keycode = 28

    input.keyboard_key(server, "KEY_DATA")

    action.assert_not_called()
    server.lib.wlr_seat_keyboard_notify_key.assert_called_once_with(
        server.seat, 42, 28, 1)


def test_keyboard_key_release():
    """A release of a bound keycode is forwarded; the action is not
    called -- bindings are edge-triggered on press."""
    action = MagicMock()
    server = make_server(bindings={(0x40, 28): action})
    server.lib.wlr_keyboard_get_modifiers.return_value = 0x40
    event = server.ffi.cast.return_value
    event.time_msec = 42
    event.state = 0
    event.keycode = 28

    input.keyboard_key(server, "KEY_DATA")

    action.assert_not_called()
    server.lib.wlr_seat_keyboard_notify_key.assert_called_once_with(
        server.seat, 42, 28, 0)


def test_keyboard_modifiers_forwards():
    """Modifier changes (Shift/Ctrl/...) are forwarded to the seat so the
    focused app interprets subsequent keys in the right shift level."""
    server = make_server()

    input.keyboard_modifiers(server, None)

    server.lib.wlr_seat_keyboard_notify_modifiers.assert_called_once_with(
        server.seat, server.ffi.addressof.return_value)


def test_lookup_binding_hit():
    """A bound (mods, code) resolves to its action."""
    action = MagicMock()
    server = make_server(bindings={(0x40, 28): action})

    assert input.lookup_binding(server, 0x40, 28) is action


def test_lookup_binding_miss():
    """An unbound (mods, code) resolves to None so the press is forwarded."""
    server = make_server(bindings={(0x40, 28): MagicMock()})

    assert input.lookup_binding(server, 0, 28) is None


def test_lookup_binding_passthrough():
    """While passing through, a bound action resolves to None so the press
    reaches the focused app instead."""
    server = make_server(
        bindings={(0x40, 28): MagicMock()}, passthrough=True)

    assert input.lookup_binding(server, 0x40, 28) is None


def test_lookup_binding_passthrough_toggle():
    """The passthrough toggle still resolves while passing through, so it can
    be switched back off."""
    server = make_server(
        bindings={(0x40, 28): input.toggle_passthrough}, passthrough=True)

    assert input.lookup_binding(server, 0x40, 28) is input.toggle_passthrough


def test_toggle_passthrough_flips():
    """Toggling flips the passthrough flag both ways."""
    server = make_server(passthrough=False)

    input.toggle_passthrough(server)
    assert server.passthrough is True
    input.toggle_passthrough(server)
    assert server.passthrough is False


def test_seat_set_selection():
    """An app's set-selection request is honored by putting its source on
    the seat clipboard with the request's serial."""
    server = make_server()
    event = server.ffi.cast.return_value
    event.source = "SOURCE"
    event.serial = 42

    input.seat_set_selection(server, "SEL_DATA")

    server.lib.wlr_seat_set_selection.assert_called_once_with(
        server.seat, "SOURCE", 42)


def test_seat_set_primary_selection():
    """An app's set-primary-selection request is honored on the seat so
    middle-click paste tracks the latest highlight."""
    server = make_server()
    event = server.ffi.cast.return_value
    event.source = "PSOURCE"
    event.serial = 7

    input.seat_set_primary_selection(server, "PSEL_DATA")

    server.lib.wlr_seat_set_primary_selection.assert_called_once_with(
        server.seat, "PSOURCE", 7)


def test_seat_set_cursor_focused():
    """A set-cursor request from the app under the pointer swaps the cursor
    image to the surface it supplied, at its hotspot (I-beam, resize arrow,
    or a NULL surface to hide it)."""
    cur = MagicMock(name="cur")
    server = make_server(
        cursor=make_cursor(cursor=cur, xcursor_manager="XMGR"))
    client = MagicMock(name="seat_client")
    server.lib.welpy_seat_pointer_focused_client.return_value = client
    event = server.ffi.cast.return_value
    event.seat_client = client
    event.surface = "CURSOR_SURFACE"
    event.hotspot_x = 4
    event.hotspot_y = 7

    input.seat_set_cursor(server, "SC_DATA")

    server.lib.wlr_cursor_set_surface.assert_called_once_with(
        cur, "CURSOR_SURFACE", 4, 7)


def test_seat_set_cursor_unfocused():
    """A set-cursor request from an app that doesn't hold pointer focus is
    ignored, so a background app can't hijack the cursor image."""
    server = make_server(
        cursor=make_cursor(cursor=MagicMock(), xcursor_manager="XMGR"))
    server.lib.welpy_seat_pointer_focused_client.return_value = MagicMock(
        name="focused")
    event = server.ffi.cast.return_value
    event.seat_client = MagicMock(name="other_client")
    event.surface = "CURSOR_SURFACE"

    input.seat_set_cursor(server, "SC_DATA")

    server.lib.wlr_cursor_set_surface.assert_not_called()


def test_seat_set_cursor_grab():
    """While a window is being mouse-dragged the compositor owns the cursor
    image, so set-cursor requests are ignored until the drag ends."""
    client = MagicMock(name="seat_client")
    grabbing = make_client(
        grab=model.Grab("move", 0, 0),
        floating_geom=layout.Rect(0, 0, 100, 100))
    server = make_server(
        clients=[grabbing],
        cursor=make_cursor(cursor=MagicMock(), xcursor_manager="XMGR"))
    server.lib.welpy_seat_pointer_focused_client.return_value = client
    event = server.ffi.cast.return_value
    event.seat_client = client
    event.surface = "CURSOR_SURFACE"

    input.seat_set_cursor(server, "SC_DATA")

    server.lib.wlr_cursor_set_surface.assert_not_called()

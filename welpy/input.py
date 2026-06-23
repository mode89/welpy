"""Input handling: the mouse pointer, pointer lock/confine, drag-to-move and
drag-to-resize, the keyboard and keybinding dispatch, and clipboard/cursor
requests from apps."""

from __future__ import annotations

import logging

from . import focus
from . import geometry
from . import libinput
from . import model
from .layout import Rect
from .model import (
    Client, Cursor, Grab, KeyboardGroup, PointerConstraint, Server, X11Client,
)

logger = logging.getLogger(__name__)


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

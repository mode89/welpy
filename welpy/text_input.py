"""The IME relay: brokers an app's focused text field (text-input-v3) and the
single bound input method (input-method-v2, e.g. fcitx5) in both directions so
native Wayland apps get input-method editing.

This module is a dependency sink (imports only `model`): `focus` calls
`relay_set_focus` at its keyboard-focus seams and `input` calls `forward_to_im`
on the hot keyboard path, while candidate-popup geometry is injected as the
`anchor_for_surface` callback so the leaf never imports `focus`/`geometry`.
"""

from __future__ import annotations

import logging

from .model import InputPopup, InputRelay, TextInput

logger = logging.getLogger(__name__)


def create(server, manager_text_input, manager_input_method,
           anchor_for_surface=None) -> InputRelay:
    """Register the relay against the two protocol managers (created in app).
    `anchor_for_surface(surface)` resolves the focused window's scene tree,
    content origin, and screen box to anchor the candidate popup."""
    lib, listen = server.lib, server.listen
    relay = InputRelay(
        input_method=None, keyboard_grab=None,
        text_inputs=[], input_popups=[],
        anchor_for_surface=anchor_for_surface,
        listeners=[], im_listeners=[], grab_listeners=[])
    relay.listeners.extend([
        listen(lib.welpy_text_input_mgr_new(manager_text_input),
            lambda data: text_input_new(server, data)),
        listen(lib.welpy_im_mgr_new(manager_input_method),
            lambda data: input_method_new(server, data)),
    ])
    server.listeners.extend(relay.listeners)
    return relay


def text_input_new(server, data) -> None:
    """An app advertised a text field. Wrap it and watch it, ignoring text
    fields bound to another seat."""
    ffi, lib, listen = server.ffi, server.lib, server.listen
    wlr_text_input = ffi.cast("struct wlr_text_input_v3 *", data)
    if wlr_text_input.seat != server.seat:
        return
    relay = server.input_relay
    text_input = TextInput(
        input=wlr_text_input, pending_surface=None,
        pending_listeners=[], listeners=[])
    text_input.listeners.extend([
        listen(lib.welpy_text_input_enable(wlr_text_input),
            lambda _data: text_input_enable(server, text_input)),
        listen(lib.welpy_text_input_commit(wlr_text_input),
            lambda _data: text_input_commit(server, text_input)),
        listen(lib.welpy_text_input_disable(wlr_text_input),
            lambda _data: text_input_disable(server, text_input)),
        listen(lib.welpy_text_input_destroy(wlr_text_input),
            lambda _data: text_input_destroy(server, text_input)),
    ])
    relay.text_inputs.append(text_input)


def input_method_new(server, data) -> None:
    """An input method connected. Bind the single one welpy allows (the rest
    get `unavailable`), then deliver any text field that was waiting on it."""
    ffi, lib, listen = server.ffi, server.lib, server.listen
    input_method = ffi.cast("struct wlr_input_method_v2 *", data)
    if input_method.seat != server.seat:
        return
    relay = server.input_relay
    if relay.input_method is not None:
        lib.wlr_input_method_v2_send_unavailable(input_method)
        return
    relay.input_method = input_method
    relay.im_listeners.extend([
        listen(lib.welpy_im_commit(input_method),
            lambda data: handle_im_commit(server, data)),
        listen(lib.welpy_im_grab_keyboard(input_method),
            lambda data: grab_keyboard(server, data)),
        listen(lib.welpy_im_new_popup(input_method),
            lambda data: input_method_new_popup(server, data)),
        listen(lib.welpy_im_destroy(input_method),
            lambda data: input_method_destroy(server, data)),
    ])
    relay_set_focus(server, server.seat.keyboard_state.focused_surface)


def relay_set_focus(server, surface) -> None:
    """Keyboard focus moved to `surface` (or None). For each text field: leave
    the old surface, and enter `surface` when it belongs to the same app -- or
    stash it as pending until an input method binds. Idempotent."""
    ffi, lib = server.ffi, server.lib
    relay = server.input_relay
    if relay is None:
        return
    if surface == ffi.NULL:
        surface = None
    surface_client = (lib.welpy_surface_client(surface)
                      if surface is not None else None)
    for text_input in relay.text_inputs:
        wlr = text_input.input
        if (text_input.pending_surface is not None
                and (surface != text_input.pending_surface
                     or relay.input_method is not None)):
            _set_pending_surface(server, text_input, None)
        if wlr.focused_surface != ffi.NULL:
            if surface is not None and surface == wlr.focused_surface:
                continue  # already focused here
            _disable_text_input(server, text_input, constrain=False)
            lib.wlr_text_input_v3_send_leave(wlr)
        if (surface is not None
                and surface_client == lib.welpy_text_input_client(wlr)):
            if relay.input_method is not None:
                text_input_send_enter(
                    server, text_input, surface, constrain=False)
            elif text_input.pending_surface != surface:
                _set_pending_surface(server, text_input, surface)
    constrain_popups(server)


def send_im_state(server, text_input, constrain=True) -> None:
    """App -> IME: relay the focused text field's state (surrounding text,
    content type, change cause), then `done`. Gated on the features the field
    actually advertised."""
    lib = server.lib
    relay = server.input_relay
    input_method = relay.input_method
    if input_method is None:
        return
    wlr = text_input.input
    if wlr.active_features & lib.WLR_TEXT_INPUT_V3_FEATURE_SURROUNDING_TEXT:
        lib.wlr_input_method_v2_send_surrounding_text(
            input_method, wlr.current.surrounding.text,
            wlr.current.surrounding.cursor, wlr.current.surrounding.anchor)
    lib.wlr_input_method_v2_send_text_change_cause(
        input_method, wlr.current.text_change_cause)
    if wlr.active_features & lib.WLR_TEXT_INPUT_V3_FEATURE_CONTENT_TYPE:
        lib.wlr_input_method_v2_send_content_type(
            input_method, wlr.current.content_type.hint,
            wlr.current.content_type.purpose)
    if constrain:
        constrain_popups(server)
    lib.wlr_input_method_v2_send_done(input_method)


def handle_im_commit(server, _data) -> None:
    """IME -> app: deliver the input method's preedit, committed text, and
    surrounding-text deletions to the focused field, then `done`. Skips fields
    the IME left empty."""
    ffi, lib = server.ffi, server.lib
    relay = server.input_relay
    text_input = _focused_text_input(server, relay)
    if text_input is None:
        return
    # wlroots emits this signal with NULL data; read the bound IME instead.
    current = relay.input_method.current
    if current.preedit.text != ffi.NULL:
        lib.wlr_text_input_v3_send_preedit_string(
            text_input.input, current.preedit.text,
            current.preedit.cursor_begin, current.preedit.cursor_end)
    if current.commit_text != ffi.NULL:
        lib.wlr_text_input_v3_send_commit_string(
            text_input.input, current.commit_text)
    if current.delete.before_length or current.delete.after_length:
        lib.wlr_text_input_v3_send_delete_surrounding_text(
            text_input.input, current.delete.before_length,
            current.delete.after_length)
    lib.wlr_text_input_v3_send_done(text_input.input)


def text_input_enable(server, text_input) -> None:
    """The field turned IME editing on: activate the input method and push the
    field's initial state."""
    lib = server.lib
    if server.input_relay.input_method is None:
        return
    lib.wlr_input_method_v2_send_activate(server.input_relay.input_method)
    send_im_state(server, text_input)


def text_input_commit(server, text_input) -> None:
    """The field committed an update; forward its state to the input method."""
    if not text_input.input.current_enabled:
        return
    if server.input_relay.input_method is None:
        return
    send_im_state(server, text_input)


def text_input_disable(server, text_input) -> None:
    """The field turned IME editing off; deactivate the input method."""
    if text_input.input.focused_surface == server.ffi.NULL:
        return
    _disable_text_input(server, text_input)


def text_input_destroy(server, text_input) -> None:
    """The field went away; deactivate if still enabled, then forget it."""
    relay = server.input_relay
    if text_input.input.current_enabled:
        _disable_text_input(server, text_input)
    _set_pending_surface(server, text_input, None)
    for listener in text_input.listeners:
        listener.remove()
    text_input.listeners.clear()
    if text_input in relay.text_inputs:
        relay.text_inputs.remove(text_input)


def grab_keyboard(server, data) -> None:
    """The input method grabbed the keyboard; point it at the seat keyboard and
    watch the grab for destruction."""
    ffi, lib, listen = server.ffi, server.lib, server.listen
    relay = server.input_relay
    keyboard_grab = ffi.cast(
        "struct wlr_input_method_keyboard_grab_v2 *", data)
    relay.keyboard_grab = keyboard_grab
    lib.wlr_input_method_keyboard_grab_v2_set_keyboard(
        keyboard_grab, lib.wlr_seat_get_keyboard(server.seat))
    relay.grab_listeners.append(
        listen(lib.welpy_im_grab_destroy(keyboard_grab),
            lambda data: grab_destroy(server, data)))


def grab_destroy(server, _data) -> None:
    """The keyboard grab ended; hand the seat keyboard's modifier state back to
    the real keyboard so the app sees a consistent state."""
    ffi, lib = server.ffi, server.lib
    relay = server.input_relay
    # wlroots emits this signal with NULL data; use the tracked grab instead.
    keyboard_grab = relay.keyboard_grab
    for listener in relay.grab_listeners:
        listener.remove()
    relay.grab_listeners.clear()
    relay.keyboard_grab = None
    if keyboard_grab is not None and keyboard_grab.keyboard != ffi.NULL:
        lib.wlr_seat_set_keyboard(server.seat, keyboard_grab.keyboard)
        lib.wlr_seat_keyboard_notify_modifiers(
            server.seat, ffi.addressof(keyboard_grab.keyboard, "modifiers"))


def input_method_destroy(server, _data) -> None:
    """The input method disconnected; drop its listeners but keep the focused
    surface pending and send `leave`, so a reconnecting IME resumes there."""
    relay = server.input_relay
    for listener in relay.im_listeners:
        listener.remove()
    relay.im_listeners.clear()
    relay.input_method = None
    text_input = _focused_text_input(server, relay)
    if text_input is not None:
        _set_pending_surface(
            server, text_input, text_input.input.focused_surface)
        server.lib.wlr_text_input_v3_send_leave(text_input.input)


def input_method_new_popup(server, data) -> None:
    """The IME announced a candidate popup. Track it, watch its surface, and
    place it at the focused field's caret."""
    ffi, lib, listen = server.ffi, server.lib, server.listen
    wlr_popup = ffi.cast("struct wlr_input_popup_surface_v2 *", data)
    relay = server.input_relay
    popup = InputPopup(
        popup=wlr_popup, scene_tree=None, surface=None,
        listeners=[], surface_listeners=[])
    surface = wlr_popup.surface
    popup.listeners.extend([
        listen(lib.welpy_im_popup_destroy(wlr_popup),
            lambda _data: input_popup_destroy(server, popup)),
        listen(lib.welpy_surface_map(surface),
            lambda _data: constrain_popup(server, popup)),
        listen(lib.welpy_surface_unmap(surface),
            lambda _data: input_popup_unmap(server, popup)),
        listen(lib.welpy_surface_commit(surface),
            lambda _data: constrain_popup(server, popup)),
    ])
    relay.input_popups.append(popup)
    constrain_popup(server, popup)


def constrain_popups(server) -> None:
    """Re-anchor every candidate popup at the currently focused field."""
    relay = server.input_relay
    if relay is None:
        return
    for popup in relay.input_popups:
        constrain_popup(server, popup)


def constrain_popup(server, popup) -> None:
    """Place `popup` at the caret of the focused field, or hide it when the
    focused surface isn't a window (layer-shell/lock get no popup)."""
    relay = server.input_relay
    text_input = _focused_text_input(server, relay)
    anchor = (relay.anchor_for_surface(text_input.input.focused_surface)
              if text_input is not None and relay.anchor_for_surface is not None
              else None)
    if anchor is None:
        _hide_popup(server, popup)
        return
    scene_tree, origin, box = anchor
    surface = text_input.input.focused_surface
    _attach_popup(server, popup, scene_tree)
    _watch_focused_surface(server, popup, surface)
    cursor_rect, has_rect = _caret_rect(server, text_input.input, surface)
    x, y, rect = place_popup(
        origin, cursor_rect, _surface_size(server, popup.popup.surface),
        (box.x, box.y, box.width, box.height))
    if has_rect:
        server.lib.wlr_input_popup_surface_v2_send_text_input_rectangle(
            popup.popup, server.ffi.new("struct wlr_box *", list(rect)))
    _place_node(server, popup, scene_tree, x, y)


def _place_node(server, popup, scene_tree, x, y) -> None:
    """Position the popup node at layout `(x, y)` relative to its parent window
    tree, and show it."""
    ffi, lib = server.ffi, server.lib
    slx, sly = ffi.new("int *"), ffi.new("int *")
    lib.wlr_scene_node_coords(ffi.addressof(scene_tree[0], "node"), slx, sly)
    node = ffi.addressof(popup.scene_tree.node)
    lib.wlr_scene_node_set_position(node, x - slx[0], y - sly[0])
    lib.wlr_scene_node_set_enabled(node, True)


def _caret_rect(server, wlr, surface):
    """The caret box (surface-local) the field advertised, falling back to the
    whole surface when it offers no cursor rectangle. The bool says which."""
    feature = server.lib.WLR_TEXT_INPUT_V3_FEATURE_CURSOR_RECTANGLE
    if not wlr.current.features & feature:
        return (0, 0, *_surface_size(server, surface)), False
    cr = wlr.current.cursor_rectangle
    return (cr.x, cr.y, cr.width, cr.height), True


# pylint: disable-next=too-many-locals
def place_popup(anchor_origin, cursor_rect, popup_size, output_box):
    """Anchor a candidate popup to the caret's bottom-left, flipping it up when
    it would overflow the screen bottom (and there's more room above) and left
    when it would overflow the right. Returns the popup's layout position and
    the caret rectangle relative to that position (sent back to the IME)."""
    ax, ay = anchor_origin
    cx, cy, cw, ch = cursor_rect
    pw, ph = popup_size
    ox, oy, ow, oh = output_box
    x1, y1 = ax + cx, ay + cy
    x2, y2 = x1 + cw, y1 + ch
    x, y = x1, y2
    available_right = ox + ow - x1
    available_left = x2 - ox
    if available_right < pw and available_left > available_right:
        x = x2 - pw
    available_down = oy + oh - y2
    available_up = y1 - oy
    if available_down < ph and available_up > available_down:
        y = y1 - ph
    return x, y, (x1 - x, y1 - y, cw, ch)


def input_popup_unmap(server, popup) -> None:
    """The popup stopped showing; hide its scene node."""
    _hide_popup(server, popup)


def input_popup_destroy(server, popup) -> None:
    """The popup went away; drop its listeners and scene node, then forget it.
    wlroots emits this with NULL data, so work off the tracked record."""
    relay = server.input_relay
    for listener in (*popup.listeners, *popup.surface_listeners):
        listener.remove()
    popup.listeners.clear()
    popup.surface_listeners.clear()
    if popup.scene_tree is not None:
        server.lib.wlr_scene_node_destroy(
            server.ffi.addressof(popup.scene_tree.node))
        popup.scene_tree = None
    if popup in relay.input_popups:
        relay.input_popups.remove(popup)


def _attach_popup(server, popup, scene_tree) -> None:
    """Parent the popup's subsurface tree under `scene_tree`, creating it on
    first use and re-parenting when focus moves to another window."""
    ffi, lib = server.ffi, server.lib
    if popup.scene_tree is None:
        popup.scene_tree = lib.wlr_scene_subsurface_tree_create(
            scene_tree, popup.popup.surface)
    elif popup.scene_tree.node.parent != scene_tree:
        lib.wlr_scene_node_reparent(
            ffi.addressof(popup.scene_tree.node), scene_tree)


def _watch_focused_surface(server, popup, surface) -> None:
    """Watch the anchored surface for unmap so the popup drops its scene node
    before the window's tree (its parent) is destroyed underneath it."""
    if popup.surface == surface:
        return
    for listener in popup.surface_listeners:
        listener.remove()
    popup.surface_listeners.clear()
    popup.surface = surface
    if surface is None or surface == server.ffi.NULL:
        return
    popup.surface_listeners.append(
        server.listen(server.lib.welpy_surface_unmap(surface),
            lambda _data: _focused_surface_unmap(popup)))


def drop_popups_for_scene_tree(server, scene_tree) -> None:
    """Forget popup scene nodes parented under a window tree being destroyed."""
    relay = server.input_relay
    if relay is None:
        return
    for popup in relay.input_popups:
        if (popup.scene_tree is not None
                and popup.scene_tree.node.parent == scene_tree):
            _drop_popup_with_parent(popup)


def _focused_surface_unmap(popup) -> None:
    """The anchored window unmapped; its scene tree (and our child node) is
    being torn down, so drop our references without touching the dead node."""
    _drop_popup_with_parent(popup)


def _drop_popup_with_parent(popup) -> None:
    for listener in popup.surface_listeners:
        listener.remove()
    popup.surface_listeners.clear()
    popup.surface = None
    popup.scene_tree = None


def _hide_popup(server, popup) -> None:
    """Drop the popup's scene node and stale anchored-surface watch."""
    _watch_focused_surface(server, popup, None)
    if popup.scene_tree is not None:
        server.lib.wlr_scene_node_destroy(
            server.ffi.addressof(popup.scene_tree.node))
        popup.scene_tree = None


def _surface_size(server, surface):
    """The committed (width, height) of a surface."""
    w, h = server.ffi.new("int *"), server.ffi.new("int *")
    server.lib.welpy_surface_size(surface, w, h)
    return w[0], h[0]


def im_grab_for(server, group):
    """The IME keyboard grab that should receive `group`'s input, or None.
    None when no IME grab is active, or when `group` is the IME's own virtual
    keyboard -- the loop-breaker that keeps fcitx's pass-through keys typing."""
    relay = server.input_relay
    if relay is None or relay.input_method is None:
        return None
    grab = relay.keyboard_grab
    if grab is None:
        return None
    client = getattr(group, "client", None)
    if (client is not None
            and client == server.lib.welpy_im_grab_client(relay.input_method)):
        return None
    return grab


def forward_to_im(server, group, keyboard, event) -> bool:
    """Hot keyboard path: forward `event` to the input method's keyboard grab,
    returning True when consumed, False when the key falls through to the
    app."""
    grab = im_grab_for(server, group)
    if grab is None:
        return False
    lib = server.lib
    _set_grab_keyboard(lib, grab, keyboard)
    lib.wlr_input_method_keyboard_grab_v2_send_key(
        grab, event.time_msec, event.keycode, event.state)
    return True


def forward_modifiers_to_im(server, group, keyboard, modifiers) -> bool:
    """Forward modifier state to the IME's keyboard grab so it knows Ctrl/Alt
    are held when deciding what to pass through. True when consumed; False
    routes modifiers to the app as usual."""
    grab = im_grab_for(server, group)
    if grab is None:
        return False
    lib = server.lib
    _set_grab_keyboard(lib, grab, keyboard)
    lib.wlr_input_method_keyboard_grab_v2_send_modifiers(grab, modifiers)
    return True


def text_input_send_enter(server, text_input, surface, constrain=True) -> None:
    """Tell the app's text field the IME is now editing into `surface`, then
    re-anchor the candidate popups at the new caret."""
    server.lib.wlr_text_input_v3_send_enter(text_input.input, surface)
    if constrain:
        constrain_popups(server)


def _set_grab_keyboard(lib, grab, keyboard) -> None:
    if getattr(grab, "keyboard", None) != keyboard:
        lib.wlr_input_method_keyboard_grab_v2_set_keyboard(grab, keyboard)


def _disable_text_input(server, text_input, constrain=True) -> None:
    """Deactivate the bound input method for `text_input` and flush state."""
    lib = server.lib
    if server.input_relay.input_method is None:
        return
    lib.wlr_input_method_v2_send_deactivate(server.input_relay.input_method)
    send_im_state(server, text_input, constrain=constrain)


def _set_pending_surface(server, text_input, surface) -> None:
    """Stash (or clear) the surface a text field will enter once an IME binds,
    watching it for destroy so the relay never holds a dangling surface."""
    ffi, lib, listen = server.ffi, server.lib, server.listen
    for listener in text_input.pending_listeners:
        listener.remove()
    text_input.pending_listeners.clear()
    if surface is None or surface == ffi.NULL:
        text_input.pending_surface = None
        return
    text_input.pending_surface = surface
    text_input.pending_listeners.append(
        listen(lib.welpy_surface_destroy(surface),
            lambda _data: _pending_surface_destroy(server, text_input)))


def _pending_surface_destroy(server, text_input) -> None:
    _set_pending_surface(server, text_input, None)


def _focused_text_input(server, relay):
    """The text field that currently holds IME focus, or None."""
    null = server.ffi.NULL
    return next(
        (t for t in relay.text_inputs if t.input.focused_surface != null),
        None)

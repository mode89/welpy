"""Unit tests for welpy.text_input: the IME relay wiring an app's text field
(text-input-v3) to the single bound input method (input-method-v2), plus the
keyboard loop-breaker. The wlroots bindings are mocked."""

from unittest.mock import MagicMock, call

import cffi

from welpy import layout, model, text_input
from tests.helpers import make_server, make_keyboard_group


def make_relay_server(**kwargs):
    """A Server with a fresh InputRelay and an ffi/lib prepared for the relay:
    NULL is a sentinel, cast returns its data argument, and the feature bits
    are concrete ints so the gating arithmetic works."""
    server = make_server(**kwargs)
    server.ffi.NULL = "NULL"
    server.seat.keyboard_state.focused_surface = server.ffi.NULL
    server.ffi.cast = lambda _type, data: data
    server.lib.WLR_TEXT_INPUT_V3_FEATURE_SURROUNDING_TEXT = 1
    server.lib.WLR_TEXT_INPUT_V3_FEATURE_CONTENT_TYPE = 2
    server.lib.WLR_TEXT_INPUT_V3_FEATURE_CURSOR_RECTANGLE = 4
    relay = model.InputRelay(
        input_method=None, keyboard_grab=None,
        text_inputs=[], input_popups=[], anchor_for_surface=None,
        listeners=[], im_listeners=[], grab_listeners=[])
    server.input_relay = relay
    return server, relay


def add_text_input(server, relay, focused_surface=None, pending_surface=None):
    """Append a TextInput record to `relay`, defaulting to unfocused."""
    record = model.TextInput(
        input=MagicMock(name="wlr_text_input"),
        pending_surface=pending_surface, pending_listeners=[], listeners=[])
    record.input.focused_surface = (
        server.ffi.NULL if focused_surface is None else focused_surface)
    record.input.current_enabled = False
    record.input.active_features = 0
    relay.text_inputs.append(record)
    return record


def test_create_registers_managers():
    """create wires the two manager-new signals and hangs the handles off the
    server so teardown reaps them."""
    server = make_server()

    relay = text_input.create(server, "TI_MGR", "IM_MGR")

    assert len(relay.listeners) == 2
    assert all(handle in server.listeners for handle in relay.listeners)


def test_text_input_new_appends():
    """A text field on our seat is wrapped and watched."""
    server, relay = make_relay_server()
    wlr = MagicMock()
    wlr.seat = server.seat

    text_input.text_input_new(server, wlr)

    assert len(relay.text_inputs) == 1
    assert relay.text_inputs[0].input is wlr
    assert len(relay.text_inputs[0].listeners) == 4


def test_text_input_other_seat_ignored():
    """A text field bound to a different seat is ignored."""
    server, relay = make_relay_server()
    wlr = MagicMock()
    wlr.seat = MagicMock(name="other_seat")

    text_input.text_input_new(server, wlr)

    assert not relay.text_inputs


def test_input_method_new_binds_first():
    """The first input method binds and gets its four signals watched."""
    server, relay = make_relay_server()
    im = MagicMock()
    im.seat = server.seat

    text_input.input_method_new(server, im)

    assert relay.input_method is im
    assert len(relay.im_listeners) == 4


def test_input_method_new_second_unavailable():
    """A second input method is rejected with `unavailable`; the first stays
    bound."""
    server, relay = make_relay_server()
    first = MagicMock(name="first")
    first.seat = server.seat
    text_input.input_method_new(server, first)
    second = MagicMock(name="second")
    second.seat = server.seat

    text_input.input_method_new(server, second)

    assert relay.input_method is first
    server.lib.wlr_input_method_v2_send_unavailable.assert_called_once_with(
        second)


def test_pending_flushes_on_ime_bind():
    """Text fields waiting on an IME all get `enter` the moment one binds, and
    their pending surfaces clear."""
    server, relay = make_relay_server()
    server.seat.keyboard_state.focused_surface = "SURFACE"
    first = add_text_input(server, relay, pending_surface="SURFACE")
    second = add_text_input(server, relay, pending_surface="SURFACE")
    im = MagicMock()
    im.seat = server.seat
    server.lib.welpy_surface_client.return_value = "CLIENT"
    server.lib.welpy_text_input_client.return_value = "CLIENT"

    text_input.input_method_new(server, im)

    assert server.lib.wlr_text_input_v3_send_enter.call_args_list == [
        call(first.input, "SURFACE"),
        call(second.input, "SURFACE"),
    ]
    assert first.pending_surface is None
    assert second.pending_surface is None


def test_focus_enters_same_client():
    """Focus into a surface owned by the text field's app sends `enter` when an
    IME is bound."""
    server, relay = make_relay_server()
    relay.input_method = "IM"
    record = add_text_input(server, relay)
    surface = MagicMock(name="surface")
    server.lib.welpy_surface_client.return_value = "CLIENT"
    server.lib.welpy_text_input_client.return_value = "CLIENT"

    text_input.relay_set_focus(server, surface)

    server.lib.wlr_text_input_v3_send_enter.assert_called_once_with(
        record.input, surface)


def test_focus_stashes_without_im():
    """With no IME bound, the surface is stashed as pending rather than
    entered."""
    server, relay = make_relay_server()
    relay.input_method = None
    record = add_text_input(server, relay)
    surface = MagicMock(name="surface")
    server.lib.welpy_surface_client.return_value = "CLIENT"
    server.lib.welpy_text_input_client.return_value = "CLIENT"

    text_input.relay_set_focus(server, surface)

    server.lib.wlr_text_input_v3_send_enter.assert_not_called()
    assert record.pending_surface is surface


def test_focus_skips_other_client():
    """A surface from a different app neither enters nor stashes."""
    server, relay = make_relay_server()
    relay.input_method = "IM"
    record = add_text_input(server, relay)
    server.lib.welpy_surface_client.return_value = "CLIENT_A"
    server.lib.welpy_text_input_client.return_value = "CLIENT_B"

    text_input.relay_set_focus(server, MagicMock(name="surface"))

    server.lib.wlr_text_input_v3_send_enter.assert_not_called()
    assert record.pending_surface is None


def test_focus_away_sends_leave():
    """Moving focus off a focused field deactivates the IME and sends
    `leave`."""
    server, relay = make_relay_server()
    relay.input_method = "IM"
    old = MagicMock(name="old_surface")
    record = add_text_input(server, relay, focused_surface=old)

    text_input.relay_set_focus(server, None)

    server.lib.wlr_input_method_v2_send_deactivate.assert_called_once_with("IM")
    server.lib.wlr_text_input_v3_send_leave.assert_called_once_with(
        record.input)


def test_pending_clears_on_enter():
    """A pending field entered after an IME binds does not keep stale pending
    state."""
    server, relay = make_relay_server()
    relay.input_method = "IM"
    surface = MagicMock(name="surface")
    record = add_text_input(server, relay, pending_surface=surface)
    listener = MagicMock(name="pending_listener")
    record.pending_listeners = [listener]
    server.lib.welpy_surface_client.return_value = "CLIENT"
    server.lib.welpy_text_input_client.return_value = "CLIENT"

    text_input.relay_set_focus(server, surface)

    listener.remove.assert_called_once_with()
    assert record.pending_surface is None
    server.lib.wlr_text_input_v3_send_enter.assert_called_once_with(
        record.input, surface)


def test_pending_focused_leaves_on_blur():
    """If a field somehow has both focused and pending state, focus-away still
    sends `leave`."""
    server, relay = make_relay_server()
    relay.input_method = "IM"
    old = MagicMock(name="old_surface")
    record = add_text_input(
        server, relay, focused_surface=old, pending_surface=old)

    text_input.relay_set_focus(server, None)

    assert record.pending_surface is None
    server.lib.wlr_input_method_v2_send_deactivate.assert_called_once_with("IM")
    server.lib.wlr_text_input_v3_send_leave.assert_called_once_with(
        record.input)


def test_send_im_state_gates_features():
    """send_im_state skips surrounding text / content type when the field
    didn't advertise them, but always sends change-cause and `done`."""
    server, relay = make_relay_server()
    relay.input_method = "IM"
    record = add_text_input(server, relay)
    record.input.active_features = 0

    text_input.send_im_state(server, record)

    server.lib.wlr_input_method_v2_send_surrounding_text.assert_not_called()
    server.lib.wlr_input_method_v2_send_content_type.assert_not_called()
    server.lib.wlr_input_method_v2_send_text_change_cause.assert_called_once()
    server.lib.wlr_input_method_v2_send_done.assert_called_once_with("IM")


def test_send_im_state_advertised_features():
    """send_im_state forwards surrounding text and content type when the field
    advertised both feature bits."""
    server, relay = make_relay_server()
    relay.input_method = "IM"
    record = add_text_input(server, relay)
    record.input.active_features = (
        server.lib.WLR_TEXT_INPUT_V3_FEATURE_SURROUNDING_TEXT
        | server.lib.WLR_TEXT_INPUT_V3_FEATURE_CONTENT_TYPE)

    text_input.send_im_state(server, record)

    server.lib.wlr_input_method_v2_send_surrounding_text.assert_called_once()
    server.lib.wlr_input_method_v2_send_content_type.assert_called_once()


def test_handle_im_commit_forwards_all():
    """handle_im_commit relays preedit, commit, and delete to the focused
    field, then `done`."""
    server, relay = make_relay_server()
    context = MagicMock()
    relay.input_method = context  # signal data is NULL; state read off the IME
    record = add_text_input(server, relay, focused_surface="FOCUSED")
    context.current.preedit.text = "pre"
    context.current.preedit.cursor_begin = 1
    context.current.preedit.cursor_end = 2
    context.current.commit_text = "txt"
    context.current.delete.before_length = 3
    context.current.delete.after_length = 0

    text_input.handle_im_commit(server, server.ffi.NULL)

    server.lib.wlr_text_input_v3_send_preedit_string.assert_called_once_with(
        record.input, "pre", 1, 2)
    server.lib.wlr_text_input_v3_send_commit_string.assert_called_once_with(
        record.input, "txt")
    server.lib.wlr_text_input_v3_send_delete_surrounding_text \
        .assert_called_once_with(record.input, 3, 0)
    server.lib.wlr_text_input_v3_send_done.assert_called_once_with(record.input)


def test_handle_im_commit_skips_null():
    """Fields the IME left empty (NULL text, zero delete) are not forwarded;
    `done` still fires."""
    server, relay = make_relay_server()
    context = MagicMock()
    relay.input_method = context  # signal data is NULL; state read off the IME
    record = add_text_input(server, relay, focused_surface="FOCUSED")
    context.current.preedit.text = server.ffi.NULL
    context.current.commit_text = server.ffi.NULL
    context.current.delete.before_length = 0
    context.current.delete.after_length = 0

    text_input.handle_im_commit(server, server.ffi.NULL)

    server.lib.wlr_text_input_v3_send_preedit_string.assert_not_called()
    server.lib.wlr_text_input_v3_send_commit_string.assert_not_called()
    server.lib.wlr_text_input_v3_send_delete_surrounding_text \
        .assert_not_called()
    server.lib.wlr_text_input_v3_send_done.assert_called_once_with(record.input)


def test_handle_im_commit_unfocused_noop():
    """With no focused field, an IME commit is dropped."""
    server, relay = make_relay_server()
    relay.input_method = "IM"
    add_text_input(server, relay)  # unfocused

    text_input.handle_im_commit(server, MagicMock())

    server.lib.wlr_text_input_v3_send_done.assert_not_called()


def test_forward_to_im_no_method():
    """No bound IME: the key is not consumed."""
    server, relay = make_relay_server()
    relay.input_method = None

    assert text_input.forward_to_im(
        server, make_keyboard_group(), "KB", MagicMock()) is False


def test_forward_to_im_no_grab():
    """A bound IME without a keyboard grab does not consume the key."""
    server, relay = make_relay_server()
    relay.input_method = "IM"
    relay.keyboard_grab = None

    assert text_input.forward_to_im(
        server, make_keyboard_group(), "KB", MagicMock()) is False


def test_forward_to_im_loop_breaker():
    """A key from the IME's own virtual keyboard falls through to the app
    instead of bouncing back into the grab."""
    server, relay = make_relay_server()
    relay.input_method = "IM"
    relay.keyboard_grab = "GRAB"
    server.lib.welpy_im_grab_client.return_value = "FCITX"
    group = make_keyboard_group(client="FCITX")

    assert text_input.forward_to_im(
        server, group, "KB", MagicMock()) is False
    server.lib.wlr_input_method_keyboard_grab_v2_send_key.assert_not_called()


def test_forward_to_im_physical_group():
    """A physical keyboard (client None) reaches the grab and the key is
    sent."""
    server, relay = make_relay_server()
    relay.input_method = "IM"
    relay.keyboard_grab = "GRAB"
    group = make_keyboard_group(client=None)
    event = MagicMock(time_msec=5, keycode=30, state=1)

    assert text_input.forward_to_im(server, group, "KB", event) is True
    server.lib.wlr_input_method_keyboard_grab_v2_set_keyboard \
        .assert_called_once_with("GRAB", "KB")
    server.lib.wlr_input_method_keyboard_grab_v2_send_key \
        .assert_called_once_with("GRAB", 5, 30, 1)


def test_forward_to_im_other_vkb():
    """A virtual keyboard that isn't the IME's own still reaches the grab."""
    server, relay = make_relay_server()
    relay.input_method = "IM"
    relay.keyboard_grab = "GRAB"
    server.lib.welpy_im_grab_client.return_value = "FCITX"
    group = make_keyboard_group(client="WTYPE")
    event = MagicMock(time_msec=5, keycode=30, state=1)

    assert text_input.forward_to_im(server, group, "KB", event) is True


def test_grab_keyboard_tracks_and_watches():
    """The IME's keyboard grab is tracked, pointed at the seat keyboard, and
    watched for destruction."""
    server, relay = make_relay_server()
    server.lib.wlr_seat_get_keyboard.return_value = "SEAT_KB"

    text_input.grab_keyboard(server, "GRAB")

    assert relay.keyboard_grab == "GRAB"
    server.lib.wlr_input_method_keyboard_grab_v2_set_keyboard \
        .assert_called_once_with("GRAB", "SEAT_KB")
    assert len(relay.grab_listeners) == 1


def test_grab_destroy_restores_modifiers():
    """Ending the grab drops its listeners, clears the grab, and hands the
    seat keyboard's modifier state back to the real keyboard."""
    server, relay = make_relay_server()
    listener = MagicMock(name="handle")
    relay.grab_listeners = [listener]
    # wlroots emits the grab-destroy signal with NULL data.
    relay.keyboard_grab = MagicMock(keyboard="REAL_KB")

    text_input.grab_destroy(server, server.ffi.NULL)

    listener.remove.assert_called_once_with()
    assert not relay.grab_listeners
    assert relay.keyboard_grab is None
    server.lib.wlr_seat_set_keyboard.assert_called_once_with(
        server.seat, "REAL_KB")
    server.lib.wlr_seat_keyboard_notify_modifiers.assert_called_once()


def test_grab_destroy_no_keyboard():
    """A grab that never bound a keyboard restores nothing -- guarding the
    modifier read against a NULL keyboard pointer."""
    server, relay = make_relay_server()
    relay.grab_listeners = [MagicMock(name="handle")]
    relay.keyboard_grab = MagicMock(keyboard=server.ffi.NULL)

    text_input.grab_destroy(server, server.ffi.NULL)

    assert relay.keyboard_grab is None
    server.lib.wlr_seat_set_keyboard.assert_not_called()
    server.lib.wlr_seat_keyboard_notify_modifiers.assert_not_called()


def test_forward_modifiers_physical_group():
    """With an IME grab active, modifier state is sent to the grab (so the IME
    knows Ctrl/Alt are held) rather than the app."""
    server, relay = make_relay_server()
    relay.input_method = "IM"
    relay.keyboard_grab = "GRAB"
    group = make_keyboard_group(client=None)

    assert text_input.forward_modifiers_to_im(
        server, group, "KB", "MODS") is True
    server.lib.wlr_input_method_keyboard_grab_v2_set_keyboard \
        .assert_called_once_with("GRAB", "KB")
    server.lib.wlr_input_method_keyboard_grab_v2_send_modifiers \
        .assert_called_once_with("GRAB", "MODS")


def test_forward_modifiers_loop_breaker():
    """The IME's own virtual keyboard's modifiers fall through to the app
    instead of bouncing back into the grab."""
    server, relay = make_relay_server()
    relay.input_method = "IM"
    relay.keyboard_grab = "GRAB"
    server.lib.welpy_im_grab_client.return_value = "FCITX"
    group = make_keyboard_group(client="FCITX")

    assert text_input.forward_modifiers_to_im(
        server, group, "KB", "MODS") is False
    server.lib.wlr_input_method_keyboard_grab_v2_send_modifiers \
        .assert_not_called()


def test_input_method_destroy_keeps_pending():
    """When the IME disconnects, the focused surface is kept as pending and the
    field is told to leave, so a reconnecting IME resumes there."""
    server, relay = make_relay_server()
    relay.input_method = "IM"
    relay.im_listeners = [MagicMock(name="handle")]
    record = add_text_input(server, relay, focused_surface="FOCUSED")

    text_input.input_method_destroy(server, MagicMock())

    assert relay.input_method is None
    assert record.pending_surface == "FOCUSED"
    server.lib.wlr_text_input_v3_send_leave.assert_called_once_with(
        record.input)


# --- candidate popups ---------------------------------------------------

def test_place_popup_below_left():
    """The common case anchors the popup at the caret's bottom-left, with the
    caret rectangle reported just above it."""
    x, y, rect = text_input.place_popup(
        anchor_origin=(100, 100), cursor_rect=(10, 20, 4, 16),
        popup_size=(200, 80), output_box=(0, 0, 800, 600))

    assert (x, y) == (110, 136)
    assert rect == (0, -16, 4, 16)


def test_place_popup_flips_up():
    """A caret near the bottom edge flips the popup above the caret."""
    _, y, _ = text_input.place_popup(
        anchor_origin=(0, 580), cursor_rect=(0, 0, 4, 16),
        popup_size=(200, 80), output_box=(0, 0, 800, 600))

    assert y == 580 - 80


def test_place_popup_flips_left():
    """A caret near the right edge right-aligns the popup to the caret."""
    x, _, _ = text_input.place_popup(
        anchor_origin=(780, 0), cursor_rect=(0, 0, 4, 16),
        popup_size=(200, 80), output_box=(0, 0, 800, 600))

    assert x == 780 + 4 - 200


def test_place_popup_whole_surface_fallback():
    """With no caret rectangle the popup anchors below the whole surface box."""
    x, y, _ = text_input.place_popup(
        anchor_origin=(50, 50), cursor_rect=(0, 0, 300, 200),
        popup_size=(100, 40), output_box=(0, 0, 800, 600))

    assert (x, y) == (50, 50 + 200)


def make_popup_server(sizes, scene_origin=(0, 0)):
    """A relay server whose ffi allocates real out-params and whose surface
    size / scene-coord helpers write concrete values, so constrain_popup's
    geometry arithmetic runs for real."""
    server, relay = make_relay_server()
    real = cffi.FFI()
    real.cdef("struct wlr_box { int x; int y; int width; int height; };")
    server.ffi.new = real.new

    def _size(surface, w, h):
        w[0], h[0] = sizes.get(surface, (0, 0))
    server.lib.welpy_surface_size.side_effect = _size

    def _coords(_node, lx, ly):
        lx[0], ly[0] = scene_origin
        return True
    server.lib.wlr_scene_node_coords.side_effect = _coords
    return server, relay


def add_popup(relay, surface="POPUP_SURF"):
    """Append an InputPopup with a mocked wlr popup surface."""
    wlr_popup = MagicMock(name="wlr_popup")
    wlr_popup.surface = surface
    popup = model.InputPopup(
        popup=wlr_popup, scene_tree=None, surface=None,
        listeners=[], surface_listeners=[])
    relay.input_popups.append(popup)
    return popup


def focus_field(server, relay, surface="FIELD", cursor_rect=(10, 20, 4, 16)):
    """A focused field advertising a caret rectangle."""
    record = add_text_input(server, relay, focused_surface=surface)
    record.input.current.features = \
        server.lib.WLR_TEXT_INPUT_V3_FEATURE_CURSOR_RECTANGLE
    cr = record.input.current.cursor_rectangle
    cr.x, cr.y, cr.width, cr.height = cursor_rect
    return record


def set_popup_anchor(relay, origin=(100, 100)):
    """Attach a window anchor resolver to `relay`."""
    scene_tree = MagicMock(name="scene_tree")
    relay.anchor_for_surface = lambda _s: (
        scene_tree, origin, layout.Rect(0, 0, 800, 600))
    return scene_tree


def test_popup_parents_to_window():
    """constrain_popup hangs the popup's subtree under the focused window's
    scene tree, places it, and enables it."""
    server, relay = make_popup_server(sizes={"POPUP_SURF": (200, 80)})
    scene_tree = set_popup_anchor(relay)
    subtree = server.lib.wlr_scene_subsurface_tree_create.return_value
    focus_field(server, relay)
    popup = add_popup(relay)

    text_input.constrain_popup(server, popup)

    server.lib.wlr_scene_subsurface_tree_create.assert_called_once_with(
        scene_tree, "POPUP_SURF")
    assert popup.scene_tree is subtree
    server.lib.wlr_scene_node_set_position.assert_called_once()
    server.lib.wlr_input_popup_surface_v2_send_text_input_rectangle \
        .assert_called_once()
    enabled = server.lib.wlr_scene_node_set_enabled.call_args[0][1]
    assert enabled is True


def test_popup_rebases_to_parent_tree():
    """The popup node is set at the caret's layout position rebased into its
    parent window tree's coordinates (layout pos minus the tree's origin)."""
    server, relay = make_popup_server(
        sizes={"POPUP_SURF": (200, 80)}, scene_origin=(40, 40))
    set_popup_anchor(relay)
    focus_field(server, relay)  # caret (10,20,4,16) -> layout popup (110, 136)
    popup = add_popup(relay)

    text_input.constrain_popup(server, popup)

    assert server.lib.wlr_scene_node_set_position.call_args[0][1:] == (70, 96)


def test_popup_no_caret_uses_surface():
    """A field that advertises no cursor rectangle anchors below its whole
    surface and reports no caret rectangle back to the IME."""
    server, relay = make_popup_server(
        sizes={"POPUP_SURF": (200, 80), "FIELD": (300, 200)})
    set_popup_anchor(relay)
    record = add_text_input(server, relay, focused_surface="FIELD")
    record.input.current.features = 0  # no FEATURE_CURSOR_RECTANGLE
    popup = add_popup(relay)

    text_input.constrain_popup(server, popup)

    assert server.lib.wlr_scene_node_set_position.call_args[0][1:] == (100, 300)
    server.lib.wlr_input_popup_surface_v2_send_text_input_rectangle \
        .assert_not_called()


def test_popup_hidden_without_window():
    """A non-window focused surface (anchor resolver returns None) drops the
    popup's scene node."""
    server, relay = make_popup_server(sizes={})
    relay.anchor_for_surface = lambda _s: None
    focus_field(server, relay)
    popup = add_popup(relay)
    popup.scene_tree = MagicMock(name="existing_subtree")

    text_input.constrain_popup(server, popup)

    server.lib.wlr_scene_node_destroy.assert_called_once()
    assert popup.scene_tree is None


def test_popup_new_tracks_and_places():
    """input_method_new_popup watches the popup surface (destroy/map/unmap/
    commit) and runs an initial placement."""
    server, relay = make_popup_server(sizes={"POPUP_SURF": (200, 80)})
    scene_tree = set_popup_anchor(relay)
    focus_field(server, relay)
    wlr_popup = MagicMock(name="wlr_popup")
    wlr_popup.surface = "POPUP_SURF"

    text_input.input_method_new_popup(server, wlr_popup)

    assert len(relay.input_popups) == 1
    assert len(relay.input_popups[0].listeners) == 4
    server.lib.wlr_scene_subsurface_tree_create.assert_called_once_with(
        scene_tree, "POPUP_SURF")


def test_popup_follows_focus_and_commit():
    """A focus enter and a commit each re-anchor the popup at the caret."""
    server, relay = make_popup_server(sizes={"POPUP_SURF": (200, 80)})
    set_popup_anchor(relay)
    relay.input_method = "IM"
    record = focus_field(server, relay, surface="FIELD")
    record.input.current_enabled = True
    server.lib.welpy_surface_client.return_value = "C"
    server.lib.welpy_text_input_client.return_value = "C"
    popup = add_popup(relay)

    text_input.relay_set_focus(server, "FIELD")
    enters = server.lib.wlr_scene_node_set_position.call_count
    assert enters >= 1

    text_input.text_input_commit(server, record)
    assert server.lib.wlr_scene_node_set_position.call_count > enters
    assert popup.scene_tree is not None


def test_popup_parent_unmap_drops_visible():
    """Before a parent window tree dies, popups under it drop references without
    touching the about-to-die scene node."""
    server, relay = make_relay_server()
    parent = MagicMock(name="parent_tree")
    popup = add_popup(relay)
    popup.scene_tree = MagicMock(name="subtree")
    popup.scene_tree.node.parent = parent
    popup.surface = "FIELD"
    listener = MagicMock(name="sl")
    popup.surface_listeners = [listener]

    text_input.drop_popups_for_scene_tree(server, parent)

    listener.remove.assert_called_once_with()
    assert popup.surface is None
    assert popup.scene_tree is None
    server.lib.wlr_scene_node_destroy.assert_not_called()


def test_popup_parent_unmap_drops_hidden():
    """A hidden popup still parented under a dying window tree drops its stale
    scene reference."""
    server, relay = make_relay_server()
    parent = MagicMock(name="parent_tree")
    popup = add_popup(relay)
    popup.scene_tree = MagicMock(name="subtree")
    popup.scene_tree.node.parent = parent

    text_input.drop_popups_for_scene_tree(server, parent)

    assert popup.scene_tree is None
    server.lib.wlr_scene_node_destroy.assert_not_called()


def test_popup_destroy_cleans_up():
    """Destroying a popup drops its listeners, tears down its scene node, and
    forgets it."""
    server, relay = make_relay_server()
    popup = add_popup(relay)
    popup.listeners = [MagicMock(name="l")]
    popup.surface_listeners = [MagicMock(name="sl")]
    popup.scene_tree = MagicMock(name="subtree")

    text_input.input_popup_destroy(server, popup)

    server.lib.wlr_scene_node_destroy.assert_called_once()
    assert popup.scene_tree is None
    assert popup not in relay.input_popups


def test_popup_surface_unmap_drops_node():
    """When the anchored window unmaps, the popup drops its (about-to-die) scene
    node reference without touching it."""
    server, relay = make_relay_server()
    popup = add_popup(relay)
    popup.scene_tree = MagicMock(name="subtree")
    popup.surface = "FIELD"
    popup.surface_listeners = [MagicMock(name="sl")]

    text_input._focused_surface_unmap(popup)  # pylint: disable=protected-access

    assert popup.scene_tree is None
    assert popup.surface is None
    assert not popup.surface_listeners
    server.lib.wlr_scene_node_destroy.assert_not_called()

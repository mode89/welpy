"""Unit tests for wel.py."""

from __future__ import annotations

import functools
import signal
import sys
from textwrap import dedent
from unittest.mock import ANY, MagicMock, call, patch

import pytest

import bindings
import ext_workspace
import wel


def make_server(**kwargs):
    """Build a Server, filling fields the test doesn't care about with mocks."""
    ffi = kwargs.get("ffi") or MagicMock(name="ffi")
    lib = kwargs.get("lib") or MagicMock(name="lib")
    lib.ZWLR_LAYER_SURFACE_V1_KEYBOARD_INTERACTIVITY_NONE = 0
    # Concrete press/release flag so keyboard_key's dispatch can compare it.
    lib.WL_KEYBOARD_KEY_STATE_PRESSED = 1
    # Setter return values participate in pending_serial bookkeeping; default
    # to 0 so existing tests stay caught up unless they opt in.
    lib.wlr_xdg_toplevel_set_size.return_value = 0
    lib.wlr_xdg_toplevel_set_activated.return_value = 0
    lib.wlr_xdg_toplevel_set_tiled.return_value = 0
    lib.wlr_xdg_toplevel_set_fullscreen.return_value = 0
    seat = kwargs.get("seat") or MagicMock(name="seat")
    seat.keyboard_state.focused_surface = ffi.NULL
    return wel.Server(**{
        "ffi": ffi, "lib": lib, "seat": seat,
        # Distinct handle per listen() call so listener counts add up.
        "listen": MagicMock(side_effect=lambda *_a: MagicMock(name="handle")),
        "add_signal": MagicMock(name="add_signal"),
        "add_timer": MagicMock(name="add_timer"),
        "display": "DISPLAY", "event_loop": MagicMock(name="event_loop"),
        "backend": "BACKEND", "session": MagicMock(name="session"),
        "renderer": "RENDERER", "allocator": "ALLOCATOR",
        "compositor": MagicMock(name="compositor"),
        "output_layout": "OUTPUT_LAYOUT",
        # Accessed structurally (scene.tree); leave auto-mocked.
        "scene": MagicMock(name="scene"), "scene_layout": "SCENE_LAYOUT",
        "xdg_shell": MagicMock(name="xdg_shell"),
        "layer_shell": MagicMock(name="layer_shell"),
        "cursor": MagicMock(name="cursor"),
        "keyboard_group": make_keyboard_group(
            group="GROUP", keymap="KEYMAP", xkb_context="XKB"),
        "monitors": [], "active_monitor": None, "clients": [],
        "workspaces": [], "previous_workspace": MagicMock(name="prev_ws"),
        "ext_workspace": MagicMock(name="ext_workspace"),
        "layers": {layer: MagicMock(name=layer.name.lower())
                   for layer in wel.Layer},
        "keycode": {}, "bindings": {}, "listeners": [],
        **kwargs,
    })


def make_client(**kwargs):
    """Build a Client, filling fields the test doesn't care about."""
    toplevel = kwargs.get("toplevel") or MagicMock()
    # Default to caught-up so pending_serial is a no-op unless the test opts
    # in. Raw-string toplevels skip this since they never hit setter wrappers.
    if isinstance(toplevel, MagicMock):
        toplevel.base.current.configure_serial = 0
        toplevel.requested.fullscreen = False
        toplevel.parent = None
    kwargs["toplevel"] = toplevel
    return wel.Client(**{
        "scene_tree": MagicMock(),
        "xdg_tree": MagicMock(),
        "borders": tuple(MagicMock() for _ in range(4)),
        "focus_order": 0, "urgent": False, "grab": None,
        "floating_geom": None,
        "workspace": None, "listeners": [],
        "pending_serial": None,
        "decoration": None, "handle": None,
        "inner_size": None,
        **kwargs,
    })


def make_monitor(**kwargs):
    """Build a Monitor, filling fields the test doesn't care about."""
    return wel.Monitor(**{
        "output": MagicMock(), "scene_output": MagicMock(),
        "layers": {layer: [] for layer in wel.SHELL_LAYERS},
        "window_area": wel.Rect(0, 0, 800, 600),
        "active_workspace": None,
        "frame_timer": MagicMock(name="frame_timer"),
        "listeners": [],
        **kwargs,
    })


def make_workspace(**kwargs):
    """Build a Workspace, filling fields the test doesn't care about."""
    return wel.Workspace(**{
        "name": "1",
        "monitor": None,
        "fullscreen": None,
        **kwargs,
    })


def make_cursor(**kwargs):
    """Build a Cursor, filling fields the test doesn't care about."""
    return wel.Cursor(**{
        "cursor": MagicMock(), "xcursor_manager": MagicMock(),
        "listeners": [],
        **kwargs,
    })


def make_keyboard_group(**kwargs):
    """Build a KeyboardGroup, filling fields the test doesn't care about."""
    return wel.KeyboardGroup(**{
        "group": MagicMock(), "keymap": MagicMock(),
        "xkb_context": MagicMock(), "listeners": [],
        **kwargs,
    })


def make_keycode_map():
    """Stand-in keycode map covering every key referenced by built-in
    bindings, so `setup()` can build `server.bindings` without KeyError."""
    return {"Return": 28, "q": 16, "j": 36, "k": 37, "f": 33, "z": 44,
            "e": 18, "space": 57, "h": 35, "l": 38, "Tab": 15,
            "1": 2, "2": 3, "3": 4, "4": 5, "5": 6,
            "6": 7, "7": 8, "8": 9, "9": 10, "0": 11,
            "F1": 59, "F2": 60, "F3": 61, "F4": 62, "F5": 63, "F6": 64,
            "F7": 65, "F8": 66, "F9": 67, "F10": 68, "F11": 87,
            "F12": 88}


def trigger(server, signal_accessor, data):
    """Invoke the callback registered with `listen` for this wlroots signal,
    simulating wlroots firing the event with `data`."""
    target = signal_accessor.return_value
    for c in server.listen.mock_calls:
        if c.args and c.args[0] is target:
            return c.args[1](data)
    raise AssertionError(f"no callback registered for {signal_accessor}")


# --- setup ----------------------------------------------------------------


def test_setup_seat_caps():
    """Setup advertises pointer + keyboard on the seat so clients bind
    both wl_pointer and wl_keyboard from their first connect."""
    ffi = MagicMock(name="ffi")
    lib = MagicMock(name="lib")
    lib.WL_SEAT_CAPABILITY_POINTER = 1
    lib.WL_SEAT_CAPABILITY_KEYBOARD = 2
    listen = MagicMock(side_effect=lambda *_a: MagicMock())
    build = (ffi, lib, listen, MagicMock(), MagicMock())
    with patch("wel.bindings.build", return_value=build), \
         patch("wel.build_keycode_map",
               return_value=make_keycode_map()):
        wel.setup()

    lib.wlr_seat_set_capabilities.assert_called_once_with(
        lib.wlr_seat_create.return_value, 3)


def test_setup_keycode():
    """Setup populates server.keycode from the default keymap so bindings
    can reference keys by name."""
    ffi = MagicMock(name="ffi")
    lib = MagicMock(name="lib")
    listen = MagicMock(side_effect=lambda *_a: MagicMock())
    build = (ffi, lib, listen, MagicMock(), MagicMock())
    with patch("wel.bindings.build", return_value=build), \
         patch("wel.build_keycode_map",
               return_value=make_keycode_map()) as bm:
        server = wel.setup()

    bm.assert_called_once_with(lib, ffi, server.keyboard_group.keymap)
    assert server.keycode == make_keycode_map()


def test_modkey_super():
    """modkey is the Super key so bindings don't clash with app shortcuts."""
    server = MagicMock()
    server.lib.WLR_MODIFIER_LOGO = 0x40

    assert wel.modkey(server) == 0x40


def test_setup_bindings():
    """Setup registers compositor bindings as (mods, code) tuples mapped
    to zero-arg callables."""
    ffi = MagicMock(name="ffi")
    lib = MagicMock(name="lib")
    lib.WLR_MODIFIER_LOGO = 0x40
    lib.WLR_MODIFIER_SHIFT = 0x1
    lib.WLR_MODIFIER_CTRL = 0x4
    lib.WLR_MODIFIER_ALT = 0x8
    lib.BTN_LEFT = 0x110
    lib.BTN_RIGHT = 0x111
    listen = MagicMock(side_effect=lambda *_a: MagicMock())
    build = (ffi, lib, listen, MagicMock(), MagicMock())
    with patch("wel.bindings.build", return_value=build), \
         patch("wel.build_keycode_map",
               return_value=make_keycode_map()):
        server = wel.setup()

    assert server.bindings
    for key, action in server.bindings.items():
        assert isinstance(key, tuple) and len(key) == 2
        assert all(isinstance(x, int) for x in key)
        assert callable(action)


def test_setup_clipboard_managers():
    """Setup creates the clipboard protocol globals so apps and clipboard
    tools can exchange selections."""
    ffi = MagicMock(name="ffi")
    lib = MagicMock(name="lib")
    listen = MagicMock(side_effect=lambda *_a: MagicMock())
    build = (ffi, lib, listen, MagicMock(), MagicMock())
    with patch("wel.bindings.build", return_value=build), \
         patch("wel.build_keycode_map",
               return_value=make_keycode_map()):
        server = wel.setup()

    lib.wlr_primary_selection_v1_device_manager_create.assert_called_once_with(
        server.display)
    lib.wlr_data_control_manager_v1_create.assert_called_once_with(
        server.display)
    lib.wlr_ext_data_control_manager_v1_create.assert_called_once_with(
        server.display, 1)


def test_seat_set_selection():
    """An app's set-selection request is honored by putting its source on
    the seat clipboard with the request's serial."""
    server = make_server()
    event = server.ffi.cast.return_value
    event.source = "SOURCE"
    event.serial = 42

    wel.seat_set_selection(server, "SEL_DATA")

    server.lib.wlr_seat_set_selection.assert_called_once_with(
        server.seat, "SOURCE", 42)


def test_seat_set_primary_selection():
    """An app's set-primary-selection request is honored on the seat so
    middle-click paste tracks the latest highlight."""
    server = make_server()
    event = server.ffi.cast.return_value
    event.source = "PSOURCE"
    event.serial = 7

    wel.seat_set_primary_selection(server, "PSEL_DATA")

    server.lib.wlr_seat_set_primary_selection.assert_called_once_with(
        server.seat, "PSOURCE", 7)


# --- teardown --------------------------------------------------------------


def test_teardown_order():
    """Shutdown calls wlroots destructors in the only valid order:
    clients first, then backend, then display."""
    server = make_server()

    wel.teardown(server)

    names = [c[0] for c in server.lib.mock_calls]
    expected = [
        "wl_display_destroy_clients",
        "wlr_backend_destroy",
        "wl_display_destroy",
    ]
    positions = [names.index(n) for n in expected]
    assert positions == sorted(positions)


def test_teardown_detach():
    """Server-level listeners must be detached before wlroots destroys
    their owners; otherwise wlroots' assertions fire on shutdown."""
    server = make_server()
    handle = MagicMock(name="handle")
    server.listeners.append(handle)

    manager = MagicMock()
    manager.attach_mock(handle.remove, "remove")
    manager.attach_mock(
        server.lib.wl_display_destroy_clients, "destroy_clients")

    wel.teardown(server)

    assert manager.mock_calls[:2] == [
        call.remove(),
        call.destroy_clients("DISPLAY"),
    ]
    assert not server.listeners


# --- monitor lifecycle -----------------------------------------------------


def test_monitor_new_order():
    """A new screen is fully configured (mode, enable, commit) before
    being placed in the layout and exposed to the scene."""
    server = make_server()

    wel.monitor_new(server, "OUTPUT_DATA")

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


def test_monitor_new_appends():
    """Each new screen produces exactly one Monitor in server.monitors."""
    server = make_server()

    wel.monitor_new(server, "OUTPUT_DATA")

    assert len(server.monitors) == 1


def test_monitor_new_frame():
    """The screen's frame signal drives monitor_render so painting happens
    once per refresh."""
    server = make_server()
    with patch("wel.monitor_render") as render:
        wel.monitor_new(server, "OUTPUT_DATA")
        trigger(server, server.lib.welpy_output_frame, "FRAME_DATA")
    render.assert_called_once_with(server, server.monitors[0], "FRAME_DATA")


def test_monitor_new_request_state():
    """The screen's request_state signal drives monitor_request_state so the
    nested-backend window can ask to resize the screen at runtime."""
    server = make_server()
    with patch("wel.monitor_request_state") as handler:
        wel.monitor_new(server, "OUTPUT_DATA")
        trigger(server, server.lib.welpy_output_request_state, "RS_DATA")
    handler.assert_called_once_with(server, server.monitors[0], "RS_DATA")


def test_monitor_request_state_commits():
    """Applying the requested state means committing it on the output, which
    is what actually triggers the mode/size change."""
    server = make_server()
    monitor = make_monitor(output="OUT", scene_output="SO")
    event = server.ffi.cast.return_value
    event.state = "REQUESTED_STATE"

    wel.monitor_request_state(server, monitor, "RS_DATA")

    server.lib.wlr_output_commit_state.assert_called_once_with(
        "OUT", "REQUESTED_STATE")


def test_monitor_new_destroy():
    """The screen's destroy signal triggers monitor_cleanup so an unplug
    self-cleans without leaks."""
    server = make_server()
    with patch("wel.monitor_cleanup") as cleanup:
        wel.monitor_new(server, "OUTPUT_DATA")
        trigger(server, server.lib.welpy_output_destroy_signal, "DESTROY_DATA")
    cleanup.assert_called_once_with(server, server.monitors[0], "DESTROY_DATA")


def test_monitor_cleanup_drops():
    """Cleanup detaches every listener and removes the monitor from the
    server's tracking list."""
    h1, h2 = MagicMock(name="h1"), MagicMock(name="h2")
    monitor = make_monitor(scene_output="SO", listeners=[h1, h2])
    server = make_server(monitors=[monitor])

    wel.monitor_cleanup(server, monitor, None)

    h1.remove.assert_called_once()
    h2.remove.assert_called_once()
    assert not monitor.listeners
    assert not server.monitors


def test_monitor_new_timer():
    """A new screen gets a safety-valve timer wired to monitor_force_paint
    so its refresh loop can be unstuck if an app is slow to catch up."""
    server = make_server()

    with patch("wel.monitor_force_paint") as forced:
        wel.monitor_new(server, "OUTPUT_DATA")
        monitor = server.monitors[0]
        server.add_timer.assert_called_once()
        callback = server.add_timer.call_args.args[0]
        callback()

    forced.assert_called_once_with(server, monitor)
    assert monitor.frame_timer is server.add_timer.return_value


def test_monitor_cleanup_removes_timer():
    """Cleanup detaches the safety-valve timer alongside other listeners."""
    timer = MagicMock(name="frame_timer")
    monitor = make_monitor(scene_output="SO", frame_timer=timer)
    server = make_server(monitors=[monitor])

    wel.monitor_cleanup(server, monitor, None)

    timer.remove.assert_called_once()


def test_monitor_render_order():
    """Each frame paints first, then notifies visible apps -- both calls
    targeting this monitor's own scene_output."""
    server = make_server()
    monitor = make_monitor(output="OUT", scene_output="SO_X")

    wel.monitor_render(server, monitor, None)

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

    wel.monitor_render(server, monitor, None)

    server.lib.wlr_scene_output_commit.assert_not_called()
    server.lib.wlr_scene_output_send_frame_done.assert_called_once()


def test_monitor_render_fullscreen():
    """A fullscreen window suppresses the hold: the occluded tiled window
    behind it never gets frame-done to ack, so its pending configure must
    not freeze the screen."""
    monitor = make_monitor(output="OUT", scene_output="SO_X")
    monitor.active_workspace = make_workspace(monitor=monitor)
    full = make_client(workspace=monitor.active_workspace)
    monitor.active_workspace.fullscreen = full
    hidden = make_client(
        workspace=monitor.active_workspace,
        pending_serial=5,
    )
    server = make_server(clients=[full, hidden])

    wel.monitor_render(server, monitor, None)

    server.lib.wlr_scene_output_commit.assert_called_once()


def test_monitor_render_floating():
    """A floating window's pending configure does not hold the screen --
    floating windows aren't synchronized with the layout."""
    monitor = make_monitor(output="OUT", scene_output="SO_X")
    monitor.active_workspace = make_workspace(monitor=monitor)
    a = make_client(
        workspace=monitor.active_workspace,
        floating_geom=wel.Rect(0, 0, 100, 100),
        pending_serial=5,
    )
    b = make_client(workspace=monitor.active_workspace)
    server = make_server(clients=[a, b])

    wel.monitor_render(server, monitor, None)

    server.lib.wlr_scene_output_commit.assert_called_once()


def test_monitor_render_resizing():
    """A float being interactively resized does not hold the screen -- a slow
    client (e.g. Firefox) would otherwise stall the whole frame during drag."""
    monitor = make_monitor(output="OUT", scene_output="SO_X")
    monitor.active_workspace = make_workspace(monitor=monitor)
    client = make_client(
        workspace=monitor.active_workspace,
        floating_geom=wel.Rect(0, 0, 100, 100),
        pending_serial=5,
        grab=wel.Grab("resize", 0, 0),
    )
    server = make_server(clients=[client])

    wel.monitor_render(server, monitor, None)

    server.lib.wlr_scene_output_commit.assert_called_once()


def test_monitor_render_moving():
    """A float being interactively *moved* does not hold the screen -- move
    is a pure scene-graph reposition, no configure to wait on."""
    monitor = make_monitor(output="OUT", scene_output="SO_X")
    monitor.active_workspace = make_workspace(monitor=monitor)
    client = make_client(
        workspace=monitor.active_workspace,
        floating_geom=wel.Rect(0, 0, 100, 100),
        pending_serial=5,
        grab=wel.Grab("move", 0, 0),
    )
    server = make_server(clients=[client])

    wel.monitor_render(server, monitor, None)

    server.lib.wlr_scene_output_commit.assert_called_once()


def test_monitor_render_clear():
    """With every tiled window caught up to its latest configure, the paint
    runs normally."""
    monitor = make_monitor(output="OUT", scene_output="SO_X")
    monitor.active_workspace = make_workspace(monitor=monitor)
    a = make_client(workspace=monitor.active_workspace)
    b = make_client(workspace=monitor.active_workspace)
    server = make_server(clients=[a, b])

    wel.monitor_render(server, monitor, None)

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

    wel.monitor_render(server, monitor, None)

    monitor.frame_timer.update.assert_called_once_with(100)


def test_monitor_render_disarms_timer():
    """A clean paint disarms the safety-valve timer."""
    server = make_server()
    monitor = make_monitor(output="OUT", scene_output="SO_X")

    wel.monitor_render(server, monitor, None)

    monitor.frame_timer.update.assert_called_once_with(0)


def test_monitor_force_paint_commits():
    """The timer callback repaints the screen so its refresh loop resumes
    and monitor_render gets another shot at clearing the hold."""
    server = make_server()
    monitor = make_monitor(output="OUT", scene_output="SO_X")

    wel.monitor_force_paint(server, monitor)

    server.lib.wlr_scene_output_commit.assert_called_once_with(
        "SO_X", server.ffi.NULL)


# --- client lifecycle ------------------------------------------------------


def test_client_commit_initial():
    """The window's first commit triggers the initial configure xdg-shell
    requires before any pixels can be shown."""
    server = make_server()
    toplevel = MagicMock()
    toplevel.base.initial_commit = True
    client = make_client(toplevel=toplevel, scene_tree=None)

    wel.client_commit(server, client, None)

    server.lib.wlr_xdg_toplevel_set_size.assert_called_once_with(toplevel, 0, 0)


def test_client_commit_subsequent():
    """Later commits don't re-send the initial configure."""
    server = make_server()
    toplevel = MagicMock()
    toplevel.base.initial_commit = False
    client = make_client(toplevel=toplevel, scene_tree=None)

    wel.client_commit(server, client, None)

    server.lib.wlr_xdg_toplevel_set_size.assert_not_called()


def test_client_commit_clears():
    """A commit after the client renders the latest configure releases the
    screen hold."""
    server = make_server()
    client = make_client(scene_tree=MagicMock(), pending_serial=7)
    client.toplevel.base.initial_commit = False
    client.toplevel.base.current.configure_serial = 9

    wel.client_commit(server, client, None)

    assert client.pending_serial is None
    server.lib.wlr_xdg_toplevel_set_size.assert_not_called()


def test_client_commit_holds():
    """A commit before the client catches up leaves pending_serial in place
    so the screen keeps waiting."""
    server = make_server()
    client = make_client(scene_tree=MagicMock(), pending_serial=7)
    client.toplevel.base.initial_commit = False
    client.toplevel.base.current.configure_serial = 3

    wel.client_commit(server, client, None)

    assert client.pending_serial == 7


def test_client_commit_initial_pending():
    """The initial configure's serial is recorded so the screen waits for
    the client's first render."""
    server = make_server()
    server.lib.wlr_xdg_toplevel_set_size.return_value = 11
    toplevel = MagicMock()
    toplevel.base.initial_commit = True
    toplevel.base.current.configure_serial = 0
    client = make_client(toplevel=toplevel, scene_tree=None)

    wel.client_commit(server, client, None)

    assert client.pending_serial == 11


def test_client_commit_reclips():
    """On a post-map commit, the surface clip is refreshed with the current
    xdg geometry offset so the picture stays correct when the client drops or
    adds its CSD shadow (e.g. on entering / leaving fullscreen)."""
    server = make_server()
    toplevel = MagicMock()
    toplevel.base.initial_commit = False
    toplevel.base.geometry.x = 0
    toplevel.base.geometry.y = 0
    client = make_client(
        toplevel=toplevel, scene_tree=MagicMock(), inner_size=(800, 600))

    wel.client_commit(server, client, None)

    server.ffi.new.assert_any_call("struct wlr_box *", [0, 0, 800, 600])
    server.lib.wlr_scene_subsurface_tree_set_clip.assert_called_once_with(
        server.ffi.addressof.return_value, server.ffi.new.return_value)


def test_client_commit_premap():
    """Before the first resize, inner_size is unset and we have no idea what
    to clip to; the initial commit must not touch the clip."""
    server = make_server()
    toplevel = MagicMock()
    toplevel.base.initial_commit = True
    client = make_client(toplevel=toplevel, scene_tree=None, inner_size=None)

    wel.client_commit(server, client, None)

    server.lib.wlr_scene_subsurface_tree_set_clip.assert_not_called()


def test_client_new_no_insert():
    """client_new only attaches listeners; the client joins server.clients
    at map time so siblings don't reflow before the new window is ready."""
    server = make_server()

    wel.client_new(server, "TOPLEVEL_DATA")

    assert not server.clients
    server.lib.wlr_scene_tree_create.assert_not_called()


def test_client_map_inserts_front():
    """A newly mapped window goes to the front of server.clients so it
    becomes the master tile."""
    old = make_client()
    server = make_server(clients=[old])
    fresh = make_client(scene_tree=None)

    with patch("wel.focus_client"):
        wel.client_map(server, fresh, None)

    assert server.clients[0] is fresh
    assert server.clients[1] is old


def test_client_new_commit():
    """The window's surface commit signal drives client_commit so the
    initial configure path runs on first commit."""
    server = make_server()
    with patch("wel.client_commit") as committed:
        wel.client_new(server, "TOPLEVEL_DATA")
        trigger(server, server.lib.welpy_surface_commit, "COMMIT_DATA")
    committed.assert_called_once_with(server, ANY, "COMMIT_DATA")


def test_client_new_map():
    """The window's surface map signal drives client_map so a window gets
    focused the moment it has something to show."""
    server = make_server()
    with patch("wel.client_map") as mapped:
        wel.client_new(server, "TOPLEVEL_DATA")
        trigger(server, server.lib.welpy_surface_map, "MAP_DATA")
    mapped.assert_called_once_with(server, ANY, "MAP_DATA")


def test_client_new_destroy():
    """The window's destroy signal triggers client_cleanup so closing an
    app doesn't leave stale listeners attached to the dying surface."""
    server = make_server()
    with patch("wel.client_cleanup") as cleanup:
        wel.client_new(server, "TOPLEVEL_DATA")
        trigger(server, server.lib.welpy_xdg_toplevel_destroy, "DESTROY_DATA")
    cleanup.assert_called_once_with(server, ANY, "DESTROY_DATA")


def test_client_new_unmap():
    """The window's surface unmap signal drives client_unmap so closing
    one window hands focus to another."""
    server = make_server()
    with patch("wel.client_unmap") as unmap:
        wel.client_new(server, "TOPLEVEL_DATA")
        trigger(server, server.lib.welpy_surface_unmap, "UNMAP_DATA")
    unmap.assert_called_once_with(server, ANY, "UNMAP_DATA")


def test_client_new_request_fullscreen():
    """The window's request_fullscreen signal drives client_request_fullscreen
    so app-initiated fullscreen toggles are honored."""
    server = make_server()
    with patch("wel.client_request_fullscreen") as handler:
        wel.client_new(server, "TOPLEVEL_DATA")
        trigger(
            server, server.lib.welpy_xdg_toplevel_request_fullscreen,
            "REQ_DATA")
    handler.assert_called_once_with(server, ANY, "REQ_DATA")


def test_client_unmap_refocuses():
    """Unmapping a window hands focus to the next-most-recently-focused
    window so closing a terminal leaves the user typing into another one."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    a = make_client(focus_order=2, workspace=m.active_workspace)
    b = make_client(focus_order=1, workspace=m.active_workspace)
    server = make_server(monitors=[m], active_monitor=m, clients=[a, b])

    with patch("wel.apply_geometry"), patch("wel.focus_client") as focus:
        wel.client_unmap(server, a, "DATA")

    focus.assert_called_once_with(server, b)


def test_client_unmap_ends_grab():
    """Unmapping a window in the middle of a drag clears its grab state so
    the user isn't left invisibly dragging a closed window."""
    client = make_client(grab=wel.Grab("move", 10, 20))
    server = make_server(clients=[client])

    wel.client_unmap(server, client, "DATA")

    assert client.grab is None


def test_client_unmap_alone():
    """Unmapping the only window leaves focus alone -- nothing to focus."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    only = make_client(
        focus_order=1,
        workspace=m.active_workspace,
    )
    server = make_server(monitors=[m], active_monitor=m, clients=[only])

    with patch("wel.focus_client") as focus:
        wel.client_unmap(server, only, "DATA")

    focus.assert_not_called()


def test_client_map_subtree():
    """A mapped window's wrapper tree hangs off the tile layer so it's
    actually rendered, with the xdg subtree nested inside it."""
    server = make_server()
    wrapper = MagicMock(name="wrapper")
    server.lib.wlr_scene_tree_create.return_value = wrapper
    client = make_client(toplevel=MagicMock(), scene_tree=None)

    with patch("wel.focus_client"):
        wel.client_map(server, client, None)

    server.lib.wlr_scene_tree_create.assert_called_once_with(
        server.layers[wel.Layer.TILE])
    parent, _ = server.lib.wlr_scene_xdg_surface_create.call_args.args
    assert parent is wrapper
    assert client.scene_tree is wrapper


def test_client_map_orders():
    """Mapping mutates focus_order before apply_geometry runs so the
    new window's order participates in the layout decision."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    server = make_server(monitors=[m], active_monitor=m)
    client = make_client(scene_tree=None)

    calls = []
    with patch(
            "wel.apply_geometry",
            side_effect=lambda *_: calls.append("geometry")), \
         patch("wel.focus_client",
               side_effect=lambda *_a: calls.append("focus")):
        wel.client_map(server, client, None)

    assert calls == ["focus", "geometry"]


def test_client_map_anchors_popups():
    """Popups find their parent's scene tree through the toplevel surface's
    `data` slot, which client_map points at the wrapper tree."""
    server = make_server()
    wrapper = MagicMock(name="wrapper")
    server.lib.wlr_scene_tree_create.return_value = wrapper
    server.ffi.cast.side_effect = lambda type_, val: ("CAST", type_, val)
    client = make_client(toplevel=MagicMock(), scene_tree=None)

    with patch("wel.focus_client"):
        wel.client_map(server, client, None)

    assert client.toplevel.base.surface.data == ("CAST", "void *", wrapper)


def test_client_unmap_clears_popup_anchor():
    """Unmap clears the popup anchor so later popups don't try to attach
    to a destroyed scene tree."""
    client = make_client()
    server = make_server(clients=[client])

    wel.client_unmap(server, client, None)

    assert client.toplevel.base.surface.data is server.ffi.NULL


# --- popups ---------------------------------------------------------------


def _stage_popup(server, *, initial_commit=True, parent_data="PDATA",
                 owner=None):
    """Stage a popup so popup_new resolves `data` to it via ffi.cast."""
    popup = MagicMock(name="popup")
    popup.base.initial_commit = initial_commit
    popup.parent.data = parent_data
    parent_tree = MagicMock(name="parent_tree")
    def cast(type_str, val):
        return {
            "struct wlr_xdg_popup *": popup,
            "struct wlr_scene_tree *": parent_tree,
        }.get(type_str, ("CAST", type_str, val))
    server.ffi.cast.side_effect = cast
    if owner is not None:
        server.lib.wlr_surface_get_root_surface.return_value = (
            owner.toplevel.base.surface)
    return popup, parent_tree


def test_setup_popup_listener():
    """Setup wires the xdg-shell new_popup signal to popup_new so each
    app-created popup hits our handler."""
    ffi = MagicMock(name="ffi")
    lib = MagicMock(name="lib")
    listen = MagicMock(side_effect=lambda *_a: MagicMock())
    build = (ffi, lib, listen, MagicMock(), MagicMock())
    with patch("wel.bindings.build", return_value=build), \
         patch("wel.build_keycode_map",
               return_value=make_keycode_map()), \
         patch("wel.popup_new") as handler:
        built = wel.setup()
        trigger(built, lib.welpy_xdg_shell_new_popup, "POPUP_DATA")
    handler.assert_called_once_with(built, "POPUP_DATA")


def test_popup_new_defers():
    """popup_new only attaches commit + destroy listeners; the scene node
    is deferred until the popup's first commit."""
    server = make_server()
    _stage_popup(server)

    wel.popup_new(server, "DATA")

    server.lib.wlr_scene_xdg_surface_create.assert_not_called()
    server.lib.wlr_xdg_popup_unconstrain_from_box.assert_not_called()


def test_popup_new_initial_commit():
    """On the popup's first commit, popup_new attaches it under the parent's
    scene tree and stores the result on the popup surface's `data` so
    nested popups can chain off it."""
    server = make_server()
    popup, parent_tree = _stage_popup(server)
    scene = MagicMock(name="scene")
    server.lib.wlr_scene_xdg_surface_create.return_value = scene

    wel.popup_new(server, "DATA")
    trigger(server, server.lib.welpy_surface_commit, "COMMIT")

    server.lib.wlr_scene_xdg_surface_create.assert_called_once_with(
        parent_tree, popup.base)
    assert popup.base.surface.data == ("CAST", "void *", scene)


def test_popup_new_non_initial_commit():
    """Subsequent commits don't re-create the scene node."""
    server = make_server()
    _stage_popup(server, initial_commit=False)

    wel.popup_new(server, "DATA")
    trigger(server, server.lib.welpy_surface_commit, "COMMIT")

    server.lib.wlr_scene_xdg_surface_create.assert_not_called()


def test_popup_new_no_parent_data():
    """A popup whose parent surface has no anchor (e.g. layer-shell, which
    we don't manage yet) is dropped instead of attached."""
    server = make_server()
    _stage_popup(server, parent_data=server.ffi.NULL)

    wel.popup_new(server, "DATA")
    trigger(server, server.lib.welpy_surface_commit, "COMMIT")

    server.lib.wlr_scene_xdg_surface_create.assert_not_called()


def test_popup_new_unconstrain():
    """After attaching, the popup is unconstrained to the owner monitor's
    box, translated into the parent client's local coordinates."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    owner = make_client(workspace=m.active_workspace)
    owner.scene_tree.node.x = 100
    owner.scene_tree.node.y = 50
    server = make_server(monitors=[m], clients=[owner])
    popup, _ = _stage_popup(server, owner=owner)
    with patch("wel.monitor_box",
               return_value=wel.Rect(10, 20, 800, 600)):
        wel.popup_new(server, "DATA")
        trigger(server, server.lib.welpy_surface_commit, "COMMIT")

    server.lib.wlr_xdg_popup_unconstrain_from_box.assert_called_once()
    args, _ = server.lib.wlr_xdg_popup_unconstrain_from_box.call_args
    assert args[0] is popup
    server.ffi.new.assert_any_call(
        "struct wlr_box *", [10 - 100, 20 - 50, 800, 600])


def test_popup_new_listeners_cleared():
    """After the first valid commit, both popup listeners detach so the
    handler runs once."""
    server = make_server()
    _stage_popup(server)
    server.lib.wlr_scene_xdg_surface_create.return_value = MagicMock()

    handles = []
    def listen(*_a):
        handles.append(MagicMock())
        return handles[-1]
    server.listen.side_effect = listen

    wel.popup_new(server, "DATA")
    trigger(server, server.lib.welpy_surface_commit, "COMMIT")

    assert not server.listeners
    for h in handles:
        h.remove.assert_called_once()


def test_popup_new_destroy_cleans_up():
    """If the popup is destroyed before its first commit, the destroy
    listener detaches both listeners."""
    server = make_server()
    _stage_popup(server)

    handles = []
    def listen(*_a):
        handles.append(MagicMock())
        return handles[-1]
    server.listen.side_effect = listen

    wel.popup_new(server, "DATA")
    trigger(server, server.lib.welpy_xdg_popup_destroy, "DESTROY")

    assert not server.listeners
    for h in handles:
        h.remove.assert_called_once()


def test_client_cleanup_drops():
    """Cleanup detaches every listener so the dying surface doesn't fire
    callbacks into freed state. Scene tree + list entry were already
    released in unmap."""
    server = make_server()
    h1, h2 = MagicMock(name="h1"), MagicMock(name="h2")
    client = make_client(
        toplevel="TL", scene_tree=None, listeners=[h1, h2])

    wel.client_cleanup(server, client, None)

    h1.remove.assert_called_once()
    h2.remove.assert_called_once()
    assert not client.listeners
    server.lib.wlr_scene_node_destroy.assert_not_called()


# --- decorations ----------------------------------------------------------


def test_setup_decoration_managers():
    """Setup creates both decoration managers and tells the legacy one to
    default to server-side so apps without xdg-decoration also get SSD."""
    ffi = MagicMock(name="ffi")
    lib = MagicMock(name="lib")
    listen = MagicMock(side_effect=lambda *_a: MagicMock())
    build = (ffi, lib, listen, MagicMock(), MagicMock())
    with patch("wel.bindings.build", return_value=build), \
         patch("wel.build_keycode_map",
               return_value=make_keycode_map()):
        wel.setup()

    lib.wlr_server_decoration_manager_set_default_mode.assert_called_once_with(
        lib.wlr_server_decoration_manager_create.return_value,
        lib.WLR_SERVER_DECORATION_MANAGER_MODE_SERVER)
    lib.wlr_xdg_decoration_manager_v1_create.assert_called_once_with(
        lib.wl_display_create.return_value)


def test_setup_decoration_listener():
    """Setup wires the xdg-decoration manager's new-toplevel-decoration
    signal to decoration_new so each app's request hits our handler."""
    ffi = MagicMock(name="ffi")
    lib = MagicMock(name="lib")
    listen = MagicMock(side_effect=lambda *_a: MagicMock())
    build = (ffi, lib, listen, MagicMock(), MagicMock())
    with patch("wel.bindings.build", return_value=build), \
         patch("wel.build_keycode_map",
               return_value=make_keycode_map()), \
         patch("wel.decoration_new") as handler:
        built = wel.setup()
        trigger(built, lib.welpy_xdg_decoration_manager_new, "DECO_DATA")
    handler.assert_called_once_with(built, "DECO_DATA")


def _make_deco(server, client, *, initialized=True):
    """Stage a deco that decoration_new will resolve to `client` via
    ffi.from_handle (mirroring how the real handle round-trips)."""
    client.toplevel.base.initialized = initialized
    deco = MagicMock(name="deco")
    deco.toplevel = client.toplevel
    server.ffi.cast.return_value = deco
    server.ffi.from_handle.return_value = client
    return deco


def test_decoration_new_forces_ssd():
    """A decoration request from an already-initialized window flips it to
    server-side immediately."""
    client = make_client()
    server = make_server(clients=[client])
    deco = _make_deco(server, client)

    wel.decoration_new(server, "DECO_DATA")

    assert client.decoration is deco
    server.lib.wlr_xdg_toplevel_decoration_v1_set_mode.assert_called_once_with(
        deco,
        server.lib.WLR_XDG_TOPLEVEL_DECORATION_V1_MODE_SERVER_SIDE)


def test_decoration_new_before_initialized():
    """A decoration request that arrives before the initial configure does
    not set the mode yet -- doing so would be a protocol error."""
    server = make_server()
    client = make_client(toplevel=MagicMock(), scene_tree=MagicMock())
    deco = _make_deco(server, client, initialized=False)

    wel.decoration_new(server, "DECO_DATA")

    assert client.decoration is deco
    server.lib.wlr_xdg_toplevel_decoration_v1_set_mode.assert_not_called()


def test_decoration_new_no_back_pointer():
    """A decoration whose toplevel was never registered (no back-pointer)
    is silently ignored; nothing to attach state to."""
    server = make_server()
    deco = MagicMock(name="deco")
    deco.toplevel.base.data = server.ffi.NULL
    server.ffi.cast.return_value = deco

    wel.decoration_new(server, "DECO_DATA")

    server.ffi.from_handle.assert_not_called()
    server.lib.wlr_xdg_toplevel_decoration_v1_set_mode.assert_not_called()


def test_decoration_request_mode_reasserts():
    """Re-emitting request_mode after initialization re-forces server-side
    so apps can't flip themselves back to client-side later."""
    client = make_client()
    server = make_server(clients=[client])
    deco = _make_deco(server, client)

    wel.decoration_new(server, "DECO_DATA")
    server.lib.wlr_xdg_toplevel_decoration_v1_set_mode.reset_mock()
    trigger(server, server.lib.welpy_xdg_decoration_request_mode, "REQ_DATA")

    server.lib.wlr_xdg_toplevel_decoration_v1_set_mode.assert_called_once_with(
        deco,
        server.lib.WLR_XDG_TOPLEVEL_DECORATION_V1_MODE_SERVER_SIDE)


def test_decoration_destroy_clears():
    """When the app destroys its decoration object, our per-decoration
    listeners are detached and the client forgets it."""
    server = make_server()
    client = make_client(toplevel=MagicMock(), scene_tree=MagicMock())
    _make_deco(server, client)

    wel.decoration_new(server, "DECO_DATA")
    attached = list(client.listeners)
    trigger(server, server.lib.welpy_xdg_decoration_destroy, "DESTROY_DATA")

    assert client.decoration is None
    assert not client.listeners
    for h in attached:
        h.remove.assert_called_once()


# --- apply_decoration ----------------------------------------------------


def test_apply_decoration_forces():
    """Every initialized window with a decoration is set to server-side."""
    a = make_client(decoration=MagicMock(name="deco_a"))
    a.toplevel.base.initialized = True
    b = make_client(decoration=MagicMock(name="deco_b"))
    b.toplevel.base.initialized = True
    server = make_server(clients=[a, b])

    wel.apply_decoration(server)

    ssd = server.lib.WLR_XDG_TOPLEVEL_DECORATION_V1_MODE_SERVER_SIDE
    set_mode = server.lib.wlr_xdg_toplevel_decoration_v1_set_mode
    assert set_mode.call_count == 2
    set_mode.assert_any_call(a.decoration, ssd)
    set_mode.assert_any_call(b.decoration, ssd)


def test_apply_decoration_skips_uninitialized():
    """A decoration on a not-yet-initialized surface is skipped to avoid
    a protocol error."""
    client = make_client(decoration=MagicMock())
    client.toplevel.base.initialized = False
    server = make_server(clients=[client])

    wel.apply_decoration(server)

    server.lib.wlr_xdg_toplevel_decoration_v1_set_mode.assert_not_called()


def test_apply_decoration_skips_no_decoration():
    """Windows without a decoration object are skipped (most apps)."""
    client = make_client(decoration=None)
    client.toplevel.base.initialized = True
    server = make_server(clients=[client])

    wel.apply_decoration(server)

    server.lib.wlr_xdg_toplevel_decoration_v1_set_mode.assert_not_called()


def test_client_map_reasserts_decoration():
    """client_map runs apply_decoration so the mode is set now that the
    initial configure has been sent."""
    server = make_server(monitors=[MagicMock(name="m", fullscreen=None)])
    client = make_client(scene_tree=None)

    with patch("wel.apply_geometry"), patch("wel.focus_client"), \
         patch("wel.apply_decoration") as ad:
        wel.client_map(server, client, None)

    ad.assert_called_once_with(server)


# --- signal handlers ------------------------------------------------------


def test_install_signals_signums():
    """The compositor installs handlers for exactly SIGINT, SIGTERM,
    SIGCHLD, and SIGPIPE -- the four signals it cares about."""
    server = make_server()

    wel.install_signals(server)

    signums = {c.args[0] for c in server.add_signal.mock_calls}
    assert signums == {
        signal.SIGINT, signal.SIGTERM, signal.SIGCHLD, signal.SIGPIPE,
    }


def test_install_signals_sigterm():
    """SIGTERM (graceful kill) cleanly stops the display loop."""
    server = make_server()
    wel.install_signals(server)
    by_signum = {c.args[0]: c.args[1] for c in server.add_signal.mock_calls}

    by_signum[signal.SIGTERM](signal.SIGTERM)

    server.lib.wl_display_terminate.assert_called_once_with("DISPLAY")


def test_install_signals_sigint():
    """SIGINT (Ctrl-C) cleanly stops the display loop."""
    server = make_server()
    wel.install_signals(server)
    by_signum = {c.args[0]: c.args[1] for c in server.add_signal.mock_calls}

    by_signum[signal.SIGINT](signal.SIGINT)

    server.lib.wl_display_terminate.assert_called_once_with("DISPLAY")


def test_install_signals_drain():
    """On SIGCHLD we keep reaping until no more children are ready, so a
    burst of exits doesn't leave zombies behind."""
    server = make_server()
    wel.install_signals(server)
    by_signum = {c.args[0]: c.args[1] for c in server.add_signal.mock_calls}

    # waitpid yields two children, then "no more ready" (pid == 0).
    with patch("wel.os.waitpid",
               side_effect=[(123, 0), (124, 0), (0, 0)]) as wp:
        by_signum[signal.SIGCHLD](signal.SIGCHLD)

    assert wp.call_count == 3


def test_install_signals_orphan():
    """A spurious SIGCHLD with no children pending must not raise."""
    server = make_server()
    wel.install_signals(server)
    by_signum = {c.args[0]: c.args[1] for c in server.add_signal.mock_calls}

    with patch("wel.os.waitpid", side_effect=ChildProcessError):
        by_signum[signal.SIGCHLD](signal.SIGCHLD)  # must not raise


# --- keyboard input -------------------------------------------------------


def test_keyboard_create_wires_seat():
    """create_keyboard_group hands the seat the combined keyboard, so the
    seat routes key events to one shared keyboard object."""
    server = make_server()
    lib = server.lib

    kg = wel.create_keyboard_group(server)

    lib.wlr_seat_set_keyboard.assert_called_once_with(
        server.seat, lib.welpy_keyboard_group_keyboard.return_value)
    assert kg.group is lib.wlr_keyboard_group_create.return_value
    assert kg.keymap is lib.xkb_keymap_new_from_names.return_value
    assert kg.xkb_context is lib.xkb_context_new.return_value


def test_keyboard_create_key():
    """The combined keyboard's key signal drives keyboard_key so presses
    on any physical keyboard reach the focused app."""
    server = make_server()
    with patch("wel.keyboard_key") as handler:
        wel.create_keyboard_group(server)
        trigger(server, server.lib.welpy_keyboard_key_signal, "KEY_DATA")
    handler.assert_called_once_with(server, "KEY_DATA")


def test_keyboard_create_modifiers():
    """The combined keyboard's modifiers signal drives keyboard_modifiers so
    shift-level changes reach the focused app."""
    server = make_server()
    with patch("wel.keyboard_modifiers") as handler:
        wel.create_keyboard_group(server)
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

    wel.destroy_keyboard_group(lib, kg)

    h1.remove.assert_called_once()
    h2.remove.assert_called_once()
    assert not kg.listeners
    lib.wlr_keyboard_group_destroy.assert_called_once_with("GROUP")
    lib.xkb_keymap_unref.assert_called_once_with("KEYMAP")
    lib.xkb_context_unref.assert_called_once_with("XKB")


def test_input_new_keyboard():
    """A new keyboard joins the combined group so its events feed the seat
    alongside any other already-plugged-in keyboards."""
    server = make_server()
    device = server.ffi.cast.return_value
    device.type = server.lib.WLR_INPUT_DEVICE_KEYBOARD

    wel.input_new(server, "DEVICE_DATA")

    server.lib.wlr_keyboard_group_add_keyboard.assert_called_once_with(
        "GROUP", server.lib.wlr_keyboard_from_input_device.return_value)


def test_input_new_keymap():
    """A new keyboard's keymap is aligned with the group's before it joins.
    wlroots rejects the join otherwise and key events never reach us."""
    server = make_server()
    device = server.ffi.cast.return_value
    device.type = server.lib.WLR_INPUT_DEVICE_KEYBOARD
    keyboard = server.lib.wlr_keyboard_from_input_device.return_value

    wel.input_new(server, "DEVICE_DATA")

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

    wel.input_new(server, "DEVICE_DATA")

    server.lib.wlr_keyboard_group_add_keyboard.assert_not_called()


def test_keyboard_key_unbound():
    """An unbound press is forwarded to the seat, which routes it to
    whichever app currently has keyboard focus."""
    server = make_server()
    event = server.ffi.cast.return_value
    event.time_msec = 42
    event.keycode = 30
    event.state = 1

    wel.keyboard_key(server, "KEY_DATA")

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

    wel.keyboard_key(server, "KEY_DATA")

    action.assert_called_once_with(server)


def test_keyboard_key_consumes():
    """A bound press is not forwarded to the focused app."""
    server = make_server(bindings={(0x40, 28): lambda _: None})
    server.lib.wlr_keyboard_get_modifiers.return_value = 0x40
    event = server.ffi.cast.return_value
    event.state = 1
    event.keycode = 28

    wel.keyboard_key(server, "KEY_DATA")

    server.lib.wlr_seat_keyboard_notify_key.assert_not_called()


def test_keyboard_key_mods():
    """Same keycode under different mods does not match; press forwards."""
    action = MagicMock()
    server = make_server(bindings={(0x40, 28): action})
    server.lib.wlr_keyboard_get_modifiers.return_value = 0x0
    event = server.ffi.cast.return_value
    event.time_msec = 42
    event.state = 1
    event.keycode = 28

    wel.keyboard_key(server, "KEY_DATA")

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

    wel.keyboard_key(server, "KEY_DATA")

    action.assert_not_called()
    server.lib.wlr_seat_keyboard_notify_key.assert_called_once_with(
        server.seat, 42, 28, 0)


def test_keycode_map_range():
    """build_keycode_map walks [min_keycode, max_keycode] inclusive, asking
    xkb for level-0 syms in layout 0."""
    lib = MagicMock()
    ffi = MagicMock()
    lib.xkb_keymap_min_keycode.return_value = 8
    lib.xkb_keymap_max_keycode.return_value = 10
    lib.xkb_keymap_key_get_syms_by_level.return_value = 0

    wel.build_keycode_map(lib, ffi, "KEYMAP")

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

    result = wel.build_keycode_map(lib, ffi, "KEYMAP")

    assert result == {"j": 28}


def test_keycode_map_unbound():
    """Keycodes with no level-0 syms are absent from the map."""
    lib = MagicMock()
    ffi = MagicMock()
    lib.xkb_keymap_min_keycode.return_value = 8
    lib.xkb_keymap_max_keycode.return_value = 8
    lib.xkb_keymap_key_get_syms_by_level.return_value = 0

    result = wel.build_keycode_map(lib, ffi, "KEYMAP")

    assert not result


def test_keyboard_modifiers_forwards():
    """Modifier changes (Shift/Ctrl/...) are forwarded to the seat so the
    focused app interprets subsequent keys in the right shift level."""
    server = make_server()

    wel.keyboard_modifiers(server, None)

    server.lib.wlr_seat_keyboard_notify_modifiers.assert_called_once_with(
        server.seat, server.ffi.addressof.return_value)


# --- mouse cursor --------------------------------------------------------


def test_cursor_create_visible():
    """create_cursor wires the pointer to the screen layout and sets a default
    xcursor image -- the combination is what makes the cursor visible."""
    server = make_server()
    lib = server.lib

    cursor = wel.create_cursor(server)

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
    with patch("wel.cursor_motion") as handler:
        wel.create_cursor(server)
        trigger(server, server.lib.welpy_cursor_motion, "MOTION_DATA")
    handler.assert_called_once_with(server, "MOTION_DATA")


def test_cursor_create_motion_absolute():
    """The cursor's absolute-motion signal drives cursor_motion_absolute so
    touchscreens / nested-backend events still position the pointer."""
    server = make_server()
    with patch("wel.cursor_motion_absolute") as handler:
        wel.create_cursor(server)
        trigger(
            server, server.lib.welpy_cursor_motion_absolute, "MA_DATA")
    handler.assert_called_once_with(server, "MA_DATA")


def test_cursor_create_axis():
    """The cursor's axis signal drives cursor_axis so scroll events reach
    apps."""
    server = make_server()
    with patch("wel.cursor_axis") as handler:
        wel.create_cursor(server)
        trigger(server, server.lib.welpy_cursor_axis, "AXIS_DATA")
    handler.assert_called_once_with(server, "AXIS_DATA")


def test_cursor_create_frame():
    """The cursor's frame signal drives cursor_frame so apps see a batch
    boundary after every grouped pointer update."""
    server = make_server()
    with patch("wel.cursor_frame") as handler:
        wel.create_cursor(server)
        trigger(server, server.lib.welpy_cursor_frame, "FRAME_DATA")
    handler.assert_called_once_with(server, "FRAME_DATA")


def test_cursor_destroy_releases():
    """destroy_cursor detaches its listeners and frees both the wlr cursor
    and its xcursor theme."""
    lib = MagicMock()
    h1, h2 = MagicMock(name="h1"), MagicMock(name="h2")
    cursor = make_cursor(
        cursor="CURSOR", xcursor_manager="XMGR", listeners=[h1, h2])

    wel.destroy_cursor(lib, cursor)

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

    wel.cursor_motion(server, "MOTION_DATA")

    server.lib.wlr_cursor_move.assert_called_once_with(
        cur, server.ffi.addressof.return_value, 3.0, -2.5)


def test_cursor_motion_absolute_warps():
    """cursor_motion_absolute warps the pointer to the absolute coordinates
    delivered by touch / tablet / nested-backend devices."""
    cur = MagicMock(name="cur")
    server = make_server(cursor=make_cursor(cursor=cur, xcursor_manager="XMGR"))
    event = server.ffi.cast.return_value
    event.x = 0.25
    event.y = 0.75

    wel.cursor_motion_absolute(server, "MA_DATA")

    server.lib.wlr_cursor_warp_absolute.assert_called_once_with(
        cur, server.ffi.addressof.return_value, 0.25, 0.75)


def test_cursor_motion_forwards():
    """A move over a surface forwards enter+motion so apps see hovers."""
    server = make_server(cursor=make_cursor(xcursor_manager="X"))
    server.lib.WLR_SCENE_NODE_BUFFER = "BUF"
    node = MagicMock(name="node", type="BUF")
    server.lib.wlr_scene_node_at.return_value = node

    wel.cursor_motion(server, "MOTION_DATA")

    server.lib.wlr_seat_pointer_notify_enter.assert_called_once()
    server.lib.wlr_seat_pointer_notify_motion.assert_called_once()


def test_cursor_motion_empty_clears():
    """A move over empty space clears pointer focus so no app keeps
    thinking it's being hovered."""
    server = make_server(cursor=make_cursor(xcursor_manager="X"))
    server.lib.wlr_scene_node_at.return_value = server.ffi.NULL

    wel.cursor_motion(server, "MOTION_DATA")

    server.lib.wlr_seat_pointer_clear_focus.assert_called_once_with(
        server.seat)
    server.lib.wlr_seat_pointer_notify_enter.assert_not_called()


def test_cursor_motion_grab_skips():
    """While dragging a window the pointer is captured -- motion isn't
    forwarded to surfaces."""
    client = make_client(
        grab=wel.Grab("move", 0, 0),
        floating_geom=wel.Rect(0, 0, 100, 100),
    )
    server = make_server(
        clients=[client], cursor=make_cursor(xcursor_manager="X"))

    wel.cursor_motion(server, "MOTION_DATA")

    server.lib.wlr_seat_pointer_notify_enter.assert_not_called()
    server.lib.wlr_seat_pointer_clear_focus.assert_not_called()


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

    wel.cursor_axis(server, "AXIS_DATA")

    server.lib.wlr_seat_pointer_notify_axis.assert_called_once_with(
        server.seat, 17, "V", 1.0, 1, "WHEEL", "NORMAL")


def test_cursor_frame_forwards():
    """The frame signal tells apps a batch of pointer events is complete."""
    server = make_server()

    wel.cursor_frame(server, "FRAME_DATA")

    server.lib.wlr_seat_pointer_notify_frame.assert_called_once_with(
        server.seat)


# --- drag-to-move --------------------------------------------------------


def test_cursor_create_button():
    """The cursor's button signal drives cursor_button so Alt+Left can start
    a drag-to-move and release can end it."""
    server = make_server()
    with patch("wel.cursor_button") as handler:
        wel.create_cursor(server)
        trigger(server, server.lib.welpy_cursor_button, "BUTTON_DATA")
    handler.assert_called_once_with(server, "BUTTON_DATA")


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

    wel.cursor_button(server, "BUTTON_DATA")

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

    with patch("wel.focus_client") as focus:
        wel.cursor_button(server, "BUTTON_DATA")

    focus.assert_called_once_with(server, client)


def test_cursor_button_active_monitor():
    """Clicking a window on another monitor makes that monitor active so
    keyboard focus follows the click."""
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

    with patch("wel.client_at", return_value=client):
        wel.cursor_button(server, "BUTTON_DATA")

    assert server.active_monitor is m2
    server.lib.wlr_seat_keyboard_notify_enter.assert_called_once()
    assert server.lib.wlr_seat_keyboard_notify_enter.call_args.args[1] is (
        client.toplevel.base.surface)


def test_cursor_button_release_ends():
    """Releasing the mouse button clears the active grab."""
    client = make_client(grab=wel.Grab("move", 0, 0))
    server = make_server(
        clients=[client], cursor=make_cursor(xcursor_manager="X"))
    event = server.ffi.cast.return_value
    event.state = "RELEASED"  # any sentinel != PRESSED

    wel.cursor_button(server, "BUTTON_DATA")

    assert client.grab is None


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

    with patch("wel.client_outer_rect",
               return_value=wel.Rect(100, 150, 200, 200)), \
         patch("wel.apply_geometry"):
        wel.begin_dragging_client(server)

    assert client.grab == wel.Grab("move", 20, 50)


def test_begin_dragging_empty():
    """With no window under the cursor, begin_dragging_client is a no-op."""
    server = make_server(cursor=make_cursor(xcursor_manager="X"))
    server.lib.wlr_scene_node_at.return_value = server.ffi.NULL

    with patch("wel.apply_geometry") as apply_geom:
        wel.begin_dragging_client(server)

    apply_geom.assert_not_called()


def test_cursor_motion_drags():
    """Motion during a grab repositions the grabbed window so it stays pinned
    to the cursor at the captured offset."""
    grabbed = make_client(
        grab=wel.Grab("move", 10, 20),
        floating_geom=wel.Rect(0, 0, 100, 100),
    )
    server = make_server(
        clients=[grabbed], cursor=make_cursor(xcursor_manager="X"))
    server.cursor.cursor.x = 200.0
    server.cursor.cursor.y = 300.0

    wel.cursor_motion(server, "MOTION_DATA")

    server.lib.wlr_scene_node_set_position.assert_called_once_with(
        server.ffi.addressof.return_value, 190, 280)
    assert grabbed.floating_geom == wel.Rect(190, 280, 100, 100)


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

    with patch("wel.client_outer_rect",
               return_value=wel.Rect(100, 150, 300, 200)), \
         patch("wel.apply_geometry"):
        wel.begin_resizing_client(server)

    assert client.grab == wel.Grab("resize", 200, 200)


def test_begin_resizing_empty():
    """With no window under the cursor, begin_resizing_client is a no-op."""
    server = make_server(cursor=make_cursor(xcursor_manager="X"))
    server.lib.wlr_scene_node_at.return_value = server.ffi.NULL

    with patch("wel.apply_geometry") as apply_geom:
        wel.begin_resizing_client(server)

    apply_geom.assert_not_called()


def test_cursor_motion_resizes():
    """Motion during a resize grab moves the bottom-right corner by the
    cursor delta; top-left stays fixed."""
    grabbed = make_client(
        grab=wel.Grab("resize", 200, 200),
        floating_geom=wel.Rect(100, 150, 100, 100),
    )
    grabbed.scene_tree.node.x = 100
    grabbed.scene_tree.node.y = 150
    server = make_server(
        clients=[grabbed], cursor=make_cursor(xcursor_manager="X"))
    server.cursor.cursor.x = 500.0
    server.cursor.cursor.y = 400.0

    with patch("wel.resize_client") as rc:
        wel.cursor_motion(server, "MOTION_DATA")

    rc.assert_called_once_with(server, grabbed, wel.Rect(100, 150, 300, 200))
    assert grabbed.floating_geom == wel.Rect(100, 150, 300, 200)


def test_cursor_motion_resize_min():
    """Resize clamps width/height to at least 1px so the window can't
    collapse to a degenerate zero-size rect."""
    grabbed = make_client(
        grab=wel.Grab("resize", 200, 200),
        floating_geom=wel.Rect(100, 150, 100, 100),
    )
    grabbed.scene_tree.node.x = 100
    grabbed.scene_tree.node.y = 150
    server = make_server(
        clients=[grabbed], cursor=make_cursor(xcursor_manager="X"))
    server.cursor.cursor.x = 50.0
    server.cursor.cursor.y = 50.0

    with patch("wel.resize_client") as rc:
        wel.cursor_motion(server, "MOTION_DATA")

    rc.assert_called_once_with(server, grabbed, wel.Rect(100, 150, 1, 1))


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

    wel.cursor_button(server, "BUTTON_DATA")

    server.lib.wlr_seat_pointer_notify_button.assert_called_once_with(
        server.seat, 42, "BTN", event.state)


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

    wel.cursor_button(server, "BUTTON_DATA")

    server.lib.wlr_seat_pointer_notify_button.assert_not_called()


def test_cursor_button_release_consumed():
    """Releasing to end a drag isn't forwarded; the app never saw the
    press, so it shouldn't see the release."""
    client = make_client(grab=wel.Grab("move", 0, 0))
    server = make_server(
        clients=[client], cursor=make_cursor(xcursor_manager="X"))
    event = server.ffi.cast.return_value
    event.state = "RELEASED"

    wel.cursor_button(server, "BUTTON_DATA")

    server.lib.wlr_seat_pointer_notify_button.assert_not_called()


def test_grabbed_client_multiple():
    """Only one window should be grabbed at a time; if two are, log a warning
    so the inconsistency doesn't go silent."""
    a = make_client(grab=wel.Grab("move", 0, 0))
    b = make_client(grab=wel.Grab("move", 0, 0))
    server = make_server(clients=[a, b])

    with patch("wel.logger") as log:
        wel.grabbed_client(server)

    log.warning.assert_called_once()


def test_input_new_pointer():
    """A new mouse / touchpad is attached to the cursor so its motion events
    actually move the on-screen pointer."""
    server = make_server(
        cursor=make_cursor(cursor="CURSOR", xcursor_manager="XMGR"))
    device = server.ffi.cast.return_value
    device.type = server.lib.WLR_INPUT_DEVICE_POINTER

    wel.input_new(server, "DEVICE_DATA")

    server.lib.wlr_cursor_attach_input_device.assert_called_once_with(
        "CURSOR", device)


def test_client_map_focuses():
    """First time a window has something to show, we focus it so it can
    start receiving keys immediately."""
    server = make_server()
    client = make_client(toplevel=MagicMock(), scene_tree=MagicMock())

    with patch("wel.focus_client") as focus:
        wel.client_map(server, client, None)

    focus.assert_called_once_with(server, client)


def test_client_map_tiled_once():
    """Mapping marks every window tiled on all edges -- set once and not
    touched again by arrange or set_floating."""
    server = make_server()
    server.lib.WLR_EDGE_TOP = 1
    server.lib.WLR_EDGE_BOTTOM = 2
    server.lib.WLR_EDGE_LEFT = 4
    server.lib.WLR_EDGE_RIGHT = 8
    client = make_client(toplevel=MagicMock(), scene_tree=MagicMock())

    wel.client_map(server, client, None)

    server.lib.wlr_xdg_toplevel_set_tiled.assert_called_once_with(
        client.toplevel, 15)


def test_focus_client_order():
    """Each focus bumps the client's focus_order above every other client's,
    so the most-recently-focused window always has the highest value."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    a = make_client(workspace=m.active_workspace)
    b = make_client(workspace=m.active_workspace)
    server = make_server(monitors=[m], active_monitor=m, clients=[a, b])

    wel.focus_client(server, a)
    wel.focus_client(server, b)
    wel.focus_client(server, a)

    assert a.focus_order > b.focus_order > 0


# --- apply_tree ----------------------------------------------------------


def test_apply_tree_clients():
    """Each client's scene node is reparented to its layer's tree."""
    a = make_client()
    b = make_client(floating_geom=wel.Rect(0, 0, 100, 100))
    server = make_server(clients=[a, b])

    wel.apply_tree(server)

    node = server.ffi.addressof.return_value
    server.lib.wlr_scene_node_reparent.assert_any_call(
        node, server.layers[wel.Layer.TILE])
    server.lib.wlr_scene_node_reparent.assert_any_call(
        node, server.layers[wel.Layer.FLOAT])


def test_apply_tree_skips_unmapped():
    """A client without a scene_tree is between create and map -- skipped."""
    client = make_client(scene_tree=None)
    server = make_server(clients=[client])

    wel.apply_tree(server)

    server.lib.wlr_scene_node_reparent.assert_not_called()


def test_apply_tree_idempotent():
    """When every node is already under the right parent, nothing is
    reparented."""
    client = make_client()
    server = make_server(clients=[client])
    server.ffi.addressof.return_value.parent = server.layers[wel.Layer.TILE]

    wel.apply_tree(server)

    server.lib.wlr_scene_node_reparent.assert_not_called()


def test_apply_tree_layer_surface():
    """A layer surface in monitor.layers[TOP] is parented under the TOP
    tree; its popups tree follows."""
    monitor = MagicMock(name="m", fullscreen=None)
    monitor.layers = {layer: [] for layer in wel.Layer}
    ls = MagicMock(name="ls")
    monitor.layers[wel.Layer.TOP].append(ls)
    server = make_server(monitors=[monitor])

    wel.apply_tree(server)

    node = server.ffi.addressof.return_value
    server.lib.wlr_scene_node_reparent.assert_any_call(
        node, server.layers[wel.Layer.TOP])


def test_apply_tree_popups_lifted():
    """A layer surface in BACKGROUND has its popups tree lifted into TOP
    so a bar can't bury them."""
    monitor = MagicMock(name="m", fullscreen=None)
    monitor.layers = {layer: [] for layer in wel.Layer}
    ls = MagicMock(name="ls")
    monitor.layers[wel.Layer.BACKGROUND].append(ls)
    server = make_server(monitors=[monitor])

    wel.apply_tree(server)

    node = server.ffi.addressof.return_value
    server.lib.wlr_scene_node_reparent.assert_any_call(
        node, server.layers[wel.Layer.BACKGROUND])
    server.lib.wlr_scene_node_reparent.assert_any_call(
        node, server.layers[wel.Layer.TOP])


# --- apply_focus ---------------------------------------------------------


def test_apply_focus_idle():
    """No monitors, no clients, no focused surface -> nothing to do."""
    server = make_server()

    wel.apply_focus(server)

    server.lib.wlr_seat_keyboard_notify_enter.assert_not_called()
    server.lib.wlr_seat_keyboard_clear_focus.assert_not_called()
    server.lib.wlr_xdg_toplevel_set_activated.assert_not_called()


def test_apply_focus_client():
    """With one client on the active monitor and nothing focused yet,
    activate it, raise it, and hand it the keyboard."""
    monitor = make_monitor()
    monitor.active_workspace = make_workspace(monitor=monitor)
    client = make_client(focus_order=1, workspace=monitor.active_workspace)
    server = make_server(
        monitors=[monitor], active_monitor=monitor, clients=[client])

    wel.apply_focus(server)

    server.lib.wlr_xdg_toplevel_set_activated.assert_called_once_with(
        client.toplevel, True)
    server.lib.wlr_scene_node_raise_to_top.assert_called_once()
    server.lib.wlr_seat_keyboard_notify_enter.assert_called_once()
    enter_args = server.lib.wlr_seat_keyboard_notify_enter.call_args.args
    assert enter_args[1] is client.toplevel.base.surface


def test_apply_focus_shell():
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
    monitor.layers[wel.Layer.OVERLAY].append(ls)

    wel.apply_focus(server)

    assert ls.focused
    server.lib.wlr_seat_keyboard_notify_enter.assert_called_once()
    enter_args = server.lib.wlr_seat_keyboard_notify_enter.call_args.args
    assert enter_args[1] is ls.layer_surface.surface
    server.lib.wlr_xdg_toplevel_set_activated.assert_not_called()


def test_apply_focus_releases():
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
    monitor.layers[wel.Layer.OVERLAY].append(ls)

    wel.apply_focus(server)

    server.lib.wlr_xdg_toplevel_set_activated.assert_called_once_with(
        client.toplevel, False)
    assert ls.focused
    enter_args = server.lib.wlr_seat_keyboard_notify_enter.call_args.args
    assert enter_args[1] is ls.layer_surface.surface


def test_apply_focus_clears():
    """A surface was focused, all candidates went away -> clear focus."""
    server = make_server()
    server.seat.keyboard_state.focused_surface = MagicMock(name="stale") # pylint: disable=no-member

    wel.apply_focus(server)

    server.lib.wlr_seat_keyboard_clear_focus.assert_called_once_with(
        server.seat)
    server.lib.wlr_seat_keyboard_notify_enter.assert_not_called()


def test_apply_focus_handoff():
    """Focus shifting from one client to another deactivates the previous
    one before activating the new one."""
    monitor = make_monitor()
    monitor.active_workspace = make_workspace(monitor=monitor)
    a = make_client(focus_order=1, workspace=monitor.active_workspace)
    b = make_client(focus_order=2, workspace=monitor.active_workspace)
    server = make_server(
        monitors=[monitor], active_monitor=monitor, clients=[a, b])
    server.seat.keyboard_state.focused_surface = a.toplevel.base.surface # pylint: disable=no-member

    wel.apply_focus(server)

    server.lib.wlr_xdg_toplevel_set_activated.assert_any_call(
        a.toplevel, False)
    server.lib.wlr_xdg_toplevel_set_activated.assert_any_call(
        b.toplevel, True)
    enter_args = server.lib.wlr_seat_keyboard_notify_enter.call_args.args
    assert enter_args[1] is b.toplevel.base.surface


def test_apply_focus_idempotent():
    """Re-running with the same desired state as wlroots already has emits
    no effects."""
    monitor = make_monitor()
    monitor.active_workspace = make_workspace(monitor=monitor)
    client = make_client(focus_order=1, workspace=monitor.active_workspace)
    server = make_server(
        monitors=[monitor], active_monitor=monitor, clients=[client])
    server.seat.keyboard_state.focused_surface = client.toplevel.base.surface # pylint: disable=no-member

    wel.apply_focus(server)

    server.lib.wlr_seat_keyboard_notify_enter.assert_not_called()
    server.lib.wlr_seat_keyboard_clear_focus.assert_not_called()
    server.lib.wlr_xdg_toplevel_set_activated.assert_not_called()
    server.lib.wlr_scene_node_raise_to_top.assert_not_called()


def test_apply_focus_tracks():
    """Activated-state configures emitted by apply_focus feed pending_serial
    so the screen hold waits for the focused window to ack."""
    monitor = make_monitor()
    monitor.active_workspace = make_workspace(monitor=monitor)
    a = make_client(focus_order=1, workspace=monitor.active_workspace)
    b = make_client(focus_order=2, workspace=monitor.active_workspace)
    a.toplevel.base.current.configure_serial = 5
    b.toplevel.base.current.configure_serial = 0
    server = make_server(
        monitors=[monitor], active_monitor=monitor, clients=[a, b])
    server.seat.keyboard_state.focused_surface = a.toplevel.base.surface # pylint: disable=no-member
    # deactivate(a)=3 already acked; activate(b)=7 still pending.
    server.lib.wlr_xdg_toplevel_set_activated.side_effect = [3, 7]

    wel.apply_focus(server)

    assert a.pending_serial is None
    assert b.pending_serial == 7


def test_apply_focus_borders():
    """apply_focus paints the new window's borders active and the previously
    focused window's borders inactive."""
    monitor = make_monitor()
    monitor.active_workspace = make_workspace(monitor=monitor)
    a = make_client(focus_order=1, workspace=monitor.active_workspace)
    b = make_client(focus_order=2, workspace=monitor.active_workspace)
    server = make_server(
        monitors=[monitor], active_monitor=monitor, clients=[a, b])
    server.seat.keyboard_state.focused_surface = a.toplevel.base.surface # pylint: disable=no-member

    wel.apply_focus(server)

    color_args = [c.args for c in server.ffi.new.call_args_list]
    assert ("float[4]", wel.BORDER_COLOR_ACTIVE) in color_args
    assert ("float[4]", wel.BORDER_COLOR_INACTIVE) in color_args


def test_apply_focus_sticky():
    """A currently-focused shell surface keeps the keyboard when another
    qualifying surface appears, so arranging an unrelated screen doesn't
    steal focus from the launcher."""
    m1, m2 = make_monitor(), make_monitor()
    server = make_server(monitors=[m1, m2])
    focused = make_layer_surface(monitor=m1, focused=True)
    focused.layer_surface.surface.mapped = True
    focused.layer_surface.current.keyboard_interactive = 1
    m1.layers[wel.Layer.OVERLAY].append(focused)
    contender = make_layer_surface(monitor=m2)
    contender.layer_surface.surface.mapped = True
    contender.layer_surface.current.keyboard_interactive = 1
    m2.layers[wel.Layer.OVERLAY].append(contender)

    wel.apply_focus(server)

    assert focused.focused
    assert not contender.focused


def test_apply_focus_priority():
    """With both TOP and OVERLAY surfaces wanting the keyboard, OVERLAY
    wins."""
    monitor = make_monitor()
    server = make_server(monitors=[monitor])
    top = make_layer_surface(monitor=monitor)
    top.layer_surface.surface.mapped = True
    top.layer_surface.current.keyboard_interactive = 1
    monitor.layers[wel.Layer.TOP].append(top)
    overlay = make_layer_surface(monitor=monitor)
    overlay.layer_surface.surface.mapped = True
    overlay.layer_surface.current.keyboard_interactive = 1
    monitor.layers[wel.Layer.OVERLAY].append(overlay)

    wel.apply_focus(server)

    assert overlay.focused
    assert not top.focused
    enter_args = server.lib.wlr_seat_keyboard_notify_enter.call_args.args
    assert enter_args[1] is overlay.layer_surface.surface


# --- layers / tiling -----------------------------------------------------


def test_setup_layers_created():
    """Setup creates a scene tree per Layer in declaration order so each
    renders above the previous."""
    ffi = MagicMock(name="ffi")
    lib = MagicMock(name="lib")
    lib.WL_SEAT_CAPABILITY_POINTER = 1
    lib.WL_SEAT_CAPABILITY_KEYBOARD = 2
    trees = [MagicMock(name=f"tree_{i}") for i in range(len(wel.Layer))]
    lib.wlr_scene_tree_create.side_effect = list(trees)
    listen = MagicMock(side_effect=lambda *_a: MagicMock())
    build = (ffi, lib, listen, MagicMock(), MagicMock())
    with patch("wel.bindings.build", return_value=build), \
         patch("wel.build_keycode_map",
               return_value=make_keycode_map()):
        server = wel.setup()

    scene_root = ffi.addressof.return_value
    assert lib.wlr_scene_tree_create.call_args_list == [
        call(scene_root) for _ in wel.Layer
    ]
    assert server.layers == dict(zip(wel.Layer, trees))


def test_client_map_to_tile():
    """Mapped windows attach under the TILE layer so they participate in
    tiling and render below floating windows."""
    server = make_server()
    client = make_client(toplevel=MagicMock(), scene_tree=None)

    with patch("wel.focus_client"):
        wel.client_map(server, client, None)

    server.lib.wlr_scene_tree_create.assert_called_once_with(
        server.layers[wel.Layer.TILE])


def test_client_map_monitor_selected():
    """A newly mapped window joins the active workspace of the active
    monitor."""
    m1 = make_monitor()
    m1.active_workspace = make_workspace(monitor=m1)
    m2 = make_monitor()
    m2.active_workspace = make_workspace(monitor=m2)
    server = make_server(monitors=[m1, m2], active_monitor=m1)
    client = make_client(scene_tree=None)

    with patch("wel.focus_client"), patch("wel.apply_geometry"):
        wel.client_map(server, client, None)

    assert client.workspace is m1.active_workspace


def test_client_map_monitor_none():
    """A newly mapped window with no active monitor is parked as orphaned."""
    server = make_server()
    client = make_client(scene_tree=None)

    with patch("wel.focus_client"):
        wel.client_map(server, client, None)

    assert client.workspace is None


def test_client_map_floats_dialog():
    """A window opened as a child of another window (a dialog) lands in the
    FLOAT layer instead of joining the tiling layout."""
    m = make_monitor(window_area=wel.Rect(0, 0, 800, 600))
    m.active_workspace = make_workspace(monitor=m)
    server = make_server(monitors=[m], active_monitor=m)
    toplevel = MagicMock()
    toplevel.base.geometry.width = 400
    toplevel.base.geometry.height = 300
    client = make_client(toplevel=toplevel, scene_tree=None)
    toplevel.parent = MagicMock(name="parent_toplevel")

    with patch("wel.focus_client"), patch("wel.apply_geometry"):
        wel.client_map(server, client, None)

    assert wel.client_layer(client) == wel.Layer.FLOAT


def test_client_map_no_parent():
    """A regular (unparented) window still joins the tiling layout."""
    m = make_monitor(window_area=wel.Rect(0, 0, 800, 600))
    m.active_workspace = make_workspace(monitor=m)
    server = make_server(monitors=[m], active_monitor=m)
    client = make_client(scene_tree=None)

    with patch("wel.focus_client"), patch("wel.apply_geometry"):
        wel.client_map(server, client, None)

    assert wel.client_layer(client) == wel.Layer.TILE


def test_top_client_per_monitor():
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

    assert wel.top_client(server, m1) is b
    assert wel.top_client(server, m2) is d


def test_top_client_empty():
    """top_client returns None when no clients are visible on the monitor."""
    server = make_server()
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)

    assert wel.top_client(server, m) is None


def test_cycle_focus_next():
    """cycle_focus(+1) moves focus to the next visible window in layout
    order."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    a = make_client(workspace=m.active_workspace)
    b = make_client(workspace=m.active_workspace)
    c = make_client(workspace=m.active_workspace)
    server = make_server(monitors=[m], active_monitor=m, clients=[a, b, c])
    wel.focus_client(server, a)

    with patch("wel.focus_client") as focus:
        wel.cycle_focus(server, +1)

    focus.assert_called_once_with(server, b)


def test_cycle_focus_prev_wraps():
    """cycle_focus(-1) wraps around past the first window."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    a = make_client(workspace=m.active_workspace)
    b = make_client(workspace=m.active_workspace)
    c = make_client(workspace=m.active_workspace)
    server = make_server(monitors=[m], active_monitor=m, clients=[a, b, c])
    wel.focus_client(server, a)

    with patch("wel.focus_client") as focus:
        wel.cycle_focus(server, -1)

    focus.assert_called_once_with(server, c)


def test_cycle_focus_empty():
    """cycle_focus is a no-op when no windows are visible on the active
    monitor."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    server = make_server(monitors=[m], active_monitor=m)

    with patch("wel.focus_client") as focus:
        wel.cycle_focus(server, +1)

    focus.assert_not_called()


def test_client_unmap_unselected():
    """Unmapping a window not on the active monitor leaves focus alone
    when there's nothing to refocus on the active monitor."""
    m1 = make_monitor()
    m1.active_workspace = make_workspace(monitor=m1)
    m2 = make_monitor()
    m2.active_workspace = make_workspace(monitor=m2)
    a = make_client(focus_order=1, workspace=m2.active_workspace)
    server = make_server(monitors=[m1], active_monitor=m1, clients=[a])

    with patch("wel.focus_client") as focus:
        wel.client_unmap(server, a, "DATA")

    focus.assert_not_called()


def test_monitor_box_returns_rect():
    """monitor_box reads the layout box and returns it as a Rect."""
    server = make_server()
    monitor = make_monitor(output="OUT", scene_output="SO")
    box = server.ffi.new.return_value
    box.x, box.y, box.width, box.height = 10, 20, 800, 600

    result = wel.monitor_box(server, monitor)

    assert result == wel.Rect(10, 20, 800, 600)
    server.lib.wlr_output_layout_get_box.assert_called_once_with(
        server.output_layout, "OUT", box)


def test_resize_client_geometry():
    """resize_client positions the wrapper subtree and configures the inner
    surface size shrunk by twice the border width, without touching
    tiled-edge state. The xdg subtree is also inset by the border so the
    surface doesn't render on top of the border rects."""
    server = make_server()
    borders = tuple(MagicMock(name=f"b{i}") for i in range(4))
    client = make_client(
        toplevel=MagicMock(), scene_tree=MagicMock(), borders=borders)

    wel.resize_client(server, client, wel.Rect(10, 20, 300, 400))

    server.lib.wlr_scene_node_set_position.assert_any_call(
        server.ffi.addressof.return_value, 10, 20)
    bw = wel.BORDER_WIDTH
    server.lib.wlr_scene_node_set_position.assert_any_call(
        server.ffi.addressof.return_value, bw, bw)
    server.lib.wlr_xdg_toplevel_set_size.assert_called_once_with(
        client.toplevel, 300 - 2 * bw, 400 - 2 * bw)
    server.lib.wlr_xdg_toplevel_set_tiled.assert_not_called()


def test_resize_client_tracks():
    """resize_client records the configure serial so the screen waits for
    the client to render at the new size."""
    server = make_server()
    server.lib.wlr_xdg_toplevel_set_size.return_value = 42
    borders = tuple(MagicMock() for _ in range(4))
    client = make_client(scene_tree=MagicMock(), borders=borders)

    wel.resize_client(server, client, wel.Rect(10, 20, 300, 400))

    assert client.pending_serial == 42


def test_borders_present():
    """A new window gets four edge rects under its wrapper tree so it has
    something to color on focus."""
    server = make_server()
    wrapper = MagicMock(name="wrapper")
    server.lib.wlr_scene_tree_create.return_value = wrapper
    client = make_client(toplevel=MagicMock(), scene_tree=None)

    with patch("wel.focus_client"):
        wel.client_map(server, client, None)

    assert len(client.borders) == 4
    parents = [
        c.args[0] for c in server.lib.wlr_scene_rect_create.call_args_list
    ]
    assert parents == [wrapper] * 4


def test_borders_resize():
    """resize_client frames the wrapper with edge rects whose sizes match
    the outer rect on the long axis and the border width on the short one."""
    server = make_server()
    top, bottom, left, right = (
        MagicMock(name="top"), MagicMock(name="bottom"),
        MagicMock(name="left"), MagicMock(name="right"))
    client = make_client(
        toplevel=MagicMock(), scene_tree=MagicMock(),
        borders=(top, bottom, left, right))

    wel.resize_client(server, client, wel.Rect(10, 20, 300, 400))

    bw = wel.BORDER_WIDTH
    sizes = server.lib.wlr_scene_rect_set_size.call_args_list
    assert sizes == [
        call(top, 300, bw),
        call(bottom, 300, bw),
        call(left, bw, 400 - 2 * bw),
        call(right, bw, 400 - 2 * bw),
    ]


def test_resize_client_clips():
    """resize_client clips the xdg subtree to the inner area, anchored at
    the surface's xdg geometry offset so CSD shadow margins are skipped."""
    server = make_server()
    toplevel = MagicMock()
    toplevel.base.geometry.x = 12
    toplevel.base.geometry.y = 34
    client = make_client(
        toplevel=toplevel, scene_tree=MagicMock(),
        borders=tuple(MagicMock() for _ in range(4)))

    wel.resize_client(server, client, wel.Rect(10, 20, 300, 400))

    bw = wel.BORDER_WIDTH
    server.ffi.new.assert_any_call(
        "struct wlr_box *", [12, 34, 300 - 2 * bw, 400 - 2 * bw])
    server.lib.wlr_scene_subsurface_tree_set_clip.assert_called_once_with(
        server.ffi.addressof.return_value, server.ffi.new.return_value)




# --- apply_geometry ------------------------------------------------------


def test_apply_geometry_single_full():
    """One tile client fills the whole window area."""
    m = make_monitor(window_area=wel.Rect(0, 0, 800, 600))
    m.active_workspace = make_workspace(monitor=m)
    a = make_client(workspace=m.active_workspace)
    server = make_server(clients=[a])

    with patch("wel.resize_client") as resize:
        wel.apply_geometry(server, m)

    resize.assert_called_once_with(server, a, wel.Rect(0, 0, 800, 600))


def test_apply_geometry_master_stack():
    """Three tile clients: master on the left half, two stacked on the right
    half with heights summing exactly to the window area's height."""
    m = make_monitor(window_area=wel.Rect(0, 0, 800, 600))
    m.active_workspace = make_workspace(monitor=m)
    a = make_client(workspace=m.active_workspace)
    b = make_client(workspace=m.active_workspace)
    c = make_client(workspace=m.active_workspace)
    server = make_server(clients=[a, b, c])

    with patch("wel.resize_client") as resize:
        wel.apply_geometry(server, m)

    assert resize.call_args_list == [
        call(server, a, wel.Rect(0, 0, 400, 600)),
        call(server, b, wel.Rect(400, 0, 400, 300)),
        call(server, c, wel.Rect(400, 300, 400, 300)),
    ]


def test_apply_geometry_other_monitor():
    """apply_geometry only touches clients visible on its monitor."""
    m1 = make_monitor(window_area=wel.Rect(0, 0, 800, 600))
    m1.active_workspace = make_workspace(monitor=m1)
    m2 = make_monitor(window_area=wel.Rect(0, 0, 800, 600))
    m2.active_workspace = make_workspace(monitor=m2)
    a = make_client(workspace=m1.active_workspace)
    b = make_client(workspace=m2.active_workspace)
    server = make_server(clients=[a, b])

    with patch("wel.resize_client") as resize:
        wel.apply_geometry(server, m1)

    resize.assert_called_once_with(server, a, wel.Rect(0, 0, 800, 600))


def test_apply_geometry_skips_floating():
    """Floating clients don't participate in tiling; apply_geometry
    leaves the tile path to tiles only."""
    m = make_monitor(window_area=wel.Rect(0, 0, 800, 600))
    m.active_workspace = make_workspace(monitor=m)
    a = make_client(
        workspace=m.active_workspace,
        floating_geom=wel.Rect(50, 60, 100, 80),
    )
    b = make_client(workspace=m.active_workspace)
    server = make_server(clients=[a, b])

    with patch("wel.resize_client") as resize:
        wel.apply_geometry(server, m)

    # The tile gets the full window area; the float gets its own rect.
    assert resize.call_args_list == [
        call(server, b, wel.Rect(0, 0, 800, 600)),
        call(server, a, wel.Rect(50, 60, 100, 80)),
    ]


def test_apply_geometry_sizes_fullscreen():
    """apply_geometry keeps any fullscreen window matched to the monitor's
    current box so monitor mode changes propagate. Tiles tile alongside."""
    m = make_monitor(window_area=wel.Rect(0, 0, 800, 600))
    m.active_workspace = make_workspace(monitor=m)
    fs = make_client(workspace=m.active_workspace)
    m.active_workspace.fullscreen = fs
    tile = make_client(workspace=m.active_workspace)
    server = make_server(clients=[fs, tile])

    with patch("wel.monitor_box", return_value=wel.Rect(0, 0, 800, 600)), \
         patch("wel.resize_client") as resize:
        wel.apply_geometry(server, m)

    # Fullscreen gets the full box; the sole tile takes the full box too.
    resize.assert_any_call(server, fs, wel.Rect(0, 0, 800, 600))
    resize.assert_any_call(server, tile, wel.Rect(0, 0, 800, 600))


def test_apply_geometry_empty():
    """With no tile/fullscreen clients on the monitor, apply_geometry
    does nothing."""
    server = make_server()
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)

    with patch("wel.resize_client") as resize:
        wel.apply_geometry(server, m)

    resize.assert_not_called()


def test_apply_geometry_reconciles_float():
    """A floating client is resized to its floating_geom on every
    apply_geometry, so any drift between wlroots state and dataclass
    state converges."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    saved = wel.Rect(10, 20, 300, 200)
    c = make_client(workspace=m.active_workspace, floating_geom=saved)
    server = make_server(clients=[c])

    with patch("wel.resize_client") as resize:
        wel.apply_geometry(server, m)

    resize.assert_called_once_with(server, c, saved)
    assert c.floating_geom == saved


def test_update_monitors_arranges_all():
    """update_monitors arranges every connected monitor."""
    m1 = MagicMock(name="m1", fullscreen=None)
    m2 = MagicMock(name="m2", fullscreen=None)
    server = make_server(monitors=[m1, m2])

    with patch("wel.apply_geometry") as apply_geom:
        wel.update_monitors(server)

    assert apply_geom.call_args_list == [call(server, m1), call(server, m2)]


def test_update_monitors_no_monitors():
    """With no monitors connected, apply_geometry isn't called."""
    server = make_server()

    with patch("wel.apply_geometry") as apply_geom:
        wel.update_monitors(server)

    apply_geom.assert_not_called()


def test_client_layer_tile():
    """A client with no floating_geom and not pinned to any monitor's
    fullscreen slot is tiled."""
    client = make_client(toplevel=MagicMock(), scene_tree=MagicMock())
    assert wel.client_layer(client) == wel.Layer.TILE


def test_client_layer_float():
    """A client with a floating_geom is floating."""
    client = make_client(
        toplevel=MagicMock(),
        scene_tree=MagicMock(),
        floating_geom=wel.Rect(0, 0, 100, 100),
    )
    assert wel.client_layer(client) == wel.Layer.FLOAT


def test_client_layer_fullscreen():
    """A client occupying its workspace's fullscreen slot is fullscreen,
    even if it also has a floating_geom stashed for restore."""
    client = make_client(
        toplevel=MagicMock(),
        scene_tree=MagicMock(),
        floating_geom=wel.Rect(0, 0, 100, 100),
    )
    client.workspace = make_workspace(fullscreen=client)
    assert wel.client_layer(client) == wel.Layer.FULLSCREEN


def test_init_floating_geom_centers():
    """init_floating_geom centers the window in its screen's usable area at
    the size the app asked for plus border."""
    m = make_monitor(window_area=wel.Rect(100, 50, 800, 600))
    m.active_workspace = make_workspace(monitor=m)
    toplevel = MagicMock()
    toplevel.base.geometry.width = 400
    toplevel.base.geometry.height = 300
    client = make_client(toplevel=toplevel, workspace=m.active_workspace)

    outer_w = 400 + 2 * wel.BORDER_WIDTH
    outer_h = 300 + 2 * wel.BORDER_WIDTH
    assert wel.init_floating_geom(client) == wel.Rect(
        100 + (800 - outer_w) // 2,
        50 + (600 - outer_h) // 2,
        outer_w, outer_h)


def test_init_floating_geom_fallback():
    """When the app commits with empty geometry, init_floating_geom picks
    a default size so the window isn't invisibly small."""
    m = make_monitor(window_area=wel.Rect(0, 0, 800, 600))
    m.active_workspace = make_workspace(monitor=m)
    toplevel = MagicMock()
    toplevel.base.geometry.width = 0
    toplevel.base.geometry.height = 0
    client = make_client(toplevel=toplevel, workspace=m.active_workspace)

    rect = wel.init_floating_geom(client)

    assert rect.width == 250 + 2 * wel.BORDER_WIDTH
    assert rect.height == 200 + 2 * wel.BORDER_WIDTH


def test_fullscreen_slot_enters():
    """Assigning a client to its workspace's fullscreen slot notifies the
    app so its xdg state matches."""
    server = make_server()
    workspace = make_workspace()
    client = make_client(
        toplevel=MagicMock(),
        scene_tree=MagicMock(),
        workspace=workspace,
    )

    wel.set_fullscreen(server, workspace, client)

    assert workspace.fullscreen is client
    server.lib.wlr_xdg_toplevel_set_fullscreen.assert_called_once_with(
        client.toplevel, True)


def test_fullscreen_slot_exits():
    """Clearing the slot notifies the previously-fullscreen app."""
    server = make_server()
    client = make_client(toplevel=MagicMock(), scene_tree=MagicMock())
    workspace = make_workspace(fullscreen=client)
    client.workspace = workspace

    wel.set_fullscreen(server, workspace, None)

    assert workspace.fullscreen is None
    server.lib.wlr_xdg_toplevel_set_fullscreen.assert_called_once_with(
        client.toplevel, False)


def test_fullscreen_slot_noop():
    """Setting the slot to its current value is a no-op so no spurious
    configure goes out."""
    server = make_server()
    client = make_client(toplevel=MagicMock(), scene_tree=MagicMock())
    workspace = make_workspace(fullscreen=client)
    client.workspace = workspace

    wel.set_fullscreen(server, workspace, client)

    server.lib.wlr_xdg_toplevel_set_fullscreen.assert_not_called()


def test_fullscreen_slot_replaces():
    """Replacing one fullscreen client with another notifies both: the
    outgoing one exits, the incoming one enters."""
    server = make_server()
    outgoing = make_client(
        toplevel=MagicMock(name="out"),
        scene_tree=MagicMock(),
    )
    incoming = make_client(
        toplevel=MagicMock(name="in"),
        scene_tree=MagicMock(),
    )
    workspace = make_workspace(fullscreen=outgoing)
    outgoing.workspace = workspace
    incoming.workspace = workspace

    wel.set_fullscreen(server, workspace, incoming)

    assert workspace.fullscreen is incoming
    assert server.lib.wlr_xdg_toplevel_set_fullscreen.call_args_list == [
        call(outgoing.toplevel, False),
        call(incoming.toplevel, True),
    ]


def test_fullscreen_slot_keeps_float():
    """Entering and exiting fullscreen leaves floating_geom untouched, so a
    window that was floating before going fullscreen returns to floating
    at the same rect; a window that was tiled stays tiled."""
    server = make_server()
    workspace = make_workspace()
    saved = wel.Rect(50, 60, 304, 204)
    floater = make_client(
        toplevel=MagicMock(),
        scene_tree=MagicMock(),
        workspace=workspace,
        floating_geom=saved,
    )
    tiler = make_client(
        toplevel=MagicMock(),
        scene_tree=MagicMock(),
        workspace=workspace,
    )

    wel.set_fullscreen(server, workspace, floater)
    wel.set_fullscreen(server, workspace, None)
    assert floater.floating_geom == saved

    wel.set_fullscreen(server, workspace, tiler)
    wel.set_fullscreen(server, workspace, None)
    assert tiler.floating_geom is None


def test_toggle_fullscreen_enters():
    """toggle_fullscreen on a tiled focused window pins it to the
    workspace's fullscreen slot."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    client = make_client(workspace=m.active_workspace)
    server = make_server(monitors=[m], active_monitor=m, clients=[client])
    wel.focus_client(server, client)

    with patch("wel.apply_geometry"):
        wel.toggle_fullscreen(server)

    assert m.active_workspace.fullscreen is client


def test_toggle_fullscreen_to_tile():
    """toggle_fullscreen on a fullscreen window with no saved float
    geometry clears the slot; client becomes tiled again."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    client = make_client(workspace=m.active_workspace, focus_order=1)
    m.active_workspace.fullscreen = client
    server = make_server(monitors=[m], active_monitor=m, clients=[client])

    with patch("wel.apply_geometry"):
        wel.toggle_fullscreen(server)

    assert m.active_workspace.fullscreen is None
    assert wel.client_layer(client) == wel.Layer.TILE


def test_toggle_fullscreen_to_float():
    """toggle_fullscreen on a fullscreen window that was floating restores
    the float; floating_geom is preserved through fullscreen."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    saved = wel.Rect(10, 20, 300, 200)
    client = make_client(
        workspace=m.active_workspace, floating_geom=saved, focus_order=1)
    m.active_workspace.fullscreen = client
    server = make_server(monitors=[m], active_monitor=m, clients=[client])

    with patch("wel.apply_geometry"):
        wel.toggle_fullscreen(server)

    assert m.active_workspace.fullscreen is None
    assert client.floating_geom == saved
    assert wel.client_layer(client) == wel.Layer.FLOAT


def test_toggle_fullscreen_no_focus():
    """toggle_fullscreen with nothing focused is a no-op."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    server = make_server(monitors=[m], active_monitor=m)

    with patch("wel.set_fullscreen") as sf:
        wel.toggle_fullscreen(server)

    sf.assert_not_called()


def test_toggle_floating_to_float():
    """toggle_floating on a tiled focused window seeds floating_geom from
    the current outer rect so the float starts where it tiled."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    client = make_client(workspace=m.active_workspace)
    server = make_server(monitors=[m], active_monitor=m, clients=[client])
    wel.focus_client(server, client)

    seed = wel.Rect(50, 60, 304, 204)
    with patch("wel.client_outer_rect", return_value=seed), \
         patch("wel.apply_geometry"):
        wel.toggle_floating(server)

    assert client.floating_geom == seed


def test_toggle_floating_to_tile():
    """toggle_floating on a floating focused window clears floating_geom so
    it re-tiles."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    client = make_client(
        workspace=m.active_workspace,
        floating_geom=wel.Rect(10, 20, 300, 200),
        focus_order=1,
    )
    server = make_server(monitors=[m], active_monitor=m, clients=[client])

    with patch("wel.apply_geometry"):
        wel.toggle_floating(server)

    assert client.floating_geom is None


def test_toggle_floating_fullscreen_noop():
    """toggle_floating is a no-op while the focused window is fullscreen."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    client = make_client(workspace=m.active_workspace, focus_order=1)
    m.active_workspace.fullscreen = client
    server = make_server(monitors=[m], active_monitor=m, clients=[client])
    before = client.floating_geom

    with patch("wel.apply_geometry") as apply_geom:
        wel.toggle_floating(server)

    assert client.floating_geom is before
    apply_geom.assert_not_called()


def test_toggle_floating_no_focus():
    """toggle_floating with nothing focused is a no-op."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    server = make_server(monitors=[m], active_monitor=m)

    with patch("wel.apply_geometry") as apply_geom:
        wel.toggle_floating(server)

    apply_geom.assert_not_called()


def test_zoom_promotes():
    """zoom on a non-master tile swaps it with the master and re-arranges.
    The focused window naturally lands as the new master."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    a = make_client(workspace=m.active_workspace)
    b = make_client(workspace=m.active_workspace)
    c = make_client(workspace=m.active_workspace)
    server = make_server(monitors=[m], active_monitor=m, clients=[a, b, c])
    wel.focus_client(server, b)  # b becomes most-recent on m

    with patch("wel.apply_geometry") as apply_geom:
        wel.zoom(server)

    assert server.clients == [b, a, c]
    assert wel.top_client(server, m) is b
    apply_geom.assert_called_once_with(server, m)


def test_zoom_toggles():
    """zoom on the master swaps it with the most-recently-focused other
    tile and follows focus to the new master."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    a = make_client(workspace=m.active_workspace)
    b = make_client(workspace=m.active_workspace)
    c = make_client(workspace=m.active_workspace)
    server = make_server(monitors=[m], active_monitor=m, clients=[a, b, c])
    # b focused most recently among non-masters; a is master and focused last.
    wel.focus_client(server, c)
    wel.focus_client(server, b)
    wel.focus_client(server, a)

    with patch("wel.apply_geometry") as apply_geom:
        wel.zoom(server)

    assert server.clients == [b, a, c]
    assert wel.top_client(server, m) is b
    apply_geom.assert_called_once_with(server, m)


def test_zoom_single_tile():
    """zoom is a no-op when fewer than two tiled windows are on the monitor."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    a = make_client(workspace=m.active_workspace)
    server = make_server(monitors=[m], active_monitor=m, clients=[a])
    wel.focus_client(server, a)

    with patch("wel.apply_geometry") as apply_geom:
        wel.zoom(server)

    assert server.clients == [a]
    apply_geom.assert_not_called()


def test_zoom_remembers_master():
    """After promoting a non-master, the next zoom toggles back to the
    displaced master, not to whatever else has the next-highest focus order."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    a = make_client(workspace=m.active_workspace)
    b = make_client(workspace=m.active_workspace)
    c = make_client(workspace=m.active_workspace)
    server = make_server(monitors=[m], active_monitor=m, clients=[a, b, c])
    # Focus history: c then b then nothing on a (a is master, never focused).
    wel.focus_client(server, c)
    wel.focus_client(server, b)

    with patch("wel.apply_geometry"):
        wel.zoom(server)  # promote b over a
    assert server.clients == [b, a, c]

    with patch("wel.apply_geometry"):
        wel.zoom(server)  # toggle back to a (not c, even though c has history)

    assert server.clients == [a, b, c]
    assert wel.top_client(server, m) is a


def test_zoom_floating_focus():
    """zoom is a no-op when the focused window isn't on the tile layer."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    a = make_client(workspace=m.active_workspace)
    b = make_client(
        workspace=m.active_workspace,
        floating_geom=wel.Rect(0, 0, 100, 100),
    )
    server = make_server(monitors=[m], active_monitor=m, clients=[a, b])
    wel.focus_client(server, b)

    with patch("wel.apply_geometry") as apply_geom:
        wel.zoom(server)

    assert server.clients == [a, b]
    apply_geom.assert_not_called()


def test_request_fullscreen_enters():
    """A tiled client whose app requests fullscreen lands in its
    workspace's fullscreen slot."""
    server = make_server()
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    client = make_client(
        toplevel=MagicMock(),
        scene_tree=MagicMock(),
        workspace=m.active_workspace,
    )
    client.toplevel.requested.fullscreen = True

    with patch("wel.apply_tree"), patch("wel.apply_geometry"), \
         patch("wel.apply_focus"):
        wel.client_request_fullscreen(server, client, None)

    assert m.active_workspace.fullscreen is client
    server.lib.wlr_xdg_toplevel_set_fullscreen.assert_called_once_with(
        client.toplevel, True)


def test_request_fullscreen_keeps_float():
    """A floating client that goes fullscreen keeps its floating_geom
    intact, so exit later returns to the same rect."""
    server = make_server()
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    saved = wel.Rect(10, 20, 300, 200)
    client = make_client(
        toplevel=MagicMock(),
        scene_tree=MagicMock(),
        workspace=m.active_workspace,
        floating_geom=saved,
    )
    client.toplevel.requested.fullscreen = True

    with patch("wel.apply_tree"), patch("wel.apply_geometry"), \
         patch("wel.apply_focus"):
        wel.client_request_fullscreen(server, client, None)

    assert m.active_workspace.fullscreen is client
    assert client.floating_geom == saved


def test_request_fullscreen_to_tile():
    """A fullscreen client with no saved float geometry returns to TILE
    when its app requests un-fullscreen."""
    server = make_server()
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    client = make_client(
        toplevel=MagicMock(),
        scene_tree=MagicMock(),
        workspace=m.active_workspace,
    )
    m.active_workspace.fullscreen = client
    client.toplevel.requested.fullscreen = False

    with patch("wel.apply_tree"), patch("wel.apply_geometry"), \
         patch("wel.apply_focus"):
        wel.client_request_fullscreen(server, client, None)

    assert m.active_workspace.fullscreen is None
    assert wel.client_layer(client) == wel.Layer.TILE


def test_request_fullscreen_to_float():
    """A fullscreen client that was floating returns to FLOAT when its app
    requests un-fullscreen."""
    server = make_server()
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    saved = wel.Rect(10, 20, 300, 200)
    client = make_client(
        toplevel=MagicMock(),
        scene_tree=MagicMock(),
        workspace=m.active_workspace,
        floating_geom=saved,
    )
    m.active_workspace.fullscreen = client
    client.toplevel.requested.fullscreen = False

    with patch("wel.apply_tree"), patch("wel.apply_geometry"), \
         patch("wel.apply_focus"):
        wel.client_request_fullscreen(server, client, None)

    assert m.active_workspace.fullscreen is None
    assert client.floating_geom == saved
    assert wel.client_layer(client) == wel.Layer.FLOAT


def test_request_fullscreen_pre_map():
    """A request that fires before map (scene_tree still None) is deferred;
    client_map then promotes the window using requested.fullscreen."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    server = make_server(monitors=[m], active_monitor=m)
    client = make_client(scene_tree=None)
    client.toplevel.requested.fullscreen = True

    with patch("wel.set_fullscreen") as sf:
        wel.client_request_fullscreen(server, client, None)
    sf.assert_not_called()

    with patch("wel.set_fullscreen") as sf, patch("wel.focus_client"):
        wel.client_map(server, client, None)
    sf.assert_called_with(server, m.active_workspace, client)


def test_request_fullscreen_noop():
    """An already-fullscreen client whose app re-requests fullscreen is a
    no-op so no spurious configure goes out."""
    server = make_server()
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    client = make_client(
        toplevel=MagicMock(),
        scene_tree=MagicMock(),
        workspace=m.active_workspace,
    )
    m.active_workspace.fullscreen = client
    client.toplevel.requested.fullscreen = True

    with patch("wel.apply_tree"), patch("wel.apply_geometry"), \
         patch("wel.apply_focus"):
        wel.client_request_fullscreen(server, client, None)

    server.lib.wlr_xdg_toplevel_set_fullscreen.assert_not_called()


def test_cycle_focus_fullscreen():
    """cycle_focus is inert while a fullscreen window owns the monitor so
    focus stays pinned to it."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    a = make_client(workspace=m.active_workspace)
    b = make_client(workspace=m.active_workspace)
    m.active_workspace.fullscreen = a
    server = make_server(monitors=[m], active_monitor=m, clients=[a, b])

    with patch("wel.focus_client") as focus:
        wel.cycle_focus(server, +1)

    focus.assert_not_called()


def test_client_map_unfullscreens_existing():
    """A new window on a workspace that already hosts a fullscreen window
    un-fullscreens that window first so the new one isn't buried."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    existing = make_client(workspace=m.active_workspace)
    m.active_workspace.fullscreen = existing
    server = make_server(monitors=[m], active_monitor=m, clients=[existing])
    fresh = make_client(scene_tree=None)

    with patch("wel.focus_client"), patch("wel.apply_geometry"):
        wel.client_map(server, fresh, None)

    assert m.active_workspace.fullscreen is None
    server.lib.wlr_xdg_toplevel_set_fullscreen.assert_called_once_with(
        existing.toplevel, False)


def test_resize_client_fullscreen():
    """Resizing a fullscreen window leaves no room for borders: the inner
    surface fills the rect, the xdg subtree sits flush at (0, 0), and the
    border rects collapse to zero size."""
    server = make_server()
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    client = make_client(
        workspace=m.active_workspace,
        borders=tuple(MagicMock(name=f"b{i}") for i in range(4)),
    )
    m.active_workspace.fullscreen = client

    wel.resize_client(server, client, wel.Rect(0, 0, 800, 600))

    server.lib.wlr_xdg_toplevel_set_size.assert_called_once_with(
        client.toplevel, 800, 600)
    # The xdg subtree is repositioned flush against the wrapper origin.
    server.lib.wlr_scene_node_set_position.assert_any_call(
        server.ffi.addressof.return_value, 0, 0)
    # All four border rects sized so they cover nothing.
    top, bottom, left, right = client.borders
    server.lib.wlr_scene_rect_set_size.assert_any_call(top, 800, 0)
    server.lib.wlr_scene_rect_set_size.assert_any_call(bottom, 800, 0)
    server.lib.wlr_scene_rect_set_size.assert_any_call(left, 0, 600)
    server.lib.wlr_scene_rect_set_size.assert_any_call(right, 0, 600)


# --- layer-shell ----------------------------------------------------------


def make_layer_surface(**kwargs):
    """Build a LayerSurface, filling fields the test doesn't care about."""
    return wel.LayerSurface(**{
        "layer_surface": MagicMock(),
        "scene_layer": MagicMock(),
        "scene_tree": MagicMock(),
        "popups_tree": MagicMock(),
        "monitor": MagicMock(),
        "focused": False,
        "listeners": [],
        **kwargs,
    })


def _stage_layer_surface_new(server, *, layer=0, output=None, monitor=None):
    """Stage layer_surface_new so the cast resolves to a controllable
    wlr_layer_surface_v1 with the given pending layer and output."""
    layer_surface = MagicMock(name="layer_surface")
    layer_surface.pending.layer = layer
    layer_surface.output = output if output is not None else server.ffi.NULL
    server.ffi.cast.side_effect = lambda type_str, val: {
        "struct wlr_layer_surface_v1 *": layer_surface,
    }.get(type_str, ("CAST", type_str, val))
    if monitor is not None and monitor not in server.monitors:
        server.monitors.append(monitor)
        server.active_monitor = monitor
    return layer_surface


def test_setup_layer_shell():
    """Setup creates the layer-shell global so apps can bind it."""
    ffi = MagicMock(name="ffi")
    lib = MagicMock(name="lib")
    lib.WL_SEAT_CAPABILITY_POINTER = 1
    lib.WL_SEAT_CAPABILITY_KEYBOARD = 2
    listen = MagicMock(side_effect=lambda *_a: MagicMock())
    build = (ffi, lib, listen, MagicMock(), MagicMock())
    with patch("wel.bindings.build", return_value=build), \
         patch("wel.build_keycode_map", return_value=make_keycode_map()):
        server = wel.setup()

    lib.wlr_layer_shell_v1_create.assert_called_once_with(
        lib.wl_display_create.return_value, 3)
    assert server.layer_shell is lib.wlr_layer_shell_v1_create.return_value


def test_setup_layer_listener():
    """new_surface on the layer-shell drives layer_surface_new so each
    shell-anchored window hits our handler."""
    ffi = MagicMock(name="ffi")
    lib = MagicMock(name="lib")
    lib.WL_SEAT_CAPABILITY_POINTER = 1
    lib.WL_SEAT_CAPABILITY_KEYBOARD = 2
    listen = MagicMock(side_effect=lambda *_a: MagicMock())
    build = (ffi, lib, listen, MagicMock(), MagicMock())
    with patch("wel.bindings.build", return_value=build), \
         patch("wel.build_keycode_map", return_value=make_keycode_map()), \
         patch("wel.layer_surface_new") as handler:
        built = wel.setup()
        trigger(built, lib.welpy_layer_shell_new_surface, "LS_DATA")
    handler.assert_called_once_with(built, "LS_DATA")


def test_layer_new_no_monitor():
    """A surface with no requested output and no monitors connected is
    rejected instead of crashing."""
    server = make_server()
    _stage_layer_surface_new(server)

    wel.layer_surface_new(server, "DATA")

    server.lib.wlr_layer_surface_v1_destroy.assert_called_once()
    server.lib.wlr_scene_layer_surface_v1_create.assert_not_called()


def test_layer_new_assigns_monitor():
    """When the app didn't pick a screen, place the surface on the active
    one and write back the output so wlroots agrees."""
    server = make_server()
    monitor = make_monitor()
    layer_surface = _stage_layer_surface_new(server, monitor=monitor)

    wel.layer_surface_new(server, "DATA")

    assert layer_surface.output is monitor.output
    assert monitor.layers[wel.Layer.BACKGROUND][0].monitor is monitor


def test_layer_new_buckets():
    """The surface lands in the monitor bucket matching its pending layer
    (zwlr 0..3 -> BACKGROUND/BOTTOM/TOP/OVERLAY)."""
    server = make_server()
    monitor = make_monitor()
    _stage_layer_surface_new(server, layer=2, monitor=monitor)  # TOP

    wel.layer_surface_new(server, "DATA")

    assert len(monitor.layers[wel.Layer.TOP]) == 1
    assert monitor.layers[wel.Layer.BACKGROUND] == []


def test_layer_new_popups_high():
    """TOP/OVERLAY surfaces keep their popups in their own scene tree so a
    launcher's menu isn't buried by sibling overlays."""
    server = make_server()
    monitor = make_monitor()
    _stage_layer_surface_new(server, layer=3, monitor=monitor)  # OVERLAY

    wel.layer_surface_new(server, "DATA")

    server.lib.wlr_scene_tree_create.assert_called_once_with(
        server.layers[wel.Layer.OVERLAY])


def test_layer_new_popups_low():
    """BACKGROUND/BOTTOM popups attach into TOP so e.g. a wallpaper's menu
    isn't hidden behind a bar."""
    server = make_server()
    monitor = make_monitor()
    _stage_layer_surface_new(server, layer=0, monitor=monitor)

    wel.layer_surface_new(server, "DATA")

    server.lib.wlr_scene_tree_create.assert_called_once_with(
        server.layers[wel.Layer.TOP])


def test_layer_new_send_enter():
    """The surface is told which screen it lives on so apps can scale
    correctly for that monitor."""
    server = make_server()
    monitor = make_monitor()
    layer_surface = _stage_layer_surface_new(server, monitor=monitor)

    wel.layer_surface_new(server, "DATA")

    server.lib.wlr_surface_send_enter.assert_called_once_with(
        layer_surface.surface, monitor.output)


def test_layer_commit_moves_bucket():
    """If the app moves the surface to a different layer on commit, the
    bucket reflects the new layer; apply_tree reparents."""
    server = make_server()
    monitor = make_monitor()
    ls = make_layer_surface(monitor=monitor)
    ls.layer_surface.initial_commit = False
    ls.layer_surface.current.layer = 2  # TOP
    monitor.layers[wel.Layer.BOTTOM].append(ls)

    with patch("wel.arrange_layers"):
        wel.layer_surface_commit(server, ls, None)

    assert ls not in monitor.layers[wel.Layer.BOTTOM]
    assert ls in monitor.layers[wel.Layer.TOP]


def test_layer_unmap_clears_focus():
    """Closing the keyboard-grabbing shell surface releases its keyboard
    hold so clients can be focused again."""
    server = make_server()
    monitor = make_monitor()
    ls = make_layer_surface(monitor=monitor, focused=True)

    with patch("wel.arrange_layers"), patch("wel.focus_client"):
        wel.layer_surface_unmap(server, ls, None)

    assert ls.focused is False


def test_layer_unmap_refocuses_client():
    """When the keyboard-grabbing surface goes away, focus returns to the
    top client on the active screen."""
    monitor = make_monitor()
    monitor.active_workspace = make_workspace(monitor=monitor)
    client = make_client(workspace=monitor.active_workspace, focus_order=1)
    server = make_server(
        monitors=[monitor], active_monitor=monitor, clients=[client])
    ls = make_layer_surface(monitor=monitor, focused=True)

    with patch("wel.arrange_layers"), patch("wel.focus_client") as focus:
        wel.layer_surface_unmap(server, ls, None)

    focus.assert_called_once_with(server, client)


def test_layer_unmap_refocuses_monitor():
    """A shell surface on a secondary screen returns focus to that screen's
    top client, not the globally selected screen."""
    selected = make_monitor()
    selected.active_workspace = make_workspace(monitor=selected)
    monitor = make_monitor()
    monitor.active_workspace = make_workspace(monitor=monitor)
    selected_client = make_client(
        workspace=selected.active_workspace, focus_order=10)
    monitor_client = make_client(
        workspace=monitor.active_workspace, focus_order=1)
    server = make_server(
        monitors=[selected, monitor], active_monitor=selected,
        clients=[selected_client, monitor_client])
    ls = make_layer_surface(monitor=monitor, focused=True)

    with patch("wel.arrange_layers"), patch("wel.focus_client") as focus:
        wel.layer_surface_unmap(server, ls, None)

    focus.assert_called_once_with(server, monitor_client)


def test_layer_unmap_unfocused():
    """Closing a non-keyboard layer surface (e.g. wallpaper) doesn't
    disturb whatever has the keyboard."""
    monitor = make_monitor()
    server = make_server(monitors=[monitor])
    ls = make_layer_surface(monitor=monitor, focused=False)

    with patch("wel.arrange_layers"), patch("wel.focus_client") as focus:
        wel.layer_surface_unmap(server, ls, None)

    focus.assert_not_called()


def test_layer_cleanup_removes():
    """Destroying a shell surface drops it from its monitor's bucket so
    later arranges don't trip over a stale entry."""
    server = make_server()
    monitor = make_monitor()
    ls = make_layer_surface(monitor=monitor)
    monitor.layers[wel.Layer.TOP].append(ls)
    h = MagicMock()
    ls.listeners.append(h)

    with patch("wel.arrange_layers"):
        wel.layer_surface_cleanup(server, ls, None)

    h.remove.assert_called_once()
    assert ls not in monitor.layers[wel.Layer.TOP]
    assert ls.monitor is None


def test_layer_cleanup_trees():
    """Destroying a shell surface releases the popup tree we own; the
    content tree is freed by wlr_scene_layer_surface_v1's own destroy
    listener so we must not touch it."""
    server = make_server()
    monitor = make_monitor()
    ls = make_layer_surface(monitor=monitor)
    server.ffi.addressof.side_effect = lambda node: ("ADDR", node)

    wel.layer_surface_cleanup(server, ls, None)

    server.lib.wlr_scene_node_destroy.assert_called_once_with(
        ("ADDR", ls.popups_tree.node))


def test_monitor_cleanup_destroys_layers():
    """When a screen goes away its layer surfaces are destroyed first so
    wlroots doesn't try to render them against a freed output."""
    monitor = make_monitor()
    server = make_server(monitors=[monitor])
    ls = make_layer_surface(monitor=monitor)
    monitor.layers[wel.Layer.TOP].append(ls)

    wel.monitor_cleanup(server, monitor, None)

    server.lib.wlr_layer_surface_v1_destroy.assert_called_once_with(
        ls.layer_surface)


def test_arrange_layers_shrinks_area():
    """A surface with exclusive_zone > 0 reserves space; the monitor's
    window_area shrinks accordingly and tiles re-flow."""
    server = make_server()
    monitor = make_monitor(window_area=wel.Rect(0, 0, 800, 600))
    ls = make_layer_surface(monitor=monitor)
    ls.layer_surface.initialized = True
    ls.layer_surface.current.exclusive_zone = 30
    monitor.layers[wel.Layer.TOP].append(ls)

    def make_box(_type, vals):
        box = MagicMock()
        box.x, box.y, box.width, box.height = vals
        return box
    server.ffi.new.side_effect = make_box
    # Mimic the wlroots helper shrinking `usable` to reflect the zone.
    def configure(_scene, _full, usable):
        usable.y = 30
        usable.height = 570
    server.lib.wlr_scene_layer_surface_v1_configure.side_effect = configure

    with patch("wel.monitor_box", return_value=wel.Rect(0, 0, 800, 600)):
        wel.arrange_layers(server, monitor)

    assert monitor.window_area == wel.Rect(0, 30, 800, 570)


def test_popup_new_layer_owner():
    """A popup whose parent is a layer-shell surface unconstrains against
    that surface's monitor, not a client's."""
    monitor = make_monitor()
    server = make_server(monitors=[monitor])
    ls = make_layer_surface(monitor=monitor)
    ls.scene_tree.node.x = 0
    ls.scene_tree.node.y = 0
    ls.layer_surface.surface = MagicMock(name="ls_surface")
    monitor.layers[wel.Layer.TOP].append(ls)
    server.lib.wlr_surface_get_root_surface.return_value = (
        ls.layer_surface.surface)
    _stage_popup(server)

    with patch("wel.monitor_box",
               return_value=wel.Rect(0, 0, 800, 600)):
        wel.popup_new(server, "DATA")
        trigger(server, server.lib.welpy_surface_commit, "COMMIT")

    server.lib.wlr_xdg_popup_unconstrain_from_box.assert_called_once()
    server.ffi.new.assert_any_call(
        "struct wlr_box *", [0, 0, 800, 600])


# --- configure tracking ---------------------------------------------------


def test_track_configure_acked():
    """An already-acked serial leaves pending cleared, so a no-op configure
    doesn't freeze the screen."""
    # pylint: disable=protected-access
    client = make_client(scene_tree=MagicMock())
    client.toplevel.base.current.configure_serial = 5

    wel._track_configure(client, 5)
    assert client.pending_serial is None
    wel._track_configure(client, 3)
    assert client.pending_serial is None


def test_track_configure_pending():
    """A serial the client hasn't reached yet is recorded as pending so the
    screen waits."""
    # pylint: disable=protected-access
    client = make_client(scene_tree=MagicMock())
    client.toplevel.base.current.configure_serial = 3

    wel._track_configure(client, 7)

    assert client.pending_serial == 7


def test_set_size_tracks():
    """set_size sends the configure and records the returned serial."""
    server = make_server()
    server.lib.wlr_xdg_toplevel_set_size.return_value = 9
    client = make_client(scene_tree=MagicMock())

    wel.set_size(server, client, 100, 200)

    server.lib.wlr_xdg_toplevel_set_size.assert_called_once_with(
        client.toplevel, 100, 200)
    assert client.pending_serial == 9


def test_set_activated_tracks():
    """set_activated sends the configure and records the returned serial."""
    server = make_server()
    server.lib.wlr_xdg_toplevel_set_activated.return_value = 4
    client = make_client(scene_tree=MagicMock())

    wel.set_activated(server, client, True)

    server.lib.wlr_xdg_toplevel_set_activated.assert_called_once_with(
        client.toplevel, True)
    assert client.pending_serial == 4


def test_set_tiled_tracks():
    """set_tiled sends the configure and records the returned serial."""
    server = make_server()
    server.lib.wlr_xdg_toplevel_set_tiled.return_value = 6
    client = make_client(scene_tree=MagicMock())

    wel.set_tiled(server, client, 15)

    server.lib.wlr_xdg_toplevel_set_tiled.assert_called_once_with(
        client.toplevel, 15)
    assert client.pending_serial == 6


def test_begin_dragging_floats():
    """begin_dragging_client makes the dragged window floating by seeding
    floating_geom from its current outer rect."""
    client = make_client()
    client.scene_tree.node.x = 0
    client.scene_tree.node.y = 0
    server = make_server(
        clients=[client], cursor=make_cursor(xcursor_manager="X"))
    server.cursor.cursor.x = 0
    server.cursor.cursor.y = 0
    node = MagicMock(name="node")
    node.parent = client.scene_tree
    server.lib.wlr_scene_node_at.return_value = node

    seed = wel.Rect(0, 0, 100, 80)
    with patch("wel.client_outer_rect", return_value=seed), \
         patch("wel.apply_geometry"):
        wel.begin_dragging_client(server)

    assert client.floating_geom == seed


def test_client_commit_initial_tiled():
    """A tiled client's initial commit defers tiling to map so siblings
    don't reflow before the new window can appear."""
    server = make_server()
    workspace = make_workspace()
    toplevel = MagicMock()
    toplevel.base.initial_commit = True
    client = make_client(toplevel=toplevel, workspace=workspace)

    with patch("wel.apply_geometry") as apply_geom:
        wel.client_commit(server, client, None)

    apply_geom.assert_not_called()
    server.lib.wlr_xdg_toplevel_set_size.assert_called_once_with(
        toplevel, 0, 0)


def test_client_commit_initial_floating():
    """A floating client falls back to the (0, 0) initial configure."""
    server = make_server()
    workspace = make_workspace()
    toplevel = MagicMock()
    toplevel.base.initial_commit = True
    client = make_client(
        toplevel=toplevel,
        floating_geom=wel.Rect(0, 0, 100, 100),
        workspace=workspace,
    )

    with patch("wel.apply_geometry") as apply_geom:
        wel.client_commit(server, client, None)

    apply_geom.assert_not_called()
    server.lib.wlr_xdg_toplevel_set_size.assert_called_once_with(
        toplevel, 0, 0)


def test_client_commit_initial_unassigned():
    """A tiled client with no workspace falls back to the (0, 0) initial
    configure so the required configure still goes out."""
    server = make_server()
    toplevel = MagicMock()
    toplevel.base.initial_commit = True
    client = make_client(toplevel=toplevel, workspace=None)

    with patch("wel.apply_geometry") as apply_geom:
        wel.client_commit(server, client, None)

    apply_geom.assert_not_called()
    server.lib.wlr_xdg_toplevel_set_size.assert_called_once_with(
        toplevel, 0, 0)


def test_client_unmap_arranges():
    """After a tiled client unmaps, its monitor re-flows so remaining
    tiles expand -- in the same event as the window's removal so it lands
    in a single frame."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    a = make_client(workspace=m.active_workspace)
    b = make_client(workspace=m.active_workspace)
    server = make_server(monitors=[m], active_monitor=m, clients=[a, b])

    with patch("wel.apply_geometry") as apply_geom:
        wel.client_unmap(server, a, None)

    apply_geom.assert_called_once_with(server, m)


def test_client_unmap_destroys_tree():
    """Unmapping releases the scene tree so the disappearing window, the
    reflow, and the focus shift all happen together."""
    client = make_client()
    server = make_server(clients=[client])

    wel.client_unmap(server, client, None)

    server.lib.wlr_scene_node_destroy.assert_called_once_with(
        server.ffi.addressof.return_value)
    assert client.scene_tree is None
    assert client not in server.clients


def test_client_unmap_orphan():
    """Unmapping an orphaned client doesn't trigger an arrange."""
    client = make_client(workspace=None)
    server = make_server(clients=[client])

    with patch("wel.apply_geometry") as apply_geom:
        wel.client_unmap(server, client, None)

    apply_geom.assert_not_called()


def test_client_unmap_stale():
    """Unmapping a client whose monitor has already been removed is a
    no-op for arrange."""
    m = make_monitor()  # not in server.monitors
    m.active_workspace = make_workspace(monitor=m)
    client = make_client(workspace=m.active_workspace)
    server = make_server(clients=[client])

    with patch("wel.apply_geometry") as apply_geom:
        wel.client_unmap(server, client, None)

    apply_geom.assert_not_called()


def test_monitor_new_updates():
    """monitor_new triggers update_monitors so the new monitor's box is
    picked up and orphans are adopted."""
    server = make_server()

    with patch("wel.update_monitors") as upd:
        wel.monitor_new(server, "OUTPUT_DATA")

    upd.assert_called_once_with(server)


def test_monitor_cleanup_removes():
    """monitor_cleanup drops the monitor from server.monitors and triggers
    update_monitors so apply_hierarchy migrates its workspaces."""
    monitor = make_monitor(scene_output="SO")
    server = make_server(monitors=[monitor])

    with patch("wel.update_monitors") as upd:
        wel.monitor_cleanup(server, monitor, None)

    assert monitor not in server.monitors
    upd.assert_called_once_with(server)


def test_setup_layout_change_updates():
    """A change in the screen layout (monitor added/removed/repositioned)
    drives update_monitors so windows re-flow onto the new geometry."""
    ffi = MagicMock(name="ffi")
    lib = MagicMock(name="lib")
    lib.WL_SEAT_CAPABILITY_POINTER = 1
    lib.WL_SEAT_CAPABILITY_KEYBOARD = 2
    listen = MagicMock(side_effect=lambda *_a: MagicMock())
    build = (ffi, lib, listen, MagicMock(), MagicMock())
    with patch("wel.bindings.build", return_value=build), \
         patch("wel.build_keycode_map",
               return_value=make_keycode_map()), \
         patch("wel.update_monitors") as upd:
        server = wel.setup()
        trigger(server, lib.welpy_output_layout_change, "LAYOUT_DATA")

    upd.assert_called_once_with(server)


def test_monitor_request_state_updates():
    """A reconfigure may have changed the monitor's box, so all monitors
    re-flow."""
    server = make_server()
    monitor = make_monitor(output="OUT", scene_output="SO")

    with patch("wel.update_monitors") as upd:
        wel.monitor_request_state(server, monitor, "RS_DATA")

    upd.assert_called_once_with(server)


# --- override / load_config ------------------------------------------------


def test_override_form1_replaces(monkeypatch):
    """@wel.override on a fresh function installs it at wel.<name>."""
    monkeypatch.setattr(wel, "modkey", wel.modkey)

    @wel.override
    def modkey(_orig, _server):
        return 0x99

    assert wel.modkey(MagicMock()) == 0x99


def test_override_form1_chains_original(monkeypatch):
    """The previous function is passed in as the first argument."""
    monkeypatch.setattr(wel, "modkey", wel.modkey)

    @wel.override
    def modkey(orig, server):
        return orig(server) | 0xFF

    server = MagicMock()
    server.lib.WLR_MODIFIER_LOGO = 0x40
    assert wel.modkey(server) == 0x40 | 0xFF


def test_override_form2_wl(monkeypatch):
    """@wel.override(target) installs at the target's home, even when the
    new function has a different local name."""
    monkeypatch.setattr(wel, "modkey", wel.modkey)

    @wel.override(wel.modkey)
    def renamed(_orig, _server):
        return 0x123

    assert wel.modkey(MagicMock()) == 0x123


def test_override_form2_bindings(monkeypatch):
    """@wel.override reaches outside wel: targeting bindings.build installs
    the replacement in bindings, not in wel."""
    monkeypatch.setattr(bindings, "build", bindings.build)

    @wel.override(bindings.build)
    def build(_orig):
        return "stub"

    assert bindings.build() == "stub"
    assert not hasattr(wel, "build")


def test_override_chain_composes(monkeypatch):
    """Form-1 then form-2 chains correctly: the second wraps the first,
    which wraps the built-in. Exercises the __module__ rewrite that lets
    form-2 find the previous wrapper at wel.<name>."""
    monkeypatch.setattr(wel, "modkey", wel.modkey)

    @wel.override
    def modkey(orig, server):
        return orig(server) + 1

    @wel.override(wel.modkey)
    def newer(orig, server):
        return orig(server) * 10

    server = MagicMock()
    server.lib.WLR_MODIFIER_LOGO = 5
    # built-in=5; inner adds 1 -> 6; outer multiplies by 10 -> 60.
    assert wel.modkey(server) == 60


def test_autostart_overridable(monkeypatch):
    """@wel.override on `autostart` lets config swap the launched programs."""
    monkeypatch.setattr(wel, "autostart", wel.autostart)
    calls = []

    @wel.override
    def autostart(_orig, server):
        calls.append(server)

    wel.autostart("SERVER")
    assert calls == ["SERVER"]


def test_override_unknown_name():
    """Form 1 against a name not on wel: error hints at the explicit form."""
    with pytest.raises(AttributeError, match=r"@wel\.override\("):
        @wel.override
        def nonexistent_function(_orig):
            pass


def test_override_non_callable():
    """Non-callable argument: explicit TypeError, not a confusing
    AttributeError on `__module__`."""
    with pytest.raises(TypeError, match="expects a function"):
        wel.override(None)


def test_override_callable_no_name():
    """Callable missing __name__ (e.g. functools.partial): default
    AttributeError names the missing attribute clearly."""
    partial = functools.partial(lambda x: x)
    with pytest.raises(AttributeError, match="__name__"):
        wel.override(partial)


def test_override_form2_moved_target():
    """Form 2 with a target detached from its supposed home silently
    degrades to form 1. This pins the limitation: the decorator can't
    distinguish a fresh form-1 function from a misused form-2 target."""
    def fake(_orig):
        pass
    fake.__module__ = "wel"
    fake.__name__ = "definitely_not_a_real_wl_attribute"
    with pytest.raises(AttributeError, match=r"@wel\.override\("):
        wel.override(fake)


def test_load_config_missing(tmp_path):
    """Absent config file is a silent no-op."""
    wel.load_config(tmp_path / "nonexistent.py")


def test_load_config_runs(tmp_path, monkeypatch):
    """The loader executes the file's top-level code."""
    # pylint: disable=protected-access,no-member
    monkeypatch.setattr(wel, "_test_marker", None, raising=False)
    config = tmp_path / "config.py"
    config.write_text(dedent("""\
        import wel
        wel._test_marker = 'ran'
    """))
    wel.load_config(config)
    assert wel._test_marker == "ran"


def test_load_config_sys_path(tmp_path, monkeypatch):
    """Sibling files are importable as top-level modules after load, and
    overrides defined in them land on the running wel module."""
    monkeypatch.setattr(wel, "modkey", wel.modkey)
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(sys, "modules", dict(sys.modules))
    (tmp_path / "sibling_ext.py").write_text(dedent("""\
        import wel

        @wel.override
        def modkey(_orig, _server):
            return 0xDEAD
    """))
    (tmp_path / "config.py").write_text(dedent("""\
        import sibling_ext  # noqa: F401
    """))
    wel.load_config(tmp_path / "config.py")
    assert wel.modkey(MagicMock()) == 0xDEAD


# --- workspaces: setup -----------------------------------------------------


def test_setup_workspaces_orphaned():
    """Setup creates 10 orphaned workspaces named "1".."9", "10"."""
    ffi = MagicMock(name="ffi")
    lib = MagicMock(name="lib")
    listen = MagicMock(side_effect=lambda *_a: MagicMock())
    build = (ffi, lib, listen, MagicMock(), MagicMock())
    with patch("wel.bindings.build", return_value=build), \
         patch("wel.build_keycode_map",
               return_value=make_keycode_map()):
        server = wel.setup()
    assert [w.name for w in server.workspaces] == [
        "1", "2", "3", "4", "5", "6", "7", "8", "9", "10"]
    assert all(w.monitor is None for w in server.workspaces)
    assert all(w.fullscreen is None for w in server.workspaces)
    assert server.active_monitor is None


# --- workspaces: apply_hierarchy ------------------------------------------


def test_hierarchy_seed():
    """A monitor with no workspaces gets seeded with the first orphan,
    becoming the active monitor."""
    monitor = make_monitor()
    server = make_server(
        workspaces=[make_workspace(name="1"), make_workspace(name="2")],
        monitors=[monitor])

    wel.apply_hierarchy(server)

    assert server.workspaces[0].monitor is monitor
    assert monitor.active_workspace is server.workspaces[0]
    assert server.active_monitor is monitor
    assert server.workspaces[1].monitor is None


def test_hierarchy_hotplug():
    """When a new monitor joins an existing layout, it stays empty -- no
    orphan is auto-claimed."""
    first = make_monitor()
    ws = make_workspace(name="1", monitor=first)
    first.active_workspace = ws
    second = make_monitor()
    server = make_server(
        workspaces=[ws], monitors=[first, second], active_monitor=first)

    wel.apply_hierarchy(server)

    assert second.active_workspace is None
    assert server.active_monitor is first


def test_hierarchy_unplug_migrate():
    """When a monitor is removed, non-empty workspaces migrate to the
    active monitor."""
    gone = make_monitor()
    survivor = make_monitor()
    ws_gone = make_workspace(name="1", monitor=gone)
    ws_surv = make_workspace(name="2", monitor=survivor)
    survivor.active_workspace = ws_surv
    server = make_server(
        workspaces=[ws_gone, ws_surv], monitors=[survivor],
        active_monitor=survivor, clients=[make_client(workspace=ws_gone)])

    wel.apply_hierarchy(server)

    assert ws_gone.monitor is survivor


def test_hierarchy_rehome_occupied():
    """After every monitor briefly vanished (e.g. a VT switch) orphaned all
    workspaces, a returning monitor re-homes the occupied ones so the bar
    sees them again, not just the seeded active workspace."""
    monitor = make_monitor()
    ws_active = make_workspace(name="1", monitor=None)
    ws_occupied = make_workspace(name="2", monitor=None)
    ws_empty = make_workspace(name="3", monitor=None)
    server = make_server(
        workspaces=[ws_active, ws_occupied, ws_empty], monitors=[monitor],
        clients=[make_client(workspace=ws_occupied)])

    wel.apply_hierarchy(server)

    assert ws_occupied.monitor is monitor
    assert ws_empty.monitor is None


def test_hierarchy_unplug_orphan():
    """When a monitor is removed, empty workspaces on it are orphaned."""
    gone = make_monitor()
    survivor = make_monitor()
    ws_gone = make_workspace(name="1", monitor=gone)
    ws_surv = make_workspace(name="2", monitor=survivor)
    survivor.active_workspace = ws_surv
    server = make_server(
        workspaces=[ws_gone, ws_surv], monitors=[survivor],
        active_monitor=survivor)

    wel.apply_hierarchy(server)

    assert ws_gone.monitor is None


def test_hierarchy_unplug_repoint():
    """When the active monitor is removed, active falls to a survivor."""
    gone = make_monitor()
    survivor = make_monitor()
    ws = make_workspace(name="1", monitor=survivor)
    survivor.active_workspace = ws
    server = make_server(
        workspaces=[ws], monitors=[survivor], active_monitor=gone)

    wel.apply_hierarchy(server)

    assert server.active_monitor is survivor


def test_hierarchy_idempotent():
    """Calling apply_hierarchy twice produces the same state as once."""
    server = make_server(
        workspaces=[make_workspace(name="1"), make_workspace(name="2")],
        monitors=[make_monitor()])

    wel.apply_hierarchy(server)
    snapshot = (
        server.active_monitor,
        [(w.name, w.monitor) for w in server.workspaces],
        [(m, m.active_workspace) for m in server.monitors])
    wel.apply_hierarchy(server)

    assert snapshot == (
        server.active_monitor,
        [(w.name, w.monitor) for w in server.workspaces],
        [(m, m.active_workspace) for m in server.monitors])


def test_hierarchy_fullscreen_unmapped():
    """A fullscreen pointer to a client that's no longer mapped is cleared."""
    monitor = make_monitor()
    ws = make_workspace(name="1", monitor=monitor)
    monitor.active_workspace = ws
    server = make_server(
        workspaces=[ws], monitors=[monitor], active_monitor=monitor)
    ghost = make_client(workspace=ws)
    ws.fullscreen = ghost  # not in server.clients

    wel.apply_hierarchy(server)

    assert ws.fullscreen is None


def test_hierarchy_fullscreen_mismatch():
    """A fullscreen pointer to a client on a different workspace is cleared."""
    monitor = make_monitor()
    active = make_workspace(name="1", monitor=monitor)
    other = make_workspace(name="2")
    monitor.active_workspace = active
    client = make_client(workspace=other)
    server = make_server(
        workspaces=[active, other], monitors=[monitor],
        active_monitor=monitor, clients=[client])
    active.fullscreen = client

    wel.apply_hierarchy(server)

    assert active.fullscreen is None


def test_hierarchy_inactive_empty():
    """A non-active workspace with no clients is orphaned."""
    monitor = make_monitor()
    active = make_workspace(name="1", monitor=monitor)
    inactive = make_workspace(name="2", monitor=monitor)
    monitor.active_workspace = active
    server = make_server(
        workspaces=[active, inactive], monitors=[monitor],
        active_monitor=monitor)

    wel.apply_hierarchy(server)

    assert inactive.monitor is None


def test_hierarchy_inactive_kept():
    """A non-active workspace with clients stays assigned."""
    monitor = make_monitor()
    active = make_workspace(name="1", monitor=monitor)
    inactive = make_workspace(name="2", monitor=monitor)
    monitor.active_workspace = active
    server = make_server(
        workspaces=[active, inactive], monitors=[monitor],
        active_monitor=monitor, clients=[make_client(workspace=inactive)])

    wel.apply_hierarchy(server)

    assert inactive.monitor is monitor


def test_hierarchy_no_monitors():
    """Without monitors, all workspaces are orphaned and active is None."""
    ws = make_workspace(name="1", monitor=MagicMock())
    server = make_server(workspaces=[ws], active_monitor=MagicMock())

    wel.apply_hierarchy(server)

    assert ws.monitor is None
    assert server.active_monitor is None


def test_hierarchy_active_repair():
    """A monitor whose active_workspace lives elsewhere gets repointed."""
    monitor = make_monitor()
    on_monitor = make_workspace(name="1", monitor=monitor)
    elsewhere = make_workspace(name="2")
    monitor.active_workspace = elsewhere  # not on monitor
    server = make_server(
        workspaces=[on_monitor, elsewhere], monitors=[monitor],
        active_monitor=monitor)

    wel.apply_hierarchy(server)

    assert monitor.active_workspace is on_monitor


# --- workspaces: apply_visibility -----------------------------------------


def test_apply_visibility_active():
    """Clients on a monitor's active workspace have their scene nodes
    enabled."""
    monitor = make_monitor()
    ws = make_workspace(name="1", monitor=monitor)
    monitor.active_workspace = ws
    client = make_client(workspace=ws)
    server = make_server(
        workspaces=[ws], monitors=[monitor], clients=[client])

    wel.apply_visibility(server)

    server.lib.wlr_scene_node_set_enabled.assert_called_with(
        server.ffi.addressof.return_value, True)


def test_apply_visibility_inactive():
    """Clients on a non-active workspace have their scene nodes disabled."""
    monitor = make_monitor()
    active = make_workspace(name="1", monitor=monitor)
    inactive = make_workspace(name="2", monitor=monitor)
    monitor.active_workspace = active
    client = make_client(workspace=inactive)
    server = make_server(
        workspaces=[active, inactive], monitors=[monitor], clients=[client])

    wel.apply_visibility(server)

    server.lib.wlr_scene_node_set_enabled.assert_called_with(
        server.ffi.addressof.return_value, False)


def test_apply_visibility_orphan():
    """Clients on an orphaned workspace are hidden."""
    ws = make_workspace(name="1")
    client = make_client(workspace=ws)
    server = make_server(workspaces=[ws], clients=[client])

    wel.apply_visibility(server)

    server.lib.wlr_scene_node_set_enabled.assert_called_with(
        server.ffi.addressof.return_value, False)


# --- workspaces: view_workspace -------------------------------------------


def test_view_workspace_activates():
    """view_workspace makes the target workspace active on its monitor and
    switches the active monitor."""
    m1 = make_monitor()
    m2 = make_monitor()
    ws1 = make_workspace(name="1", monitor=m1)
    ws2 = make_workspace(name="2", monitor=m2)
    m1.active_workspace = ws1
    m2.active_workspace = ws2
    server = make_server(
        workspaces=[ws1, ws2], monitors=[m1, m2], active_monitor=m1)

    wel.view_workspace(server, "2")

    assert m2.active_workspace is ws2
    assert server.active_monitor is m2


def test_view_workspace_adopts_orphan():
    """view_workspace on an orphaned workspace binds it to the active
    monitor before activating it."""
    monitor = make_monitor()
    ws1 = make_workspace(name="1", monitor=monitor)
    orphan = make_workspace(name="2")
    monitor.active_workspace = ws1
    server = make_server(
        workspaces=[ws1, orphan], monitors=[monitor], active_monitor=monitor)

    wel.view_workspace(server, "2")

    assert orphan.monitor is monitor
    assert monitor.active_workspace is orphan


def test_view_workspace_ends_grabs():
    """view_workspace clears any in-progress mouse grabs."""
    monitor = make_monitor()
    ws1 = make_workspace(name="1", monitor=monitor)
    ws2 = make_workspace(name="2")
    monitor.active_workspace = ws1
    client = make_client(workspace=ws1, grab=wel.Grab("move", 0, 0))
    server = make_server(
        workspaces=[ws1, ws2], monitors=[monitor], active_monitor=monitor,
        clients=[client])

    wel.view_workspace(server, "2")

    assert client.grab is None


def test_view_workspace_unknown():
    """view_workspace with an unknown name leaves state untouched."""
    monitor = make_monitor()
    ws = make_workspace(name="1", monitor=monitor)
    monitor.active_workspace = ws
    server = make_server(
        workspaces=[ws], monitors=[monitor], active_monitor=monitor)

    wel.view_workspace(server, "xyz")

    assert monitor.active_workspace is ws


def test_view_workspace_records_previous():
    """Switching workspaces remembers the one being left."""
    monitor = make_monitor()
    ws1 = make_workspace(name="1", monitor=monitor)
    ws2 = make_workspace(name="2")
    monitor.active_workspace = ws1
    server = make_server(
        workspaces=[ws1, ws2], monitors=[monitor], active_monitor=monitor)

    wel.view_workspace(server, "2")

    assert server.previous_workspace == "1"


def test_view_previous_switches_back():
    """view_previous_workspace returns to the last-viewed workspace."""
    monitor = make_monitor()
    ws1 = make_workspace(name="1", monitor=monitor)
    ws2 = make_workspace(name="2")
    monitor.active_workspace = ws1
    server = make_server(
        workspaces=[ws1, ws2], monitors=[monitor], active_monitor=monitor,
        previous_workspace=None)

    wel.view_workspace(server, "2")
    wel.view_previous_workspace(server)

    assert monitor.active_workspace is ws1


def test_view_previous_noop():
    """view_previous_workspace does nothing when there is no history."""
    monitor = make_monitor()
    ws = make_workspace(name="1", monitor=monitor)
    monitor.active_workspace = ws
    server = make_server(
        workspaces=[ws], monitors=[monitor], active_monitor=monitor,
        previous_workspace=None)

    wel.view_previous_workspace(server)

    assert monitor.active_workspace is ws


def test_view_workspace_outgoing():
    """Switching away from an empty workspace orphans it."""
    monitor = make_monitor()
    ws1 = make_workspace(name="1", monitor=monitor)
    ws2 = make_workspace(name="2")
    monitor.active_workspace = ws1
    server = make_server(
        workspaces=[ws1, ws2], monitors=[monitor], active_monitor=monitor)

    wel.view_workspace(server, "2")

    assert ws1.monitor is None


# --- workspaces: move_client_to_workspace ----------------------------------


def test_move_client_reassigns():
    """move_client_to_workspace changes the focused client's workspace."""
    monitor = make_monitor()
    ws1 = make_workspace(name="1", monitor=monitor)
    ws2 = make_workspace(name="3")
    monitor.active_workspace = ws1
    client = make_client(workspace=ws1, focus_order=1)
    server = make_server(
        workspaces=[ws1, ws2], monitors=[monitor], active_monitor=monitor,
        clients=[client])

    wel.move_client_to_workspace(server, "3")

    assert client.workspace is ws2


def test_move_client_adopts():
    """move_client_to_workspace adopts the target onto active_monitor
    if it was orphaned."""
    monitor = make_monitor()
    ws1 = make_workspace(name="1", monitor=monitor)
    orphan = make_workspace(name="4")
    monitor.active_workspace = ws1
    client = make_client(workspace=ws1, focus_order=1)
    server = make_server(
        workspaces=[ws1, orphan], monitors=[monitor], active_monitor=monitor,
        clients=[client])

    wel.move_client_to_workspace(server, "4")

    assert orphan.monitor is monitor


def test_move_client_fullscreen():
    """Moving the fullscreen client off its workspace clears that
    workspace's fullscreen pointer."""
    monitor = make_monitor()
    ws1 = make_workspace(name="1", monitor=monitor)
    ws2 = make_workspace(name="2")
    monitor.active_workspace = ws1
    client = make_client(workspace=ws1, focus_order=1)
    ws1.fullscreen = client
    server = make_server(
        workspaces=[ws1, ws2], monitors=[monitor], active_monitor=monitor,
        clients=[client])

    wel.move_client_to_workspace(server, "2")

    assert ws1.fullscreen is None


def test_move_client_fullscreen_notifies():
    """Moving a fullscreen client out of a workspace also tells the app
    it is no longer fullscreen."""
    monitor = make_monitor()
    ws1 = make_workspace(name="1", monitor=monitor)
    ws2 = make_workspace(name="2")
    monitor.active_workspace = ws1
    client = make_client(workspace=ws1, focus_order=1)
    ws1.fullscreen = client
    server = make_server(
        workspaces=[ws1, ws2], monitors=[monitor], active_monitor=monitor,
        clients=[client])

    wel.move_client_to_workspace(server, "2")

    server.lib.wlr_xdg_toplevel_set_fullscreen.assert_any_call(
        client.toplevel, False)


def test_move_client_target_fullscreen():
    """Moving a client into a workspace with a fullscreen client clears the
    old fullscreen state so the moved window is not buried."""
    monitor = make_monitor()
    ws1 = make_workspace(name="1", monitor=monitor)
    ws2 = make_workspace(name="2", monitor=monitor)
    monitor.active_workspace = ws1
    moved = make_client(workspace=ws1, focus_order=2)
    fullscreen = make_client(workspace=ws2, focus_order=1)
    ws2.fullscreen = fullscreen
    server = make_server(
        workspaces=[ws1, ws2], monitors=[monitor], active_monitor=monitor,
        clients=[moved, fullscreen])

    wel.move_client_to_workspace(server, "2")

    assert ws2.fullscreen is None
    server.lib.wlr_xdg_toplevel_set_fullscreen.assert_any_call(
        fullscreen.toplevel, False)


# --- workspaces: move_active_workspace_to_monitor --------------------------


def test_move_workspace_next():
    """move_active_workspace_to_monitor(+1) migrates the active workspace
    to the next monitor and switches focus there."""
    m1 = make_monitor()
    m2 = make_monitor()
    ws1 = make_workspace(name="1", monitor=m1)
    ws2 = make_workspace(name="2", monitor=m2)
    m1.active_workspace = ws1
    m2.active_workspace = ws2
    server = make_server(
        workspaces=[ws1, ws2], monitors=[m1, m2], active_monitor=m1)

    wel.move_active_workspace_to_monitor(server, +1)

    assert ws1.monitor is m2
    assert m2.active_workspace is ws1
    assert server.active_monitor is m2


def test_move_workspace_wraps():
    """move_active_workspace_to_monitor wraps from the first monitor to
    the last with direction -1."""
    m1 = make_monitor()
    m2 = make_monitor()
    ws1 = make_workspace(name="1", monitor=m1)
    ws2 = make_workspace(name="2", monitor=m2)
    m1.active_workspace = ws1
    m2.active_workspace = ws2
    server = make_server(
        workspaces=[ws1, ws2], monitors=[m1, m2], active_monitor=m1)

    wel.move_active_workspace_to_monitor(server, -1)

    assert server.active_monitor is m2


def test_move_workspace_single():
    """move_active_workspace_to_monitor is a no-op with one monitor."""
    monitor = make_monitor()
    ws = make_workspace(name="1", monitor=monitor)
    monitor.active_workspace = ws
    server = make_server(
        workspaces=[ws], monitors=[monitor], active_monitor=monitor)

    wel.move_active_workspace_to_monitor(server, +1)

    assert server.active_monitor is monitor


# --- workspaces: helpers --------------------------------------------------


def test_clients_in_filters():
    """clients_in returns only the clients assigned to the workspace."""
    ws1 = make_workspace(name="1")
    ws2 = make_workspace(name="2")
    a = make_client(workspace=ws1)
    b = make_client(workspace=ws2)
    c = make_client(workspace=ws1)
    server = make_server(clients=[a, b, c])

    assert wel.clients_in(server, ws1) == [a, c]


def test_clients_visible_active():
    """clients_visible returns clients on the monitor's active workspace."""
    monitor = make_monitor()
    active = make_workspace(name="1", monitor=monitor)
    inactive = make_workspace(name="2", monitor=monitor)
    monitor.active_workspace = active
    on_active = make_client(workspace=active)
    on_inactive = make_client(workspace=inactive)
    server = make_server(clients=[on_active, on_inactive])

    assert wel.clients_visible(server, monitor) == [on_active]


def test_clients_visible_empty():
    """clients_visible returns [] for a monitor with no active workspace."""
    server = make_server()
    monitor = make_monitor()

    assert wel.clients_visible(server, monitor) == []


def test_client_monitor_derives():
    """client_monitor reads the monitor through the client's workspace."""
    monitor = make_monitor()
    workspace = make_workspace(monitor=monitor)
    client = make_client(workspace=workspace)

    assert wel.client_monitor(client) is monitor


def test_client_monitor_orphaned():
    """A client with no workspace has no monitor."""
    client = make_client(workspace=None)

    assert wel.client_monitor(client) is None


def test_urgent_marks():
    """An activation request flags an unfocused window urgent."""
    monitor = make_monitor()
    monitor.active_workspace = make_workspace(monitor=monitor)
    client = make_client(workspace=monitor.active_workspace)
    server = make_server(
        ext_workspace=None, monitors=[monitor], clients=[client])
    event = MagicMock(name="event")
    event.surface = client.toplevel.base.surface
    server.ffi.cast.return_value = event # pylint: disable=no-member

    wel.client_request_activate(server, "DATA")

    assert client.urgent


def test_urgent_skips_focused():
    """Activating the already-focused window does not mark it urgent."""
    monitor = make_monitor()
    monitor.active_workspace = make_workspace(monitor=monitor)
    client = make_client(workspace=monitor.active_workspace)
    server = make_server(
        ext_workspace=None, monitors=[monitor], clients=[client])
    server.seat.keyboard_state.focused_surface = client.toplevel.base.surface # pylint: disable=no-member
    event = MagicMock(name="event")
    event.surface = client.toplevel.base.surface
    server.ffi.cast.return_value = event # pylint: disable=no-member

    wel.client_request_activate(server, "DATA")

    assert not client.urgent


def test_urgent_clears_on_focus():
    """Focusing an urgent window clears its urgent flag."""
    monitor = make_monitor()
    monitor.active_workspace = make_workspace(monitor=monitor)
    client = make_client(
        focus_order=1, urgent=True, workspace=monitor.active_workspace)
    server = make_server(
        ext_workspace=None, monitors=[monitor], active_monitor=monitor,
        clients=[client])

    wel.apply_focus(server)

    assert not client.urgent


# --- ext-workspace-v1 -----------------------------------------------------


def make_extws_server(**kwargs):
    """Server mock prepared for ext_workspace tests: ffi.cast returns the
    object's id so `_addr` is a usable hashable key, and resource factories
    yield a fresh mock per call so each group/handle gets a unique address."""
    server = make_server(**kwargs)
    server.ffi.cast = lambda _type, ptr: id(ptr)
    server.ffi.NULL = 0
    server.ffi.new_handle = lambda obj: obj
    server.ffi.new = lambda *_a, **_kw: MagicMock(name="new")
    server.lib.welpy_extws_create_group.side_effect = (
        lambda *_a: MagicMock(name="group_resource"))
    server.lib.welpy_extws_create_handle.side_effect = (
        lambda *_a: MagicMock(name="handle_resource"))
    server.lib.welpy_extws_resource_client.side_effect = (
        lambda r: MagicMock(name="client"))
    server.lib.welpy_extws_output_resource.side_effect = (
        lambda *_a: MagicMock(name="output_resource"))
    server.lib.welpy_extws_manager_create.return_value = MagicMock(
        name="global")
    return server


def make_extws(server, externs=None, on_activate=None, on_assign=None):
    """Build a ExtWorkspace against `server`, capturing the registered
    extern callbacks into `externs` (a dict keyed by function name)."""
    if externs is None:
        externs = {}

    def fake_def_extern():
        def decorator(f):
            externs[f.__name__] = f
            return f
        return decorator
    server.ffi.def_extern = fake_def_extern
    ext = ext_workspace.create(
        server,
        on_activate=on_activate or MagicMock(name="on_activate"),
        on_assign=on_assign or MagicMock(name="on_assign"),
    )
    server.ext_workspace = ext
    return ext, externs


def bind_extws_client(ext, externs):
    """Simulate a client binding the manager global. Returns the manager
    resource mock used."""
    manager = MagicMock(name="manager_resource")
    externs["_welpy_extws_bind"](ext.handle, manager)
    return manager


def extws_group(manager, monitor):
    """Find a client's group entry by the monitor it represents."""
    return next(g for g in manager.groups if g.monitor is monitor)


def extws_handle(manager, workspace):
    """Find a client's handle entry by the workspace it represents."""
    return next(w for w in manager.workspaces if w.workspace is workspace)


def test_extws_create():
    """create() creates the wl_global via the C helper, passing the ext
    handle so the bind callback can find it."""
    server = make_extws_server()
    ext, _ = make_extws(server)

    server.lib.welpy_extws_manager_create.assert_called_once_with(
        server.display, ext.handle)
    assert ext.global_ == server.lib.welpy_extws_manager_create.return_value


def test_extws_destroy():
    """destroy() releases the global and clears all per-client state."""
    server = make_extws_server()
    ext, externs = make_extws(server)
    bind_extws_client(ext, externs)

    ext_workspace.destroy(ext)

    server.lib.welpy_extws_manager_destroy.assert_called_once()
    assert ext.global_ is None
    assert not ext.managers


def test_extws_bind_initial():
    """Binding sends workspace_group + workspace events for the current
    layout, followed by exactly one `done`."""
    monitor = make_monitor()
    ws = make_workspace(name="1", monitor=monitor)
    monitor.active_workspace = ws
    server = make_extws_server(monitors=[monitor], workspaces=[ws])
    ext, externs = make_extws(server)

    manager = bind_extws_client(ext, externs)

    server.lib.welpy_extws_send_workspace_group.assert_called_once()
    server.lib.welpy_extws_send_workspace.assert_called_once()
    server.lib.welpy_extws_send_done.assert_called_once_with(manager)


def test_extws_layout_groups():
    """With N monitors, the manager receives N group resources and one
    handle per non-orphan workspace."""
    m1, m2 = make_monitor(), make_monitor()
    ws1 = make_workspace(name="1", monitor=m1)
    ws2 = make_workspace(name="2", monitor=m2)
    orphan = make_workspace(name="3")
    m1.active_workspace = ws1
    m2.active_workspace = ws2
    server = make_extws_server(
        monitors=[m1, m2], workspaces=[ws1, ws2, orphan])
    ext, externs = make_extws(server)

    bind_extws_client(ext, externs)

    manager = ext.managers[0]
    assert {id(g.monitor) for g in manager.groups} == {id(m1), id(m2)}
    assert {id(w.workspace) for w in manager.workspaces} == {id(ws1), id(ws2)}


def test_extws_orphan_hidden():
    """Orphan workspaces (monitor=None) are not exposed as handles."""
    monitor = make_monitor()
    ws = make_workspace(name="1", monitor=monitor)
    orphan = make_workspace(name="2")
    monitor.active_workspace = ws
    server = make_extws_server(monitors=[monitor], workspaces=[ws, orphan])
    ext, externs = make_extws(server)

    bind_extws_client(ext, externs)

    manager = ext.managers[0]
    assert all(w.workspace is not orphan for w in manager.workspaces)
    assert server.lib.welpy_extws_create_handle.call_count == 1


def test_extws_publish_done():
    """Each publish() call emits one `done` per bound client, no more."""
    monitor = make_monitor()
    ws = make_workspace(name="1", monitor=monitor)
    monitor.active_workspace = ws
    server = make_extws_server(monitors=[monitor], workspaces=[ws])
    ext, externs = make_extws(server)
    bind_extws_client(ext, externs)
    bind_extws_client(ext, externs)
    server.lib.welpy_extws_send_done.reset_mock()

    ext_workspace.publish(server)

    assert server.lib.welpy_extws_send_done.call_count == 2


def test_extws_activate():
    """Receiving an `activate` request invokes the on_activate callback
    with the workspace name."""
    monitor = make_monitor()
    ws = make_workspace(name="3", monitor=monitor)
    monitor.active_workspace = ws
    server = make_extws_server(monitors=[monitor], workspaces=[ws])
    on_activate = MagicMock(name="on_activate")
    ext, externs = make_extws(server, on_activate=on_activate)
    bind_extws_client(ext, externs)
    manager = ext.managers[0]
    handle_r = extws_handle(manager, ws).resource

    externs["_welpy_extws_handle_activate"]("CLIENT", handle_r)

    on_activate.assert_called_once_with("3")


def test_extws_assign():
    """Receiving an `assign` request invokes the on_assign callback with
    the workspace and the target monitor."""
    m1, m2 = make_monitor(), make_monitor()
    ws = make_workspace(name="1", monitor=m1)
    other = make_workspace(name="2", monitor=m2)
    m1.active_workspace = ws
    m2.active_workspace = other
    server = make_extws_server(monitors=[m1, m2], workspaces=[ws, other])
    on_assign = MagicMock(name="on_assign")
    ext, externs = make_extws(server, on_assign=on_assign)
    bind_extws_client(ext, externs)
    manager = ext.managers[0]
    handle_r = extws_handle(manager, ws).resource
    target_group_r = extws_group(manager, m2).resource

    externs["_welpy_extws_handle_assign"](
        "CLIENT", handle_r, target_group_r)

    on_assign.assert_called_once_with(ws, m2)


def test_extws_orphan_transition():
    """A workspace gaining a monitor causes a new handle; losing the
    monitor causes `removed` and frees the entry."""
    monitor = make_monitor()
    ws = make_workspace(name="1", monitor=monitor)
    other = make_workspace(name="2")
    monitor.active_workspace = ws
    server = make_extws_server(monitors=[monitor], workspaces=[ws, other])
    ext, externs = make_extws(server)
    bind_extws_client(ext, externs)
    manager = ext.managers[0]
    assert all(w.workspace is not other for w in manager.workspaces)

    other.monitor = monitor
    ext_workspace.publish(server)

    assert any(w.workspace is other for w in manager.workspaces)

    handle_r = extws_handle(manager, other).resource
    other.monitor = None
    ext_workspace.publish(server)

    server.lib.welpy_extws_send_handle_removed.assert_any_call(handle_r)
    assert all(w.workspace is not other for w in manager.workspaces)


def test_extws_monitor_unplug():
    """Dropping a monitor sends `removed` on its group resource and
    forgets the entry."""
    m1, m2 = make_monitor(), make_monitor()
    ws1 = make_workspace(name="1", monitor=m1)
    ws2 = make_workspace(name="2", monitor=m2)
    m1.active_workspace = ws1
    m2.active_workspace = ws2
    server = make_extws_server(monitors=[m1, m2], workspaces=[ws1, ws2])
    ext, externs = make_extws(server)
    bind_extws_client(ext, externs)
    manager = ext.managers[0]
    group_r = extws_group(manager, m2).resource

    server.monitors.remove(m2)
    ws2.monitor = None
    ext_workspace.publish(server)

    server.lib.welpy_extws_send_group_removed.assert_any_call(group_r)
    assert all(g.monitor is not m2 for g in manager.groups)


def test_extws_active_change():
    """Swapping the active workspace on a monitor emits a `state` event on
    each affected handle (one going active, one going inactive)."""
    monitor = make_monitor()
    ws1 = make_workspace(name="1", monitor=monitor)
    ws2 = make_workspace(name="2", monitor=monitor)
    monitor.active_workspace = ws1
    server = make_extws_server(monitors=[monitor], workspaces=[ws1, ws2])
    ext, externs = make_extws(server)
    bind_extws_client(ext, externs)
    manager = ext.managers[0]
    handle_r1 = extws_handle(manager, ws1).resource
    handle_r2 = extws_handle(manager, ws2).resource
    server.lib.welpy_extws_send_state.reset_mock()

    monitor.active_workspace = ws2
    ext_workspace.publish(server)

    sent = {c.args for c in server.lib.welpy_extws_send_state.mock_calls}
    assert (handle_r1, 0) in sent
    assert (handle_r2, 1) in sent


def test_extws_urgent_state():
    """A window flagged urgent publishes the urgent bit on its workspace
    handle, OR'd with the active bit."""
    monitor = make_monitor()
    ws1 = make_workspace(name="1", monitor=monitor)
    monitor.active_workspace = ws1
    client = make_client(workspace=ws1)
    server = make_extws_server(
        monitors=[monitor], workspaces=[ws1], clients=[client])
    ext, externs = make_extws(server)
    bind_extws_client(ext, externs)
    manager = ext.managers[0]
    handle_r1 = extws_handle(manager, ws1).resource
    server.lib.welpy_extws_send_state.reset_mock()

    client.urgent = True
    ext_workspace.publish(server)

    sent = {c.args for c in server.lib.welpy_extws_send_state.mock_calls}
    assert (handle_r1, 3) in sent


def test_setup_extws():
    """wel.setup() builds an ext_workspace ext on the server."""
    ffi = MagicMock(name="ffi")
    lib = MagicMock(name="lib")
    listen = MagicMock(side_effect=lambda *_a: MagicMock())
    build = (ffi, lib, listen, MagicMock(), MagicMock())
    with patch("wel.bindings.build", return_value=build), \
         patch("wel.build_keycode_map",
               return_value=make_keycode_map()), \
         patch("wel.ext_workspace.create") as create:
        server = wel.setup()

    create.assert_called_once_with(
        server, on_activate=ANY, on_assign=ANY)
    assert server.ext_workspace is create.return_value

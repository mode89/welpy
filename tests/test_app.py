"""Unit tests for welpy.app: compositor lifecycle + the keybinding table."""

from __future__ import annotations

import functools
import os
import signal
import sys
from textwrap import dedent
from unittest.mock import ANY, MagicMock, call, patch

import pytest

import welpy
from welpy import app, bindings, model
from tests.helpers import (
    make_server, make_bindings, make_monitor,
    make_keycode_map, trigger,
)


# --- setup ----------------------------------------------------------------


def test_setup_seat_caps():
    """Setup advertises pointer + keyboard on the seat so clients bind
    both wl_pointer and wl_keyboard from their first connect."""
    build = make_bindings()
    _, lib, *_ = build
    lib.WL_SEAT_CAPABILITY_POINTER = 1
    lib.WL_SEAT_CAPABILITY_KEYBOARD = 2
    with patch("welpy.app.bindings.build", return_value=build), \
         patch("welpy.input.build_keycode_map",
               return_value=make_keycode_map()):
        app.setup()

    lib.wlr_seat_set_capabilities.assert_called_once_with(
        lib.wlr_seat_create.return_value, 3)


def test_setup_keycode():
    """Setup populates server.keycode from the default keymap so bindings
    can reference keys by name."""
    build = make_bindings()
    ffi, lib, *_ = build
    with patch("welpy.app.bindings.build", return_value=build), \
         patch("welpy.input.build_keycode_map",
               return_value=make_keycode_map()) as bm:
        server = app.setup()

    bm.assert_called_once_with(lib, ffi, server.keyboard_group.keymap)
    assert server.keycode == make_keycode_map()


def test_modkey_super():
    """modkey is the Super key so bindings don't clash with app shortcuts."""
    server = MagicMock()
    server.lib.WLR_MODIFIER_LOGO = 0x40

    assert app.modkey(server) == 0x40


def test_setup_bindings():
    """Setup registers compositor bindings as (mods, code) tuples mapped
    to zero-arg callables."""
    build = make_bindings()
    _, lib, *_ = build
    lib.WLR_MODIFIER_LOGO = 0x40
    lib.WLR_MODIFIER_SHIFT = 0x1
    lib.WLR_MODIFIER_CTRL = 0x4
    lib.WLR_MODIFIER_ALT = 0x8
    lib.BTN_LEFT = 0x110
    lib.BTN_RIGHT = 0x111
    with patch("welpy.app.bindings.build", return_value=build), \
         patch("welpy.input.build_keycode_map",
               return_value=make_keycode_map()):
        server = app.setup()

    assert server.bindings
    for key, action in server.bindings.items():
        assert isinstance(key, tuple) and len(key) == 2
        assert all(isinstance(x, int) for x in key)
        assert callable(action)


def test_setup_clipboard_managers():
    """Setup creates the clipboard protocol globals so apps and clipboard
    tools can exchange selections."""
    build = make_bindings()
    _, lib, *_ = build
    with patch("welpy.app.bindings.build", return_value=build), \
         patch("welpy.input.build_keycode_map",
               return_value=make_keycode_map()):
        server = app.setup()

    lib.wlr_primary_selection_v1_device_manager_create.assert_called_once_with(
        server.display)
    lib.wlr_data_control_manager_v1_create.assert_called_once_with(
        server.display)
    lib.wlr_ext_data_control_manager_v1_create.assert_called_once_with(
        server.display, 1)


def test_setup_dmabuf_integrated():
    """On a real GPU, setup creates the wl_drm and linux-dmabuf globals and
    wires dmabuf into the scene for direct scan-out."""
    build = make_bindings()
    _, lib, *_ = build
    lib.wlr_renderer_get_drm_fd.return_value = 7
    dmabuf = lib.wlr_linux_dmabuf_v1_create_with_renderer.return_value
    with patch("welpy.app.bindings.build", return_value=build), \
         patch("welpy.input.build_keycode_map",
               return_value=make_keycode_map()):
        server = app.setup()

    lib.wlr_drm_create.assert_called_once_with(server.display, server.renderer)
    lib.wlr_linux_dmabuf_v1_create_with_renderer.assert_called_once_with(
        server.display, 5, server.renderer)
    lib.wlr_scene_set_linux_dmabuf_v1.assert_called_once_with(
        server.scene, dmabuf)


def test_setup_no_drm_fd():
    """Without a GPU device (nested/headless), setup skips wl_drm, dmabuf,
    and syncobj but still sets up shared memory."""
    build = make_bindings()
    _, lib, *_ = build
    lib.wlr_renderer_get_drm_fd.return_value = -1
    with patch("welpy.app.bindings.build", return_value=build), \
         patch("welpy.input.build_keycode_map",
               return_value=make_keycode_map()):
        server = app.setup()

    lib.wlr_renderer_init_wl_shm.assert_called_once_with(
        server.renderer, server.display)
    lib.wlr_drm_create.assert_not_called()
    lib.wlr_linux_dmabuf_v1_create_with_renderer.assert_not_called()
    lib.wlr_linux_drm_syncobj_manager_v1_create.assert_not_called()


def test_setup_syncobj_timeline():
    """Explicit-sync global is created only when renderer and backend both
    support timelines."""
    build = make_bindings()
    _, lib, *_ = build
    lib.wlr_renderer_get_drm_fd.return_value = 7
    lib.welpy_supports_timeline.return_value = True
    with patch("welpy.app.bindings.build", return_value=build), \
         patch("welpy.input.build_keycode_map",
               return_value=make_keycode_map()):
        server = app.setup()

    lib.wlr_linux_drm_syncobj_manager_v1_create.assert_called_once_with(
        server.display, 1, 7)


def test_setup_no_timeline():
    """With a GPU but no timeline support, dmabuf is set up but the
    explicit-sync global is skipped."""
    build = make_bindings()
    _, lib, *_ = build
    lib.wlr_renderer_get_drm_fd.return_value = 7
    lib.welpy_supports_timeline.return_value = False
    with patch("welpy.app.bindings.build", return_value=build), \
         patch("welpy.input.build_keycode_map",
               return_value=make_keycode_map()):
        app.setup()

    lib.wlr_linux_drm_syncobj_manager_v1_create.assert_not_called()


def test_setup_xwayland_failure():
    """If XWayland cannot be created, setup fails clearly before wiring
    listeners that would dereference the NULL server."""
    build = make_bindings()
    ffi, lib, *_ = build
    lib.wlr_xwayland_create.return_value = ffi.NULL

    with patch("welpy.app.bindings.build", return_value=build), \
         patch("welpy.input.build_keycode_map",
               return_value=make_keycode_map()), \
         pytest.raises(RuntimeError, match="XWayland"):
        app.setup()


def test_setup_renderer_lost_listener():
    """Setup subscribes to the renderer's lost signal so a GPU reset drives
    recovery."""
    build = make_bindings()
    _, lib, *_ = build
    with patch("welpy.app.bindings.build", return_value=build), \
         patch("welpy.input.build_keycode_map",
               return_value=make_keycode_map()), \
         patch("welpy.app.renderer_lost") as handler:
        server = app.setup()
        trigger(server, lib.welpy_renderer_lost_signal, "LOST")

    handler.assert_called_once_with(server)


def test_renderer_lost_recreates():
    """A GPU reset rebuilds renderer + allocator, re-points the compositor and
    every screen at them, and destroys the old pair."""
    monitor = make_monitor()
    server = make_server(monitors=[monitor])
    lib = server.lib
    old_renderer, old_allocator = server.renderer, server.allocator
    old_handle = server.renderer_lost
    new_renderer = lib.wlr_renderer_autocreate.return_value
    new_allocator = lib.wlr_allocator_autocreate.return_value

    app.renderer_lost(server)

    old_handle.remove.assert_called_once_with()
    lib.wlr_compositor_set_renderer.assert_called_once_with(
        server.compositor, new_renderer)
    lib.wlr_output_init_render.assert_called_once_with(
        monitor.output, new_allocator, new_renderer)
    lib.wlr_allocator_destroy.assert_called_once_with(old_allocator)
    lib.wlr_renderer_destroy.assert_called_once_with(old_renderer)
    assert server.renderer is new_renderer
    assert server.allocator is new_allocator
    assert server.renderer_lost is not old_handle


# --- teardown --------------------------------------------------------------


def test_teardown_order():
    """Shutdown calls wlroots destructors in the only valid order: clients
    and backend first, then the cursor and keyboard they reach into on
    unmap/screen-destroy, then the display."""
    server = make_server()

    app.teardown(server)

    names = [c[0] for c in server.lib.mock_calls]
    expected = [
        "wl_display_destroy_clients",
        "wlr_backend_destroy",
        "wlr_keyboard_group_destroy",
        "wlr_cursor_destroy",
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

    app.teardown(server)

    assert manager.mock_calls[:2] == [
        call.remove(),
        call.destroy_clients("DISPLAY"),
    ]
    assert not server.listeners


# --- client lifecycle ------------------------------------------------------


# --- popups ---------------------------------------------------------------


def test_setup_popup_listener():
    """Setup wires the xdg-shell new_popup signal to popup_new so each
    app-created popup hits our handler."""
    build = make_bindings()
    _, lib, *_ = build
    with patch("welpy.app.bindings.build", return_value=build), \
         patch("welpy.input.build_keycode_map",
               return_value=make_keycode_map()), \
         patch("welpy.windows.popup_new") as handler:
        built = app.setup()
        trigger(built, lib.welpy_xdg_shell_new_popup, "POPUP_DATA")
    handler.assert_called_once_with(built, "POPUP_DATA")


# --- decorations ----------------------------------------------------------


def test_setup_decoration_managers():
    """Setup creates both decoration managers and tells the legacy one to
    default to server-side so apps without xdg-decoration also get SSD."""
    build = make_bindings()
    _, lib, *_ = build
    with patch("welpy.app.bindings.build", return_value=build), \
         patch("welpy.input.build_keycode_map",
               return_value=make_keycode_map()):
        app.setup()

    lib.wlr_server_decoration_manager_set_default_mode.assert_called_once_with(
        lib.wlr_server_decoration_manager_create.return_value,
        lib.WLR_SERVER_DECORATION_MANAGER_MODE_SERVER)
    lib.wlr_xdg_decoration_manager_v1_create.assert_called_once_with(
        lib.wl_display_create.return_value)


def test_setup_decoration_listener():
    """Setup wires the xdg-decoration manager's new-toplevel-decoration
    signal to decoration_new so each app's request hits our handler."""
    build = make_bindings()
    _, lib, *_ = build
    with patch("welpy.app.bindings.build", return_value=build), \
         patch("welpy.input.build_keycode_map",
               return_value=make_keycode_map()), \
         patch("welpy.geometry.decoration_new") as handler:
        built = app.setup()
        trigger(built, lib.welpy_xdg_decoration_manager_new, "DECO_DATA")
    handler.assert_called_once_with(built, "DECO_DATA")


# --- signal handlers ------------------------------------------------------


def test_install_signals_signums():
    """The compositor installs handlers for exactly SIGINT, SIGTERM,
    SIGCHLD, and SIGPIPE -- the four signals it cares about."""
    server = make_server()

    app.install_signals(server)

    signums = {c.args[0] for c in server.add_signal.mock_calls}
    assert signums == {
        signal.SIGINT, signal.SIGTERM, signal.SIGCHLD, signal.SIGPIPE,
    }


def test_install_signals_sigterm():
    """SIGTERM (graceful kill) cleanly stops the display loop."""
    server = make_server()
    app.install_signals(server)
    by_signum = {c.args[0]: c.args[1] for c in server.add_signal.mock_calls}

    by_signum[signal.SIGTERM](signal.SIGTERM)

    server.lib.wl_display_terminate.assert_called_once_with("DISPLAY")


def test_install_signals_sigint():
    """SIGINT (Ctrl-C) cleanly stops the display loop."""
    server = make_server()
    app.install_signals(server)
    by_signum = {c.args[0]: c.args[1] for c in server.add_signal.mock_calls}

    by_signum[signal.SIGINT](signal.SIGINT)

    server.lib.wl_display_terminate.assert_called_once_with("DISPLAY")


def test_install_signals_drain():
    """On SIGCHLD we keep reaping until no more children are ready, so a
    burst of exits doesn't leave zombies behind."""
    server = make_server()
    app.install_signals(server)
    by_signum = {c.args[0]: c.args[1] for c in server.add_signal.mock_calls}

    # waitpid yields two children, then "no more ready" (pid == 0).
    with patch("welpy.app.os.waitpid",
               side_effect=[(123, 0), (124, 0), (0, 0)]) as wp:
        by_signum[signal.SIGCHLD](signal.SIGCHLD)

    assert wp.call_count == 3


def test_install_signals_orphan():
    """A spurious SIGCHLD with no children pending must not raise."""
    server = make_server()
    app.install_signals(server)
    by_signum = {c.args[0]: c.args[1] for c in server.add_signal.mock_calls}

    with patch("welpy.app.os.waitpid", side_effect=ChildProcessError):
        by_signum[signal.SIGCHLD](signal.SIGCHLD)  # must not raise


# --- layers / tiling -----------------------------------------------------


def test_setup_layers_created():
    """Setup creates a scene tree per Layer in declaration order so each
    renders above the previous."""
    build = make_bindings()
    ffi, lib, *_ = build
    lib.WL_SEAT_CAPABILITY_POINTER = 1
    lib.WL_SEAT_CAPABILITY_KEYBOARD = 2
    trees = [MagicMock(name=f"tree_{i}") for i in range(len(model.Layer))]
    lib.wlr_scene_tree_create.side_effect = list(trees)
    with patch("welpy.app.bindings.build", return_value=build), \
         patch("welpy.input.build_keycode_map",
               return_value=make_keycode_map()):
        server = app.setup()

    scene_root = ffi.addressof.return_value
    assert lib.wlr_scene_tree_create.call_args_list == [
        call(scene_root) for _ in model.Layer
    ]
    assert server.layers == dict(zip(model.Layer, trees))


# --- layer-shell ----------------------------------------------------------


def test_setup_layer_shell():
    """Setup creates the layer-shell global so apps can bind it."""
    build = make_bindings()
    _, lib, *_ = build
    lib.WL_SEAT_CAPABILITY_POINTER = 1
    lib.WL_SEAT_CAPABILITY_KEYBOARD = 2
    with patch("welpy.app.bindings.build", return_value=build), \
         patch("welpy.input.build_keycode_map",
               return_value=make_keycode_map()):
        server = app.setup()

    lib.wlr_layer_shell_v1_create.assert_called_once_with(
        lib.wl_display_create.return_value, 5)
    assert server.layer_shell is lib.wlr_layer_shell_v1_create.return_value


def test_setup_layer_listener():
    """new_surface on the layer-shell drives layer_surface_new so each
    shell-anchored window hits our handler."""
    build = make_bindings()
    _, lib, *_ = build
    lib.WL_SEAT_CAPABILITY_POINTER = 1
    lib.WL_SEAT_CAPABILITY_KEYBOARD = 2
    with patch("welpy.app.bindings.build", return_value=build), \
         patch("welpy.input.build_keycode_map",
               return_value=make_keycode_map()), \
         patch("welpy.layer_shell.on_create") as handler:
        built = app.setup()
        trigger(built, lib.welpy_layer_shell_new_surface, "LS_DATA")
    handler.assert_called_once_with(built, "LS_DATA")


def test_setup_session_lock():
    """Setup creates the session-lock global so screen lockers can bind it."""
    build = make_bindings()
    _, lib, *_ = build
    lib.WL_SEAT_CAPABILITY_POINTER = 1
    lib.WL_SEAT_CAPABILITY_KEYBOARD = 2
    with patch("welpy.app.bindings.build", return_value=build), \
         patch("welpy.input.build_keycode_map",
               return_value=make_keycode_map()):
        app.setup()

    lib.wlr_session_lock_manager_v1_create.assert_called_once_with(
        lib.wl_display_create.return_value)


def test_setup_lock_listener():
    """new_lock on the session-lock manager drives lock_new so each lock
    request hits our handler."""
    build = make_bindings()
    _, lib, *_ = build
    lib.WL_SEAT_CAPABILITY_POINTER = 1
    lib.WL_SEAT_CAPABILITY_KEYBOARD = 2
    with patch("welpy.app.bindings.build", return_value=build), \
         patch("welpy.input.build_keycode_map",
               return_value=make_keycode_map()), \
         patch("welpy.session_lock.on_lock") as handler:
        built = app.setup()
        trigger(built, lib.welpy_session_lock_mgr_new_lock, "LOCK_DATA")
    handler.assert_called_once_with(built, "LOCK_DATA")


def test_setup_pointer_constraints():
    """Setup creates the pointer-constraints and relative-pointer globals so
    games can lock the pointer and read raw motion."""
    build = make_bindings()
    _, lib, *_ = build
    lib.WL_SEAT_CAPABILITY_POINTER = 1
    lib.WL_SEAT_CAPABILITY_KEYBOARD = 2
    with patch("welpy.app.bindings.build", return_value=build), \
         patch("welpy.input.build_keycode_map",
               return_value=make_keycode_map()):
        app.setup()

    lib.wlr_pointer_constraints_v1_create.assert_called_once_with(
        lib.wl_display_create.return_value)
    lib.wlr_relative_pointer_manager_v1_create.assert_called_once_with(
        lib.wl_display_create.return_value)
    lib.wlr_virtual_keyboard_manager_v1_create.assert_called_once_with(
        lib.wl_display_create.return_value)


def test_setup_constraint_listener():
    """new_constraint on the pointer-constraints manager drives constraint_new
    so each lock/confine request hits our handler."""
    build = make_bindings()
    _, lib, *_ = build
    lib.WL_SEAT_CAPABILITY_POINTER = 1
    lib.WL_SEAT_CAPABILITY_KEYBOARD = 2
    with patch("welpy.app.bindings.build", return_value=build), \
         patch("welpy.input.build_keycode_map",
               return_value=make_keycode_map()), \
         patch("welpy.input.constraint_new") as handler:
        built = app.setup()
        trigger(built, lib.welpy_pointer_constraints_new_constraint, "C_DATA")
    handler.assert_called_once_with(built, "C_DATA")


def test_setup_virtual_keyboard_listener():
    """new_virtual_keyboard on the manager drives virtual_keyboard_new so each
    injected keyboard (wtype, on-screen keyboards) hits our handler."""
    build = make_bindings()
    _, lib, *_ = build
    lib.WL_SEAT_CAPABILITY_POINTER = 1
    lib.WL_SEAT_CAPABILITY_KEYBOARD = 2
    with patch("welpy.app.bindings.build", return_value=build), \
         patch("welpy.input.build_keycode_map",
               return_value=make_keycode_map()), \
         patch("welpy.input.virtual_keyboard_new") as handler:
        built = app.setup()
        trigger(built, lib.welpy_virtual_keyboard_mgr_new, "VKB_DATA")
    handler.assert_called_once_with(built, "VKB_DATA")


def test_setup_set_cursor_listener():
    """request_set_cursor on the seat drives seat_set_cursor so apps can set
    their own cursor image or hide it."""
    build = make_bindings()
    _, lib, *_ = build
    lib.WL_SEAT_CAPABILITY_POINTER = 1
    lib.WL_SEAT_CAPABILITY_KEYBOARD = 2
    with patch("welpy.app.bindings.build", return_value=build), \
         patch("welpy.input.build_keycode_map",
               return_value=make_keycode_map()), \
         patch("welpy.input.seat_set_cursor") as handler:
        built = app.setup()
        trigger(built, lib.welpy_seat_request_set_cursor, "SC_DATA")
    handler.assert_called_once_with(built, "SC_DATA")


def test_setup_output_power():
    """Setup creates the output-power global so DPMS clients can blank
    screens."""
    build = make_bindings()
    _, lib, *_ = build
    lib.WL_SEAT_CAPABILITY_POINTER = 1
    lib.WL_SEAT_CAPABILITY_KEYBOARD = 2
    with patch("welpy.app.bindings.build", return_value=build), \
         patch("welpy.input.build_keycode_map",
               return_value=make_keycode_map()):
        app.setup()

    lib.wlr_output_power_manager_v1_create.assert_called_once_with(
        lib.wl_display_create.return_value)


def test_setup_output_power_listener():
    """set_mode on the output-power manager drives output_power_set_mode so
    each DPMS request hits our handler."""
    build = make_bindings()
    _, lib, *_ = build
    lib.WL_SEAT_CAPABILITY_POINTER = 1
    lib.WL_SEAT_CAPABILITY_KEYBOARD = 2
    with patch("welpy.app.bindings.build", return_value=build), \
         patch("welpy.input.build_keycode_map",
               return_value=make_keycode_map()), \
         patch("welpy.output.on_power_mode") as handler:
        built = app.setup()
        trigger(built, lib.welpy_output_power_mgr_set_mode, "PWR_DATA")
    handler.assert_called_once_with(built, "PWR_DATA")


def test_setup_layout_change_reflows():
    """A change in the screen layout (monitor added/removed/repositioned)
    drives update_monitors so windows re-flow onto the new geometry."""
    build = make_bindings()
    _, lib, *_ = build
    lib.WL_SEAT_CAPABILITY_POINTER = 1
    lib.WL_SEAT_CAPABILITY_KEYBOARD = 2
    with patch("welpy.app.bindings.build", return_value=build), \
         patch("welpy.input.build_keycode_map",
               return_value=make_keycode_map()), \
         patch("welpy.reflow.outputs") as upd:
        server = app.setup()
        trigger(server, lib.welpy_output_layout_change, "LAYOUT_DATA")

    upd.assert_called_once_with(server)


# --- override / load_config ------------------------------------------------


def test_override_chains_original(monkeypatch):
    """The previous function is passed in as the first argument."""
    monkeypatch.setattr(app, "modkey", app.modkey)

    @welpy.override(app.modkey)
    def modkey(orig, server):
        return orig(server) | 0xFF

    server = MagicMock()
    server.lib.WLR_MODIFIER_LOGO = 0x40
    assert app.modkey(server) == 0x40 | 0xFF


def test_override_renamed_function(monkeypatch):
    """@welpy.override(target) installs at the target's home, even when the
    new function has a different local name."""
    monkeypatch.setattr(app, "modkey", app.modkey)

    @welpy.override(app.modkey)
    def renamed(_orig, _server):
        return 0x123

    assert app.modkey(MagicMock()) == 0x123


def test_override_chain_after_renamed_function(monkeypatch):
    """A later override still targets the hook, not the prior function name."""
    monkeypatch.setattr(app, "modkey", app.modkey)
    monkeypatch.setattr(app, "renamed", None, raising=False)

    @welpy.override(app.modkey)
    def renamed(orig, server):
        return orig(server) + 1

    @welpy.override(app.modkey)
    def newer(orig, server):
        return orig(server) * 10

    server = MagicMock()
    server.lib.WLR_MODIFIER_LOGO = 5
    assert app.modkey(server) == 60


def test_override_cross_module(monkeypatch):
    """@welpy.override reaches outside welpy.app: targeting bindings.build
    installs the replacement in bindings, not in welpy.app."""
    monkeypatch.setattr(bindings, "build", bindings.build)

    @welpy.override(bindings.build)
    def build(_orig):
        return "stub"

    assert bindings.build() == "stub"
    assert not hasattr(app, "build")


def test_override_chain_composes(monkeypatch):
    """Form-1 then form-2 chains correctly: the second wraps the first,
    which wraps the built-in. Exercises the __module__ rewrite that lets
    form-2 find the previous wrapper at welpy.app.<name>."""
    monkeypatch.setattr(app, "modkey", app.modkey)

    @welpy.override(app.modkey)
    def modkey(orig, server):
        return orig(server) + 1

    @welpy.override(app.modkey)
    def newer(orig, server):
        return orig(server) * 10

    server = MagicMock()
    server.lib.WLR_MODIFIER_LOGO = 5
    # built-in=5; inner adds 1 -> 6; outer multiplies by 10 -> 60.
    assert app.modkey(server) == 60


def test_override_autostart(monkeypatch):
    """@welpy.override on `autostart` lets config swap the launched programs."""
    monkeypatch.setattr(app, "autostart", app.autostart)
    calls = []

    @welpy.override(app.autostart)
    def autostart(_orig, server):
        calls.append(server)

    app.autostart("SERVER")
    assert calls == ["SERVER"]


def test_override_class_method():
    """@welpy.override on a method (dotted __qualname__) patches the class
    attribute, with the previous method curried before self; a second
    override chains onto the first, routing to the same class attribute."""
    builder = bindings.core.Builder
    original = builder.compile
    try:
        @welpy.override(builder.compile)
        def stub(_orig, _self, name):
            return f"<{name}>"

        assert builder.compile(object(), "x") == "<x>"

        @welpy.override(builder.compile)
        def louder(orig, self, name):
            return orig(self, name).upper()

        assert builder.compile(object(), "x") == "<X>"
    finally:
        builder.compile = original


def test_override_non_callable():
    """Non-callable argument: explicit TypeError, not a confusing
    AttributeError on `__module__`."""
    with pytest.raises(TypeError, match="expects a function"):
        welpy.override(None)


def test_override_missing_name():
    """Callable missing __qualname__ (e.g. functools.partial): default
    AttributeError names the missing attribute clearly."""
    partial = functools.partial(lambda x: x)
    with pytest.raises(AttributeError, match="__qualname__"):
        welpy.override(partial)


def test_config_missing_noop(tmp_path):
    """Absent config file is a silent no-op."""
    app.load_config(tmp_path / "nonexistent.py")


def test_config_runs_file(tmp_path, monkeypatch):
    """The loader executes the file's top-level code."""
    # pylint: disable=protected-access,no-member
    monkeypatch.setattr(app, "_test_marker", None, raising=False)
    config = tmp_path / "config.py"
    config.write_text(dedent("""\
        import welpy.app
        welpy.app._test_marker = 'ran'
    """))
    app.load_config(config)
    assert app._test_marker == "ran"


def test_config_sibling_importable(tmp_path, monkeypatch):
    """Sibling files are importable as top-level modules after load, and
    overrides defined in them land on the running welpy.app module."""
    monkeypatch.setattr(app, "modkey", app.modkey)
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(sys, "modules", dict(sys.modules))
    (tmp_path / "sibling_ext.py").write_text(dedent("""\
        import welpy.app

        @welpy.override(welpy.app.modkey)
        def modkey(_orig, _server):
            return 0xDEAD
    """))
    (tmp_path / "config.py").write_text(dedent("""\
        import sibling_ext  # noqa: F401
    """))
    app.load_config(tmp_path / "config.py")
    assert app.modkey(MagicMock()) == 0xDEAD


# --- workspaces: setup -----------------------------------------------------


def test_setup_workspaces_orphaned():
    """Setup creates 10 orphaned workspaces named "1".."9", "10"."""
    build = make_bindings()
    with patch("welpy.app.bindings.build", return_value=build), \
         patch("welpy.input.build_keycode_map",
               return_value=make_keycode_map()):
        server = app.setup()
    assert [w.name for w in server.workspaces] == [
        "1", "2", "3", "4", "5", "6", "7", "8", "9", "10"]
    assert all(w.monitor is None for w in server.workspaces)
    assert all(w.fullscreen is None for w in server.workspaces)
    assert server.active_monitor is None


# --- setup: ext-workspace -------------------------------------------------


def test_setup_ext_workspace():
    """app.setup() builds an ext_workspace ext on the server."""
    build = make_bindings()
    with patch("welpy.app.bindings.build", return_value=build), \
         patch("welpy.input.build_keycode_map",
               return_value=make_keycode_map()), \
         patch("welpy.app.ext_workspace.create") as create:
        server = app.setup()

    create.assert_called_once_with(
        server, on_activate=ANY, on_assign=ANY)
    assert server.ext_workspace is create.return_value


def test_main_display_before_autostart(monkeypatch):
    """main() exports DISPLAY before running autostart, so apps launched at
    startup reach our Xwayland and not the parent compositor's X server."""
    server = make_server()
    server.lib.wlr_backend_start.return_value = True
    # ffi.string().decode() is called for WAYLAND_DISPLAY then DISPLAY.
    server.ffi.string.return_value.decode.side_effect = ["wayland-9", ":4"]
    monkeypatch.setenv("DISPLAY", "")

    seen = {}
    def record(_server):
        seen["display"] = os.environ["DISPLAY"]
    with patch("welpy.app.setup", return_value=server), \
         patch("welpy.app.load_config"), \
         patch("welpy.app.install_signals"), \
         patch("welpy.app.teardown"), \
         patch("welpy.app.autostart", side_effect=record):
        app.main()

    assert seen["display"] == ":4"

"""Unit tests for wel.py."""

from __future__ import annotations

import functools
import os
import signal
import sys
from textwrap import dedent
from unittest.mock import ANY, MagicMock, call, patch

import cffi
import pytest

import welpy
from welpy import (  # pylint: disable=redefined-builtin
    app as wel, bindings, ext_workspace, focus, geometry, input, layout,
    windows)
from tests.helpers import (
    make_server, make_bindings, make_client, make_x11_client, make_unmanaged,
    make_monitor, make_workspace, flat_tree, make_cursor,
    make_keycode_map, make_session_lock, trigger,
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
        wel.setup()

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
        server = wel.setup()

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
        server = wel.setup()

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
        server = wel.setup()

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
        server = wel.setup()

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
        server = wel.setup()

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
        wel.setup()

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
        wel.setup()


def test_setup_renderer_lost_listener():
    """Setup subscribes to the renderer's lost signal so a GPU reset drives
    recovery."""
    build = make_bindings()
    _, lib, *_ = build
    with patch("welpy.app.bindings.build", return_value=build), \
         patch("welpy.input.build_keycode_map",
               return_value=make_keycode_map()), \
         patch("welpy.app.renderer_lost") as handler:
        server = wel.setup()
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

    wel.renderer_lost(server)

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

    wel.teardown(server)

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

    wel.teardown(server)

    assert manager.mock_calls[:2] == [
        call.remove(),
        call.destroy_clients("DISPLAY"),
    ]
    assert not server.listeners


# --- wl_list iterator ------------------------------------------------------


def test_wl_list_for_each_yields_containers():
    """The wl_list iterator walks an intrusive list and recovers each owning
    struct from its embedded link, in order."""
    ffi = cffi.FFI()
    ffi.cdef(
        "struct wl_list { struct wl_list *prev; struct wl_list *next; };"
        "struct node { int v; struct wl_list link; };")
    head = ffi.new("struct wl_list *")
    nodes = [ffi.new("struct node *") for _ in range(3)]
    for i, node in enumerate(nodes):
        node.v = i + 1
    links = [ffi.addressof(n[0], "link") for n in nodes]
    chain = [head, *links, head]
    for i in range(1, len(chain) - 1):
        chain[i].prev = chain[i - 1]
        chain[i].next = chain[i + 1]
    head.next, head.prev = links[0], links[-1]

    got = [
        c.v for c in bindings.wl_list_for_each(
            ffi, head, "struct node", "link")]

    assert got == [1, 2, 3]


def test_wl_list_for_each_empty():
    """An empty list -- its sentinel points back at itself -- yields nothing."""
    ffi = cffi.FFI()
    ffi.cdef("struct wl_list { struct wl_list *prev; struct wl_list *next; };")
    head = ffi.new("struct wl_list *")
    head.next, head.prev = head, head

    assert not list(
        bindings.wl_list_for_each(ffi, head, "struct wl_list", "prev"))


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
        built = wel.setup()
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
        wel.setup()

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
        built = wel.setup()
        trigger(built, lib.welpy_xdg_decoration_manager_new, "DECO_DATA")
    handler.assert_called_once_with(built, "DECO_DATA")


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
    with patch("welpy.app.os.waitpid",
               side_effect=[(123, 0), (124, 0), (0, 0)]) as wp:
        by_signum[signal.SIGCHLD](signal.SIGCHLD)

    assert wp.call_count == 3


def test_install_signals_orphan():
    """A spurious SIGCHLD with no children pending must not raise."""
    server = make_server()
    wel.install_signals(server)
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
    trees = [MagicMock(name=f"tree_{i}") for i in range(len(wel.Layer))]
    lib.wlr_scene_tree_create.side_effect = list(trees)
    with patch("welpy.app.bindings.build", return_value=build), \
         patch("welpy.input.build_keycode_map",
               return_value=make_keycode_map()):
        server = wel.setup()

    scene_root = ffi.addressof.return_value
    assert lib.wlr_scene_tree_create.call_args_list == [
        call(scene_root) for _ in wel.Layer
    ]
    assert server.layers == dict(zip(wel.Layer, trees))


def test_focus_direction_moves():
    """Directional focus shifts to the structurally adjacent tiled window: from
    the left column of a three-column row, RIGHT lands on the middle one."""
    # pylint: disable=duplicate-code
    m = make_monitor(window_area=wel.Rect(0, 0, 900, 600))
    m.active_workspace = make_workspace(monitor=m)
    a = make_client(workspace=m.active_workspace)
    b = make_client(workspace=m.active_workspace)
    c = make_client(workspace=m.active_workspace)
    m.active_workspace.root = flat_tree(a, b, c)
    server = make_server(monitors=[m], active_monitor=m, clients=[a, b, c])
    focus.focus_client(server, a)

    with patch("welpy.focus.apply_focus"), \
         patch("welpy.focus.focus_client") as focus_client:
        wel.focus_direction(server, layout.Direction.RIGHT)

    focus_client.assert_called_once_with(server, b)


def test_focus_direction_edge():
    """Directional focus is a no-op at an edge: nothing lies right of the
    rightmost window."""
    m = make_monitor(window_area=wel.Rect(0, 0, 900, 600))
    m.active_workspace = make_workspace(monitor=m)
    a = make_client(workspace=m.active_workspace)
    b = make_client(workspace=m.active_workspace)
    m.active_workspace.root = flat_tree(a, b)
    server = make_server(monitors=[m], active_monitor=m, clients=[a, b])
    focus.focus_client(server, b)

    with patch("welpy.focus.apply_focus"), \
         patch("welpy.focus.focus_client") as focus_client:
        wel.focus_direction(server, layout.Direction.RIGHT)

    focus_client.assert_not_called()


def test_focus_direction_fullscreen():
    """Directional focus is inert while a fullscreen window owns the screen."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    a = make_client(workspace=m.active_workspace)
    b = make_client(workspace=m.active_workspace)
    m.active_workspace.root = flat_tree(a, b)
    m.active_workspace.fullscreen = a
    server = make_server(monitors=[m], active_monitor=m, clients=[a, b])
    focus.focus_client(server, a)

    with patch("welpy.focus.apply_focus"), \
         patch("welpy.focus.focus_client") as focus_client:
        wel.focus_direction(server, layout.Direction.RIGHT)

    focus_client.assert_not_called()


def test_focus_direction_floating():
    """Directional focus is a no-op when the focused window is floating, since
    floats aren't tiled leaves."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    a = make_client(workspace=m.active_workspace)
    b = make_client(
        workspace=m.active_workspace,
        floating_geom=wel.Rect(0, 0, 100, 100),
    )
    m.active_workspace.root = flat_tree(a)
    server = make_server(monitors=[m], active_monitor=m, clients=[a, b])
    focus.focus_client(server, b)

    with patch("welpy.focus.apply_focus"), \
         patch("welpy.focus.focus_client") as focus_client:
        wel.focus_direction(server, layout.Direction.LEFT)

    focus_client.assert_not_called()


def test_focus_direction_group_mru():
    """Focusing into a neighboring group lands on its most-recently-focused
    window, regardless of where that window sits in the group."""
    m = make_monitor(window_area=wel.Rect(0, 0, 900, 600))
    m.active_workspace = make_workspace(monitor=m)
    a = make_client(focus_order=1, workspace=m.active_workspace)
    b = make_client(focus_order=2, workspace=m.active_workspace)
    c = make_client(focus_order=3, workspace=m.active_workspace)
    inner = layout.Container(layout.ContainerLayout.VERTICAL, [b, c])
    m.active_workspace.root = layout.Container(
        layout.ContainerLayout.HORIZONTAL, [a, inner])
    server = make_server(monitors=[m], active_monitor=m, clients=[a, b, c])
    focus.focus_client(server, a)

    with patch("welpy.focus.apply_focus"), \
         patch("welpy.focus.focus_client") as focus_client:
        wel.focus_direction(server, layout.Direction.RIGHT)

    focus_client.assert_called_once_with(server, c)


def test_move_direction_moves():
    """mod+shift relocates the focused window one slot that way: from the left
    of a three-column row, RIGHT reorders it past its neighbor."""
    # pylint: disable=duplicate-code
    m = make_monitor(window_area=wel.Rect(0, 0, 900, 600))
    m.active_workspace = make_workspace(monitor=m)
    a = make_client(workspace=m.active_workspace)
    b = make_client(workspace=m.active_workspace)
    c = make_client(workspace=m.active_workspace)
    m.active_workspace.root = flat_tree(a, b, c)
    server = make_server(monitors=[m], active_monitor=m, clients=[a, b, c])
    focus.focus_client(server, a)

    with patch("welpy.geometry.apply_geometry"), \
         patch("welpy.focus.apply_focus"):
        wel.move_direction(server, layout.Direction.RIGHT)

    assert m.active_workspace.root.children == [b, a, c]


def test_move_direction_edge():
    """Moving toward an edge is a no-op: nothing lies right of the rightmost
    window, so the tree is unchanged."""
    m = make_monitor(window_area=wel.Rect(0, 0, 900, 600))
    m.active_workspace = make_workspace(monitor=m)
    a = make_client(workspace=m.active_workspace)
    b = make_client(workspace=m.active_workspace)
    m.active_workspace.root = flat_tree(a, b)
    server = make_server(monitors=[m], active_monitor=m, clients=[a, b])
    focus.focus_client(server, b)

    with patch("welpy.geometry.apply_geometry"), \
         patch("welpy.focus.apply_focus"):
        wel.move_direction(server, layout.Direction.RIGHT)

    assert m.active_workspace.root.children == [a, b]


def test_move_direction_fullscreen():
    """Moving is inert while a fullscreen window owns the screen."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    a = make_client(workspace=m.active_workspace)
    b = make_client(workspace=m.active_workspace)
    m.active_workspace.root = flat_tree(a, b)
    m.active_workspace.fullscreen = a
    server = make_server(monitors=[m], active_monitor=m, clients=[a, b])
    focus.focus_client(server, a)

    with patch("welpy.geometry.apply_geometry"), \
         patch("welpy.focus.apply_focus"):
        wel.move_direction(server, layout.Direction.RIGHT)

    assert m.active_workspace.root.children == [a, b]


def test_move_direction_floating():
    """Moving is a no-op when the focused window is floating, since floats
    aren't tiled leaves."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    a = make_client(workspace=m.active_workspace)
    b = make_client(
        workspace=m.active_workspace,
        floating_geom=wel.Rect(0, 0, 100, 100),
    )
    m.active_workspace.root = flat_tree(a)
    server = make_server(monitors=[m], active_monitor=m, clients=[a, b])
    focus.focus_client(server, b)

    with patch("welpy.geometry.apply_geometry") as apply_geom, \
         patch("welpy.focus.apply_focus"):
        wel.move_direction(server, layout.Direction.LEFT)

    apply_geom.assert_not_called()
    assert m.active_workspace.root.children == [a]


def test_move_direction_vertical():
    """mod+shift+j relocates the focused window down a column, exercising the
    vertical move axis."""
    m = make_monitor(window_area=wel.Rect(0, 0, 600, 900))
    m.active_workspace = make_workspace(monitor=m)
    a = make_client(workspace=m.active_workspace)
    b = make_client(workspace=m.active_workspace)
    c = make_client(workspace=m.active_workspace)
    m.active_workspace.root = layout.Container(
        layout.ContainerLayout.VERTICAL, [a, b, c])
    server = make_server(monitors=[m], active_monitor=m, clients=[a, b, c])
    focus.focus_client(server, a)

    with patch("welpy.geometry.apply_geometry"), \
         patch("welpy.focus.apply_focus"):
        wel.move_direction(server, layout.Direction.DOWN)

    assert m.active_workspace.root.children == [b, a, c]


def test_group_window_wraps():
    """mod+v wraps a window that has a sibling in its own group, split along
    the window's long side (here VERTICAL)."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    a = make_client(workspace=m.active_workspace, inner_size=(400, 600))
    b = make_client(workspace=m.active_workspace)
    m.active_workspace.root = flat_tree(a, b)
    server = make_server(monitors=[m], active_monitor=m, clients=[a, b])
    focus.focus_client(server, a)

    with patch("welpy.geometry.apply_geometry"), \
         patch("welpy.focus.apply_focus"):
        wel.group_window(server)

    root = m.active_workspace.root
    assert isinstance(root.children[0], layout.Container)
    assert root.children[0].layout == layout.ContainerLayout.VERTICAL
    assert root.children[0].children == [a]
    assert root.children[1] is b


def test_group_window_alone():
    """mod+v is a no-op on a window with no siblings -- there's nothing to
    split it off from, so the tree is unchanged."""
    m = make_monitor(window_area=wel.Rect(0, 0, 800, 600))
    m.active_workspace = make_workspace(monitor=m)
    a = make_client(workspace=m.active_workspace)
    m.active_workspace.root = flat_tree(a)
    server = make_server(monitors=[m], active_monitor=m, clients=[a])
    focus.focus_client(server, a)

    with patch("welpy.geometry.apply_geometry"), \
         patch("welpy.focus.apply_focus"):
        wel.group_window(server)

    assert m.active_workspace.root.children == [a]


def test_group_window_nested():
    """mod+v wraps a window nested inside a sub-group too, as long as it has a
    sibling there; the rest of the tree is untouched."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    a = make_client(workspace=m.active_workspace, inner_size=(400, 300))
    b = make_client(workspace=m.active_workspace)
    c = make_client(workspace=m.active_workspace)
    column = layout.Container(layout.ContainerLayout.VERTICAL, [a, b])
    m.active_workspace.root = layout.Container(
        layout.ContainerLayout.HORIZONTAL, [column, c])
    server = make_server(monitors=[m], active_monitor=m, clients=[a, b, c])
    focus.focus_client(server, a)

    with patch("welpy.geometry.apply_geometry"), \
         patch("welpy.focus.apply_focus"):
        wel.group_window(server)

    assert isinstance(column.children[0], layout.Container)
    assert column.children[0].layout == layout.ContainerLayout.HORIZONTAL
    assert column.children[0].children == [a]
    assert column.children[1] is b
    assert m.active_workspace.root.children[1] is c


def test_cycle_layout_flips():
    """mod+e flips the focused window's container between a row and a column."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    a = make_client(workspace=m.active_workspace)
    b = make_client(workspace=m.active_workspace)
    m.active_workspace.root = flat_tree(a, b)
    server = make_server(monitors=[m], active_monitor=m, clients=[a, b])
    focus.focus_client(server, a)

    with patch("welpy.geometry.apply_geometry"), \
         patch("welpy.focus.apply_focus"):
        wel.cycle_layout(server)

    assert m.active_workspace.root.layout == layout.ContainerLayout.VERTICAL


def test_client_unmap_unselected():
    """Unmapping a window not on the active monitor leaves focus alone
    when there's nothing to refocus on the active monitor."""
    m1 = make_monitor()
    m1.active_workspace = make_workspace(monitor=m1)
    m2 = make_monitor()
    m2.active_workspace = make_workspace(monitor=m2)
    a = make_client(focus_order=1, workspace=m2.active_workspace)
    server = make_server(monitors=[m1], active_monitor=m1, clients=[a])

    with patch("welpy.focus.focus_client") as focus_client:
        windows.client_unmap(server, a, "DATA")

    focus_client.assert_not_called()


def test_borders_present():
    """A new window gets four edge rects under its wrapper tree so it has
    something to color on focus."""
    server = make_server()
    wrapper = MagicMock(name="wrapper")
    server.lib.wlr_scene_tree_create.return_value = wrapper
    client = make_client(toplevel=MagicMock(), scene_tree=None)

    with patch("welpy.focus.focus_client"):
        windows.client_map(server, client, None)

    assert len(client.borders) == 4
    parents = [
        c.args[0] for c in server.lib.wlr_scene_rect_create.call_args_list
    ]
    assert parents == [wrapper] * 4


# --- apply_geometry ------------------------------------------------------


def test_layout_walk_row():
    """A HORIZONTAL container splits its area into equal columns that sum
    exactly to the width."""
    a, b, c = object(), object(), object()
    root = layout.Container(layout.ContainerLayout.HORIZONTAL, [a, b, c])
    placed = list(layout.walk(root, layout.Rect(0, 0, 900, 600)))

    assert placed == [
        (a, layout.Rect(0, 0, 300, 600)),
        (b, layout.Rect(300, 0, 300, 600)),
        (c, layout.Rect(600, 0, 300, 600)),
    ]


def test_layout_walk_nested():
    """A nested container subdivides only its own slice of the parent area."""
    a, b, c = object(), object(), object()
    inner = layout.Container(layout.ContainerLayout.VERTICAL, [b, c])
    root = layout.Container(layout.ContainerLayout.HORIZONTAL, [a, inner])
    placed = dict((id(k), v) for k, v in layout.walk(
        root, layout.Rect(0, 0, 800, 600)))

    assert placed[id(a)] == layout.Rect(0, 0, 400, 600)
    assert placed[id(b)] == layout.Rect(400, 0, 400, 300)
    assert placed[id(c)] == layout.Rect(400, 300, 400, 300)


def test_layout_insert_after():
    """insert_sibling places the new leaf right after its target."""
    a, b, c = object(), object(), object()
    root = layout.Container(layout.ContainerLayout.HORIZONTAL, [a, b])
    layout.insert_sibling(root, a, c)

    assert root.children == [a, c, b]


def test_layout_insert_append():
    """insert_sibling appends to the root when the target is None or absent."""
    a, b = object(), object()
    root = layout.Container(layout.ContainerLayout.HORIZONTAL, [a])
    layout.insert_sibling(root, None, b)

    assert root.children == [a, b]


def test_layout_remove_promotes():
    """Removing a window that leaves its container with one sibling promotes
    that sibling, dropping the now-redundant container."""
    a, b, c = object(), object(), object()
    inner = layout.Container(layout.ContainerLayout.VERTICAL, [b, c])
    root = layout.Container(layout.ContainerLayout.HORIZONTAL, [a, inner])
    layout.remove(root, b)

    assert root.children == [a, c]


def test_layout_remove_empty():
    """Removing the only window of a group drops the emptied container."""
    a, b = object(), object()
    root = layout.Container(layout.ContainerLayout.HORIZONTAL, [a, b])
    layout.wrap(root, a, layout.ContainerLayout.VERTICAL)
    layout.remove(root, a)

    assert root.children == [b]


def test_layout_remove_unrelated():
    """Collapse touches only the removed window's ancestors, so a one-window
    group elsewhere survives an unrelated removal."""
    a, b, c = object(), object(), object()
    group = layout.Container(layout.ContainerLayout.VERTICAL, [a])
    root = layout.Container(layout.ContainerLayout.HORIZONTAL, [group, b, c])
    layout.remove(root, c)

    assert root.children[0] is group and group.children == [a]


def test_layout_wrap_unwrap():
    """wrap nests a leaf one level deeper; unwrap splices the group back."""
    a, b = object(), object()
    root = layout.Container(layout.ContainerLayout.HORIZONTAL, [a, b])
    layout.wrap(root, a, layout.ContainerLayout.VERTICAL)
    group = root.children[0]

    assert isinstance(group, layout.Container) and group.children == [a]

    layout.unwrap(root, group)
    assert root.children == [a, b]


def test_layout_cycle_flips():
    """cycle_layout toggles a container between HORIZONTAL and VERTICAL."""
    root = layout.Container(layout.ContainerLayout.HORIZONTAL, [object()])
    layout.cycle_layout(root)
    assert root.layout == layout.ContainerLayout.VERTICAL
    layout.cycle_layout(root)
    assert root.layout == layout.ContainerLayout.HORIZONTAL


def test_layout_adjacent_leaves_sibling():
    """In a flat row the adjacent set is the single neighboring window."""
    a, b, c = object(), object(), object()
    root = layout.Container(layout.ContainerLayout.HORIZONTAL, [a, b, c])

    assert layout.adjacent_leaves(root, a, layout.Direction.RIGHT) == [b]
    assert layout.adjacent_leaves(root, c, layout.Direction.LEFT) == [b]


def test_layout_adjacent_leaves_edge():
    """Nothing lies past an edge or along an axis no ancestor splits on, so the
    adjacent set is empty."""
    a, b = object(), object()
    root = layout.Container(layout.ContainerLayout.HORIZONTAL, [a, b])

    assert not layout.adjacent_leaves(root, b, layout.Direction.RIGHT)
    assert not layout.adjacent_leaves(root, a, layout.Direction.UP)


def test_layout_adjacent_leaves_group():
    """A neighboring container contributes all its windows as candidates."""
    a, b, c = object(), object(), object()
    inner = layout.Container(layout.ContainerLayout.VERTICAL, [b, c])
    root = layout.Container(
        layout.ContainerLayout.HORIZONTAL, [a, inner])

    assert layout.adjacent_leaves(root, a, layout.Direction.RIGHT) == [b, c]


def test_layout_successor_siblings():
    """In a flat row the successor is the highest-ranked other window."""
    a, b, c = object(), object(), object()
    root = layout.Container(layout.ContainerLayout.HORIZONTAL, [a, b, c])
    rank = {a: 1, b: 3, c: 2}

    assert layout.successor(root, a, rank.get) is b


def test_layout_successor_inner():
    """The innermost enclosing group wins: a grouped window's successor is a
    groupmate, even when a higher-ranked window sits outside the group."""
    a, b, c = object(), object(), object()
    inner = layout.Container(layout.ContainerLayout.VERTICAL, [b, c])
    root = layout.Container(layout.ContainerLayout.HORIZONTAL, [a, inner])
    rank = {a: 9, b: 1, c: 2}

    assert layout.successor(root, b, rank.get) is c


def test_layout_successor_climbs():
    """When the innermost group holds no one else, the climb skips it and
    picks from the next ancestor."""
    a, b = object(), object()
    inner = layout.Container(layout.ContainerLayout.VERTICAL, [b])
    root = layout.Container(layout.ContainerLayout.HORIZONTAL, [a, inner])

    assert layout.successor(root, b, lambda n: 0) is a


def test_layout_successor_alone():
    """A sole window, or one absent from the tree, has no successor."""
    a = object()
    root = layout.Container(layout.ContainerLayout.HORIZONTAL, [a])

    assert layout.successor(root, a, lambda n: 0) is None
    assert layout.successor(root, object(), lambda n: 0) is None


def test_layout_container_parent():
    """container_of returns the parent and index of a window by identity."""
    a, b = object(), object()
    inner = layout.Container(layout.ContainerLayout.VERTICAL, [a, b])
    root = layout.Container(layout.ContainerLayout.HORIZONTAL, [inner])

    assert layout.container_of(root, b) == (inner, 1)
    assert layout.container_of(root, object()) is None


def test_layout_move_reorder():
    """Moving a window toward a leaf sibling reorders it past that sibling
    within the same container."""
    a, b, c = object(), object(), object()
    root = layout.Container(layout.ContainerLayout.HORIZONTAL, [a, b, c])

    layout.move(root, a, layout.Direction.RIGHT)

    assert root.children == [b, a, c]


def test_layout_move_pops_out():
    """Moving a window past the edge of its container pops it out beside that
    container in the parent, collapsing the container it vacated."""
    a, f, b = object(), object(), object()
    inner = layout.Container(layout.ContainerLayout.VERTICAL, [a, f])
    root = layout.Container(
        layout.ContainerLayout.HORIZONTAL, [inner, b])

    layout.move(root, f, layout.Direction.RIGHT)

    assert root.children == [a, f, b]


def test_layout_move_descends():
    """Moving a window into an adjacent container descends into it, entering a
    perpendicular container at the front."""
    f, b, c = object(), object(), object()
    inner = layout.Container(layout.ContainerLayout.VERTICAL, [b, c])
    root = layout.Container(
        layout.ContainerLayout.HORIZONTAL, [f, inner])

    layout.move(root, f, layout.Direction.RIGHT)

    assert root.children == [inner]
    assert inner.children == [f, b, c]


def test_layout_move_perp():
    """Moving a window out of a perpendicular container pops it into the parent
    beside that container, which keeps its remaining windows."""
    a, b, f, c = object(), object(), object(), object()
    inner = layout.Container(layout.ContainerLayout.VERTICAL, [b, f, c])
    root = layout.Container(
        layout.ContainerLayout.HORIZONTAL, [a, inner])

    layout.move(root, f, layout.Direction.LEFT)

    assert root.children == [a, f, inner]
    assert inner.children == [b, c]


def test_layout_move_edge():
    """Moving a window toward the outer edge of the root is a no-op."""
    a, f = object(), object()
    root = layout.Container(layout.ContainerLayout.HORIZONTAL, [a, f])

    layout.move(root, f, layout.Direction.RIGHT)

    assert root.children == [a, f]


def test_layout_move_root_perp():
    """Moving along an axis no ancestor splits on is a no-op."""
    a, f = object(), object()
    root = layout.Container(layout.ContainerLayout.VERTICAL, [a, f])

    layout.move(root, f, layout.Direction.RIGHT)

    assert root.children == [a, f]


def test_layout_move_escapes_parent():
    """At the edge of its container, a window escapes its immediate parent one
    level up, toward the move side; the drained single-child group collapses."""
    a, b, f = object(), object(), object()
    inner = layout.Container(layout.ContainerLayout.VERTICAL, [b, f])
    root = layout.Container(
        layout.ContainerLayout.HORIZONTAL, [a, inner])

    layout.move(root, f, layout.Direction.DOWN)

    assert root.children == [a, b, f]


def test_layout_move_escapes_up():
    """Escaping toward the up/left side lands the window before its parent."""
    a, b, f = object(), object(), object()
    inner = layout.Container(layout.ContainerLayout.VERTICAL, [f, b])
    root = layout.Container(
        layout.ContainerLayout.HORIZONTAL, [inner, a])

    layout.move(root, f, layout.Direction.UP)

    assert root.children == [f, b, a]


def test_layout_move_escape_keeps_container():
    """A parent left with more than one child survives the escape."""
    a, b, c, f = object(), object(), object(), object()
    inner = layout.Container(layout.ContainerLayout.VERTICAL, [b, c, f])
    root = layout.Container(
        layout.ContainerLayout.HORIZONTAL, [a, inner])

    layout.move(root, f, layout.Direction.DOWN)

    assert root.children == [a, inner, f]
    assert inner.children == [b, c]


def test_layout_move_escapes_to_grandparent():
    """The escape rises only one level: a deeply nested window lands in its
    grandparent, not the root."""
    a, x, y, f = object(), object(), object(), object()
    h2 = layout.Container(layout.ContainerLayout.HORIZONTAL, [y, f])
    v1 = layout.Container(layout.ContainerLayout.VERTICAL, [x, h2])
    root = layout.Container(
        layout.ContainerLayout.HORIZONTAL, [a, v1])

    layout.move(root, f, layout.Direction.DOWN)

    assert root.children == [a, v1]
    assert v1.children == [x, y, f]


def test_layout_move_reorder_left():
    """Moving left reorders a window past its left-hand leaf sibling within the
    same container (negative-step reorder)."""
    a, b, c = object(), object(), object()
    root = layout.Container(layout.ContainerLayout.HORIZONTAL, [a, b, c])

    layout.move(root, c, layout.Direction.LEFT)

    assert root.children == [a, c, b]


def test_layout_move_vertical_reorder():
    """Moving DOWN in a column reorders a window past the one below it."""
    a, b, c = object(), object(), object()
    root = layout.Container(layout.ContainerLayout.VERTICAL, [a, b, c])

    layout.move(root, a, layout.Direction.DOWN)

    assert root.children == [b, a, c]


def test_layout_move_pops_up():
    """Moving UP pops a window out of its nested row into the column."""
    x, a, f = object(), object(), object()
    row = layout.Container(layout.ContainerLayout.HORIZONTAL, [a, f])
    root = layout.Container(layout.ContainerLayout.VERTICAL, [x, row])

    layout.move(root, f, layout.Direction.UP)

    assert root.children == [x, f, a]


def test_layout_move_descend_front():
    """Descending into a same-axis container from the low side enters it at the
    front."""
    f, y, z = object(), object(), object()
    inner = layout.Container(layout.ContainerLayout.HORIZONTAL, [y, z])
    root = layout.Container(
        layout.ContainerLayout.HORIZONTAL, [f, inner])

    layout.move(root, f, layout.Direction.RIGHT)

    assert root.children == [inner]
    assert inner.children == [f, y, z]


def test_layout_move_descend_end():
    """Descending into a same-axis container from the high side enters it at
    the back."""
    y, z, f = object(), object(), object()
    inner = layout.Container(layout.ContainerLayout.HORIZONTAL, [y, z])
    root = layout.Container(
        layout.ContainerLayout.HORIZONTAL, [inner, f])

    layout.move(root, f, layout.Direction.LEFT)

    assert root.children == [inner]
    assert inner.children == [y, z, f]


def test_layout_move_deep_climb():
    """A window with no room nearby climbs past several ancestors to the first
    matching-axis one with a neighbor, popping out there and collapsing the
    chain it left behind."""
    a, c, f, b = object(), object(), object(), object()
    inner = layout.Container(layout.ContainerLayout.HORIZONTAL, [c, f])
    column = layout.Container(layout.ContainerLayout.VERTICAL, [a, inner])
    root = layout.Container(
        layout.ContainerLayout.HORIZONTAL, [column, b])

    layout.move(root, f, layout.Direction.RIGHT)

    assert root.children == [column, f, b]
    assert column.children == [a, c]


def test_layout_move_popout_survives():
    """Popping out of a multi-window container leaves that container in place
    with its remaining windows."""
    a, b, f, x = object(), object(), object(), object()
    inner = layout.Container(layout.ContainerLayout.HORIZONTAL, [a, b, f])
    root = layout.Container(
        layout.ContainerLayout.HORIZONTAL, [inner, x])

    layout.move(root, f, layout.Direction.RIGHT)

    assert root.children == [inner, f, x]
    assert inner.children == [a, b]


def test_toggle_fullscreen_enters():
    """toggle_fullscreen on a tiled focused window pins it to the
    workspace's fullscreen slot."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    client = make_client(workspace=m.active_workspace)
    server = make_server(monitors=[m], active_monitor=m, clients=[client])
    focus.focus_client(server, client)

    with patch("welpy.geometry.apply_geometry"):
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

    with patch("welpy.geometry.apply_geometry"):
        wel.toggle_fullscreen(server)

    assert m.active_workspace.fullscreen is None
    assert geometry.client_layer(client) == wel.Layer.TILE


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

    with patch("welpy.geometry.apply_geometry"):
        wel.toggle_fullscreen(server)

    assert m.active_workspace.fullscreen is None
    assert client.floating_geom == saved
    assert geometry.client_layer(client) == wel.Layer.FLOAT


def test_toggle_fullscreen_no_focus():
    """toggle_fullscreen with nothing focused is a no-op."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    server = make_server(monitors=[m], active_monitor=m)

    with patch("welpy.geometry.set_fullscreen") as sf:
        wel.toggle_fullscreen(server)

    sf.assert_not_called()


def test_toggle_floating_to_float():
    """toggle_floating on a tiled focused window seeds floating_geom from
    the current outer rect so the float starts where it tiled."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    client = make_client(workspace=m.active_workspace)
    server = make_server(monitors=[m], active_monitor=m, clients=[client])
    focus.focus_client(server, client)

    seed = wel.Rect(50, 60, 304, 204)
    with patch("welpy.geometry.client_outer_rect", return_value=seed), \
         patch("welpy.geometry.apply_geometry"):
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

    with patch("welpy.geometry.apply_geometry"):
        wel.toggle_floating(server)

    assert client.floating_geom is None


def test_toggle_floating_drops_leaf():
    """Floating a tiled window drops its leaf from the workspace tree."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    a = make_client(workspace=m.active_workspace)
    b = make_client(workspace=m.active_workspace)
    m.active_workspace.root = flat_tree(a, b)
    server = make_server(monitors=[m], active_monitor=m, clients=[a, b])
    focus.focus_client(server, a)

    seed = wel.Rect(0, 0, 100, 100)
    with patch("welpy.geometry.client_outer_rect", return_value=seed), \
         patch("welpy.geometry.apply_geometry"):
        wel.toggle_floating(server)

    assert m.active_workspace.root.children == [b]


def test_toggle_floating_adds_leaf():
    """Un-floating a window inserts its leaf next to the most-recent tile."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    tiled = make_client(workspace=m.active_workspace, focus_order=1)
    floater = make_client(
        workspace=m.active_workspace,
        floating_geom=wel.Rect(10, 20, 300, 200),
        focus_order=2,
    )
    m.active_workspace.root = flat_tree(tiled)
    server = make_server(
        monitors=[m], active_monitor=m, clients=[tiled, floater])

    with patch("welpy.geometry.apply_geometry"):
        wel.toggle_floating(server)

    assert m.active_workspace.root.children == [tiled, floater]


def test_toggle_floating_fullscreen_noop():
    """toggle_floating is a no-op while the focused window is fullscreen."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    client = make_client(workspace=m.active_workspace, focus_order=1)
    m.active_workspace.fullscreen = client
    server = make_server(monitors=[m], active_monitor=m, clients=[client])
    before = client.floating_geom

    with patch("welpy.geometry.apply_geometry") as apply_geom:
        wel.toggle_floating(server)

    assert client.floating_geom is before
    apply_geom.assert_not_called()


def test_toggle_floating_no_focus():
    """toggle_floating with nothing focused is a no-op."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    server = make_server(monitors=[m], active_monitor=m)

    with patch("welpy.geometry.apply_geometry") as apply_geom:
        wel.toggle_floating(server)

    apply_geom.assert_not_called()


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
        server = wel.setup()

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
         patch("welpy.layer_shell.layer_surface_new") as handler:
        built = wel.setup()
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
        wel.setup()

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
         patch("welpy.session_lock.lock_new") as handler:
        built = wel.setup()
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
        wel.setup()

    lib.wlr_pointer_constraints_v1_create.assert_called_once_with(
        lib.wl_display_create.return_value)
    lib.wlr_relative_pointer_manager_v1_create.assert_called_once_with(
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
        built = wel.setup()
        trigger(built, lib.welpy_pointer_constraints_new_constraint, "C_DATA")
    handler.assert_called_once_with(built, "C_DATA")


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
        built = wel.setup()
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
        wel.setup()

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
         patch("welpy.output.output_power_set_mode") as handler:
        built = wel.setup()
        trigger(built, lib.welpy_output_power_mgr_set_mode, "PWR_DATA")
    handler.assert_called_once_with(built, "PWR_DATA")


# --- session lock ---------------------------------------------------------


def test_keyboard_modifiers_locked():
    """While locked with no locker surface, modifiers are not forwarded to a
    stale app focus."""
    session_lock = make_session_lock(surfaces=[])
    server = make_server(session_lock=session_lock, locked=True)
    server.seat.keyboard_state.focused_surface = MagicMock(name="window")

    input.keyboard_modifiers(server, "MOD_DATA")

    server.lib.wlr_seat_keyboard_notify_modifiers.assert_not_called()
    server.lib.wlr_seat_keyboard_clear_focus.assert_called_once_with(
        server.seat)


def test_keyboard_key_locked():
    """While locked, compositor keybindings are suppressed so the lock can't
    be bypassed; the key still forwards to the locker."""
    action = MagicMock()
    server = make_server(bindings={(0x40, 28): action}, locked=True)
    server.lib.wlr_keyboard_get_modifiers.return_value = 0x40
    event = server.ffi.cast.return_value
    event.time_msec = 42
    event.state = 1
    event.keycode = 28

    input.keyboard_key(server, "KEY_DATA")

    action.assert_not_called()
    server.lib.wlr_seat_keyboard_notify_key.assert_called_once_with(
        server.seat, 42, 28, 1)


def test_cursor_button_locked():
    """While locked, clicking a window neither focuses it nor runs a mouse
    binding; the click still forwards to the locker."""
    action = MagicMock()
    server = make_server(
        bindings={(0x8, 0x110): action}, locked=True,
        cursor=make_cursor(xcursor_manager="X"))
    server.lib.wlr_keyboard_get_modifiers.return_value = 0x8
    event = server.ffi.cast.return_value
    event.button = 0x110
    event.time_msec = 7
    event.state = server.lib.WL_POINTER_BUTTON_STATE_PRESSED

    with patch("welpy.focus.focus_client") as focus_client, \
         patch("welpy.focus.client_at") as at:
        input.cursor_button(server, "BUTTON_DATA")

    action.assert_not_called()
    focus_client.assert_not_called()
    at.assert_not_called()
    server.lib.wlr_seat_pointer_notify_button.assert_called_once()


# --- configure tracking ---------------------------------------------------


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
    with patch("welpy.geometry.client_outer_rect", return_value=seed), \
         patch("welpy.geometry.apply_geometry"):
        input.begin_dragging_client(server)

    assert client.floating_geom == seed


def test_begin_dragging_drops_leaf():
    """Starting a mouse move on a tiled window drops its leaf from the
    workspace tree so it floats outside the layout."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    a = make_client(workspace=m.active_workspace)
    b = make_client(workspace=m.active_workspace)
    m.active_workspace.root = flat_tree(a, b)
    server = make_server(
        monitors=[m], active_monitor=m, clients=[a, b],
        cursor=make_cursor(xcursor_manager="X"))
    node = MagicMock(name="node")
    node.parent = a.scene_tree
    server.lib.wlr_scene_node_at.return_value = node

    with patch("welpy.geometry.client_outer_rect",
               return_value=wel.Rect(0, 0, 100, 80)), \
         patch("welpy.geometry.apply_geometry"):
        input.begin_dragging_client(server)

    assert m.active_workspace.root.children == [b]


def test_begin_resizing_drops_leaf():
    """Starting a mouse resize on a tiled window drops its leaf from the
    workspace tree so it floats outside the layout."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    a = make_client(workspace=m.active_workspace)
    b = make_client(workspace=m.active_workspace)
    m.active_workspace.root = flat_tree(a, b)
    server = make_server(
        monitors=[m], active_monitor=m, clients=[a, b],
        cursor=make_cursor(xcursor_manager="X"))
    node = MagicMock(name="node")
    node.parent = a.scene_tree
    server.lib.wlr_scene_node_at.return_value = node

    with patch("welpy.geometry.client_outer_rect",
               return_value=wel.Rect(0, 0, 100, 80)), \
         patch("welpy.geometry.apply_geometry"):
        input.begin_resizing_client(server)

    assert m.active_workspace.root.children == [b]


def test_client_commit_initial_tiled():
    """A tiled client's initial commit defers tiling to map so siblings
    don't reflow before the new window can appear."""
    server = make_server()
    workspace = make_workspace()
    toplevel = MagicMock()
    toplevel.base.initial_commit = True
    client = make_client(toplevel=toplevel, workspace=workspace)

    with patch("welpy.geometry.apply_geometry") as apply_geom:
        windows.client_commit(server, client, None)

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

    with patch("welpy.geometry.apply_geometry") as apply_geom:
        windows.client_commit(server, client, None)

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

    with patch("welpy.geometry.apply_geometry") as apply_geom:
        windows.client_commit(server, client, None)

    apply_geom.assert_not_called()
    server.lib.wlr_xdg_toplevel_set_size.assert_called_once_with(
        toplevel, 0, 0)


def test_client_unmap_arranges():
    """After a tiled client unmaps, its monitor re-flows so remaining
    tiles expand -- in the same event as the window's removal so it lands
    in a single frame."""
    # pylint: disable=duplicate-code
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    a = make_client(workspace=m.active_workspace)
    b = make_client(workspace=m.active_workspace)
    server = make_server(monitors=[m], active_monitor=m, clients=[a, b])

    with patch("welpy.geometry.apply_geometry") as apply_geom:
        windows.client_unmap(server, a, None)

    apply_geom.assert_called_once_with(server, m)


def test_client_unmap_destroys_tree():
    """Unmapping releases the scene tree so the disappearing window, the
    reflow, and the focus shift all happen together."""
    client = make_client()
    server = make_server(clients=[client])

    windows.client_unmap(server, client, None)

    server.lib.wlr_scene_node_destroy.assert_called_once_with(
        server.ffi.addressof.return_value)
    assert client.scene_tree is None
    assert client not in server.clients


def test_client_unmap_drops_leaf():
    """Unmapping a tiled window drops its leaf from the workspace tree so the
    siblings reflow."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    a = make_client(workspace=m.active_workspace, focus_order=1)
    b = make_client(workspace=m.active_workspace, focus_order=2)
    m.active_workspace.root = flat_tree(a, b)
    server = make_server(monitors=[m], active_monitor=m, clients=[a, b])

    with patch("welpy.focus.focus_client"), \
         patch("welpy.geometry.apply_geometry"):
        windows.client_unmap(server, a, None)

    assert m.active_workspace.root.children == [b]


def test_client_unmap_orphan():
    """Unmapping an orphaned client doesn't trigger an arrange."""
    client = make_client(workspace=None)
    server = make_server(clients=[client])

    with patch("welpy.geometry.apply_geometry") as apply_geom:
        windows.client_unmap(server, client, None)

    apply_geom.assert_not_called()


def test_client_unmap_stale():
    """Unmapping a client whose monitor has already been removed is a
    no-op for arrange."""
    m = make_monitor()  # not in server.monitors
    m.active_workspace = make_workspace(monitor=m)
    client = make_client(workspace=m.active_workspace)
    server = make_server(clients=[client])

    with patch("welpy.geometry.apply_geometry") as apply_geom:
        windows.client_unmap(server, client, None)

    apply_geom.assert_not_called()


def test_setup_layout_change_updates():
    """A change in the screen layout (monitor added/removed/repositioned)
    drives update_monitors so windows re-flow onto the new geometry."""
    build = make_bindings()
    _, lib, *_ = build
    lib.WL_SEAT_CAPABILITY_POINTER = 1
    lib.WL_SEAT_CAPABILITY_KEYBOARD = 2
    with patch("welpy.app.bindings.build", return_value=build), \
         patch("welpy.input.build_keycode_map",
               return_value=make_keycode_map()), \
         patch("welpy.output.update_monitors") as upd:
        server = wel.setup()
        trigger(server, lib.welpy_output_layout_change, "LAYOUT_DATA")

    upd.assert_called_once_with(server)


# --- override / load_config ------------------------------------------------


def test_override_form1_chains_original(monkeypatch):
    """The previous function is passed in as the first argument."""
    monkeypatch.setattr(wel, "modkey", wel.modkey)

    @welpy.override(wel.modkey)
    def modkey(orig, server):
        return orig(server) | 0xFF

    server = MagicMock()
    server.lib.WLR_MODIFIER_LOGO = 0x40
    assert wel.modkey(server) == 0x40 | 0xFF


def test_override_form2_wl(monkeypatch):
    """@welpy.override(target) installs at the target's home, even when the
    new function has a different local name."""
    monkeypatch.setattr(wel, "modkey", wel.modkey)

    @welpy.override(wel.modkey)
    def renamed(_orig, _server):
        return 0x123

    assert wel.modkey(MagicMock()) == 0x123


def test_override_form2_bindings(monkeypatch):
    """@welpy.override reaches outside wel: targeting bindings.build installs
    the replacement in bindings, not in wel."""
    monkeypatch.setattr(bindings, "build", bindings.build)

    @welpy.override(bindings.build)
    def build(_orig):
        return "stub"

    assert bindings.build() == "stub"
    assert not hasattr(wel, "build")


def test_override_chain_composes(monkeypatch):
    """Form-1 then form-2 chains correctly: the second wraps the first,
    which wraps the built-in. Exercises the __module__ rewrite that lets
    form-2 find the previous wrapper at wel.<name>."""
    monkeypatch.setattr(wel, "modkey", wel.modkey)

    @welpy.override(wel.modkey)
    def modkey(orig, server):
        return orig(server) + 1

    @welpy.override(wel.modkey)
    def newer(orig, server):
        return orig(server) * 10

    server = MagicMock()
    server.lib.WLR_MODIFIER_LOGO = 5
    # built-in=5; inner adds 1 -> 6; outer multiplies by 10 -> 60.
    assert wel.modkey(server) == 60


def test_autostart_overridable(monkeypatch):
    """@welpy.override on `autostart` lets config swap the launched programs."""
    monkeypatch.setattr(wel, "autostart", wel.autostart)
    calls = []

    @welpy.override(wel.autostart)
    def autostart(_orig, server):
        calls.append(server)

    wel.autostart("SERVER")
    assert calls == ["SERVER"]


def test_override_non_callable():
    """Non-callable argument: explicit TypeError, not a confusing
    AttributeError on `__module__`."""
    with pytest.raises(TypeError, match="expects a function"):
        welpy.override(None)


def test_override_callable_no_name():
    """Callable missing __name__ (e.g. functools.partial): default
    AttributeError names the missing attribute clearly."""
    partial = functools.partial(lambda x: x)
    with pytest.raises(AttributeError, match="__name__"):
        welpy.override(partial)


def test_load_config_missing(tmp_path):
    """Absent config file is a silent no-op."""
    wel.load_config(tmp_path / "nonexistent.py")


def test_load_config_runs(tmp_path, monkeypatch):
    """The loader executes the file's top-level code."""
    # pylint: disable=protected-access,no-member
    monkeypatch.setattr(wel, "_test_marker", None, raising=False)
    config = tmp_path / "config.py"
    config.write_text(dedent("""\
        import welpy.app
        welpy.app._test_marker = 'ran'
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
        import welpy.app

        @welpy.override(welpy.app.modkey)
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
    build = make_bindings()
    with patch("welpy.app.bindings.build", return_value=build), \
         patch("welpy.input.build_keycode_map",
               return_value=make_keycode_map()):
        server = wel.setup()
    assert [w.name for w in server.workspaces] == [
        "1", "2", "3", "4", "5", "6", "7", "8", "9", "10"]
    assert all(w.monitor is None for w in server.workspaces)
    assert all(w.fullscreen is None for w in server.workspaces)
    assert server.active_monitor is None


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
    # pylint: disable=duplicate-code
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


def test_move_client_moves_leaf():
    """Moving a tiled window detaches its leaf from the source tree and
    attaches it to the target tree."""
    monitor = make_monitor()
    ws1 = make_workspace(name="1", monitor=monitor)
    ws2 = make_workspace(name="2")
    monitor.active_workspace = ws1
    client = make_client(workspace=ws1, focus_order=1)
    ws1.root = flat_tree(client)
    server = make_server(
        workspaces=[ws1, ws2], monitors=[monitor], active_monitor=monitor,
        clients=[client])

    with patch("welpy.geometry.apply_geometry"), \
         patch("welpy.focus.apply_focus"):
        wel.move_client_to_workspace(server, "2")

    assert ws1.root.children == []
    assert ws2.root.children == [client]


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
    # pylint: disable=duplicate-code
    monitor = make_monitor()
    ws = make_workspace(name="1", monitor=monitor)
    monitor.active_workspace = ws
    server = make_server(
        workspaces=[ws], monitors=[monitor], active_monitor=monitor)

    wel.move_active_workspace_to_monitor(server, +1)

    assert server.active_monitor is monitor


# --- workspaces: helpers --------------------------------------------------


def test_urgent_clears_on_focus():
    """Focusing an urgent window clears its urgent flag."""
    monitor = make_monitor()
    monitor.active_workspace = make_workspace(monitor=monitor)
    client = make_client(
        focus_order=1, urgent=True, workspace=monitor.active_workspace)
    server = make_server(
        ext_workspace=None, monitors=[monitor], active_monitor=monitor,
        clients=[client])

    focus.apply_focus(server)

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


def test_extws_unplug_migrate():
    """Unplugging a monitor whose workspace migrates to a surviving monitor
    re-homes the handle without dereferencing the just-removed group."""
    m1, m2 = make_monitor(), make_monitor()
    ws1 = make_workspace(name="1", monitor=m1)
    ws2 = make_workspace(name="2", monitor=m2)
    m1.active_workspace = ws1
    m2.active_workspace = ws2
    server = make_extws_server(monitors=[m1, m2], workspaces=[ws1, ws2])
    ext, externs = make_extws(server)
    bind_extws_client(ext, externs)
    manager = ext.managers[0]
    handle_r = extws_handle(manager, ws1).resource
    new_group_r = extws_group(manager, m2).resource
    server.lib.welpy_extws_send_workspace_enter.reset_mock()

    server.monitors.remove(m1)
    ws1.monitor = m2
    ext_workspace.publish(server)

    assert extws_handle(manager, ws1).monitor is m2
    server.lib.welpy_extws_send_workspace_enter.assert_any_call(
        new_group_r, handle_r)


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
    build = make_bindings()
    with patch("welpy.app.bindings.build", return_value=build), \
         patch("welpy.input.build_keycode_map",
               return_value=make_keycode_map()), \
         patch("welpy.app.ext_workspace.create") as create:
        server = wel.setup()

    create.assert_called_once_with(
        server, on_activate=ANY, on_assign=ANY)
    assert server.ext_workspace is create.return_value


# --- xwayland (X11 clients) ------------------------------------------------


def test_xwayland_close():
    """Closing the focused X11 window routes to the X11 close call."""
    server = make_server()
    client = make_x11_client()
    with patch("welpy.focus.top_client", return_value=client):
        wel.close_window(server)
    server.lib.wlr_xwayland_surface_close.assert_called_once_with(
        client.xsurface)


def test_xwayland_map_front():
    """A mapped X11 window goes to the front of server.clients, like a
    Wayland one."""
    old = make_client()
    server = make_server(clients=[old])
    fresh = make_x11_client(scene_tree=None)

    with patch("welpy.focus.focus_client"):
        windows.client_map(server, fresh, None)

    assert server.clients[0] is fresh


def test_xwayland_map_scene():
    """An X11 window's content goes into a subsurface tree, not an xdg one."""
    server = make_server()
    client = make_x11_client(scene_tree=None)

    with patch("welpy.focus.focus_client"):
        windows.client_map(server, client, None)

    server.lib.wlr_scene_subsurface_tree_create.assert_called_once_with(
        server.lib.wlr_scene_tree_create.return_value, client.xsurface.surface)
    server.lib.wlr_scene_xdg_surface_create.assert_not_called()


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
        wel.main()

    assert seen["display"] == ":4"


def test_close_window_xdg():
    """Closing a focused Wayland window still routes to xdg send_close."""
    server = make_server()
    client = make_client()
    with patch("welpy.focus.top_client", return_value=client):
        wel.close_window(server)
    server.lib.wlr_xdg_toplevel_send_close.assert_called_once_with(
        client.toplevel)


def test_unmanaged_focus_defers():
    """apply_focus keeps the keyboard on a focus-holding unmanaged surface and
    skips the normal window-focus path."""
    server = make_server()
    um = make_unmanaged()
    server.unmanaged_focus = um

    with patch("welpy.focus.top_client") as top:
        focus.apply_focus(server)

    top.assert_not_called()
    server.lib.wlr_seat_keyboard_notify_enter.assert_called_once()
    assert (server.lib.wlr_seat_keyboard_notify_enter.call_args.args[1]
            is um.xsurface.surface)

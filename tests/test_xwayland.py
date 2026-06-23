"""Unit tests for welpy.xwayland: managed X11 window wiring,
override-redirect surface lifecycle, X11 requests, and XWayland readiness."""

from unittest.mock import ANY, MagicMock, patch

from welpy import app as wel, xwayland
from tests.helpers import (
    make_server, make_x11_client, make_unmanaged, trigger,
)


def test_xwayland_new_unmanaged():
    """Override-redirect surfaces (menus, tooltips) take the lighter unmanaged
    path instead of the managed-window wiring."""
    server = make_server()
    xsurface = MagicMock()
    xsurface.override_redirect = True
    server.ffi.cast.return_value = xsurface

    with patch("welpy.xwayland.unmanaged_new") as unmanaged:
        xwayland.x11_surface_new(server, "DATA")

    unmanaged.assert_called_once_with(server, xsurface)


def test_xwayland_new_attaches_listeners():
    """A managed X11 window gets its lifecycle listeners."""
    server = make_server()
    xsurface = MagicMock()
    xsurface.override_redirect = False
    server.ffi.cast.return_value = xsurface

    xwayland.x11_surface_new(server, "DATA")

    server.lib.welpy_xwayland_surface_associate.assert_called_once_with(
        xsurface)
    server.lib.welpy_xwayland_surface_destroy.assert_called_once_with(xsurface)


def test_xwayland_associate_map():
    """On associate, the wl_surface's map drives the shared client_map."""
    server = make_server()
    xsurface = MagicMock()
    xsurface.override_redirect = False
    server.ffi.cast.return_value = xsurface
    xwayland.x11_surface_new(server, "DATA")

    with patch("welpy.windows.client_map") as mapped:
        trigger(server, server.lib.welpy_xwayland_surface_associate, None)
        trigger(server, server.lib.welpy_surface_map, "MAP")

    mapped.assert_called_once_with(server, ANY, "MAP")


def test_xwayland_dissociate_detaches():
    """Dissociate removes the map/unmap/commit listeners wired on associate."""
    server = make_server()
    xsurface = MagicMock()
    xsurface.override_redirect = False
    server.ffi.cast.return_value = xsurface

    registry = {}

    def fake_listen(sig, cb):
        handle = MagicMock(name="handle")
        registry[sig] = (cb, handle)
        return handle
    server.listen = MagicMock(side_effect=fake_listen)

    xwayland.x11_surface_new(server, "DATA")
    registry[server.lib.welpy_xwayland_surface_associate.return_value][0](None)
    map_handle = registry[server.lib.welpy_surface_map.return_value][1]
    registry[server.lib.welpy_xwayland_surface_dissociate.return_value][0](None)

    map_handle.remove.assert_called_once_with()


def test_xwayland_hints_wired():
    """A managed X11 window listens for ICCCM hint changes."""
    server = make_server()
    xsurface = MagicMock()
    xsurface.override_redirect = False
    server.ffi.cast.return_value = xsurface

    xwayland.x11_surface_new(server, "DATA")

    server.lib.welpy_xwayland_surface_set_hints.assert_called_once_with(
        xsurface)


def test_xwayland_configure_premap():
    """Before map, honor the X11 app's requested geometry verbatim."""
    server = make_server()
    client = make_x11_client(scene_tree=None)
    event = MagicMock(x=5, y=10, width=300, height=200)
    server.ffi.cast.return_value = event

    xwayland.x11_request_configure(server, client, "DATA")

    server.lib.wlr_xwayland_surface_configure.assert_called_once_with(
        client.xsurface, 5, 10, 300, 200)


def test_xwayland_activate_urgent():
    """An X11 activate request shows an urgent border instead of stealing
    focus."""
    server = make_server()
    client = make_x11_client()
    with patch("welpy.windows.mark_urgent") as mark:
        xwayland.x11_request_activate(server, client)
    mark.assert_called_once_with(server, client)


def test_xwayland_hints_urgent():
    """An urgent ICCCM hint on a mapped window shows an urgent border."""
    server = make_server()
    client = make_x11_client()
    server.lib.welpy_xwayland_surface_is_urgent.return_value = True

    with patch("welpy.windows.mark_urgent") as mark:
        xwayland.x11_set_hints(server, client)

    mark.assert_called_once_with(server, client)


def test_xwayland_hints_premap():
    """Hints before the window maps don't raise urgency."""
    server = make_server()
    client = make_x11_client(scene_tree=None)
    server.lib.welpy_xwayland_surface_is_urgent.return_value = True

    with patch("welpy.windows.mark_urgent") as mark:
        xwayland.x11_set_hints(server, client)

    mark.assert_not_called()


def test_xwayland_ready_seat():
    """When the X server comes up we point it at our seat and set its cursor."""
    server = make_server()

    xwayland.x11_ready(server)

    server.lib.wlr_xwayland_set_seat.assert_called_once_with(
        server.xwayland, server.seat)
    server.lib.welpy_xwayland_set_default_cursor.assert_called_once_with(
        server.xwayland, server.cursor.xcursor_manager)


def test_unmanaged_new_listeners():
    """An unmanaged surface wires associate/destroy/configure, but none of the
    managed-window-only signals."""
    server = make_server()
    xsurface = MagicMock()

    xwayland.unmanaged_new(server, xsurface)

    server.lib.welpy_xwayland_surface_associate.assert_called_once_with(
        xsurface)
    server.lib.welpy_xwayland_surface_request_configure.assert_called_once_with(
        xsurface)
    server.lib.welpy_xwayland_surface_request_fullscreen.assert_not_called()


def test_unmanaged_associate_map():
    """On associate, the wl_surface's map drives unmanaged_map."""
    server = make_server()
    xsurface = MagicMock()
    xwayland.unmanaged_new(server, xsurface)

    with patch("welpy.xwayland.unmanaged_map") as mapped:
        trigger(server, server.lib.welpy_xwayland_surface_associate, None)
        trigger(server, server.lib.welpy_surface_map, "MAP")

    mapped.assert_called_once_with(server, ANY, "MAP")


def test_unmanaged_map_position():
    """Mapping places the surface in the OVERLAY layer at the app's coords and,
    without a focus request, leaves the keyboard alone."""
    server = make_server()
    um = make_unmanaged()
    um.xsurface.x = 30
    um.xsurface.y = 40
    server.lib.wlr_xwayland_surface_override_redirect_wants_focus \
        .return_value = False

    xwayland.unmanaged_map(server, um, None)

    server.lib.wlr_scene_subsurface_tree_create.assert_called_once_with(
        server.layers[wel.Layer.OVERLAY], um.xsurface.surface)
    server.lib.wlr_scene_node_set_position.assert_called_once_with(ANY, 30, 40)
    assert server.unmanaged_focus is None


def test_unmanaged_map_focus():
    """A focus-wanting unmanaged surface becomes the keyboard owner on map."""
    server = make_server()
    um = make_unmanaged()
    server.lib.wlr_xwayland_surface_override_redirect_wants_focus \
        .return_value = True

    with patch("welpy.focus.apply_focus") as apply_focus:
        xwayland.unmanaged_map(server, um, None)

    assert server.unmanaged_focus is um
    apply_focus.assert_called_once_with(server)


def test_unmanaged_configure_position():
    """A configure request repositions the scene node to the requested spot."""
    server = make_server()
    um = make_unmanaged(scene_tree=MagicMock())
    server.ffi.cast.return_value = MagicMock(x=5, y=6, width=100, height=200)

    xwayland.unmanaged_configure(server, um, "DATA")

    server.lib.wlr_xwayland_surface_configure.assert_called_once_with(
        um.xsurface, 5, 6, 100, 200)
    server.lib.wlr_scene_node_set_position.assert_called_once_with(ANY, 5, 6)


def test_unmanaged_configure_premap():
    """Before map there's no scene node to move, only the X surface to ack."""
    server = make_server()
    um = make_unmanaged()
    server.ffi.cast.return_value = MagicMock(x=5, y=6, width=100, height=200)

    xwayland.unmanaged_configure(server, um, "DATA")

    server.lib.wlr_xwayland_surface_configure.assert_called_once()
    server.lib.wlr_scene_node_set_position.assert_not_called()


def test_unmanaged_unmap_restores():
    """Unmapping tears down the scene node and returns the keyboard."""
    server = make_server()
    um = make_unmanaged(scene_tree=MagicMock())
    server.unmanaged_focus = um

    with patch("welpy.focus.apply_focus") as apply_focus:
        xwayland.unmanaged_unmap(server, um, None)

    server.lib.wlr_scene_node_destroy.assert_called_once()
    assert um.scene_tree is None
    assert server.unmanaged_focus is None
    apply_focus.assert_called_once_with(server)


def test_unmanaged_unmap_unfocused():
    """Unmapping a surface that never held focus leaves focus untouched."""
    server = make_server()
    other = make_unmanaged()
    server.unmanaged_focus = other
    um = make_unmanaged(scene_tree=MagicMock())

    with patch("welpy.focus.apply_focus") as apply_focus:
        xwayland.unmanaged_unmap(server, um, None)

    apply_focus.assert_not_called()
    assert server.unmanaged_focus is other


def test_unmanaged_cleanup_detaches():
    """Destroy detaches listeners and drops the focus slot if it held it."""
    server = make_server()
    listener = MagicMock()
    um = make_unmanaged()
    um.listeners = [listener]
    server.unmanaged_focus = um

    xwayland.unmanaged_cleanup(server, um, None)

    listener.remove.assert_called_once_with()
    assert not um.listeners
    assert server.unmanaged_focus is None

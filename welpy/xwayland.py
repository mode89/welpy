"""XWayland integration for legacy X11 apps: managed windows,
override-redirect surfaces, activation/configure requests, and startup
readiness."""

from __future__ import annotations

from . import focus
from . import geometry
from . import windows
from .model import Layer, Server, Unmanaged, X11Client


def x11_surface_new(server: Server, data) -> None:
    """Fires when an X11 app creates a window. Managed windows get the same
    lifecycle wiring as Wayland ones; override-redirect surfaces (menus,
    tooltips, drag icons) take the lighter unmanaged path."""
    ffi, lib, listen = server.ffi, server.lib, server.listen
    xsurface = ffi.cast("struct wlr_xwayland_surface *", data)
    if xsurface.override_redirect:
        unmanaged_new(server, xsurface)
        return
    client = X11Client(
        xsurface=xsurface, scene_tree=None, content_tree=None, borders=(),
        focus_order=0, urgent=False, grab=None, floating_geom=None,
        workspace=None, listeners=[], decoration=None, handle=None,
        inner_size=None)

    # The wl_surface only exists between associate and dissociate, so its
    # map/unmap/commit listeners are wired on associate and dropped after.
    surface_listeners = []

    def on_associate(_data):
        surface = xsurface.surface
        surface_listeners.extend([
            listen(lib.welpy_surface_map(surface),
                lambda data: windows.client_map(server, client, data)),
            listen(lib.welpy_surface_unmap(surface),
                lambda data: windows.client_unmap(server, client, data)),
            listen(lib.welpy_surface_commit(surface),
                lambda data: windows.client_commit(server, client, data)),
        ])

    def on_dissociate(_data):
        for l in surface_listeners:
            l.remove()
        surface_listeners.clear()

    client.listeners.extend([
        listen(lib.welpy_xwayland_surface_associate(xsurface), on_associate),
        listen(lib.welpy_xwayland_surface_dissociate(xsurface), on_dissociate),
        listen(lib.welpy_xwayland_surface_destroy(xsurface),
            lambda data: windows.client_cleanup(server, client, data)),
        listen(lib.welpy_xwayland_surface_request_configure(xsurface),
            lambda data: x11_request_configure(server, client, data)),
        listen(lib.welpy_xwayland_surface_request_fullscreen(xsurface),
            lambda data: windows.client_request_fullscreen(
                server, client, data)),
        listen(lib.welpy_xwayland_surface_request_activate(xsurface),
            lambda _data: x11_request_activate(server, client)),
        listen(lib.welpy_xwayland_surface_set_hints(xsurface),
            lambda _data: x11_set_hints(server, client)),
    ])


def x11_request_configure(server: Server, client: X11Client, data) -> None:
    """An X11 app asked for a position/size. Honor it before the window maps
    (its initial geometry); once mapped the layout owns geometry, so reassert
    ours."""
    ffi, lib = server.ffi, server.lib
    event = ffi.cast("struct wlr_xwayland_surface_configure_event *", data)
    if client.scene_tree is None:
        lib.wlr_xwayland_surface_configure(
            client.xsurface, event.x, event.y, event.width, event.height)
    elif client.inner_size is not None:
        geometry.configure_x11(server, client, *client.inner_size)


def x11_request_activate(server: Server, client: X11Client) -> None:
    """An X11 app asked for the foreground; show an urgent border instead of
    stealing focus."""
    if client.scene_tree is not None:
        windows.mark_urgent(server, client)


def x11_set_hints(server: Server, client: X11Client) -> None:
    """An X11 app updated its ICCCM hints; show an urgent border if it set the
    urgency flag while unfocused."""
    if (client.scene_tree is not None
            and server.lib.welpy_xwayland_surface_is_urgent(client.xsurface)):
        windows.mark_urgent(server, client)


def x11_ready(server: Server) -> None:
    """Fires once the embedded X server is up. Point it at our seat and give it
    a default cursor."""
    lib = server.lib
    lib.wlr_xwayland_set_seat(server.xwayland, server.seat)
    lib.welpy_xwayland_set_default_cursor(
        server.xwayland, server.cursor.xcursor_manager)


def unmanaged_new(server: Server, xsurface) -> None:
    """Fires for an override-redirect X11 surface (menu, tooltip, dropdown).
    Wires its map/unmap lifecycle; we don't manage it as a window."""
    lib, listen = server.lib, server.listen
    um = Unmanaged(xsurface=xsurface, scene_tree=None, listeners=[])

    # The wl_surface only exists between associate and dissociate.
    surface_listeners = []

    def on_associate(_data):
        surface = xsurface.surface
        surface_listeners.extend([
            listen(lib.welpy_surface_map(surface),
                lambda data: unmanaged_map(server, um, data)),
            listen(lib.welpy_surface_unmap(surface),
                lambda data: unmanaged_unmap(server, um, data)),
        ])

    def on_dissociate(_data):
        for l in surface_listeners:
            l.remove()
        surface_listeners.clear()

    um.listeners.extend([
        listen(lib.welpy_xwayland_surface_associate(xsurface), on_associate),
        listen(lib.welpy_xwayland_surface_dissociate(xsurface), on_dissociate),
        listen(lib.welpy_xwayland_surface_destroy(xsurface),
            lambda data: unmanaged_cleanup(server, um, data)),
        listen(lib.welpy_xwayland_surface_request_configure(xsurface),
            lambda data: unmanaged_configure(server, um, data)),
    ])


def unmanaged_map(server: Server, um: Unmanaged, _data) -> None:
    """Place an override-redirect surface at the coords the app requested,
    above the windows, and give it the keyboard if it wants it."""
    ffi, lib = server.ffi, server.lib
    xsurface = um.xsurface
    um.scene_tree = lib.wlr_scene_subsurface_tree_create(
        server.layers[Layer.OVERLAY], xsurface.surface)
    lib.wlr_scene_node_set_position(
        ffi.addressof(um.scene_tree.node), xsurface.x, xsurface.y)
    lib.wlr_scene_node_raise_to_top(ffi.addressof(um.scene_tree.node))
    if lib.wlr_xwayland_surface_override_redirect_wants_focus(xsurface):
        server.unmanaged_focus = um
        focus.apply_focus(server)


def unmanaged_configure(server: Server, um: Unmanaged, data) -> None:
    """An override-redirect surface asked to move/resize itself; honor it and
    keep its scene node in sync."""
    ffi, lib = server.ffi, server.lib
    event = ffi.cast("struct wlr_xwayland_surface_configure_event *", data)
    lib.wlr_xwayland_surface_configure(
        um.xsurface, event.x, event.y, event.width, event.height)
    if um.scene_tree is not None:
        lib.wlr_scene_node_set_position(
            ffi.addressof(um.scene_tree.node), event.x, event.y)


def unmanaged_unmap(server: Server, um: Unmanaged, _data) -> None:
    """Tear down an override-redirect surface's scene node and return the
    keyboard to whoever had it before."""
    ffi, lib = server.ffi, server.lib
    if um.scene_tree is not None:
        lib.wlr_scene_node_destroy(ffi.addressof(um.scene_tree.node))
        um.scene_tree = None
    if server.unmanaged_focus is um:
        server.unmanaged_focus = None
        focus.apply_focus(server)


def unmanaged_cleanup(server: Server, um: Unmanaged, _data) -> None:
    """Fires when an override-redirect surface is destroyed; drop listeners."""
    if server.unmanaged_focus is um:
        server.unmanaged_focus = None
    for listener in um.listeners:
        listener.remove()
    um.listeners.clear()

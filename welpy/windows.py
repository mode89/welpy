"""Window lifecycle for xdg-shell apps: creating, mapping, unmapping, and
destroying windows, handling their fullscreen/maximize/activation requests,
and placing their transient popups (menus, tooltips)."""

from __future__ import annotations

from . import ext_workspace
from . import focus
from . import geometry
from . import layout
from . import model
from .model import Client, Layer, Server, X11Client, XdgClient


def client_new(server: Server, data) -> None:
    """Fires when an app creates a new window. Attaches lifecycle
    listeners; the scene tree, borders, and layout entry are added at map
    time so creation lands in a single frame."""
    ffi, lib, listen = server.ffi, server.lib, server.listen
    toplevel = ffi.cast("struct wlr_xdg_toplevel *", data)
    client = XdgClient(
        toplevel=toplevel, scene_tree=None, content_tree=None, borders=(),
        focus_order=0, urgent=False, grab=None, floating_geom=None,
        workspace=None, listeners=[], pending_serial=None,
        decoration=None, handle=None, inner_size=None)
    # Back-pointer toplevel -> Client, so per-surface protocols (xdg-decoration
    # etc.) can resolve the owning client before it joins server.clients at map.
    client.handle = ffi.new_handle(client)
    toplevel.base.data = client.handle
    client.listeners.extend([
        listen(lib.welpy_surface_commit(toplevel.base.surface),
            lambda data: client_commit(server, client, data)),
        listen(lib.welpy_surface_map(toplevel.base.surface),
            lambda data: client_map(server, client, data)),
        listen(lib.welpy_surface_unmap(toplevel.base.surface),
            lambda data: client_unmap(server, client, data)),
        listen(lib.welpy_xdg_toplevel_destroy(toplevel),
            lambda data: client_cleanup(server, client, data)),
        listen(lib.welpy_xdg_toplevel_request_fullscreen(toplevel),
            lambda data: client_request_fullscreen(server, client, data)),
        listen(lib.welpy_xdg_toplevel_request_maximize(toplevel),
            lambda data: client_request_maximize(server, client, data)),
    ])


def client_commit(server: Server, client: Client, _data) -> None:
    """Fires every time the app commits new state for its window."""
    if isinstance(client, XdgClient):
        if client.toplevel.base.initial_commit:
            # Empty configure; real tile size is sent from client_map.
            geometry.set_size(server, client, 0, 0)
            return
        if client.pending_serial is not None:
            # Release the screen hold once the client has caught up.
            acked = client.toplevel.base.current.configure_serial
            if acked >= client.pending_serial:
                client.pending_serial = None
    # Commits can still arrive after unmap, once the clipped tree is gone.
    if client.scene_tree is not None and client.inner_size is not None:
        # geometry offset can shift between commits (CSD on/off); resync.
        geometry.apply_clip(server, client)


def client_map(server: Server, client: Client, _data) -> None:
    """Fires the first time the window has a buffer to show. Builds the
    window's scene tree, joins the layout, and shifts focus -- all in one
    event so the new window, sibling reflow, and focus highlight land in
    a single frame."""
    lib = server.lib
    create_window_scene(server, client)
    if server.active_monitor is not None:
        client.workspace = server.active_monitor.active_workspace
    monitor = model.client_monitor(client)
    workspace = client.workspace
    # Un-fullscreen any window already fullscreen on this workspace so the
    # new window isn't buried under it.
    if workspace is not None and workspace.fullscreen is not None:
        geometry.set_fullscreen(server, workspace, None)
    # A new tiled window joins next to the focused one.
    target = focus.top_client(server, monitor)
    server.clients.insert(0, client)
    if geometry.client_wants_float(client) and monitor is not None:
        client.floating_geom = geometry.init_floating_geom(client)
    elif workspace is not None:
        layout.insert_sibling(workspace.root, target, client)
    geometry.set_tiled(
        server, client,
        lib.WLR_EDGE_TOP | lib.WLR_EDGE_BOTTOM
        | lib.WLR_EDGE_LEFT | lib.WLR_EDGE_RIGHT)
    if geometry.client_wants_fullscreen(client) and workspace is not None:
        # Honor a pre-map or initial-commit fullscreen request.
        geometry.set_fullscreen(server, workspace, client)
    focus.focus_client(server, client)
    geometry.apply_hierarchy(server)
    geometry.apply_visibility(server)
    geometry.apply_tree(server)
    if monitor is not None:
        geometry.apply_geometry(server, monitor)
    focus.apply_focus(server)
    # The decoration request may have arrived before the initial configure;
    # now that the surface is initialized, set_mode is safe.
    geometry.apply_decoration(server)


def client_unmap(server: Server, client: Client, _data) -> None:
    """Fires when a window stops showing (close or voluntary hide). Tears
    down the window's scene tree, leaves the layout, reflows siblings,
    and shifts focus to a window beside the closed one -- in one event so
    removal lands in a single frame."""
    ffi, lib = server.ffi, server.lib
    client.grab = None
    monitor = model.client_monitor(client)
    successor = None
    if client.workspace is not None and client.floating_geom is None:
        # Pick the successor before remove(): _collapse drops the lineage.
        successor = layout.successor(
            client.workspace.root, client, lambda c: c.focus_order)
        layout.remove(client.workspace.root, client)
    server.clients.remove(client)
    # Wrapper isn't tied to the content role's lifetime
    lib.wlr_scene_node_destroy(ffi.addressof(client.scene_tree.node))
    if isinstance(client, XdgClient):
        client.toplevel.base.surface.data = ffi.NULL
    client.scene_tree = None
    client.content_tree = None
    client.borders = ()
    client.workspace = None
    geometry.apply_hierarchy(server)
    geometry.apply_visibility(server)
    if successor is None:
        successor = focus.top_client(server, monitor)
    if successor is not None:
        focus.focus_client(server, successor)
    if monitor is not None and monitor in server.monitors:
        geometry.apply_geometry(server, monitor)
    focus.apply_focus(server)


def client_request_fullscreen(
        server: Server, client: Client, _data) -> None:
    """An app asked to enter or leave fullscreen."""
    # Pre-map: client_map reads the same flag once the tree exists.
    workspace = client.workspace if client.scene_tree is not None else None
    monitor = (model.client_monitor(client)
               if client.scene_tree is not None else None)
    if workspace is not None and monitor is not None:
        wants = geometry.client_wants_fullscreen(client)
        if wants and workspace.fullscreen is not client:
            geometry.set_fullscreen(server, workspace, client)
        elif not wants and workspace.fullscreen is client:
            geometry.set_fullscreen(server, workspace, None)
        geometry.apply_tree(server)
        geometry.apply_geometry(server, monitor)
        focus.apply_focus(server)


def client_request_maximize(server: Server, client: Client, _data) -> None:
    """An app asked to (un)maximize. We don't maximize, but xdg-shell still
    requires a configure in reply, so ack the request with an empty one."""
    # Clients may request maximize before their first commit; scheduling a
    # configure then trips a wlroots assertion. The initial configure covers it.
    if client.toplevel.base.initialized:
        server.lib.wlr_xdg_surface_schedule_configure(client.toplevel.base)


def client_request_activate(server: Server, data) -> None:
    """An app asked to be brought to the foreground. We never steal focus:
    the window just shows an urgent border until the user focuses it."""
    event = server.ffi.cast(
        "struct wlr_xdg_activation_v1_request_activate_event *", data)
    client = focus.client_for_surface(server, event.surface)
    if client is not None:
        mark_urgent(server, client)


def mark_urgent(server: Server, client: Client) -> None:
    """Flag a window as wanting attention: it shows an urgent border until the
    user focuses it. No-op if it already has focus."""
    focused = focus.client_for_surface(
        server, server.seat.keyboard_state.focused_surface)
    if client is focused:
        return
    client.urgent = True
    geometry.set_border_color(server, client, model.BORDER_COLOR_URGENT)
    if server.ext_workspace is not None:
        ext_workspace.publish(server)


def client_cleanup(_server: Server, client: Client, _data) -> None:
    """Fires when an app closes a window (or its connection drops). The
    visible work happened in unmap (if the window was ever mapped); this
    just detaches listeners."""
    for listener in client.listeners:
        listener.remove()
    client.listeners.clear()


def create_window_scene(server: Server, client: Client) -> None:
    """Build the window's wrapper scene tree: the app's content subtree inset
    within four border rects. The inset is reapplied per-resize so fullscreen
    can collapse it to 0."""
    ffi, lib = server.ffi, server.lib
    client.scene_tree = lib.wlr_scene_tree_create(server.layers[Layer.TILE])
    if isinstance(client, X11Client):
        client.content_tree = lib.wlr_scene_subsurface_tree_create(
            client.scene_tree, client.xsurface.surface)
    else:
        client.content_tree = lib.wlr_scene_xdg_surface_create(
            client.scene_tree, client.toplevel.base)
        # Anchor for popups: wlr_xdg_popup.parent points at this wlr_surface,
        # and popup_new reads .data to find the parent scene tree.
        client.toplevel.base.surface.data = ffi.cast(
            "void *", client.scene_tree)
    color = ffi.new("float[4]", model.BORDER_COLOR_INACTIVE)
    client.borders = tuple(
        lib.wlr_scene_rect_create(client.scene_tree, 0, 0, color)
        for _ in range(4))


def popup_new(server: Server, data) -> None:
    """Fires when an app creates a transient sub-window (menu, tooltip,
    autocomplete). The scene node is deferred to the popup's first commit
    so the parent surface is fully set up first."""
    ffi, lib, listen = server.ffi, server.lib, server.listen
    popup = ffi.cast("struct wlr_xdg_popup *", data)

    def on_commit(_data):
        if popup.base.initial_commit:
            # Short-circuit guards popup.parent.data when popup.parent is NULL.
            parent_scene = (
                ffi.cast("struct wlr_scene_tree *", popup.parent.data)
                if popup.parent != ffi.NULL  # pylint: disable=consider-using-in
                    and popup.parent.data != ffi.NULL
                else None
            )
            if parent_scene is not None:
                scene = lib.wlr_scene_xdg_surface_create(
                    parent_scene, popup.base)
                popup.base.surface.data = ffi.cast("void *", scene)
                root = lib.wlr_surface_get_root_surface(popup.parent)
                owner_node, owner_monitor = _popup_owner(server, root)
                if owner_monitor is not None and owner_node is not None:
                    box = geometry.monitor_box(server, owner_monitor)
                    wlr_box = ffi.new("struct wlr_box *",
                        [box.x - owner_node.x, box.y - owner_node.y,
                         box.width, box.height])
                    lib.wlr_xdg_popup_unconstrain_from_box(popup, wlr_box)
            cleanup()

    def cleanup():
        for l in listeners:
            l.remove()
        listeners.clear()

    listeners = [
        listen(lib.welpy_surface_commit(popup.base.surface), on_commit),
        listen(lib.welpy_xdg_popup_destroy(popup), lambda _data: cleanup()),
    ]


def _popup_owner(server: Server, root_surface):
    """The (parent-scene-node, monitor) for the window owning this surface,
    or (None, None) if no window claims it."""
    for c in server.clients:
        if (c.scene_tree is not None
                and geometry.client_surface(c) == root_surface):
            return c.scene_tree.node, model.client_monitor(c)
    for m in server.monitors:
        for bucket in m.layers.values():
            for ls in bucket:
                if ls.layer_surface.surface == root_surface:
                    return ls.scene_tree.node, ls.monitor
    return None, None

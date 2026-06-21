"""Window geometry and layout: sizing and placing windows and their borders,
arranging the tiling tree and layer-shell bars, and the per-window geometry
queries."""

from __future__ import annotations

import logging

from . import layout
from . import model
from .layout import Rect
from .model import (
    BORDER_WIDTH, Client, Layer, LayerSurface, Monitor, Server,
    SHELL_LAYERS, X11Client,
)

logger = logging.getLogger(__name__)


def apply_geometry(server: Server, monitor: Monitor) -> None:
    """Lay out this monitor's windows. A fullscreen window fills the screen and
    hides the rest; otherwise tiled windows follow the workspace tree and
    floats keep their own rects."""
    workspace = monitor.active_workspace
    if workspace is None:
        return
    if workspace.fullscreen is not None:
        # The rest are hidden behind it; they re-tile when fullscreen exits.
        if workspace.fullscreen.scene_tree is not None:
            resize_client(
                server, workspace.fullscreen, monitor_box(server, monitor))
        return
    for leaf, rect in layout.walk(workspace.root, monitor.window_area):
        if leaf.scene_tree is not None:
            resize_client(server, leaf, rect)
    for c in model.clients_visible(server, monitor):
        if client_layer(c) == Layer.FLOAT and c.scene_tree is not None:
            resize_client(server, c, c.floating_geom)


def apply_hierarchy(server: Server) -> None: # pylint: disable=too-many-branches
    """Repair Server↔Monitor↔Workspace edges so the requirements hold.
    Idempotent: callable after any direct write to fix downstream state."""
    # Clear stale workspace.fullscreen pointers.
    for w in server.workspaces:
        if w.fullscreen is not None and (
                w.fullscreen not in server.clients
                or w.fullscreen.workspace is not w):
            w.fullscreen = None

    if not server.monitors:
        for w in server.workspaces:
            w.monitor = None
        server.active_monitor = None
        return

    home = (
        server.active_monitor
        if server.active_monitor in server.monitors
        else server.monitors[0]
    )

    # Workspaces whose monitor went away: migrate non-empty, orphan empty.
    for w in server.workspaces:
        if w.monitor not in server.monitors:
            has_clients = any(c.workspace is w for c in server.clients)
            w.monitor = home if has_clients else None

    # Fix each monitor.active_workspace.
    for m in server.monitors:
        if (m.active_workspace is None or m.active_workspace.monitor is not m):
            here = [w for w in server.workspaces if w.monitor is m]
            m.active_workspace = here[0] if here else None

    # Orphan non-active empty workspaces.
    for w in server.workspaces:
        if (w.monitor is not None
                and w.monitor.active_workspace is not w
                and not any(c.workspace is w for c in server.clients)):
            w.monitor = None

    # Fix server.active_monitor.
    if (server.active_monitor not in server.monitors
            or server.active_monitor.active_workspace is None):
        server.active_monitor = next(
            (m for m in server.monitors if m.active_workspace is not None),
            None)

    # Seed the first monitor with an orphan if nothing is assigned anywhere.
    if server.active_monitor is None:
        seed = server.monitors[0]
        orphan = next(
            (w for w in server.workspaces if w.monitor is None), None)
        if orphan is not None:
            orphan.monitor = seed
            seed.active_workspace = orphan
            server.active_monitor = seed


def apply_visibility(server: Server) -> None:
    """Show each client iff its workspace is the active one on its monitor."""
    ffi, lib = server.ffi, server.lib
    for client in server.clients:
        if client.scene_tree is None:
            continue
        w = client.workspace
        visible = (
            w is not None
            and w.monitor is not None
            and w.monitor.active_workspace is w)
        lib.wlr_scene_node_set_enabled(
            ffi.addressof(client.scene_tree.node), visible)


def apply_tree(server: Server) -> None:
    """Parent each window's scene node under the layer it belongs to."""
    ffi, lib = server.ffi, server.lib

    for client in server.clients:
        if client.scene_tree is not None:
            node = ffi.addressof(client.scene_tree.node)
            target = server.layers[client_layer(client)]
            if node.parent != target:
                lib.wlr_scene_node_reparent(node, target)

    for monitor in server.monitors:
        for layer, bucket in monitor.layers.items():
            target = server.layers[layer]
            # BG/BOTTOM popups lift into TOP so a bar can't bury them.
            popups_target = server.layers[
                Layer.TOP if layer in (Layer.BACKGROUND, Layer.BOTTOM)
                else layer]
            for ls in bucket:
                node = ffi.addressof(ls.scene_tree.node)
                if node.parent != target:
                    lib.wlr_scene_node_reparent(node, target)
                popups_node = ffi.addressof(ls.popups_tree.node)
                if popups_node.parent != popups_target:
                    lib.wlr_scene_node_reparent(popups_node, popups_target)


def resize_client(server: Server, client: Client, rect: Rect) -> None:
    """Move and resize a window so its outer wrapper matches `rect`. The
    inner surface is configured smaller to leave room for the border;
    fullscreen windows skip the border so the surface fills the rect."""
    ffi, lib = server.ffi, server.lib
    bw = 0 if client_layer(client) == Layer.FULLSCREEN else BORDER_WIDTH
    inner_w = max(rect.width - 2 * bw, 0)
    inner_h = max(rect.height - 2 * bw, 0)
    lib.wlr_scene_node_set_position(
        ffi.addressof(client.scene_tree.node), rect.x, rect.y)
    lib.wlr_scene_node_set_position(
        ffi.addressof(client.content_tree.node), bw, bw)
    set_size(server, client, inner_w, inner_h)
    top, bottom, left, right = client.borders
    lib.wlr_scene_rect_set_size(top, rect.width, bw)
    lib.wlr_scene_rect_set_size(bottom, rect.width, bw)
    lib.wlr_scene_rect_set_size(left, bw, inner_h)
    lib.wlr_scene_rect_set_size(right, bw, inner_h)
    lib.wlr_scene_node_set_position(
        lib.welpy_scene_rect_node(top), 0, 0)
    lib.wlr_scene_node_set_position(
        lib.welpy_scene_rect_node(bottom), 0, rect.height - bw)
    lib.wlr_scene_node_set_position(
        lib.welpy_scene_rect_node(left), 0, bw)
    lib.wlr_scene_node_set_position(
        lib.welpy_scene_rect_node(right), rect.width - bw, bw)
    client.inner_size = (inner_w, inner_h)
    apply_clip(server, client)


def apply_clip(server: Server, client: Client) -> None:
    """Clip the window's surface to its inner area so the app can't paint
    over the border or leak its CSD shadow outside the wrapper."""
    ffi, lib = server.ffi, server.lib
    inner_w, inner_h = client.inner_size
    # Anchor at the geometry offset so the CSD shadow margin (baked into the
    # surface buffer by GTK/libadwaita) is clipped away.
    geom = client_geometry(client)
    clip = ffi.new("struct wlr_box *",
        [geom.x, geom.y, inner_w, inner_h])
    lib.wlr_scene_subsurface_tree_set_clip(
        ffi.addressof(client.content_tree.node), clip)


def set_size(
        server: Server, client: Client, width: int, height: int) -> None:
    """Tell this window what size to render at."""
    if isinstance(client, X11Client):
        configure_x11(server, client, width, height)
        return
    if client.inner_size == (width, height):
        return
    serial = server.lib.wlr_xdg_toplevel_set_size(
        client.toplevel, width, height)
    _track_configure(client, serial)


def set_tiled(server: Server, client: Client, edges: int) -> None:
    """Tell this window which of its edges are flush against neighbors or
    screen borders, so the app can suppress decorations on those edges."""
    if isinstance(client, X11Client):
        return  # X11 has no tiled-edge state.
    serial = server.lib.wlr_xdg_toplevel_set_tiled(client.toplevel, edges)
    _track_configure(client, serial)


def set_fullscreen(
        server: Server, workspace, client: Client | None) -> None:
    """Set this workspace's fullscreen window (None to clear), notifying the
    affected apps so their window state matches."""
    def _fullscreen(target: Client, fullscreen: bool) -> None:
        if isinstance(target, X11Client):
            server.lib.wlr_xwayland_surface_set_fullscreen(
                target.xsurface, fullscreen)
            return
        serial = server.lib.wlr_xdg_toplevel_set_fullscreen(
            target.toplevel, fullscreen)
        _track_configure(target, serial)

    prev = workspace.fullscreen
    if prev is not client:
        workspace.fullscreen = client
        if prev is not None:
            _fullscreen(prev, False)
        if client is not None:
            _fullscreen(client, True)


def set_border_color(server: Server, client: Client, color) -> None:
    """Paint every edge of this window's border with the same RGBA color."""
    color_arr = server.ffi.new("float[4]", color)
    for rect in client.borders:
        server.lib.wlr_scene_rect_set_color(rect, color_arr)


def set_activated(server: Server, client: Client, activated: bool) -> None:
    """Tell this window whether it has focus, so the app can render its
    focused state (title-bar styling, cursor blink, etc.)."""
    if isinstance(client, X11Client):
        server.lib.wlr_xwayland_surface_activate(client.xsurface, activated)
        return
    server.lib.wlr_xdg_toplevel_set_activated(client.toplevel, activated)


def configure_x11(
        server: Server, client: X11Client, width: int, height: int) -> None:
    """Configure an X11 window. X11 couples position and size in one request,
    so derive the absolute content origin from the already-placed wrapper."""
    bw = 0 if client_layer(client) == Layer.FULLSCREEN else BORDER_WIDTH
    node = client.scene_tree.node
    x, y = node.x + bw, node.y + bw
    xs = client.xsurface
    # wlroots re-sends a ConfigureNotify even when the geometry is unchanged.
    if (xs.x, xs.y, xs.width, xs.height) != (x, y, width, height):
        server.lib.wlr_xwayland_surface_configure(xs, x, y, width, height)


def _track_configure(client: Client, serial: int) -> None:
    """Remember the configure we sent so we can hold the screen until the
    client renders at the new state."""
    acked = client.toplevel.base.current.configure_serial
    if acked >= serial:
        # wlroots allocates a fresh monotonic serial per configure, so this
        # branch shouldn't be reachable; clear pending defensively.
        logger.warning(
            "configure serial %d already acked (current=%d)", serial, acked)
        client.pending_serial = None
    else:
        client.pending_serial = serial


def arrange_layers(server: Server, monitor: Monitor) -> None:
    """Configure layer-shell surfaces on `monitor` and recompute its usable
    window area from the space anchored bars reserve."""
    ffi, lib = server.ffi, server.lib
    full = monitor_box(server, monitor)
    full_box = ffi.new("struct wlr_box *",
        [full.x, full.y, full.width, full.height])
    usable = ffi.new("struct wlr_box *",
        [full.x, full.y, full.width, full.height])
    # Top-down so higher layers reserve their space first.
    for layer in reversed(SHELL_LAYERS):
        for ls in monitor.layers[layer]:
            if (ls.layer_surface.initialized
                    and ls.layer_surface.current.exclusive_zone > 0):
                lib.wlr_scene_layer_surface_v1_configure(
                    ls.scene_layer, full_box, usable)
    monitor.window_area = Rect(usable.x, usable.y, usable.width, usable.height)
    # Non-exclusive surfaces overlay the remaining area without shrinking it.
    for layer in reversed(SHELL_LAYERS):
        for ls in monitor.layers[layer]:
            if (ls.layer_surface.initialized
                    and ls.layer_surface.current.exclusive_zone <= 0):
                lib.wlr_scene_layer_surface_v1_configure(
                    ls.scene_layer, full_box, usable)
    for bucket in monitor.layers.values():
        for ls in bucket:
            # Popups live in a sibling tree; mirror their owner's position.
            lib.wlr_scene_node_set_position(
                ffi.addressof(ls.popups_tree.node),
                ls.scene_tree.node.x, ls.scene_tree.node.y)


def monitor_box(server: Server, monitor: Monitor) -> Rect:
    """This monitor's extent in layout coordinates."""
    ffi, lib = server.ffi, server.lib
    box = ffi.new("struct wlr_box *")
    lib.wlr_output_layout_get_box(server.output_layout, monitor.output, box)
    return Rect(box.x, box.y, box.width, box.height)


def place_in_layer_bucket(
        monitor: Monitor, ls: LayerSurface, layer: Layer) -> None:
    """Move a shell surface into `layer`'s bucket on its screen."""
    if ls not in monitor.layers[layer]:
        for bucket in monitor.layers.values():
            if ls in bucket:
                bucket.remove(ls)
                break
        monitor.layers[layer].append(ls)


def decoration_new(server: Server, data) -> None:
    """Fires when an app announces an xdg-toplevel decoration object so we
    can negotiate its mode. Welpy draws its own border and forces every
    window onto server-side decorations."""
    ffi, lib, listen = server.ffi, server.lib, server.listen
    deco = ffi.cast("struct wlr_xdg_toplevel_decoration_v1 *", data)
    if deco.toplevel.base.data != ffi.NULL:
        client = ffi.from_handle(deco.toplevel.base.data)
        client.decoration = deco

        def on_destroy(_data):
            client.decoration = None
            for l in listeners:
                l.remove()

        listeners = [
            listen(lib.welpy_xdg_decoration_request_mode(deco),
                lambda _data: apply_decoration(server)),
            listen(lib.welpy_xdg_decoration_destroy(deco), on_destroy),
        ]

        apply_decoration(server)


def apply_decoration(server: Server) -> None:
    """Force server-side decoration on every window that announced an
    xdg-toplevel decoration and is past the initial configure."""
    lib = server.lib
    for client in server.clients:
        # Setting the mode schedules a configure; before the surface is
        # initialized that would be a protocol error.
        if (client.decoration is not None
                and client.toplevel.base.initialized):
            lib.wlr_xdg_toplevel_decoration_v1_set_mode(
                client.decoration,
                lib.WLR_XDG_TOPLEVEL_DECORATION_V1_MODE_SERVER_SIDE)


def float_client(client: Client) -> None:
    """Detach a tiled window into a free-floating one in place: seed its rect
    from where it sits now and drop it from the tiling layout."""
    client.floating_geom = client_outer_rect(client)
    if client.workspace is not None:
        layout.remove(client.workspace.root, client)


def init_floating_geom(client: Client) -> Rect:
    """Center a freshly-floated window in its screen's usable area at the
    size the app asked for (or a default if it didn't)."""
    area = model.client_monitor(client).window_area
    geom = client_geometry(client)
    inner_w = geom.width or 250
    inner_h = geom.height or 200
    outer_w = inner_w + 2 * BORDER_WIDTH
    outer_h = inner_h + 2 * BORDER_WIDTH
    return Rect(
        area.x + (area.width - outer_w) // 2,
        area.y + (area.height - outer_h) // 2,
        outer_w, outer_h)


def client_outer_rect(client: Client) -> Rect:
    """This window's current outer rectangle (the area it draws into,
    borders included) in layout coordinates."""
    geom = client_geometry(client)
    return Rect(
        client.scene_tree.node.x, client.scene_tree.node.y,
        geom.width + 2 * BORDER_WIDTH, geom.height + 2 * BORDER_WIDTH)


def client_layer(client: Client) -> Layer:
    """The z-bucket this window lives in, derived from its workspace's
    fullscreen pointer and its own floating rect."""
    if (client.workspace is not None
            and client.workspace.fullscreen is client):
        return Layer.FULLSCREEN
    if client.floating_geom is not None:
        return Layer.FLOAT
    return Layer.TILE


def client_geometry(client: Client) -> Rect:
    """The window's content extent: offset plus size. For Wayland windows this
    is the xdg geometry box (whose offset trims the CSD shadow margin); X11
    windows have no such offset, so it's the raw surface size at (0, 0)."""
    if isinstance(client, X11Client):
        return Rect(0, 0, client.xsurface.width, client.xsurface.height)
    geom = client.toplevel.base.geometry
    return Rect(geom.x, geom.y, geom.width, geom.height)


def client_surface(client: Client):
    """The wl_surface backing this window's content."""
    if isinstance(client, X11Client):
        return client.xsurface.surface
    return client.toplevel.base.surface


def client_wants_fullscreen(client: Client) -> bool:
    """Whether the app is asking to be fullscreen."""
    if isinstance(client, X11Client):
        return bool(client.xsurface.fullscreen)
    return bool(client.toplevel.requested.fullscreen)


def client_wants_float(client: Client) -> bool:
    """Whether this window should open floating (it's a transient child of
    another window)."""
    if isinstance(client, X11Client):
        return bool(client.xsurface.parent)
    return bool(client.toplevel.parent)

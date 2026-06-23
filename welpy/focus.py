"""Window focus and pointer hit-testing: choosing which window or shell surface
holds the keyboard, applying the focus indicators, the focus/tile queries, and
resolving the surface or window under the cursor."""

from __future__ import annotations

import logging

from . import ext_workspace
from . import geometry
from . import layout
from . import model
from .model import Client, Layer, Server

logger = logging.getLogger(__name__)


def focus_client(server: Server, client: Client) -> None:
    """Mark `client` as most-recently-focused. The actual focus effects
    are emitted by apply_focus at the handler boundary."""
    previous = top_client(server, server.active_monitor)
    client.focus_order = (previous.focus_order if previous else 0) + 1


def apply_focus(server: Server) -> None: # pylint: disable=too-many-branches
    """Reconcile keyboard focus and focus indicators to match current state.
    Picks the highest-priority TOP/OVERLAY shell surface that asks for the
    keyboard, else the most-recently-focused window on the selected screen,
    and emits only the effects needed to converge wlroots onto that target."""
    ffi, lib = server.ffi, server.lib
    none = lib.ZWLR_LAYER_SURFACE_V1_KEYBOARD_INTERACTIVITY_NONE

    if server.locked:
        # The locker owns the keyboard; windows and shell surfaces can't.
        focus_lock(server)
        return

    if server.unmanaged_focus is not None:
        focus_unmanaged(server)
        return

    def qualifies(ls):
        return (ls.layer_surface.surface.mapped
                and ls.layer_surface.current.keyboard_interactive != none)

    # Prefer the currently-focused layer surface if it still qualifies, so
    # arranging an unrelated screen doesn't steal the keyboard from it.
    target_ls = next((
        ls for m in server.monitors
        for bucket in m.layers.values()
        for ls in bucket
        if ls.focused and qualifies(ls)), None)
    if target_ls is None:
        for m in server.monitors:
            for layer in (Layer.OVERLAY, Layer.TOP):
                for ls in reversed(m.layers[layer]):
                    if qualifies(ls):
                        target_ls = ls
                        break
                if target_ls is not None:
                    break
            if target_ls is not None:
                break

    target_client = (top_client(server, server.active_monitor)
                     if target_ls is None else None)
    target_surface = (
        target_ls.layer_surface.surface if target_ls is not None
        else geometry.client_surface(target_client) if target_client is not None
        else None)

    # ls.focused is a cache of what apply_focus last picked.
    for m in server.monitors:
        for bucket in m.layers.values():
            for ls in bucket:
                ls.focused = ls is target_ls

    current_surface = server.seat.keyboard_state.focused_surface
    if current_surface == ffi.NULL:
        current_surface = None
    current_client = client_for_surface(server, current_surface)

    if target_client is not None and target_client.urgent:
        target_client.urgent = False
        if server.ext_workspace is not None:
            ext_workspace.publish(server)

    if (current_client is not None
            and current_client is not target_client):
        geometry.set_activated(server, current_client, False)
        geometry.set_border_color(
            server, current_client, model.BORDER_COLOR_INACTIVE)

    if target_client is not None and target_client is not current_client:
        lib.wlr_scene_node_raise_to_top(
            ffi.addressof(target_client.scene_tree.node))
        geometry.set_activated(server, target_client, True)
        geometry.set_border_color(
            server, target_client, model.BORDER_COLOR_ACTIVE)

    if target_surface != current_surface:
        if target_surface is None:
            lib.wlr_seat_keyboard_clear_focus(server.seat)
        else:
            kb_group = lib.welpy_keyboard_group_keyboard(
                server.keyboard_group.group)
            lib.wlr_seat_keyboard_notify_enter(
                server.seat, target_surface,
                kb_group.keycodes, kb_group.num_keycodes,
                ffi.addressof(kb_group, "modifiers"))

    # Re-point pointer focus after the scene changed without mouse motion,
    # so events don't hit a now-hidden window; a drag keeps its own focus.
    if grabbed_client(server) is None:
        forward_pointer_motion(server, 0)


def focus_lock(server: Server) -> None:
    """While locked, route the keyboard to the lock surface on the active
    screen so the user can type their password, and nowhere else."""
    ffi, lib = server.ffi, server.lib
    surface = None
    if server.session_lock is not None and server.session_lock.surfaces:
        ls = next(
            (s for s in server.session_lock.surfaces
             if s.monitor is server.active_monitor),
            server.session_lock.surfaces[0])
        surface = ls.lock_surface.surface
    current = server.seat.keyboard_state.focused_surface
    if current == ffi.NULL:
        current = None
    if surface != current:
        if surface is None:
            lib.wlr_seat_keyboard_clear_focus(server.seat)
        else:
            kb = lib.welpy_keyboard_group_keyboard(server.keyboard_group.group)
            lib.wlr_seat_keyboard_notify_enter(
                server.seat, surface,
                kb.keycodes, kb.num_keycodes, ffi.addressof(kb, "modifiers"))


def focus_unmanaged(server: Server) -> None:
    """While an override-redirect surface holds focus, keep the keyboard on it
    so a stray reflow can't yank focus away and dismiss the menu."""
    ffi, lib = server.ffi, server.lib
    surface = server.unmanaged_focus.xsurface.surface
    current = server.seat.keyboard_state.focused_surface
    if current == ffi.NULL:
        current = None
    if surface != current:
        kb = lib.welpy_keyboard_group_keyboard(server.keyboard_group.group)
        lib.wlr_seat_keyboard_notify_enter(
            server.seat, surface,
            kb.keycodes, kb.num_keycodes, ffi.addressof(kb, "modifiers"))


def focused_container(server: Server):
    """The focused window with its screen and the container holding it, or None
    when no tiled window is focused or it isn't in the tree."""
    client = focused_tiled(server)
    if client is None:
        return None
    monitor = server.active_monitor
    found = layout.container_of(monitor.active_workspace.root, client)
    if found is None:
        return None
    return monitor, client, found[0]


def focused_tiled(server: Server):
    """The active screen's focused window when it's a tiled tree leaf with no
    fullscreen over it -- the precondition for the tree keybinds, else None."""
    monitor = server.active_monitor
    if monitor is None or monitor.active_workspace is None:
        return None
    if monitor.active_workspace.fullscreen is not None:
        return None
    client = top_client(server, monitor)
    if client is None or client.floating_geom is not None:
        return None
    return client


def recent_tiled_leaf(root):
    """The most-recently-focused window in `root`'s tile tree, or None when the
    tree is empty -- the anchor a new tile attaches next to."""
    return max(
        layout.leaves(root), key=lambda c: c.focus_order, default=None)


def client_for_surface(server: Server, surface):
    """The mapped window backing `surface`, or None."""
    if surface is None or surface == server.ffi.NULL:
        return None
    return next((
        c for c in server.clients
        if c.scene_tree is not None
        and geometry.client_surface(c) == surface), None)


def grabbed_client(server: Server):
    """Return the window currently being mouse-dragged, or None."""
    grabbed = [c for c in server.clients if c.grab is not None]
    if len(grabbed) > 1:
        logger.warning("multiple windows grabbed: %d", len(grabbed))
    return grabbed[0] if grabbed else None


def top_client(server: Server, monitor):
    """The most-recently-focused visible window on `monitor`, or None."""
    return max(
        (c for c in model.clients_visible(server, monitor)
         if c.scene_tree is not None),
        key=lambda c: c.focus_order, default=None)


def forward_pointer_motion(server: Server, time_msec: int) -> None:
    """Forward a pointer move to whatever surface sits under the cursor so
    apps see hovers and tooltips."""
    lib = server.lib
    cur = server.cursor.cursor
    surface, sx, sy = surface_at(server, cur.x, cur.y)
    if surface is None:
        # Restore the default image so a cursor a client set earlier doesn't
        # linger once the pointer leaves it for the background.
        lib.wlr_cursor_set_xcursor(
            cur, server.cursor.xcursor_manager, b"default")
        lib.wlr_seat_pointer_clear_focus(server.seat)
    else:
        lib.wlr_seat_pointer_notify_enter(server.seat, surface, sx, sy)
        lib.wlr_seat_pointer_notify_motion(server.seat, time_msec, sx, sy)


def rebase_pointer(server: Server, time_msec: int) -> None:
    """Re-point pointer focus at the surface now under the cursor before a
    click or scroll is dispatched, so the event reaches the right window when
    the scene changed under a still cursor (e.g. a window grew into
    fullscreen). A no-op when focus already matches, so a scroll in place
    doesn't emit a spurious motion."""
    ffi, lib = server.ffi, server.lib
    cur = server.cursor.cursor
    surface, sx, sy = surface_at(server, cur.x, cur.y)
    focused = server.seat.pointer_state.focused_surface
    if focused == ffi.NULL:
        focused = None
    if surface == focused:
        return
    if surface is None:
        lib.wlr_seat_pointer_clear_focus(server.seat)
    else:
        lib.wlr_seat_pointer_notify_enter(server.seat, surface, sx, sy)
        lib.wlr_seat_pointer_notify_motion(server.seat, time_msec, sx, sy)


def client_at(server: Server, lx: float, ly: float):
    """Find the window covering the given layout point, or None. Walks up
    from the deepest scene node at that point to whichever ancestor tree we
    own as a window's root."""
    ffi, lib = server.ffi, server.lib
    nx = ffi.new("double *")
    ny = ffi.new("double *")
    node = lib.wlr_scene_node_at(
        ffi.addressof(server.scene.tree.node), lx, ly, nx, ny)
    if node == ffi.NULL:
        return None
    tree = node.parent
    while tree != ffi.NULL:
        for client in server.clients:
            if client.scene_tree == tree:
                return client
        tree = tree.node.parent
    return None


def surface_at(server: Server, lx: float, ly: float):
    """The `(surface, sx, sy)` under a layout point, or `(None, 0, 0)`.
    Resolves the deepest scene buffer node at that point back to its
    wlr_surface and surface-local coordinates."""
    ffi, lib = server.ffi, server.lib
    nx = ffi.new("double *")
    ny = ffi.new("double *")
    node = lib.wlr_scene_node_at(
        ffi.addressof(server.scene.tree.node), lx, ly, nx, ny)
    if node == ffi.NULL or node.type != lib.WLR_SCENE_NODE_BUFFER:
        return None, 0, 0
    scene_surface = lib.wlr_scene_surface_try_from_buffer(
        lib.wlr_scene_buffer_from_node(node))
    if scene_surface == ffi.NULL:
        return None, 0, 0
    return scene_surface.surface, nx[0], ny[0]

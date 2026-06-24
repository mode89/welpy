"""Screen-lock (session lock) support: blank every screen and hand a locker
app the top of the scene until it authenticates the user."""

from __future__ import annotations

import logging

from . import focus
from . import geometry
from .model import Layer, LockSurface, Server, SessionLock

logger = logging.getLogger(__name__)


def on_lock(server: Server, data) -> None:
    """A screen-locker app asked to lock the screen. Blank every screen and
    hand the locker the top of the scene until it authenticates the user."""
    ffi, lib, listen = server.ffi, server.lib, server.listen
    lock = ffi.cast("struct wlr_session_lock_v1 *", data)
    lib.wlr_scene_node_set_enabled(
        lib.welpy_scene_rect_node(server.lock_background), True)
    lib.wlr_seat_pointer_clear_focus(server.seat)
    for client in server.clients:
        client.grab = None
    if server.session_lock is not None:
        # Only one locker at a time; reject the latecomer.
        lib.wlr_session_lock_v1_destroy(lock)
        return
    tree = lib.wlr_scene_tree_create(server.layers[Layer.LOCK])
    session_lock = SessionLock(
        lock=lock, tree=tree, surfaces=[], listeners=[])
    server.session_lock = session_lock
    server.locked = True
    session_lock.listeners.extend([
        listen(lib.welpy_session_lock_new_surface(lock),
            lambda data: on_surface_created(server, data)),
        listen(lib.welpy_session_lock_unlock(lock),
            lambda _data: on_unlock(server)),
        listen(lib.welpy_session_lock_destroy(lock),
            lambda _data: on_destroy(server)),
    ])
    lib.wlr_session_lock_v1_send_locked(lock)
    focus.reconcile(server)


def on_surface_created(server: Server, data) -> None:
    """The locker created its blanking surface for one screen."""
    ffi, lib, listen = server.ffi, server.lib, server.listen
    lock_surface = ffi.cast("struct wlr_session_lock_surface_v1 *", data)
    monitor = next(
        (m for m in server.monitors if m.output == lock_surface.output), None)
    if server.session_lock is None:
        return
    if monitor is None:
        logger.warning("ignoring lock surface for unknown screen")
        return
    scene_tree = lib.wlr_scene_subsurface_tree_create(
        server.session_lock.tree, lock_surface.surface)
    box = geometry.monitor_box(server, monitor)
    lib.wlr_scene_node_set_position(
        ffi.addressof(scene_tree.node), box.x, box.y)
    lib.wlr_session_lock_surface_v1_configure(
        lock_surface, box.width, box.height)
    ls = LockSurface(
        lock_surface=lock_surface, monitor=monitor, scene_tree=scene_tree,
        listeners=[])
    server.session_lock.surfaces.append(ls)
    ls.listeners.append(
        listen(lib.welpy_session_lock_surface_destroy(lock_surface),
            lambda _data: on_surface_destroyed(server, ls)))
    focus.reconcile(server)


def on_surface_destroyed(server: Server, ls: LockSurface) -> None:
    """A lock surface went away; drop it and move focus to a sibling."""
    for listener in ls.listeners:
        listener.remove()
    ls.listeners.clear()
    if server.session_lock is not None and ls in server.session_lock.surfaces:
        server.session_lock.surfaces.remove(ls)
    focus.reconcile(server)


def on_unlock(server: Server) -> None:
    """The locker authenticated the user; reveal the screen again."""
    teardown(server, unlocked=True)


def on_destroy(server: Server) -> None:
    """The locker vanished without unlocking (e.g. it crashed). Stay locked
    with a blank screen so window contents aren't exposed."""
    teardown(server, unlocked=False)


def teardown(server: Server, unlocked: bool) -> None:
    """Tear down the active lock. `unlocked` reveals the screen; otherwise the
    blanking rectangle stays up so a crashed locker can't leak contents."""
    ffi, lib = server.ffi, server.lib
    session_lock = server.session_lock
    if session_lock is None:
        return
    for ls in session_lock.surfaces:
        for listener in ls.listeners:
            listener.remove()
        ls.listeners.clear()
    session_lock.surfaces.clear()
    for listener in session_lock.listeners:
        listener.remove()
    session_lock.listeners.clear()
    lib.wlr_scene_node_destroy(ffi.addressof(session_lock.tree.node))
    server.session_lock = None
    if unlocked:
        server.locked = False
        lib.wlr_scene_node_set_enabled(
            lib.welpy_scene_rect_node(server.lock_background), False)
    focus.reconcile(server)


def update_background(server: Server) -> None:
    """Size the blanking rectangle to cover the whole screen layout."""
    ffi, lib = server.ffi, server.lib
    box = ffi.new("struct wlr_box *")
    lib.wlr_output_layout_get_box(server.output_layout, ffi.NULL, box)
    lib.wlr_scene_node_set_position(
        lib.welpy_scene_rect_node(server.lock_background), box.x, box.y)
    lib.wlr_scene_rect_set_size(
        server.lock_background, box.width, box.height)


def update_surfaces(server: Server) -> None:
    """Keep active lock surfaces matched to their current screens."""
    ffi, lib = server.ffi, server.lib
    if server.session_lock is None:
        return
    for ls in list(server.session_lock.surfaces):
        if ls.monitor not in server.monitors:
            on_surface_destroyed(server, ls)
        else:
            box = geometry.monitor_box(server, ls.monitor)
            lib.wlr_scene_node_set_position(
                ffi.addressof(ls.scene_tree.node), box.x, box.y)
            lib.wlr_session_lock_surface_v1_configure(
                ls.lock_surface, box.width, box.height)


def create_background(ffi, lib, tree):
    """Black rectangle kept on top of every window while the screen is locked;
    sized to the whole layout by update_background."""
    black = ffi.new("float[4]", (0.0, 0.0, 0.0, 1.0))
    rect = lib.wlr_scene_rect_create(tree, 0, 0, black)
    lib.wlr_scene_node_set_enabled(lib.welpy_scene_rect_node(rect), False)
    return rect

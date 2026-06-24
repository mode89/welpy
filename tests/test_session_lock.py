"""Unit tests for welpy.session_lock: screen-lock lifecycle — blanking,
lock-surface placement, unlock/teardown, and crash-safe relock."""

import logging
from unittest.mock import MagicMock

from welpy import model, session_lock
from tests.helpers import (
    make_server, make_client, make_monitor, make_session_lock,
)


def _stage_lock_new(server):
    """Stage lock_new so the cast resolves to a controllable lock object."""
    lock = MagicMock(name="lock")
    server.ffi.cast.side_effect = lambda type_str, val: {
        "struct wlr_session_lock_v1 *": lock,
    }.get(type_str, ("CAST", type_str, val))
    return lock


def test_engage_blanks_screen():
    """A lock request blanks the screen, marks the server locked, and tells
    the locker the screen is now locked."""
    server = make_server()
    lock = _stage_lock_new(server)

    session_lock.on_lock(server, "DATA")

    assert server.locked is True
    assert server.session_lock.lock is lock
    server.lib.wlr_session_lock_v1_send_locked.assert_called_once_with(lock)
    server.lib.wlr_scene_node_set_enabled.assert_any_call(
        server.lib.welpy_scene_rect_node.return_value, True)


def test_engage_rejects_second_locker():
    """Only one locker may hold the screen; a second lock is rejected."""
    existing = make_session_lock()
    server = make_server(session_lock=existing, locked=True)
    lock = _stage_lock_new(server)

    session_lock.on_lock(server, "DATA")

    server.lib.wlr_session_lock_v1_destroy.assert_called_once_with(lock)
    assert server.session_lock is existing


def test_engage_clears_pointer():
    """Locking drops pointer focus so clicks don't reach the old window."""
    server = make_server()
    _stage_lock_new(server)

    session_lock.on_lock(server, "DATA")

    server.lib.wlr_seat_pointer_clear_focus.assert_called_once_with(
        server.seat)


def test_engage_cancels_grabs():
    """Locking cancels active window drags so motion goes to the locker."""
    client = make_client(grab=model.Grab("move", 1, 2))
    server = make_server(clients=[client])
    _stage_lock_new(server)

    session_lock.on_lock(server, "DATA")

    assert client.grab is None


def test_surface_placed_and_sized():
    """A lock surface is placed on its screen and sized to fill it."""
    monitor = make_monitor()
    sess_lock = make_session_lock()
    server = make_server(
        monitors=[monitor], active_monitor=monitor,
        session_lock=sess_lock, locked=True)
    lock_surface = MagicMock(name="lock_surface")
    lock_surface.output = monitor.output
    server.ffi.cast.side_effect = lambda type_str, val: {
        "struct wlr_session_lock_surface_v1 *": lock_surface,
    }.get(type_str, ("CAST", type_str, val))

    session_lock.on_surface_created(server, "DATA")

    server.lib.wlr_scene_subsurface_tree_create.assert_called_once_with(
        sess_lock.tree, lock_surface.surface)
    server.lib.wlr_session_lock_surface_v1_configure.assert_called_once()
    assert len(sess_lock.surfaces) == 1
    assert sess_lock.surfaces[0].monitor is monitor


def test_surface_unknown_screen_ignored():
    """A lock surface for an unknown screen is ignored, not crashed on."""
    sess_lock = make_session_lock()
    server = make_server(session_lock=sess_lock, locked=True)
    lock_surface = MagicMock(name="lock_surface")
    lock_surface.output = "GONE"
    server.ffi.cast.side_effect = lambda type_str, val: {
        "struct wlr_session_lock_surface_v1 *": lock_surface,
    }.get(type_str, ("CAST", type_str, val))

    session_lock.on_surface_created(server, "DATA")

    assert not sess_lock.surfaces
    server.lib.wlr_scene_subsurface_tree_create.assert_not_called()


def test_surface_unknown_screen_logged(caplog):
    """An unknown-screen lock surface is visible in logs so locker/output
    races can be diagnosed."""
    sess_lock = make_session_lock()
    server = make_server(session_lock=sess_lock, locked=True)
    lock_surface = MagicMock(name="lock_surface")
    lock_surface.output = "GONE"
    server.ffi.cast.side_effect = lambda type_str, val: {
        "struct wlr_session_lock_surface_v1 *": lock_surface,
    }.get(type_str, ("CAST", type_str, val))

    with caplog.at_level(logging.WARNING, logger="welpy.session_lock"):
        session_lock.on_surface_created(server, "DATA")

    assert "unknown screen" in caplog.text


def test_surface_after_teardown_ignored():
    """A lock-surface signal arriving after lock teardown is ignored instead
    of crashing on missing lock state."""
    monitor = make_monitor()
    server = make_server(monitors=[monitor], session_lock=None, locked=True)
    lock_surface = MagicMock(name="lock_surface")
    lock_surface.output = monitor.output
    server.ffi.cast.side_effect = lambda type_str, val: {
        "struct wlr_session_lock_surface_v1 *": lock_surface,
    }.get(type_str, ("CAST", type_str, val))

    session_lock.on_surface_created(server, "DATA")

    server.lib.wlr_scene_subsurface_tree_create.assert_not_called()


def test_teardown_unlock_reveals():
    """Unlocking clears the lock, un-blanks the screen, and tears down the
    lock's scene tree."""
    listener = MagicMock(name="listener")
    sess_lock = make_session_lock(listeners=[listener])
    server = make_server(session_lock=sess_lock, locked=True)

    session_lock.on_unlock(server)

    assert server.locked is False
    assert server.session_lock is None
    listener.remove.assert_called_once()
    server.lib.wlr_scene_node_set_enabled.assert_any_call(
        server.lib.welpy_scene_rect_node.return_value, False)
    server.lib.wlr_scene_node_destroy.assert_called_once()


def test_teardown_destroy_stays_locked():
    """If the locker vanishes without unlocking, the screen stays blank and
    locked so window contents are never exposed."""
    sess_lock = make_session_lock()
    server = make_server(session_lock=sess_lock, locked=True)

    session_lock.on_destroy(server)

    assert server.locked is True
    assert server.session_lock is None
    server.lib.wlr_scene_node_set_enabled.assert_not_called()


def test_surface_destroyed_dropped():
    """A destroyed lock surface is dropped and its listeners detached."""
    listener = MagicMock(name="listener")
    ls = model.LockSurface(
        lock_surface=MagicMock(), monitor=make_monitor(),
        scene_tree=MagicMock(), listeners=[listener])
    sess_lock = make_session_lock(surfaces=[ls])
    server = make_server(session_lock=sess_lock, locked=True)

    session_lock.on_surface_destroyed(server, ls)

    assert ls not in sess_lock.surfaces
    listener.remove.assert_called_once()

"""Phase-2 box-7 driver: carve welpy/session_lock.py out of app.py.

Read-only on the repo; writes the transformed files to /tmp/welpy-session-lock/.
Run from the repo root: ``python scripts/phase2_session_lock.py``.
"""

import pathlib

import refactorlib as r

REPO = pathlib.Path(__file__).resolve().parent.parent
STAGE = pathlib.Path("/tmp/welpy-session-lock")

# Top-down: protocol entry, surface lifecycle, teardown trio, layout-update
# helpers, then the standalone scene helper.
SESSION_LOCK_ORDER = [
    "lock_new", "lock_surface_new", "lock_surface_destroy",
    "lock_unlock", "lock_destroy", "destroy_lock",
    "update_lock_background", "update_lock_surfaces", "create_lock_background",
]

# tests: wel.NAME -> session_lock.NAME (whole-file occurrence counts).
WEL_REFS = [
    ("lock_new", 4), ("lock_surface_new", 4), ("lock_surface_destroy", 1),
    ("lock_unlock", 1), ("lock_destroy", 1),
]

# tests: "welpy.app.NAME" patch targets -> "welpy.session_lock.NAME".
APP_STRINGS = [("lock_new", 1)]

# session-lock-subject tests, ordered to mirror SESSION_LOCK_ORDER.
MOVE = [
    "_stage_lock_new",
    "test_lock_new_blanks", "test_lock_new_rejects",
    "test_lock_pointer_cleared", "test_lock_grabs_cleared",
    "test_lock_surface_configures", "test_lock_surface_orphan",
    "test_lock_surface_orphan_logged", "test_lock_surface_stale_ignored",
    "test_lock_unlock_reveals", "test_lock_destroy_locked",
    "test_lock_surface_gone",
]

SESSION_LOCK_HEADER = '''\
"""Screen-lock (session lock) support: blank every screen and hand a locker
app the top of the scene until it authenticates the user."""

from __future__ import annotations

import logging

from . import focus
from . import geometry
from .model import Layer, LockSurface, Server, SessionLock

logger = logging.getLogger(__name__)
'''

TEST_HEADER = '''\
"""Unit tests for welpy.session_lock: screen-lock lifecycle — blanking,
lock-surface placement, unlock/teardown, and crash-safe relock."""

import logging
from unittest.mock import MagicMock

from welpy import app as wel, session_lock
from tests.helpers import (
    make_server, make_client, make_monitor, make_session_lock,
)
'''


def main() -> None:
    _build_session_lock()
    _rewrite_app()
    _rewrite_tests()
    print(f"staged to {STAGE}")
    _warn_long("welpy/session_lock.py", "welpy/app.py",
               "tests/test_session_lock.py", "tests/test_app.py")


def _build_session_lock() -> None:
    body = r.extract_defs(_read("welpy/app.py"), SESSION_LOCK_ORDER)
    _write("welpy/session_lock.py", SESSION_LOCK_HEADER + "\n\n" + body)


def _rewrite_app() -> None:
    a = r.delete_defs(_read("welpy/app.py"), SESSION_LOCK_ORDER)
    a = r.replace(a, "from . import model\n",
                  "from . import model\nfrom . import session_lock\n", n=1)
    a = r.replace(
        a,
        "    Client, Cursor, Grab, KeyboardGroup, Layer,\n"
        "    LockSurface, Monitor, PointerConstraint, Server, SessionLock,\n"
        "    SHELL_LAYERS, Workspace, X11Client, XdgClient,\n",
        "    Client, Cursor, Grab, KeyboardGroup, Layer,\n"
        "    Monitor, PointerConstraint, Server,\n"
        "    SHELL_LAYERS, Workspace, X11Client, XdgClient,\n",
        n=1)
    a = r.replace(
        a,
        "LayerSurface = model.LayerSurface\nUnmanaged = model.Unmanaged\n",
        "LayerSurface = model.LayerSurface\n"
        "LockSurface = model.LockSurface\n"
        "SessionLock = model.SessionLock\n"
        "Unmanaged = model.Unmanaged\n",
        n=1)
    for old, new in [
        ("create_lock_background(ffi, lib, layers[Layer.LOCK])",
         "session_lock.create_lock_background(ffi, lib, layers[Layer.LOCK])"),
        ("lambda data: lock_new(server, data)",
         "lambda data: session_lock.lock_new(server, data)"),
        ("update_lock_background(server)",
         "session_lock.update_lock_background(server)"),
        ("update_lock_surfaces(server)",
         "session_lock.update_lock_surfaces(server)"),
    ]:
        a = r.replace(a, old, new, n=1)
    _write("welpy/app.py", a)


def _rewrite_tests() -> None:
    t = _read("tests/test_app.py")
    for name, n in WEL_REFS:
        t = r.replace(t, "wel." + name, "session_lock." + name, n=n)
    for name, n in APP_STRINGS:
        t = r.replace(t, '"welpy.app.' + name + '"',
                      '"welpy.session_lock.' + name + '"', n=n)

    moved = r.extract_defs(t, MOVE)
    _write("tests/test_session_lock.py", TEST_HEADER + "\n\n" + moved)

    rest = r.delete_defs(t, MOVE)
    _write("tests/test_app.py", rest)


def _warn_long(*rels: str) -> None:
    for rel in rels:
        for i, line in enumerate((STAGE / rel).read_text().split("\n"), 1):
            if len(line) > 80:
                print(f"  LONG {rel}:{i} ({len(line)}): {line}")


def _read(rel: str) -> str:
    return (REPO / rel).read_text()


def _write(rel: str, content: str) -> None:
    path = STAGE / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


if __name__ == "__main__":
    main()

"""Phase-2 box-3 driver: carve welpy/focus.py out of app.py (transient).

Read-only on the repo; writes the transformed files to /tmp/welpy-focus/.
Run from the repo root: ``python scripts/phase2_focus.py``.
"""

import pathlib

import refactorlib as r

REPO = pathlib.Path(__file__).resolve().parent.parent
STAGE = pathlib.Path("/tmp/welpy-focus")

# Top-down: callers above callees, kept inside the policy / queries / pointer
# bands (topo demands focused_container before focused_tiled; the heavily-called
# top_client / surface_at sink to the bottom of their bands).
FOCUS_ORDER = [
    "focus_client", "apply_focus", "focus_lock", "focus_unmanaged",
    "focused_container", "focused_tiled", "recent_tiled_leaf",
    "client_for_surface", "grabbed_client", "top_client",
    "forward_pointer_motion", "rebase_pointer", "client_at", "surface_at",
]

# app.py bare external calls -> focus.X (post-delete counts). focus_unmanaged
# omitted (0 external callers; only apply_focus calls it).
QUALIFY = [
    ("focus_client", 5), ("apply_focus", 25), ("focus_lock", 1),
    ("grabbed_client", 4), ("top_client", 7), ("recent_tiled_leaf", 2),
    ("client_for_surface", 3), ("focused_tiled", 2), ("focused_container", 2),
    ("surface_at", 1), ("client_at", 3), ("forward_pointer_motion", 3),
    ("rebase_pointer", 2),
]

# tests: wel.NAME -> focus.NAME (whole-file occurrence counts).
WEL_REFS = [
    ("focus_client", 20), ("apply_focus", 18), ("focus_lock", 2),
    ("grabbed_client", 1), ("top_client", 3), ("client_for_surface", 1),
    ("forward_pointer_motion", 2), ("rebase_pointer", 4),
]

# tests: "welpy.app.NAME" patch targets -> "welpy.focus.NAME".
APP_STRINGS = [
    ("focus_client", 34), ("apply_focus", 25), ("top_client", 3),
    ("client_for_surface", 1), ("surface_at", 7), ("client_at", 2),
    ("forward_pointer_motion", 6), ("rebase_pointer", 4),
]

# Focus-subject tests, ordered to mirror FOCUS_ORDER.
MOVE = [
    "test_focus_client_order",
    "test_apply_focus_idle", "test_apply_focus_client", "test_apply_focus_shell",
    "test_apply_focus_releases", "test_apply_focus_clears",
    "test_apply_focus_handoff", "test_apply_focus_idempotent",
    "test_apply_focus_no_hold", "test_apply_focus_borders",
    "test_apply_focus_sticky", "test_apply_focus_priority",
    "test_apply_focus_pointer", "test_apply_focus_pointer_grab",
    "test_apply_focus_pointer_locked",
    "test_focus_lock_keyboard", "test_focus_lock_cleared",
    "test_xwayland_for_surface",
    "test_grabbed_client_multiple",
    "test_top_client_per_monitor", "test_top_client_empty",
    "test_pointer_motion_resets_default", "test_pointer_motion_keeps_cursor",
    "test_pointer_rebase_repoints", "test_pointer_rebase_matched",
    "test_pointer_rebase_clears", "test_pointer_rebase_empty",
]

FOCUS_HEADER = '''\
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
'''

TEST_HEADER = '''\
"""Unit tests for welpy.focus: focus policy and indicators, the focus/tile
queries, and pointer-focus hit-testing."""

from unittest.mock import MagicMock, patch

from welpy import app as wel, focus, model
from tests.helpers import (
    make_server, make_client, make_x11_client, make_monitor, make_workspace,
    make_cursor, make_layer_surface, make_session_lock,
)
'''


def main() -> None:
    _build_focus()
    _rewrite_app()
    _rewrite_tests()
    print(f"staged to {STAGE}")
    _warn_long("welpy/focus.py", "welpy/app.py",
               "tests/test_focus.py", "tests/test_app.py")


def _build_focus() -> None:
    body = r.extract_defs(_read("welpy/app.py"), FOCUS_ORDER)
    _write("welpy/focus.py", FOCUS_HEADER + "\n\n" + body)


def _rewrite_app() -> None:
    a = r.delete_defs(_read("welpy/app.py"), FOCUS_ORDER)
    a = r.replace(a, "from . import geometry\n",
                  "from . import focus\nfrom . import geometry\n", n=1)
    for name, n in QUALIFY:
        a = r.replace(a, name + "(", "focus." + name + "(", n=n)
    _write("welpy/app.py", a)


def _rewrite_tests() -> None:
    t = _read("tests/test_app.py")

    # promote make_session_lock to helpers
    helpers = _read("tests/helpers.py")
    msl = r.extract_defs(t, ["make_session_lock"])
    _write("tests/helpers.py", r.replace(
        helpers, "def trigger(", msl + "\n\ndef trigger(", n=1))

    for name, n in WEL_REFS:
        t = r.replace(t, "wel." + name, "focus." + name, n=n)
    for name, n in APP_STRINGS:
        t = r.replace(t, '"welpy.app.' + name + '"',
                      '"welpy.focus.' + name + '"', n=n)

    focus_tests = r.extract_defs(t, MOVE)
    focus_tests = r.replace(  # logger ref travels with grabbed_client
        focus_tests, '"welpy.app.logger"', '"welpy.focus.logger"', n=1)
    _write("tests/test_focus.py", TEST_HEADER + "\n\n" + focus_tests)

    rest = r.delete_defs(t, MOVE + ["make_session_lock"])
    rest = r.replace(
        rest,
        "from welpy import (\n"
        "    app as wel, bindings, ext_workspace, geometry, layout, libinput, "
        "model)\n",
        "from welpy import (\n"
        "    app as wel, bindings, ext_workspace, focus, geometry, layout, "
        "libinput,\n    model)\n", n=1)
    rest = r.replace(
        rest, "    make_keycode_map, make_layer_surface, trigger,\n)",
        "    make_keycode_map, make_layer_surface, make_session_lock, "
        "trigger,\n)", n=1)
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

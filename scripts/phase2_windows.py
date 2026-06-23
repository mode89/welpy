"""Phase-2 box-4 driver: carve welpy/windows.py out of app.py (transient).

Read-only on the repo; writes the transformed files to /tmp/welpy-windows/.
Run from the repo root: ``python scripts/phase2_windows.py``.
"""

import pathlib

import refactorlib as r

REPO = pathlib.Path(__file__).resolve().parent.parent
STAGE = pathlib.Path("/tmp/welpy-windows")

# Top-down: callers above callees; xdg lifecycle then popups (SPEC order).
WINDOWS_ORDER = [
    "client_new", "client_commit", "client_map", "client_unmap",
    "client_request_fullscreen", "client_request_maximize",
    "client_request_activate", "mark_urgent", "client_cleanup",
    "create_window_scene", "popup_new", "_popup_owner",
]

# app.py bare external calls -> windows.X (post-delete counts).
# client_request_maximize / create_window_scene / _popup_owner omitted
# (0 external callers; only moved fns call them).
QUALIFY = [
    ("client_new", 1), ("client_commit", 1), ("client_map", 1),
    ("client_unmap", 1), ("client_request_fullscreen", 1),
    ("client_request_activate", 1), ("mark_urgent", 2),
    ("client_cleanup", 1), ("popup_new", 1),
]

# tests: wel.NAME -> windows.NAME (whole-file occurrence counts).
WEL_REFS = [
    ("client_new", 7), ("client_commit", 11), ("client_map", 18),
    ("client_unmap", 12), ("client_request_fullscreen", 6),
    ("client_request_maximize", 3), ("client_request_activate", 2),
    ("client_cleanup", 1), ("popup_new", 8),
]

# tests: "welpy.app.NAME" patch targets -> "welpy.windows.NAME".
APP_STRINGS = [
    ("client_commit", 1), ("client_map", 2), ("client_unmap", 1),
    ("client_cleanup", 1), ("client_request_fullscreen", 1),
    ("client_request_maximize", 1), ("mark_urgent", 3), ("popup_new", 1),
]

# Window-subject tests, ordered to mirror WINDOWS_ORDER; _stage_popup (used
# only by the moving popup tests) travels in front of the popup band.
MOVE = [
    "test_client_new_no_insert", "test_client_new_commit", "test_client_new_map",
    "test_client_new_unmap", "test_client_new_destroy",
    "test_client_new_request_fullscreen", "test_client_new_request_maximize",
    "test_client_commit_initial", "test_client_commit_subsequent",
    "test_client_commit_clears", "test_client_commit_holds",
    "test_client_commit_initial_pending", "test_client_commit_reclips",
    "test_client_commit_postunmap", "test_client_commit_premap",
    "test_client_map_inserts_front", "test_client_map_subtree",
    "test_client_map_orders", "test_client_map_anchors_popups",
    "test_client_map_reasserts_decoration", "test_client_map_focuses",
    "test_client_map_tiled_once", "test_client_map_to_tile",
    "test_client_map_monitor_selected", "test_client_map_monitor_none",
    "test_client_map_floats_dialog", "test_client_map_no_parent",
    "test_client_map_adds_leaf", "test_client_map_unfullscreens_existing",
    "test_client_unmap_refocuses", "test_client_unmap_ends_grab",
    "test_client_unmap_alone", "test_client_unmap_lineage",
    "test_client_unmap_float_fallback", "test_client_unmap_clears_popup_anchor",
    "test_request_fullscreen_enters", "test_request_fullscreen_keeps_float",
    "test_request_fullscreen_to_tile", "test_request_fullscreen_to_float",
    "test_request_fullscreen_pre_map", "test_request_fullscreen_noop",
    "test_request_maximize_acks_initialized",
    "test_request_maximize_before_initialized",
    "test_request_maximize_configures",
    "test_urgent_marks", "test_urgent_skips_focused",
    "test_client_cleanup_drops",
    "_stage_popup",
    "test_popup_new_defers", "test_popup_new_initial_commit",
    "test_popup_new_non_initial_commit", "test_popup_new_no_parent_data",
    "test_popup_new_unconstrain", "test_popup_new_listeners_cleared",
    "test_popup_new_destroy_cleans_up", "test_popup_new_layer_owner",
]

WINDOWS_HEADER = '''\
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
'''

TEST_HEADER = '''\
"""Unit tests for welpy.windows: the xdg-shell window lifecycle (create, map,
unmap, fullscreen/maximize/activate requests) and transient popup placement."""

from unittest.mock import ANY, MagicMock, call, patch

from welpy import app as wel, geometry, windows
from tests.helpers import (
    make_server, make_client, make_monitor, make_workspace, make_layer_surface,
)
'''


def main() -> None:
    _build_windows()
    _rewrite_app()
    _rewrite_tests()
    print(f"staged to {STAGE}")
    _warn_long("welpy/windows.py", "welpy/app.py",
               "tests/test_windows.py", "tests/test_app.py")


def _build_windows() -> None:
    body = r.extract_defs(_read("welpy/app.py"), WINDOWS_ORDER)
    _write("welpy/windows.py", WINDOWS_HEADER + "\n\n" + body)


def _rewrite_app() -> None:
    a = r.delete_defs(_read("welpy/app.py"), WINDOWS_ORDER)
    a = r.replace(a, "from . import model\n",
                  "from . import model\nfrom . import windows\n", n=1)
    for name, n in QUALIFY:
        a = r.replace(a, name + "(", "windows." + name + "(", n=n)
    _write("welpy/app.py", a)


def _rewrite_tests() -> None:
    t = _read("tests/test_app.py")
    for name, n in WEL_REFS:
        t = r.replace(t, "wel." + name, "windows." + name, n=n)
    for name, n in APP_STRINGS:
        t = r.replace(t, '"welpy.app.' + name + '"',
                      '"welpy.windows.' + name + '"', n=n)

    windows_tests = r.extract_defs(t, MOVE)
    _write("tests/test_windows.py", TEST_HEADER + "\n\n" + windows_tests)

    rest = r.delete_defs(t, MOVE)
    rest = r.replace(
        rest,
        "    app as wel, bindings, ext_workspace, focus, geometry, layout, "
        "libinput,\n    model)",
        "    app as wel, bindings, ext_workspace, focus, geometry, layout, "
        "libinput,\n    model, windows)", n=1)
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

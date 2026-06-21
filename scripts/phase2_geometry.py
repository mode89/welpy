"""Phase-2 box-2 driver: carve welpy/geometry.py out of app.py (transient).

Read-only on the repo; writes the transformed files to /tmp/welpy-geometry/.
Run from the repo root: ``python scripts/phase2_geometry.py``.
"""

import pathlib

import refactorlib as r

REPO = pathlib.Path(__file__).resolve().parent.parent
STAGE = pathlib.Path("/tmp/welpy-geometry")

# Top-down: every function precedes the ones it calls (callers above callees).
GEOM_ORDER = [
    "apply_geometry", "apply_hierarchy", "apply_visibility", "apply_tree",
    "resize_client", "apply_clip",
    "set_size", "set_tiled", "set_fullscreen", "set_border_color",
    "set_activated", "_configure_x11", "_track_configure",
    "arrange_layers", "monitor_box", "place_in_layer_bucket",
    "decoration_new", "apply_decoration",
    "float_client", "init_floating_geom", "client_outer_rect",
    "client_layer", "client_geometry", "client_surface",
    "client_wants_fullscreen", "client_wants_float",
]

# app.py: bare outside calls to qualify (post-delete counts). Excludes
# set_size (handled below to dodge the wlr_*_set_size substring) and the
# three names with no outside callers (_track_configure, client_geometry,
# client_outer_rect).
QUALIFY = [
    ("apply_geometry", 17), ("apply_hierarchy", 7), ("apply_visibility", 7),
    ("apply_tree", 12), ("resize_client", 1), ("apply_clip", 1),
    ("set_tiled", 1), ("set_fullscreen", 10), ("set_border_color", 3),
    ("set_activated", 2), ("_configure_x11", 2), ("arrange_layers", 4),
    ("monitor_box", 4), ("place_in_layer_bucket", 1), ("decoration_new", 1),
    ("apply_decoration", 1), ("float_client", 3), ("init_floating_geom", 1),
    ("client_layer", 1), ("client_surface", 4), ("client_wants_fullscreen", 2),
    ("client_wants_float", 1),
]

# tests: wel.NAME -> geometry.NAME (whole-file occurrence counts).
WEL_REFS = [
    ("apply_geometry", 7), ("apply_hierarchy", 14), ("apply_visibility", 3),
    ("apply_tree", 5), ("resize_client", 5), ("set_size", 4), ("set_tiled", 2),
    ("set_fullscreen", 9), ("set_activated", 2), ("_track_configure", 3),
    ("arrange_layers", 1), ("monitor_box", 1), ("decoration_new", 5),
    ("apply_decoration", 3), ("init_floating_geom", 2), ("client_layer", 9),
    ("client_geometry", 1), ("client_surface", 1),
    ("client_wants_fullscreen", 1), ("client_wants_float", 1),
]

# tests: "welpy.app.NAME" patch targets -> "welpy.geometry.NAME".
APP_STRINGS = [
    ("apply_geometry", 52), ("apply_hierarchy", 2), ("apply_visibility", 2),
    ("apply_tree", 7), ("resize_client", 9), ("set_fullscreen", 3),
    ("arrange_layers", 9), ("monitor_box", 5), ("decoration_new", 1),
    ("apply_decoration", 1), ("client_outer_rect", 7),
]

# Geometry-subject tests + the decoration helper, ordered to mirror GEOM_ORDER.
MOVE = [
    "test_apply_geometry_single_full", "test_apply_geometry_row",
    "test_apply_geometry_other_monitor", "test_apply_geometry_skips_floating",
    "test_apply_geometry_sizes_fullscreen", "test_apply_geometry_empty",
    "test_apply_geometry_reconciles_float",
    "test_hierarchy_seed", "test_hierarchy_hotplug",
    "test_hierarchy_unplug_migrate", "test_hierarchy_rehome_occupied",
    "test_hierarchy_unplug_orphan", "test_hierarchy_unplug_repoint",
    "test_hierarchy_idempotent", "test_hierarchy_fullscreen_unmapped",
    "test_hierarchy_fullscreen_mismatch", "test_hierarchy_inactive_empty",
    "test_hierarchy_inactive_kept", "test_hierarchy_no_monitors",
    "test_hierarchy_active_repair",
    "test_apply_visibility_active", "test_apply_visibility_inactive",
    "test_apply_visibility_orphan",
    "test_apply_tree_clients", "test_apply_tree_skips_unmapped",
    "test_apply_tree_idempotent", "test_apply_tree_layer_surface",
    "test_apply_tree_popups_lifted",
    "test_resize_client_geometry", "test_resize_client_tracks",
    "test_resize_client_clips", "test_resize_client_fullscreen",
    "test_borders_resize",
    "test_set_size_tracks", "test_xwayland_set_size",
    "test_xwayland_position_only", "test_xwayland_size_unchanged_skips",
    "test_set_tiled_tracks", "test_xwayland_set_tiled",
    "test_fullscreen_slot_enters", "test_fullscreen_slot_exits",
    "test_fullscreen_slot_noop", "test_fullscreen_slot_replaces",
    "test_fullscreen_slot_keeps_float", "test_xwayland_set_fullscreen",
    "test_set_activated_no_hold", "test_xwayland_set_activated",
    "test_track_configure_acked", "test_track_configure_pending",
    "test_arrange_layers_shrinks_area",
    "test_monitor_box_returns_rect",
    "_make_deco",
    "test_decoration_new_forces_ssd", "test_decoration_new_before_initialized",
    "test_decoration_new_no_back_pointer",
    "test_decoration_request_mode_reasserts", "test_decoration_destroy_clears",
    "test_apply_decoration_forces", "test_apply_decoration_skips_uninitialized",
    "test_apply_decoration_skips_no_decoration",
    "test_init_floating_geom_centers", "test_init_floating_geom_fallback",
    "test_client_layer_tile", "test_client_layer_float",
    "test_client_layer_fullscreen",
    "test_xwayland_client_geometry", "test_xwayland_client_surface",
    "test_xwayland_wants_fullscreen", "test_xwayland_wants_float",
]

GEOM_HEADER = '''\
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
'''

TEST_HEADER = '''\
"""Unit tests for welpy.geometry: window sizing and placement, the tiling and
layer-shell arrangement, and the per-window geometry queries."""

from unittest.mock import MagicMock, call, patch

from welpy import app as wel, geometry, model
from tests.helpers import (
    make_server, make_client, make_x11_client, make_monitor, make_workspace,
    flat_tree, trigger, make_layer_surface,
)
'''

OLD_APP_IMPORT = (
    "from welpy import app as wel, bindings, ext_workspace, layout, libinput\n")
NEW_APP_IMPORT = (
    "from welpy import (\n"
    "    app as wel, bindings, ext_workspace, geometry, layout, libinput, "
    "model)\n")


def main() -> None:
    _build_geometry()
    _rewrite_app()
    _rewrite_tests()
    print(f"staged to {STAGE}")
    _warn_long("welpy/geometry.py", "welpy/app.py", "tests/test_geometry.py")


def _build_geometry() -> None:
    app = _read("welpy/app.py")
    body = r.extract_defs(app, GEOM_ORDER)
    _write("welpy/geometry.py", GEOM_HEADER + "\n\n" + body)


def _rewrite_app() -> None:
    a = r.delete_defs(_read("welpy/app.py"), GEOM_ORDER)
    a = r.replace(
        a, "    BORDER_WIDTH, Client, Cursor, DEFAULT_SCALE, Grab, "
        "KeyboardGroup,\n",
        "    Client, Cursor, DEFAULT_SCALE, Grab, KeyboardGroup,\n", n=1)
    a = r.replace(
        a, "from . import ext_workspace\n",
        "from . import ext_workspace\nfrom . import geometry\n", n=1)
    for name, n in QUALIFY:
        a = r.replace(a, name + "(", "geometry." + name + "(", n=n)
    a = r.replace(
        a, "set_size(server, client, 0, 0)",
        "geometry.set_size(server, client, 0, 0)", n=1)
    _write("welpy/app.py", a)


def _rewrite_tests() -> None:
    t = _read("tests/test_app.py")

    helpers = _read("tests/helpers.py")
    mls = r.extract_defs(t, ["make_layer_surface"])
    _write("tests/helpers.py", r.replace(
        helpers, "def trigger(", mls + "\n\ndef trigger(", n=1))

    t = r.replace(t, "wel.BORDER_WIDTH", "model.BORDER_WIDTH", n=15)
    for name, n in WEL_REFS:
        t = r.replace(t, "wel." + name, "geometry." + name, n=n)
    for name, n in APP_STRINGS:
        t = r.replace(
            t, '"welpy.app.' + name + '"', '"welpy.geometry.' + name + '"', n=n)

    geom_tests = r.extract_defs(t, MOVE)
    _write("tests/test_geometry.py", TEST_HEADER + "\n\n" + geom_tests)

    rest = r.delete_defs(t, MOVE + ["make_layer_surface"])
    rest = r.replace(rest, OLD_APP_IMPORT, NEW_APP_IMPORT, n=1)
    rest = r.replace(
        rest, "    make_keycode_map, trigger,\n)",
        "    make_keycode_map, make_layer_surface, trigger,\n)", n=1)
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

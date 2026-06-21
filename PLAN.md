# PLAN: Phase 2 box 2 — extract `welpy/geometry.py`

## Context

SPEC's Phase 2 carves `welpy/app.py` into a strict-DAG module split, one module
per commit. Box 1 (`model.py`) shipped. This box extracts the **geometry/arrange
band** — the second DAG layer (deps: `model`, `layout`) — into `welpy/geometry.py`.
Pure file move, no behavior change; each gate stays green.

Verified up front: **every** one of the 26 geometry functions references only
`model` types/constants, `layout`, `server.ffi/lib/listen`, a module `logger`, and
*other geometry functions* — none call back into focus/handlers/app. So the leaf
property holds and no import cycle forms (the `cyclic-import` pylint gate stays
clean). The two `model.clients_visible`/`model.client_monitor` calls box 1 already
qualified live inside two of these functions and travel with them.

Unlike box 1 (zero patch churn), geometry functions are **qualified-only** (not
re-exported into `app.py`), so every geometry reference in the tests must repoint —
this is the bulk of the work, and it touches many tests that *stay* in
`test_app.py`.

## Manifest — 26 functions to `geometry.py`

arrange/render: `apply_geometry`, `apply_hierarchy`, `apply_visibility`,
`apply_tree`, `apply_clip` · setters: `resize_client`, `set_border_color`,
`set_size`, `set_activated`, `set_tiled`, `set_fullscreen`, `_configure_x11`,
`_track_configure` · screen: `monitor_box`, `arrange_layers`,
`place_in_layer_bucket` · decoration: `decoration_new`, `apply_decoration` ·
client-geometry queries: `client_surface`, `client_geometry`,
`client_wants_fullscreen`, `client_wants_float`, `client_layer`,
`client_outer_rect`, `init_floating_geom`, `float_client`.

## `welpy/geometry.py` (new)

Header:
```python
"""Window geometry and layout: sizing/placing windows and their borders,
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
```
- `from . import model` — for `model.clients_visible` (in `apply_geometry`) and
  `model.client_monitor` (in `init_floating_geom`), kept qualified.
- `from . import layout` — `layout.walk`, `layout.remove`.
- 8 by-name `model` names cover all annotations/`isinstance`/constant refs.
- No `bindings` import (uses `server.ffi/lib/listen` at runtime), matching `model.py`.
- `logger` only feeds `_track_configure`'s defensive warning; its tests assert
  `pending_serial`, not log output, so `getLogger(__name__)` is safe.

Body: `extract_defs([...])` of the 26 names. `extract_defs` emits in the order
requested, so the list is written **top-down** — every function precedes the ones
it calls (callers above callees), grouped per the SPEC module map. Reordering
module-level `def`s is behavior-preserving (none run at import). Verified order
(call-graph checked):

1. arrange/render: `apply_geometry`, `apply_hierarchy`, `apply_visibility`,
   `apply_tree`, `resize_client`, `apply_clip`
2. setters: `set_size`, `set_tiled`, `set_fullscreen`, `set_border_color`,
   `set_activated`, `_configure_x11`, `_track_configure`
3. screen/layers: `arrange_layers`, `monitor_box`, `place_in_layer_bucket`
4. decoration: `decoration_new`, `apply_decoration`
5. client-geometry queries: `float_client`, `init_floating_geom`,
   `client_outer_rect`, `client_layer`, `client_geometry`, `client_surface`,
   `client_wants_fullscreen`, `client_wants_float`

(e.g. `apply_geometry`→`resize_client`→`set_size`→`_configure_x11`/
`_track_configure`; `float_client`→`client_outer_rect`→`client_geometry` — each
caller sits above its callee.)

## `welpy/app.py` edits

1. **Drop `BORDER_WIDTH`** from the `from .model import (...)` block — its only
   uses were geometry functions (all other model names stay; `SHELL_LAYERS`, border
   colors, `Rect`, `from . import model` all remain used by staying handlers).
2. **Add** `from . import geometry` (alphabetical: …`ext_workspace`, `geometry`,
   `layout`…).
3. `delete_defs([26 names])`.
4. **Qualify the remaining (outside) call sites** `NAME(` → `geometry.NAME(`, run
   *after* `delete_defs` (so only outside calls remain), each a count-checked
   `replace`:

   | n | fn | n | fn | n | fn |
   |---|----|---|----|---|----|
   |17|apply_geometry|7|apply_hierarchy|7|apply_visibility|
   |12|apply_tree|10|set_fullscreen|4|monitor_box|
   |4|arrange_layers|4|client_surface|3|set_border_color|
   |2|_configure_x11|2|set_activated|2|client_wants_fullscreen|
   |1|decoration_new|1|apply_decoration|1|place_in_layer_bucket|
   |1|resize_client|1|apply_clip|1|set_size|
   |1|set_tiled|1|client_wants_float|1|client_layer|
   |1|init_floating_geom| | | | |

   `_track_configure`, `client_geometry`, `client_outer_rect` have **0** outside
   callers (only same-module callees) — skip. The `apply_geometry` mention in
   `drag_client`'s docstring has no `(`, so the paren-anchored replace skips it.

## Test edits

All counts are on pristine `tests/test_app.py`; do the repoints on the full text
**first**, then split.

1. **Repoint refs** (count-checked `replace`, whole file):
   - `wel.BORDER_WIDTH` → `model.BORDER_WIDTH` (n=12) — forced by dropping the
     re-export. 7 travel to `test_geometry.py`, 5 stay (x11-configure tests).
   - `wel.NAME` → `geometry.NAME` per geometry name (totals: apply_hierarchy 14,
     set_fullscreen 9, client_layer 9, apply_geometry 7, decoration_new 5,
     apply_tree 5, resize_client 5, set_size 4, apply_visibility 3,
     apply_decoration 3, _track_configure 3, set_activated 2, set_tiled 2,
     init_floating_geom 2, monitor_box 1, arrange_layers 1, client_surface 1,
     client_geometry 1, client_wants_fullscreen 1, client_wants_float 1).
   - `patch("welpy.app.NAME"` → `patch("welpy.geometry.NAME"` per name (totals:
     apply_geometry 51, arrange_layers 9, resize_client 9, apply_tree 7,
     client_outer_rect 7, monitor_box 5, set_fullscreen 3, apply_hierarchy 2,
     apply_visibility 2, decoration_new 1, apply_decoration 1).
2. **Add imports** to `test_app.py` line 17: `geometry, model` (model for the 5
   staying `model.BORDER_WIDTH`; geometry for staying `geometry.NAME` setup-calls).
3. **Relocate 62 geometry-subject unit tests** to new `tests/test_geometry.py` via
   `extract_defs` + `delete_defs`. Selection rule: a test moves iff its
   subject-under-test is one of the 26 geometry functions. The move-list is
   **ordered to mirror `geometry.py`** (top-down by subject), so the new test file
   follows the same high-level-first order as the source:
   - **apply_geometry** (7): `test_apply_geometry_{single_full,row,other_monitor,skips_floating,sizes_fullscreen,empty,reconciles_float}`
   - **apply_hierarchy** (13): `test_hierarchy_*` (seed, hotplug, unplug_migrate, rehome_occupied, unplug_orphan, unplug_repoint, idempotent, fullscreen_unmapped, fullscreen_mismatch, inactive_empty, inactive_kept, no_monitors, active_repair)
   - **apply_visibility** (3): `test_apply_visibility_{active,inactive,orphan}`
   - **apply_tree** (5): `test_apply_tree_{clients,skips_unmapped,idempotent,layer_surface,popups_lifted}`
   - **resize_client** (5): `test_resize_client_{geometry,tracks,clips,fullscreen}`, `test_borders_resize`
   - **set_size / set_tiled / set_activated** (3): `test_set_size_tracks`, `test_set_tiled_tracks`, `test_set_activated_no_hold`
   - **set_fullscreen** (5): `test_fullscreen_slot_{enters,exits,noop,replaces,keeps_float}`
   - **_track_configure** (2): `test_track_configure_{acked,pending}`
   - **arrange_layers** (1): `test_arrange_layers_shrinks_area`
   - **monitor_box** (1): `test_monitor_box_returns_rect`
   - **decoration_new** (5): `test_decoration_new_{forces_ssd,before_initialized,no_back_pointer}`, `test_decoration_{request_mode_reasserts,destroy_clears}`
   - **apply_decoration** (3): `test_apply_decoration_{forces,skips_uninitialized,skips_no_decoration}`
   - **init_floating_geom** (2): `test_init_floating_geom_{centers,fallback}`
   - **client_layer** (3): `test_client_layer_{tile,float,fullscreen}`
   - **client queries, x11 branch** (4): `test_xwayland_{client_geometry,client_surface,wants_fullscreen,wants_float}`

   Explicitly **stay** (subject is not geometry): all `test_layout_*` (test the
   already-split `layout` module), `test_toggle_*`/`test_request_fullscreen_*`/
   `test_request_maximize_*`/`test_client_map_*` (commands/window handlers that
   merely stub geometry), `test_update_monitors_*` (output), `test_borders_present`
   (subject = `client_map`), `test_setup_decoration_*` (subject = `setup`).
4. **`test_geometry.py` header** (mirror `test_model.py`): module docstring,
   `from unittest.mock import MagicMock, call, patch`,
   `from welpy import app as wel, geometry, model`,
   `from tests.helpers import (...)` (the `make_*` builders the moved tests use).

## Driver & mechanics

`scripts/phase2_geometry.py` over `scripts/refactorlib.py`, mirroring
`phase2_model.py`: **read-only on the repo**, staging to `/tmp/welpy-geometry/`.
Build `geometry.py`; rewrite `app.py` (`delete_defs` → import edits → qualify
replaces); rewrite tests (repoints on full text → `extract_defs`/`delete_defs`
split). Every text edit is a count-asserted `replace`, so a wrong count throws
rather than mis-carving. Review the staged diff, copy back (**no `git mv`**), then
finish with surgical Pass-2 edits:
- Re-wrap any line the `geometry.` prefix (or `model.BORDER_WIDTH`) pushes past 80.
- Trim `test_app.py`/`test_geometry.py` headers to exactly what's used
  (pylint `unused-import` / `undefined-name`).

## Verification gate

- `nix-shell --run "pytest -q"` → **464 passed** (402 in `test_app.py`, 62 in
  `test_geometry.py`; total preserved).
- `nix-shell --run "pylint ."` → **10.00/10** (no `cyclic-import`, no
  `unused-import`).
- Imports resolve: `welpy.geometry`, `welpy.app`; `app.Server`/`app.Rect` still
  re-export; `geometry.apply_geometry`/`geometry.client_layer` resolve;
  `geometry.model.clients_visible` reachable.
- `python -m welpy` boots through all Python wiring, dying only at the headless
  wlroots C backend (expected segfault, not a regression).

## Critical files

- `welpy/geometry.py` (new, ~26 functions)
- `welpy/app.py` (drop `BORDER_WIDTH`, add `geometry` import, qualify ~87 sites)
- `tests/test_app.py` (repoint ~180 refs, add imports, remove 62 tests)
- `tests/test_geometry.py` (new, 62 tests)
- `scripts/phase2_geometry.py` (new throwaway driver)

## Risks

- **Move-list misclassification** — the `# --- apply_geometry` test section is
  mostly `test_layout_*` and command/handler tests; the 62 movers were selected
  individually by subject, not by section. The gate (green + counts) catches a
  wrong split.
- **`wel.BORDER_WIDTH` ripple** — dropping the re-export forces the n=12 repoint to
  `model.BORDER_WIDTH` and the `model` import in both test files; `wel.Rect`
  (76 refs) is unaffected (Rect stays re-exported).

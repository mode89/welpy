# Plan: Phase 2 box 1 — extract `welpy/model.py`

## Context

`welpy/app.py` (2806 lines) is the Phase-1 remainder of the old `wel.py`. Per
`SPEC.md`, Phase 2 peels responsibility modules off it in dependency order, one
module per commit, each keeping the system green. `model` is the base **leaf**
(data records + constants + pure lookups) that every later box imports, so it is
carved first — nothing else has to be revisited. **Pure file move, no behavior
change.**

Outcome: a new `welpy/model.py` holding the data model; `app.py` imports it; a
mirror `tests/test_model.py`; `pytest` + `pylint` + `python -m welpy` stay green.

## What moves (carve manifest)

From `welpy/app.py` → `welpy/model.py`:

- **Constants** (lines 27–33): `BORDER_WIDTH`, `OUTPUT_SCALE`, `DEFAULT_SCALE`,
  `BORDER_COLOR_ACTIVE/INACTIVE/URGENT`, `WORKSPACE_NAMES`.
- **`Layer`** enum (the z-order layers) + **`SHELL_LAYERS`** tuple (the layer-index
  list, lines 48–49).
- **Dataclasses**: `Grab`, `Workspace`, `Monitor`, `LayerSurface`, `Client`,
  `XdgClient`, `X11Client`, `Unmanaged`, `SessionLock`, `LockSurface`, `Cursor`,
  `PointerConstraint`, `KeyboardGroup`, `Server`.
- **Shared queries**: `clients_in`, `clients_visible`, `client_monitor` (lines
  1727–1741) — pure `Server`/`Client` lookups, no `layout`/`logger`/`bindings` use.

Nothing else in `welpy/` references these (verified: other matches are docstrings).

## New file: `welpy/model.py`

Header:
```python
"""Compositor data model: window/screen/state records, layout constants, and
the shared window-lookup queries."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any

from .layout import Rect
```
`Rect` is needed for the `Monitor.window_area` / `Client.floating_geom`
annotations (pylint checks annotation names even under `from __future__ import
annotations`). `model → layout` is acyclic (`layout` is a dependency sink).

Body order (mirrors current source): constants block, `Layer`, `SHELL_LAYERS`,
the 14 dataclasses (source order resolves all forward refs: `Monitor` before
`LayerSurface`/`LockSurface`/`Server`; `Cursor`/`KeyboardGroup` before `Server`),
then the 3 queries. `clients_visible` keeps its bare `clients_in` call
(same-module).

## Edits to `welpy/app.py`

1. **Remove** the moved code: the 14 dataclasses + `Layer` + 3 queries
   (`delete_defs`), and the constants block + `SHELL_LAYERS`+comment (literal
   `replace`).
2. **Drop now-unused stdlib imports**: `import enum`, `from dataclasses import
   dataclass`, `from typing import Any` (all their uses moved; verified no other
   `enum.`/`@dataclass`/`Any` remains). **Keep** `from .layout import Rect` (17
   other uses).
3. **Add imports** (grouped with the existing relative imports, alphabetical):
   ```python
   from . import model            # after `from . import libinput`
   from .model import (           # after `from .layout import Rect`
       BORDER_COLOR_ACTIVE, BORDER_COLOR_INACTIVE, BORDER_COLOR_URGENT,
       BORDER_WIDTH, Client, Cursor, DEFAULT_SCALE, Grab, KeyboardGroup,
       Layer, LayerSurface, LockSurface, Monitor, OUTPUT_SCALE,
       PointerConstraint, Server, SessionLock, SHELL_LAYERS, Unmanaged,
       Workspace, WORKSPACE_NAMES, X11Client, XdgClient,
   )
   ```
   Types + constants imported **by name** so every existing bare ref (`Server`,
   `Layer`, `BORDER_WIDTH`, …) and `wel.X` test ref keeps resolving with no sweep.
4. **Qualify the moved-query calls** (SPEC: moved fns called qualified so
   `@welpy.override(model.…)` chains): `clients_visible(` → `model.clients_visible(`
   (3 sites: 682, 1747, 2047); `client_monitor(` → `model.client_monitor(` (8
   sites: 896, 935, 967, 1323, 2239, 2516, 2563, 2584). No `clients_in` call
   remains in `app.py`.

## Tests

- **New `tests/test_model.py`**:
  ```python
  """Unit tests for welpy.model: shared client-lookup queries."""

  from welpy import model
  from tests.helpers import (
      make_server, make_client, make_monitor, make_workspace)
  ```
  Hosts the 5 query tests with `wel.` → `model.` (`clients_in`/`clients_visible`/
  `client_monitor`).
- **From `tests/test_app.py`**: `delete_defs` the 5 tests
  (`test_clients_in_filters`, `test_clients_visible_active`,
  `test_clients_visible_empty`, `test_client_monitor_derives`,
  `test_client_monitor_orphaned`); drop the now-orphaned
  `# --- workspaces: helpers ---` section header (~line 6492) if it no longer
  labels following tests (verify it doesn't belong to `test_urgent_marks`).

## Mechanical execution

Throwaway driver `scripts/phase2_model.py` over `scripts/refactorlib.py`
(`extract_defs`/`delete_defs`/`replace`, count-asserted), **read-only on the
repo**, staging the rewritten `welpy/model.py` + `welpy/app.py` +
`tests/test_app.py` + `tests/test_model.py` to `/tmp/welpy-model/`:

1. Read pristine `app.py`/`test_app.py`.
2. Build `model.py`: header + literal constants block + `extract_defs(["Layer"])`
   + literal `SHELL_LAYERS` block + `extract_defs([dataclasses…, queries])`
   (joined with the house `\n\n\n` two-blank-line separator).
3. Build `app.py`: `delete_defs(moved names)` → literal `replace`s removing the
   constants/`SHELL_LAYERS` blocks and the 3 dropped imports → insert the `model`
   imports → count-asserted `replace`s qualifying the query calls (n=3, n=8).
4. Build `tests/test_app.py`: `delete_defs(5 test names)` + drop orphan header.
5. Build `tests/test_model.py`: header + `extract_defs(5 test names)` from
   pristine `test_app.py`, then `replace` `wel.` → `model.` on the query calls.

Review the staged diff, copy back (no `git mv`), then **surgical finishing**:
wrap any line the `model.`-qualification pushed past 80 cols (e.g. line 2239
`area = model.client_monitor(client).window_area`) per the Phase-1 line-wrap
lesson; remove the staged-pyc noise from the diff. `scripts/` stays
`pylint`-ignored, deleted at landing.

## Verification (gate — must all pass)

```
nix-shell --run "pytest -q"        # 469 still pass (5 tests relocated, not lost)
nix-shell --run "pylint ."         # 10.00/10; catches any unused/undefined import
nix-shell --run "python -c 'import welpy.model, welpy.app; from welpy import override'"
nix-shell --run "python -m welpy"  # wiring import check
```
Spot-check: `python -c "from welpy import model; print(model.clients_in, model.Server, model.BORDER_WIDTH)"`
and `python -c "from welpy import app; print(app.Server is app.model.Server)"` (by-name
re-export identity).

## Critical files

- `welpy/app.py` — source of the carve (edits 1–4).
- `welpy/model.py` — **new**, the extracted data model.
- `tests/test_app.py` — remove 5 relocated tests.
- `tests/test_model.py` — **new**, mirror test file.
- `scripts/phase2_model.py` — **new** throwaway driver; reuses
  `scripts/refactorlib.py` (`extract_defs`/`delete_defs`/`replace`).

## Notes / observed (out of scope, not fixed here)

- `welpy/layout.py` module docstring still says "`wel.py` owns the window/workspace
  side" — stale post-rename; a Final-sweep doc fix, not this box.
- After landing this box, `SPEC.md` checklist `model.py` → `[x]`; `geometry.py`
  (deps: model, layout) is the next box.

# SPEC: split `wel.py` into a `welpy/` package

Throwaway working doc; delete with the landing commit.

## Goal

`wel.py` (2844 lines) and `tests.py` (7614 lines) are too large. Split source by
responsibility into a `welpy/` package and mirror the split in a `tests/` package.
**Pure file move — no behavior change.** Each step keeps the system working.

## Decisions (locked)

- **Package**: all source under `welpy/`; `tests/` stays at repo root (not shipped).
- **Carve grain**: by responsibility, into a strict dependency DAG (no callback
  injection), acyclic once shared client queries move to `model` and
  `key_bindings`/`modkey` to `app` (see *Why the layering is acyclic*).
- **`override`**: drop the bare `@wel.override` form; target-only
  `@welpy.override(module.hook)`. Exposed as `welpy.override`.
- **Entry point**: `python -m welpy` via `welpy/__main__.py`. Removes the
  `load_config` `sys.modules.setdefault("wel", ...)` alias hack (canonical package
  identity).
- **Imports**: intra-package **relative** (`from . import focus`); call hooks
  **qualified** (`focus.apply_focus`) so overrides chain; import **types by name**
  (`from .model import Server`).
- **Tests**: `tests/` package, harness in `tests/helpers.py`, mirror files
  `tests/test_<module>.py`; domain stagers travel with their domain. Run `pytest`.
- **Churn accepted**: tests + user configs updated to qualified refs;
  `AGENTS.md`/`MEMORY.md` updated. `tests.py` split into `tests/`; `wel.py` renamed to
  `welpy/app.py` and dissected by Phase 2 (not deleted).

## Target layout

Module contents are listed under *Module map*; here only roles/structure.

```
welpy/
  __init__.py        # public surface: re-exports override
  __main__.py        # thin entry: `from welpy.app import main; main()`
  app.py             # lifecycle + keybinding table (Phase 1: renamed wel.py; Phase 2 shrinks it here)
  model.py           # data model (leaf)
  geometry.py        # arrange/geometry (leaf)
  focus.py           # focus + hit-testing
  output.py          # protocol/event handlers:
  windows.py
  xwayland.py
  layer_shell.py
  session_lock.py
  input.py
  commands.py
  bindings.py        # moved unchanged:
  layout.py
  ext_workspace.py
  libinput.py
tests/
  __init__.py        # regular package (for `from tests.helpers import …`)
  helpers.py         # shared harness (make_*, trigger, flat_tree)
  test_<module>.py   # one per source module
pyproject.toml       # testpaths=["tests"], pythonpath=["."]
```

## Module map

Each entry: module — *(deps)* — contents.

- `model.py` — *(leaf)*
  - dataclasses: `Layer` (+ layer index list), `Grab`, `Workspace`, `Monitor`, `LayerSurface`, `Client`/`XdgClient`/`X11Client`, `Unmanaged`, `SessionLock`, `LockSurface`, `Cursor`, `PointerConstraint`, `KeyboardGroup`, `Server`
  - constants: `BORDER_WIDTH`, border colors, `WORKSPACE_NAMES`, `OUTPUT_SCALE`, `DEFAULT_SCALE`
  - shared client queries (pure `Server`/`Client` lookups, called from geometry+focus+handlers): `clients_in`, `clients_visible`, `client_monitor`
- `geometry.py` — *(model, layout)*
  - arrange/render: `apply_geometry/hierarchy/tree/visibility`, `apply_clip`
  - geometry/setters: `resize_client`, `set_border_color/size/activated/tiled/fullscreen`, `_configure_x11`, `_track_configure`, `monitor_box`
  - decoration: `decoration_new`, `apply_decoration`
  - layer geometry: `arrange_layers`, `place_in_layer_bucket`
  - client-geometry queries: `client_surface/geometry/wants_*/layer/outer_rect`, `init_floating_geom`, `float_client`
- `focus.py` — *(model, geometry, layout, ext_workspace)*
  - focus policy: `focus_client`, `apply_focus`, `focus_lock`, `focus_unmanaged`
  - queries: `top_client`, `recent_tiled_leaf`, `client_for_surface`, `focused_tiled/container`, `grabbed_client`
  - pointer focus / hit-testing: `surface_at`, `client_at`, `forward_pointer_motion`, `rebase_pointer`
- `output.py` — *(model, geometry, focus, session_lock, ext_workspace)*
  - `monitor_new/render/cleanup/request_state`, `update_monitors`, `output_power_set_mode`, `monitor_force_paint`, `client_holds_paint`, `client_rendered`
  - (`update_monitors` calls `session_lock.update_lock_*` and `ext_workspace.publish`)
- `windows.py` — *(model, geometry, focus)*
  - xdg lifecycle: `client_new/commit/map/unmap`, `client_request_fullscreen/maximize/activate`, `mark_urgent`, `client_cleanup`, `create_window_scene`
  - popups: `popup_new`, `_popup_owner`
- `xwayland.py` — *(model, geometry, focus, windows)*
  - `x11_surface_new`, `x11_request_configure/activate`, `x11_set_hints`, `x11_ready`, `unmanaged_new/map/configure/unmap/cleanup`
  - (`x11_surface_new` wires the `windows` lifecycle handlers as listeners)
- `layer_shell.py` — *(model, geometry, focus)*
  - `layer_surface_new/commit/unmap/cleanup`
- `session_lock.py` — *(model, geometry, focus)*
  - `lock_new`, `lock_surface_new/destroy`, `lock_unlock/destroy`, `destroy_lock`, `update_lock_background/surfaces`, `create_lock_background`
- `input.py` — *(model, geometry, focus)*
  - cursor: `cursor_motion/_absolute/button/axis/frame`, `create/destroy_cursor`
  - pointer motion + constraints: `process_pointer_motion`, `apply_pointer_constraint`, `confine_delta`, `set_active_constraint`, `constraint_new/destroy`, `constraint_warp_to_hint`
  - drag: `begin_dragging/resizing_client`, `drag_client`
  - keyboard/bindings: `build_keycode_map`, `create/destroy_keyboard_group`, `input_new`, `keyboard_key/modifiers`, `lookup_binding`, `toggle_passthrough`, `change_vt`
  - seat: `seat_set_selection/primary_selection/cursor`
- `commands.py` — *(model, geometry, focus)*
  - `focus_direction`, `move_direction`, `group_window`, `cycle_layout`, `toggle_fullscreen/floating`, `close_window`
  - workspace ops: `view_workspace`, `view_previous_workspace`, `move_client_to_workspace`, `assign_workspace_to_monitor`, `move_active_workspace_to_monitor`
- `app.py` — *(all)*
  - `main`, `load_config`, `setup` (listener wiring), `install_signals`, `autostart`, `spawn`, `teardown`, `terminate`, `renderer_lost`
  - keybinding table: `key_bindings`, `modkey` — references `commands` + lifecycle (`spawn`/`terminate`), so it sits at the DAG top (in `input` it would form an `app`↔`input` cycle); `modkey` is called only by `key_bindings`
- `__init__.py` — *(leaf)*
  - `override`, `_install`

Cross-handler edges — `output.update_monitors` → `session_lock.update_lock_*` +
`ext_workspace.publish`, and `xwayland.x11_surface_new` → `windows` handlers — are
resolved by extracting the callee module first (`session_lock` before `output`,
`windows` before `xwayland`); none are mutually cyclic.

## Why the layering is acyclic

`focus` looks like a mutual hub, but its cross-subsystem reach is mostly not
module-level coupling:

- `apply_focus`'s `focus_lock`/`focus_unmanaged` helpers read only `Server` fields
  (`session_lock`, `unmanaged_focus`, seat, keyboard_group) — they import nothing
  from the lock/xwayland subsystems, so they sit in `focus.py` cleanly.
- The geometry/arrange band depends only on `model` + `layout` **once the shared
  client queries `clients_in`/`clients_visible`/`client_monitor` move to `model`**
  (`apply_geometry` calls `clients_visible`; `init_floating_geom` calls
  `client_monitor`) — these are pure `Server`/`Client` lookups, not focus logic, so
  hosting them in `focus` (as first mapped) would create a real `geometry`↔`focus`
  import cycle. After the move, geometry calls no focus/pointer function; the lone
  `apply_focus` in that line range is in `update_monitors` (an `output`
  orchestrator), not geometry.
- Pointer-focus hit-testing (`surface_at`, `client_at`, `forward_pointer_motion`,
  `rebase_pointer`) lives in `focus.py`, so cursor handlers call **into** focus
  one-way (`input → focus`), not a cycle.

So `model → geometry → focus → handlers → app` is a true DAG needing no callback
injection. This matches the existing extracted modules, which are dependency
**sinks**: `layout`/`ext_workspace`/`libinput` never import `wel`, and
`ext_workspace` inverts the dependency via injected callbacks
(`on_activate`/`on_assign`) rather than calling back into the compositor — the
fallback pattern if any future module needs core behavior.

**Rejected**: a naive `windows/output/input/shell/focus/geometry` peer split (real
import cycles, since `focus` is mutually entangled with the pointer path); a facade
`wel` re-exporting every name (keeps `wel.X` working but no real boundary);
callback-injection-everywhere coupling (zero cycles but heavy wiring for ~140 fns).

## Override mechanism

```python
def override(target):          # in welpy/__init__.py, target-only
    if not callable(target):   # keep the guard: test_override_non_callable
        raise TypeError(...)
    home = sys.modules[target.__module__]
    return lambda fn: _install(home, target.__name__, target, fn)
```
`_install` unchanged (curries previous version as first arg; rewrites
`wrapper.__module__` so chains route correctly). Config example:
```python
import welpy
from welpy import focus
@welpy.override(focus.apply_focus)
def apply_focus(prev, server): ...
```
Test churn beyond ref-repointing: the bare-form override tests must be **rewritten
to the target form or deleted** (`test_override_form1_replaces`, `_chains_original`,
`test_autostart_overridable`, `test_override_chain_composes`), and the dropped
name-lookup path's tests removed (`test_override_unknown_name`,
`test_override_form2_moved_target`). Keep the `callable`/`__name__` guards so
`test_override_non_callable` / `test_override_callable_no_name` still pass.

## Execution plan

**Why incremental works**: the graph is a strict DAG, so extracting **leaves
first** (dependency order) means each extracted module references only
already-extracted modules — never `app` (the shrinking remainder) — so no
back-edge, no cycle.

Each box below is one commit. **Gate after every box**: `pytest` green +
`pylint .` clean + `python -m welpy` imports.

**Per-module recipe** (the `model`…`commands` boxes):
1. Move the module's functions out, ordered **top-down** in the new file (callers
   above callees, high-level first), not in `app.py`'s scattered source order.
   `extract_defs` emits in the order requested, and reordering module-level `def`s
   is behavior-preserving (none run at import), so pass the names already sorted.
2. Sweep `app` (the remainder) — bare calls to moved fns → qualified.
3. Move the module's tests into its mirror file, **mirroring the new module's
   function order** (top-down by subject); re-point its `wel.X` refs (the
   Phase-1 alias) → `module.X`; switch any `@wel.override` to the target form.

**Mechanical-first per box** (used for Phase 1; reuse where it helps): drive the
deterministic copy/replace/remove from a temporary `scripts/phase<N>.py` over the
shared `scripts/refactorlib.py` — AST `extract_defs`/`delete_defs` by **symbol name**
(a *move* = extract + delete; spans inferred, never hardcoded) + count-checked literal
`replace`s for the few text edits (imports etc. hardcoded inline, not parsed), so the
pass self-verifies. The driver is **read-only on the repo**, staging to
`/tmp/welpy-<box>/`; review the staged diff, copy back (**no `git mv`** — rename
detection preserves history), then finish with surgical content-addressed edits for the
judgment-only changes. `scripts/` is `pylint`-ignored and removed at landing.

- [x] **Phase 1 — packageize** (shipped): renamed `wel.py` → `welpy/app.py` (copy +
  git rename-detect, **no `git mv`**; Phase 2 peels modules off it); moved the other
  flat files (`bindings`/`layout`/`ext_workspace`/`libinput`) into `welpy/`, imports →
  relative; lifted `override`/`_install` into `__init__.py`; added `__main__.py`
  (`from welpy.app import main; main()`); dropped the `load_config`
  `sys.modules["wel"]` alias hack; added `pyproject.toml`; created `tests/`
  (`__init__.py` + `helpers.py`) and moved `tests.py` → `tests/test_app.py`. Per the
  spec-literal scope, the coupled changes landed now: **target-only `override`**, and
  test refs split — `wel.X` attribute refs ride `from welpy import app as wel`, but the
  281 `patch("wel.X")` strings became canonical `patch("welpy.app.X")`. No code moves
  between modules yet.
- [x] `model.py` — extracted (green + reviewed clean)
- [x] `geometry.py` — extracted (green + reviewed clean)
- [x] `focus.py` — extracted (green + reviewed clean)
- [ ] `windows.py`
- [ ] `xwayland.py`
- [ ] `layer_shell.py`
- [ ] `session_lock.py`
- [ ] `output.py`
- [ ] `input.py`
- [ ] `commands.py`
- [ ] **Final**: `welpy/app.py` now holds only lifecycle + the keybinding table; drop
  the transitional `from welpy import app as wel` test alias; update `AGENTS.md` (file
  list, launch cmd, override model, test naming/location) and `MEMORY.md` override
  notes; promote the *To Remember* entries into `MEMORY.md`.

The `model`…`commands` boxes are in extraction (dependency) order, satisfying the
two cross-handler constraints: `windows` before `xwayland`, `session_lock` before
`output`. (`key_bindings`/`modkey` live in `app`, created in Phase 1, so they aren't
a Phase-2 box.)

## Caveats

- "Working" = `pytest` green + `pylint` clean + package imports. The suite mocks
  the C bindings; a missed qualified-ref surfaces as `NameError`/undefined-name.
- `pylint` `cyclic-import` is an active gate (`.pylintrc` disables only
  `too-many-lines`), so any residual import cycle fails the per-box gate — the
  relocations below are load-bearing, not cosmetic.
- Two relocations off the first-pass map break otherwise-real cycles:
  `clients_in`/`clients_visible`/`client_monitor` → `model` (else `geometry`↔`focus`),
  and `key_bindings`/`modkey` → `app` (else `app`↔`input`).
- Placement micro-picks (already chosen): `arrange_layers`→geometry, constants→model,
  seat helpers→input, commands as its own module.
- Phase 1 needs `tests/__init__.py` (regular package) so `from tests.helpers import …`
  resolves under pytest's default prepend import mode; harness split must preserve the
  `make_server`/`make_keycode_map` invariants (every `Server` field + every bound key).

## To Remember

_Durable facts kept out of `MEMORY.md` while the split is unimplemented. They're
provisional — verify each against what actually shipped and revise before promoting
(e.g. a placement micro-pick or override detail may change during the work). On
landing, add the revised entries to `MEMORY.md` (sections noted), then delete this
spec._

- **(Architecture)** `apply_focus`'s cross-subsystem reach is mostly not module-level
  coupling: `focus_lock`/`focus_unmanaged` read only `Server` fields (no lock/xwayland
  imports). With the shared client queries (`clients_in`/`clients_visible`/
  `client_monitor`) hosted in `model`, the geometry/arrange band is a clean
  `model`+`layout` leaf, so focus/geometry/handlers layer acyclically with no
  callback injection.
- **(Conventions)** welpy's extracted modules are dependency sinks:
  `layout`/`ext_workspace`/`libinput` never import `wel`; `ext_workspace` inverts the
  dependency via injected callbacks (`on_activate`/`on_assign`). **Why:** keeps the
  import graph acyclic. **How to apply:** a new module needing core behavior takes a
  callback or stays a pure leaf, rather than importing the compositor.
- **(Decisions)** the split was a bottom-up (leaves-first) extraction so each new
  module referenced only already-extracted modules, never `wel` — no import cycle
  formed mid-split.

## Log

_Refactor-only working notes — observations from doing the boxes, for whoever resumes.
**Not** durable project context (that's `MEMORY.md`), and **not** promoted at landing
(unlike *To Remember*); deleted with this spec._

_Plan mode: editing `SPEC.md` and `MEMORY.md` while planning is fine — the "only edit
`PLAN.md`" restriction covers code/config under plan, not these working/memory docs._

**Phase 1 (packageize) — shipped (commit `d4c3fd4`), green:** 469 tests
pass (472 − 3 deleted override tests), `pylint .` 10/10, `import welpy.app` +
`welpy.override` ok; the six moved files show as git renames (history preserved). The
target-only `override` and `python -m welpy` decisions shipped here and are already in
`MEMORY.md` (so they were dropped from *To Remember* above).

- Mechanical pass = `scripts/phase1.py` over `scripts/refactorlib.py`
  (`extract_defs`/`delete_defs`/`truncate_at`/`replace`, count-asserted; `refactorlib`
  has its own `pytest` self-tests). Re-running reproduces the *semantic* state only —
  the line-wraps are manual finishing, not in the driver.
- Two driver bugs caught at the gate, fixed in `phase1.py`: (1) **don't drop `import
  functools`** — its `functools.partial` use is in the *kept*
  `test_override_callable_no_name`, not the deleted `test_override_unknown_name`; an
  import's last use is decided by the enclosing def, not grep line-proximity. (2)
  `patch("wel.` missed one `patch(` whose `"wel.apply_geometry"` string wraps to its own
  line — fixed with an explicit `"wel.apply_geometry"` replace (matching bare `"wel.`
  would also rewrite a docstring).
- Count substrings on the **pristine** source before any `delete_defs` (deleting first
  undercounts). Pinned for a re-run: `patch("wel.` ×281, `wel.override` ×16,
  `logger="wel"` ×1; 12 harness builders → `tests/helpers.py`; 3 obsolete override
  tests removed (472→469).
- The alias→canonical rename pushes ~27 `with patch(...)` lines past 80
  (`patch("welpy.app.X")` is +6 chars); wrap them in the surgical pass. The pre-existing
  100+ char lines stay clean — pylint exempts any line with a trailing `# pylint:` pragma.
- Deferred to **Final** (not done): `tests/test_app.py` still has the docstring
  `"""Unit tests for wel.py."""`; `welpy/layout.py`'s module docstring still says
  "`wel.py` owns the window/workspace side"; `AGENTS.md` still shows the bare
  `@wel.override` form, `pytest tests.py`, and `wel.py`/`tests.py` in its file list.

**Phase 2 (module carving) — box 1 `model.py` — done (green, reviewed clean):** carved
via `scripts/phase2_model.py`. Manifest = 7 constants, `Layer` + `SHELL_LAYERS`, 14
dataclasses (`Grab`…`Server`), the 3 queries
(`clients_in`/`clients_visible`/`client_monitor`); 5 query tests → `tests/test_model.py`.
One surgical line-wrap (a `model.client_monitor(...)` ternary crossed 80).

- `refactorlib`'s `extract_defs`/`delete_defs` resolve `def`/`class` only (`_DEFS` =
  FunctionDef/AsyncFunctionDef/ClassDef), so module-level constants and tuples
  (`BORDER_WIDTH`…, `SHELL_LAYERS`) move via literal `replace`, not the AST primitives —
  recurs in any box that relocates constants.
- Importing the moved types/constants **by name** into `app.py` needs no ref sweep: bare
  refs and `wel.X` test refs keep resolving via re-export; only moved *functions* are
  qualified (`model.X`) and their tests move to the mirror file. The cross-module
  qualified-call rule + its override-mechanism reason are now in `MEMORY.md`
  (Conventions), so Final won't need to re-derive them. (Tunable **constants** were later
  reversed to qualified reads too — see the constants note at the Log's end.)

**Phase 2 (module carving) — box 2 `geometry.py` — done (green, reviewed clean):** carved
via `scripts/phase2_geometry.py`. Manifest = 26 functions (arrange/setters/screen/
decoration/client-geometry queries), written **top-down** (callers above callees);
68 geometry-subject tests → `tests/test_geometry.py`. `app.py` 2592→2175 lines.

- `extract_defs` was changed to emit in **requested** order (it had sorted by source
  line) so a carved module can be written top-down; its self-test was updated, 8 self-
  tests green. Remaining boxes rely on this requested-order behavior.
- Literal-replace qualification landmine: a moved fn name can be a **suffix of a
  wlroots C call** — `set_size(` also matches `wlr_*_set_size(` in a *staying* fn.
  Qualified `set_size` via its unique full-arg literal so the wlr call wasn't corrupted.
  Watch this in remaining boxes: a substring collision can net the *expected* count yet
  corrupt, so the count-assert alone won't catch it — check `calls` vs word-boundary refs.
- Test-move selection = **subject rule**: a test moves iff its subject-under-test is a
  carved fn (incl. the 6 `test_xwayland_set_*`, which call a geometry setter as their
  action), whereas a test that merely *asserts* a carved fn (e.g.
  `client_layer(...)==Layer.X`) stays. 68 moved, not the 62 first estimated.
- Local test helpers travel by use: `_make_deco` (used only by moved decoration tests)
  moved into the mirror file; `make_layer_surface` (used by staying + 1 moved test) was
  promoted to `tests/helpers.py`.
- Driver reproduces the **semantic** state only — the `_configure_x11`→`configure_x11`
  rename, ~17 `with patch(...)` line-wraps (the `welpy.geometry` qualifier is +5 chars),
  4 inline `# pylint: disable=duplicate-code`, and one local `geometry`→`apply_geom` var
  rename are manual Pass-2 finishing (re-running regenerates the pre-rename names + long
  lines). Two durable rules from this box are now in `MEMORY.md` (so Final won't re-
  derive): the R0801 duplicate-code inline-disable policy (Conventions) and the
  cross-module `_private`→W0212 carve-rename (Gotchas).
- Deferred to **Final** (cosmetic): two now-thin section headers in `tests/test_app.py`
  read stale after their subject tests moved out — `# --- configure tracking ---` (fronts
  only `begin_dragging`/`client_commit` tests) and `# --- apply_decoration ---` (fronts
  only `test_client_map_reasserts_decoration`).

**Tunable constants read qualified (supersedes the box-1 'constants by-name' note):**
updating the user's config exposed that a config's `model.BORDER_WIDTH = 3` no longer
propagated — `geometry`/`app` imported the tunable constants (`BORDER_WIDTH`,
`BORDER_COLOR_*`, `WORKSPACE_NAMES`, `OUTPUT_SCALE`/`DEFAULT_SCALE`) **by name**, freezing
the value before the config runs. Fixed by reading them `model.X` qualified (dropped from
the by-name imports; 5 `wel.X`→`model.X` test repoints; a few +6-char lines wrapped).
Now an `AGENTS.md` rule: qualify customization points (override-hook functions **and**
tunable constants); **types** stay by-name and re-export as `wel.X` for the test bridge.
Remaining boxes: import model **types** by-name, read model **constants** qualified.

**Phase 2 (module carving) — box 3 `focus.py` — done (green, reviewed clean):** carved
via `scripts/phase2_focus.py`. Manifest = 14 functions written **top-down** in three
bands (focus policy / focus+tile queries / pointer hit-testing); 60 `focus.X`
qualifications in the `app.py` remainder; 27 subject-tests → `tests/test_focus.py`.
`welpy/focus.py` 290 lines; `app.py` 1901 lines (was 2175). Gate: 469 pass, pylint
10/10, `import welpy.focus` clean.

- **Deps wider than the first-pass Module map** (now corrected to `(model, geometry,
  layout, ext_workspace)`): `recent_tiled_leaf`/`focused_container` call `layout.*`,
  `apply_focus` calls `ext_workspace.publish`. Both extras are verified leaves/sinks
  (neither imports `app`/`focus`), so the DAG holds — a doc imprecision, not a redesign.
  `focus.py` also needs its own `logger = logging.getLogger(__name__)` (`grabbed_client`
  warns).
- **Mock-var-shadow landmine** (now a durable `MEMORY.md` Gotcha, so Final won't re-
  derive): adding `from welpy import focus` to `test_app.py` shadowed 19 pre-existing
  `patch(...) as focus:` locals — pylint W0621, plus `UnboundLocalError` in the 5
  `test_focus_direction_*` tests that also call the real `focus.focus_client(...)` for
  setup. Renamed every mock var to the patched symbol (`as focus_client`/`apply_focus`);
  the `as focus,` comma form needed a separate fix. The driver can't do this — it's
  Pass-2 (analogous to box-2's `geometry`→`apply_geom`).
- Test-move details: `make_session_lock` promoted to `tests/helpers.py` (used by 2 moved
  + 10 staying lock tests); `test_xwayland_for_surface` moved by the **subject rule**
  (action is `client_for_surface`, box-2 `test_xwayland_set_*` precedent); the single
  `patch("welpy.app.logger")` (in `test_grabbed_client_multiple`) repointed to
  `welpy.focus.logger` only on the moved content (`n=1`), not the many staying
  `welpy.app.logger` patches.
- Line-wraps were 12, not the ~2 predicted: the +`focus.` qualifier kept app.py sites
  ≤80, but the `"welpy.app.X"`→`"welpy.focus.X"` test-patch strings (+2 chars) pushed 12
  past 80. 2 inline `# pylint: disable=duplicate-code` (R0801) per policy. No `_private`
  rename (all 14 names already public). The orphaned `# --- apply_focus ---` header was
  removed now (not deferred to Final, unlike box-2's stale headers).

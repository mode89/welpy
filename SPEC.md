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
- `focus.py` — *(model, geometry)*
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
1. Move the module's functions out.
2. Sweep `app` (the remainder) — bare calls to moved fns → qualified.
3. Move the module's tests into its mirror file; re-point its `wel.X` refs (the
   Phase-1 alias) → `module.X`; switch any `@wel.override` to the target form.

- [ ] **Phase 1 — packageize**: `git mv wel.py welpy/app.py` wholesale (Phase 2 peels
  modules off it); move the other flat files (`bindings`/`layout`/`ext_workspace`/
  `libinput`) into `welpy/`, imports → relative; lift `override`/`_install` into
  `__init__.py`; add `__main__.py` (`from welpy.app import main; main()`); drop the
  `load_config` `sys.modules["wel"]` alias hack; add `pyproject.toml`; create `tests/`
  (`__init__.py` + `helpers.py`) and move `tests.py` in, aliasing `from welpy import
  app as wel` so its `wel.*` refs keep resolving until Phase 2 migrates them per
  module. No code moves between modules yet.
- [ ] `model.py`
- [ ] `geometry.py`
- [ ] `focus.py`
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
- **(Decisions)** `welpy.override` is target-only (`@welpy.override(module.hook)`);
  the bare `@wel.override` form was dropped because it installs into
  `sys.modules["wel"]` by name and silently fails once a hook moves out of `wel.py`,
  whereas the target form resolves `target.__module__`.
- **(Decisions)** the compositor launches via `python -m welpy` (`welpy/__main__.py`);
  canonical package identity removed the `load_config`
  `sys.modules.setdefault("wel", ...)` double-import alias hack that `wel.py`-as-script
  needed.
- **(Decisions)** the split was a bottom-up (leaves-first) extraction so each new
  module referenced only already-extracted modules, never `wel` — no import cycle
  formed mid-split.

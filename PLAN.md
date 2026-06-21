# PLAN — Phase 1: packageize `wel.py` into `welpy/`

## Context

`wel.py` (2844 lines) + `tests.py` (7614 lines, 472 tests) are too large. `SPEC.md`
plans a responsibility-split into a `welpy/` package mirrored by `tests/`, executed
incrementally. **Phase 1 is the packageize step**: relocate the flat modules into a
`welpy/` package and a `tests/` package, switch to a `python -m welpy` entry point,
and (per the chosen *spec-literal* scope) land the two coupled behavior changes now —
make `override` target-only and drop the `load_config` `import wel` alias hack. No
source functions move *between* modules yet (that's Phase 2); `wel.py` becomes
`welpy/app.py`, the single remainder Phase 2 will peel modules off.

Gate after the commit: `pytest` green + `pylint .` clean + `python -c "import welpy.app"`.

## Target tree (end of Phase 1)

```
welpy/    __init__.py (override/_install)  __main__.py  app.py (=renamed wel.py)
          bindings.py  layout.py  ext_workspace.py  libinput.py
tests/    __init__.py  helpers.py (shared harness)  test_app.py (=renamed tests.py)
scripts/  refactorlib.py + phase1.py   # transient Python; staged to /tmp; removed at landing (like SPEC.md)
pyproject.toml
```

## Verified facts (from code)

- Imports to relativize: `app.py` imports `bindings/ext_workspace/layout/libinput`
  + `from layout import Rect`; `bindings.py` imports `ext_workspace`+`libinput`.
  `layout`/`ext_workspace`/`libinput` are pure leaves (no edits).
- `bindings.build()` compiles to a tempdir + loads `_welpy_cffi` — cwd/`__file__`-
  independent; nothing heavy runs at import, so `import welpy.app` is side-effect-free.
- Test name-alias (`from welpy import app as wel`) is the pylint-correct bridge; a
  `sys.modules` conftest alias would pass at runtime but fail pylint `import-error`.
  → tests use **canonical imports** and must repoint the **281 `patch("wel.X")`**
  strings to `patch("welpy.app.X")` (the 950 `wel.X` attribute refs ride the alias).
- Renaming changes the module logger name `wel` → `welpy.app`: one test pins
  `logger="wel"` (tests.py:5395).
- Removing `override`/`_install` leaves `import functools` unused in `app.py` (drop it);
  `import sys` stays (used by `load_config`'s `sys.path.append`).
- Shared-harness symbols (moved to `helpers.py` **by name**, not line number):
  `make_server`, `make_bindings`, `make_client`, `make_x11_client`, `make_unmanaged`,
  `make_monitor`, `make_workspace`, `flat_tree`, `make_cursor`, `make_keyboard_group`,
  `make_keycode_map`, `trigger` (the self-contained top cluster, ~lines 23–216). Deep
  domain stagers (`make_layer_surface`, `make_session_lock`, `make_extws*`) stay in
  `test_app.py` (Phase 2 moves them).

## Execution — stage, review, apply (symbol-driven Python)

Mechanical transforms are driven by a temporary `scripts/phase1.py` over a shared
`scripts/refactorlib.py` (kept under `scripts/` for Phase 2 to reuse, removed at landing
with `SPEC.md`; `scripts/` added to `.pylintrc` `ignore-paths`). The lib operates on
**symbol names**, locating top-level `def`/`class` spans via `ast` (line numbers
inferred, never hardcoded), and every op **asserts its preconditions** (symbol found,
replace count matches) so the mechanical pass self-verifies. Drivers are **read-only on
the repo** — they stage the transformed tree to `/tmp/welpy-phase1/`. **No `git mv`:**
copy-back deletes the flat files and adds the package ones; git rename detection
preserves history.

`scripts/refactorlib.py` API (reused across every box — Phase 2's core op is a *move* =
`extract_defs` then `delete_defs`):
```python
extract_defs(src, names) -> str             # AST-copy named top-level def/class nodes (joined); src unchanged
delete_defs(src, names)  -> remaining        # AST-delete named top-level def/class nodes from src
truncate_at(src, name)   -> remaining        # drop from the `name` def/class to EOF
replace(src, old, new, n=1) -> remaining     # literal swap; asserts `old` occurs exactly n times
```
A *move* is `extract_defs` (write the new file) + `delete_defs` (cut from the old) —
two orthogonal symbol primitives, no tuple; both assert every name is found, and
`delete_defs` also does the standalone obsolete-test deletions. Each name resolves to
its top-level `def` **or** `class` node (incl. `@dataclass`) through one resolver — **no
separate `extract_class`**, since the AST span logic is identical (Phase 2's `model.py`
is ~16 dataclasses). The few text edits (imports, alias lines, the import block,
`patch`/logger subs) are **hardcoded literals** passed to `replace` — the set is tiny,
so no line-mapping or import parsing, just count-checked literal swaps.

### Pass 1 — mechanical: `scripts/phase1.py` → `/tmp/welpy-phase1/`

_Source:_
1. `wel.py`→`welpy/app.py`: hardcoded `replace` (n=1 each) for the four
   `import …`→`from . import …`, `from layout import Rect`→`from .layout import Rect`,
   dropping `import functools`, and removing the alias comment + `setdefault("wel"…)`
   line; `truncate_at('override')` drops `override`/`_install`/the `__main__` guard.
2. `bindings.py`→`welpy/bindings.py`: two hardcoded `replace` (n=1) for the sibling imports → `from . import …`.
3. `layout.py`/`ext_workspace.py`/`libinput.py` → `welpy/` verbatim.
4. Static new files: `welpy/__init__.py` (body below), `welpy/__main__.py`
   (`from .app import main` + `if __name__ == "__main__": main()`), empty `tests/__init__.py`,
   `pyproject.toml` (`[tool.pytest.ini_options]` testpaths=`["tests"]`, pythonpath=`["."]`).

_Tests (`tests.py`→`tests/test_app.py`):_
5. `tests/helpers.py` = header + `extract_defs(test_app, [<12 harness names>])`; then
   `test_app = delete_defs(test_app, [<same 12>])`. Header = `from unittest.mock import
   MagicMock` + the `welpy` modules the cluster uses.
6. `replace('patch("wel.', 'patch("welpy.app.', n=281)`;
   `replace('logger="wel"', 'logger="welpy.app"', n=1)`; `replace('wel.override',
   'welpy.override', n=<counted>)`.
7. `delete_defs(test_app, ['test_override_form1_replaces', 'test_override_unknown_name',
   'test_override_form2_moved_target'])`.
8. `replace` (n=1) the five-line `import …` block with `import welpy` +
   `from welpy import app as wel, bindings, ext_workspace, layout, libinput` +
   explicit `from tests.helpers import (…)`.

`welpy/__init__.py` body:
```python
def override(target):
    if not callable(target):
        raise TypeError(f"welpy.override expects a function; got {type(target).__name__}")
    name = target.__name__                 # AttributeError names __name__ for partials
    home = sys.modules[target.__module__]
    return lambda fn: _install(home, name, target, fn)
```
(`_install` unchanged from `app.py`, incl. the `wrapper.__module__` rewrite that
preserves chaining.)

### Review the staging dir (gate before touching the repo)

- Confirm the driver's asserts passed (every symbol found; replace counts: imports/alias n=1, patch n=281, logger n=1).
- `diff wel.py /tmp/welpy-phase1/welpy/app.py` → only the intended import/removal lines differ.
- `diff bindings.py …/welpy/bindings.py` → only the two relative-import lines.
- `diff tests.py …/tests/test_app.py` → only the subs, the import block, the moved harness, and the 3 deleted tests.
- Read `welpy/__init__.py`, `welpy/__main__.py`, `pyproject.toml`, `tests/helpers.py`;
  confirm the helpers header resolves every name the cluster uses.

### Apply + checkpoint

- Copy staged `welpy/ tests/ scripts/ pyproject.toml` into the repo; `git rm` the six
  flat files (`wel.py bindings.py layout.py ext_workspace.py libinput.py tests.py`);
  add `scripts/` to `.pylintrc` `ignore-paths`; `git add -A`.
- `python -c "import welpy.app"` clean; `pytest -q` red on **only** the 5-test island
  (3 bare-form override + 2 `load_config` configs) — else green.

### Pass 2 — surgical (content-addressed `Edit`, in-repo; judgment-only)

9. Rewrite the 3 bare-form override tests to target form (`@welpy.override(wel.X)`):
   `test_override_form1_chains_original`, `test_override_chain_composes`,
   `test_autostart_overridable`.
10. Rewrite the 2 `load_config` config strings to canonical imports
    (`import welpy.app` / `welpy.app.X`; sibling → `from welpy import app` +
    `@welpy.override(app.modkey)`).

## Critical files

- `scripts/refactorlib.py` + `scripts/phase1.py` (new, transient) — symbol-driven AST
  extract/delete + count-asserted literal replaces; stage to `/tmp/welpy-phase1/`.
- `.pylintrc` — add `scripts/` to `ignore-paths` (transient; reverted when `scripts/` is deleted at landing).
- `welpy/app.py` (renamed `wel.py`) — relative imports; remove override/`_install`/alias/`__main__`.
- `welpy/__init__.py` (new) — target-only `override`/`_install`.
- `welpy/__main__.py` (new), `welpy/bindings.py` (relative imports).
- `tests/test_app.py` (renamed `tests.py`) — imports, 281 patch strings, override + load_config tests, logger fix.
- `tests/helpers.py` (new), `tests/__init__.py` (new), `pyproject.toml` (new).

## Verification (run via pinned `nix-shell --run`)

- `nix-shell --run "pytest -q"` → **469** pass (472 − 3 deleted override tests), 0 fail.
- `nix-shell --run "pylint ."` → no new findings (only `too-many-lines` disabled).
- `nix-shell --run "python -c 'import welpy.app'"` → no error (relative-import wiring).
- Spot-check chaining: `test_override_chain_composes` still asserts `== 60`.

## Also: record the method in SPEC.md

The stage→review→apply technique is reusable for the Phase-2 boxes, so during
implementation add this short note under SPEC.md's *Execution plan* (after the
per-module recipe). _(Can't edit SPEC.md in plan mode — this is the draft to apply.)_

> **Mechanical-first per box** (used for Phase 1; apply to any box where it helps):
> drive deterministic copy/replace/remove from a temporary `scripts/phase<N>.py` over
> the shared `scripts/refactorlib.py` — AST `extract_defs`/`delete_defs` by **symbol
> name** (a *move* = extract + delete; line spans inferred, never hardcoded) +
> count-checked literal `replace`s for the few text edits (imports etc. hardcoded
> inline, not parsed), so the pass self-verifies. Scripts are **read-only on the repo**, staging to `/tmp/welpy-<box>/`;
> review the staged diff, copy back (**no `git mv`**; git rename detection preserves
> history), gate, then finish with surgical content-addressed edits for the
> judgment-only changes. `scripts/` is `pylint`-ignored and removed at landing.

## Out of scope (later)

- Phase 2 module carving (`model`…`commands`), per the SPEC checklist.
- **Final** (deferred per SPEC): `AGENTS.md`/`MEMORY.md` updates (incl. the now-removed
  `@wel.override` bare form in AGENTS *Customization*), deleting `SPEC.md`/`scripts/`/
  `session.jsonl`, promoting the *To Remember* entries. → AGENTS/MEMORY transiently
  describe the old override form until then; `scripts/` stays for Phase 2 reuse.

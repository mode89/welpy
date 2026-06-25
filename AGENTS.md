Wayland compositor written in Python on top of wlroots.

## Files

- `welpy/`: the compositor package.
  - `app.py`: compositor lifecycle (`main`/`setup`/`teardown`/…) + the `key_bindings`/`modkey` table.
  - `model.py`: data model — window/screen state dataclasses, layout constants, shared client-lookup queries (`clients_in`/`clients_visible`/`client_monitor`).
  - `geometry.py`: window sizing/placement/borders + tiling-tree & layer-shell arrangement.
  - `focus.py`: focus policy + pointer hit-testing.
  - `windows.py`: xdg-shell window/popup lifecycle.
  - `xwayland.py`: X11 + override-redirect surfaces.
  - `layer_shell.py`: layer-shell surface lifecycle.
  - `session_lock.py`: screen-lock lifecycle.
  - `output.py`: monitor/output band — the `reconcile` render orchestrator.
  - `input.py`: cursor/pointer/drag/keyboard/seat handling.
  - `commands.py`: the user-facing keybinding actions.
  - `bindings/`: inline cffi bindings to wlroots, split by feature (plumbing; see Bindings).
  - `layout.py`: pure tiling-tree operations; no compositor imports.
  - `ext_workspace.py`: ext-workspace-v1 protocol logic; a callback-driven leaf (bindings in `bindings/ext_workspace.py`).
  - `libinput.py`: libinput device configuration (bindings in `bindings/libinput.py`).
- `tests/`: unit tests mirroring source + shared `helpers.py`.
- `TODO.md`: planned features, ordered by priority.

The `welpy/` modules layer as an acyclic DAG `model → geometry → focus → windows → xwayland → layer_shell → session_lock → output → input → commands → app`: a module imports only earlier ones, so don't add a back-edge.

## Customization

Users customize welpy from `~/.config/welpy/config.py`, run at startup before the compositor is built, by monkey-patching its modules. `@welpy.override(module.hook)` swaps a module-level function (or a class method), currying the previous version as its first arg so overrides chain.

- Module-level functions are the extension surface — keep customizable behavior in one so it stays patchable, not inlined or nested in a closure.
- Spot new customization points: behavior encoding a user preference, policy, or aesthetic (keybindings, launched apps, colors, focus/placement rules, etc.). Expose what you write as a top-level hook; flag existing inlined cases instead of refactoring them. Trigger: a user would plausibly want to change it.
- A hook's signature is an API contract: reordering or inserting positional params silently breaks configs and chained overrides. Don't churn it.
- Hooks are genuine customization points, not every function — don't freeze ordinary helpers.
- Reference a customization point — an override-hook function or a tunable constant (`BORDER_WIDTH`, `BORDER_COLOR_*`, scales) — **qualified** (`model.BORDER_WIDTH`), never by name (`from .model import BORDER_WIDTH`): a config rebinds the attribute on the symbol's home module, which a qualified read sees but a by-name import — bound in the importer before the config loads — silently ignores. Types/classes stay by-name; configs construct with them, never rebind them.

## Bindings

- `bindings/` is one cffi compilation unit split across modules. `core` holds the shared cdef (base types) + the `Builder`/compile machinery; `__init__` defines `build`/`wl_list_for_each` (so `welpy.override` targets resolve to the `welpy.bindings` package); each feature module (`render`/`output`/`scene`/`shell`/`xwayland`/`input`/`layer_shell`/`session_lock`/`idle`, plus `ext_workspace`/`libinput` whose run-time logic stays in `welpy/<name>.py`) leads with `contribute(builder)` that appends its `_CDEF`/`_SOURCE`. Struct/type definitions live in `core` (defined once, parsed first); function decls, `#define`s, and `welpy_*` C glue live with their feature. A module needing its own protocol scanner calls `builder.scanner`/`enum_header` in its `contribute`, like `bindings/ext_workspace`.
- `welpy_*` C helpers are plumbing only — static-inline wrappers, alloc/free for opaque-sized structs, accessors for anonymous struct members. For regular named struct fields, declare the struct in the cdef and access from Python directly. Logic stays in Python.
- All listeners share one `extern "Python"` trampoline; `listen` routes by listener address.

## Testing

Run with `pytest`.

Name tests `test_<scenario>`, where `<scenario>` is 1-5 words describing the behavior under test — not the function under test (functions get renamed; scenarios don't). Related cases in a file share a leading prefix so they group together (e.g. `test_map_*`, `test_constraint_*`); the filename already names the module, so don't repeat it. Each source module `welpy/<m>.py` has a mirror `tests/test_<m>.py`; a test lives in the mirror of the module it exercises, and shared builders live in `tests/helpers.py`.

## Linting

Run `pylint .` and address what it flags.

## Docstrings

- Write for someone unfamiliar with Wayland/wlroots: prefer "window", "screen", "app" over "toplevel", "output", "client".
- Focus on the high-level purpose. Don't restate the field list of a dataclass or the implementation of a function.
- Keep docstrings to 1-2 lines. Push non-obvious semantics and implementation details into inline comments at the relevant line.

## Inline comments

- Prefer no comment. Only add one when the code can't speak for itself.
- Keep inline comments to a single line, focused on *why* or non-obvious semantics.

> **Memory — read first.** Read `MEMORY.md` at the start of each session, before your first response — it records facts about this project, its conventions, landmines, dead ends, and decision rationale you can't recover from the code. Skipping it risks repeating solved mistakes.

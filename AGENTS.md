Wayland compositor written in Python on top of wlroots.

## Files

- `wel.py`: entry point.
- `bindings.py`: inline cffi bindings.
- `tests.py`: unit tests.
- `TODO.md`: planned features, ordered by priority.

## Customization

Users customize welpy from `~/.config/welpy/config.py`, run at startup before the compositor is built, by monkey-patching its modules. `@wel.override` swaps a module-level function, currying the previous version as its first arg so overrides chain.

- Module-level functions are the extension surface — keep customizable behavior in one so it stays patchable, not inlined or nested in a closure.
- Spot new customization points: behavior encoding a user preference, policy, or aesthetic (keybindings, launched apps, colors, focus/placement rules, etc.). Expose what you write as a top-level hook; flag existing inlined cases instead of refactoring them. Trigger: a user would plausibly want to change it.
- A hook's signature is an API contract: reordering or inserting positional params silently breaks configs and chained overrides. Don't churn it.
- Hooks are genuine customization points, not every function — don't freeze ordinary helpers.
- Reference a customization point — an override-hook function or a tunable constant (`BORDER_WIDTH`, `BORDER_COLOR_*`, scales) — **qualified** (`model.BORDER_WIDTH`), never by name (`from .model import BORDER_WIDTH`): a config rebinds the attribute on the symbol's home module, which a qualified read sees but a by-name import — bound in the importer before the config loads — silently ignores. Types/classes stay by-name; configs construct with them, never rebind them.

## Bindings

- `welpy_*` C helpers are plumbing only — static-inline wrappers, alloc/free for opaque-sized structs, accessors for anonymous struct members. For regular named struct fields, declare the struct in the cdef and access from Python directly. Logic stays in Python.
- All listeners share one `extern "Python"` trampoline; `listen` routes by listener address.

## Testing

Run with `pytest tests.py`.

Name tests `test_<system>_<scenario>`, where `<system>` is 1-2 words for the subsystem under test and `<scenario>` is 1-2 words for the specific case.

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

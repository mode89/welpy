Wayland compositor written in Python on top of wlroots.

## Files

- `wel.py`: entry point.
- `bindings.py`: inline cffi bindings.
- `tests.py`: unit tests.
- `TODO.md`: planned features, ordered by priority.

## Bindings

- `welpy_*` C helpers are plumbing only — static-inline wrappers, alloc/free for opaque-sized structs, accessors for anonymous struct members. For regular named struct fields, declare the struct in the cdef and access from Python directly. Logic stays in Python.
- All listeners share one `extern "Python"` trampoline; `listen` routes by listener address.

## Testing

Run with `pytest tests.py`.

Name tests `test_<system>_<scenario>`, where `<system>` is 1-2 words for the subsystem under test and `<scenario>` is 1-2 words for the specific case.

## Linting

Run `pylint wel.py bindings.py tests.py` and address what it flags.

## Docstrings

- Write for someone unfamiliar with Wayland/wlroots: prefer "window", "screen", "app" over "toplevel", "output", "client".
- Focus on the high-level purpose. Don't restate the field list of a dataclass or the implementation of a function.
- Keep docstrings to 1-2 lines. Push non-obvious semantics and implementation details into inline comments at the relevant line.

## Inline comments

- Prefer no comment. Only add one when the code can't speak for itself.
- Keep inline comments to a single line, focused on *why* or non-obvious semantics.

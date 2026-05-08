Wayland compositor written in Python on top of wlroots.

## Files

- `wl.py`: entry point.
- `bindings.py`: inline cffi bindings.
- `tests.py`: unit tests.

## Bindings

- `pywl_*` C helpers are plumbing only — static-inline wrappers, alloc/free for opaque-sized structs, accessors for anonymous struct members. For regular named struct fields, declare the struct in the cdef and access from Python directly. Logic stays in Python.
- All listeners share one `extern "Python"` trampoline; `listen` routes by listener address.

## Testing

Run with `pytest tests.py`.

Name tests `test_<system>_<scenario>`, where `<system>` is 1-2 words for the subsystem under test and `<scenario>` is 1-2 words for the specific case.

## Linting

Run `pylint wl.py bindings.py tests.py` and address what it flags.

## Docstrings

- Write for someone unfamiliar with Wayland/wlroots: prefer "window", "screen", "app" over "toplevel", "output", "client".
- Focus on *why*, high-level purpose and non-obvious semantics. Don't restate the field list of a dataclass or the implementation of a function.
- If you feel the urge to document a field, put it as an inline comment on the field itself, not in the class docstring.

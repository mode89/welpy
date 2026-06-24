"""Inline cffi bindings to wlroots, split by feature group.

`core` holds the shared cdef (base types, plumbing) and the compile
machinery; sibling modules contribute their feature's cdef + C glue.
`build` and `wl_list_for_each` live here so `welpy.override` targets resolve
to the `welpy.bindings` package, as before the split."""

from types import SimpleNamespace

from . import core
from .core import Builder


def build():
    """Compile the cffi extension. Returns (ffi, lib, listen).
    Call once from main(); module-load itself is side-effect free."""
    ffi, lib = core.build_extension()

    # All trampolines share one registry: key -> (keepalive, callback).
    # `keepalive` is whatever cdata must outlive the C-side registration
    # (the wl_listener struct, or the ffi.new_handle passed as `data`).
    handles = {}

    def _key(ptr):
        return int(ffi.cast("uintptr_t", ptr))

    @ffi.def_extern()
    def _welpy_dispatch(wl_listener, data):
        entry = handles.get(_key(wl_listener))
        if entry is not None:
            entry[1](data)

    @ffi.def_extern()
    def _welpy_timer_dispatch(data):
        entry = handles.get(_key(data))
        if entry is not None:
            entry[1]()
        return 0

    @ffi.def_extern()
    def _welpy_signal_dispatch(signum, data):
        entry = handles.get(_key(data))
        if entry is not None:
            entry[1](signum)
        return 0

    def listen(signal, callback):
        """Register `callback(data)` on `signal`. Returns a handle with a
        `.remove()` method that detaches the underlying wl_listener.
        `.remove()` is safe to call more than once."""
        wl_listener = ffi.new("struct wl_listener *")
        wl_listener.notify = lib._welpy_dispatch  # pylint: disable=protected-access
        key = _key(wl_listener)
        handles[key] = (wl_listener, callback)
        lib.welpy_signal_add(signal, wl_listener)

        def remove():
            entry = handles.pop(key, None)
            if entry is not None:
                held, _cb = entry
                lib.wl_list_remove(ffi.addressof(held[0], "link"))

        return SimpleNamespace(remove=remove)

    def add_timer(event_loop, callback):
        """Register a wayland event-loop timer that calls `callback()` on
        each fire. Returns a handle with a `.remove()` method that detaches
        the underlying wl_event_source and the trampoline entry.
        `.remove()` is safe to call more than once.

        The returned handle also exposes `.update(milliseconds)` to (re)arm
        the timer."""
        data = ffi.new_handle(callback)
        key = _key(data)
        handles[key] = (data, callback)
        source = lib.wl_event_loop_add_timer(
            event_loop, lib._welpy_timer_dispatch, data,  # pylint: disable=protected-access
        )

        def remove():
            if handles.pop(key, None) is not None:
                lib.wl_event_source_remove(source)

        def update(milliseconds):
            lib.wl_event_source_timer_update(source, milliseconds)

        return SimpleNamespace(remove=remove, update=update, source=source)

    def add_signal(event_loop, signum, callback):
        """Register `callback(signum)` for `signum` on the wayland event
        loop (uses signalfd internally, so the signal is blocked in the
        process and dispatched cooperatively). Returns a handle with a
        `.remove()` method that detaches the underlying wl_event_source.
        `.remove()` is safe to call more than once."""
        data = ffi.new_handle(callback)
        key = _key(data)
        handles[key] = (data, callback)
        source = lib.wl_event_loop_add_signal(
            event_loop, signum, lib._welpy_signal_dispatch, data,  # pylint: disable=protected-access
        )

        def remove():
            if handles.pop(key, None) is not None:
                lib.wl_event_source_remove(source)

        return SimpleNamespace(remove=remove, source=source)

    return ffi, lib, listen, add_timer, add_signal


def wl_list_for_each(ffi, head, ctype, member):
    """Yield each `ctype *` linked into the intrusive list `head` through its
    `member` field (the libwayland `wl_list` iteration idiom)."""
    offset = ffi.offsetof(ctype, member)
    node = head.next
    while node != head:
        # container_of: step back from the embedded link to its owning struct.
        yield ffi.cast(ctype + " *", ffi.cast("char *", node) - offset)
        node = node.next

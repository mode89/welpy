"""Unit tests for welpy.bindings: the wl_list iterator and container_of
pointer helpers over the wlroots ABI."""

import cffi

from welpy import bindings


def test_wl_list_walks_in_order():
    """The wl_list iterator walks an intrusive list and recovers each owning
    struct from its embedded link, in order."""
    ffi = cffi.FFI()
    ffi.cdef(
        "struct wl_list { struct wl_list *prev; struct wl_list *next; };"
        "struct node { int v; struct wl_list link; };")
    head = ffi.new("struct wl_list *")
    nodes = [ffi.new("struct node *") for _ in range(3)]
    for i, node in enumerate(nodes):
        node.v = i + 1
    links = [ffi.addressof(n[0], "link") for n in nodes]
    chain = [head, *links, head]
    for i in range(1, len(chain) - 1):
        chain[i].prev = chain[i - 1]
        chain[i].next = chain[i + 1]
    head.next, head.prev = links[0], links[-1]

    got = [
        c.v for c in bindings.wl_list_for_each(
            ffi, head, "struct node", "link")]

    assert got == [1, 2, 3]


def test_wl_list_empty_yields_nothing():
    """An empty list -- its sentinel points back at itself -- yields nothing."""
    ffi = cffi.FFI()
    ffi.cdef("struct wl_list { struct wl_list *prev; struct wl_list *next; };")
    head = ffi.new("struct wl_list *")
    head.next, head.prev = head, head

    assert not list(
        bindings.wl_list_for_each(ffi, head, "struct wl_list", "prev"))

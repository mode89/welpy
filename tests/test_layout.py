"""Unit tests for welpy.layout: the pure tiling-tree operations -- walk,
insert/remove, wrap, cycle, adjacency, successor, and move."""

from welpy import layout


def test_walk_row():
    """A HORIZONTAL container splits its area into equal columns that sum
    exactly to the width."""
    a, b, c = object(), object(), object()
    root = layout.Container(layout.ContainerLayout.HORIZONTAL, [a, b, c])
    placed = list(layout.walk(root, layout.Rect(0, 0, 900, 600)))

    assert placed == [
        (a, layout.Rect(0, 0, 300, 600)),
        (b, layout.Rect(300, 0, 300, 600)),
        (c, layout.Rect(600, 0, 300, 600)),
    ]


def test_walk_nested():
    """A nested container subdivides only its own slice of the parent area."""
    a, b, c = object(), object(), object()
    inner = layout.Container(layout.ContainerLayout.VERTICAL, [b, c])
    root = layout.Container(layout.ContainerLayout.HORIZONTAL, [a, inner])
    placed = dict((id(k), v) for k, v in layout.walk(
        root, layout.Rect(0, 0, 800, 600)))

    assert placed[id(a)] == layout.Rect(0, 0, 400, 600)
    assert placed[id(b)] == layout.Rect(400, 0, 400, 300)
    assert placed[id(c)] == layout.Rect(400, 300, 400, 300)


def test_insert_after():
    """insert_sibling places the new leaf right after its target."""
    a, b, c = object(), object(), object()
    root = layout.Container(layout.ContainerLayout.HORIZONTAL, [a, b])
    layout.insert_sibling(root, a, c)

    assert root.children == [a, c, b]


def test_insert_append():
    """insert_sibling appends to the root when the target is None or absent."""
    a, b = object(), object()
    root = layout.Container(layout.ContainerLayout.HORIZONTAL, [a])
    layout.insert_sibling(root, None, b)

    assert root.children == [a, b]


def test_remove_promotes():
    """Removing a window that leaves its container with one sibling promotes
    that sibling, dropping the now-redundant container."""
    a, b, c = object(), object(), object()
    inner = layout.Container(layout.ContainerLayout.VERTICAL, [b, c])
    root = layout.Container(layout.ContainerLayout.HORIZONTAL, [a, inner])
    layout.remove(root, b)

    assert root.children == [a, c]


def test_remove_empty():
    """Removing the only window of a group drops the emptied container."""
    a, b = object(), object()
    root = layout.Container(layout.ContainerLayout.HORIZONTAL, [a, b])
    layout.wrap(root, a, layout.ContainerLayout.VERTICAL)
    layout.remove(root, a)

    assert root.children == [b]


def test_remove_unrelated():
    """Collapse touches only the removed window's ancestors, so a one-window
    group elsewhere survives an unrelated removal."""
    a, b, c = object(), object(), object()
    group = layout.Container(layout.ContainerLayout.VERTICAL, [a])
    root = layout.Container(layout.ContainerLayout.HORIZONTAL, [group, b, c])
    layout.remove(root, c)

    assert root.children[0] is group and group.children == [a]


def test_wrap_unwrap():
    """wrap nests a leaf one level deeper; unwrap splices the group back."""
    a, b = object(), object()
    root = layout.Container(layout.ContainerLayout.HORIZONTAL, [a, b])
    layout.wrap(root, a, layout.ContainerLayout.VERTICAL)
    group = root.children[0]

    assert isinstance(group, layout.Container) and group.children == [a]

    layout.unwrap(root, group)
    assert root.children == [a, b]


def test_cycle_flips():
    """cycle_layout toggles a container between HORIZONTAL and VERTICAL."""
    root = layout.Container(layout.ContainerLayout.HORIZONTAL, [object()])
    layout.cycle(root)
    assert root.layout == layout.ContainerLayout.VERTICAL
    layout.cycle(root)
    assert root.layout == layout.ContainerLayout.HORIZONTAL


def test_adjacent_sibling():
    """In a flat row the adjacent set is the single neighboring window."""
    a, b, c = object(), object(), object()
    root = layout.Container(layout.ContainerLayout.HORIZONTAL, [a, b, c])

    assert layout.adjacent_leaves(root, a, layout.Direction.RIGHT) == [b]
    assert layout.adjacent_leaves(root, c, layout.Direction.LEFT) == [b]


def test_adjacent_edge():
    """Nothing lies past an edge or along an axis no ancestor splits on, so the
    adjacent set is empty."""
    a, b = object(), object()
    root = layout.Container(layout.ContainerLayout.HORIZONTAL, [a, b])

    assert not layout.adjacent_leaves(root, b, layout.Direction.RIGHT)
    assert not layout.adjacent_leaves(root, a, layout.Direction.UP)


def test_adjacent_group():
    """A neighboring container contributes all its windows as candidates."""
    a, b, c = object(), object(), object()
    inner = layout.Container(layout.ContainerLayout.VERTICAL, [b, c])
    root = layout.Container(
        layout.ContainerLayout.HORIZONTAL, [a, inner])

    assert layout.adjacent_leaves(root, a, layout.Direction.RIGHT) == [b, c]


def test_successor_siblings():
    """In a flat row the successor is the highest-ranked other window."""
    a, b, c = object(), object(), object()
    root = layout.Container(layout.ContainerLayout.HORIZONTAL, [a, b, c])
    rank = {a: 1, b: 3, c: 2}

    assert layout.successor(root, a, rank.get) is b


def test_successor_inner():
    """The innermost enclosing group wins: a grouped window's successor is a
    groupmate, even when a higher-ranked window sits outside the group."""
    a, b, c = object(), object(), object()
    inner = layout.Container(layout.ContainerLayout.VERTICAL, [b, c])
    root = layout.Container(layout.ContainerLayout.HORIZONTAL, [a, inner])
    rank = {a: 9, b: 1, c: 2}

    assert layout.successor(root, b, rank.get) is c


def test_successor_climbs():
    """When the innermost group holds no one else, the climb skips it and
    picks from the next ancestor."""
    a, b = object(), object()
    inner = layout.Container(layout.ContainerLayout.VERTICAL, [b])
    root = layout.Container(layout.ContainerLayout.HORIZONTAL, [a, inner])

    assert layout.successor(root, b, lambda n: 0) is a


def test_successor_alone():
    """A sole window, or one absent from the tree, has no successor."""
    a = object()
    root = layout.Container(layout.ContainerLayout.HORIZONTAL, [a])

    assert layout.successor(root, a, lambda n: 0) is None
    assert layout.successor(root, object(), lambda n: 0) is None


def test_container_parent():
    """container_of returns the parent and index of a window by identity."""
    a, b = object(), object()
    inner = layout.Container(layout.ContainerLayout.VERTICAL, [a, b])
    root = layout.Container(layout.ContainerLayout.HORIZONTAL, [inner])

    assert layout.container_of(root, b) == (inner, 1)
    assert layout.container_of(root, object()) is None


def test_move_reorder():
    """Moving a window toward a leaf sibling reorders it past that sibling
    within the same container."""
    a, b, c = object(), object(), object()
    root = layout.Container(layout.ContainerLayout.HORIZONTAL, [a, b, c])

    layout.move(root, a, layout.Direction.RIGHT)

    assert root.children == [b, a, c]


def test_move_pops_out():
    """Moving a window past the edge of its container pops it out beside that
    container in the parent, collapsing the container it vacated."""
    a, f, b = object(), object(), object()
    inner = layout.Container(layout.ContainerLayout.VERTICAL, [a, f])
    root = layout.Container(
        layout.ContainerLayout.HORIZONTAL, [inner, b])

    layout.move(root, f, layout.Direction.RIGHT)

    assert root.children == [a, f, b]


def test_move_descends():
    """Moving a window into an adjacent container descends into it, entering a
    perpendicular container at the front."""
    f, b, c = object(), object(), object()
    inner = layout.Container(layout.ContainerLayout.VERTICAL, [b, c])
    root = layout.Container(
        layout.ContainerLayout.HORIZONTAL, [f, inner])

    layout.move(root, f, layout.Direction.RIGHT)

    assert root.children == [inner]
    assert inner.children == [f, b, c]


def test_move_perp():
    """Moving a window out of a perpendicular container pops it into the parent
    beside that container, which keeps its remaining windows."""
    a, b, f, c = object(), object(), object(), object()
    inner = layout.Container(layout.ContainerLayout.VERTICAL, [b, f, c])
    root = layout.Container(
        layout.ContainerLayout.HORIZONTAL, [a, inner])

    layout.move(root, f, layout.Direction.LEFT)

    assert root.children == [a, f, inner]
    assert inner.children == [b, c]


def test_move_edge():
    """Moving a window toward the outer edge of the root is a no-op."""
    a, f = object(), object()
    root = layout.Container(layout.ContainerLayout.HORIZONTAL, [a, f])

    layout.move(root, f, layout.Direction.RIGHT)

    assert root.children == [a, f]


def test_move_root_perp():
    """Moving along an axis no ancestor splits on is a no-op."""
    a, f = object(), object()
    root = layout.Container(layout.ContainerLayout.VERTICAL, [a, f])

    layout.move(root, f, layout.Direction.RIGHT)

    assert root.children == [a, f]


def test_move_escape_after_parent():
    """At the edge of its container, a window escapes its immediate parent one
    level up, toward the move side; the drained single-child group collapses."""
    a, b, f = object(), object(), object()
    inner = layout.Container(layout.ContainerLayout.VERTICAL, [b, f])
    root = layout.Container(
        layout.ContainerLayout.HORIZONTAL, [a, inner])

    layout.move(root, f, layout.Direction.DOWN)

    assert root.children == [a, b, f]


def test_move_escape_before_parent():
    """Escaping toward the up/left side lands the window before its parent."""
    a, b, f = object(), object(), object()
    inner = layout.Container(layout.ContainerLayout.VERTICAL, [f, b])
    root = layout.Container(
        layout.ContainerLayout.HORIZONTAL, [inner, a])

    layout.move(root, f, layout.Direction.UP)

    assert root.children == [f, b, a]


def test_move_escape_keeps_parent():
    """A parent left with more than one child survives the escape."""
    a, b, c, f = object(), object(), object(), object()
    inner = layout.Container(layout.ContainerLayout.VERTICAL, [b, c, f])
    root = layout.Container(
        layout.ContainerLayout.HORIZONTAL, [a, inner])

    layout.move(root, f, layout.Direction.DOWN)

    assert root.children == [a, inner, f]
    assert inner.children == [b, c]


def test_move_escape_one_level():
    """The escape rises only one level: a deeply nested window lands in its
    grandparent, not the root."""
    a, x, y, f = object(), object(), object(), object()
    h2 = layout.Container(layout.ContainerLayout.HORIZONTAL, [y, f])
    v1 = layout.Container(layout.ContainerLayout.VERTICAL, [x, h2])
    root = layout.Container(
        layout.ContainerLayout.HORIZONTAL, [a, v1])

    layout.move(root, f, layout.Direction.DOWN)

    assert root.children == [a, v1]
    assert v1.children == [x, y, f]


def test_move_reorder_left():
    """Moving left reorders a window past its left-hand leaf sibling within the
    same container (negative-step reorder)."""
    a, b, c = object(), object(), object()
    root = layout.Container(layout.ContainerLayout.HORIZONTAL, [a, b, c])

    layout.move(root, c, layout.Direction.LEFT)

    assert root.children == [a, c, b]


def test_move_reorder_down():
    """Moving DOWN in a column reorders a window past the one below it."""
    a, b, c = object(), object(), object()
    root = layout.Container(layout.ContainerLayout.VERTICAL, [a, b, c])

    layout.move(root, a, layout.Direction.DOWN)

    assert root.children == [b, a, c]


def test_move_pops_up():
    """Moving UP pops a window out of its nested row into the column."""
    x, a, f = object(), object(), object()
    row = layout.Container(layout.ContainerLayout.HORIZONTAL, [a, f])
    root = layout.Container(layout.ContainerLayout.VERTICAL, [x, row])

    layout.move(root, f, layout.Direction.UP)

    assert root.children == [x, f, a]


def test_move_descend_front():
    """Descending into a same-axis container from the low side enters it at the
    front."""
    f, y, z = object(), object(), object()
    inner = layout.Container(layout.ContainerLayout.HORIZONTAL, [y, z])
    root = layout.Container(
        layout.ContainerLayout.HORIZONTAL, [f, inner])

    layout.move(root, f, layout.Direction.RIGHT)

    assert root.children == [inner]
    assert inner.children == [f, y, z]


def test_move_descend_back():
    """Descending into a same-axis container from the high side enters it at
    the back."""
    y, z, f = object(), object(), object()
    inner = layout.Container(layout.ContainerLayout.HORIZONTAL, [y, z])
    root = layout.Container(
        layout.ContainerLayout.HORIZONTAL, [inner, f])

    layout.move(root, f, layout.Direction.LEFT)

    assert root.children == [inner]
    assert inner.children == [y, z, f]


def test_move_climbs_ancestors():
    """A window with no room nearby climbs past several ancestors to the first
    matching-axis one with a neighbor, popping out there and collapsing the
    chain it left behind."""
    a, c, f, b = object(), object(), object(), object()
    inner = layout.Container(layout.ContainerLayout.HORIZONTAL, [c, f])
    column = layout.Container(layout.ContainerLayout.VERTICAL, [a, inner])
    root = layout.Container(
        layout.ContainerLayout.HORIZONTAL, [column, b])

    layout.move(root, f, layout.Direction.RIGHT)

    assert root.children == [column, f, b]
    assert column.children == [a, c]


def test_move_popout_keeps_container():
    """Popping out of a multi-window container leaves that container in place
    with its remaining windows."""
    a, b, f, x = object(), object(), object(), object()
    inner = layout.Container(layout.ContainerLayout.HORIZONTAL, [a, b, f])
    root = layout.Container(
        layout.ContainerLayout.HORIZONTAL, [inner, x])

    layout.move(root, f, layout.Direction.RIGHT)

    assert root.children == [inner, f, x]
    assert inner.children == [a, b]

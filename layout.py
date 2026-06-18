"""Pure tile-tree layout: window arrangement as structure and geometry, with
no compositor state.

A workspace's windows form an n-ary tree of `Container` nodes; the leaves are
the windows themselves. Leaves are opaque -- this module only tells a
`Container` apart from a leaf via `isinstance`, never reading a leaf's fields --
so it never imports the compositor's `Client`. `wel.py` owns the
window/workspace side and calls these helpers on each `Workspace.root`.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


@dataclass(frozen=True)
class Rect:
    """Axis-aligned rectangle in layout coordinates."""
    x: int
    y: int
    width: int
    height: int


class ContainerLayout(enum.Enum):
    """How a container arranges its children: side by side or stacked."""
    HORIZONTAL = enum.auto()
    VERTICAL = enum.auto()


class Direction(enum.Enum):
    """A screen direction for directional focus and window movement."""
    LEFT = enum.auto()
    RIGHT = enum.auto()
    UP = enum.auto()
    DOWN = enum.auto()


@dataclass
class Container:
    """A tiling node: an ordered list of windows and sub-containers that split
    the node's area equally along one axis."""
    layout: ContainerLayout
    children: list


def walk(node, area):
    """Yield `(leaf, rect)` for every window under `node`, splitting `area`
    equally among children along each container's axis."""
    n = len(node.children)
    for i, child in enumerate(node.children):
        rect = _split(area, node.layout, i, n)
        if isinstance(child, Container):
            yield from walk(child, rect)
        else:
            yield child, rect


def leaves(node):
    """Every window under `node`, left to right."""
    result = []
    for child in node.children:
        if isinstance(child, Container):
            result.extend(leaves(child))
        else:
            result.append(child)
    return result


def container_of(root, node):
    """The `(parent, index)` holding `node`, or None if `node` isn't in the
    tree (or is `root` itself). Matches by identity, so it locates a window or
    a sub-container alike."""
    path = _path(root, node)
    return path[-1] if path is not None else None


def adjacent_leaves(root, focused, direction):
    """The windows in the group structurally next to `focused` one step in
    `direction`: climb to the nearest ancestor that splits on the move axis
    with a sibling that way, and return that sibling's windows (a lone window
    yields just itself). Empty at an edge, where nothing lies that way."""
    path = _path(root, focused)
    if path is None:
        return []
    axis = (
        ContainerLayout.HORIZONTAL
        if direction in (Direction.LEFT, Direction.RIGHT)
        else ContainerLayout.VERTICAL
    )
    step = -1 if direction in (Direction.LEFT, Direction.UP) else 1
    found = _neighbor(path, axis, step)
    if found is None:
        return []
    ancestor, _, j = found
    neighbor = ancestor.children[j]
    return leaves(neighbor) if isinstance(neighbor, Container) else [neighbor]


def insert_sibling(root, target, leaf):
    """Insert `leaf` right after `target` in `target`'s container. If `target`
    is None or absent, append `leaf` to `root`."""
    found = container_of(root, target) if target is not None else None
    if found is None:
        root.children.append(leaf)
    else:
        parent, i = found
        parent.children.insert(i + 1, leaf)


def remove(root, leaf):
    """Remove `leaf`, then tidy its ancestor chain bottom-up: drop containers
    left empty and promote any left with a single child. Collapse runs only
    here, so a group made by `wrap` survives until a window is removed."""
    path = _path(root, leaf)
    if path is None:
        return
    parent, i = path[-1]
    del parent.children[i]
    _collapse(path)


def wrap(root, leaf, container_layout):
    """Replace `leaf` with a new single-child container, grouping it one level
    deeper."""
    found = container_of(root, leaf)
    if found is None:
        return
    parent, i = found
    parent.children[i] = Container(container_layout, [leaf])


def unwrap(root, container):
    """Dissolve `container`, splicing its children into its parent at its spot.
    No-op on the root."""
    found = container_of(root, container)
    if found is None:
        return
    parent, i = found
    parent.children[i:i + 1] = container.children


def cycle_layout(container):
    """Flip a container between arranging its windows side by side and
    stacked."""
    container.layout = (
        ContainerLayout.VERTICAL
        if container.layout == ContainerLayout.HORIZONTAL
        else ContainerLayout.HORIZONTAL)


def move(root, leaf, direction):
    """Relocate `leaf` one step in `direction`: reorder past a sibling, descend
    into an adjacent container, or pop out at an edge. Purely structural; a
    no-op only when `leaf` is a direct child of `root`."""
    path = _path(root, leaf)
    if path is None:
        return
    axis = (
        ContainerLayout.HORIZONTAL
        if direction in (Direction.LEFT, Direction.RIGHT)
        else ContainerLayout.VERTICAL
    )
    step = -1 if direction in (Direction.LEFT, Direction.UP) else 1
    found = _neighbor(path, axis, step)
    if found is None:
        _escape(path, leaf, step)
        return
    ancestor, i, j = found
    parent, ileaf = path[-1]
    neighbor = ancestor.children[j]
    if ancestor is parent and not isinstance(neighbor, Container):
        ancestor.children.pop(i)
        ancestor.children.insert(j, leaf)
        return
    del parent.children[ileaf]
    if isinstance(neighbor, Container):
        # facing end for a matching axis, index 0 for a perpendicular one
        end = neighbor.layout == axis and step < 0
        neighbor.children.insert(len(neighbor.children) if end else 0, leaf)
    else:
        ancestor.children.insert(i + 1 if step > 0 else i, leaf)
    _collapse(path)


def _split(area, container_layout, i, n):
    """The i-th of n equal slices of `area` along the axis; slices sum exactly
    to the area, leaving no gap or overflow."""
    if container_layout == ContainerLayout.HORIZONTAL:
        x0 = area.x + (i * area.width) // n
        x1 = area.x + ((i + 1) * area.width) // n
        return Rect(x0, area.y, x1 - x0, area.height)
    y0 = area.y + (i * area.height) // n
    y1 = area.y + ((i + 1) * area.height) // n
    return Rect(area.x, y0, area.width, y1 - y0)


def _path(node, target):
    """The chain of `(container, index)` pairs from `node` down to `target`:
    each container's child at that index is the next step down, and the last
    pair's child is `target`. None if `target` isn't under `node`."""
    for i, child in enumerate(node.children):
        if child is target:
            return [(node, i)]
        if isinstance(child, Container):
            below = _path(child, target)
            if below is not None:
                return [(node, i), *below]
    return None


def _index(children, node):
    """Index of `node` in `children` by identity (leaves needn't support
    equality), or None."""
    for i, child in enumerate(children):
        if child is node:
            return i
    return None


def _collapse(path):
    """Tidy `path` bottom-up: drop containers left empty and promote any left
    with a single child. The root (`path[0]`) is never collapsed."""
    for k in range(len(path) - 1, 0, -1):
        node, above = path[k][0], path[k - 1][0]
        j = _index(above.children, node)
        if len(node.children) == 0:
            del above.children[j]
        elif len(node.children) == 1:
            above.children[j] = node.children[0]


def _escape(path, leaf, step):
    """Pop `leaf` out of its immediate parent into the grandparent, on the
    `step` side -- the edge case where no ancestor along `path` can carry it
    further. No-op when that parent is the root, with nowhere left to go."""
    if len(path) < 2:
        return
    grandparent, iparent = path[-2]
    parent, ileaf = path[-1]
    del parent.children[ileaf]
    grandparent.children.insert(iparent + 1 if step > 0 else iparent, leaf)
    _collapse(path)


def _neighbor(path, axis, step):
    """Climbing from the leaf, the first ancestor in `path` that splits on
    `axis` and has a sibling slot `step` away: `(ancestor, i, j)` where the
    leaf descends through `ancestor.children[i]` and the sibling sits at `j`.
    None when every such ancestor holds the leaf at its facing edge."""
    for ancestor, i in reversed(path):
        if ancestor.layout != axis:
            continue
        j = i + step
        if 0 <= j < len(ancestor.children):
            return ancestor, i, j
    return None

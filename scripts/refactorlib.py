"""Symbol-driven source transforms for the welpy refactor (transient tooling).

Copy, delete, or truncate top-level ``def``/``class`` blocks by name (spans
inferred from the AST), plus a count-checked literal ``replace``.
"""

import ast

import pytest

_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def extract_defs(src: str, names: list[str]) -> str:
    """Named top-level def/class blocks in the requested order; src untouched."""
    nodes = _resolve(src, names)
    lines = src.split("\n")
    spans = [_span(nodes[name]) for name in names]
    blocks = ["\n".join(lines[start - 1:end]) for start, end in spans]
    return "\n\n\n".join(blocks) + "\n"  # two blank lines between top-level defs


def delete_defs(src: str, names: list[str]) -> str:
    """Return src with the named top-level def/class blocks removed."""
    nodes = _resolve(src, names)
    lines = src.split("\n")
    drop = set()
    for name in names:
        start, end = _span(nodes[name])
        # also consume the blank lines that followed, so no widening gap is left
        while end < len(lines) and not lines[end].strip():
            end += 1
        drop.update(range(start, end + 1))
    return "\n".join(line for i, line in enumerate(lines, 1) if i not in drop)


def truncate_at(src: str, name: str) -> str:
    """Src up to the named top-level def/class, dropping the rest."""
    start, _ = _span(_resolve(src, [name])[name])
    head = src.split("\n")[:start - 1]
    while head and not head[-1].strip():
        head.pop()
    return "\n".join(head) + "\n"


def replace(src: str, old: str, new: str, n: int = 1) -> str:
    """Literal old->new; assert old occurs n times (n=None skips the check)."""
    count = src.count(old)
    if n is not None and count != n:
        raise ValueError(f"replace: {old!r} occurs {count} times, expected {n}")
    return src.replace(old, new)


def _resolve(src: str, names: list[str]) -> dict:
    nodes = {
        node.name: node
        for node in ast.parse(src).body
        if isinstance(node, _DEFS)
    }
    missing = [name for name in names if name not in nodes]
    if missing:
        raise ValueError(f"top-level def/class not found: {missing}")
    return nodes


def _span(node) -> tuple[int, int]:
    decos = node.decorator_list
    start = decos[0].lineno if decos else node.lineno
    return start, node.end_lineno


# --- tests: run with `pytest scripts/refactorlib.py` ---

_SAMPLE = '''import os


@deco
def alpha(x):
    return x


class Box:
    def m(self):
        return 1


def beta():
    return 2


if __name__ == "__main__":
    alpha(1)
'''


def test_extract_given_order():
    """Extraction follows the requested name order and includes decorators."""
    expected = (
        "def beta():\n"
        "    return 2\n"
        "\n"
        "\n"
        "@deco\n"
        "def alpha(x):\n"
        "    return x\n"
    )
    assert extract_defs(_SAMPLE, ["beta", "alpha"]) == expected


def test_extract_class():
    """A class extracts together with its methods as one block."""
    expected = "class Box:\n    def m(self):\n        return 1\n"
    assert extract_defs(_SAMPLE, ["Box"]) == expected


def test_delete_removes_block():
    """Deleting a def also drops its trailing blanks, leaving no gap."""
    out = delete_defs(_SAMPLE, ["alpha"])
    assert "def alpha" not in out and "@deco" not in out
    assert out.startswith("import os\n\n\nclass Box:")


def test_delete_missing_raises():
    """An unknown name is rejected so a typo can't silently no-op."""
    with pytest.raises(ValueError):
        delete_defs(_SAMPLE, ["nope"])


def test_truncate_drops_tail():
    """Truncation drops the named def and everything after it."""
    out = truncate_at(_SAMPLE, "beta")
    assert "def beta" not in out and "__main__" not in out
    assert out.endswith("        return 1\n")


def test_replace_counts():
    """A literal swap touches exactly the asserted number of spots."""
    assert replace("a.x a.y", "a.", "b.", n=2) == "b.x b.y"


def test_replace_wrong_count_raises():
    """A count mismatch is rejected so a stale literal fails loudly."""
    with pytest.raises(ValueError):
        replace("a.x", "a.", "b.", n=2)


def test_replace_none_skips():
    """n=None swaps every occurrence without asserting a count."""
    assert replace("a a a", "a", "b", n=None) == "b b b"

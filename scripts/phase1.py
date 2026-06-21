"""Phase-1 packageize driver: stage the welpy/ + tests/ tree (transient tooling).

Read-only on the repo; writes the transformed tree to /tmp/welpy-phase1/.
Run from the repo root: ``python scripts/phase1.py``.
"""

import pathlib
import shutil

import refactorlib as r

REPO = pathlib.Path(__file__).resolve().parent.parent
STAGE = pathlib.Path("/tmp/welpy-phase1")

HARNESS = [
    "make_server", "make_bindings", "make_client", "make_x11_client",
    "make_unmanaged", "make_monitor", "make_workspace", "flat_tree",
    "make_cursor", "make_keyboard_group", "make_keycode_map", "trigger",
]
OBSOLETE = [
    "test_override_form1_replaces", "test_override_unknown_name",
    "test_override_form2_moved_target",
]

ALIAS_BLOCK = (
    "    # Alias so config's `import wel` finds us instead of a second"
    " instance.\n"
    '    sys.modules.setdefault("wel", sys.modules[__name__])\n'
)

IMPORT_OLD = (
    "import bindings\n"
    "import ext_workspace\n"
    "import layout\n"
    "import libinput\n"
    "import wel\n"
)

IMPORT_NEW = """\
import welpy
from welpy import app as wel, bindings, ext_workspace, layout, libinput
from tests.helpers import (
    make_server, make_bindings, make_client, make_x11_client, make_unmanaged,
    make_monitor, make_workspace, flat_tree, make_cursor, make_keyboard_group,
    make_keycode_map, trigger,
)
"""

HELPERS_HEADER = '''\
"""Shared test harness: builders for the compositor's data types, plus a
helper to deliver a wlroots event to its registered callback."""

from unittest.mock import MagicMock

from welpy import app as wel, layout
'''

INIT_PY = '''\
"""welpy: the Wayland compositor, plus the user-facing override hook."""

import functools
import sys


def override(target):
    """Replace a function with one receiving the previous version as its first
    argument, so user overrides chain. Use as ``@welpy.override(target)``."""
    if not callable(target):
        raise TypeError(
            f"welpy.override expects a function; got {type(target).__name__}")
    name = target.__name__
    home = sys.modules[target.__module__]
    return lambda fn: _install(home, name, target, fn)


def _install(module, name, target, fn):
    """Install `fn` at `module.name`, with `target` curried as first arg."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return fn(target, *args, **kwargs)
    # Rewrite so a later @welpy.override(wrapper) routes here, not to fn's
    # original module.
    wrapper.__module__ = module.__name__
    setattr(module, name, wrapper)
    return wrapper
'''

MAIN_PY = '''\
"""Entry point: ``python -m welpy`` boots the compositor."""

from .app import main

if __name__ == "__main__":
    main()
'''

PYPROJECT = '''\
[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
'''


def main():
    if STAGE.exists():
        shutil.rmtree(STAGE)

    app = _read("wel.py")
    for mod in ("bindings", "ext_workspace", "layout", "libinput"):
        app = r.replace(app, f"import {mod}\n", f"from . import {mod}\n")
    app = r.replace(app, "from layout import Rect\n", "from .layout import Rect\n")
    app = r.replace(app, "import functools\n", "")
    app = r.replace(app, ALIAS_BLOCK, "")
    app = r.truncate_at(app, "override")
    _write("welpy/app.py", app)

    binds = _read("bindings.py")
    for mod in ("ext_workspace", "libinput"):
        binds = r.replace(binds, f"import {mod}\n", f"from . import {mod}\n")
    _write("welpy/bindings.py", binds)

    for leaf in ("layout.py", "ext_workspace.py", "libinput.py"):
        _write(f"welpy/{leaf}", _read(leaf))

    _write("welpy/__init__.py", INIT_PY)
    _write("welpy/__main__.py", MAIN_PY)

    tests = _read("tests.py")
    _write("tests/helpers.py",
           HELPERS_HEADER + "\n\n" + r.extract_defs(tests, HARNESS))

    t = r.replace(tests, 'patch("wel.', 'patch("welpy.app.', n=281)
    # the one patch target whose string is wrapped onto its own line
    t = r.replace(t, '"wel.apply_geometry"', '"welpy.app.apply_geometry"')
    t = r.replace(t, 'logger="wel"', 'logger="welpy.app"', n=1)
    t = r.replace(t, "wel.override", "welpy.override", n=16)
    t = r.delete_defs(t, HARNESS)
    t = r.delete_defs(t, OBSOLETE)
    t = r.replace(t, IMPORT_OLD, IMPORT_NEW)
    _write("tests/test_app.py", t)
    _write("tests/__init__.py", "")

    _write("pyproject.toml", PYPROJECT)
    print(f"staged to {STAGE}")


def _read(rel: str) -> str:
    return (REPO / rel).read_text()


def _write(rel: str, content: str) -> None:
    path = STAGE / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


if __name__ == "__main__":
    main()

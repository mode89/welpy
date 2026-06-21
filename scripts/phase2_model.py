"""Phase-2 box-1 driver: carve welpy/model.py out of app.py (transient tooling).

Read-only on the repo; writes the transformed files to /tmp/welpy-model/.
Run from the repo root: ``python scripts/phase2_model.py``.
"""

import pathlib

import refactorlib as r

REPO = pathlib.Path(__file__).resolve().parent.parent
STAGE = pathlib.Path("/tmp/welpy-model")

DATACLASSES = [
    "Grab", "Workspace", "Monitor", "LayerSurface", "Client", "XdgClient",
    "X11Client", "Unmanaged", "SessionLock", "LockSurface", "Cursor",
    "PointerConstraint", "KeyboardGroup", "Server",
]
QUERIES = ["clients_in", "clients_visible", "client_monitor"]
TESTS = [
    "test_clients_in_filters", "test_clients_visible_active",
    "test_clients_visible_empty", "test_client_monitor_derives",
    "test_client_monitor_orphaned",
]

MODEL_HEADER = '''\
"""Compositor data model: window/screen/state records, layout constants, and
the shared window-lookup queries."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any

from .layout import Rect
'''

CONSTANTS = '''\
BORDER_WIDTH = 2
OUTPUT_SCALE = {}  # screen name -> scale factor; e.g. {"eDP-1": 2.0}
DEFAULT_SCALE = 1.0
BORDER_COLOR_ACTIVE = (0.0, 0.5, 1.0, 1.0)
BORDER_COLOR_INACTIVE = (0.3, 0.3, 0.3, 1.0)
BORDER_COLOR_URGENT = (0.9, 0.0, 0.0, 1.0)
WORKSPACE_NAMES = ("1", "2", "3", "4", "5", "6", "7", "8", "9", "10")
'''

SHELL_LAYERS = '''\
# Indexed by zwlr_layer_shell_v1 layer values (0..3) to map them to Layer.
SHELL_LAYERS = (Layer.BACKGROUND, Layer.BOTTOM, Layer.TOP, Layer.OVERLAY)
'''

APP_MODEL_IMPORT = """\
from .model import (
    BORDER_COLOR_ACTIVE, BORDER_COLOR_INACTIVE, BORDER_COLOR_URGENT,
    BORDER_WIDTH, Client, Cursor, DEFAULT_SCALE, Grab, KeyboardGroup,
    Layer, LayerSurface, LockSurface, Monitor, OUTPUT_SCALE,
    PointerConstraint, Server, SessionLock, SHELL_LAYERS, Unmanaged,
    Workspace, WORKSPACE_NAMES, X11Client, XdgClient,
)
"""

TEST_HEADER = '''\
"""Unit tests for welpy.model: shared client-lookup queries."""

from welpy import model
from tests.helpers import (
    make_server, make_client, make_monitor, make_workspace)
'''


def main() -> None:
    app = _read("welpy/app.py")
    layer = r.extract_defs(app, ["Layer"])
    rest = r.extract_defs(app, DATACLASSES + QUERIES)

    model = (
        MODEL_HEADER + "\n\n" + CONSTANTS + "\n\n" + layer + "\n\n"
        + SHELL_LAYERS + "\n\n" + rest)
    _write("welpy/model.py", model)

    remove_block = CONSTANTS + "\n\n" + layer + "\n\n" + SHELL_LAYERS + "\n\n"
    a = r.delete_defs(app, DATACLASSES + QUERIES)
    a = r.replace(a, remove_block, "", n=1)
    a = r.replace(a, "import enum\n", "", n=1)
    a = r.replace(a, "from dataclasses import dataclass\n", "", n=1)
    a = r.replace(a, "from typing import Any\n", "", n=1)
    a = r.replace(
        a, "from . import libinput\n",
        "from . import libinput\nfrom . import model\n", n=1)
    a = r.replace(
        a, "from .layout import Rect\n",
        "from .layout import Rect\n" + APP_MODEL_IMPORT, n=1)
    a = r.replace(a, "clients_visible(", "model.clients_visible(", n=3)
    a = r.replace(a, "client_monitor(", "model.client_monitor(", n=8)
    _write("welpy/app.py", a)

    tests = _read("tests/test_app.py")
    _write("tests/test_app.py", r.delete_defs(tests, TESTS))

    model_tests = r.replace(
        r.extract_defs(tests, TESTS), "wel.", "model.", n=5)
    _write("tests/test_model.py", TEST_HEADER + "\n\n" + model_tests)

    print(f"staged to {STAGE}")
    for rel in ("welpy/model.py", "welpy/app.py"):
        for i, line in enumerate((STAGE / rel).read_text().split("\n"), 1):
            if len(line) > 80:
                print(f"  LONG {rel}:{i} ({len(line)}): {line}")


def _read(rel: str) -> str:
    return (REPO / rel).read_text()


def _write(rel: str, content: str) -> None:
    path = STAGE / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


if __name__ == "__main__":
    main()

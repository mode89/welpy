"""Phase-2 box-8 driver: carve welpy/output.py out of app.py.

Read-only on the repo; writes the transformed files to /tmp/welpy-output/.
Run from the repo root: ``python scripts/phase2_output.py``.
"""

import pathlib

import refactorlib as r

REPO = pathlib.Path(__file__).resolve().parent.parent
STAGE = pathlib.Path("/tmp/welpy-output")

# Top-down: orchestrator, monitor lifecycle, DPMS, render path, paint helpers.
OUTPUT_ORDER = [
    "update_monitors",
    "monitor_new", "monitor_request_state", "monitor_cleanup",
    "output_power_set_mode",
    "monitor_render", "client_holds_paint", "client_rendered",
    "monitor_force_paint",
]

# tests: wel.NAME -> output.NAME (whole-file occurrence counts; all in moved tests).
WEL_REFS = [
    ("monitor_new", 9), ("monitor_request_state", 2), ("monitor_cleanup", 4),
    ("monitor_render", 10), ("monitor_force_paint", 1),
    ("output_power_set_mode", 3), ("update_monitors", 4),
    ("client_holds_paint", 1),
]

# tests: "welpy.app.NAME" patch targets -> "welpy.output.NAME".
APP_STRINGS = [
    ("monitor_request_state", 1), ("monitor_cleanup", 1),
    ("monitor_render", 1), ("monitor_force_paint", 1),
    ("output_power_set_mode", 1), ("update_monitors", 4),
    ("client_rendered", 4),
]

# output-subject tests, ordered to mirror OUTPUT_ORDER.
MOVE = [
    "test_update_monitors_arranges_all", "test_update_monitors_no_monitors",
    "test_lock_surfaces_reconfigured", "test_lock_surfaces_pruned",
    "test_monitor_new_order", "test_monitor_scale_configured",
    "test_monitor_scale_default", "test_monitor_new_appends",
    "test_monitor_new_frame", "test_monitor_new_request_state",
    "test_monitor_new_destroy", "test_monitor_new_timer",
    "test_monitor_new_updates",
    "test_monitor_request_state_commits", "test_monitor_request_state_updates",
    "test_monitor_cleanup_drops", "test_monitor_cleanup_removes_timer",
    "test_monitor_cleanup_destroys_layers", "test_monitor_cleanup_removes",
    "test_output_power_off", "test_output_power_on", "test_output_power_unknown",
    "test_monitor_render_order", "test_monitor_render_holds",
    "test_monitor_render_occluded", "test_monitor_render_fullscreen_holds",
    "test_monitor_render_floating", "test_monitor_render_resizing",
    "test_monitor_render_moving", "test_monitor_render_clear",
    "test_monitor_render_arms_timer", "test_monitor_render_disarms_timer",
    "test_xwayland_holds_paint",
    "test_monitor_force_paint_commits",
]

OUTPUT_HEADER = '''\
"""Screen (output) management: bring monitors online, run their per-frame
paint loop, and re-flow window geometry/focus when the screen layout changes."""

from __future__ import annotations

import logging
import time

from . import bindings
from . import ext_workspace
from . import focus
from . import geometry
from . import model
from . import session_lock
from .layout import Rect
from .model import Client, Layer, Monitor, Server, SHELL_LAYERS, XdgClient

logger = logging.getLogger(__name__)
'''

TEST_HEADER = '''\
"""Unit tests for welpy.output: screen (output) management — bring-up, the
per-frame paint loop, paint-hold predicates, and output-layout re-flow."""

from unittest.mock import ANY, MagicMock, call, patch

from welpy import app as wel, model, output
from tests.helpers import (
    make_server, make_client, make_x11_client, make_monitor,
    make_workspace, make_layer_surface, make_session_lock, trigger,
)
'''


def main() -> None:
    _build_output()
    _rewrite_app()
    _rewrite_tests()
    print(f"staged to {STAGE}")
    _warn_long("welpy/output.py", "welpy/app.py",
               "tests/test_output.py", "tests/test_app.py")


def _build_output() -> None:
    body = r.extract_defs(_read("welpy/app.py"), OUTPUT_ORDER)
    _write("welpy/output.py", OUTPUT_HEADER + "\n\n" + body)


def _rewrite_app() -> None:
    a = r.delete_defs(_read("welpy/app.py"), OUTPUT_ORDER)
    a = r.replace(a, "from . import model\n",
                  "from . import model\nfrom . import output\n", n=1)
    a = r.replace(
        a,
        "    Monitor, PointerConstraint, Server,\n"
        "    SHELL_LAYERS, Workspace, X11Client, XdgClient,\n",
        "    Monitor, PointerConstraint, Server, Workspace, X11Client,\n",
        n=1)
    a = r.replace(
        a,
        "LayerSurface = model.LayerSurface\n"
        "LockSurface = model.LockSurface\n"
        "SessionLock = model.SessionLock\n"
        "Unmanaged = model.Unmanaged\n",
        "LayerSurface = model.LayerSurface\n"
        "LockSurface = model.LockSurface\n"
        "SHELL_LAYERS = model.SHELL_LAYERS\n"
        "SessionLock = model.SessionLock\n"
        "Unmanaged = model.Unmanaged\n"
        "XdgClient = model.XdgClient\n",
        n=1)
    for old, new in [
        ("lambda data: monitor_new(server, data)",
         "lambda data: output.monitor_new(server, data)"),
        ("lambda _data: update_monitors(server)",
         "lambda _data: output.update_monitors(server)"),
        ("lambda data: output_power_set_mode(server, data)",
         "lambda data: output.output_power_set_mode(server, data)"),
    ]:
        a = r.replace(a, old, new, n=1)
    _write("welpy/app.py", a)


def _rewrite_tests() -> None:
    t = _read("tests/test_app.py")
    for name, n in WEL_REFS:
        t = r.replace(t, "wel." + name, "output." + name, n=n)
    for name, n in APP_STRINGS:
        t = r.replace(t, '"welpy.app.' + name + '"',
                      '"welpy.output.' + name + '"', n=n)

    moved = r.extract_defs(t, MOVE)
    _write("tests/test_output.py", TEST_HEADER + "\n\n" + moved)

    rest = r.delete_defs(t, MOVE)
    _write("tests/test_app.py", rest)


def _warn_long(*rels: str) -> None:
    for rel in rels:
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

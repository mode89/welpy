"""Phase-2 box-5 driver: carve welpy/xwayland.py out of app.py (transient).

Read-only on the repo; writes the transformed files to /tmp/welpy-xwayland/.
Run from the repo root: ``python scripts/phase2_xwayland.py``.
"""

import pathlib

import refactorlib as r

REPO = pathlib.Path(__file__).resolve().parent.parent
STAGE = pathlib.Path("/tmp/welpy-xwayland")

# Top-down: managed X11 entry point and its handlers, then X server readiness,
# then override-redirect surface lifecycle.
XWAYLAND_ORDER = [
    "x11_surface_new", "x11_request_configure", "x11_request_activate",
    "x11_set_hints", "x11_ready", "unmanaged_new", "unmanaged_map",
    "unmanaged_configure", "unmanaged_unmap", "unmanaged_cleanup",
]

# tests: wel.NAME -> xwayland.NAME (whole-file occurrence counts).
WEL_REFS = [
    ("x11_surface_new", 5), ("x11_request_configure", 1),
    ("x11_request_activate", 1), ("x11_set_hints", 2),
    ("x11_ready", 1), ("unmanaged_new", 2), ("unmanaged_map", 2),
    ("unmanaged_configure", 2), ("unmanaged_unmap", 2),
    ("unmanaged_cleanup", 1),
]

# tests: "welpy.app.NAME" patch targets -> "welpy.xwayland.NAME".
APP_STRINGS = [
    ("unmanaged_new", 1), ("unmanaged_map", 1),
]

# XWayland-subject tests, ordered to mirror XWAYLAND_ORDER.
MOVE = [
    "test_xwayland_new_unmanaged",
    "test_xwayland_new_attaches_listeners",
    "test_xwayland_associate_map",
    "test_xwayland_dissociate_detaches",
    "test_xwayland_hints_wired",
    "test_xwayland_configure_premap",
    "test_xwayland_activate_urgent",
    "test_xwayland_hints_urgent",
    "test_xwayland_hints_premap",
    "test_xwayland_ready_seat",
    "test_unmanaged_new_listeners",
    "test_unmanaged_associate_map",
    "test_unmanaged_map_position",
    "test_unmanaged_map_focus",
    "test_unmanaged_configure_position",
    "test_unmanaged_configure_premap",
    "test_unmanaged_unmap_restores",
    "test_unmanaged_unmap_unfocused",
    "test_unmanaged_cleanup_detaches",
]

XWAYLAND_HEADER = '''\
"""XWayland integration for legacy X11 apps: managed windows,
override-redirect surfaces, activation/configure requests, and startup
readiness."""

from __future__ import annotations

from . import focus
from . import geometry
from . import windows
from .model import Layer, Server, Unmanaged, X11Client
'''

TEST_HEADER = '''\
"""Unit tests for welpy.xwayland: managed X11 window wiring,
override-redirect surface lifecycle, X11 requests, and XWayland readiness."""

from unittest.mock import ANY, MagicMock, patch

from welpy import app as wel, xwayland
from tests.helpers import (
    make_server, make_x11_client, make_unmanaged, trigger,
)
'''


def main() -> None:
    _build_xwayland()
    _rewrite_app()
    _rewrite_tests()
    print(f"staged to {STAGE}")
    _warn_long("welpy/xwayland.py", "welpy/app.py",
               "tests/test_xwayland.py", "tests/test_app.py")


def _build_xwayland() -> None:
    body = r.extract_defs(_read("welpy/app.py"), XWAYLAND_ORDER)
    _write("welpy/xwayland.py", XWAYLAND_HEADER + "\n\n" + body)


def _rewrite_app() -> None:
    a = r.delete_defs(_read("welpy/app.py"), XWAYLAND_ORDER)
    a = r.replace(a, "from . import windows\n",
                  "from . import windows\nfrom . import xwayland\n", n=1)
    a = r.replace(a,
                  "    SHELL_LAYERS, Unmanaged, Workspace, X11Client, XdgClient,\n",
                  "    SHELL_LAYERS, Workspace, X11Client, XdgClient,\n", n=1)
    a = r.replace(a, "\n\nlogger = logging.getLogger(__name__)\n",
                  "\n\nUnmanaged = model.Unmanaged\n"
                  "logger = logging.getLogger(__name__)\n", n=1)
    for old, new in [
        ("xwayland = lib.wlr_xwayland_create(display, compositor, False)",
         "xwayland_server = lib.wlr_xwayland_create(display, compositor, False)"),
        ("if xwayland == ffi.NULL:", "if xwayland_server == ffi.NULL:"),
        ("xdg_shell=xdg_shell, layer_shell=layer_shell, xwayland=xwayland,",
         "xdg_shell=xdg_shell, layer_shell=layer_shell, "
         "xwayland=xwayland_server,"),
        ("lib.welpy_xwayland_new_surface(xwayland)",
         "lib.welpy_xwayland_new_surface(xwayland_server)"),
        ("lambda data: x11_surface_new(server, data)",
         "lambda data: xwayland.x11_surface_new(server, data)"),
        ("lib.welpy_xwayland_ready(xwayland)",
         "lib.welpy_xwayland_ready(xwayland_server)"),
        ("lambda _data: x11_ready(server)",
         "lambda _data: xwayland.x11_ready(server)"),
    ]:
        a = r.replace(a, old, new, n=1)
    _write("welpy/app.py", a)


def _rewrite_tests() -> None:
    t = _read("tests/test_app.py")
    for name, n in WEL_REFS:
        t = r.replace(t, "wel." + name, "xwayland." + name, n=n)
    for name, n in APP_STRINGS:
        t = r.replace(t, '"welpy.app.' + name + '"',
                      '"welpy.xwayland.' + name + '"', n=n)

    xwayland_tests = r.extract_defs(t, MOVE)
    _write("tests/test_xwayland.py", TEST_HEADER + "\n\n" + xwayland_tests)

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

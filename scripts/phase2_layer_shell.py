"""Phase-2 box-6 driver: carve welpy/layer_shell.py out of app.py.

Read-only on the repo; writes the transformed files to /tmp/welpy-layer-shell/.
Run from the repo root: ``python scripts/phase2_layer_shell.py``.
"""

import pathlib

import refactorlib as r

REPO = pathlib.Path(__file__).resolve().parent.parent
STAGE = pathlib.Path("/tmp/welpy-layer-shell")

LAYER_SHELL_ORDER = [
    "layer_surface_new", "layer_surface_commit",
    "layer_surface_unmap", "layer_surface_cleanup",
]

WEL_REFS = [
    ("layer_surface_new", 6),
    ("layer_surface_commit", 2),
    ("layer_surface_unmap", 4),
    ("layer_surface_cleanup", 2),
]

APP_STRINGS = [("layer_surface_new", 1)]

MOVE = [
    "_stage_layer_surface_new",
    "test_layer_new_no_monitor",
    "test_layer_new_assigns_monitor",
    "test_layer_new_buckets",
    "test_layer_new_popups_high",
    "test_layer_new_popups_low",
    "test_layer_new_send_enter",
    "test_layer_commit_moves_bucket",
    "test_layer_commit_skips_content",
    "test_layer_unmap_clears_focus",
    "test_layer_unmap_refocuses_client",
    "test_layer_unmap_refocuses_monitor",
    "test_layer_unmap_unfocused",
    "test_layer_cleanup_removes",
    "test_layer_cleanup_trees",
]

LAYER_SHELL_HEADER = '''\
"""Layer-shell surface lifecycle for bars, wallpaper, panels, and launchers."""

from __future__ import annotations

from . import focus
from . import geometry
from . import model
from .model import Layer, LayerSurface, Server
'''

TEST_HEADER = '''\
"""Unit tests for welpy.layer_shell: shell-surface lifecycle for bars,
wallpaper, panels, and launchers."""

from unittest.mock import MagicMock, patch

from welpy import app as wel, layer_shell
from tests.helpers import (
    make_server, make_client, make_monitor, make_workspace, make_layer_surface,
)
'''


def main() -> None:
    _build_layer_shell()
    _rewrite_app()
    _rewrite_tests()
    print(f"staged to {STAGE}")
    _warn_long("welpy/layer_shell.py", "welpy/app.py",
               "tests/test_layer_shell.py", "tests/test_app.py")


def _build_layer_shell() -> None:
    body = r.extract_defs(_read("welpy/app.py"), LAYER_SHELL_ORDER)
    body = r.replace(
        body,
        "layer = SHELL_LAYERS[layer_surface.pending.layer]",
        "layer = model.SHELL_LAYERS[layer_surface.pending.layer]",
        n=1)
    body = r.replace(
        body,
        "geometry.place_in_layer_bucket(\n"
        "                    monitor, ls, "
        "SHELL_LAYERS[layer_surface.current.layer])",
        "geometry.place_in_layer_bucket(\n"
        "                    monitor, ls,\n"
        "                    model.SHELL_LAYERS[layer_surface.current.layer])",
        n=1)
    _write("welpy/layer_shell.py", LAYER_SHELL_HEADER + "\n\n" + body)


def _rewrite_app() -> None:
    a = r.delete_defs(_read("welpy/app.py"), LAYER_SHELL_ORDER)
    a = r.replace(a, "from . import geometry\n",
                  "from . import geometry\nfrom . import layer_shell\n", n=1)
    a = r.replace(
        a,
        "    Client, Cursor, Grab, KeyboardGroup, Layer, LayerSurface,\n",
        "    Client, Cursor, Grab, KeyboardGroup, Layer,\n",
        n=1)
    a = r.replace(a, "\n\nUnmanaged = model.Unmanaged\n",
                  "\n\nLayerSurface = model.LayerSurface\n"
                  "Unmanaged = model.Unmanaged\n", n=1)
    for old, new in [
        ("layer_shell = lib.wlr_layer_shell_v1_create(display, 5)",
         "layer_shell_server = lib.wlr_layer_shell_v1_create(display, 5)"),
        ("xdg_shell=xdg_shell, layer_shell=layer_shell, "
         "xwayland=xwayland_server,",
         "xdg_shell=xdg_shell,\n"
         "        layer_shell=layer_shell_server, xwayland=xwayland_server,"),
        ("lib.welpy_layer_shell_new_surface(layer_shell)",
         "lib.welpy_layer_shell_new_surface(layer_shell_server)"),
        ("lambda data: layer_surface_new(server, data)",
         "lambda data: layer_shell.layer_surface_new(server, data)"),
    ]:
        a = r.replace(a, old, new, n=1)
    _write("welpy/app.py", a)


def _rewrite_tests() -> None:
    t = _read("tests/test_app.py")
    for name, n in WEL_REFS:
        t = r.replace(t, "wel." + name, "layer_shell." + name, n=n)
    for name, n in APP_STRINGS:
        t = r.replace(t, '"welpy.app.' + name + '"',
                      '"welpy.layer_shell.' + name + '"', n=n)

    layer_shell_tests = r.extract_defs(t, MOVE)
    _write(
        "tests/test_layer_shell.py", TEST_HEADER + "\n\n" + layer_shell_tests)

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

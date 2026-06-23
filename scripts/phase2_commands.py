"""Phase-2 box-10 driver: carve welpy/commands.py out of app.py.

Read-only on the repo; writes transformed files to /tmp/welpy-commands/.
Run from the repo root: ``python scripts/phase2_commands.py``.
"""

import pathlib

import refactorlib as r

REPO = pathlib.Path(__file__).resolve().parent.parent
STAGE = pathlib.Path("/tmp/welpy-commands")

COMMANDS_ORDER = [
    "focus_direction", "move_direction", "group_window", "cycle_layout",
    "toggle_fullscreen", "toggle_floating", "close_window",
    "view_workspace", "view_previous_workspace", "move_client_to_workspace",
    "assign_workspace_to_monitor", "move_active_workspace_to_monitor",
]

# tests: wel.NAME -> commands.NAME (longest-prefix first; whole-file counts).
WEL_REFS = [
    ("move_active_workspace_to_monitor", 3), ("move_client_to_workspace", 6),
    ("move_direction", 5),
    ("view_previous_workspace", 2), ("view_workspace", 7),
    ("toggle_fullscreen", 4), ("toggle_floating", 6),
    ("focus_direction", 5), ("group_window", 3), ("cycle_layout", 1),
    ("close_window", 2),
]

MOVE = [
    "test_focus_direction_moves", "test_focus_direction_edge",
    "test_focus_direction_fullscreen", "test_focus_direction_floating",
    "test_focus_direction_group_mru",
    "test_move_direction_moves", "test_move_direction_edge",
    "test_move_direction_fullscreen", "test_move_direction_floating",
    "test_move_direction_vertical",
    "test_group_window_wraps", "test_group_window_alone",
    "test_group_window_nested",
    "test_cycle_layout_flips",
    "test_toggle_fullscreen_enters", "test_toggle_fullscreen_to_tile",
    "test_toggle_fullscreen_to_float", "test_toggle_fullscreen_no_focus",
    "test_toggle_floating_to_float", "test_toggle_floating_to_tile",
    "test_toggle_floating_drops_leaf", "test_toggle_floating_adds_leaf",
    "test_toggle_floating_fullscreen_noop", "test_toggle_floating_no_focus",
    "test_xwayland_close", "test_close_window_xdg",
    "test_view_workspace_activates", "test_view_workspace_adopts_orphan",
    "test_view_workspace_ends_grabs", "test_view_workspace_unknown",
    "test_view_workspace_records_previous", "test_view_previous_switches_back",
    "test_view_previous_noop", "test_view_workspace_outgoing",
    "test_move_client_reassigns", "test_move_client_moves_leaf",
    "test_move_client_adopts", "test_move_client_fullscreen",
    "test_move_client_fullscreen_notifies", "test_move_client_target_fullscreen",
    "test_move_workspace_next", "test_move_workspace_wraps",
    "test_move_workspace_single",
]

# ext_workspace-subject tests + their exclusive helpers -> own mirror file
# (subject = ext_workspace.X; box-9 tests/test_libinput.py precedent).
EXTWS_MOVE = [
    "make_extws_server", "make_extws", "bind_extws_client",
    "extws_group", "extws_handle",
    "test_extws_create", "test_extws_destroy", "test_extws_bind_initial",
    "test_extws_layout_groups", "test_extws_orphan_hidden",
    "test_extws_publish_done", "test_extws_activate", "test_extws_assign",
    "test_extws_orphan_transition", "test_extws_monitor_unplug",
    "test_extws_unplug_migrate", "test_extws_active_change",
    "test_extws_urgent_state",
]

COMMANDS_HEADER = '''\
"""Compositor commands bound to keys: directional focus and window movement,
grouping and layout flips, fullscreen/float toggles, closing the focused
window, and workspace switching/relocation."""

from __future__ import annotations

from . import ext_workspace
from . import focus
from . import geometry
from . import layout
from .model import Monitor, Server, Workspace, X11Client
'''

TEST_HEADER = '''\
"""Unit tests for welpy.commands: directional focus and window movement,
grouping/layout flips, fullscreen/float toggles, closing the focused window,
and workspace switching/relocation."""

from unittest.mock import patch

from welpy import app as wel, commands, focus, geometry, layout
from tests.helpers import (
    make_server, make_client, make_x11_client, make_monitor, make_workspace,
    flat_tree,
)
'''

EXTWS_TEST_HEADER = '''\
"""Unit tests for welpy.ext_workspace: the ext-workspace-v1 protocol exposing
workspaces and per-monitor groups to clients, plus activate/assign requests."""

from unittest.mock import MagicMock

from welpy import ext_workspace
from tests.helpers import (
    make_client, make_monitor, make_server, make_workspace,
)
'''


def main() -> None:
    _build_commands()
    _rewrite_app()
    _rewrite_tests()
    print(f"staged to {STAGE}")
    _warn_long("welpy/commands.py", "welpy/app.py", "tests/test_commands.py",
               "tests/test_ext_workspace.py", "tests/test_app.py")


def _build_commands() -> None:
    body = r.extract_defs(_read("welpy/app.py"), COMMANDS_ORDER)
    _write("welpy/commands.py", COMMANDS_HEADER + "\n\n" + body)


def _rewrite_app() -> None:
    a = r.delete_defs(_read("welpy/app.py"), COMMANDS_ORDER)
    a = r.replace(
        a, "from . import bindings\n",
        "from . import bindings\nfrom . import commands\n", n=1)
    a = r.replace(a, "from . import focus\n", "", n=1)
    a = r.replace(
        a, "    Layer, Monitor, Server, Workspace, X11Client,\n",
        "    Layer, Server, Workspace,\n", n=1)
    a = r.replace(
        a, "LockSurface = model.LockSurface\n",
        "LockSurface = model.LockSurface\nMonitor = model.Monitor\n", n=1)
    a = r.replace(
        a, "Unmanaged = model.Unmanaged\n",
        "Unmanaged = model.Unmanaged\nX11Client = model.X11Client\n", n=1)
    for old, new in [
        ("lambda name: view_workspace(server, name)",
         "lambda name: commands.view_workspace(server, name)"),
        ("lambda ws, target: assign_workspace_to_monitor(",
         "lambda ws, target: commands.assign_workspace_to_monitor("),
        ('server.keycode["q"]): close_window,',
         'server.keycode["q"]): commands.close_window,'),
        ('server.keycode["f"]): toggle_fullscreen,',
         'server.keycode["f"]): commands.toggle_fullscreen,'),
        ("\n            toggle_floating,",
         "\n            commands.toggle_floating,"),
        ('server.keycode["v"]): group_window,',
         'server.keycode["v"]): commands.group_window,'),
        ('server.keycode["e"]): cycle_layout,',
         'server.keycode["e"]): commands.cycle_layout,'),
        ('server.keycode["Tab"]): view_previous_workspace,',
         'server.keycode["Tab"]): commands.view_previous_workspace,'),
        ("lambda s, n=name: view_workspace(s, n)",
         "lambda s, n=name: commands.view_workspace(s, n)"),
        ("lambda s, n=name: move_client_to_workspace(s, n)",
         "lambda s, n=name: commands.move_client_to_workspace(s, n)"),
    ]:
        a = r.replace(a, old, new, n=1)
    a = r.replace(
        a, "focus_direction(s, layout.Direction.",
        "commands.focus_direction(s, layout.Direction.", n=4)
    a = r.replace(
        a, "move_direction(s, layout.Direction.",
        "commands.move_direction(s, layout.Direction.", n=4)
    a = r.replace(
        a, "move_active_workspace_to_monitor(s, ",
        "commands.move_active_workspace_to_monitor(s, ", n=2)
    _write("welpy/app.py", a)


def _rewrite_tests() -> None:
    t = _read("tests/test_app.py")
    for name, n in WEL_REFS:
        t = r.replace(t, "wel." + name, "commands." + name, n=n)
    moved = r.extract_defs(t, MOVE)
    _write("tests/test_commands.py", TEST_HEADER + "\n\n" + moved)
    extws = r.extract_defs(t, EXTWS_MOVE)
    _write("tests/test_ext_workspace.py", EXTWS_TEST_HEADER + "\n\n" + extws)
    rest = r.delete_defs(t, MOVE + EXTWS_MOVE)
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

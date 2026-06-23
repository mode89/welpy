"""Phase-2 box-9 driver: carve welpy/input.py out of app.py.

Read-only on the repo; writes the transformed files to /tmp/welpy-input/.
Run from the repo root: ``python scripts/phase2_input.py``.
"""

import pathlib

import refactorlib as r

REPO = pathlib.Path(__file__).resolve().parent.parent
STAGE = pathlib.Path("/tmp/welpy-input")

INPUT_ORDER = [
    "create_cursor", "destroy_cursor",
    "cursor_motion", "cursor_motion_absolute", "cursor_button",
    "cursor_axis", "cursor_frame",
    "process_pointer_motion", "apply_pointer_constraint",
    "confine_delta", "set_active_constraint",
    "constraint_new", "constraint_destroy", "constraint_warp_to_hint",
    "begin_dragging_client", "begin_resizing_client", "drag_client",
    "create_keyboard_group", "destroy_keyboard_group", "build_keycode_map",
    "input_new", "keyboard_key", "keyboard_modifiers",
    "lookup_binding", "toggle_passthrough", "change_vt",
    "seat_set_selection", "seat_set_primary_selection", "seat_set_cursor",
]

# tests: wel.NAME -> input.NAME  (longest-prefix first; whole-file counts).
WEL_REFS = [
    ("cursor_motion_absolute", 1), ("cursor_motion", 13), ("cursor_button", 11),
    ("cursor_axis", 3), ("cursor_frame", 1),
    ("create_cursor", 6), ("destroy_cursor", 1),
    ("set_active_constraint", 1), ("constraint_new", 1),
    ("constraint_destroy", 2), ("constraint_warp_to_hint", 2),
    ("begin_dragging_client", 4), ("begin_resizing_client", 3),
    ("drag_client", 1),
    ("build_keycode_map", 3), ("create_keyboard_group", 3),
    ("destroy_keyboard_group", 1), ("input_new", 4),
    ("keyboard_key", 7), ("keyboard_modifiers", 2),
    ("lookup_binding", 4), ("toggle_passthrough", 4),
    ("seat_set_primary_selection", 1), ("seat_set_selection", 1),
    ("seat_set_cursor", 3),
]

# tests: "welpy.app.NAME" patch targets -> "welpy.input.NAME".
APP_STRINGS = [
    ("build_keycode_map", 26),
    ("cursor_motion_absolute", 1), ("cursor_motion", 1), ("cursor_button", 1),
    ("cursor_axis", 1), ("cursor_frame", 1),
    ("constraint_new", 1), ("constraint_warp_to_hint", 2),
    ("keyboard_key", 1), ("keyboard_modifiers", 1),
    ("lookup_binding", 1), ("seat_set_cursor", 1),
]

# input-subject tests, ordered to mirror INPUT_ORDER (libinput tests excluded).
MOVE = [
    # cursor lifecycle + handlers
    "test_cursor_create_visible", "test_cursor_create_motion",
    "test_cursor_create_motion_absolute", "test_cursor_create_axis",
    "test_cursor_create_frame", "test_cursor_create_button",
    "test_cursor_destroy_releases",
    "test_cursor_motion_moves", "test_cursor_motion_absolute_converts",
    "test_cursor_motion_forwards", "test_cursor_motion_empty_clears",
    "test_cursor_motion_grab_skips", "test_cursor_motion_drags",
    "test_cursor_motion_resizes", "test_cursor_motion_resize_min",
    "test_cursor_button_binding", "test_cursor_button_focuses",
    "test_cursor_button_active_monitor", "test_cursor_button_release_ends",
    "test_cursor_button_release_pointer", "test_cursor_button_forwards",
    "test_cursor_button_rebases", "test_cursor_button_binding_norebase",
    "test_cursor_button_consumes", "test_cursor_button_release_consumed",
    "test_cursor_axis_forwards", "test_cursor_axis_rebases",
    "test_cursor_axis_grab", "test_cursor_frame_forwards",
    # pointer motion + constraints
    "test_motion_relative_sent",
    "test_constraint_activates", "test_constraint_deactivates",
    "test_constraint_locked_pins", "test_constraint_confined_clamps",
    "test_constraint_grab_skips", "test_constraint_new_listens",
    "test_constraint_destroy_clears", "test_constraint_deactivate_destroys",
    "test_constraint_warp_to_hint", "test_constraint_warp_no_hint",
    # drag
    "test_begin_dragging_offset", "test_begin_dragging_empty",
    "test_begin_resizing_anchor", "test_begin_resizing_empty",
    "test_xwayland_drag_move",
    # keyboard / bindings
    "test_keyboard_create_wires_seat", "test_keyboard_create_key",
    "test_keyboard_create_modifiers", "test_keyboard_destroy_releases",
    "test_keycode_map_range", "test_keycode_map_names", "test_keycode_map_unbound",
    "test_input_new_keyboard", "test_input_new_keymap", "test_input_new_other",
    "test_input_new_pointer",
    "test_keyboard_key_unbound", "test_keyboard_key_binding",
    "test_keyboard_key_lookup_hook", "test_keyboard_key_consumes",
    "test_keyboard_key_mods", "test_keyboard_key_release",
    "test_keyboard_modifiers_forwards",
    "test_lookup_binding_hit", "test_lookup_binding_miss",
    "test_lookup_binding_passthrough", "test_lookup_binding_passthrough_toggle",
    "test_toggle_passthrough_flips",
    # seat
    "test_seat_set_selection", "test_seat_set_primary_selection",
    "test_seat_set_cursor_focused", "test_seat_set_cursor_unfocused",
    "test_seat_set_cursor_grab",
]

# libinput-subject tests -> their own mirror file (subject = libinput.configure).
LIBINPUT_MOVE = [
    "test_libinput_skips_nonlibinput", "test_libinput_null_handle",
    "test_libinput_applies_settings", "test_libinput_unsupported_skipped",
    "test_libinput_unknown_choice_defaults",
]

INPUT_HEADER = '''\
"""Input handling: the mouse pointer, pointer lock/confine, drag-to-move and
drag-to-resize, the keyboard and keybinding dispatch, and clipboard/cursor
requests from apps."""

from __future__ import annotations

import logging

from . import focus
from . import geometry
from . import libinput
from . import model
from .layout import Rect
from .model import (
    Client, Cursor, Grab, KeyboardGroup, PointerConstraint, Server, X11Client,
)

logger = logging.getLogger(__name__)
'''

TEST_HEADER = '''\
"""Unit tests for welpy.input: the mouse pointer and pointer lock/confine,
drag-to-move/resize, keyboard + keybinding dispatch, and seat requests."""

from unittest.mock import ANY, MagicMock, call, patch

import cffi

from welpy import (  # pylint: disable=redefined-builtin
    app as wel, focus, geometry, input, layout, model)
from tests.helpers import (
    make_server, make_client, make_x11_client, make_cursor,
    make_keyboard_group, make_keycode_map, make_monitor, make_workspace,
    trigger,
)
'''

LIBINPUT_TEST_HEADER = '''\
"""Unit tests for welpy.libinput: applying per-device libinput settings."""

from unittest.mock import patch

from welpy import libinput
from tests.helpers import make_server
'''


def main() -> None:
    _build_input()
    _rewrite_app()
    _rewrite_tests()
    print(f"staged to {STAGE}")
    _warn_long("welpy/input.py", "welpy/app.py", "tests/test_input.py",
               "tests/test_libinput.py", "tests/test_app.py")


def _build_input() -> None:
    body = r.extract_defs(_read("welpy/app.py"), INPUT_ORDER)
    _write("welpy/input.py", INPUT_HEADER + "\n\n" + body)


def _rewrite_app() -> None:
    a = r.delete_defs(_read("welpy/app.py"), INPUT_ORDER)
    a = r.replace(
        a, "from . import geometry\n",
        "from . import geometry\n"
        "from . import input  # pylint: disable=redefined-builtin\n", n=1)
    a = r.replace(
        a,
        "    Client, Cursor, Grab, KeyboardGroup, Layer,\n"
        "    Monitor, PointerConstraint, Server, Workspace, X11Client,\n",
        "    Layer, Monitor, Server, Workspace, X11Client,\n", n=1)
    a = r.replace(
        a, "LayerSurface = model.LayerSurface\n",
        "Cursor = model.Cursor\n"
        "Grab = model.Grab\n"
        "KeyboardGroup = model.KeyboardGroup\n"
        "LayerSurface = model.LayerSurface\n", n=1)
    a = r.replace(
        a, "LockSurface = model.LockSurface\n",
        "LockSurface = model.LockSurface\n"
        "PointerConstraint = model.PointerConstraint\n", n=1)
    for old, new in [
        ("server.cursor = create_cursor(server)",
         "server.cursor = input.create_cursor(server)"),
        ("server.keyboard_group = create_keyboard_group(server)",
         "server.keyboard_group = input.create_keyboard_group(server)"),
        ("build_keycode_map(lib, ffi, server.keyboard_group.keymap)",
         "input.build_keycode_map(lib, ffi, server.keyboard_group.keymap)"),
        ("lambda data: input_new(server, data)",
         "lambda data: input.input_new(server, data)"),
        ("lambda data: constraint_new(server, data)",
         "lambda data: input.constraint_new(server, data)"),
        ("lambda data: seat_set_selection(server, data)",
         "lambda data: input.seat_set_selection(server, data)"),
        ("lambda data: seat_set_primary_selection(server, data)",
         "lambda data: input.seat_set_primary_selection(server, data)"),
        ("lambda data: seat_set_cursor(server, data)",
         "lambda data: input.seat_set_cursor(server, data)"),
        ("destroy_keyboard_group(lib, server.keyboard_group)",
         "input.destroy_keyboard_group(lib, server.keyboard_group)"),
        ("destroy_cursor(lib, server.cursor)",
         "input.destroy_cursor(lib, server.cursor)"),
        ("(mod, lib.BTN_LEFT): begin_dragging_client,",
         "(mod, lib.BTN_LEFT): input.begin_dragging_client,"),
        ("(mod, lib.BTN_RIGHT): begin_resizing_client,",
         "(mod, lib.BTN_RIGHT): input.begin_resizing_client,"),
        ("\n            toggle_passthrough,",
         "\n            input.toggle_passthrough,"),
        ("lambda s, n=i: change_vt(s, n)",
         "lambda s, n=i: input.change_vt(s, n)"),
    ]:
        a = r.replace(a, old, new, n=1)
    _write("welpy/app.py", a)


def _rewrite_tests() -> None:
    t = _read("tests/test_app.py")
    for name, n in WEL_REFS:
        t = r.replace(t, "wel." + name, "input." + name, n=n)
    for name, n in APP_STRINGS:
        t = r.replace(t, '"welpy.app.' + name + '"',
                      '"welpy.input.' + name + '"', n=n)

    moved = r.extract_defs(t, MOVE)
    _write("tests/test_input.py", TEST_HEADER + "\n\n" + moved)

    moved_libinput = r.extract_defs(t, LIBINPUT_MOVE)
    _write("tests/test_libinput.py",
           LIBINPUT_TEST_HEADER + "\n\n" + moved_libinput)

    rest = r.delete_defs(t, MOVE + LIBINPUT_MOVE)
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

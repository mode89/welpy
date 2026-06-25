"""Shared test harness: builders for the compositor's data types, plus a
helper to deliver a wlroots event to its registered callback."""

from unittest.mock import MagicMock

from welpy import layout, model


def make_server(**kwargs):
    """Build a Server, filling fields the test doesn't care about with mocks."""
    ffi, lib, listen, add_timer, add_signal = make_bindings(
        ffi=kwargs.get("ffi"), lib=kwargs.get("lib"))
    seat = kwargs.get("seat") or MagicMock(name="seat")
    seat.keyboard_state.focused_surface = ffi.NULL
    return model.Server(**{
        "ffi": ffi, "lib": lib, "seat": seat,
        "listen": listen,
        "add_signal": add_signal,
        "add_timer": add_timer,
        "display": "DISPLAY", "event_loop": MagicMock(name="event_loop"),
        "backend": "BACKEND", "session": MagicMock(name="session"),
        "renderer": "RENDERER", "allocator": "ALLOCATOR",
        "renderer_lost": MagicMock(name="renderer_lost"),
        "compositor": MagicMock(name="compositor"),
        "output_layout": "OUTPUT_LAYOUT",
        # Accessed structurally (scene.tree); leave auto-mocked.
        "scene": MagicMock(name="scene"), "scene_layout": "SCENE_LAYOUT",
        "xdg_shell": MagicMock(name="xdg_shell"),
        "layer_shell": MagicMock(name="layer_shell"),
        "xwayland": MagicMock(name="xwayland"),
        "cursor": MagicMock(name="cursor"),
        "keyboard_group": make_keyboard_group(
            group="GROUP", keymap="KEYMAP", xkb_context="XKB"),
        "virtual_keyboards": [],
        "monitors": [], "active_monitor": None, "clients": [],
        "workspaces": [], "previous_workspace": MagicMock(name="prev_ws"),
        "ext_workspace": MagicMock(name="ext_workspace"),
        "layers": {layer: MagicMock(name=layer.name.lower())
                   for layer in model.Layer},
        "lock_background": MagicMock(name="lock_background"),
        "session_lock": None, "locked": False, "unmanaged_focus": None,
        "keycode": {}, "bindings": {}, "passthrough": False,
        "pointer_constraints": MagicMock(name="pointer_constraints"),
        "relative_pointer_mgr": MagicMock(name="relative_pointer_mgr"),
        "active_constraint": None, "constraints": [],
        "listeners": [],
        **kwargs,
    })


def make_bindings(**kwargs):
    """Mock the bindings.build() tuple (ffi, lib, listen, add_timer,
    add_signal) with defaults shared by make_server() and setup() tests."""
    ffi = kwargs.get("ffi") or MagicMock(name="ffi")
    lib = kwargs.get("lib") or MagicMock(name="lib")
    lib.ZWLR_LAYER_SURFACE_V1_KEYBOARD_INTERACTIVITY_NONE = 0
    # Concrete press/release flag so keyboard_key's dispatch can compare it.
    lib.WL_KEYBOARD_KEY_STATE_PRESSED = 1
    # Setter return values participate in pending_serial bookkeeping; default
    # to 0 so existing tests stay caught up unless they opt in.
    lib.wlr_xdg_toplevel_set_size.return_value = 0
    lib.wlr_xdg_toplevel_set_activated.return_value = 0
    lib.wlr_xdg_toplevel_set_tiled.return_value = 0
    lib.wlr_xdg_toplevel_set_fullscreen.return_value = 0
    # No GPU device by default: skip the wlr_drm / dmabuf / syncobj branch.
    lib.wlr_renderer_get_drm_fd.return_value = -1
    # No active pointer constraint by default; resolve to NULL like wlroots.
    lib.wlr_pointer_constraints_v1_constraint_for_surface.return_value = \
        ffi.NULL
    # Distinct handle per listen() call so listener counts add up.
    listen = MagicMock(side_effect=lambda *_a: MagicMock(name="handle"))
    return (ffi, lib, listen,
            MagicMock(name="add_timer"), MagicMock(name="add_signal"))


def make_client(**kwargs):
    """Build an XdgClient, filling fields the test doesn't care about."""
    toplevel = kwargs.get("toplevel") or MagicMock()
    # Default to caught-up so pending_serial is a no-op unless the test opts
    # in. Raw-string toplevels skip this since they never hit setter wrappers.
    if isinstance(toplevel, MagicMock):
        toplevel.base.current.configure_serial = 0
        toplevel.requested.fullscreen = False
        toplevel.parent = None
    kwargs["toplevel"] = toplevel
    return model.XdgClient(**{
        "scene_tree": MagicMock(),
        "content_tree": MagicMock(),
        "borders": tuple(MagicMock() for _ in range(4)),
        "focus_order": 0, "urgent": False, "grab": None,
        "floating_geom": None,
        "workspace": None, "listeners": [],
        "pending_serial": None,
        "decoration": None, "handle": None,
        "inner_size": None,
        **kwargs,
    })


def make_x11_client(**kwargs):
    """Build an X11Client, filling fields the test doesn't care about."""
    xsurface = kwargs.get("xsurface") or MagicMock()
    if isinstance(xsurface, MagicMock):
        xsurface.fullscreen = False
        xsurface.parent = None
        xsurface.override_redirect = False
    kwargs["xsurface"] = xsurface
    return model.X11Client(**{
        "scene_tree": MagicMock(),
        "content_tree": MagicMock(),
        "borders": tuple(MagicMock() for _ in range(4)),
        "focus_order": 0, "urgent": False, "grab": None,
        "floating_geom": None,
        "workspace": None, "listeners": [],
        "decoration": None, "handle": None,
        "inner_size": None,
        **kwargs,
    })


def make_unmanaged(**kwargs):
    """Build an Unmanaged override-redirect surface entity."""
    xsurface = kwargs.get("xsurface") or MagicMock()
    if isinstance(xsurface, MagicMock):
        xsurface.override_redirect = True
    kwargs["xsurface"] = xsurface
    return model.Unmanaged(**{
        "scene_tree": None,
        "listeners": [],
        **kwargs,
    })


def make_monitor(**kwargs):
    """Build a Monitor, filling fields the test doesn't care about."""
    return model.Monitor(**{
        "output": MagicMock(), "scene_output": MagicMock(),
        "layers": {layer: [] for layer in model.SHELL_LAYERS},
        "window_area": layout.Rect(0, 0, 800, 600),
        "active_workspace": None,
        "frame_timer": MagicMock(name="frame_timer"),
        "listeners": [],
        **kwargs,
    })


def make_workspace(**kwargs):
    """Build a Workspace, filling fields the test doesn't care about."""
    return model.Workspace(**{
        "name": "1",
        "monitor": None,
        "fullscreen": None,
        "root": layout.Container(layout.ContainerLayout.HORIZONTAL, []),
        **kwargs,
    })


def flat_tree(*clients):
    """A one-level HORIZONTAL container holding `clients` as tiled leaves."""
    return layout.Container(
        layout.ContainerLayout.HORIZONTAL, list(clients))


def make_cursor(**kwargs):
    """Build a Cursor, filling fields the test doesn't care about."""
    return model.Cursor(**{
        "cursor": MagicMock(), "xcursor_manager": MagicMock(),
        "listeners": [],
        **kwargs,
    })


def make_keyboard_group(**kwargs):
    """Build a KeyboardGroup, filling fields the test doesn't care about."""
    return model.KeyboardGroup(**{
        "group": MagicMock(), "keymap": MagicMock(),
        "xkb_context": MagicMock(), "listeners": [],
        **kwargs,
    })


def make_keycode_map():
    """Stand-in keycode map covering every key referenced by built-in
    bindings, so `setup()` can build `server.bindings` without KeyError."""
    return {"Return": 28, "q": 16, "j": 36, "k": 37, "f": 33,
            "p": 25, "v": 47,
            "e": 18, "space": 57, "h": 35, "l": 38, "Tab": 15,
            "1": 2, "2": 3, "3": 4, "4": 5, "5": 6,
            "6": 7, "7": 8, "8": 9, "9": 10, "0": 11,
            "F1": 59, "F2": 60, "F3": 61, "F4": 62, "F5": 63, "F6": 64,
            "F7": 65, "F8": 66, "F9": 67, "F10": 68, "F11": 87,
            "F12": 88}


def make_layer_surface(**kwargs):
    """Build a LayerSurface, filling fields the test doesn't care about."""
    return model.LayerSurface(**{
        "layer_surface": MagicMock(),
        "scene_layer": MagicMock(),
        "scene_tree": MagicMock(),
        "popups_tree": MagicMock(),
        "monitor": MagicMock(),
        "focused": False,
        "mapped": False,
        "listeners": [],
        **kwargs,
    })


def make_session_lock(**kwargs):
    """Build a SessionLock, filling fields the test doesn't care about."""
    return model.SessionLock(**{
        "lock": MagicMock(name="lock"), "tree": MagicMock(name="tree"),
        "surfaces": [], "listeners": [],
        **kwargs,
    })


def trigger(server, signal_accessor, data):
    """Invoke the callback registered with `listen` for this wlroots signal,
    simulating wlroots firing the event with `data`."""
    target = signal_accessor.return_value
    for c in server.listen.mock_calls:
        if c.args and c.args[0] is target:
            return c.args[1](data)
    raise AssertionError(f"no callback registered for {signal_accessor}")

"""Compositor data model: window/screen/state records, layout constants, and
the shared window-lookup queries."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any

from .layout import Rect


BORDER_WIDTH = 2
OUTPUT_SCALE = {}  # screen name -> scale factor; e.g. {"eDP-1": 2.0}
DEFAULT_SCALE = 1.0
BORDER_COLOR_ACTIVE = (0.0, 0.5, 1.0, 1.0)
BORDER_COLOR_INACTIVE = (0.3, 0.3, 0.3, 1.0)
BORDER_COLOR_URGENT = (0.9, 0.0, 0.0, 1.0)
WORKSPACE_NAMES = ("1", "2", "3", "4", "5", "6", "7", "8", "9", "10")


class Layer(enum.Enum):
    """Z-ordered scene layers; later members render above earlier ones."""
    BACKGROUND = enum.auto()
    BOTTOM = enum.auto()
    TILE = enum.auto()
    FLOAT = enum.auto()
    TOP = enum.auto()
    FULLSCREEN = enum.auto()
    OVERLAY = enum.auto()
    LOCK = enum.auto()


# Indexed by zwlr_layer_shell_v1 layer values (0..3) to map them to Layer.
SHELL_LAYERS = (Layer.BACKGROUND, Layer.BOTTOM, Layer.TOP, Layer.OVERLAY)


@dataclass(frozen=True)
class Grab:
    """Active mouse-driven interaction on a window. In both kinds,
    `cursor - (x, y)` is the value drag_client preserves under motion --
    the window origin for "move", the window size for "resize"."""
    kind: str
    x: int
    y: int


@dataclass
class Workspace:
    """Switchable container of windows on a monitor."""
    name: str                # user-facing label, e.g. "1".."9", "0"
    monitor: Any             # the screen this workspace lives on; may be None
    fullscreen: Any          # Client occupying it fullscreen, or None
    root: Any                # layout.Container: this workspace's tile tree


@dataclass
class Monitor:
    """Physical screen."""
    output: Any              # wlr_output: the physical screen
    scene_output: Any        # per-screen render state inside the scene graph
    layers: dict             # {Layer: list[LayerSurface]} per shell layer
    window_area: Rect        # screen rect minus shell exclusive zones
    active_workspace: Any    # currently shown Workspace, or None if empty
    frame_timer: Any         # safety valve: forces a paint if hold lingers
    listeners: list[Any]


@dataclass
class LayerSurface: # pylint: disable=too-many-instance-attributes
    """Shell-component window (bar, wallpaper, launcher) anchored to a
    screen edge via the layer-shell protocol."""
    layer_surface: Any       # wlr_layer_surface_v1
    scene_layer: Any         # wlr_scene_layer_surface_v1: anchor/zone geometry
    scene_tree: Any          # scene_layer.tree, cached for reparenting
    popups_tree: Any         # parent scene tree for popups from this surface
    monitor: Monitor
    focused: bool            # True while this surface holds the keyboard
    mapped: bool             # last-seen surface.mapped; gates re-arrange
    listeners: list[Any]


@dataclass(kw_only=True)
class Client: # pylint: disable=too-many-instance-attributes
    """Application window. Base shared by xdg-shell and X11/XWayland windows;
    per-kind fields live on the subclasses below."""
    scene_tree: Any          # wrapper tree: content subtree + four border rects
    content_tree: Any        # content subtree; inset within the wrapper
    borders: tuple           # (top, bottom, left, right) wlr_scene_rect handles
    focus_order: int         # bumped on each focus; higher = more recent
    urgent: bool             # app asked for attention while unfocused
    grab: Grab | None        # active mouse drag (move/resize), or None
    floating_geom: Rect | None  # the float's rect; None means tiled
    workspace: Any           # Workspace this window belongs to; None pre-map
    listeners: list[Any]
    decoration: Any          # wlr_xdg_toplevel_decoration_v1, if any; X11: None
    handle: Any              # ffi.new_handle: backs the surface's back-pointer
    inner_size: tuple[int, int] | None  # inner (w, h) configured; None pre-map


@dataclass(kw_only=True)
class XdgClient(Client):
    """A Wayland-native window speaking the xdg-shell protocol."""
    toplevel: Any            # the window (xdg_toplevel role on a wl_surface)
    pending_serial: int | None   # configure serial; None when client caught up


@dataclass(kw_only=True)
class X11Client(Client):
    """A legacy X11 window bridged through the embedded XWayland server."""
    xsurface: Any            # wlr_xwayland_surface


@dataclass(kw_only=True)
class Unmanaged:
    """An override-redirect X11 surface (menu, tooltip, dropdown, drag icon):
    the app positions and stacks it itself, so we just place it above the
    windows and hand it the keyboard when it asks."""
    xsurface: Any            # wlr_xwayland_surface
    scene_tree: Any          # subsurface tree in OVERLAY layer; None pre-map
    listeners: list[Any]


@dataclass
class SessionLock:
    """An active screen lock: a locker app has taken over every screen and
    holds it until it authenticates the user (or crashes)."""
    lock: Any                # wlr_session_lock_v1
    tree: Any                # scene tree holding the lock surfaces, above all
    surfaces: list           # LockSurface per screen
    listeners: list[Any]


@dataclass
class LockSurface:
    """A locker's blanking surface covering one screen while locked."""
    lock_surface: Any        # wlr_session_lock_surface_v1
    monitor: Monitor
    scene_tree: Any
    listeners: list[Any]


@dataclass
class Cursor:
    """Mouse pointer."""
    cursor: Any              # wlr_cursor: tracks pointer position
    xcursor_manager: Any     # loads themed cursor images from disk
    listeners: list[Any]


@dataclass
class PointerConstraint:
    """A client's request to lock or confine the pointer to one of its windows
    (games, 3D tools). Tracked so its destroy listener can be detached."""
    constraint: Any          # wlr_pointer_constraint_v1
    listeners: list[Any]


@dataclass
class KeyboardGroup:
    """Every physical keyboard funneled into one logical keyboard, so apps
    see a single source of key events no matter how many keyboards are
    plugged in."""
    group: Any               # wlr_keyboard_group: combines member keyboards
    keymap: Any              # xkb_keymap: layout shared by every member
    xkb_context: Any         # xkb_context: owns the keymap
    listeners: list[Any]


@dataclass
class TextInput:
    """One app's editable text field advertised via text-input-v3. The relay
    wires the focused one to the bound input method."""
    input: Any               # wlr_text_input_v3
    pending_surface: Any     # deferred enter target while no IME is bound
    pending_listeners: list[Any]  # watch the pending surface for destroy
    listeners: list[Any]


@dataclass
class InputPopup:
    """An IME candidate window: the little list of CJK/emoji candidates,
    anchored at the text caret inside the focused window."""
    popup: Any               # wlr_input_popup_surface_v2
    scene_tree: Any          # subtree under the anchored window; None when none
    surface: Any             # the focused surface the popup is anchored to
    listeners: list[Any]
    surface_listeners: list[Any]  # watch the anchored surface for unmap


@dataclass
class InputRelay:  # pylint: disable=too-many-instance-attributes
    """Brokers the focused app's text field and the single bound input method
    (fcitx5/ibus) in both directions, so native Wayland apps get IME."""
    input_method: Any        # the bound wlr_input_method_v2, or None
    keyboard_grab: Any       # active wlr_input_method_keyboard_grab_v2, or None
    text_inputs: list[TextInput]
    input_popups: list[InputPopup]
    anchor_for_surface: Any  # callback resolving the focused window's anchor
    listeners: list[Any]     # manager-level (new text-input / input-method)
    im_listeners: list[Any]  # bound input-method's signals
    grab_listeners: list[Any]  # active keyboard grab's signals


@dataclass(eq=False)
class VirtualKeyboard:
    """One client-injected keyboard isolated from the physical group."""
    group: Any               # wlr_keyboard_group: single virtual keyboard
    client: Any              # wl_client for the IME loop-breaker
    listeners: list[Any]


@dataclass
class Server: # pylint: disable=too-many-instance-attributes
    """The compositor's long-lived state."""
    ffi: Any
    lib: Any
    listen: Any
    add_signal: Any
    add_timer: Any
    display: Any
    event_loop: Any
    backend: Any
    session: Any             # NULL under nested wayland/x11 backends
    renderer: Any
    allocator: Any
    renderer_lost: Any       # listener handle, re-bound on GPU reset
    compositor: Any
    output_layout: Any       # geometric arrangement of physical screens
    scene: Any               # root of the scene graph -- everything to draw
    scene_layout: Any        # bridges scene_outputs with output_layout
    xdg_shell: Any
    layer_shell: Any
    xwayland: Any            # embedded X server for legacy X11 apps
    seat: Any
    cursor: Cursor
    keyboard_group: KeyboardGroup
    virtual_keyboards: list[VirtualKeyboard]
    monitors: list[Monitor]
    active_monitor: Any      # Monitor receiving new windows / key bindings
    clients: list[Client]
    workspaces: list         # all Workspaces; created at setup, never resized
    previous_workspace: Any  # name of last-viewed workspace, for toggling back
    ext_workspace: Any       # ext-workspace-v1 protocol state
    input_relay: Any         # text-input/input-method relay (IME)
    layers: dict             # scene tree per Layer; key order = z order
    lock_background: Any      # black rect on the LOCK layer hiding all windows
    session_lock: Any        # active SessionLock, or None when unlocked
    locked: bool             # True while the screen is locked
    unmanaged_focus: Any     # focus-holding override-redirect surface, or None
    keycode: dict            # sym-name -> evdev-keycode
    bindings: dict           # (mods, code) -> action(server)
    passthrough: bool        # True forwards keys to the app, bypassing bindings
    pointer_constraints: Any  # wlr_pointer_constraints_v1: lock/confine manager
    relative_pointer_mgr: Any  # streams raw motion deltas to games/3D tools
    active_constraint: Any   # wlr_pointer_constraint_v1 in effect, or None
    constraints: list        # PointerConstraint records, for listener cleanup
    listeners: list[Any]


def clients_in(server: Server, workspace):
    """Clients assigned to `workspace`, preserving `Server.clients` order."""
    return [c for c in server.clients if c.workspace is workspace]


def clients_visible(server: Server, monitor):
    """Clients shown on `monitor` (its active workspace's clients)."""
    if monitor is None or monitor.active_workspace is None:
        return []
    return clients_in(server, monitor.active_workspace)


def client_monitor(client: Client):
    """The monitor this window appears on, or None if orphaned/pre-map."""
    return client.workspace.monitor if client.workspace is not None else None

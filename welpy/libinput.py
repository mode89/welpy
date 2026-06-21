"""libinput per-device tuning for touchpads and mice.

Build-time: `contribute(builder)` injects the cdef for the handful of
libinput config probes/setters we call, plus the includes that resolve
them.

Run-time: `configure(server, device)` applies the module-level settings
below to a freshly plugged-in pointer. Each setting is only pushed when the
device reports it supports it, mirroring libinput's own probe-then-set
convention.

The settings are plain module globals so a user's `config.py` can override
them (`import libinput; libinput.natural_scrolling = True`) before any
device is configured.
"""

from __future__ import annotations

import logging
from typing import Any


logger = logging.getLogger(__name__)


# Touchpad / mouse feel. Defaults follow dwl's out-of-the-box behaviour.
TAP_TO_CLICK = True
TAP_AND_DRAG = True
DRAG_LOCK = True
NATURAL_SCROLLING = False
DISABLE_WHILE_TYPING = True
LEFT_HANDED = False
MIDDLE_EMULATION = False
ACCEL_SPEED = 0.0  # -1.0 (slowest) .. 1.0 (fastest)

# String-keyed choices, mapped to libinput enum constants at apply time.
TAP_BUTTON_MAP = "lrm"        # lrm | lmr
SCROLL_METHOD = "two_finger"  # none | two_finger | edge | on_button_down
CLICK_METHOD = "button_areas"  # none | button_areas | clickfinger
SEND_EVENTS = "enabled"  # enabled | disabled | disabled_on_external_mouse
ACCEL_PROFILE = "adaptive"  # flat | adaptive


_TAP_BUTTON_MAPS = {
    "lrm": "LIBINPUT_CONFIG_TAP_MAP_LRM",
    "lmr": "LIBINPUT_CONFIG_TAP_MAP_LMR",
}
_SCROLL_METHODS = {
    "none": "LIBINPUT_CONFIG_SCROLL_NO_SCROLL",
    "two_finger": "LIBINPUT_CONFIG_SCROLL_2FG",
    "edge": "LIBINPUT_CONFIG_SCROLL_EDGE",
    "on_button_down": "LIBINPUT_CONFIG_SCROLL_ON_BUTTON_DOWN",
}
_CLICK_METHODS = {
    "none": "LIBINPUT_CONFIG_CLICK_METHOD_NONE",
    "button_areas": "LIBINPUT_CONFIG_CLICK_METHOD_BUTTON_AREAS",
    "clickfinger": "LIBINPUT_CONFIG_CLICK_METHOD_CLICKFINGER",
}
_SEND_EVENTS = {
    "enabled": "LIBINPUT_CONFIG_SEND_EVENTS_ENABLED",
    "disabled": "LIBINPUT_CONFIG_SEND_EVENTS_DISABLED",
    "disabled_on_external_mouse":
        "LIBINPUT_CONFIG_SEND_EVENTS_DISABLED_ON_EXTERNAL_MOUSE",
}
_ACCEL_PROFILES = {
    "flat": "LIBINPUT_CONFIG_ACCEL_PROFILE_FLAT",
    "adaptive": "LIBINPUT_CONFIG_ACCEL_PROFILE_ADAPTIVE",
}


def configure(server, device) -> None:
    """Apply the tuning settings to a libinput-backed pointer; ignore
    devices that aren't libinput (e.g. the nested backend's virtual mouse)."""
    lib, ffi = server.lib, server.ffi
    if not lib.wlr_input_device_is_libinput(device):
        return
    handle = lib.wlr_libinput_get_device_handle(device)
    if handle == ffi.NULL:
        return

    if lib.libinput_device_config_tap_get_finger_count(handle):
        lib.libinput_device_config_tap_set_enabled(handle, int(TAP_TO_CLICK))
        lib.libinput_device_config_tap_set_drag_enabled(
            handle, int(TAP_AND_DRAG))
        lib.libinput_device_config_tap_set_drag_lock_enabled(
            handle, int(DRAG_LOCK))
        lib.libinput_device_config_tap_set_button_map(
            handle, _enum(lib, _TAP_BUTTON_MAPS, TAP_BUTTON_MAP))

    if lib.libinput_device_config_scroll_has_natural_scroll(handle):
        lib.libinput_device_config_scroll_set_natural_scroll_enabled(
            handle, int(NATURAL_SCROLLING))

    if lib.libinput_device_config_dwt_is_available(handle):
        lib.libinput_device_config_dwt_set_enabled(
            handle, int(DISABLE_WHILE_TYPING))

    if lib.libinput_device_config_left_handed_is_available(handle):
        lib.libinput_device_config_left_handed_set(handle, int(LEFT_HANDED))

    if lib.libinput_device_config_middle_emulation_is_available(handle):
        lib.libinput_device_config_middle_emulation_set_enabled(
            handle, int(MIDDLE_EMULATION))

    if lib.libinput_device_config_scroll_get_methods(handle) != \
            lib.LIBINPUT_CONFIG_SCROLL_NO_SCROLL:
        lib.libinput_device_config_scroll_set_method(
            handle, _enum(lib, _SCROLL_METHODS, SCROLL_METHOD))

    if lib.libinput_device_config_click_get_methods(handle) != \
            lib.LIBINPUT_CONFIG_CLICK_METHOD_NONE:
        lib.libinput_device_config_click_set_method(
            handle, _enum(lib, _CLICK_METHODS, CLICK_METHOD))

    if lib.libinput_device_config_send_events_get_modes(handle):
        lib.libinput_device_config_send_events_set_mode(
            handle, _enum(lib, _SEND_EVENTS, SEND_EVENTS))

    if lib.libinput_device_config_accel_is_available(handle):
        lib.libinput_device_config_accel_set_profile(
            handle, _enum(lib, _ACCEL_PROFILES, ACCEL_PROFILE))
        lib.libinput_device_config_accel_set_speed(handle, float(ACCEL_SPEED))


def _enum(lib, choices: dict, key: str) -> Any:
    """Resolve a config string to its libinput enum value, defaulting to the
    first choice on an unknown key so a typo can't crash device setup."""
    name = choices.get(key)
    if name is None:
        name = next(iter(choices.values()))
        logger.warning("unknown libinput option %r; using default", key)
    return getattr(lib, name)


def contribute(builder) -> None:
    """Inject the libinput config bindings into the cffi build."""
    builder.append(cdef=_CDEF, source=_SOURCE, pkgs=("libinput",))


_CDEF = r"""
struct libinput_device;
bool wlr_input_device_is_libinput(struct wlr_input_device *);
struct libinput_device *wlr_libinput_get_device_handle(
        struct wlr_input_device *);

int libinput_device_config_tap_get_finger_count(struct libinput_device *);
int libinput_device_config_tap_set_enabled(struct libinput_device *, int);
int libinput_device_config_tap_set_drag_enabled(struct libinput_device *, int);
int libinput_device_config_tap_set_drag_lock_enabled(
        struct libinput_device *, int);
int libinput_device_config_tap_set_button_map(struct libinput_device *, int);
int libinput_device_config_scroll_has_natural_scroll(struct libinput_device *);
int libinput_device_config_scroll_set_natural_scroll_enabled(
        struct libinput_device *, int);
int libinput_device_config_dwt_is_available(struct libinput_device *);
int libinput_device_config_dwt_set_enabled(struct libinput_device *, int);
int libinput_device_config_left_handed_is_available(struct libinput_device *);
int libinput_device_config_left_handed_set(struct libinput_device *, int);
int libinput_device_config_middle_emulation_is_available(
        struct libinput_device *);
int libinput_device_config_middle_emulation_set_enabled(
        struct libinput_device *, int);
uint32_t libinput_device_config_scroll_get_methods(struct libinput_device *);
int libinput_device_config_scroll_set_method(struct libinput_device *, int);
uint32_t libinput_device_config_click_get_methods(struct libinput_device *);
int libinput_device_config_click_set_method(struct libinput_device *, int);
uint32_t libinput_device_config_send_events_get_modes(struct libinput_device *);
int libinput_device_config_send_events_set_mode(struct libinput_device *,
        uint32_t);
int libinput_device_config_accel_is_available(struct libinput_device *);
int libinput_device_config_accel_set_profile(struct libinput_device *, int);
int libinput_device_config_accel_set_speed(struct libinput_device *, double);

#define LIBINPUT_CONFIG_TAP_MAP_LRM ...
#define LIBINPUT_CONFIG_TAP_MAP_LMR ...
#define LIBINPUT_CONFIG_SCROLL_NO_SCROLL ...
#define LIBINPUT_CONFIG_SCROLL_2FG ...
#define LIBINPUT_CONFIG_SCROLL_EDGE ...
#define LIBINPUT_CONFIG_SCROLL_ON_BUTTON_DOWN ...
#define LIBINPUT_CONFIG_CLICK_METHOD_NONE ...
#define LIBINPUT_CONFIG_CLICK_METHOD_BUTTON_AREAS ...
#define LIBINPUT_CONFIG_CLICK_METHOD_CLICKFINGER ...
#define LIBINPUT_CONFIG_SEND_EVENTS_ENABLED ...
#define LIBINPUT_CONFIG_SEND_EVENTS_DISABLED ...
#define LIBINPUT_CONFIG_SEND_EVENTS_DISABLED_ON_EXTERNAL_MOUSE ...
#define LIBINPUT_CONFIG_ACCEL_PROFILE_FLAT ...
#define LIBINPUT_CONFIG_ACCEL_PROFILE_ADAPTIVE ...
"""


_SOURCE = r"""
#include <libinput.h>
#include <wlr/backend/libinput.h>
"""

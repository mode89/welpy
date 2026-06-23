"""Unit tests for welpy.libinput: applying per-device libinput settings."""

from unittest.mock import patch

from welpy import libinput
from tests.helpers import make_server


def test_libinput_skips_nonlibinput():
    """A pointer from the nested backend isn't libinput-backed, so it gets
    attached to the cursor without any libinput config calls."""
    server = make_server()
    server.lib.wlr_input_device_is_libinput.return_value = False

    libinput.configure(server, "DEVICE")

    server.lib.wlr_libinput_get_device_handle.assert_not_called()
    server.lib.libinput_device_config_tap_set_enabled.assert_not_called()


def test_libinput_null_handle():
    """If wlroots can't hand back a libinput device, configuration is a
    no-op rather than dereferencing a null pointer."""
    server = make_server()
    server.lib.wlr_input_device_is_libinput.return_value = True
    server.lib.wlr_libinput_get_device_handle.return_value = server.ffi.NULL

    libinput.configure(server, "DEVICE")

    server.lib.libinput_device_config_tap_set_enabled.assert_not_called()


def test_libinput_applies_settings():
    """Each supported knob is pushed to the device, with string choices
    resolved to the matching libinput enum value."""
    server = make_server()
    lib = server.lib
    lib.wlr_input_device_is_libinput.return_value = True
    lib.wlr_libinput_get_device_handle.return_value = "HANDLE"
    lib.libinput_device_config_tap_get_finger_count.return_value = 2
    lib.LIBINPUT_CONFIG_SCROLL_NO_SCROLL = 0
    lib.libinput_device_config_scroll_get_methods.return_value = 1
    lib.LIBINPUT_CONFIG_CLICK_METHOD_NONE = 0
    lib.libinput_device_config_click_get_methods.return_value = 1
    lib.libinput_device_config_send_events_get_modes.return_value = 1
    lib.LIBINPUT_CONFIG_TAP_MAP_LRM = "LRM"
    lib.LIBINPUT_CONFIG_SCROLL_2FG = "2FG"

    with patch.multiple(
            libinput, TAP_TO_CLICK=True, DRAG_LOCK=False,
            NATURAL_SCROLLING=True, SCROLL_METHOD="two_finger",
            TAP_BUTTON_MAP="lrm", ACCEL_SPEED=0.5):
        libinput.configure(server, "DEVICE")

    lib.libinput_device_config_tap_set_enabled.assert_called_once_with(
        "HANDLE", 1)
    lib.libinput_device_config_tap_set_drag_lock_enabled \
        .assert_called_once_with("HANDLE", 0)
    lib.libinput_device_config_tap_set_button_map.assert_called_once_with(
        "HANDLE", "LRM")
    lib.libinput_device_config_scroll_set_natural_scroll_enabled \
        .assert_called_once_with("HANDLE", 1)
    lib.libinput_device_config_scroll_set_method.assert_called_once_with(
        "HANDLE", "2FG")
    lib.libinput_device_config_accel_set_speed.assert_called_once_with(
        "HANDLE", 0.5)


def test_libinput_unsupported_skipped():
    """Knobs a device doesn't advertise are left untouched; here tapping
    isn't available so no tap setting is sent."""
    server = make_server()
    lib = server.lib
    lib.wlr_input_device_is_libinput.return_value = True
    lib.wlr_libinput_get_device_handle.return_value = "HANDLE"
    lib.libinput_device_config_tap_get_finger_count.return_value = 0

    libinput.configure(server, "DEVICE")

    lib.libinput_device_config_tap_set_enabled.assert_not_called()


def test_libinput_unknown_choice_defaults():
    """An unrecognized string falls back to the first choice instead of
    raising, so a config typo can't crash device setup."""
    server = make_server()
    server.lib.LIBINPUT_CONFIG_TAP_MAP_LRM = "LRM"

    result = libinput._enum(  # pylint: disable=protected-access
        server.lib, libinput._TAP_BUTTON_MAPS, "bogus")  # pylint: disable=protected-access

    assert result == "LRM"

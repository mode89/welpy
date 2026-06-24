"""libinput config bindings: the device config probes/setters we call."""


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

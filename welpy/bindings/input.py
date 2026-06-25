"""Bindings: seat, keyboard, pointer/cursor, xkb, and the
pointer-constraints/relative-pointer protocols."""


def register(builder) -> None:
    """Inject this group's cdef and C glue into the build."""
    # wlr_pointer_constraints_v1.h pulls in the generated enum-only header.
    builder.enum_header(
        "wayland-protocols",
        "unstable/pointer-constraints/pointer-constraints-unstable-v1.xml",
        "pointer-constraints-unstable-v1")
    builder.append(cdef=_CDEF, source=_SOURCE)


_CDEF = r"""
void wlr_seat_set_selection(struct wlr_seat *,
        struct wlr_data_source *, uint32_t serial);

void wlr_seat_set_primary_selection(struct wlr_seat *,
        struct wlr_primary_selection_source *, uint32_t serial);

struct wlr_seat *wlr_seat_create(struct wl_display *, const char *);

void wlr_seat_set_capabilities(struct wlr_seat *, uint32_t caps);

void wlr_seat_set_keyboard(struct wlr_seat *, struct wlr_keyboard *);

void wlr_seat_keyboard_notify_key(struct wlr_seat *, uint32_t time_msec,
        uint32_t key, uint32_t state);

void wlr_seat_keyboard_notify_modifiers(struct wlr_seat *,
        struct wlr_keyboard_modifiers *modifiers);

void wlr_seat_keyboard_notify_enter(struct wlr_seat *, struct wlr_surface *,
        const uint32_t keycodes[], size_t num_keycodes,
        struct wlr_keyboard_modifiers *modifiers);

struct wlr_keyboard *wlr_keyboard_from_input_device(struct wlr_input_device *);

bool wlr_keyboard_set_keymap(struct wlr_keyboard *, struct xkb_keymap *);

void wlr_keyboard_set_repeat_info(
        struct wlr_keyboard *, int32_t rate_hz, int32_t delay_ms);

uint32_t wlr_keyboard_get_modifiers(struct wlr_keyboard *);

struct wlr_keyboard *wlr_seat_get_keyboard(struct wlr_seat *);

void wlr_seat_keyboard_clear_focus(struct wlr_seat *);

struct wlr_keyboard_group *wlr_keyboard_group_create(void);

void wlr_keyboard_group_destroy(struct wlr_keyboard_group *);

bool wlr_keyboard_group_add_keyboard(struct wlr_keyboard_group *,
        struct wlr_keyboard *);

struct wlr_cursor *wlr_cursor_create(void);

void wlr_cursor_destroy(struct wlr_cursor *);

void wlr_cursor_attach_output_layout(
        struct wlr_cursor *, struct wlr_output_layout *);

void wlr_cursor_attach_input_device(
        struct wlr_cursor *, struct wlr_input_device *);

void wlr_cursor_move(struct wlr_cursor *, struct wlr_input_device *,
        double delta_x, double delta_y);

bool wlr_cursor_warp(struct wlr_cursor *, struct wlr_input_device *,
        double lx, double ly);

void wlr_cursor_warp_absolute(struct wlr_cursor *, struct wlr_input_device *,
        double x, double y);

void wlr_cursor_warp_closest(struct wlr_cursor *, struct wlr_input_device *,
        double x, double y);

void wlr_cursor_absolute_to_layout_coords(struct wlr_cursor *,
        struct wlr_input_device *, double x, double y,
        double *lx, double *ly);

void wlr_cursor_set_xcursor(struct wlr_cursor *,
        struct wlr_xcursor_manager *, const char *);

void wlr_cursor_set_surface(struct wlr_cursor *,
        struct wlr_surface *, int32_t hotspot_x, int32_t hotspot_y);

struct wlr_xcursor_manager *wlr_xcursor_manager_create(
        const char *name, uint32_t size);

void wlr_xcursor_manager_destroy(struct wlr_xcursor_manager *);

bool wlr_xcursor_manager_load(struct wlr_xcursor_manager *, float scale);

void wlr_seat_pointer_notify_enter(struct wlr_seat *, struct wlr_surface *,
        double sx, double sy);

void wlr_seat_pointer_notify_motion(
        struct wlr_seat *, uint32_t time_msec, double sx, double sy);

uint32_t wlr_seat_pointer_notify_button(struct wlr_seat *,
        uint32_t time_msec, uint32_t button, uint32_t state);

void wlr_seat_pointer_notify_axis(struct wlr_seat *, uint32_t time_msec,
        uint32_t orientation, double value, int32_t value_discrete,
        uint32_t source, uint32_t relative_direction);

void wlr_seat_pointer_notify_frame(struct wlr_seat *);

void wlr_seat_pointer_clear_focus(struct wlr_seat *);

struct xkb_context *xkb_context_new(int flags);

void xkb_context_unref(struct xkb_context *);

struct xkb_keymap *xkb_keymap_new_from_names(struct xkb_context *,
        const struct xkb_rule_names *names, int flags);

void xkb_keymap_unref(struct xkb_keymap *);

// enum wlr_input_device_type
#define WLR_INPUT_DEVICE_KEYBOARD ...

#define WLR_INPUT_DEVICE_POINTER ...

// wl_seat capability bits
#define WL_SEAT_CAPABILITY_POINTER ...

#define WL_SEAT_CAPABILITY_KEYBOARD ...

// wl_pointer button state
#define WL_POINTER_BUTTON_STATE_PRESSED ...

#define WL_KEYBOARD_KEY_STATE_PRESSED ...

// enum wlr_keyboard_modifier
#define WLR_MODIFIER_ALT ...

#define WLR_MODIFIER_SHIFT ...

#define WLR_MODIFIER_CTRL ...

#define WLR_MODIFIER_LOGO ...

struct wlr_pointer_constraints_v1 *wlr_pointer_constraints_v1_create(
        struct wl_display *);

struct wlr_pointer_constraint_v1 *
        wlr_pointer_constraints_v1_constraint_for_surface(
        struct wlr_pointer_constraints_v1 *, struct wlr_surface *,
        struct wlr_seat *);

void wlr_pointer_constraint_v1_send_activated(
        struct wlr_pointer_constraint_v1 *);

void wlr_pointer_constraint_v1_send_deactivated(
        struct wlr_pointer_constraint_v1 *);

struct wl_signal *welpy_pointer_constraints_new_constraint(
        struct wlr_pointer_constraints_v1 *);

struct wl_signal *welpy_pointer_constraint_destroy(
        struct wlr_pointer_constraint_v1 *);

bool welpy_constraint_confine(struct wlr_pointer_constraint_v1 *,
        double x1, double y1, double x2, double y2,
        double *x_out, double *y_out);

bool welpy_constraint_cursor_hint(struct wlr_pointer_constraint_v1 *,
        double *x, double *y);

struct wlr_relative_pointer_manager_v1 *
        wlr_relative_pointer_manager_v1_create(struct wl_display *);

struct wlr_virtual_keyboard_manager_v1 *
        wlr_virtual_keyboard_manager_v1_create(struct wl_display *);

struct wl_signal *welpy_virtual_keyboard_mgr_new(
        struct wlr_virtual_keyboard_manager_v1 *);

void wlr_relative_pointer_manager_v1_send_relative_motion(
        struct wlr_relative_pointer_manager_v1 *, struct wlr_seat *,
        uint64_t time_usec, double dx, double dy,
        double dx_unaccel, double dy_unaccel);

// keymap / keysym helpers
uint32_t xkb_keymap_min_keycode(struct xkb_keymap *);

uint32_t xkb_keymap_max_keycode(struct xkb_keymap *);

int xkb_keymap_key_get_syms_by_level(struct xkb_keymap *, uint32_t keycode,
        uint32_t layout, uint32_t level, const uint32_t **syms_out);

int xkb_keysym_get_name(uint32_t keysym, char *buffer, size_t size);

uint32_t xkb_keysym_from_name(const char *name, int flags);

// First xkb keysym for an event keycode on this keyboard, or 0.
uint32_t welpy_keyboard_keysym(struct wlr_keyboard *kb, uint32_t keycode);

// linux/input-event-codes.h
#define BTN_LEFT ...

#define BTN_RIGHT ...

#define BTN_MIDDLE ...

struct wl_signal *welpy_input_device_destroy_signal(struct wlr_input_device *);

struct wl_signal *welpy_seat_request_set_selection(struct wlr_seat *);

struct wl_signal *welpy_seat_request_set_cursor(struct wlr_seat *);

struct wl_signal *welpy_seat_request_set_primary_selection(struct wlr_seat *);

struct wlr_seat_client *welpy_seat_pointer_focused_client(struct wlr_seat *);

struct wlr_keyboard *welpy_keyboard_group_keyboard(struct wlr_keyboard_group *);

// cursor signals (events.* lives in an anonymous sub-struct)
struct wl_signal *welpy_cursor_motion(struct wlr_cursor *);

struct wl_signal *welpy_cursor_motion_absolute(struct wlr_cursor *);

struct wl_signal *welpy_cursor_button(struct wlr_cursor *);

struct wl_signal *welpy_cursor_axis(struct wlr_cursor *);

struct wl_signal *welpy_cursor_frame(struct wlr_cursor *);

// keyboard input field accessors (struct layout we don't want to declare)
struct wl_signal *welpy_backend_new_input(struct wlr_backend *);

struct wl_signal *welpy_keyboard_key_signal(struct wlr_keyboard *);

struct wl_signal *welpy_keyboard_modifiers_signal(struct wlr_keyboard *);

struct wl_signal *welpy_keyboard_destroy_signal(struct wlr_keyboard *);
"""


_SOURCE = r"""
uint32_t welpy_keyboard_keysym(struct wlr_keyboard *kb, uint32_t keycode) {
    // Return the unshifted (level-0) keysym for binding lookup. This way
    // a config entry of "q" matches both `q` and `Shift+q`, and the
    // explicit Shift bit in the binding's mod mask disambiguates them.
    // wlr_keyboard_key_event.keycode is evdev; xkb expects +8.
    const xkb_keysym_t *syms;
    xkb_keycode_t kc = keycode + 8;
    xkb_layout_index_t layout = xkb_state_key_get_layout(kb->xkb_state, kc);
    int n = xkb_keymap_key_get_syms_by_level(kb->keymap, kc, layout, 0, &syms);
    return n > 0 ? (uint32_t)syms[0] : 0;
}

struct wl_signal *welpy_input_device_destroy_signal(struct wlr_input_device *d) {
    return &d->events.destroy;
}

struct wl_signal *welpy_backend_new_input(struct wlr_backend *b) {
    return &b->events.new_input;
}

struct wl_signal *welpy_keyboard_key_signal(struct wlr_keyboard *k) {
    return &k->events.key;
}

struct wl_signal *welpy_keyboard_modifiers_signal(struct wlr_keyboard *k) {
    return &k->events.modifiers;
}

struct wl_signal *welpy_keyboard_destroy_signal(struct wlr_keyboard *k) {
    return &k->base.events.destroy;
}

struct wl_signal *welpy_seat_request_set_selection(struct wlr_seat *s) {
    return &s->events.request_set_selection;
}

struct wl_signal *welpy_cursor_motion(struct wlr_cursor *c) {
    return &c->events.motion;
}

struct wl_signal *welpy_cursor_motion_absolute(struct wlr_cursor *c) {
    return &c->events.motion_absolute;
}

struct wl_signal *welpy_cursor_button(struct wlr_cursor *c) {
    return &c->events.button;
}

struct wl_signal *welpy_cursor_axis(struct wlr_cursor *c) {
    return &c->events.axis;
}

struct wl_signal *welpy_cursor_frame(struct wlr_cursor *c) {
    return &c->events.frame;
}

struct wl_signal *welpy_seat_request_set_cursor(struct wlr_seat *s) {
    return &s->events.request_set_cursor;
}

struct wl_signal *welpy_seat_request_set_primary_selection(struct wlr_seat *s) {
    return &s->events.request_set_primary_selection;
}

struct wlr_seat_client *welpy_seat_pointer_focused_client(struct wlr_seat *s) {
    return s->pointer_state.focused_client;
}

struct wlr_keyboard *welpy_keyboard_group_keyboard(struct wlr_keyboard_group *g) {
    return &g->keyboard;
}

struct wl_signal *welpy_pointer_constraints_new_constraint(
        struct wlr_pointer_constraints_v1 *c) {
    return &c->events.new_constraint;
}

struct wl_signal *welpy_virtual_keyboard_mgr_new(
        struct wlr_virtual_keyboard_manager_v1 *m) {
    return &m->events.new_virtual_keyboard;
}

struct wl_signal *welpy_pointer_constraint_destroy(
        struct wlr_pointer_constraint_v1 *c) {
    return &c->events.destroy;
}

bool welpy_constraint_confine(struct wlr_pointer_constraint_v1 *c,
        double x1, double y1, double x2, double y2,
        double *x_out, double *y_out) {
    return wlr_region_confine(&c->region, x1, y1, x2, y2, x_out, y_out);
}

bool welpy_constraint_cursor_hint(struct wlr_pointer_constraint_v1 *c,
        double *x, double *y) {
    if (!c->current.cursor_hint.enabled)
        return false;
    *x = c->current.cursor_hint.x;
    *y = c->current.cursor_hint.y;
    return true;
}
"""

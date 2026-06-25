"""Bindings: the embedded X server and its X11/override-redirect surfaces."""


def register(builder) -> None:
    """Inject this group's cdef and C glue into the build."""
    builder.append(cdef=_CDEF, source=_SOURCE)


_CDEF = r"""
// xwayland: embedded X server for legacy X11 apps
struct wlr_xwayland *wlr_xwayland_create(
        struct wl_display *, struct wlr_compositor *, bool lazy);

void wlr_xwayland_destroy(struct wlr_xwayland *);

void wlr_xwayland_set_seat(struct wlr_xwayland *, struct wlr_seat *);

void wlr_xwayland_surface_activate(
        struct wlr_xwayland_surface *, bool activated);

void wlr_xwayland_surface_configure(struct wlr_xwayland_surface *,
        int16_t x, int16_t y, uint16_t width, uint16_t height);

void wlr_xwayland_surface_close(struct wlr_xwayland_surface *);

void wlr_xwayland_surface_set_fullscreen(
        struct wlr_xwayland_surface *, bool fullscreen);

bool wlr_xwayland_surface_override_redirect_wants_focus(
        const struct wlr_xwayland_surface *);

struct wlr_xwayland_surface *wlr_xwayland_surface_try_from_wlr_surface(
        struct wlr_surface *);

void welpy_xwayland_set_default_cursor(
        struct wlr_xwayland *, struct wlr_xcursor_manager *);

struct wl_signal *welpy_xwayland_ready(struct wlr_xwayland *);

struct wl_signal *welpy_xwayland_new_surface(struct wlr_xwayland *);

struct wl_signal *welpy_xwayland_surface_associate(
        struct wlr_xwayland_surface *);

struct wl_signal *welpy_xwayland_surface_dissociate(
        struct wlr_xwayland_surface *);

struct wl_signal *welpy_xwayland_surface_destroy(
        struct wlr_xwayland_surface *);

struct wl_signal *welpy_xwayland_surface_request_configure(
        struct wlr_xwayland_surface *);

struct wl_signal *welpy_xwayland_surface_request_fullscreen(
        struct wlr_xwayland_surface *);

struct wl_signal *welpy_xwayland_surface_request_activate(
        struct wlr_xwayland_surface *);

struct wl_signal *welpy_xwayland_surface_set_hints(
        struct wlr_xwayland_surface *);

bool welpy_xwayland_surface_is_urgent(struct wlr_xwayland_surface *);
"""


_SOURCE = r"""
void welpy_xwayland_set_default_cursor(struct wlr_xwayland *xwayland,
        struct wlr_xcursor_manager *mgr) {
    struct wlr_xcursor *xcursor =
        wlr_xcursor_manager_get_xcursor(mgr, "default", 1);
    if (xcursor) {
        struct wlr_xcursor_image *image = xcursor->images[0];
        wlr_xwayland_set_cursor(xwayland, wlr_xcursor_image_get_buffer(image),
            image->hotspot_x, image->hotspot_y);
    }
}

struct wl_signal *welpy_xwayland_ready(struct wlr_xwayland *x) {
    return &x->events.ready;
}

struct wl_signal *welpy_xwayland_new_surface(struct wlr_xwayland *x) {
    return &x->events.new_surface;
}

struct wl_signal *welpy_xwayland_surface_associate(
        struct wlr_xwayland_surface *s) {
    return &s->events.associate;
}

struct wl_signal *welpy_xwayland_surface_dissociate(
        struct wlr_xwayland_surface *s) {
    return &s->events.dissociate;
}

struct wl_signal *welpy_xwayland_surface_destroy(
        struct wlr_xwayland_surface *s) {
    return &s->events.destroy;
}

struct wl_signal *welpy_xwayland_surface_request_configure(
        struct wlr_xwayland_surface *s) {
    return &s->events.request_configure;
}

struct wl_signal *welpy_xwayland_surface_request_fullscreen(
        struct wlr_xwayland_surface *s) {
    return &s->events.request_fullscreen;
}

struct wl_signal *welpy_xwayland_surface_request_activate(
        struct wlr_xwayland_surface *s) {
    return &s->events.request_activate;
}

struct wl_signal *welpy_xwayland_surface_set_hints(
        struct wlr_xwayland_surface *s) {
    return &s->events.set_hints;
}

bool welpy_xwayland_surface_is_urgent(struct wlr_xwayland_surface *s) {
    return s->hints && xcb_icccm_wm_hints_get_urgency(s->hints);
}
"""

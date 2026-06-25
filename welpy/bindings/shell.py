"""Bindings: xdg-shell windows/popups plus the xdg/server decoration and
xdg-activation protocols."""


def register(builder) -> None:
    """Inject this group's cdef and C glue into the build."""
    # wlroots links xdg-shell's marshalling code; we need only its headers.
    builder.scanner(
        "wayland-protocols", "stable/xdg-shell/xdg-shell.xml",
        "xdg-shell", private_code=False)
    # wlroots 0.20's wlr_xdg_shell.h pulls in the generated enum-only header.
    builder.enum_header(
        "wayland-protocols", "stable/xdg-shell/xdg-shell.xml", "xdg-shell")
    builder.append(cdef=_CDEF, source=_SOURCE)


_CDEF = r"""
struct wlr_xdg_shell *wlr_xdg_shell_create(
        struct wl_display *, uint32_t version);

uint32_t wlr_xdg_toplevel_set_size(struct wlr_xdg_toplevel *, int32_t, int32_t);

uint32_t wlr_xdg_toplevel_set_activated(struct wlr_xdg_toplevel *, bool activated);

uint32_t wlr_xdg_toplevel_set_fullscreen(struct wlr_xdg_toplevel *, bool);

uint32_t wlr_xdg_toplevel_set_maximized(struct wlr_xdg_toplevel *, bool);

uint32_t wlr_xdg_toplevel_set_tiled(struct wlr_xdg_toplevel *, uint32_t edges);

void wlr_xdg_toplevel_set_bounds(struct wlr_xdg_toplevel *, int32_t w, int32_t h);

void wlr_xdg_toplevel_set_wm_capabilities(struct wlr_xdg_toplevel *, uint32_t caps);

void wlr_xdg_toplevel_send_close(struct wlr_xdg_toplevel *);

struct wlr_xdg_surface *wlr_xdg_surface_try_from_wlr_surface(struct wlr_surface *);

struct wlr_xdg_toplevel *wlr_xdg_toplevel_try_from_wlr_surface(
        struct wlr_surface *);

struct wlr_xdg_popup *wlr_xdg_popup_try_from_wlr_surface(struct wlr_surface *);

void wlr_xdg_surface_schedule_configure(struct wlr_xdg_surface *);

void wlr_xdg_popup_unconstrain_from_box(
        struct wlr_xdg_popup *, const struct wlr_box *);

// xdg-toplevel WM capability bits
#define WLR_XDG_TOPLEVEL_WM_CAPABILITIES_FULLSCREEN ...

// xdg-toplevel resize edge bits
#define WLR_EDGE_NONE ...

#define WLR_EDGE_TOP ...

#define WLR_EDGE_BOTTOM ...

#define WLR_EDGE_LEFT ...

#define WLR_EDGE_RIGHT ...

// xdg-decoration
#define WLR_XDG_TOPLEVEL_DECORATION_V1_MODE_SERVER_SIDE ...

struct wlr_xdg_decoration_manager_v1 *wlr_xdg_decoration_manager_v1_create(
        struct wl_display *);

uint32_t wlr_xdg_toplevel_decoration_v1_set_mode(
        struct wlr_xdg_toplevel_decoration_v1 *, uint32_t mode);

struct wlr_xdg_activation_v1 *wlr_xdg_activation_v1_create(
        struct wl_display *);

struct wl_signal *welpy_xdg_activation_request_activate(
        struct wlr_xdg_activation_v1 *);

// server-decoration (predecessor to xdg-decoration; obsolete but still used
// by some clients)
#define WLR_SERVER_DECORATION_MANAGER_MODE_SERVER ...

struct wlr_server_decoration_manager *wlr_server_decoration_manager_create(
        struct wl_display *);

void wlr_server_decoration_manager_set_default_mode(
        struct wlr_server_decoration_manager *, uint32_t default_mode);

struct wl_signal *welpy_xdg_shell_new_toplevel(struct wlr_xdg_shell *);

struct wl_signal *welpy_xdg_toplevel_destroy(struct wlr_xdg_toplevel *);

struct wl_signal *welpy_xdg_toplevel_set_title(struct wlr_xdg_toplevel *);

struct wl_signal *welpy_xdg_toplevel_request_maximize(struct wlr_xdg_toplevel *);

struct wl_signal *welpy_xdg_shell_new_popup(struct wlr_xdg_shell *);

struct wl_signal *welpy_xdg_popup_destroy(struct wlr_xdg_popup *);

struct wl_signal *welpy_xdg_toplevel_request_fullscreen(struct wlr_xdg_toplevel *);

struct wl_signal *welpy_xdg_toplevel_set_app_id(struct wlr_xdg_toplevel *);

struct wl_signal *welpy_xdg_decoration_manager_new(
        struct wlr_xdg_decoration_manager_v1 *);

struct wl_signal *welpy_xdg_decoration_request_mode(
        struct wlr_xdg_toplevel_decoration_v1 *);

struct wl_signal *welpy_xdg_decoration_destroy(
        struct wlr_xdg_toplevel_decoration_v1 *);
"""


_SOURCE = r"""
struct wl_signal *welpy_xdg_shell_new_toplevel(struct wlr_xdg_shell *s) {
    return &s->events.new_toplevel;
}

struct wl_signal *welpy_xdg_toplevel_destroy(struct wlr_xdg_toplevel *t) {
    return &t->events.destroy;
}

struct wl_signal *welpy_xdg_shell_new_popup(struct wlr_xdg_shell *s) {
    return &s->events.new_popup;
}

struct wl_signal *welpy_xdg_popup_destroy(struct wlr_xdg_popup *p) {
    return &p->events.destroy;
}

struct wl_signal *welpy_xdg_toplevel_set_title(struct wlr_xdg_toplevel *t) {
    return &t->events.set_title;
}

struct wl_signal *welpy_xdg_toplevel_request_maximize(struct wlr_xdg_toplevel *t) {
    return &t->events.request_maximize;
}

struct wl_signal *welpy_xdg_toplevel_request_fullscreen(struct wlr_xdg_toplevel *t) {
    return &t->events.request_fullscreen;
}

struct wl_signal *welpy_xdg_toplevel_set_app_id(struct wlr_xdg_toplevel *t) {
    return &t->events.set_app_id;
}

struct wl_signal *welpy_xdg_decoration_manager_new(
        struct wlr_xdg_decoration_manager_v1 *m) {
    return &m->events.new_toplevel_decoration;
}

struct wl_signal *welpy_xdg_decoration_request_mode(
        struct wlr_xdg_toplevel_decoration_v1 *d) {
    return &d->events.request_mode;
}

struct wl_signal *welpy_xdg_decoration_destroy(
        struct wlr_xdg_toplevel_decoration_v1 *d) {
    return &d->events.destroy;
}

struct wl_signal *welpy_xdg_activation_request_activate(
        struct wlr_xdg_activation_v1 *a) {
    return &a->events.request_activate;
}
"""

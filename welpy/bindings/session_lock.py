"""Bindings: session-lock (screen lockers)."""


def register(builder) -> None:
    """Inject this group's cdef and C glue into the build."""
    builder.append(cdef=_CDEF, source=_SOURCE)


_CDEF = r"""
struct wlr_session_lock_manager_v1 *wlr_session_lock_manager_v1_create(
        struct wl_display *);

void wlr_session_lock_v1_send_locked(struct wlr_session_lock_v1 *);

void wlr_session_lock_v1_destroy(struct wlr_session_lock_v1 *);

uint32_t wlr_session_lock_surface_v1_configure(
        struct wlr_session_lock_surface_v1 *, uint32_t w, uint32_t h);

struct wl_signal *welpy_session_lock_mgr_new_lock(
        struct wlr_session_lock_manager_v1 *);

struct wl_signal *welpy_session_lock_new_surface(struct wlr_session_lock_v1 *);

struct wl_signal *welpy_session_lock_unlock(struct wlr_session_lock_v1 *);

struct wl_signal *welpy_session_lock_destroy(struct wlr_session_lock_v1 *);

struct wl_signal *welpy_session_lock_surface_destroy(
        struct wlr_session_lock_surface_v1 *);
"""


_SOURCE = r"""
struct wl_signal *welpy_session_lock_mgr_new_lock(
        struct wlr_session_lock_manager_v1 *m) {
    return &m->events.new_lock;
}

struct wl_signal *welpy_session_lock_new_surface(
        struct wlr_session_lock_v1 *l) {
    return &l->events.new_surface;
}

struct wl_signal *welpy_session_lock_unlock(struct wlr_session_lock_v1 *l) {
    return &l->events.unlock;
}

struct wl_signal *welpy_session_lock_destroy(struct wlr_session_lock_v1 *l) {
    return &l->events.destroy;
}

struct wl_signal *welpy_session_lock_surface_destroy(
        struct wlr_session_lock_surface_v1 *s) {
    return &s->events.destroy;
}
"""

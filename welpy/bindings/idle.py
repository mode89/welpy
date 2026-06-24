"""Bindings: idle-notify and idle-inhibit."""


def contribute(builder) -> None:
    """Inject this group's cdef and C glue into the build."""
    builder.append(cdef=_CDEF, source=_SOURCE)


_CDEF = r"""
struct wlr_idle_notifier_v1 *wlr_idle_notifier_v1_create(struct wl_display *);

void wlr_idle_notifier_v1_set_inhibited(
        struct wlr_idle_notifier_v1 *, bool inhibited);

void wlr_idle_notifier_v1_notify_activity(
        struct wlr_idle_notifier_v1 *, struct wlr_seat *);

struct wlr_idle_inhibit_manager_v1 *wlr_idle_inhibit_v1_create(
        struct wl_display *);

struct wl_signal *welpy_idle_inhibit_new_inhibitor(
        struct wlr_idle_inhibit_manager_v1 *);

struct wl_signal *welpy_idle_inhibitor_destroy(
        struct wlr_idle_inhibitor_v1 *);

struct wlr_idle_inhibitor_v1 *welpy_idle_inhibitor_from_link(struct wl_list *);
"""


_SOURCE = r"""
struct wl_signal *welpy_idle_inhibit_new_inhibitor(
        struct wlr_idle_inhibit_manager_v1 *m) {
    return &m->events.new_inhibitor;
}

struct wl_signal *welpy_idle_inhibitor_destroy(
        struct wlr_idle_inhibitor_v1 *i) {
    return &i->events.destroy;
}

struct wlr_idle_inhibitor_v1 *welpy_idle_inhibitor_from_link(struct wl_list *l) {
    struct wlr_idle_inhibitor_v1 *inhibitor;
    return wl_container_of(l, inhibitor, link);
}
"""

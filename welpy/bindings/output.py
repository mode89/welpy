"""Bindings: monitors/outputs, the scene-output wiring, and the
output-management/power/gamma/xdg-output protocols."""


def register(builder) -> None:
    """Inject this group's cdef and C glue into the build."""
    builder.scanner(
        "wlr-protocols",
        "unstable/wlr-output-power-management-unstable-v1.xml",
        "wlr-output-power-management-unstable-v1", private_code=False)
    builder.append(cdef=_CDEF, source=_SOURCE)


_CDEF = r"""
struct wlr_output_layout *wlr_output_layout_create(struct wl_display *);

struct wlr_output_layout_output *wlr_output_layout_add_auto(
        struct wlr_output_layout *, struct wlr_output *);

void wlr_output_layout_remove(struct wlr_output_layout *, struct wlr_output *);

void wlr_output_layout_get_box(struct wlr_output_layout *,
        struct wlr_output *, struct wlr_box *dest);

bool wlr_output_init_render(struct wlr_output *,
        struct wlr_allocator *, struct wlr_renderer *);

void wlr_output_state_set_enabled(struct wlr_output_state *, bool enabled);

void wlr_output_state_set_mode(
        struct wlr_output_state *, struct wlr_output_mode *);

void wlr_output_state_set_custom_mode(
        struct wlr_output_state *, int32_t width, int32_t height,
        int32_t refresh);

void wlr_output_state_set_transform(struct wlr_output_state *, uint32_t);

void wlr_output_state_set_scale(struct wlr_output_state *, float);

void wlr_output_state_set_adaptive_sync_enabled(
        struct wlr_output_state *, bool);

struct wlr_output_mode *wlr_output_preferred_mode(struct wlr_output *);

bool wlr_output_commit_state(
        struct wlr_output *, const struct wlr_output_state *);

bool wlr_output_test_state(
        struct wlr_output *, const struct wlr_output_state *);

void wlr_output_layout_add(struct wlr_output_layout *,
        struct wlr_output *, int x, int y);

struct wlr_output_layout_output *wlr_output_layout_get(
        struct wlr_output_layout *, struct wlr_output *);

struct wlr_scene_output_layout *wlr_scene_attach_output_layout(
        struct wlr_scene *, struct wlr_output_layout *);

struct wlr_scene_output *wlr_scene_output_create(
        struct wlr_scene *, struct wlr_output *);

void wlr_scene_output_destroy(struct wlr_scene_output *);

void wlr_scene_output_layout_add_output(struct wlr_scene_output_layout *,
        struct wlr_output_layout_output *, struct wlr_scene_output *);

struct wlr_scene_output *wlr_scene_get_scene_output(
        struct wlr_scene *, struct wlr_output *);

void wlr_scene_output_set_position(struct wlr_scene_output *, int lx, int ly);

bool wlr_scene_output_commit(struct wlr_scene_output *, void *);

void wlr_scene_output_send_frame_done(struct wlr_scene_output *,
        struct timespec *);

struct wlr_xdg_output_manager_v1 *wlr_xdg_output_manager_v1_create(
        struct wl_display *, struct wlr_output_layout *);

struct wlr_fractional_scale_manager_v1 *wlr_fractional_scale_manager_v1_create(
        struct wl_display *, uint32_t version);

struct wlr_output_manager_v1 *wlr_output_manager_v1_create(struct wl_display *);

void wlr_output_manager_v1_set_configuration(
        struct wlr_output_manager_v1 *,
        struct wlr_output_configuration_v1 *);

struct wlr_output_configuration_v1 *wlr_output_configuration_v1_create(void);

struct wlr_output_configuration_head_v1 *
wlr_output_configuration_head_v1_create(
        struct wlr_output_configuration_v1 *, struct wlr_output *);

void wlr_output_configuration_v1_destroy(
        struct wlr_output_configuration_v1 *);

void wlr_output_configuration_v1_send_succeeded(
        struct wlr_output_configuration_v1 *);

void wlr_output_configuration_v1_send_failed(
        struct wlr_output_configuration_v1 *);

struct wl_signal *welpy_output_mgr_apply(struct wlr_output_manager_v1 *);

struct wl_signal *welpy_output_mgr_test(struct wlr_output_manager_v1 *);

struct wlr_gamma_control_manager_v1 *wlr_gamma_control_manager_v1_create(
        struct wl_display *);

void wlr_scene_set_gamma_control_manager_v1(
        struct wlr_scene *, struct wlr_gamma_control_manager_v1 *);

struct wlr_output_power_manager_v1 *wlr_output_power_manager_v1_create(
        struct wl_display *);

struct wl_signal *welpy_output_power_mgr_set_mode(
        struct wlr_output_power_manager_v1 *);

// wl_container_of for the head list: recovers the head from its link node.
struct wlr_output_configuration_head_v1 *welpy_config_head_from_link(
        struct wl_list *link);

struct wl_signal *welpy_backend_new_output(struct wlr_backend *);

struct wl_signal *welpy_output_frame(struct wlr_output *);

struct wl_signal *welpy_output_request_state(struct wlr_output *);

struct wl_signal *welpy_output_destroy_signal(struct wlr_output *);

// wlr_output_state has non-trivial init and unstable layout; expose
// alloc/free instead of declaring it.
struct wlr_output_state *welpy_output_state_new(void);

void welpy_output_state_free(struct wlr_output_state *);

struct wl_signal *welpy_output_layout_change(struct wlr_output_layout *);
"""


_SOURCE = r"""
struct wl_signal *welpy_backend_new_output(struct wlr_backend *b) {
    return &b->events.new_output;
}

struct wl_signal *welpy_output_frame(struct wlr_output *o) {
    return &o->events.frame;
}

struct wl_signal *welpy_output_request_state(struct wlr_output *o) {
    return &o->events.request_state;
}

struct wl_signal *welpy_output_destroy_signal(struct wlr_output *o) {
    return &o->events.destroy;
}

struct wlr_output_state *welpy_output_state_new(void) {
    struct wlr_output_state *s = calloc(1, sizeof(*s));
    wlr_output_state_init(s);
    return s;
}

void welpy_output_state_free(struct wlr_output_state *s) {
    wlr_output_state_finish(s);
    free(s);
}

struct wl_signal *welpy_output_layout_change(struct wlr_output_layout *l) {
    return &l->events.change;
}

struct wl_signal *welpy_output_mgr_apply(struct wlr_output_manager_v1 *m) {
    return &m->events.apply;
}

struct wl_signal *welpy_output_mgr_test(struct wlr_output_manager_v1 *m) {
    return &m->events.test;
}

struct wlr_output_configuration_head_v1 *welpy_config_head_from_link(
        struct wl_list *l) {
    struct wlr_output_configuration_head_v1 *head;
    return wl_container_of(l, head, link);
}

struct wl_signal *welpy_output_power_mgr_set_mode(
        struct wlr_output_power_manager_v1 *m) {
    return &m->events.set_mode;
}
"""

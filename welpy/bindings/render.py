"""Bindings: renderer, allocator, GPU buffer sharing, surface effects,
compositor, and data-device/selection globals."""


def contribute(builder) -> None:
    """Inject this group's cdef and C glue into the build."""
    builder.append(cdef=_CDEF, source=_SOURCE)


_CDEF = r"""
// wlroots
struct wlr_backend *wlr_backend_autocreate(
        struct wl_event_loop *, struct wlr_session **);

bool wlr_backend_start(struct wlr_backend *);

void wlr_backend_destroy(struct wlr_backend *);

bool wlr_session_change_vt(struct wlr_session *, unsigned vt);

struct wlr_renderer *wlr_renderer_autocreate(struct wlr_backend *);

bool wlr_renderer_init_wl_shm(struct wlr_renderer *, struct wl_display *);

int wlr_renderer_get_drm_fd(struct wlr_renderer *);

#define WLR_BUFFER_CAP_DMABUF ...

const struct wlr_drm_format_set *wlr_renderer_get_texture_formats(
        struct wlr_renderer *, uint32_t buffer_caps);

void wlr_renderer_destroy(struct wlr_renderer *);

void wlr_compositor_set_renderer(struct wlr_compositor *, struct wlr_renderer *);

struct wlr_allocator *wlr_allocator_autocreate(
        struct wlr_backend *, struct wlr_renderer *);

void wlr_allocator_destroy(struct wlr_allocator *);

struct wlr_drm *wlr_drm_create(struct wl_display *, struct wlr_renderer *);

struct wlr_linux_dmabuf_v1 *wlr_linux_dmabuf_v1_create_with_renderer(
        struct wl_display *, uint32_t version, struct wlr_renderer *);

struct wlr_linux_drm_syncobj_manager_v1 *wlr_linux_drm_syncobj_manager_v1_create(
        struct wl_display *, uint32_t version, int drm_fd);

// Explicit sync needs both the renderer and the backend to support timelines.
bool welpy_supports_timeline(struct wlr_renderer *, struct wlr_backend *);

// Surface-effect globals the scene helper implements on our behalf:
// viewporter (scale/crop), alpha-modifier (per-surface transparency), and
// single-pixel-buffer (cheap solid-color fills).
struct wlr_viewporter *wlr_viewporter_create(struct wl_display *);

struct wlr_alpha_modifier_v1 *wlr_alpha_modifier_v1_create(struct wl_display *);

struct wlr_single_pixel_buffer_manager_v1
        *wlr_single_pixel_buffer_manager_v1_create(struct wl_display *);

// Tells apps when their frames actually hit the screen; the scene helper
// sends the per-surface feedback once this global exists.
struct wlr_presentation *wlr_presentation_create(
        struct wl_display *, struct wlr_backend *, uint32_t version);

struct wlr_compositor *wlr_compositor_create(
        struct wl_display *, uint32_t version, struct wlr_renderer *);

struct wlr_subcompositor *wlr_subcompositor_create(struct wl_display *);

struct wlr_data_device_manager *wlr_data_device_manager_create(
        struct wl_display *);

struct wlr_data_control_manager_v1 *wlr_data_control_manager_v1_create(
        struct wl_display *);

struct wlr_ext_data_control_manager_v1 *wlr_ext_data_control_manager_v1_create(
        struct wl_display *, uint32_t version);

struct wlr_primary_selection_v1_device_manager
        *wlr_primary_selection_v1_device_manager_create(struct wl_display *);

struct wl_signal *welpy_renderer_lost_signal(struct wlr_renderer *);
"""


_SOURCE = r"""
struct wl_signal *welpy_renderer_lost_signal(struct wlr_renderer *r) {
    return &r->events.lost;
}

bool welpy_supports_timeline(struct wlr_renderer *r, struct wlr_backend *b) {
    return r->features.timeline && b->features.timeline;
}
"""

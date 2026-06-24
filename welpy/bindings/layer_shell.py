"""Bindings: layer-shell surfaces (bars, backgrounds, overlays)."""


def contribute(builder) -> None:
    """Inject this group's cdef and C glue into the build."""
    builder.scanner(
        "wlr-protocols", "unstable/wlr-layer-shell-unstable-v1.xml",
        "wlr-layer-shell-unstable-v1", private_code=False)
    builder.append(cdef=_CDEF, source=_SOURCE)


_CDEF = r"""
// layer-shell
#define ZWLR_LAYER_SHELL_V1_LAYER_BACKGROUND ...

#define ZWLR_LAYER_SHELL_V1_LAYER_BOTTOM ...

#define ZWLR_LAYER_SHELL_V1_LAYER_TOP ...

#define ZWLR_LAYER_SHELL_V1_LAYER_OVERLAY ...

#define ZWLR_LAYER_SURFACE_V1_KEYBOARD_INTERACTIVITY_NONE ...

struct wlr_layer_shell_v1 *wlr_layer_shell_v1_create(
    struct wl_display *, uint32_t version);

void wlr_layer_surface_v1_destroy(struct wlr_layer_surface_v1 *);

struct wlr_scene_layer_surface_v1 *wlr_scene_layer_surface_v1_create(
    struct wlr_scene_tree *, struct wlr_layer_surface_v1 *);

void wlr_scene_layer_surface_v1_configure(
    struct wlr_scene_layer_surface_v1 *,
    const struct wlr_box *full_area, struct wlr_box *usable_area);

struct wl_signal *welpy_layer_shell_new_surface(struct wlr_layer_shell_v1 *);

struct wl_signal *welpy_layer_surface_destroy(struct wlr_layer_surface_v1 *);
"""


_SOURCE = r"""
struct wl_signal *welpy_layer_shell_new_surface(struct wlr_layer_shell_v1 *s) {
    return &s->events.new_surface;
}

struct wl_signal *welpy_layer_surface_destroy(struct wlr_layer_surface_v1 *l) {
    return &l->events.destroy;
}
"""

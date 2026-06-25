"""Bindings: the scene graph (trees, rects, surfaces) and scene hit-testing."""


def register(builder) -> None:
    """Inject this group's cdef and C glue into the build."""
    builder.append(cdef=_CDEF, source=_SOURCE)


_CDEF = r"""
void wlr_scene_node_destroy(struct wlr_scene_node *);

void wlr_scene_set_linux_dmabuf_v1(
        struct wlr_scene *, struct wlr_linux_dmabuf_v1 *);

struct wlr_scene *wlr_scene_create(void);

struct wlr_scene_tree *wlr_scene_tree_create(struct wlr_scene_tree *parent);

struct wlr_scene_rect *wlr_scene_rect_create(struct wlr_scene_tree *parent,
        int width, int height, const float color[4]);

void wlr_scene_rect_set_size(struct wlr_scene_rect *, int width, int height);

void wlr_scene_rect_set_color(struct wlr_scene_rect *, const float color[4]);

void wlr_scene_node_set_enabled(struct wlr_scene_node *, bool enabled);

void wlr_scene_node_place_below(
        struct wlr_scene_node *, struct wlr_scene_node *sibling);

struct wlr_scene_tree *wlr_scene_xdg_surface_create(
        struct wlr_scene_tree *, struct wlr_xdg_surface *);

void wlr_scene_subsurface_tree_set_clip(
        struct wlr_scene_node *, struct wlr_box *);

void wlr_scene_node_set_position(struct wlr_scene_node *, int x, int y);

void wlr_scene_node_raise_to_top(struct wlr_scene_node *);

void wlr_scene_node_reparent(
        struct wlr_scene_node *, struct wlr_scene_tree *new_parent);

struct wlr_scene_tree *wlr_scene_subsurface_tree_create(
        struct wlr_scene_tree *parent, struct wlr_surface *);

bool wlr_scene_node_coords(struct wlr_scene_node *, int *lx, int *ly);

struct wlr_scene_node *welpy_scene_rect_node(struct wlr_scene_rect *);

struct wlr_scene_node *wlr_scene_node_at(struct wlr_scene_node *root,
        double lx, double ly, double *nx, double *ny);

struct wlr_scene_buffer *wlr_scene_buffer_from_node(struct wlr_scene_node *);

struct wlr_scene_surface *wlr_scene_surface_try_from_buffer(
        struct wlr_scene_buffer *);

// enum wlr_scene_node_type
#define WLR_SCENE_NODE_BUFFER ...
"""


_SOURCE = r"""
struct wlr_scene_node *welpy_scene_rect_node(struct wlr_scene_rect *r) {
    return &r->node;
}
"""

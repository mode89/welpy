"""text-input-v3 + input-method-v2 bindings: the two managers, the protocol
send functions the relay drives, signal accessors, and the client/grab
accessors the keyboard loop-breaker needs. Struct decls live in `core`."""


def register(builder) -> None:
    """Inject this group's cdef and C glue into the build."""
    builder.append(cdef=_CDEF, source=_SOURCE)


_CDEF = r"""
struct wl_client;

struct wlr_text_input_manager_v3 *wlr_text_input_manager_v3_create(
        struct wl_display *);

struct wlr_input_method_manager_v2 *wlr_input_method_manager_v2_create(
        struct wl_display *);

void wlr_text_input_v3_send_enter(
        struct wlr_text_input_v3 *, struct wlr_surface *);

void wlr_text_input_v3_send_leave(struct wlr_text_input_v3 *);

void wlr_text_input_v3_send_preedit_string(struct wlr_text_input_v3 *,
        const char *text, int32_t cursor_begin, int32_t cursor_end);

void wlr_text_input_v3_send_commit_string(
        struct wlr_text_input_v3 *, const char *text);

void wlr_text_input_v3_send_delete_surrounding_text(
        struct wlr_text_input_v3 *, uint32_t before_length,
        uint32_t after_length);

void wlr_text_input_v3_send_done(struct wlr_text_input_v3 *);

void wlr_input_method_v2_send_activate(struct wlr_input_method_v2 *);

void wlr_input_method_v2_send_deactivate(struct wlr_input_method_v2 *);

void wlr_input_method_v2_send_surrounding_text(struct wlr_input_method_v2 *,
        const char *text, uint32_t cursor, uint32_t anchor);

void wlr_input_method_v2_send_content_type(
        struct wlr_input_method_v2 *, uint32_t hint, uint32_t purpose);

void wlr_input_method_v2_send_text_change_cause(
        struct wlr_input_method_v2 *, uint32_t cause);

void wlr_input_method_v2_send_done(struct wlr_input_method_v2 *);

void wlr_input_method_v2_send_unavailable(struct wlr_input_method_v2 *);

void wlr_input_method_keyboard_grab_v2_send_key(
        struct wlr_input_method_keyboard_grab_v2 *,
        uint32_t time, uint32_t key, uint32_t state);

void wlr_input_method_keyboard_grab_v2_send_modifiers(
        struct wlr_input_method_keyboard_grab_v2 *,
        struct wlr_keyboard_modifiers *);

void wlr_input_method_keyboard_grab_v2_set_keyboard(
        struct wlr_input_method_keyboard_grab_v2 *, struct wlr_keyboard *);

void wlr_input_popup_surface_v2_send_text_input_rectangle(
        struct wlr_input_popup_surface_v2 *, struct wlr_box *);

// committed buffer size of a surface (its `current` state isn't in the cdef)
void welpy_surface_size(struct wlr_surface *, int *w, int *h);

// enum wlr_text_input_v3_features
#define WLR_TEXT_INPUT_V3_FEATURE_SURROUNDING_TEXT ...

#define WLR_TEXT_INPUT_V3_FEATURE_CONTENT_TYPE ...

#define WLR_TEXT_INPUT_V3_FEATURE_CURSOR_RECTANGLE ...

// signal accessors (one trampoline routes them all via `listen`)
struct wl_signal *welpy_text_input_mgr_new(struct wlr_text_input_manager_v3 *);

struct wl_signal *welpy_im_mgr_new(struct wlr_input_method_manager_v2 *);

struct wl_signal *welpy_text_input_enable(struct wlr_text_input_v3 *);

struct wl_signal *welpy_text_input_commit(struct wlr_text_input_v3 *);

struct wl_signal *welpy_text_input_disable(struct wlr_text_input_v3 *);

struct wl_signal *welpy_text_input_destroy(struct wlr_text_input_v3 *);

struct wl_signal *welpy_im_commit(struct wlr_input_method_v2 *);

struct wl_signal *welpy_im_new_popup(struct wlr_input_method_v2 *);

struct wl_signal *welpy_im_popup_destroy(
        struct wlr_input_popup_surface_v2 *);

struct wl_signal *welpy_im_grab_keyboard(struct wlr_input_method_v2 *);

struct wl_signal *welpy_im_destroy(struct wlr_input_method_v2 *);

struct wl_signal *welpy_im_grab_destroy(
        struct wlr_input_method_keyboard_grab_v2 *);

struct wl_signal *welpy_surface_destroy(struct wlr_surface *);

// client accessors for the keyboard loop-breaker + relay focus routing
struct wl_client *welpy_text_input_client(struct wlr_text_input_v3 *);

struct wl_client *welpy_surface_client(struct wlr_surface *);

struct wl_client *welpy_im_grab_client(struct wlr_input_method_v2 *);

struct wl_client *welpy_vkb_client(struct wlr_virtual_keyboard_v1 *);
"""


_SOURCE = r"""
struct wl_signal *welpy_text_input_mgr_new(
        struct wlr_text_input_manager_v3 *m) {
    return &m->events.new_text_input;
}

struct wl_signal *welpy_im_mgr_new(struct wlr_input_method_manager_v2 *m) {
    return &m->events.new_input_method;
}

struct wl_signal *welpy_text_input_enable(struct wlr_text_input_v3 *t) {
    return &t->events.enable;
}

struct wl_signal *welpy_text_input_commit(struct wlr_text_input_v3 *t) {
    return &t->events.commit;
}

struct wl_signal *welpy_text_input_disable(struct wlr_text_input_v3 *t) {
    return &t->events.disable;
}

struct wl_signal *welpy_text_input_destroy(struct wlr_text_input_v3 *t) {
    return &t->events.destroy;
}

struct wl_signal *welpy_im_commit(struct wlr_input_method_v2 *im) {
    return &im->events.commit;
}

struct wl_signal *welpy_im_new_popup(struct wlr_input_method_v2 *im) {
    return &im->events.new_popup_surface;
}

struct wl_signal *welpy_im_popup_destroy(
        struct wlr_input_popup_surface_v2 *p) {
    return &p->events.destroy;
}

void welpy_surface_size(struct wlr_surface *s, int *w, int *h) {
    *w = s->current.width;
    *h = s->current.height;
}

struct wl_signal *welpy_im_grab_keyboard(struct wlr_input_method_v2 *im) {
    return &im->events.grab_keyboard;
}

struct wl_signal *welpy_im_destroy(struct wlr_input_method_v2 *im) {
    return &im->events.destroy;
}

struct wl_signal *welpy_im_grab_destroy(
        struct wlr_input_method_keyboard_grab_v2 *g) {
    return &g->events.destroy;
}

struct wl_signal *welpy_surface_destroy(struct wlr_surface *s) {
    return &s->events.destroy;
}

struct wl_client *welpy_text_input_client(struct wlr_text_input_v3 *t) {
    return wl_resource_get_client(t->resource);
}

struct wl_client *welpy_surface_client(struct wlr_surface *s) {
    return wl_resource_get_client(s->resource);
}

struct wl_client *welpy_im_grab_client(struct wlr_input_method_v2 *im) {
    return im->keyboard_grab
        ? wl_resource_get_client(im->keyboard_grab->resource) : NULL;
}

struct wl_client *welpy_vkb_client(struct wlr_virtual_keyboard_v1 *vkb) {
    return wl_resource_get_client(vkb->resource);
}
"""

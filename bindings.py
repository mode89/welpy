"""Inline cffi bindings to wlroots 0.19 / libwayland-server / xkbcommon.

Compiled at import time via `cffi.FFI.set_source` + `compile()` into a
tempdir and loaded as `_pywl_cffi`. Re-exports `ffi`, `lib`, and the
`listen` helper.

Only the symbols required by main.py are exposed. Struct field access is
avoided by writing tiny C accessor helpers, so we do not depend on wlroots
struct layout — just on its public function ABI.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
from types import SimpleNamespace

import cffi


_PKGS = ("wlroots-0.19", "wayland-server", "xkbcommon", "pixman-1")
_MODULE = "_pywl_cffi"


CDEF = r"""
typedef _Bool bool;
typedef unsigned int uint32_t;
typedef int int32_t;

struct wl_list { struct wl_list *prev; struct wl_list *next; };
struct timespec { long tv_sec; long tv_nsec; };

struct wl_listener;
typedef void (*wl_notify_func_t)(struct wl_listener *, void *);
struct wl_listener {
    struct wl_list link;
    wl_notify_func_t notify;
};
struct wl_signal { struct wl_list listener_list; };

// opaque types
struct wl_display;
struct wl_event_loop;
struct wl_event_source;
typedef int (*wl_event_loop_timer_func_t)(void *data);
struct wlr_backend;
struct wlr_session;
struct wlr_renderer;
struct wlr_allocator;
struct wlr_output_layout;
struct wlr_output_layout_output { int x, y; ...; };
struct wlr_scene_output;
struct wlr_scene_output_layout;
struct wlr_xdg_shell;
struct wlr_surface;
struct wlr_seat_pointer_state { struct wlr_surface *focused_surface; ...; };
struct wlr_seat_keyboard_state { struct wlr_surface *focused_surface; ...; };
struct wlr_seat {
    struct wlr_seat_pointer_state pointer_state;
    struct wlr_seat_keyboard_state keyboard_state;
    ...;
};
struct wlr_compositor;
struct wlr_subcompositor;
struct wlr_data_device_manager;
struct wlr_data_control_manager_v1;
struct wlr_ext_data_control_manager_v1;
struct xkb_context;
struct xkb_keymap;
// wlr_keyboard_modifiers is embedded in wlr_keyboard, so cffi needs a
// (possibly empty) layout for it. We never read its fields from Python.
struct wlr_keyboard_modifiers { ...; };


// partial decls: cffi asks the C compiler for offsets at build time, so
// these are not locked to a specific wlroots ABI — only to the existence
// and types of the fields listed here.
struct wlr_output { int width; int height; bool enabled; char *name; ...; };
struct wlr_cursor { double x; double y; ...; };
struct wlr_input_device { int type; ...; };
struct wlr_surface { bool mapped; void *data; ...; };
struct wlr_keyboard {
    struct wlr_keyboard_modifiers modifiers;
    struct xkb_keymap *keymap;
    uint32_t keycodes[...];
    size_t num_keycodes;
    struct { int32_t rate; int32_t delay; } repeat_info;
    ...;
};
struct wlr_box {
    int x;
    int y;
    int width;
    int height;
};
struct wlr_scene_tree;
struct wlr_scene_node {
    int x;
    int y;
    int type;
    struct wlr_scene_tree *parent;
    void *data;
    ...;
};
struct wlr_scene_tree { struct wlr_scene_node node; ...; };
struct wlr_scene { struct wlr_scene_tree tree; ...; };
struct wlr_scene_surface { struct wlr_surface *surface; ...; };
struct wlr_xdg_surface_state {
    uint32_t configure_serial;
    ...;
};
struct wlr_xdg_surface {
    struct wlr_surface *surface;
    struct wlr_xdg_surface_state current;
    bool initialized;
    bool initial_commit;
    struct wlr_box geometry;
    void *data;
    ...;
};
struct wlr_xdg_toplevel_requested {
    bool maximized;
    bool fullscreen;
    bool minimized;
    ...;
};
struct wlr_xdg_toplevel_state {
    int32_t width;
    int32_t height;
    ...;
};
struct wlr_xdg_toplevel {
    struct wlr_xdg_surface *base;
    struct wlr_xdg_toplevel *parent;
    struct wlr_xdg_toplevel_state current;
    struct wlr_xdg_toplevel_requested requested;
    char *title;
    char *app_id;
    ...;
};
struct wlr_xdg_popup {
    struct wlr_xdg_surface *base;
    struct wlr_surface *parent;
    ...;
};
struct wlr_data_source;
struct wlr_seat_request_set_selection_event {
    struct wlr_data_source *source;
    uint32_t serial;
    ...;
};
struct wlr_primary_selection_source;
struct wlr_seat_request_set_primary_selection_event {
    struct wlr_primary_selection_source *source;
    uint32_t serial;
    ...;
};

struct wlr_seat_client;
struct wlr_seat_pointer_request_set_cursor_event {
    struct wlr_seat_client *seat_client;
    struct wlr_surface *surface;
    int32_t hotspot_x;
    int32_t hotspot_y;
    uint32_t serial;
    ...;
};
struct wlr_keyboard_key_event {
    uint32_t time_msec;
    uint32_t keycode;
    uint32_t state;
    ...;
};

// libwayland-server
void wl_list_remove(struct wl_list *);
struct wl_display *wl_display_create(void);
void wl_display_destroy(struct wl_display *);
void wl_display_destroy_clients(struct wl_display *);
void wl_display_run(struct wl_display *);
void wl_display_terminate(struct wl_display *);
void wl_display_flush_clients(struct wl_display *);
int wl_event_loop_dispatch(struct wl_event_loop *, int timeout);
struct wl_event_source *wl_event_loop_add_timer(
        struct wl_event_loop *, wl_event_loop_timer_func_t, void *data);
int wl_event_source_timer_update(struct wl_event_source *, int ms_delay);
int wl_event_source_remove(struct wl_event_source *);
const char *wl_display_add_socket_auto(struct wl_display *);
struct wl_event_loop *wl_display_get_event_loop(struct wl_display *);

// wlroots
struct wlr_backend *wlr_backend_autocreate(
        struct wl_event_loop *, struct wlr_session **);
bool wlr_backend_start(struct wlr_backend *);
void wlr_backend_destroy(struct wlr_backend *);
bool wlr_session_change_vt(struct wlr_session *, unsigned vt);

struct wlr_renderer *wlr_renderer_autocreate(struct wlr_backend *);
bool wlr_renderer_init_wl_display(struct wlr_renderer *, struct wl_display *);
bool wlr_renderer_init_wl_shm(struct wlr_renderer *, struct wl_display *);
void wlr_renderer_destroy(struct wlr_renderer *);
void wlr_compositor_set_renderer(struct wlr_compositor *, struct wlr_renderer *);

struct wlr_allocator *wlr_allocator_autocreate(
        struct wlr_backend *, struct wlr_renderer *);
void wlr_allocator_destroy(struct wlr_allocator *);
void wlr_scene_node_destroy(struct wlr_scene_node *);

struct wlr_compositor *wlr_compositor_create(
        struct wl_display *, uint32_t version, struct wlr_renderer *);
struct wlr_subcompositor *wlr_subcompositor_create(struct wl_display *);
struct wlr_data_device_manager *wlr_data_device_manager_create(
        struct wl_display *);
struct wlr_data_control_manager_v1 *wlr_data_control_manager_v1_create(
        struct wl_display *);
struct wlr_ext_data_control_manager_v1 *wlr_ext_data_control_manager_v1_create(
        struct wl_display *, uint32_t version);

struct wlr_output_layout *wlr_output_layout_create(struct wl_display *);
struct wlr_output_layout_output *wlr_output_layout_add_auto(
        struct wlr_output_layout *, struct wlr_output *);
void wlr_output_layout_remove(struct wlr_output_layout *, struct wlr_output *);
void wlr_output_layout_get_box(struct wlr_output_layout *,
        struct wlr_output *, struct wlr_box *dest);

struct wlr_output_state;
struct wlr_output_mode;
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

struct wlr_scene *wlr_scene_create(void);
struct wlr_scene_rect;
struct wlr_scene_tree *wlr_scene_tree_create(struct wlr_scene_tree *parent);
struct wlr_scene_rect *wlr_scene_rect_create(struct wlr_scene_tree *parent,
        int width, int height, const float color[4]);
void wlr_scene_rect_set_size(struct wlr_scene_rect *, int width, int height);
void wlr_scene_rect_set_color(struct wlr_scene_rect *, const float color[4]);
void wlr_scene_node_set_enabled(struct wlr_scene_node *, bool enabled);
void wlr_scene_node_place_below(
        struct wlr_scene_node *, struct wlr_scene_node *sibling);
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
struct wlr_scene_tree *wlr_scene_xdg_surface_create(
        struct wlr_scene_tree *, struct wlr_xdg_surface *);
void wlr_scene_subsurface_tree_set_clip(
        struct wlr_scene_node *, struct wlr_box *);

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
struct wlr_surface *wlr_surface_get_root_surface(struct wlr_surface *);
void wlr_xdg_surface_schedule_configure(struct wlr_xdg_surface *);
void wlr_xdg_popup_unconstrain_from_box(
        struct wlr_xdg_popup *, const struct wlr_box *);
void wlr_seat_set_selection(struct wlr_seat *,
        struct wlr_data_source *, uint32_t serial);
void wlr_seat_set_primary_selection(struct wlr_seat *,
        struct wlr_primary_selection_source *, uint32_t serial);

struct wlr_seat *wlr_seat_create(struct wl_display *, const char *);
void wlr_seat_set_capabilities(struct wlr_seat *, uint32_t caps);
struct wlr_scene_node;
void wlr_scene_node_set_position(struct wlr_scene_node *, int x, int y);
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

struct wlr_keyboard_group;
struct wlr_keyboard_group *wlr_keyboard_group_create(void);
void wlr_keyboard_group_destroy(struct wlr_keyboard_group *);
bool wlr_keyboard_group_add_keyboard(struct wlr_keyboard_group *,
        struct wlr_keyboard *);

// Cursor / pointer
struct wlr_xcursor_manager;
struct wlr_pointer { struct wlr_input_device base; ...; };
struct wlr_pointer_motion_event {
    struct wlr_pointer *pointer;
    uint32_t time_msec;
    double delta_x;
    double delta_y;
    ...;
};
struct wlr_pointer_motion_absolute_event {
    struct wlr_pointer *pointer;
    uint32_t time_msec;
    double x;
    double y;
    ...;
};
struct wlr_pointer_button_event {
    struct wlr_pointer *pointer;
    uint32_t time_msec;
    uint32_t button;
    uint32_t state;
    ...;
};
struct wlr_pointer_axis_event {
    struct wlr_pointer *pointer;
    uint32_t time_msec;
    uint32_t source;
    uint32_t orientation;
    uint32_t relative_direction;
    double delta;
    int32_t delta_discrete;
    ...;
};

struct wlr_cursor *wlr_cursor_create(void);
void wlr_cursor_destroy(struct wlr_cursor *);
void wlr_cursor_attach_output_layout(
        struct wlr_cursor *, struct wlr_output_layout *);
void wlr_cursor_attach_input_device(
        struct wlr_cursor *, struct wlr_input_device *);
void wlr_cursor_move(struct wlr_cursor *, struct wlr_input_device *,
        double delta_x, double delta_y);
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

void wlr_scene_node_raise_to_top(struct wlr_scene_node *);
void wlr_scene_node_reparent(
        struct wlr_scene_node *, struct wlr_scene_tree *new_parent);

struct xkb_context *xkb_context_new(int flags);
void xkb_context_unref(struct xkb_context *);
struct xkb_rule_names {
    const char *rules;
    const char *model;
    const char *layout;
    const char *variant;
    const char *options;
};
struct xkb_keymap *xkb_keymap_new_from_names(struct xkb_context *,
        const struct xkb_rule_names *names, int flags);
void xkb_keymap_unref(struct xkb_keymap *);

void wlr_scene_output_send_frame_done(struct wlr_scene_output *,
        struct timespec *);

void wlr_log_init(int verbosity, void *callback);

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

// xdg-toplevel WM capability bits
#define WLR_XDG_TOPLEVEL_WM_CAPABILITIES_FULLSCREEN ...

// xdg-toplevel resize edge bits
#define WLR_EDGE_NONE ...
#define WLR_EDGE_TOP ...
#define WLR_EDGE_BOTTOM ...
#define WLR_EDGE_LEFT ...
#define WLR_EDGE_RIGHT ...

// layer-shell
#define ZWLR_LAYER_SHELL_V1_LAYER_BACKGROUND ...
#define ZWLR_LAYER_SHELL_V1_LAYER_BOTTOM ...
#define ZWLR_LAYER_SHELL_V1_LAYER_TOP ...
#define ZWLR_LAYER_SHELL_V1_LAYER_OVERLAY ...
#define ZWLR_LAYER_SURFACE_V1_KEYBOARD_INTERACTIVITY_NONE ...
struct wlr_layer_shell_v1;
struct wlr_layer_surface_v1_state {
    uint32_t committed;
    uint32_t anchor;
    int32_t exclusive_zone;
    uint32_t keyboard_interactive;
    uint32_t desired_width, desired_height;
    uint32_t layer;
    ...;
};
struct wlr_layer_surface_v1 {
    struct wlr_surface *surface;
    struct wlr_output *output;
    char *namespace;
    bool initialized;
    bool initial_commit;
    struct wlr_layer_surface_v1_state current, pending;
    ...;
};
struct wlr_scene_layer_surface_v1 {
    struct wlr_scene_tree *tree;
    struct wlr_layer_surface_v1 *layer_surface;
    ...;
};
struct wlr_layer_shell_v1 *wlr_layer_shell_v1_create(
    struct wl_display *, uint32_t version);
void wlr_layer_surface_v1_destroy(struct wlr_layer_surface_v1 *);
struct wlr_scene_layer_surface_v1 *wlr_scene_layer_surface_v1_create(
    struct wlr_scene_tree *, struct wlr_layer_surface_v1 *);
void wlr_scene_layer_surface_v1_configure(
    struct wlr_scene_layer_surface_v1 *,
    const struct wlr_box *full_area, struct wlr_box *usable_area);
void wlr_surface_send_enter(struct wlr_surface *, struct wlr_output *);

// xdg-decoration
#define WLR_XDG_TOPLEVEL_DECORATION_V1_MODE_SERVER_SIDE ...
struct wlr_xdg_decoration_manager_v1;
struct wlr_xdg_toplevel_decoration_v1 {
    struct wlr_xdg_toplevel *toplevel;
    ...;
};
struct wlr_xdg_decoration_manager_v1 *wlr_xdg_decoration_manager_v1_create(
        struct wl_display *);
uint32_t wlr_xdg_toplevel_decoration_v1_set_mode(
        struct wlr_xdg_toplevel_decoration_v1 *, uint32_t mode);

// xdg-activation
struct wlr_xdg_activation_v1;
struct wlr_xdg_activation_v1_request_activate_event {
    struct wlr_surface *surface;
    ...;
};
struct wlr_xdg_activation_v1 *wlr_xdg_activation_v1_create(
        struct wl_display *);
struct wl_signal *pywl_xdg_activation_request_activate(
        struct wlr_xdg_activation_v1 *);

// xdg-output: per-screen name/description for bars, screenshot tools, etc.
struct wlr_xdg_output_manager_v1;
struct wlr_xdg_output_manager_v1 *wlr_xdg_output_manager_v1_create(
        struct wl_display *, struct wlr_output_layout *);

// output-management: clients like wlr-randr / kanshi reconfigure outputs.
struct wlr_output_head_v1_state {
    struct wlr_output *output;
    bool enabled;
    struct wlr_output_mode *mode;
    struct { int32_t width; int32_t height; int32_t refresh; } custom_mode;
    int32_t x, y;
    uint32_t transform;
    float scale;
    bool adaptive_sync_enabled;
    ...;
};
struct wlr_output_configuration_head_v1 {
    struct wlr_output_head_v1_state state;
    struct wl_list link;
    ...;
};
struct wlr_output_configuration_v1 {
    struct wl_list heads;
    ...;
};
struct wlr_output_manager_v1;
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
struct wl_signal *pywl_output_mgr_apply(struct wlr_output_manager_v1 *);
struct wl_signal *pywl_output_mgr_test(struct wlr_output_manager_v1 *);

// gamma-control: wlsunset / gammastep set per-output gamma LUTs.
struct wlr_gamma_control_manager_v1;
struct wlr_gamma_control_manager_v1 *wlr_gamma_control_manager_v1_create(
        struct wl_display *);
void wlr_scene_set_gamma_control_manager_v1(
        struct wlr_scene *, struct wlr_gamma_control_manager_v1 *);

// output-power-management: DPMS (clients turn screens on/off).
struct wlr_output_power_v1_set_mode_event {
    struct wlr_output *output;
    uint32_t mode;
    ...;
};
struct wlr_output_power_manager_v1;
struct wlr_output_power_manager_v1 *wlr_output_power_manager_v1_create(
        struct wl_display *);
struct wl_signal *pywl_output_power_mgr_set_mode(
        struct wlr_output_power_manager_v1 *);

// session-lock: swaylock-style screen lockers.
struct wlr_session_lock_manager_v1;
struct wlr_session_lock_v1;
struct wlr_session_lock_surface_v1 {
    struct wlr_output *output;
    struct wlr_surface *surface;
    ...;
};
struct wlr_session_lock_manager_v1 *wlr_session_lock_manager_v1_create(
        struct wl_display *);
void wlr_session_lock_v1_send_locked(struct wlr_session_lock_v1 *);
void wlr_session_lock_v1_destroy(struct wlr_session_lock_v1 *);
uint32_t wlr_session_lock_surface_v1_configure(
        struct wlr_session_lock_surface_v1 *, uint32_t w, uint32_t h);
struct wlr_scene_tree *wlr_scene_subsurface_tree_create(
        struct wlr_scene_tree *parent, struct wlr_surface *);
struct wl_signal *pywl_session_lock_mgr_new_lock(
        struct wlr_session_lock_manager_v1 *);
struct wl_signal *pywl_session_lock_new_surface(struct wlr_session_lock_v1 *);
struct wl_signal *pywl_session_lock_unlock(struct wlr_session_lock_v1 *);
struct wl_signal *pywl_session_lock_destroy(struct wlr_session_lock_v1 *);
struct wl_signal *pywl_session_lock_surface_destroy(
        struct wlr_session_lock_surface_v1 *);

// idle-notify: ext-idle-notify-v1 (swayidle), and idle-inhibit (video
// players asking the compositor not to dim/lock the screen).
struct wlr_idle_notifier_v1;
struct wlr_idle_notifier_v1 *wlr_idle_notifier_v1_create(struct wl_display *);
void wlr_idle_notifier_v1_set_inhibited(
        struct wlr_idle_notifier_v1 *, bool inhibited);
void wlr_idle_notifier_v1_notify_activity(
        struct wlr_idle_notifier_v1 *, struct wlr_seat *);
struct wlr_idle_inhibitor_v1 {
    struct wlr_surface *surface;
    struct wl_list link;
    ...;
};
struct wlr_idle_inhibit_manager_v1 {
    struct wl_list inhibitors;
    ...;
};
struct wlr_idle_inhibit_manager_v1 *wlr_idle_inhibit_v1_create(
        struct wl_display *);
struct wl_signal *pywl_idle_inhibit_new_inhibitor(
        struct wlr_idle_inhibit_manager_v1 *);
struct wl_signal *pywl_idle_inhibitor_destroy(
        struct wlr_idle_inhibitor_v1 *);
struct wlr_idle_inhibitor_v1 *pywl_idle_inhibitor_from_link(struct wl_list *);

bool wlr_scene_node_coords(struct wlr_scene_node *, int *lx, int *ly);

// wl_container_of for the head list: recovers the head from its link node.
struct wlr_output_configuration_head_v1 *pywl_config_head_from_link(
        struct wl_list *link);

// server-decoration (predecessor to xdg-decoration; obsolete but still used
// by some clients)
#define WLR_SERVER_DECORATION_MANAGER_MODE_SERVER ...
struct wlr_server_decoration_manager;
struct wlr_server_decoration_manager *wlr_server_decoration_manager_create(
        struct wl_display *);
void wlr_server_decoration_manager_set_default_mode(
        struct wlr_server_decoration_manager *, uint32_t default_mode);

// Resolve a key name (e.g. "Return") to an xkb keysym, or 0 if unknown.
uint32_t xkb_keysym_from_name(const char *name, int flags);

// First xkb keysym for an event keycode on this keyboard, or 0.
uint32_t pywl_keyboard_keysym(struct wlr_keyboard *kb, uint32_t keycode);

// linux/input-event-codes.h
#define BTN_LEFT ...
#define BTN_RIGHT ...
#define BTN_MIDDLE ...

// our helpers
void pywl_signal_add(struct wl_signal *, struct wl_listener *);
struct wl_signal *pywl_backend_new_output(struct wlr_backend *);
struct wl_signal *pywl_output_frame(struct wlr_output *);
struct wl_signal *pywl_output_request_state(struct wlr_output *);
struct wl_signal *pywl_output_destroy_signal(struct wlr_output *);
struct wl_signal *pywl_input_device_destroy_signal(struct wlr_input_device *);
struct wl_signal *pywl_xdg_shell_new_toplevel(struct wlr_xdg_shell *);
struct wl_signal *pywl_surface_commit(struct wlr_surface *);
// wlr_output_state has non-trivial init and unstable layout; expose
// alloc/free instead of declaring it.
struct wlr_output_state *pywl_output_state_new(void);
void pywl_output_state_free(struct wlr_output_state *);

struct wl_signal *pywl_xdg_toplevel_destroy(struct wlr_xdg_toplevel *);
struct wl_signal *pywl_xdg_toplevel_set_title(struct wlr_xdg_toplevel *);
struct wl_signal *pywl_xdg_toplevel_request_maximize(struct wlr_xdg_toplevel *);
struct wl_signal *pywl_xdg_shell_new_popup(struct wlr_xdg_shell *);
struct wl_signal *pywl_xdg_popup_destroy(struct wlr_xdg_popup *);
struct wl_signal *pywl_surface_unmap(struct wlr_surface *);
struct wl_signal *pywl_seat_request_set_selection(struct wlr_seat *);
struct wl_signal *pywl_seat_request_set_cursor(struct wlr_seat *);
struct wl_signal *pywl_seat_request_set_primary_selection(struct wlr_seat *);
struct wlr_seat_client *pywl_seat_pointer_focused_client(struct wlr_seat *);
struct wl_signal *pywl_renderer_lost_signal(struct wlr_renderer *);
struct wlr_keyboard *pywl_keyboard_group_keyboard(struct wlr_keyboard_group *);
struct wlr_scene_node *pywl_scene_rect_node(struct wlr_scene_rect *);
struct wl_signal *pywl_xdg_toplevel_request_fullscreen(struct wlr_xdg_toplevel *);
struct wl_signal *pywl_xdg_toplevel_set_app_id(struct wlr_xdg_toplevel *);
struct wl_signal *pywl_output_layout_change(struct wlr_output_layout *);
struct wl_signal *pywl_xdg_decoration_manager_new(
        struct wlr_xdg_decoration_manager_v1 *);
struct wl_signal *pywl_xdg_decoration_request_mode(
        struct wlr_xdg_toplevel_decoration_v1 *);
struct wl_signal *pywl_xdg_decoration_destroy(
        struct wlr_xdg_toplevel_decoration_v1 *);
struct wl_signal *pywl_layer_shell_new_surface(struct wlr_layer_shell_v1 *);
struct wl_signal *pywl_layer_surface_destroy(struct wlr_layer_surface_v1 *);

struct wlr_output_event_request_state {
    const struct wlr_output_state *state;
    ...;
};

// cursor signals (events.* lives in an anonymous sub-struct)
struct wl_signal *pywl_cursor_motion(struct wlr_cursor *);
struct wl_signal *pywl_cursor_motion_absolute(struct wlr_cursor *);
struct wl_signal *pywl_cursor_button(struct wlr_cursor *);
struct wl_signal *pywl_cursor_axis(struct wlr_cursor *);
struct wl_signal *pywl_cursor_frame(struct wlr_cursor *);

// hit-test entry point
struct wlr_scene_buffer;
struct wlr_scene_node *wlr_scene_node_at(struct wlr_scene_node *root,
        double lx, double ly, double *nx, double *ny);
struct wlr_scene_buffer *wlr_scene_buffer_from_node(struct wlr_scene_node *);
struct wlr_scene_surface *wlr_scene_surface_try_from_buffer(
        struct wlr_scene_buffer *);

// enum wlr_scene_node_type
#define WLR_SCENE_NODE_BUFFER ...

// keyboard input field accessors (struct layout we don't want to declare)
struct wl_signal *pywl_backend_new_input(struct wlr_backend *);
struct wl_signal *pywl_surface_map(struct wlr_surface *);
struct wl_signal *pywl_keyboard_key_signal(struct wlr_keyboard *);
struct wl_signal *pywl_keyboard_modifiers_signal(struct wlr_keyboard *);
extern "Python" void _pywl_dispatch(struct wl_listener *, void *);
extern "Python" int _pywl_timer_dispatch(void *);
"""


SOURCE = r"""
#define WLR_USE_UNSTABLE
#include <stdint.h>
#include <stdbool.h>
#include <stdlib.h>
#include <time.h>
#include <linux/input-event-codes.h>
#include <wayland-server-core.h>
#include <wlr/types/wlr_primary_selection.h>
#include <wlr/backend.h>
#include <wlr/backend/session.h>
#include <wlr/render/allocator.h>
#include <wlr/render/wlr_renderer.h>
#include <wlr/types/wlr_compositor.h>
#include <wlr/types/wlr_data_device.h>
#include <wlr/types/wlr_data_control_v1.h>
#include <wlr/types/wlr_ext_data_control_v1.h>
#include <wlr/types/wlr_output.h>
#include <wlr/types/wlr_output_layout.h>
#include <wlr/types/wlr_scene.h>
#include <wlr/types/wlr_seat.h>
#include <wlr/types/wlr_subcompositor.h>
#include <wlr/types/wlr_input_device.h>
#include <wlr/types/wlr_keyboard.h>
#include <wlr/types/wlr_keyboard_group.h>
#include <wlr/types/wlr_pointer.h>
#include <wlr/types/wlr_cursor.h>
#include <wlr/types/wlr_xcursor_manager.h>
#include <wlr/types/wlr_layer_shell_v1.h>
#include <wlr/types/wlr_xdg_activation_v1.h>
#include <wlr/types/wlr_xdg_decoration_v1.h>
#include <wlr/types/wlr_xdg_output_v1.h>
#include <wlr/types/wlr_output_management_v1.h>
#include <wlr/types/wlr_output_power_management_v1.h>
#include <wlr/types/wlr_gamma_control_v1.h>
#include <wlr/types/wlr_session_lock_v1.h>
#include <wlr/types/wlr_idle_notify_v1.h>
#include <wlr/types/wlr_idle_inhibit_v1.h>
#include <wlr/types/wlr_server_decoration.h>
#include <wlr/types/wlr_xdg_shell.h>
#include <wlr/util/box.h>
#include <wlr/util/log.h>
#include <xkbcommon/xkbcommon.h>

void pywl_signal_add(struct wl_signal *s, struct wl_listener *l) {
    wl_signal_add(s, l);
}

uint32_t pywl_keyboard_keysym(struct wlr_keyboard *kb, uint32_t keycode) {
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

struct wl_signal *pywl_backend_new_output(struct wlr_backend *b) {
    return &b->events.new_output;
}
struct wl_signal *pywl_output_frame(struct wlr_output *o) {
    return &o->events.frame;
}
struct wl_signal *pywl_output_request_state(struct wlr_output *o) {
    return &o->events.request_state;
}
struct wl_signal *pywl_output_destroy_signal(struct wlr_output *o) {
    return &o->events.destroy;
}
struct wl_signal *pywl_input_device_destroy_signal(struct wlr_input_device *d) {
    return &d->events.destroy;
}
struct wl_signal *pywl_xdg_shell_new_toplevel(struct wlr_xdg_shell *s) {
    return &s->events.new_toplevel;
}
struct wl_signal *pywl_surface_commit(struct wlr_surface *s) {
    return &s->events.commit;
}

struct wlr_output_state *pywl_output_state_new(void) {
    struct wlr_output_state *s = calloc(1, sizeof(*s));
    wlr_output_state_init(s);
    return s;
}
void pywl_output_state_free(struct wlr_output_state *s) {
    wlr_output_state_finish(s);
    free(s);
}

struct wl_signal *pywl_backend_new_input(struct wlr_backend *b) {
    return &b->events.new_input;
}
struct wl_signal *pywl_surface_map(struct wlr_surface *s) {
    return &s->events.map;
}
struct wl_signal *pywl_keyboard_key_signal(struct wlr_keyboard *k) {
    return &k->events.key;
}
struct wl_signal *pywl_keyboard_modifiers_signal(struct wlr_keyboard *k) {
    return &k->events.modifiers;
}
struct wl_signal *pywl_xdg_toplevel_destroy(struct wlr_xdg_toplevel *t) {
    return &t->events.destroy;
}
struct wl_signal *pywl_xdg_shell_new_popup(struct wlr_xdg_shell *s) {
    return &s->events.new_popup;
}
struct wl_signal *pywl_xdg_popup_destroy(struct wlr_xdg_popup *p) {
    return &p->events.destroy;
}
struct wl_signal *pywl_surface_unmap(struct wlr_surface *s) {
    return &s->events.unmap;
}
struct wl_signal *pywl_seat_request_set_selection(struct wlr_seat *s) {
    return &s->events.request_set_selection;
}

struct wl_signal *pywl_cursor_motion(struct wlr_cursor *c) {
    return &c->events.motion;
}
struct wl_signal *pywl_cursor_motion_absolute(struct wlr_cursor *c) {
    return &c->events.motion_absolute;
}
struct wl_signal *pywl_cursor_button(struct wlr_cursor *c) {
    return &c->events.button;
}
struct wl_signal *pywl_cursor_axis(struct wlr_cursor *c) {
    return &c->events.axis;
}
struct wl_signal *pywl_cursor_frame(struct wlr_cursor *c) {
    return &c->events.frame;
}

struct wl_signal *pywl_xdg_toplevel_set_title(struct wlr_xdg_toplevel *t) {
    return &t->events.set_title;
}
struct wl_signal *pywl_xdg_toplevel_request_maximize(struct wlr_xdg_toplevel *t) {
    return &t->events.request_maximize;
}
struct wl_signal *pywl_seat_request_set_cursor(struct wlr_seat *s) {
    return &s->events.request_set_cursor;
}
struct wl_signal *pywl_seat_request_set_primary_selection(struct wlr_seat *s) {
    return &s->events.request_set_primary_selection;
}
struct wlr_seat_client *pywl_seat_pointer_focused_client(struct wlr_seat *s) {
    return s->pointer_state.focused_client;
}
struct wl_signal *pywl_renderer_lost_signal(struct wlr_renderer *r) {
    return &r->events.lost;
}
struct wlr_keyboard *pywl_keyboard_group_keyboard(struct wlr_keyboard_group *g) {
    return &g->keyboard;
}
struct wlr_scene_node *pywl_scene_rect_node(struct wlr_scene_rect *r) {
    return &r->node;
}
struct wl_signal *pywl_xdg_toplevel_request_fullscreen(struct wlr_xdg_toplevel *t) {
    return &t->events.request_fullscreen;
}
struct wl_signal *pywl_xdg_toplevel_set_app_id(struct wlr_xdg_toplevel *t) {
    return &t->events.set_app_id;
}
struct wl_signal *pywl_output_layout_change(struct wlr_output_layout *l) {
    return &l->events.change;
}
struct wl_signal *pywl_xdg_decoration_manager_new(
        struct wlr_xdg_decoration_manager_v1 *m) {
    return &m->events.new_toplevel_decoration;
}
struct wl_signal *pywl_xdg_decoration_request_mode(
        struct wlr_xdg_toplevel_decoration_v1 *d) {
    return &d->events.request_mode;
}
struct wl_signal *pywl_xdg_decoration_destroy(
        struct wlr_xdg_toplevel_decoration_v1 *d) {
    return &d->events.destroy;
}
struct wl_signal *pywl_layer_shell_new_surface(struct wlr_layer_shell_v1 *s) {
    return &s->events.new_surface;
}
struct wl_signal *pywl_layer_surface_destroy(struct wlr_layer_surface_v1 *l) {
    return &l->events.destroy;
}
struct wl_signal *pywl_xdg_activation_request_activate(
        struct wlr_xdg_activation_v1 *a) {
    return &a->events.request_activate;
}
struct wl_signal *pywl_output_mgr_apply(struct wlr_output_manager_v1 *m) {
    return &m->events.apply;
}
struct wl_signal *pywl_output_mgr_test(struct wlr_output_manager_v1 *m) {
    return &m->events.test;
}
struct wlr_output_configuration_head_v1 *pywl_config_head_from_link(
        struct wl_list *l) {
    struct wlr_output_configuration_head_v1 *head;
    return wl_container_of(l, head, link);
}
struct wl_signal *pywl_output_power_mgr_set_mode(
        struct wlr_output_power_manager_v1 *m) {
    return &m->events.set_mode;
}
struct wl_signal *pywl_session_lock_mgr_new_lock(
        struct wlr_session_lock_manager_v1 *m) {
    return &m->events.new_lock;
}
struct wl_signal *pywl_session_lock_new_surface(
        struct wlr_session_lock_v1 *l) {
    return &l->events.new_surface;
}
struct wl_signal *pywl_session_lock_unlock(struct wlr_session_lock_v1 *l) {
    return &l->events.unlock;
}
struct wl_signal *pywl_session_lock_destroy(struct wlr_session_lock_v1 *l) {
    return &l->events.destroy;
}
struct wl_signal *pywl_session_lock_surface_destroy(
        struct wlr_session_lock_surface_v1 *s) {
    return &s->events.destroy;
}
struct wl_signal *pywl_idle_inhibit_new_inhibitor(
        struct wlr_idle_inhibit_manager_v1 *m) {
    return &m->events.new_inhibitor;
}
struct wl_signal *pywl_idle_inhibitor_destroy(
        struct wlr_idle_inhibitor_v1 *i) {
    return &i->events.destroy;
}
struct wlr_idle_inhibitor_v1 *pywl_idle_inhibitor_from_link(struct wl_list *l) {
    struct wlr_idle_inhibitor_v1 *inhibitor;
    return wl_container_of(l, inhibitor, link);
}
"""


def _build():
    """Compile the inline cffi extension and return its (ffi, lib)."""
    def pkgcfg(flag, *pkgs):
        return subprocess.check_output(
            ["pkg-config", flag, *pkgs]
        ).decode().split()

    cflags = pkgcfg("--cflags", *_PKGS)
    libs = pkgcfg("--libs", *_PKGS)
    include_dirs = [a[2:] for a in cflags if a.startswith("-I")]
    extra_cflags = (
        [a for a in cflags if not a.startswith("-I")]
        + ["-DWLR_USE_UNSTABLE"]
    )
    libraries = [a[2:] for a in libs if a.startswith("-l")]
    library_dirs = [a[2:] for a in libs if a.startswith("-L")]

    build_dir = tempfile.mkdtemp(prefix="pywl-build-")

    # wlroots includes <xdg-shell-protocol.h>, which must be generated
    # locally from the xdg-shell.xml protocol description shipped with
    # wayland-protocols.
    protocols_dir = subprocess.check_output(
        ["pkg-config", "--variable=pkgdatadir", "wayland-protocols"]
    ).decode().strip()
    subprocess.check_call([
        "wayland-scanner", "server-header",
        os.path.join(protocols_dir, "stable/xdg-shell/xdg-shell.xml"),
        os.path.join(build_dir, "xdg-shell-protocol.h"),
    ])
    wlr_protocols_dir = subprocess.check_output(
        ["pkg-config", "--variable=pkgdatadir", "wlr-protocols"]
    ).decode().strip()
    for stem in (
            "wlr-layer-shell-unstable-v1",
            "wlr-output-power-management-unstable-v1",
    ):
        subprocess.check_call([
            "wayland-scanner", "server-header",
            os.path.join(wlr_protocols_dir, f"unstable/{stem}.xml"),
            os.path.join(build_dir, f"{stem}-protocol.h"),
        ])
    include_dirs.append(build_dir)

    builder = cffi.FFI()
    builder.cdef(CDEF)
    builder.set_source(
        _MODULE,
        SOURCE,
        include_dirs=include_dirs,
        libraries=libraries,
        library_dirs=library_dirs,
        extra_compile_args=extra_cflags + ["-w"],
    )
    print(f"Compiling bindings in {build_dir} ...")
    so_path = builder.compile(tmpdir=build_dir)

    spec = importlib.util.spec_from_file_location(_MODULE, so_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE] = mod
    spec.loader.exec_module(mod)

    return mod.ffi, mod.lib


def build():
    """Compile the cffi extension. Returns (ffi, lib, listen).
    Call once from main(); module-load itself is side-effect free."""
    ffi, lib = _build()

    @ffi.def_extern()
    def _pywl_dispatch(wl_listener, data):
        entry = listen.listeners.get(int(ffi.cast("uintptr_t", wl_listener)))
        if entry is not None:
            entry[1](data)

    def listen(signal, callback):
        """Register `callback(data)` on `signal`. Returns a handle with a
        `.remove()` method that detaches the underlying wl_listener.
        `.remove()` is safe to call more than once."""
        wl_listener = ffi.new("struct wl_listener *")
        wl_listener.notify = lib._pywl_dispatch  # pylint: disable=protected-access
        key = int(ffi.cast("uintptr_t", wl_listener))
        listen.listeners[key] = (wl_listener, callback)
        lib.pywl_signal_add(signal, wl_listener)

        def remove():
            entry = listen.listeners.pop(key, None)
            if entry is None:
                return
            held, _cb = entry
            lib.wl_list_remove(ffi.addressof(held[0], "link"))

        return SimpleNamespace(remove=remove)

    listen.listeners = {}

    @ffi.def_extern()
    def _pywl_timer_dispatch(data):
        callback = add_timer.timers.get(int(ffi.cast("uintptr_t", data)))
        if callback is not None:
            callback()
        return 0

    def add_timer(event_loop, callback):
        """Register a wayland event-loop timer that calls `callback()` on
        each fire. Returns a handle with a `.remove()` method that detaches
        the underlying wl_event_source and the trampoline entry.
        `.remove()` is safe to call more than once.

        The returned handle also exposes `.update(milliseconds)` to (re)arm
        the timer."""
        data = ffi.new_handle(callback)
        key = int(ffi.cast("uintptr_t", data))
        add_timer.timers[key] = callback
        source = lib.wl_event_loop_add_timer(
            event_loop, lib._pywl_timer_dispatch, data,  # pylint: disable=protected-access
        )

        def remove():
            removed = add_timer.timers.pop(key, None)
            if removed is None:
                return
            lib.wl_event_source_remove(source)

        def update(milliseconds):
            lib.wl_event_source_timer_update(source, milliseconds)

        return SimpleNamespace(remove=remove, update=update, source=source)

    add_timer.timers = {}

    return ffi, lib, listen, add_timer

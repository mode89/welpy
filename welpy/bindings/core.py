"""Inline cffi bindings to wlroots 0.20 / libwayland-server / xkbcommon.

Compiled at import time via `cffi.FFI.set_source` + `compile()` into a
tempdir and loaded as `_welpy_cffi`. This module holds the shared cdef
(base types + plumbing) and the compile machinery; sibling modules
register their feature's cdef + C glue via `register(builder)`.

Only the symbols required by welpy are exposed. Struct field access is
avoided by writing tiny C accessor helpers, so we do not depend on wlroots
struct layout — just on its public function ABI.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field

import cffi

from . import (render, output, scene, shell, xwayland,
               layer_shell, session_lock, idle, ext_workspace, libinput)
from . import input as input_bindings


logger = logging.getLogger(__name__)


def build_extension():
    """Compile the inline cffi extension and return its (ffi, lib)."""
    build_dir = tempfile.mkdtemp(prefix="welpy-build-")
    builder = Builder(build_dir=build_dir)
    builder.append(
        cdef=_CDEF, source=_SOURCE, pkgs=_PKGS,
        extra_compile_args=["-DWLR_USE_UNSTABLE", "-w"])

    register_bindings(builder)

    logger.info("building in %s ...", build_dir)
    so_path = builder.compile(_MODULE)

    spec = importlib.util.spec_from_file_location(_MODULE, so_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE] = mod
    spec.loader.exec_module(mod)

    return mod.ffi, mod.lib


def register_bindings(builder) -> None:
    """Inject every feature module's cdef and C glue into the build."""
    render.register(builder)
    output.register(builder)
    scene.register(builder)
    shell.register(builder)
    xwayland.register(builder)
    input_bindings.register(builder)
    layer_shell.register(builder)
    session_lock.register(builder)
    idle.register(builder)
    ext_workspace.register(builder)
    libinput.register(builder)


@dataclass
class Builder:  # pylint: disable=too-many-instance-attributes
    """Collects cffi contributions and drives the final compile.
    Centralizes wayland-scanner invocation so each contributor declares
    what it needs without duplicating subprocess plumbing."""
    build_dir: str
    include_dirs: list = field(default_factory=list)
    libraries: list = field(default_factory=list)
    library_dirs: list = field(default_factory=list)
    extra_compile_args: list = field(default_factory=list)
    ffi: cffi.FFI = field(default_factory=cffi.FFI)
    cdef: str = ""
    source: str = ""
    c_sources: list = field(default_factory=list)

    def append(  # pylint: disable=too-many-arguments
            self, *, cdef: str = "", source: str = "",
            c_sources: list = (), pkgs: tuple = (), include_dirs: list = (),
            libraries: list = (), library_dirs: list = (),
            extra_compile_args: list = ()) -> None:
        """Append fragments. `pkgs` are resolved via pkg-config so a
        contributor can pull in its own dependency's include dirs, libraries,
        and compile flags. Callers are append-only; no reordering."""
        self.cdef += cdef
        self.source += source
        self.c_sources.extend(c_sources)
        self.include_dirs.extend(include_dirs)
        self.libraries.extend(libraries)
        self.library_dirs.extend(library_dirs)
        self.extra_compile_args.extend(extra_compile_args)
        if pkgs:
            self._add_pkgs(pkgs)

    def _add_pkgs(self, pkgs: tuple) -> None:
        cflags = subprocess.check_output(
            ["pkg-config", "--cflags", *pkgs]).decode().split()
        libs = subprocess.check_output(
            ["pkg-config", "--libs", *pkgs]).decode().split()
        self.include_dirs.extend(a[2:] for a in cflags if a.startswith("-I"))
        self.libraries.extend(a[2:] for a in libs if a.startswith("-l"))
        self.library_dirs.extend(a[2:] for a in libs if a.startswith("-L"))
        self.extra_compile_args.extend(
            a for a in cflags if not a.startswith("-I"))

    def scanner(self, pkg: str, rel_xml: str, stem: str, *,
                private_code: bool = True) -> tuple:
        """Generate `<stem>-protocol.h` (and optionally `<stem>-protocol.c`)
        from `<pkg>'s pkgdatadir/<rel_xml>` into `build_dir`. Returns
        (header_path, c_path-or-None). With `private_code=False`, skip
        marshalling code generation -- use for protocols whose private
        code is already linked by a library we depend on."""
        protocols_dir = subprocess.check_output(
            ["pkg-config", "--variable=pkgdatadir", pkg]
        ).decode().strip()
        xml = os.path.join(protocols_dir, rel_xml)
        header = os.path.join(self.build_dir, f"{stem}-protocol.h")
        subprocess.check_call(
            ["wayland-scanner", "server-header", xml, header])
        if not private_code:
            return header, None
        csrc = os.path.join(self.build_dir, f"{stem}-protocol.c")
        subprocess.check_call(
            ["wayland-scanner", "private-code", xml, csrc])
        return header, csrc

    def enum_header(self, pkg: str, rel_xml: str, stem: str) -> str:
        """Generate `wayland-protocols/<stem>-enum.h` from `<pkg>'s
        pkgdatadir/<rel_xml>` into `build_dir`. wlroots 0.20 headers include
        these enum-only headers via `<wayland-protocols/...>`, so they must
        sit under that subdir on the include path."""
        protocols_dir = subprocess.check_output(
            ["pkg-config", "--variable=pkgdatadir", pkg]
        ).decode().strip()
        xml = os.path.join(protocols_dir, rel_xml)
        out_dir = os.path.join(self.build_dir, "wayland-protocols")
        os.makedirs(out_dir, exist_ok=True)
        header = os.path.join(out_dir, f"{stem}-enum.h")
        subprocess.check_call(
            ["wayland-scanner", "enum-header", xml, header])
        return header

    def compile(self, module_name: str) -> str:
        """Apply accumulated cdef/source and produce a compiled .so.
        `build_dir` is added to `include_dirs` so scanner outputs are
        visible to the C compiler."""
        self.ffi.cdef(self.cdef)
        self.ffi.set_source(
            module_name, self.source,
            sources=self.c_sources,
            include_dirs=[*self.include_dirs, self.build_dir],
            libraries=self.libraries,
            library_dirs=self.library_dirs,
            extra_compile_args=self.extra_compile_args,
        )
        return self.ffi.compile(tmpdir=self.build_dir)


_PKGS = ("wlroots-0.20", "wayland-server", "xkbcommon", "pixman-1",
         "xcb", "xcb-icccm", "xcb-ewmh")
_MODULE = "_welpy_cffi"


_CDEF = r"""
typedef _Bool bool;

typedef unsigned int uint32_t;

typedef int int32_t;

typedef short int16_t;

typedef unsigned short uint16_t;

typedef unsigned long uint64_t;

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

struct wlr_primary_selection_v1_device_manager;

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

struct wlr_surface { bool mapped; void *data; struct wl_list current_outputs; ...; };

struct wlr_surface_output {
    struct wlr_surface *surface;
    struct wlr_output *output;
    struct wl_list link;
    ...;
};

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

struct wlr_xwayland {
    const char *display_name;
    ...;
};

struct wlr_xwayland_surface {
    struct wlr_surface *surface;
    int16_t x, y;
    uint16_t width, height;
    bool override_redirect;
    bool fullscreen;
    struct wlr_xwayland_surface *parent;
    void *data;
    ...;
};

struct wlr_xwayland_surface_configure_event {
    struct wlr_xwayland_surface *surface;
    int16_t x, y;
    uint16_t width, height;
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

typedef int (*wl_event_loop_signal_func_t)(int signal_number, void *data);

struct wl_event_source *wl_event_loop_add_signal(
        struct wl_event_loop *, int signal_number,
        wl_event_loop_signal_func_t, void *data);

int wl_event_source_timer_update(struct wl_event_source *, int ms_delay);

int wl_event_source_remove(struct wl_event_source *);

const char *wl_display_add_socket_auto(struct wl_display *);

struct wl_event_loop *wl_display_get_event_loop(struct wl_display *);

struct wlr_drm_format_set;

// GPU buffer-sharing globals: wl_drm (legacy zero-copy), linux-dmabuf-v1
// (modern zero-copy import; wired into the scene for direct scan-out and
// presentation feedback), and linux-drm-syncobj-v1 (explicit timeline sync).
struct wlr_drm;

struct wlr_linux_dmabuf_v1;

struct wlr_linux_drm_syncobj_manager_v1;

struct wlr_output_state;

struct wlr_output_mode;

struct wlr_scene_rect;

struct wlr_xcursor_manager;

struct wlr_surface *wlr_surface_get_root_surface(struct wlr_surface *);

struct wlr_scene_node;

struct wlr_keyboard_group;

// Cursor / pointer
struct wlr_xcursor_manager;

struct wlr_pointer { struct wlr_input_device base; ...; };

struct wlr_pointer_motion_event {
    struct wlr_pointer *pointer;
    uint32_t time_msec;
    double delta_x;
    double delta_y;
    double unaccel_dx;
    double unaccel_dy;
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

struct xkb_rule_names {
    const char *rules;
    const char *model;
    const char *layout;
    const char *variant;
    const char *options;
};

void wlr_log_init(int verbosity, void *callback);

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

void wlr_surface_send_enter(struct wlr_surface *, struct wlr_output *);

struct wlr_xdg_decoration_manager_v1;

struct wlr_xdg_toplevel_decoration_v1 {
    struct wlr_xdg_toplevel *toplevel;
    ...;
};

// xdg-activation
struct wlr_xdg_activation_v1;

struct wlr_xdg_activation_v1_request_activate_event {
    struct wlr_surface *surface;
    ...;
};

// xdg-output: per-screen name/description for bars, screenshot tools, etc.
struct wlr_xdg_output_manager_v1;

// fractional-scale: lets apps render crisply at in-between scales (1.25x, 1.5x).
struct wlr_fractional_scale_manager_v1;

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

// gamma-control: wlsunset / gammastep set per-output gamma LUTs.
struct wlr_gamma_control_manager_v1;

// output-power-management: DPMS (clients turn screens on/off).
struct wlr_output_power_v1_set_mode_event {
    struct wlr_output *output;
    uint32_t mode;
    ...;
};

struct wlr_output_power_manager_v1;

// session-lock: swaylock-style screen lockers.
struct wlr_session_lock_manager_v1;

struct wlr_session_lock_v1;

struct wlr_session_lock_surface_v1 {
    struct wlr_output *output;
    struct wlr_surface *surface;
    ...;
};

// idle-notify: ext-idle-notify-v1 (swayidle), and idle-inhibit (video
// players asking the compositor not to dim/lock the screen).
struct wlr_idle_notifier_v1;

struct wlr_idle_inhibitor_v1 {
    struct wlr_surface *surface;
    struct wl_list link;
    ...;
};

struct wlr_idle_inhibit_manager_v1 {
    struct wl_list inhibitors;
    ...;
};

// pointer-constraints + relative-pointer: lock/confine the pointer and
// stream raw motion for games and 3D tools.
enum wlr_pointer_constraint_v1_type {
    WLR_POINTER_CONSTRAINT_V1_LOCKED,
    WLR_POINTER_CONSTRAINT_V1_CONFINED,
};

struct wlr_pointer_constraints_v1;

struct wlr_pointer_constraint_v1 {
    struct wlr_surface *surface;
    enum wlr_pointer_constraint_v1_type type;
    ...;
};

struct wlr_relative_pointer_manager_v1;

struct wlr_server_decoration_manager;

// our helpers
void welpy_signal_add(struct wl_signal *, struct wl_listener *);

struct wl_signal *welpy_surface_commit(struct wlr_surface *);

struct wl_signal *welpy_surface_unmap(struct wlr_surface *);

struct wlr_output_event_request_state {
    const struct wlr_output_state *state;
    ...;
};

// hit-test entry point
struct wlr_scene_buffer;

struct wl_signal *welpy_surface_map(struct wlr_surface *);

extern "Python" void _welpy_dispatch(struct wl_listener *, void *);

extern "Python" int _welpy_timer_dispatch(void *);

extern "Python" int _welpy_signal_dispatch(int, void *);
"""


_SOURCE = r"""
#define WLR_USE_UNSTABLE
#include <stdint.h>
#include <stdbool.h>
#include <stdlib.h>
#include <time.h>
#include <linux/input-event-codes.h>
#include <wayland-server-core.h>
#include <wlr/types/wlr_primary_selection.h>
#include <wlr/types/wlr_primary_selection_v1.h>
#include <wlr/backend.h>
#include <wlr/backend/session.h>
#include <wlr/render/allocator.h>
#include <wlr/render/wlr_renderer.h>
#include <wlr/types/wlr_buffer.h>
#include <wlr/types/wlr_drm.h>
#include <wlr/types/wlr_linux_dmabuf_v1.h>
#include <wlr/types/wlr_linux_drm_syncobj_v1.h>
#include <wlr/types/wlr_viewporter.h>
#include <wlr/types/wlr_alpha_modifier_v1.h>
#include <wlr/types/wlr_single_pixel_buffer_v1.h>
#include <wlr/types/wlr_presentation_time.h>
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
#include <wlr/types/wlr_fractional_scale_v1.h>
#include <wlr/types/wlr_output_management_v1.h>
#include <wlr/types/wlr_output_power_management_v1.h>
#include <wlr/types/wlr_gamma_control_v1.h>
#include <wlr/types/wlr_session_lock_v1.h>
#include <wlr/types/wlr_idle_notify_v1.h>
#include <wlr/types/wlr_idle_inhibit_v1.h>
#include <wlr/types/wlr_pointer_constraints_v1.h>
#include <wlr/types/wlr_relative_pointer_v1.h>
#include <wlr/util/region.h>
#include <wlr/types/wlr_server_decoration.h>
#include <wlr/types/wlr_xdg_shell.h>
#include <wlr/xwayland.h>
#include <wlr/util/box.h>
#include <wlr/util/log.h>
#include <xkbcommon/xkbcommon.h>

void welpy_signal_add(struct wl_signal *s, struct wl_listener *l) {
    wl_signal_add(s, l);
}

struct wl_signal *welpy_surface_commit(struct wlr_surface *s) {
    return &s->events.commit;
}

struct wl_signal *welpy_surface_map(struct wlr_surface *s) {
    return &s->events.map;
}

struct wl_signal *welpy_surface_unmap(struct wlr_surface *s) {
    return &s->events.unmap;
}
"""

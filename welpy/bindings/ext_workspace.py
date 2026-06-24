"""ext-workspace-v1 bindings: wayland-scanner output plus the static
C glue wiring the protocol request vtables to extern-Python callbacks."""


def contribute(builder) -> None:
    """Generate the scanner output and inject our cdef + C glue."""
    header, csrc = builder.scanner(
        "wayland-protocols",
        "staging/ext-workspace/ext-workspace-v1.xml",
        "ext-workspace-v1")
    builder.append(
        cdef=_CDEF,
        source=_SOURCE_TEMPLATE.format(header=header),
        c_sources=[csrc],
    )


_CDEF = r"""
struct wl_client;
struct wl_resource;
struct wl_global;

extern "Python" void _welpy_extws_bind(void *, struct wl_resource *);
extern "Python" void _welpy_extws_destroyed(struct wl_resource *);
extern "Python" void _welpy_extws_mgr_commit(
    struct wl_client *, struct wl_resource *);
extern "Python" void _welpy_extws_mgr_stop(
    struct wl_client *, struct wl_resource *);
extern "Python" void _welpy_extws_group_create_workspace(
    struct wl_client *, struct wl_resource *, const char *);
extern "Python" void _welpy_extws_handle_activate(
    struct wl_client *, struct wl_resource *);
extern "Python" void _welpy_extws_handle_deactivate(
    struct wl_client *, struct wl_resource *);
extern "Python" void _welpy_extws_handle_assign(
    struct wl_client *, struct wl_resource *, struct wl_resource *);
extern "Python" void _welpy_extws_handle_remove(
    struct wl_client *, struct wl_resource *);

struct wl_global *welpy_extws_manager_create(
    struct wl_display *, void *data);
void welpy_extws_manager_destroy(struct wl_global *);
struct wl_resource *welpy_extws_create_group(struct wl_resource *manager);
struct wl_resource *welpy_extws_create_handle(struct wl_resource *manager);
struct wl_resource *welpy_extws_output_resource(
    struct wl_client *, struct wlr_output *);
void welpy_extws_destroy_resource(struct wl_resource *);
struct wl_client *welpy_extws_resource_client(struct wl_resource *);

void welpy_extws_send_workspace_group(
    struct wl_resource *mgr, struct wl_resource *group);
void welpy_extws_send_workspace(
    struct wl_resource *mgr, struct wl_resource *handle);
void welpy_extws_send_done(struct wl_resource *mgr);
void welpy_extws_send_finished(struct wl_resource *mgr);
void welpy_extws_send_group_capabilities(
    struct wl_resource *group, uint32_t caps);
void welpy_extws_send_group_output_enter(
    struct wl_resource *group, struct wl_resource *output);
void welpy_extws_send_group_output_leave(
    struct wl_resource *group, struct wl_resource *output);
void welpy_extws_send_workspace_enter(
    struct wl_resource *group, struct wl_resource *handle);
void welpy_extws_send_workspace_leave(
    struct wl_resource *group, struct wl_resource *handle);
void welpy_extws_send_group_removed(struct wl_resource *group);
void welpy_extws_send_id(struct wl_resource *handle, const char *id);
void welpy_extws_send_name(struct wl_resource *handle, const char *name);
void welpy_extws_send_coordinates(
    struct wl_resource *handle, uint32_t *coords, size_t n);
void welpy_extws_send_state(struct wl_resource *handle, uint32_t state);
void welpy_extws_send_handle_capabilities(
    struct wl_resource *handle, uint32_t caps);
void welpy_extws_send_handle_removed(struct wl_resource *handle);
"""


_SOURCE_TEMPLATE = r"""
#include <stdlib.h>
#include <string.h>
#include <wayland-server-core.h>
#include <wlr/types/wlr_output.h>
#include "{header}"

// Forward declarations for extern-Python callbacks; cffi emits these as
// `static` definitions after this SOURCE, so the impl tables below can't
// see them without matching `static` forwards.
static void _welpy_extws_bind(void *, struct wl_resource *);
static void _welpy_extws_destroyed(struct wl_resource *);
static void _welpy_extws_mgr_commit(
    struct wl_client *, struct wl_resource *);
static void _welpy_extws_mgr_stop(
    struct wl_client *, struct wl_resource *);
static void _welpy_extws_group_create_workspace(
    struct wl_client *, struct wl_resource *, const char *);
static void _welpy_extws_handle_activate(
    struct wl_client *, struct wl_resource *);
static void _welpy_extws_handle_deactivate(
    struct wl_client *, struct wl_resource *);
static void _welpy_extws_handle_assign(
    struct wl_client *, struct wl_resource *, struct wl_resource *);
static void _welpy_extws_handle_remove(
    struct wl_client *, struct wl_resource *);

static void _welpy_extws_destroy_request(
        struct wl_client *client, struct wl_resource *r) {{
    (void)client;
    wl_resource_destroy(r);
}}

static const struct ext_workspace_manager_v1_interface _welpy_extws_mgr_impl = {{
    .commit = _welpy_extws_mgr_commit,
    .stop = _welpy_extws_mgr_stop,
}};
static const struct ext_workspace_group_handle_v1_interface
        _welpy_extws_group_impl = {{
    .create_workspace = _welpy_extws_group_create_workspace,
    .destroy = _welpy_extws_destroy_request,
}};
static const struct ext_workspace_handle_v1_interface _welpy_extws_handle_impl = {{
    .destroy = _welpy_extws_destroy_request,
    .activate = _welpy_extws_handle_activate,
    .deactivate = _welpy_extws_handle_deactivate,
    .assign = _welpy_extws_handle_assign,
    .remove = _welpy_extws_handle_remove,
}};

static void _welpy_extws_resource_destroyed(struct wl_resource *r) {{
    _welpy_extws_destroyed(r);
}}

static void _welpy_extws_bind_cb(struct wl_client *client, void *data,
        uint32_t version, uint32_t id) {{
    struct wl_resource *r = wl_resource_create(
        client, &ext_workspace_manager_v1_interface, (int)version, id);
    if (r == NULL) {{
        wl_client_post_no_memory(client);
        return;
    }}
    wl_resource_set_implementation(
        r, &_welpy_extws_mgr_impl, data, _welpy_extws_resource_destroyed);
    _welpy_extws_bind(data, r);
}}

struct wl_global *welpy_extws_manager_create(
        struct wl_display *display, void *data) {{
    return wl_global_create(
        display, &ext_workspace_manager_v1_interface, 1,
        data, _welpy_extws_bind_cb);
}}

void welpy_extws_manager_destroy(struct wl_global *g) {{
    wl_global_destroy(g);
}}

struct wl_resource *welpy_extws_create_group(struct wl_resource *manager) {{
    struct wl_resource *r = wl_resource_create(
        wl_resource_get_client(manager),
        &ext_workspace_group_handle_v1_interface,
        wl_resource_get_version(manager), 0);
    if (r == NULL) return NULL;
    wl_resource_set_implementation(
        r, &_welpy_extws_group_impl, NULL,
        _welpy_extws_resource_destroyed);
    return r;
}}

struct wl_resource *welpy_extws_create_handle(struct wl_resource *manager) {{
    struct wl_resource *r = wl_resource_create(
        wl_resource_get_client(manager),
        &ext_workspace_handle_v1_interface,
        wl_resource_get_version(manager), 0);
    if (r == NULL) return NULL;
    wl_resource_set_implementation(
        r, &_welpy_extws_handle_impl, NULL,
        _welpy_extws_resource_destroyed);
    return r;
}}

struct wl_resource *welpy_extws_output_resource(
        struct wl_client *client, struct wlr_output *output) {{
    struct wl_resource *r;
    wl_resource_for_each(r, &output->resources) {{
        if (wl_resource_get_client(r) == client) return r;
    }}
    return NULL;
}}

void welpy_extws_destroy_resource(struct wl_resource *r) {{
    wl_resource_destroy(r);
}}

struct wl_client *welpy_extws_resource_client(struct wl_resource *r) {{
    return wl_resource_get_client(r);
}}

void welpy_extws_send_workspace_group(
        struct wl_resource *mgr, struct wl_resource *g) {{
    ext_workspace_manager_v1_send_workspace_group(mgr, g);
}}
void welpy_extws_send_workspace(
        struct wl_resource *mgr, struct wl_resource *h) {{
    ext_workspace_manager_v1_send_workspace(mgr, h);
}}
void welpy_extws_send_done(struct wl_resource *mgr) {{
    ext_workspace_manager_v1_send_done(mgr);
}}
void welpy_extws_send_finished(struct wl_resource *mgr) {{
    ext_workspace_manager_v1_send_finished(mgr);
}}

void welpy_extws_send_group_capabilities(struct wl_resource *g, uint32_t caps) {{
    ext_workspace_group_handle_v1_send_capabilities(g, caps);
}}
void welpy_extws_send_group_output_enter(
        struct wl_resource *g, struct wl_resource *o) {{
    ext_workspace_group_handle_v1_send_output_enter(g, o);
}}
void welpy_extws_send_group_output_leave(
        struct wl_resource *g, struct wl_resource *o) {{
    ext_workspace_group_handle_v1_send_output_leave(g, o);
}}
void welpy_extws_send_workspace_enter(
        struct wl_resource *g, struct wl_resource *h) {{
    ext_workspace_group_handle_v1_send_workspace_enter(g, h);
}}
void welpy_extws_send_workspace_leave(
        struct wl_resource *g, struct wl_resource *h) {{
    ext_workspace_group_handle_v1_send_workspace_leave(g, h);
}}
void welpy_extws_send_group_removed(struct wl_resource *g) {{
    ext_workspace_group_handle_v1_send_removed(g);
}}

void welpy_extws_send_id(struct wl_resource *h, const char *id) {{
    ext_workspace_handle_v1_send_id(h, id);
}}
void welpy_extws_send_name(struct wl_resource *h, const char *name) {{
    ext_workspace_handle_v1_send_name(h, name);
}}
void welpy_extws_send_coordinates(
        struct wl_resource *h, uint32_t *coords, size_t n) {{
    struct wl_array arr;
    arr.size = n * sizeof(uint32_t);
    arr.alloc = arr.size;
    arr.data = coords;
    ext_workspace_handle_v1_send_coordinates(h, &arr);
}}
void welpy_extws_send_state(struct wl_resource *h, uint32_t state) {{
    ext_workspace_handle_v1_send_state(h, state);
}}
void welpy_extws_send_handle_capabilities(
        struct wl_resource *h, uint32_t caps) {{
    ext_workspace_handle_v1_send_capabilities(h, caps);
}}
void welpy_extws_send_handle_removed(struct wl_resource *h) {{
    ext_workspace_handle_v1_send_removed(h);
}}
"""

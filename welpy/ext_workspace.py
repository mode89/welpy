"""ext-workspace-v1 protocol: exposes workspaces to bars/taskbars.

Run-time (bindings live in `welpy/bindings/ext_workspace.py`):
- `create(server)` registers the global and the request callbacks.
- `publish(ext)` diffs the current workspace state against what each bound
  client last saw and emits exactly one `done` per client.
- `destroy(ext)` tears the global down.

Workspace<->monitor mapping is per-monitor: one workspace_group per Monitor,
one workspace_handle per Workspace with a monitor. Orphan workspaces
(monitor=None) are hidden -- no handle is exposed for them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any


logger = logging.getLogger(__name__)


# Workspace state bitfield from the protocol's `state` enum.
_STATE_ACTIVE = 1
_STATE_URGENT = 2

# Workspace capabilities the compositor exposes.
_HANDLE_CAPS = (
    1  # activate
    | 8  # assign
)


@dataclass
class _Group:
    """One client's protocol view of a monitor."""
    resource: Any
    monitor: Any
    output_resource: Any  # wl_resource * for the client's wl_output, or NULL


@dataclass
class _Workspace:
    """One client's protocol view of a workspace."""
    workspace: Any
    resource: Any
    monitor: Any  # last-emitted group assignment
    active: bool  # last-emitted active flag
    urgent: bool  # last-emitted urgent flag


@dataclass
class _Manager:
    """One client's bound manager resource and the per-client groups and
    workspace handles it has created."""
    resource: Any
    groups: list = field(default_factory=list)      # _Group
    workspaces: list = field(default_factory=list)  # _Workspace


@dataclass
class ExtWorkspace:  # pylint: disable=too-many-instance-attributes
    """Runtime state for the ext-workspace-v1 global. Reaches its owning
    server via `Server.ext_workspace`, so we don't store a back-reference;
    `lib` and `ffi` are pinned at create time for the inner helpers."""
    lib: Any              # cffi-compiled C library
    ffi: Any              # cffi FFI instance
    handle: Any           # ffi.new_handle keeping `self` alive for C
    global_: Any          # wl_global *
    on_activate: Any      # callback: (name) -> None
    on_assign: Any        # callback: (workspace, target_monitor) -> None
    managers: list = field(default_factory=list)  # _Manager, one per client


def create(server, *, on_activate, on_assign) -> ExtWorkspace:
    """Register the ext-workspace-v1 global and wire request callbacks.
    `on_activate(name)` runs when a client activates a workspace handle;
    `on_assign(workspace, target_monitor)` runs when a client moves a
    workspace to a different group."""
    ext = ExtWorkspace(
        lib=server.lib, ffi=server.ffi,
        handle=None, global_=None,
        on_activate=on_activate, on_assign=on_assign)
    ext.handle = server.ffi.new_handle(ext)
    _register_externs(server, ext)
    ext.global_ = server.lib.welpy_extws_manager_create(
        server.display, ext.handle)
    return ext


def destroy(ext: ExtWorkspace) -> None:
    """Tear the global down and forget all per-client state."""
    if ext.global_ is not None:
        ext.lib.welpy_extws_manager_destroy(ext.global_)
        ext.global_ = None
    ext.managers.clear()


def publish(server) -> None:
    """Sync each bound client to the current server workspace state and
    emit one `done` per client."""
    ext = server.ext_workspace
    for manager in list(ext.managers):
        _publish_manager(server, ext, manager)


# pylint: disable-next=too-many-locals,too-many-branches,too-many-statements
def _publish_manager(server, ext: ExtWorkspace, manager: _Manager) -> None:
    """Diff one client's last-emitted state against the live state and
    emit the minimum set of events, then exactly one `done`."""
    lib, ffi = ext.lib, ext.ffi
    monitors = list(server.monitors)
    monitor_ids = {id(m) for m in monitors}
    workspaces_live = [w for w in server.workspaces if w.monitor in monitors]
    workspace_ids = {id(w) for w in workspaces_live}

    # Phase 1: drop workspaces whose monitor is gone, then groups whose
    # monitor is gone. Order matters: `removed` on a group is only allowed
    # once all its workspaces have left.
    for entry in list(manager.workspaces):
        if id(entry.workspace) not in workspace_ids:
            old_group = _group_for_monitor(manager, entry.monitor)
            if old_group is not None:
                lib.welpy_extws_send_workspace_leave(
                    old_group.resource, entry.resource)
            lib.welpy_extws_send_handle_removed(entry.resource)
            manager.workspaces.remove(entry)

    for entry in list(manager.groups):
        if id(entry.monitor) not in monitor_ids:
            lib.welpy_extws_send_group_removed(entry.resource)
            manager.groups.remove(entry)

    # Phase 2: create groups for new monitors and announce them.
    for m in monitors:
        if _group_for_monitor(manager, m) is None:
            res = lib.welpy_extws_create_group(manager.resource)
            lib.welpy_extws_send_workspace_group(manager.resource, res)
            lib.welpy_extws_send_group_capabilities(res, 0)
            client = lib.welpy_extws_resource_client(manager.resource)
            out_res = lib.welpy_extws_output_resource(client, m.output)
            if out_res != ffi.NULL:
                lib.welpy_extws_send_group_output_enter(res, out_res)
            entry = _Group(
                resource=res, monitor=m, output_resource=out_res)
            manager.groups.append(entry)

    # Phase 3: create workspaces for newly-monitored welpy Workspaces.
    for ws in workspaces_live:
        if _workspace_entry(manager, ws) is None:
            res = lib.welpy_extws_create_handle(manager.resource)
            lib.welpy_extws_send_workspace(manager.resource, res)
            name = ws.name.encode()
            lib.welpy_extws_send_id(res, name)
            lib.welpy_extws_send_name(res, name)
            coords = ffi.new("uint32_t[1]", [server.workspaces.index(ws)])
            lib.welpy_extws_send_coordinates(res, coords, 1)
            lib.welpy_extws_send_handle_capabilities(res, _HANDLE_CAPS)
            active = ws.monitor.active_workspace is ws
            urgent = _is_urgent(server, ws)
            lib.welpy_extws_send_state(res, _state_bits(active, urgent))
            lib.welpy_extws_send_workspace_enter(
                _group_for_monitor(manager, ws.monitor).resource, res)
            entry = _Workspace(
                workspace=ws, resource=res, monitor=ws.monitor,
                active=active, urgent=urgent)
            manager.workspaces.append(entry)

    # Phase 4: emit deltas for surviving workspaces (reassignment + state).
    for entry in manager.workspaces:
        ws = entry.workspace
        if entry.monitor is not ws.monitor:
            # The old monitor's group may already be gone (unplugged this
            # publish), in which case its `removed` covered the leave.
            old_group = _group_for_monitor(manager, entry.monitor)
            if old_group is not None:
                lib.welpy_extws_send_workspace_leave(
                    old_group.resource, entry.resource)
            lib.welpy_extws_send_workspace_enter(
                _group_for_monitor(manager, ws.monitor).resource,
                entry.resource)
            entry.monitor = ws.monitor
        active = ws.monitor.active_workspace is ws
        urgent = _is_urgent(server, ws)
        if entry.active != active or entry.urgent != urgent:
            lib.welpy_extws_send_state(
                entry.resource, _state_bits(active, urgent))
            entry.active = active
            entry.urgent = urgent

    lib.welpy_extws_send_done(manager.resource)


def _is_urgent(server, ws) -> bool:
    return any(c.urgent for c in server.clients if c.workspace is ws)


def _state_bits(active: bool, urgent: bool) -> int:
    return (_STATE_ACTIVE if active else 0) | (_STATE_URGENT if urgent else 0)


def _addr(ffi, resource) -> int:
    return int(ffi.cast("uintptr_t", resource))


def _group_for_monitor(manager: _Manager, monitor):
    for entry in manager.groups:
        if entry.monitor is monitor:
            return entry
    return None


def _workspace_entry(manager: _Manager, workspace):
    for entry in manager.workspaces:
        if entry.workspace is workspace:
            return entry
    return None


def _find_group(ext: ExtWorkspace, resource):
    """Return (manager, _Group) owning `resource`, or (None, None)."""
    addr = _addr(ext.ffi, resource)
    for manager in ext.managers:
        for entry in manager.groups:
            if _addr(ext.ffi, entry.resource) == addr:
                return manager, entry
    return None, None


def _find_workspace(ext: ExtWorkspace, resource):
    """Return (manager, _Workspace) owning `resource`, or (None, None)."""
    addr = _addr(ext.ffi, resource)
    for manager in ext.managers:
        for entry in manager.workspaces:
            if _addr(ext.ffi, entry.resource) == addr:
                return manager, entry
    return None, None


def _register_externs(server, ext: ExtWorkspace) -> None:
    """Plug Python implementations into the cdef'd extern callbacks.
    The closures capture `server` (for live state in publish) and `ext`,
    so re-creating the global rebinds them to the new instance."""
    ffi, lib = ext.ffi, ext.lib

    @ffi.def_extern()
    def _welpy_extws_bind(_data, resource):
        manager = _Manager(resource=resource)
        ext.managers.append(manager)
        _publish_manager(server, ext, manager)

    @ffi.def_extern()
    def _welpy_extws_destroyed(resource):
        _on_resource_destroyed(ext, resource)

    @ffi.def_extern()
    def _welpy_extws_mgr_commit(_client, _resource):
        # Requests are applied immediately; commit is a no-op since each
        # mutation already produces its own `done` via `publish`.
        pass

    @ffi.def_extern()
    def _welpy_extws_mgr_stop(_client, resource):
        lib.welpy_extws_send_finished(resource)
        lib.welpy_extws_destroy_resource(resource)

    @ffi.def_extern()
    def _welpy_extws_group_create_workspace(_client, _resource, _name):
        pass  # capability not advertised; spec says ignore

    @ffi.def_extern()
    def _welpy_extws_handle_activate(_client, resource):
        _, entry = _find_workspace(ext, resource)
        if entry is not None:
            ext.on_activate(entry.workspace.name)

    @ffi.def_extern()
    def _welpy_extws_handle_deactivate(_client, _resource):
        pass  # not advertised

    @ffi.def_extern()
    def _welpy_extws_handle_assign(_client, handle_r, group_r):
        _, ws_entry = _find_workspace(ext, handle_r)
        _, group = _find_group(ext, group_r)
        if ws_entry is None or group is None:
            return
        ws = ws_entry.workspace
        if group.monitor is ws.monitor:
            return
        ext.on_assign(ws, group.monitor)

    @ffi.def_extern()
    def _welpy_extws_handle_remove(_client, _resource):
        pass  # not advertised


def _on_resource_destroyed(ext: ExtWorkspace, resource) -> None:
    """Fires for any resource we tracked. Removes the matching record;
    a missing entry is fine -- the resource may already have been forgotten
    by a `removed` event sent ahead of the client's destroy request."""
    addr = _addr(ext.ffi, resource)
    for manager in ext.managers:
        if _addr(ext.ffi, manager.resource) == addr:
            ext.managers.remove(manager)
            return
    manager, entry = _find_workspace(ext, resource)
    if entry is not None:
        manager.workspaces.remove(entry)
        return
    manager, entry = _find_group(ext, resource)
    if entry is not None:
        manager.groups.remove(entry)

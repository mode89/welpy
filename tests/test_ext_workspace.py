"""Unit tests for welpy.ext_workspace: the ext-workspace-v1 protocol exposing
workspaces and per-monitor groups to clients, plus activate/assign requests."""

from unittest.mock import MagicMock

from welpy import ext_workspace
from tests.helpers import (
    make_client, make_monitor, make_server, make_workspace,
)


def make_extws_server(**kwargs):
    """Server mock prepared for ext_workspace tests: ffi.cast returns the
    object's id so `_addr` is a usable hashable key, and resource factories
    yield a fresh mock per call so each group/handle gets a unique address."""
    server = make_server(**kwargs)
    server.ffi.cast = lambda _type, ptr: id(ptr)
    server.ffi.NULL = 0
    server.ffi.new_handle = lambda obj: obj
    server.ffi.new = lambda *_a, **_kw: MagicMock(name="new")
    server.lib.welpy_extws_create_group.side_effect = (
        lambda *_a: MagicMock(name="group_resource"))
    server.lib.welpy_extws_create_handle.side_effect = (
        lambda *_a: MagicMock(name="handle_resource"))
    server.lib.welpy_extws_resource_client.side_effect = (
        lambda r: MagicMock(name="client"))
    server.lib.welpy_extws_output_resource.side_effect = (
        lambda *_a: MagicMock(name="output_resource"))
    server.lib.welpy_extws_manager_create.return_value = MagicMock(
        name="global")
    return server


def make_extws(server, externs=None, on_activate=None, on_assign=None):
    """Build a ExtWorkspace against `server`, capturing the registered
    extern callbacks into `externs` (a dict keyed by function name)."""
    if externs is None:
        externs = {}

    def fake_def_extern():
        def decorator(f):
            externs[f.__name__] = f
            return f
        return decorator
    server.ffi.def_extern = fake_def_extern
    ext = ext_workspace.create(
        server,
        on_activate=on_activate or MagicMock(name="on_activate"),
        on_assign=on_assign or MagicMock(name="on_assign"),
    )
    server.ext_workspace = ext
    return ext, externs


def bind_extws_client(ext, externs):
    """Simulate a client binding the manager global. Returns the manager
    resource mock used."""
    manager = MagicMock(name="manager_resource")
    externs["_welpy_extws_bind"](ext.handle, manager)
    return manager


def extws_group(manager, monitor):
    """Find a client's group entry by the monitor it represents."""
    return next(g for g in manager.groups if g.monitor is monitor)


def extws_handle(manager, workspace):
    """Find a client's handle entry by the workspace it represents."""
    return next(w for w in manager.workspaces if w.workspace is workspace)


def test_extws_create():
    """create() creates the wl_global via the C helper, passing the ext
    handle so the bind callback can find it."""
    server = make_extws_server()
    ext, _ = make_extws(server)

    server.lib.welpy_extws_manager_create.assert_called_once_with(
        server.display, ext.handle)
    assert ext.global_ == server.lib.welpy_extws_manager_create.return_value


def test_extws_destroy():
    """destroy() releases the global and clears all per-client state."""
    server = make_extws_server()
    ext, externs = make_extws(server)
    bind_extws_client(ext, externs)

    ext_workspace.destroy(ext)

    server.lib.welpy_extws_manager_destroy.assert_called_once()
    assert ext.global_ is None
    assert not ext.managers


def test_extws_bind_initial():
    """Binding sends workspace_group + workspace events for the current
    layout, followed by exactly one `done`."""
    monitor = make_monitor()
    ws = make_workspace(name="1", monitor=monitor)
    monitor.active_workspace = ws
    server = make_extws_server(monitors=[monitor], workspaces=[ws])
    ext, externs = make_extws(server)

    manager = bind_extws_client(ext, externs)

    server.lib.welpy_extws_send_workspace_group.assert_called_once()
    server.lib.welpy_extws_send_workspace.assert_called_once()
    server.lib.welpy_extws_send_done.assert_called_once_with(manager)


def test_extws_layout_groups():
    """With N monitors, the manager receives N group resources and one
    handle per non-orphan workspace."""
    m1, m2 = make_monitor(), make_monitor()
    ws1 = make_workspace(name="1", monitor=m1)
    ws2 = make_workspace(name="2", monitor=m2)
    orphan = make_workspace(name="3")
    m1.active_workspace = ws1
    m2.active_workspace = ws2
    server = make_extws_server(
        monitors=[m1, m2], workspaces=[ws1, ws2, orphan])
    ext, externs = make_extws(server)

    bind_extws_client(ext, externs)

    manager = ext.managers[0]
    assert {id(g.monitor) for g in manager.groups} == {id(m1), id(m2)}
    assert {id(w.workspace) for w in manager.workspaces} == {id(ws1), id(ws2)}


def test_extws_orphan_hidden():
    """Orphan workspaces (monitor=None) are not exposed as handles."""
    monitor = make_monitor()
    ws = make_workspace(name="1", monitor=monitor)
    orphan = make_workspace(name="2")
    monitor.active_workspace = ws
    server = make_extws_server(monitors=[monitor], workspaces=[ws, orphan])
    ext, externs = make_extws(server)

    bind_extws_client(ext, externs)

    manager = ext.managers[0]
    assert all(w.workspace is not orphan for w in manager.workspaces)
    assert server.lib.welpy_extws_create_handle.call_count == 1


def test_extws_publish_done():
    """Each publish() call emits one `done` per bound client, no more."""
    monitor = make_monitor()
    ws = make_workspace(name="1", monitor=monitor)
    monitor.active_workspace = ws
    server = make_extws_server(monitors=[monitor], workspaces=[ws])
    ext, externs = make_extws(server)
    bind_extws_client(ext, externs)
    bind_extws_client(ext, externs)
    server.lib.welpy_extws_send_done.reset_mock()

    ext_workspace.publish(server)

    assert server.lib.welpy_extws_send_done.call_count == 2


def test_extws_activate():
    """Receiving an `activate` request invokes the on_activate callback
    with the workspace name."""
    monitor = make_monitor()
    ws = make_workspace(name="3", monitor=monitor)
    monitor.active_workspace = ws
    server = make_extws_server(monitors=[monitor], workspaces=[ws])
    on_activate = MagicMock(name="on_activate")
    ext, externs = make_extws(server, on_activate=on_activate)
    bind_extws_client(ext, externs)
    manager = ext.managers[0]
    handle_r = extws_handle(manager, ws).resource

    externs["_welpy_extws_handle_activate"]("CLIENT", handle_r)

    on_activate.assert_called_once_with("3")


def test_extws_assign():
    """Receiving an `assign` request invokes the on_assign callback with
    the workspace and the target monitor."""
    m1, m2 = make_monitor(), make_monitor()
    ws = make_workspace(name="1", monitor=m1)
    other = make_workspace(name="2", monitor=m2)
    m1.active_workspace = ws
    m2.active_workspace = other
    server = make_extws_server(monitors=[m1, m2], workspaces=[ws, other])
    on_assign = MagicMock(name="on_assign")
    ext, externs = make_extws(server, on_assign=on_assign)
    bind_extws_client(ext, externs)
    manager = ext.managers[0]
    handle_r = extws_handle(manager, ws).resource
    target_group_r = extws_group(manager, m2).resource

    externs["_welpy_extws_handle_assign"](
        "CLIENT", handle_r, target_group_r)

    on_assign.assert_called_once_with(ws, m2)


def test_extws_orphan_transition():
    """A workspace gaining a monitor causes a new handle; losing the
    monitor causes `removed` and frees the entry."""
    monitor = make_monitor()
    ws = make_workspace(name="1", monitor=monitor)
    other = make_workspace(name="2")
    monitor.active_workspace = ws
    server = make_extws_server(monitors=[monitor], workspaces=[ws, other])
    ext, externs = make_extws(server)
    bind_extws_client(ext, externs)
    manager = ext.managers[0]
    assert all(w.workspace is not other for w in manager.workspaces)

    other.monitor = monitor
    ext_workspace.publish(server)

    assert any(w.workspace is other for w in manager.workspaces)

    handle_r = extws_handle(manager, other).resource
    other.monitor = None
    ext_workspace.publish(server)

    server.lib.welpy_extws_send_handle_removed.assert_any_call(handle_r)
    assert all(w.workspace is not other for w in manager.workspaces)


def test_extws_monitor_unplug():
    """Dropping a monitor sends `removed` on its group resource and
    forgets the entry."""
    m1, m2 = make_monitor(), make_monitor()
    ws1 = make_workspace(name="1", monitor=m1)
    ws2 = make_workspace(name="2", monitor=m2)
    m1.active_workspace = ws1
    m2.active_workspace = ws2
    server = make_extws_server(monitors=[m1, m2], workspaces=[ws1, ws2])
    ext, externs = make_extws(server)
    bind_extws_client(ext, externs)
    manager = ext.managers[0]
    group_r = extws_group(manager, m2).resource

    server.monitors.remove(m2)
    ws2.monitor = None
    ext_workspace.publish(server)

    server.lib.welpy_extws_send_group_removed.assert_any_call(group_r)
    assert all(g.monitor is not m2 for g in manager.groups)


def test_extws_unplug_migrate():
    """Unplugging a monitor whose workspace migrates to a surviving monitor
    re-homes the handle without dereferencing the just-removed group."""
    m1, m2 = make_monitor(), make_monitor()
    ws1 = make_workspace(name="1", monitor=m1)
    ws2 = make_workspace(name="2", monitor=m2)
    m1.active_workspace = ws1
    m2.active_workspace = ws2
    server = make_extws_server(monitors=[m1, m2], workspaces=[ws1, ws2])
    ext, externs = make_extws(server)
    bind_extws_client(ext, externs)
    manager = ext.managers[0]
    handle_r = extws_handle(manager, ws1).resource
    new_group_r = extws_group(manager, m2).resource
    server.lib.welpy_extws_send_workspace_enter.reset_mock()

    server.monitors.remove(m1)
    ws1.monitor = m2
    ext_workspace.publish(server)

    assert extws_handle(manager, ws1).monitor is m2
    server.lib.welpy_extws_send_workspace_enter.assert_any_call(
        new_group_r, handle_r)


def test_extws_active_change():
    """Swapping the active workspace on a monitor emits a `state` event on
    each affected handle (one going active, one going inactive)."""
    monitor = make_monitor()
    ws1 = make_workspace(name="1", monitor=monitor)
    ws2 = make_workspace(name="2", monitor=monitor)
    monitor.active_workspace = ws1
    server = make_extws_server(monitors=[monitor], workspaces=[ws1, ws2])
    ext, externs = make_extws(server)
    bind_extws_client(ext, externs)
    manager = ext.managers[0]
    handle_r1 = extws_handle(manager, ws1).resource
    handle_r2 = extws_handle(manager, ws2).resource
    server.lib.welpy_extws_send_state.reset_mock()

    monitor.active_workspace = ws2
    ext_workspace.publish(server)

    sent = {c.args for c in server.lib.welpy_extws_send_state.mock_calls}
    assert (handle_r1, 0) in sent
    assert (handle_r2, 1) in sent


def test_extws_urgent_state():
    """A window flagged urgent publishes the urgent bit on its workspace
    handle, OR'd with the active bit."""
    monitor = make_monitor()
    ws1 = make_workspace(name="1", monitor=monitor)
    monitor.active_workspace = ws1
    client = make_client(workspace=ws1)
    server = make_extws_server(
        monitors=[monitor], workspaces=[ws1], clients=[client])
    ext, externs = make_extws(server)
    bind_extws_client(ext, externs)
    manager = ext.managers[0]
    handle_r1 = extws_handle(manager, ws1).resource
    server.lib.welpy_extws_send_state.reset_mock()

    client.urgent = True
    ext_workspace.publish(server)

    sent = {c.args for c in server.lib.welpy_extws_send_state.mock_calls}
    assert (handle_r1, 3) in sent

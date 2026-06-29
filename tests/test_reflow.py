"""Unit tests for welpy.reflow: the single authority for re-flow ordering --
the window-, topology-, and output-layout-scoped re-flow entry points."""

from unittest.mock import MagicMock, call, patch

from welpy import layout, model, reflow
from tests.helpers import make_server, make_monitor, make_session_lock


def _patch_phases(mgr):
    """Replace every re-flow phase with a child of `mgr` so `mgr.mock_calls`
    records the global call order across phases."""
    return (
        patch("welpy.geometry.apply_hierarchy", mgr.apply_hierarchy),
        patch("welpy.geometry.apply_visibility", mgr.apply_visibility),
        patch("welpy.geometry.apply_tree", mgr.apply_tree),
        patch("welpy.geometry.reconcile", mgr.reconcile),
        patch("welpy.focus.reconcile", mgr.focus_reconcile),
        patch("welpy.ext_workspace.publish", mgr.publish),
    )


def test_window_single_monitor():
    """A window-scope reflow reconciles exactly its one screen, no repair,
    no publish."""
    server = make_server()
    m = make_monitor()
    mgr = MagicMock()
    p = _patch_phases(mgr)
    with p[0], p[1], p[2], p[3], p[4], p[5]:
        reflow.window(server, m)

    assert mgr.mock_calls == [
        call.apply_tree(server),
        call.reconcile(server, m),
        call.focus_reconcile(server),
    ]


def test_window_none_monitor():
    """A window-scope reflow with no screen still settles the tree and focus
    but reconciles no screen."""
    server = make_server()
    mgr = MagicMock()
    p = _patch_phases(mgr)
    with p[0], p[1], p[2], p[3], p[4], p[5]:
        reflow.window(server, None)

    assert mgr.mock_calls == [
        call.apply_tree(server),
        call.focus_reconcile(server),
    ]


def test_topology_all_monitors():
    """A topology-scope reflow repairs, reconciles every screen, and
    publishes."""
    m1, m2 = make_monitor(), make_monitor()
    server = make_server(monitors=[m1, m2])
    mgr = MagicMock()
    p = _patch_phases(mgr)
    with p[0], p[1], p[2], p[3], p[4], p[5]:
        reflow.topology(server)

    assert mgr.mock_calls == [
        call.apply_hierarchy(server),
        call.apply_visibility(server),
        call.apply_tree(server),
        call.reconcile(server, m1),
        call.reconcile(server, m2),
        call.focus_reconcile(server),
        call.publish(server),
    ]


def test_topology_skips_publish_without_ext_workspace():
    """Topology reflow skips publish when no ext-workspace manager is bound."""
    server = make_server(monitors=[make_monitor()], ext_workspace=None)
    mgr = MagicMock()
    p = _patch_phases(mgr)
    with p[0], p[1], p[2], p[3], p[4], p[5]:
        reflow.topology(server)

    assert call.publish(server) not in mgr.mock_calls


def test_outputs_arranges_all_monitors():
    """An output-layout reflow arranges every connected screen."""
    m1 = MagicMock(name="m1", fullscreen=None)
    m2 = MagicMock(name="m2", fullscreen=None)
    server = make_server(monitors=[m1, m2])

    with patch("welpy.geometry.reconcile") as apply_geom:
        reflow.outputs(server)

    assert apply_geom.call_args_list == [call(server, m1), call(server, m2)]


def test_outputs_no_monitors():
    """With no screens connected, no per-screen geometry runs."""
    server = make_server()

    with patch("welpy.geometry.reconcile") as apply_geom:
        reflow.outputs(server)

    apply_geom.assert_not_called()


def test_outputs_resizes_lock_surfaces():
    """Output layout changes resize and move active lock surfaces."""
    monitor = make_monitor()
    lock_surface = MagicMock(name="lock_surface")
    scene_tree = MagicMock(name="scene_tree")
    ls = model.LockSurface(
        lock_surface=lock_surface, monitor=monitor,
        scene_tree=scene_tree, listeners=[])
    server = make_server(
        monitors=[monitor], active_monitor=monitor, locked=True,
        session_lock=make_session_lock(surfaces=[ls]))
    server.ffi.addressof.side_effect = lambda obj, *args: ("ADDR", obj, *args)

    with patch("welpy.geometry.apply_hierarchy"), \
         patch("welpy.geometry.apply_visibility"), \
         patch("welpy.geometry.apply_tree"), \
         patch("welpy.geometry.arrange_layers"), \
         patch("welpy.geometry.reconcile"), \
         patch("welpy.focus.reconcile"), \
         patch("welpy.geometry.monitor_box",
               return_value=layout.Rect(10, 20, 300, 200)):
        reflow.outputs(server)

    server.lib.wlr_scene_node_set_position.assert_any_call(
        ("ADDR", scene_tree.node), 10, 20)
    server.lib.wlr_session_lock_surface_v1_configure.assert_called_once_with(
        lock_surface, 300, 200)


def test_outputs_prunes_stale_lock_surface():
    """A removed screen drops its stale lock surface from lock state."""
    removed = make_monitor()
    remaining = make_monitor()
    ls = model.LockSurface(
        lock_surface=MagicMock(), monitor=removed,
        scene_tree=MagicMock(), listeners=[])
    session_lock = make_session_lock(surfaces=[ls])
    server = make_server(
        monitors=[remaining], active_monitor=remaining, locked=True,
        session_lock=session_lock)

    with patch("welpy.geometry.apply_hierarchy"), \
         patch("welpy.geometry.apply_visibility"), \
         patch("welpy.geometry.apply_tree"), \
         patch("welpy.geometry.arrange_layers"), \
         patch("welpy.geometry.reconcile"), \
         patch("welpy.focus.reconcile"):
        reflow.outputs(server)

    assert ls not in session_lock.surfaces

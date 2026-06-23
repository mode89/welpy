"""Unit tests for welpy.geometry: window sizing and placement, the tiling and
layer-shell arrangement, and the per-window geometry queries."""

from unittest.mock import MagicMock, call, patch

from welpy import geometry, layout, model
from tests.helpers import (
    make_server, make_client, make_x11_client, make_monitor, make_workspace,
    flat_tree, trigger, make_layer_surface,
)


def test_apply_geometry_single_full():
    """One tiled window fills the whole window area."""
    m = make_monitor(window_area=layout.Rect(0, 0, 800, 600))
    m.active_workspace = make_workspace(monitor=m)
    a = make_client(workspace=m.active_workspace)
    m.active_workspace.root = flat_tree(a)
    server = make_server(clients=[a])

    with patch("welpy.geometry.resize_client") as resize:
        geometry.apply_geometry(server, m)

    resize.assert_called_once_with(server, a, layout.Rect(0, 0, 800, 600))


def test_apply_geometry_row():
    """Three tiled windows in a HORIZONTAL container split the width into three
    equal columns spanning the full height, summing exactly to the area."""
    m = make_monitor(window_area=layout.Rect(0, 0, 800, 600))
    m.active_workspace = make_workspace(monitor=m)
    a = make_client(workspace=m.active_workspace)
    b = make_client(workspace=m.active_workspace)
    c = make_client(workspace=m.active_workspace)
    m.active_workspace.root = flat_tree(a, b, c)
    server = make_server(clients=[a, b, c])

    with patch("welpy.geometry.resize_client") as resize:
        geometry.apply_geometry(server, m)

    assert resize.call_args_list == [
        call(server, a, layout.Rect(0, 0, 266, 600)),
        call(server, b, layout.Rect(266, 0, 267, 600)),
        call(server, c, layout.Rect(533, 0, 267, 600)),
    ]


def test_apply_geometry_other_monitor():
    """apply_geometry only touches clients visible on its monitor."""
    m1 = make_monitor(window_area=layout.Rect(0, 0, 800, 600))
    m1.active_workspace = make_workspace(monitor=m1)
    m2 = make_monitor(window_area=layout.Rect(0, 0, 800, 600))
    m2.active_workspace = make_workspace(monitor=m2)
    a = make_client(workspace=m1.active_workspace)
    b = make_client(workspace=m2.active_workspace)
    m1.active_workspace.root = flat_tree(a)
    m2.active_workspace.root = flat_tree(b)
    server = make_server(clients=[a, b])

    with patch("welpy.geometry.resize_client") as resize:
        geometry.apply_geometry(server, m1)

    resize.assert_called_once_with(server, a, layout.Rect(0, 0, 800, 600))


def test_apply_geometry_skips_floating():
    """Floating windows aren't in the tree; the tile path covers tiles only,
    then floats get their own rect."""
    m = make_monitor(window_area=layout.Rect(0, 0, 800, 600))
    m.active_workspace = make_workspace(monitor=m)
    a = make_client(
        workspace=m.active_workspace,
        floating_geom=layout.Rect(50, 60, 100, 80),
    )
    b = make_client(workspace=m.active_workspace)
    m.active_workspace.root = flat_tree(b)
    server = make_server(clients=[a, b])

    with patch("welpy.geometry.resize_client") as resize:
        geometry.apply_geometry(server, m)

    # The tile gets the full window area; the float gets its own rect.
    assert resize.call_args_list == [
        call(server, b, layout.Rect(0, 0, 800, 600)),
        call(server, a, layout.Rect(50, 60, 100, 80)),
    ]


def test_apply_geometry_sizes_fullscreen():
    """A fullscreen window is sized to the monitor box; the windows hidden
    behind it are left untouched until fullscreen exits."""
    m = make_monitor(window_area=layout.Rect(0, 0, 800, 600))
    m.active_workspace = make_workspace(monitor=m)
    fs = make_client(workspace=m.active_workspace)
    m.active_workspace.fullscreen = fs
    tile = make_client(workspace=m.active_workspace)
    m.active_workspace.root = flat_tree(fs, tile)
    server = make_server(clients=[fs, tile])

    with patch("welpy.geometry.monitor_box",
               return_value=layout.Rect(0, 0, 800, 600)), \
         patch("welpy.geometry.resize_client") as resize:
        geometry.apply_geometry(server, m)

    resize.assert_called_once_with(server, fs, layout.Rect(0, 0, 800, 600))


def test_apply_geometry_empty():
    """With no tile/fullscreen clients on the monitor, apply_geometry
    does nothing."""
    server = make_server()
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)

    with patch("welpy.geometry.resize_client") as resize:
        geometry.apply_geometry(server, m)

    resize.assert_not_called()


def test_apply_geometry_reconciles_float():
    """A floating client is resized to its floating_geom on every
    apply_geometry, so any drift between wlroots state and dataclass
    state converges."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    saved = layout.Rect(10, 20, 300, 200)
    c = make_client(workspace=m.active_workspace, floating_geom=saved)
    server = make_server(clients=[c])

    with patch("welpy.geometry.resize_client") as resize:
        geometry.apply_geometry(server, m)

    resize.assert_called_once_with(server, c, saved)
    assert c.floating_geom == saved


def test_hierarchy_seed():
    """A monitor with no workspaces gets seeded with the first orphan,
    becoming the active monitor."""
    monitor = make_monitor()
    server = make_server(
        workspaces=[make_workspace(name="1"), make_workspace(name="2")],
        monitors=[monitor])

    geometry.apply_hierarchy(server)

    assert server.workspaces[0].monitor is monitor
    assert monitor.active_workspace is server.workspaces[0]
    assert server.active_monitor is monitor
    assert server.workspaces[1].monitor is None


def test_hierarchy_hotplug():
    """When a new monitor joins an existing layout, it stays empty -- no
    orphan is auto-claimed."""
    first = make_monitor()
    ws = make_workspace(name="1", monitor=first)
    first.active_workspace = ws
    second = make_monitor()
    server = make_server(
        workspaces=[ws], monitors=[first, second], active_monitor=first)

    geometry.apply_hierarchy(server)

    assert second.active_workspace is None
    assert server.active_monitor is first


def test_hierarchy_unplug_migrate():
    """When a monitor is removed, non-empty workspaces migrate to the
    active monitor."""
    gone = make_monitor()
    survivor = make_monitor()
    ws_gone = make_workspace(name="1", monitor=gone)
    ws_surv = make_workspace(name="2", monitor=survivor)
    survivor.active_workspace = ws_surv
    server = make_server(
        workspaces=[ws_gone, ws_surv], monitors=[survivor],
        active_monitor=survivor, clients=[make_client(workspace=ws_gone)])

    geometry.apply_hierarchy(server)

    assert ws_gone.monitor is survivor


def test_hierarchy_rehome_occupied():
    """After every monitor briefly vanished (e.g. a VT switch) orphaned all
    workspaces, a returning monitor re-homes the occupied ones so the bar
    sees them again, not just the seeded active workspace."""
    monitor = make_monitor()
    ws_active = make_workspace(name="1", monitor=None)
    ws_occupied = make_workspace(name="2", monitor=None)
    ws_empty = make_workspace(name="3", monitor=None)
    server = make_server(
        workspaces=[ws_active, ws_occupied, ws_empty], monitors=[monitor],
        clients=[make_client(workspace=ws_occupied)])

    geometry.apply_hierarchy(server)

    assert ws_occupied.monitor is monitor
    assert ws_empty.monitor is None


def test_hierarchy_unplug_orphan():
    """When a monitor is removed, empty workspaces on it are orphaned."""
    gone = make_monitor()
    survivor = make_monitor()
    ws_gone = make_workspace(name="1", monitor=gone)
    ws_surv = make_workspace(name="2", monitor=survivor)
    survivor.active_workspace = ws_surv
    server = make_server(
        workspaces=[ws_gone, ws_surv], monitors=[survivor],
        active_monitor=survivor)

    geometry.apply_hierarchy(server)

    assert ws_gone.monitor is None


def test_hierarchy_unplug_repoint():
    """When the active monitor is removed, active falls to a survivor."""
    gone = make_monitor()
    survivor = make_monitor()
    ws = make_workspace(name="1", monitor=survivor)
    survivor.active_workspace = ws
    server = make_server(
        workspaces=[ws], monitors=[survivor], active_monitor=gone)

    geometry.apply_hierarchy(server)

    assert server.active_monitor is survivor


def test_hierarchy_idempotent():
    """Calling apply_hierarchy twice produces the same state as once."""
    server = make_server(
        workspaces=[make_workspace(name="1"), make_workspace(name="2")],
        monitors=[make_monitor()])

    geometry.apply_hierarchy(server)
    snapshot = (
        server.active_monitor,
        [(w.name, w.monitor) for w in server.workspaces],
        [(m, m.active_workspace) for m in server.monitors])
    geometry.apply_hierarchy(server)

    assert snapshot == (
        server.active_monitor,
        [(w.name, w.monitor) for w in server.workspaces],
        [(m, m.active_workspace) for m in server.monitors])


def test_hierarchy_fullscreen_unmapped():
    """A fullscreen pointer to a client that's no longer mapped is cleared."""
    monitor = make_monitor()
    ws = make_workspace(name="1", monitor=monitor)
    monitor.active_workspace = ws
    server = make_server(
        workspaces=[ws], monitors=[monitor], active_monitor=monitor)
    ghost = make_client(workspace=ws)
    ws.fullscreen = ghost  # not in server.clients

    geometry.apply_hierarchy(server)

    assert ws.fullscreen is None


def test_hierarchy_fullscreen_mismatch():
    """A fullscreen pointer to a client on a different workspace is cleared."""
    monitor = make_monitor()
    active = make_workspace(name="1", monitor=monitor)
    other = make_workspace(name="2")
    monitor.active_workspace = active
    client = make_client(workspace=other)
    server = make_server(
        workspaces=[active, other], monitors=[monitor],
        active_monitor=monitor, clients=[client])
    active.fullscreen = client

    geometry.apply_hierarchy(server)

    assert active.fullscreen is None


def test_hierarchy_inactive_empty():
    """A non-active workspace with no clients is orphaned."""
    monitor = make_monitor()
    active = make_workspace(name="1", monitor=monitor)
    inactive = make_workspace(name="2", monitor=monitor)
    monitor.active_workspace = active
    server = make_server(
        workspaces=[active, inactive], monitors=[monitor],
        active_monitor=monitor)

    geometry.apply_hierarchy(server)

    assert inactive.monitor is None


def test_hierarchy_inactive_kept():
    """A non-active workspace with clients stays assigned."""
    monitor = make_monitor()
    active = make_workspace(name="1", monitor=monitor)
    inactive = make_workspace(name="2", monitor=monitor)
    monitor.active_workspace = active
    server = make_server(
        workspaces=[active, inactive], monitors=[monitor],
        active_monitor=monitor, clients=[make_client(workspace=inactive)])

    geometry.apply_hierarchy(server)

    assert inactive.monitor is monitor


def test_hierarchy_no_monitors():
    """Without monitors, all workspaces are orphaned and active is None."""
    ws = make_workspace(name="1", monitor=MagicMock())
    server = make_server(workspaces=[ws], active_monitor=MagicMock())

    geometry.apply_hierarchy(server)

    assert ws.monitor is None
    assert server.active_monitor is None


def test_hierarchy_active_repair():
    """A monitor whose active_workspace lives elsewhere gets repointed."""
    monitor = make_monitor()
    on_monitor = make_workspace(name="1", monitor=monitor)
    elsewhere = make_workspace(name="2")
    monitor.active_workspace = elsewhere  # not on monitor
    server = make_server(
        workspaces=[on_monitor, elsewhere], monitors=[monitor],
        active_monitor=monitor)

    geometry.apply_hierarchy(server)

    assert monitor.active_workspace is on_monitor


def test_apply_visibility_active():
    """Clients on a monitor's active workspace have their scene nodes
    enabled."""
    monitor = make_monitor()
    ws = make_workspace(name="1", monitor=monitor)
    monitor.active_workspace = ws
    client = make_client(workspace=ws)
    server = make_server(
        workspaces=[ws], monitors=[monitor], clients=[client])

    geometry.apply_visibility(server)

    server.lib.wlr_scene_node_set_enabled.assert_called_with(
        server.ffi.addressof.return_value, True)


def test_apply_visibility_inactive():
    """Clients on a non-active workspace have their scene nodes disabled."""
    monitor = make_monitor()
    active = make_workspace(name="1", monitor=monitor)
    inactive = make_workspace(name="2", monitor=monitor)
    monitor.active_workspace = active
    client = make_client(workspace=inactive)
    server = make_server(
        workspaces=[active, inactive], monitors=[monitor], clients=[client])

    geometry.apply_visibility(server)

    server.lib.wlr_scene_node_set_enabled.assert_called_with(
        server.ffi.addressof.return_value, False)


def test_apply_visibility_orphan():
    """Clients on an orphaned workspace are hidden."""
    ws = make_workspace(name="1")
    client = make_client(workspace=ws)
    server = make_server(workspaces=[ws], clients=[client])

    geometry.apply_visibility(server)

    server.lib.wlr_scene_node_set_enabled.assert_called_with(
        server.ffi.addressof.return_value, False)


def test_apply_tree_clients():
    """Each client's scene node is reparented to its layer's tree."""
    a = make_client()
    b = make_client(floating_geom=layout.Rect(0, 0, 100, 100))
    server = make_server(clients=[a, b])

    geometry.apply_tree(server)

    node = server.ffi.addressof.return_value
    server.lib.wlr_scene_node_reparent.assert_any_call(
        node, server.layers[model.Layer.TILE])
    server.lib.wlr_scene_node_reparent.assert_any_call(
        node, server.layers[model.Layer.FLOAT])


def test_apply_tree_skips_unmapped():
    """A client without a scene_tree is between create and map -- skipped."""
    client = make_client(scene_tree=None)
    server = make_server(clients=[client])

    geometry.apply_tree(server)

    server.lib.wlr_scene_node_reparent.assert_not_called()


def test_apply_tree_idempotent():
    """When every node is already under the right parent, nothing is
    reparented."""
    client = make_client()
    server = make_server(clients=[client])
    server.ffi.addressof.return_value.parent = server.layers[model.Layer.TILE]

    geometry.apply_tree(server)

    server.lib.wlr_scene_node_reparent.assert_not_called()


def test_apply_tree_layer_surface():
    """A layer surface in monitor.layers[TOP] is parented under the TOP
    tree; its popups tree follows."""
    monitor = MagicMock(name="m", fullscreen=None)
    monitor.layers = {layer: [] for layer in model.Layer}
    ls = MagicMock(name="ls")
    monitor.layers[model.Layer.TOP].append(ls)
    server = make_server(monitors=[monitor])

    geometry.apply_tree(server)

    node = server.ffi.addressof.return_value
    server.lib.wlr_scene_node_reparent.assert_any_call(
        node, server.layers[model.Layer.TOP])


def test_apply_tree_popups_lifted():
    """A layer surface in BACKGROUND has its popups tree lifted into TOP
    so a bar can't bury them."""
    monitor = MagicMock(name="m", fullscreen=None)
    monitor.layers = {layer: [] for layer in model.Layer}
    ls = MagicMock(name="ls")
    monitor.layers[model.Layer.BACKGROUND].append(ls)
    server = make_server(monitors=[monitor])

    geometry.apply_tree(server)

    node = server.ffi.addressof.return_value
    server.lib.wlr_scene_node_reparent.assert_any_call(
        node, server.layers[model.Layer.BACKGROUND])
    server.lib.wlr_scene_node_reparent.assert_any_call(
        node, server.layers[model.Layer.TOP])


def test_resize_client_geometry():
    """resize_client positions the wrapper subtree and configures the inner
    surface size shrunk by twice the border width, without touching
    tiled-edge state. The xdg subtree is also inset by the border so the
    surface doesn't render on top of the border rects."""
    server = make_server()
    borders = tuple(MagicMock(name=f"b{i}") for i in range(4))
    client = make_client(
        toplevel=MagicMock(), scene_tree=MagicMock(), borders=borders)

    geometry.resize_client(server, client, layout.Rect(10, 20, 300, 400))

    server.lib.wlr_scene_node_set_position.assert_any_call(
        server.ffi.addressof.return_value, 10, 20)
    bw = model.BORDER_WIDTH
    server.lib.wlr_scene_node_set_position.assert_any_call(
        server.ffi.addressof.return_value, bw, bw)
    server.lib.wlr_xdg_toplevel_set_size.assert_called_once_with(
        client.toplevel, 300 - 2 * bw, 400 - 2 * bw)
    server.lib.wlr_xdg_toplevel_set_tiled.assert_not_called()


def test_resize_client_tracks():
    """resize_client records the configure serial so the screen waits for
    the client to render at the new size."""
    server = make_server()
    server.lib.wlr_xdg_toplevel_set_size.return_value = 42
    borders = tuple(MagicMock() for _ in range(4))
    client = make_client(scene_tree=MagicMock(), borders=borders)

    geometry.resize_client(server, client, layout.Rect(10, 20, 300, 400))

    assert client.pending_serial == 42


def test_resize_client_clips():
    """resize_client clips the xdg subtree to the inner area, anchored at
    the surface's xdg geometry offset so CSD shadow margins are skipped."""
    server = make_server()
    toplevel = MagicMock()
    toplevel.base.geometry.x = 12
    toplevel.base.geometry.y = 34
    client = make_client(
        toplevel=toplevel, scene_tree=MagicMock(),
        borders=tuple(MagicMock() for _ in range(4)))

    geometry.resize_client(server, client, layout.Rect(10, 20, 300, 400))

    bw = model.BORDER_WIDTH
    server.ffi.new.assert_any_call(
        "struct wlr_box *", [12, 34, 300 - 2 * bw, 400 - 2 * bw])
    server.lib.wlr_scene_subsurface_tree_set_clip.assert_called_once_with(
        server.ffi.addressof.return_value, server.ffi.new.return_value)


def test_resize_client_fullscreen():
    """Resizing a fullscreen window leaves no room for borders: the inner
    surface fills the rect, the xdg subtree sits flush at (0, 0), and the
    border rects collapse to zero size."""
    server = make_server()
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    client = make_client(
        workspace=m.active_workspace,
        borders=tuple(MagicMock(name=f"b{i}") for i in range(4)),
    )
    m.active_workspace.fullscreen = client

    geometry.resize_client(server, client, layout.Rect(0, 0, 800, 600))

    server.lib.wlr_xdg_toplevel_set_size.assert_called_once_with(
        client.toplevel, 800, 600)
    # The xdg subtree is repositioned flush against the wrapper origin.
    server.lib.wlr_scene_node_set_position.assert_any_call(
        server.ffi.addressof.return_value, 0, 0)
    # All four border rects sized so they cover nothing.
    top, bottom, left, right = client.borders
    server.lib.wlr_scene_rect_set_size.assert_any_call(top, 800, 0)
    server.lib.wlr_scene_rect_set_size.assert_any_call(bottom, 800, 0)
    server.lib.wlr_scene_rect_set_size.assert_any_call(left, 0, 600)
    server.lib.wlr_scene_rect_set_size.assert_any_call(right, 0, 600)


def test_borders_resize():
    """resize_client frames the wrapper with edge rects whose sizes match
    the outer rect on the long axis and the border width on the short one."""
    server = make_server()
    top, bottom, left, right = (
        MagicMock(name="top"), MagicMock(name="bottom"),
        MagicMock(name="left"), MagicMock(name="right"))
    client = make_client(
        toplevel=MagicMock(), scene_tree=MagicMock(),
        borders=(top, bottom, left, right))

    geometry.resize_client(server, client, layout.Rect(10, 20, 300, 400))

    bw = model.BORDER_WIDTH
    sizes = server.lib.wlr_scene_rect_set_size.call_args_list
    assert sizes == [
        call(top, 300, bw),
        call(bottom, 300, bw),
        call(left, bw, 400 - 2 * bw),
        call(right, bw, 400 - 2 * bw),
    ]


def test_set_size_tracks():
    """set_size sends the configure and records the returned serial."""
    server = make_server()
    server.lib.wlr_xdg_toplevel_set_size.return_value = 9
    client = make_client(scene_tree=MagicMock())

    geometry.set_size(server, client, 100, 200)

    server.lib.wlr_xdg_toplevel_set_size.assert_called_once_with(
        client.toplevel, 100, 200)
    assert client.pending_serial == 9


def test_xwayland_set_size():
    """X11 sizing couples position and size, so set_size sends an absolute
    configure derived from the already-placed wrapper plus the border inset."""
    server = make_server()
    client = make_x11_client(inner_size=None)
    client.scene_tree.node.x = 100
    client.scene_tree.node.y = 50

    geometry.set_size(server, client, 200, 150)

    server.lib.wlr_xwayland_surface_configure.assert_called_once_with(
        client.xsurface,
        100 + model.BORDER_WIDTH, 50 + model.BORDER_WIDTH, 200, 150)


def test_xwayland_position_only():
    """An X11 move with unchanged size still sends ConfigureNotify because
    X11 couples the window position and size in one configure."""
    server = make_server()
    client = make_x11_client(inner_size=(200, 150))
    client.scene_tree.node.x = 300
    client.scene_tree.node.y = 400

    geometry.set_size(server, client, 200, 150)

    server.lib.wlr_xwayland_surface_configure.assert_called_once_with(
        client.xsurface,
        300 + model.BORDER_WIDTH, 400 + model.BORDER_WIDTH, 200, 150)


def test_xwayland_size_unchanged_skips():
    """Re-applying the geometry an X11 window already has sends no configure,
    so repeated layouts don't spam the client with redundant ConfigureNotify."""
    server = make_server()
    client = make_x11_client(inner_size=(200, 150))
    client.scene_tree.node.x = 100
    client.scene_tree.node.y = 50
    client.xsurface.x = 100 + model.BORDER_WIDTH
    client.xsurface.y = 50 + model.BORDER_WIDTH
    client.xsurface.width = 200
    client.xsurface.height = 150

    geometry.set_size(server, client, 200, 150)

    server.lib.wlr_xwayland_surface_configure.assert_not_called()


def test_set_tiled_tracks():
    """set_tiled sends the configure and records the returned serial."""
    server = make_server()
    server.lib.wlr_xdg_toplevel_set_tiled.return_value = 6
    client = make_client(scene_tree=MagicMock())

    geometry.set_tiled(server, client, 15)

    server.lib.wlr_xdg_toplevel_set_tiled.assert_called_once_with(
        client.toplevel, 15)
    assert client.pending_serial == 6


def test_xwayland_set_tiled():
    """X11 has no tiled-edge state, so set_tiled does nothing."""
    server = make_server()
    client = make_x11_client()
    geometry.set_tiled(server, client, 15)
    server.lib.wlr_xdg_toplevel_set_tiled.assert_not_called()


def test_fullscreen_slot_enters():
    """Assigning a client to its workspace's fullscreen slot notifies the
    app so its xdg state matches."""
    server = make_server()
    workspace = make_workspace()
    client = make_client(
        toplevel=MagicMock(),
        scene_tree=MagicMock(),
        workspace=workspace,
    )

    geometry.set_fullscreen(server, workspace, client)

    assert workspace.fullscreen is client
    server.lib.wlr_xdg_toplevel_set_fullscreen.assert_called_once_with(
        client.toplevel, True)


def test_fullscreen_slot_exits():
    """Clearing the slot notifies the previously-fullscreen app."""
    server = make_server()
    client = make_client(toplevel=MagicMock(), scene_tree=MagicMock())
    workspace = make_workspace(fullscreen=client)
    client.workspace = workspace

    geometry.set_fullscreen(server, workspace, None)

    assert workspace.fullscreen is None
    server.lib.wlr_xdg_toplevel_set_fullscreen.assert_called_once_with(
        client.toplevel, False)


def test_fullscreen_slot_noop():
    """Setting the slot to its current value is a no-op so no spurious
    configure goes out."""
    server = make_server()
    client = make_client(toplevel=MagicMock(), scene_tree=MagicMock())
    workspace = make_workspace(fullscreen=client)
    client.workspace = workspace

    geometry.set_fullscreen(server, workspace, client)

    server.lib.wlr_xdg_toplevel_set_fullscreen.assert_not_called()


def test_fullscreen_slot_replaces():
    """Replacing one fullscreen client with another notifies both: the
    outgoing one exits, the incoming one enters."""
    server = make_server()
    outgoing = make_client(
        toplevel=MagicMock(name="out"),
        scene_tree=MagicMock(),
    )
    incoming = make_client(
        toplevel=MagicMock(name="in"),
        scene_tree=MagicMock(),
    )
    workspace = make_workspace(fullscreen=outgoing)
    outgoing.workspace = workspace
    incoming.workspace = workspace

    geometry.set_fullscreen(server, workspace, incoming)

    assert workspace.fullscreen is incoming
    assert server.lib.wlr_xdg_toplevel_set_fullscreen.call_args_list == [
        call(outgoing.toplevel, False),
        call(incoming.toplevel, True),
    ]


def test_fullscreen_slot_keeps_float():
    """Entering and exiting fullscreen leaves floating_geom untouched, so a
    window that was floating before going fullscreen returns to floating
    at the same rect; a window that was tiled stays tiled."""
    server = make_server()
    workspace = make_workspace()
    saved = layout.Rect(50, 60, 304, 204)
    floater = make_client(
        toplevel=MagicMock(),
        scene_tree=MagicMock(),
        workspace=workspace,
        floating_geom=saved,
    )
    tiler = make_client(
        toplevel=MagicMock(),
        scene_tree=MagicMock(),
        workspace=workspace,
    )

    geometry.set_fullscreen(server, workspace, floater)
    geometry.set_fullscreen(server, workspace, None)
    assert floater.floating_geom == saved

    geometry.set_fullscreen(server, workspace, tiler)
    geometry.set_fullscreen(server, workspace, None)
    assert tiler.floating_geom is None


def test_xwayland_set_fullscreen():
    """set_fullscreen routes to the X11 fullscreen call."""
    server = make_server()
    ws = make_workspace()
    client = make_x11_client(workspace=ws)
    geometry.set_fullscreen(server, ws, client)
    server.lib.wlr_xwayland_surface_set_fullscreen.assert_called_once_with(
        client.xsurface, True)


def test_set_activated_no_hold():
    """set_activated sends focus state without recording a resize hold."""
    server = make_server()
    server.lib.wlr_xdg_toplevel_set_activated.return_value = 4
    client = make_client(scene_tree=MagicMock())

    geometry.set_activated(server, client, True)

    server.lib.wlr_xdg_toplevel_set_activated.assert_called_once_with(
        client.toplevel, True)
    assert client.pending_serial is None


def test_xwayland_set_activated():
    """set_activated routes to the X11 activate call."""
    server = make_server()
    client = make_x11_client()
    geometry.set_activated(server, client, True)
    server.lib.wlr_xwayland_surface_activate.assert_called_once_with(
        client.xsurface, True)


def test_track_configure_acked():
    """An already-acked serial leaves pending cleared, so a no-op configure
    doesn't freeze the screen."""
    # pylint: disable=protected-access
    client = make_client(scene_tree=MagicMock())
    client.toplevel.base.current.configure_serial = 5

    geometry._track_configure(client, 5)
    assert client.pending_serial is None
    geometry._track_configure(client, 3)
    assert client.pending_serial is None


def test_track_configure_pending():
    """A serial the client hasn't reached yet is recorded as pending so the
    screen waits."""
    # pylint: disable=protected-access
    client = make_client(scene_tree=MagicMock())
    client.toplevel.base.current.configure_serial = 3

    geometry._track_configure(client, 7)

    assert client.pending_serial == 7


def test_arrange_layers_shrinks_area():
    """A surface with exclusive_zone > 0 reserves space; the monitor's
    window_area shrinks accordingly and tiles re-flow."""
    server = make_server()
    monitor = make_monitor(window_area=layout.Rect(0, 0, 800, 600))
    ls = make_layer_surface(monitor=monitor)
    ls.layer_surface.initialized = True
    ls.layer_surface.current.exclusive_zone = 30
    monitor.layers[model.Layer.TOP].append(ls)

    def make_box(_type, vals):
        box = MagicMock()
        box.x, box.y, box.width, box.height = vals
        return box
    server.ffi.new.side_effect = make_box
    # Mimic the wlroots helper shrinking `usable` to reflect the zone.
    def configure(_scene, _full, usable):
        usable.y = 30
        usable.height = 570
    server.lib.wlr_scene_layer_surface_v1_configure.side_effect = configure

    with patch(
            "welpy.geometry.monitor_box",
            return_value=layout.Rect(0, 0, 800, 600)):
        geometry.arrange_layers(server, monitor)

    assert monitor.window_area == layout.Rect(0, 30, 800, 570)


def test_monitor_box_returns_rect():
    """monitor_box reads the layout box and returns it as a Rect."""
    server = make_server()
    monitor = make_monitor(output="OUT", scene_output="SO")
    box = server.ffi.new.return_value
    box.x, box.y, box.width, box.height = 10, 20, 800, 600

    result = geometry.monitor_box(server, monitor)

    assert result == layout.Rect(10, 20, 800, 600)
    server.lib.wlr_output_layout_get_box.assert_called_once_with(
        server.output_layout, "OUT", box)


def _make_deco(server, client, *, initialized=True):
    """Stage a deco that decoration_new will resolve to `client` via
    ffi.from_handle (mirroring how the real handle round-trips)."""
    client.toplevel.base.initialized = initialized
    deco = MagicMock(name="deco")
    deco.toplevel = client.toplevel
    server.ffi.cast.return_value = deco
    server.ffi.from_handle.return_value = client
    return deco


def test_decoration_new_forces_ssd():
    """A decoration request from an already-initialized window flips it to
    server-side immediately."""
    client = make_client()
    server = make_server(clients=[client])
    deco = _make_deco(server, client)

    geometry.decoration_new(server, "DECO_DATA")

    assert client.decoration is deco
    server.lib.wlr_xdg_toplevel_decoration_v1_set_mode.assert_called_once_with(
        deco,
        server.lib.WLR_XDG_TOPLEVEL_DECORATION_V1_MODE_SERVER_SIDE)


def test_decoration_new_before_initialized():
    """A decoration request that arrives before the initial configure does
    not set the mode yet -- doing so would be a protocol error."""
    server = make_server()
    client = make_client(toplevel=MagicMock(), scene_tree=MagicMock())
    deco = _make_deco(server, client, initialized=False)

    geometry.decoration_new(server, "DECO_DATA")

    assert client.decoration is deco
    server.lib.wlr_xdg_toplevel_decoration_v1_set_mode.assert_not_called()


def test_decoration_new_no_back_pointer():
    """A decoration whose toplevel was never registered (no back-pointer)
    is silently ignored; nothing to attach state to."""
    server = make_server()
    deco = MagicMock(name="deco")
    deco.toplevel.base.data = server.ffi.NULL
    server.ffi.cast.return_value = deco

    geometry.decoration_new(server, "DECO_DATA")

    server.ffi.from_handle.assert_not_called()
    server.lib.wlr_xdg_toplevel_decoration_v1_set_mode.assert_not_called()


def test_decoration_request_mode_reasserts():
    """Re-emitting request_mode after initialization re-forces server-side
    so apps can't flip themselves back to client-side later."""
    client = make_client()
    server = make_server(clients=[client])
    deco = _make_deco(server, client)

    geometry.decoration_new(server, "DECO_DATA")
    server.lib.wlr_xdg_toplevel_decoration_v1_set_mode.reset_mock()
    trigger(server, server.lib.welpy_xdg_decoration_request_mode, "REQ_DATA")

    server.lib.wlr_xdg_toplevel_decoration_v1_set_mode.assert_called_once_with(
        deco,
        server.lib.WLR_XDG_TOPLEVEL_DECORATION_V1_MODE_SERVER_SIDE)


def test_decoration_destroy_clears():
    """When the app destroys its decoration object, our per-decoration
    listeners are detached and the client forgets it."""
    server = make_server()
    client = make_client(toplevel=MagicMock(), scene_tree=MagicMock())
    _make_deco(server, client)

    geometry.decoration_new(server, "DECO_DATA")
    attached = list(client.listeners)
    trigger(server, server.lib.welpy_xdg_decoration_destroy, "DESTROY_DATA")

    assert client.decoration is None
    assert not client.listeners
    for h in attached:
        h.remove.assert_called_once()


def test_apply_decoration_forces():
    """Every initialized window with a decoration is set to server-side."""
    a = make_client(decoration=MagicMock(name="deco_a"))
    a.toplevel.base.initialized = True
    b = make_client(decoration=MagicMock(name="deco_b"))
    b.toplevel.base.initialized = True
    server = make_server(clients=[a, b])

    geometry.apply_decoration(server)

    ssd = server.lib.WLR_XDG_TOPLEVEL_DECORATION_V1_MODE_SERVER_SIDE
    set_mode = server.lib.wlr_xdg_toplevel_decoration_v1_set_mode
    assert set_mode.call_count == 2
    set_mode.assert_any_call(a.decoration, ssd)
    set_mode.assert_any_call(b.decoration, ssd)


def test_apply_decoration_skips_uninitialized():
    """A decoration on a not-yet-initialized surface is skipped to avoid
    a protocol error."""
    client = make_client(decoration=MagicMock())
    client.toplevel.base.initialized = False
    server = make_server(clients=[client])

    geometry.apply_decoration(server)

    server.lib.wlr_xdg_toplevel_decoration_v1_set_mode.assert_not_called()


def test_apply_decoration_skips_no_decoration():
    """Windows without a decoration object are skipped (most apps)."""
    client = make_client(decoration=None)
    client.toplevel.base.initialized = True
    server = make_server(clients=[client])

    geometry.apply_decoration(server)

    server.lib.wlr_xdg_toplevel_decoration_v1_set_mode.assert_not_called()


def test_init_floating_geom_centers():
    """init_floating_geom centers the window in its screen's usable area at
    the size the app asked for plus border."""
    m = make_monitor(window_area=layout.Rect(100, 50, 800, 600))
    m.active_workspace = make_workspace(monitor=m)
    toplevel = MagicMock()
    toplevel.base.geometry.width = 400
    toplevel.base.geometry.height = 300
    client = make_client(toplevel=toplevel, workspace=m.active_workspace)

    outer_w = 400 + 2 * model.BORDER_WIDTH
    outer_h = 300 + 2 * model.BORDER_WIDTH
    assert geometry.init_floating_geom(client) == layout.Rect(
        100 + (800 - outer_w) // 2,
        50 + (600 - outer_h) // 2,
        outer_w, outer_h)


def test_init_floating_geom_fallback():
    """When the app commits with empty geometry, init_floating_geom picks
    a default size so the window isn't invisibly small."""
    m = make_monitor(window_area=layout.Rect(0, 0, 800, 600))
    m.active_workspace = make_workspace(monitor=m)
    toplevel = MagicMock()
    toplevel.base.geometry.width = 0
    toplevel.base.geometry.height = 0
    client = make_client(toplevel=toplevel, workspace=m.active_workspace)

    rect = geometry.init_floating_geom(client)

    assert rect.width == 250 + 2 * model.BORDER_WIDTH
    assert rect.height == 200 + 2 * model.BORDER_WIDTH


def test_client_layer_tile():
    """A client with no floating_geom and not pinned to any monitor's
    fullscreen slot is tiled."""
    client = make_client(toplevel=MagicMock(), scene_tree=MagicMock())
    assert geometry.client_layer(client) == model.Layer.TILE


def test_client_layer_float():
    """A client with a floating_geom is floating."""
    client = make_client(
        toplevel=MagicMock(),
        scene_tree=MagicMock(),
        floating_geom=layout.Rect(0, 0, 100, 100),
    )
    assert geometry.client_layer(client) == model.Layer.FLOAT


def test_client_layer_fullscreen():
    """A client occupying its workspace's fullscreen slot is fullscreen,
    even if it also has a floating_geom stashed for restore."""
    client = make_client(
        toplevel=MagicMock(),
        scene_tree=MagicMock(),
        floating_geom=layout.Rect(0, 0, 100, 100),
    )
    client.workspace = make_workspace(fullscreen=client)
    assert geometry.client_layer(client) == model.Layer.FULLSCREEN


def test_xwayland_client_geometry():
    """X11 windows have no CSD offset; geometry is the raw size at (0, 0)."""
    client = make_x11_client()
    client.xsurface.width = 640
    client.xsurface.height = 480

    geom = geometry.client_geometry(client)

    assert (geom.x, geom.y, geom.width, geom.height) == (0, 0, 640, 480)


def test_xwayland_client_surface():
    """client_surface unwraps the X11 surface to its inner wl_surface."""
    client = make_x11_client()
    assert geometry.client_surface(client) is client.xsurface.surface


def test_xwayland_wants_fullscreen():
    """client_wants_fullscreen reads the X11 surface's fullscreen flag."""
    client = make_x11_client()
    client.xsurface.fullscreen = True
    assert geometry.client_wants_fullscreen(client) is True


def test_xwayland_wants_float():
    """A transient X11 window (one with a parent) opens floating."""
    client = make_x11_client()
    client.xsurface.parent = MagicMock()
    assert geometry.client_wants_float(client) is True

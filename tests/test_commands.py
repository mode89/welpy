"""Unit tests for welpy.commands: directional focus and window movement,
grouping/layout flips, fullscreen/float toggles, closing the focused window,
and workspace switching/relocation."""

from unittest.mock import patch

from welpy import app as wel, commands, focus, geometry, layout
from tests.helpers import (
    make_server, make_client, make_x11_client, make_monitor, make_workspace,
    flat_tree,
)


def test_focus_direction_moves():
    """Directional focus shifts to the structurally adjacent tiled window: from
    the left column of a three-column row, RIGHT lands on the middle one."""
    # pylint: disable=duplicate-code
    m = make_monitor(window_area=wel.Rect(0, 0, 900, 600))
    m.active_workspace = make_workspace(monitor=m)
    a = make_client(workspace=m.active_workspace)
    b = make_client(workspace=m.active_workspace)
    c = make_client(workspace=m.active_workspace)
    m.active_workspace.root = flat_tree(a, b, c)
    server = make_server(monitors=[m], active_monitor=m, clients=[a, b, c])
    focus.focus_client(server, a)

    with patch("welpy.focus.apply_focus"), \
         patch("welpy.focus.focus_client") as focus_client:
        commands.focus_direction(server, layout.Direction.RIGHT)

    focus_client.assert_called_once_with(server, b)


def test_focus_direction_edge():
    """Directional focus is a no-op at an edge: nothing lies right of the
    rightmost window."""
    m = make_monitor(window_area=wel.Rect(0, 0, 900, 600))
    m.active_workspace = make_workspace(monitor=m)
    a = make_client(workspace=m.active_workspace)
    b = make_client(workspace=m.active_workspace)
    m.active_workspace.root = flat_tree(a, b)
    server = make_server(monitors=[m], active_monitor=m, clients=[a, b])
    focus.focus_client(server, b)

    with patch("welpy.focus.apply_focus"), \
         patch("welpy.focus.focus_client") as focus_client:
        commands.focus_direction(server, layout.Direction.RIGHT)

    focus_client.assert_not_called()


def test_focus_direction_fullscreen():
    """Directional focus is inert while a fullscreen window owns the screen."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    a = make_client(workspace=m.active_workspace)
    b = make_client(workspace=m.active_workspace)
    m.active_workspace.root = flat_tree(a, b)
    m.active_workspace.fullscreen = a
    server = make_server(monitors=[m], active_monitor=m, clients=[a, b])
    focus.focus_client(server, a)

    with patch("welpy.focus.apply_focus"), \
         patch("welpy.focus.focus_client") as focus_client:
        commands.focus_direction(server, layout.Direction.RIGHT)

    focus_client.assert_not_called()


def test_focus_direction_floating():
    """Directional focus is a no-op when the focused window is floating, since
    floats aren't tiled leaves."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    a = make_client(workspace=m.active_workspace)
    b = make_client(
        workspace=m.active_workspace,
        floating_geom=wel.Rect(0, 0, 100, 100),
    )
    m.active_workspace.root = flat_tree(a)
    server = make_server(monitors=[m], active_monitor=m, clients=[a, b])
    focus.focus_client(server, b)

    with patch("welpy.focus.apply_focus"), \
         patch("welpy.focus.focus_client") as focus_client:
        commands.focus_direction(server, layout.Direction.LEFT)

    focus_client.assert_not_called()


def test_focus_direction_group_mru():
    """Focusing into a neighboring group lands on its most-recently-focused
    window, regardless of where that window sits in the group."""
    m = make_monitor(window_area=wel.Rect(0, 0, 900, 600))
    m.active_workspace = make_workspace(monitor=m)
    a = make_client(focus_order=1, workspace=m.active_workspace)
    b = make_client(focus_order=2, workspace=m.active_workspace)
    c = make_client(focus_order=3, workspace=m.active_workspace)
    inner = layout.Container(layout.ContainerLayout.VERTICAL, [b, c])
    m.active_workspace.root = layout.Container(
        layout.ContainerLayout.HORIZONTAL, [a, inner])
    server = make_server(monitors=[m], active_monitor=m, clients=[a, b, c])
    focus.focus_client(server, a)

    with patch("welpy.focus.apply_focus"), \
         patch("welpy.focus.focus_client") as focus_client:
        commands.focus_direction(server, layout.Direction.RIGHT)

    focus_client.assert_called_once_with(server, c)


def test_move_direction_moves():
    """mod+shift relocates the focused window one slot that way: from the left
    of a three-column row, RIGHT reorders it past its neighbor."""
    # pylint: disable=duplicate-code
    m = make_monitor(window_area=wel.Rect(0, 0, 900, 600))
    m.active_workspace = make_workspace(monitor=m)
    a = make_client(workspace=m.active_workspace)
    b = make_client(workspace=m.active_workspace)
    c = make_client(workspace=m.active_workspace)
    m.active_workspace.root = flat_tree(a, b, c)
    server = make_server(monitors=[m], active_monitor=m, clients=[a, b, c])
    focus.focus_client(server, a)

    with patch("welpy.geometry.apply_geometry"), \
         patch("welpy.focus.apply_focus"):
        commands.move_direction(server, layout.Direction.RIGHT)

    assert m.active_workspace.root.children == [b, a, c]


def test_move_direction_edge():
    """Moving toward an edge is a no-op: nothing lies right of the rightmost
    window, so the tree is unchanged."""
    m = make_monitor(window_area=wel.Rect(0, 0, 900, 600))
    m.active_workspace = make_workspace(monitor=m)
    a = make_client(workspace=m.active_workspace)
    b = make_client(workspace=m.active_workspace)
    m.active_workspace.root = flat_tree(a, b)
    server = make_server(monitors=[m], active_monitor=m, clients=[a, b])
    focus.focus_client(server, b)

    with patch("welpy.geometry.apply_geometry"), \
         patch("welpy.focus.apply_focus"):
        commands.move_direction(server, layout.Direction.RIGHT)

    assert m.active_workspace.root.children == [a, b]


def test_move_direction_fullscreen():
    """Moving is inert while a fullscreen window owns the screen."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    a = make_client(workspace=m.active_workspace)
    b = make_client(workspace=m.active_workspace)
    m.active_workspace.root = flat_tree(a, b)
    m.active_workspace.fullscreen = a
    server = make_server(monitors=[m], active_monitor=m, clients=[a, b])
    focus.focus_client(server, a)

    with patch("welpy.geometry.apply_geometry"), \
         patch("welpy.focus.apply_focus"):
        commands.move_direction(server, layout.Direction.RIGHT)

    assert m.active_workspace.root.children == [a, b]


def test_move_direction_floating():
    """Moving is a no-op when the focused window is floating, since floats
    aren't tiled leaves."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    a = make_client(workspace=m.active_workspace)
    b = make_client(
        workspace=m.active_workspace,
        floating_geom=wel.Rect(0, 0, 100, 100),
    )
    m.active_workspace.root = flat_tree(a)
    server = make_server(monitors=[m], active_monitor=m, clients=[a, b])
    focus.focus_client(server, b)

    with patch("welpy.geometry.apply_geometry") as apply_geom, \
         patch("welpy.focus.apply_focus"):
        commands.move_direction(server, layout.Direction.LEFT)

    apply_geom.assert_not_called()
    assert m.active_workspace.root.children == [a]


def test_move_direction_vertical():
    """mod+shift+j relocates the focused window down a column, exercising the
    vertical move axis."""
    m = make_monitor(window_area=wel.Rect(0, 0, 600, 900))
    m.active_workspace = make_workspace(monitor=m)
    a = make_client(workspace=m.active_workspace)
    b = make_client(workspace=m.active_workspace)
    c = make_client(workspace=m.active_workspace)
    m.active_workspace.root = layout.Container(
        layout.ContainerLayout.VERTICAL, [a, b, c])
    server = make_server(monitors=[m], active_monitor=m, clients=[a, b, c])
    focus.focus_client(server, a)

    with patch("welpy.geometry.apply_geometry"), \
         patch("welpy.focus.apply_focus"):
        commands.move_direction(server, layout.Direction.DOWN)

    assert m.active_workspace.root.children == [b, a, c]


def test_group_window_wraps():
    """mod+v wraps a window that has a sibling in its own group, split along
    the window's long side (here VERTICAL)."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    a = make_client(workspace=m.active_workspace, inner_size=(400, 600))
    b = make_client(workspace=m.active_workspace)
    m.active_workspace.root = flat_tree(a, b)
    server = make_server(monitors=[m], active_monitor=m, clients=[a, b])
    focus.focus_client(server, a)

    with patch("welpy.geometry.apply_geometry"), \
         patch("welpy.focus.apply_focus"):
        commands.group_window(server)

    root = m.active_workspace.root
    assert isinstance(root.children[0], layout.Container)
    assert root.children[0].layout == layout.ContainerLayout.VERTICAL
    assert root.children[0].children == [a]
    assert root.children[1] is b


def test_group_window_alone():
    """mod+v is a no-op on a window with no siblings -- there's nothing to
    split it off from, so the tree is unchanged."""
    m = make_monitor(window_area=wel.Rect(0, 0, 800, 600))
    m.active_workspace = make_workspace(monitor=m)
    a = make_client(workspace=m.active_workspace)
    m.active_workspace.root = flat_tree(a)
    server = make_server(monitors=[m], active_monitor=m, clients=[a])
    focus.focus_client(server, a)

    with patch("welpy.geometry.apply_geometry"), \
         patch("welpy.focus.apply_focus"):
        commands.group_window(server)

    assert m.active_workspace.root.children == [a]


def test_group_window_nested():
    """mod+v wraps a window nested inside a sub-group too, as long as it has a
    sibling there; the rest of the tree is untouched."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    a = make_client(workspace=m.active_workspace, inner_size=(400, 300))
    b = make_client(workspace=m.active_workspace)
    c = make_client(workspace=m.active_workspace)
    column = layout.Container(layout.ContainerLayout.VERTICAL, [a, b])
    m.active_workspace.root = layout.Container(
        layout.ContainerLayout.HORIZONTAL, [column, c])
    server = make_server(monitors=[m], active_monitor=m, clients=[a, b, c])
    focus.focus_client(server, a)

    with patch("welpy.geometry.apply_geometry"), \
         patch("welpy.focus.apply_focus"):
        commands.group_window(server)

    assert isinstance(column.children[0], layout.Container)
    assert column.children[0].layout == layout.ContainerLayout.HORIZONTAL
    assert column.children[0].children == [a]
    assert column.children[1] is b
    assert m.active_workspace.root.children[1] is c


def test_cycle_layout_flips():
    """mod+e flips the focused window's container between a row and a column."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    a = make_client(workspace=m.active_workspace)
    b = make_client(workspace=m.active_workspace)
    m.active_workspace.root = flat_tree(a, b)
    server = make_server(monitors=[m], active_monitor=m, clients=[a, b])
    focus.focus_client(server, a)

    with patch("welpy.geometry.apply_geometry"), \
         patch("welpy.focus.apply_focus"):
        commands.cycle_layout(server)

    assert m.active_workspace.root.layout == layout.ContainerLayout.VERTICAL


def test_toggle_fullscreen_enters():
    """toggle_fullscreen on a tiled focused window pins it to the
    workspace's fullscreen slot."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    client = make_client(workspace=m.active_workspace)
    server = make_server(monitors=[m], active_monitor=m, clients=[client])
    focus.focus_client(server, client)

    with patch("welpy.geometry.apply_geometry"):
        commands.toggle_fullscreen(server)

    assert m.active_workspace.fullscreen is client


def test_toggle_fullscreen_to_tile():
    """toggle_fullscreen on a fullscreen window with no saved float
    geometry clears the slot; client becomes tiled again."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    client = make_client(workspace=m.active_workspace, focus_order=1)
    m.active_workspace.fullscreen = client
    server = make_server(monitors=[m], active_monitor=m, clients=[client])

    with patch("welpy.geometry.apply_geometry"):
        commands.toggle_fullscreen(server)

    assert m.active_workspace.fullscreen is None
    assert geometry.client_layer(client) == wel.Layer.TILE


def test_toggle_fullscreen_to_float():
    """toggle_fullscreen on a fullscreen window that was floating restores
    the float; floating_geom is preserved through fullscreen."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    saved = wel.Rect(10, 20, 300, 200)
    client = make_client(
        workspace=m.active_workspace, floating_geom=saved, focus_order=1)
    m.active_workspace.fullscreen = client
    server = make_server(monitors=[m], active_monitor=m, clients=[client])

    with patch("welpy.geometry.apply_geometry"):
        commands.toggle_fullscreen(server)

    assert m.active_workspace.fullscreen is None
    assert client.floating_geom == saved
    assert geometry.client_layer(client) == wel.Layer.FLOAT


def test_toggle_fullscreen_no_focus():
    """toggle_fullscreen with nothing focused is a no-op."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    server = make_server(monitors=[m], active_monitor=m)

    with patch("welpy.geometry.set_fullscreen") as sf:
        commands.toggle_fullscreen(server)

    sf.assert_not_called()


def test_toggle_floating_to_float():
    """toggle_floating on a tiled focused window seeds floating_geom from
    the current outer rect so the float starts where it tiled."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    client = make_client(workspace=m.active_workspace)
    server = make_server(monitors=[m], active_monitor=m, clients=[client])
    focus.focus_client(server, client)

    seed = wel.Rect(50, 60, 304, 204)
    with patch("welpy.geometry.client_outer_rect", return_value=seed), \
         patch("welpy.geometry.apply_geometry"):
        commands.toggle_floating(server)

    assert client.floating_geom == seed


def test_toggle_floating_to_tile():
    """toggle_floating on a floating focused window clears floating_geom so
    it re-tiles."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    client = make_client(
        workspace=m.active_workspace,
        floating_geom=wel.Rect(10, 20, 300, 200),
        focus_order=1,
    )
    server = make_server(monitors=[m], active_monitor=m, clients=[client])

    with patch("welpy.geometry.apply_geometry"):
        commands.toggle_floating(server)

    assert client.floating_geom is None


def test_toggle_floating_drops_leaf():
    """Floating a tiled window drops its leaf from the workspace tree."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    a = make_client(workspace=m.active_workspace)
    b = make_client(workspace=m.active_workspace)
    m.active_workspace.root = flat_tree(a, b)
    server = make_server(monitors=[m], active_monitor=m, clients=[a, b])
    focus.focus_client(server, a)

    seed = wel.Rect(0, 0, 100, 100)
    with patch("welpy.geometry.client_outer_rect", return_value=seed), \
         patch("welpy.geometry.apply_geometry"):
        commands.toggle_floating(server)

    assert m.active_workspace.root.children == [b]


def test_toggle_floating_adds_leaf():
    """Un-floating a window inserts its leaf next to the most-recent tile."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    tiled = make_client(workspace=m.active_workspace, focus_order=1)
    floater = make_client(
        workspace=m.active_workspace,
        floating_geom=wel.Rect(10, 20, 300, 200),
        focus_order=2,
    )
    m.active_workspace.root = flat_tree(tiled)
    server = make_server(
        monitors=[m], active_monitor=m, clients=[tiled, floater])

    with patch("welpy.geometry.apply_geometry"):
        commands.toggle_floating(server)

    assert m.active_workspace.root.children == [tiled, floater]


def test_toggle_floating_fullscreen_noop():
    """toggle_floating is a no-op while the focused window is fullscreen."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    client = make_client(workspace=m.active_workspace, focus_order=1)
    m.active_workspace.fullscreen = client
    server = make_server(monitors=[m], active_monitor=m, clients=[client])
    before = client.floating_geom

    with patch("welpy.geometry.apply_geometry") as apply_geom:
        commands.toggle_floating(server)

    assert client.floating_geom is before
    apply_geom.assert_not_called()


def test_toggle_floating_no_focus():
    """toggle_floating with nothing focused is a no-op."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    server = make_server(monitors=[m], active_monitor=m)

    with patch("welpy.geometry.apply_geometry") as apply_geom:
        commands.toggle_floating(server)

    apply_geom.assert_not_called()


def test_xwayland_close():
    """Closing the focused X11 window routes to the X11 close call."""
    server = make_server()
    client = make_x11_client()
    with patch("welpy.focus.top_client", return_value=client):
        commands.close_window(server)
    server.lib.wlr_xwayland_surface_close.assert_called_once_with(
        client.xsurface)


def test_close_window_xdg():
    """Closing a focused Wayland window still routes to xdg send_close."""
    server = make_server()
    client = make_client()
    with patch("welpy.focus.top_client", return_value=client):
        commands.close_window(server)
    server.lib.wlr_xdg_toplevel_send_close.assert_called_once_with(
        client.toplevel)


def test_view_workspace_activates():
    """view_workspace makes the target workspace active on its monitor and
    switches the active monitor."""
    m1 = make_monitor()
    m2 = make_monitor()
    ws1 = make_workspace(name="1", monitor=m1)
    ws2 = make_workspace(name="2", monitor=m2)
    m1.active_workspace = ws1
    m2.active_workspace = ws2
    server = make_server(
        workspaces=[ws1, ws2], monitors=[m1, m2], active_monitor=m1)

    commands.view_workspace(server, "2")

    assert m2.active_workspace is ws2
    assert server.active_monitor is m2


def test_view_workspace_adopts_orphan():
    """view_workspace on an orphaned workspace binds it to the active
    monitor before activating it."""
    monitor = make_monitor()
    ws1 = make_workspace(name="1", monitor=monitor)
    orphan = make_workspace(name="2")
    monitor.active_workspace = ws1
    server = make_server(
        workspaces=[ws1, orphan], monitors=[monitor], active_monitor=monitor)

    commands.view_workspace(server, "2")

    assert orphan.monitor is monitor
    assert monitor.active_workspace is orphan


def test_view_workspace_ends_grabs():
    """view_workspace clears any in-progress mouse grabs."""
    monitor = make_monitor()
    ws1 = make_workspace(name="1", monitor=monitor)
    ws2 = make_workspace(name="2")
    monitor.active_workspace = ws1
    client = make_client(workspace=ws1, grab=wel.Grab("move", 0, 0))
    server = make_server(
        workspaces=[ws1, ws2], monitors=[monitor], active_monitor=monitor,
        clients=[client])

    commands.view_workspace(server, "2")

    assert client.grab is None


def test_view_workspace_unknown():
    """view_workspace with an unknown name leaves state untouched."""
    # pylint: disable=duplicate-code
    monitor = make_monitor()
    ws = make_workspace(name="1", monitor=monitor)
    monitor.active_workspace = ws
    server = make_server(
        workspaces=[ws], monitors=[monitor], active_monitor=monitor)

    commands.view_workspace(server, "xyz")

    assert monitor.active_workspace is ws


def test_view_workspace_records_previous():
    """Switching workspaces remembers the one being left."""
    monitor = make_monitor()
    ws1 = make_workspace(name="1", monitor=monitor)
    ws2 = make_workspace(name="2")
    monitor.active_workspace = ws1
    server = make_server(
        workspaces=[ws1, ws2], monitors=[monitor], active_monitor=monitor)

    commands.view_workspace(server, "2")

    assert server.previous_workspace == "1"


def test_view_previous_switches_back():
    """view_previous_workspace returns to the last-viewed workspace."""
    monitor = make_monitor()
    ws1 = make_workspace(name="1", monitor=monitor)
    ws2 = make_workspace(name="2")
    monitor.active_workspace = ws1
    server = make_server(
        workspaces=[ws1, ws2], monitors=[monitor], active_monitor=monitor,
        previous_workspace=None)

    commands.view_workspace(server, "2")
    commands.view_previous_workspace(server)

    assert monitor.active_workspace is ws1


def test_view_previous_noop():
    """view_previous_workspace does nothing when there is no history."""
    monitor = make_monitor()
    ws = make_workspace(name="1", monitor=monitor)
    monitor.active_workspace = ws
    server = make_server(
        workspaces=[ws], monitors=[monitor], active_monitor=monitor,
        previous_workspace=None)

    commands.view_previous_workspace(server)

    assert monitor.active_workspace is ws


def test_view_workspace_outgoing():
    """Switching away from an empty workspace orphans it."""
    monitor = make_monitor()
    ws1 = make_workspace(name="1", monitor=monitor)
    ws2 = make_workspace(name="2")
    monitor.active_workspace = ws1
    server = make_server(
        workspaces=[ws1, ws2], monitors=[monitor], active_monitor=monitor)

    commands.view_workspace(server, "2")

    assert ws1.monitor is None


def test_move_client_reassigns():
    """move_client_to_workspace changes the focused client's workspace."""
    monitor = make_monitor()
    ws1 = make_workspace(name="1", monitor=monitor)
    ws2 = make_workspace(name="3")
    monitor.active_workspace = ws1
    client = make_client(workspace=ws1, focus_order=1)
    server = make_server(
        workspaces=[ws1, ws2], monitors=[monitor], active_monitor=monitor,
        clients=[client])

    commands.move_client_to_workspace(server, "3")

    assert client.workspace is ws2


def test_move_client_moves_leaf():
    """Moving a tiled window detaches its leaf from the source tree and
    attaches it to the target tree."""
    monitor = make_monitor()
    ws1 = make_workspace(name="1", monitor=monitor)
    ws2 = make_workspace(name="2")
    monitor.active_workspace = ws1
    client = make_client(workspace=ws1, focus_order=1)
    ws1.root = flat_tree(client)
    server = make_server(
        workspaces=[ws1, ws2], monitors=[monitor], active_monitor=monitor,
        clients=[client])

    with patch("welpy.geometry.apply_geometry"), \
         patch("welpy.focus.apply_focus"):
        commands.move_client_to_workspace(server, "2")

    assert ws1.root.children == []
    assert ws2.root.children == [client]


def test_move_client_adopts():
    """move_client_to_workspace adopts the target onto active_monitor
    if it was orphaned."""
    monitor = make_monitor()
    ws1 = make_workspace(name="1", monitor=monitor)
    orphan = make_workspace(name="4")
    monitor.active_workspace = ws1
    client = make_client(workspace=ws1, focus_order=1)
    server = make_server(
        workspaces=[ws1, orphan], monitors=[monitor], active_monitor=monitor,
        clients=[client])

    commands.move_client_to_workspace(server, "4")

    assert orphan.monitor is monitor


def test_move_client_fullscreen():
    """Moving the fullscreen client off its workspace clears that
    workspace's fullscreen pointer."""
    monitor = make_monitor()
    ws1 = make_workspace(name="1", monitor=monitor)
    ws2 = make_workspace(name="2")
    monitor.active_workspace = ws1
    client = make_client(workspace=ws1, focus_order=1)
    ws1.fullscreen = client
    server = make_server(
        workspaces=[ws1, ws2], monitors=[monitor], active_monitor=monitor,
        clients=[client])

    commands.move_client_to_workspace(server, "2")

    assert ws1.fullscreen is None


def test_move_client_fullscreen_notifies():
    """Moving a fullscreen client out of a workspace also tells the app
    it is no longer fullscreen."""
    monitor = make_monitor()
    ws1 = make_workspace(name="1", monitor=monitor)
    ws2 = make_workspace(name="2")
    monitor.active_workspace = ws1
    client = make_client(workspace=ws1, focus_order=1)
    ws1.fullscreen = client
    server = make_server(
        workspaces=[ws1, ws2], monitors=[monitor], active_monitor=monitor,
        clients=[client])

    commands.move_client_to_workspace(server, "2")

    server.lib.wlr_xdg_toplevel_set_fullscreen.assert_any_call(
        client.toplevel, False)


def test_move_client_target_fullscreen():
    """Moving a client into a workspace with a fullscreen client clears the
    old fullscreen state so the moved window is not buried."""
    monitor = make_monitor()
    ws1 = make_workspace(name="1", monitor=monitor)
    ws2 = make_workspace(name="2", monitor=monitor)
    monitor.active_workspace = ws1
    moved = make_client(workspace=ws1, focus_order=2)
    fullscreen = make_client(workspace=ws2, focus_order=1)
    ws2.fullscreen = fullscreen
    server = make_server(
        workspaces=[ws1, ws2], monitors=[monitor], active_monitor=monitor,
        clients=[moved, fullscreen])

    commands.move_client_to_workspace(server, "2")

    assert ws2.fullscreen is None
    server.lib.wlr_xdg_toplevel_set_fullscreen.assert_any_call(
        fullscreen.toplevel, False)


def test_move_workspace_next():
    """move_active_workspace_to_monitor(+1) migrates the active workspace
    to the next monitor and switches focus there."""
    m1 = make_monitor()
    m2 = make_monitor()
    ws1 = make_workspace(name="1", monitor=m1)
    ws2 = make_workspace(name="2", monitor=m2)
    m1.active_workspace = ws1
    m2.active_workspace = ws2
    server = make_server(
        workspaces=[ws1, ws2], monitors=[m1, m2], active_monitor=m1)

    commands.move_active_workspace_to_monitor(server, +1)

    assert ws1.monitor is m2
    assert m2.active_workspace is ws1
    assert server.active_monitor is m2


def test_move_workspace_wraps():
    """move_active_workspace_to_monitor wraps from the first monitor to
    the last with direction -1."""
    m1 = make_monitor()
    m2 = make_monitor()
    ws1 = make_workspace(name="1", monitor=m1)
    ws2 = make_workspace(name="2", monitor=m2)
    m1.active_workspace = ws1
    m2.active_workspace = ws2
    server = make_server(
        workspaces=[ws1, ws2], monitors=[m1, m2], active_monitor=m1)

    commands.move_active_workspace_to_monitor(server, -1)

    assert server.active_monitor is m2


def test_move_workspace_single():
    """move_active_workspace_to_monitor is a no-op with one monitor."""
    # pylint: disable=duplicate-code
    monitor = make_monitor()
    ws = make_workspace(name="1", monitor=monitor)
    monitor.active_workspace = ws
    server = make_server(
        workspaces=[ws], monitors=[monitor], active_monitor=monitor)

    commands.move_active_workspace_to_monitor(server, +1)

    assert server.active_monitor is monitor

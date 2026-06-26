"""Unit tests for welpy.windows: the xdg-shell window lifecycle (create, map,
unmap, fullscreen/maximize/activate requests) and transient popup placement."""

from unittest.mock import ANY, MagicMock, patch

from welpy import geometry, layout, model, windows
from tests.helpers import (
    make_server, make_client, make_x11_client, make_monitor, make_workspace,
    make_layer_surface, flat_tree, trigger,
)


def test_create_defers_registration():
    """client_new only attaches listeners; the client joins server.clients
    at map time so siblings don't reflow before the new window is ready."""
    server = make_server()

    windows.on_create(server, "TOPLEVEL_DATA")

    assert not server.clients
    server.lib.wlr_scene_tree_create.assert_not_called()


def test_create_routes_commit():
    """The window's surface commit signal drives client_commit so the
    initial configure path runs on first commit."""
    server = make_server()
    with patch("welpy.windows.on_commit") as committed:
        windows.on_create(server, "TOPLEVEL_DATA")
        trigger(server, server.lib.welpy_surface_commit, "COMMIT_DATA")
    committed.assert_called_once_with(server, ANY, "COMMIT_DATA")


def test_create_routes_map():
    """The window's surface map signal drives client_map so a window gets
    focused the moment it has something to show."""
    server = make_server()
    with patch("welpy.windows.on_map") as mapped:
        windows.on_create(server, "TOPLEVEL_DATA")
        trigger(server, server.lib.welpy_surface_map, "MAP_DATA")
    mapped.assert_called_once_with(server, ANY, "MAP_DATA")


def test_create_routes_unmap():
    """The window's surface unmap signal drives client_unmap so closing
    one window hands focus to another."""
    server = make_server()
    with patch("welpy.windows.on_unmap") as unmap:
        windows.on_create(server, "TOPLEVEL_DATA")
        trigger(server, server.lib.welpy_surface_unmap, "UNMAP_DATA")
    unmap.assert_called_once_with(server, ANY, "UNMAP_DATA")


def test_create_routes_destroy():
    """The window's destroy signal triggers client_cleanup so closing an
    app doesn't leave stale listeners attached to the dying surface."""
    server = make_server()
    with patch("welpy.windows.on_destroy") as cleanup:
        windows.on_create(server, "TOPLEVEL_DATA")
        trigger(server, server.lib.welpy_xdg_toplevel_destroy, "DESTROY_DATA")
    cleanup.assert_called_once_with(server, ANY, "DESTROY_DATA")


def test_create_routes_fullscreen_request():
    """The window's request_fullscreen signal drives client_request_fullscreen
    so app-initiated fullscreen toggles are honored."""
    server = make_server()
    with patch("welpy.windows.on_request_fullscreen") as handler:
        windows.on_create(server, "TOPLEVEL_DATA")
        trigger(
            server, server.lib.welpy_xdg_toplevel_request_fullscreen,
            "REQ_DATA")
    handler.assert_called_once_with(server, ANY, "REQ_DATA")


def test_create_routes_maximize_request():
    """The window's request_maximize signal drives client_request_maximize
    so the client gets the configure xdg-shell requires in reply."""
    server = make_server()
    with patch("welpy.windows.on_request_maximize") as handler:
        windows.on_create(server, "TOPLEVEL_DATA")
        trigger(
            server, server.lib.welpy_xdg_toplevel_request_maximize,
            "REQ_DATA")
    handler.assert_called_once_with(server, ANY, "REQ_DATA")


def test_commit_initial_configure():
    """The window's first commit triggers the initial configure xdg-shell
    requires before any pixels can be shown."""
    server = make_server()
    toplevel = MagicMock()
    toplevel.base.initial_commit = True
    client = make_client(toplevel=toplevel, scene_tree=None)

    windows.on_commit(server, client, None)

    server.lib.wlr_xdg_toplevel_set_size.assert_called_once_with(toplevel, 0, 0)


def test_commit_subsequent_no_configure():
    """Later commits don't re-send the initial configure."""
    server = make_server()
    toplevel = MagicMock()
    toplevel.base.initial_commit = False
    client = make_client(toplevel=toplevel, scene_tree=None)

    windows.on_commit(server, client, None)

    server.lib.wlr_xdg_toplevel_set_size.assert_not_called()


def test_commit_releases_screen_hold():
    """A commit after the client renders the latest configure releases the
    screen hold."""
    server = make_server()
    client = make_client(scene_tree=MagicMock(), pending_serial=7)
    client.toplevel.base.initial_commit = False
    client.toplevel.base.current.configure_serial = 9

    windows.on_commit(server, client, None)

    assert client.pending_serial is None
    server.lib.wlr_xdg_toplevel_set_size.assert_not_called()


def test_commit_keeps_screen_hold():
    """A commit before the client catches up leaves pending_serial in place
    so the screen keeps waiting."""
    server = make_server()
    client = make_client(scene_tree=MagicMock(), pending_serial=7)
    client.toplevel.base.initial_commit = False
    client.toplevel.base.current.configure_serial = 3

    windows.on_commit(server, client, None)

    assert client.pending_serial == 7


def test_commit_initial_records_serial():
    """The initial configure's serial is recorded so the screen waits for
    the client's first render."""
    server = make_server()
    server.lib.wlr_xdg_toplevel_set_size.return_value = 11
    toplevel = MagicMock()
    toplevel.base.initial_commit = True
    toplevel.base.current.configure_serial = 0
    client = make_client(toplevel=toplevel, scene_tree=None)

    windows.on_commit(server, client, None)

    assert client.pending_serial == 11


def test_commit_reclips_geometry():
    """On a post-map commit, the surface clip is refreshed with the current
    xdg geometry offset so the picture stays correct when the client drops or
    adds its CSD shadow (e.g. on entering / leaving fullscreen)."""
    server = make_server()
    toplevel = MagicMock()
    toplevel.base.initial_commit = False
    toplevel.base.geometry.x = 0
    toplevel.base.geometry.y = 0
    client = make_client(
        toplevel=toplevel, scene_tree=MagicMock(), inner_size=(800, 600))

    windows.on_commit(server, client, None)

    server.ffi.new.assert_any_call("struct wlr_box *", [0, 0, 800, 600])
    server.lib.wlr_scene_subsurface_tree_set_clip.assert_called_once_with(
        server.ffi.addressof.return_value, server.ffi.new.return_value)


def test_commit_after_unmap_skips_clip():
    """A commit arriving after unmap (during teardown) must not reclip: the
    scene tree -- and the xdg subtree it clips -- has already been destroyed."""
    server = make_server()
    toplevel = MagicMock()
    toplevel.base.initial_commit = False
    client = make_client(
        toplevel=toplevel, scene_tree=None, inner_size=(800, 600))

    windows.on_commit(server, client, None)

    server.lib.wlr_scene_subsurface_tree_set_clip.assert_not_called()


def test_commit_before_map_skips_clip():
    """Before the first resize, inner_size is unset and we have no idea what
    to clip to; the initial commit must not touch the clip."""
    server = make_server()
    toplevel = MagicMock()
    toplevel.base.initial_commit = True
    client = make_client(toplevel=toplevel, scene_tree=None, inner_size=None)

    windows.on_commit(server, client, None)

    server.lib.wlr_scene_subsurface_tree_set_clip.assert_not_called()


def test_map_registers_front():
    """A newly mapped window goes to the front of server.clients, which is
    now just a registry; tiling order lives in the workspace tree."""
    old = make_client()
    server = make_server(clients=[old])
    fresh = make_client(scene_tree=None)

    with patch("welpy.focus.bump_focus_order"):
        windows.on_map(server, fresh, None)

    assert server.clients[0] is fresh
    assert server.clients[1] is old


def test_map_builds_scene_subtree():
    """A mapped window's wrapper tree hangs off the tile layer so it's
    actually rendered, with the xdg subtree nested inside it."""
    # pylint: disable=duplicate-code
    server = make_server()
    wrapper = MagicMock(name="wrapper")
    server.lib.wlr_scene_tree_create.return_value = wrapper
    client = make_client(toplevel=MagicMock(), scene_tree=None)

    with patch("welpy.focus.bump_focus_order"):
        windows.on_map(server, client, None)

    server.lib.wlr_scene_tree_create.assert_called_once_with(
        server.layers[model.Layer.TILE])
    parent, _ = server.lib.wlr_scene_xdg_surface_create.call_args.args
    assert parent is wrapper
    assert client.scene_tree is wrapper


def test_map_focus_before_layout():
    """Mapping mutates focus_order before apply_geometry runs so the
    new window's order participates in the layout decision."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    server = make_server(monitors=[m], active_monitor=m)
    client = make_client(scene_tree=None)

    calls = []
    with patch(
            "welpy.geometry.reconcile",
            side_effect=lambda *_: calls.append("geometry")), \
         patch("welpy.focus.bump_focus_order",
               side_effect=lambda *_a: calls.append("focus")):
        windows.on_map(server, client, None)

    assert calls == ["focus", "geometry"]


def test_map_sets_popup_anchor():
    """Popups find their parent's scene tree through the toplevel surface's
    `data` slot, which client_map points at the wrapper tree."""
    server = make_server()
    wrapper = MagicMock(name="wrapper")
    server.lib.wlr_scene_tree_create.return_value = wrapper
    server.ffi.cast.side_effect = lambda type_, val: ("CAST", type_, val)
    client = make_client(toplevel=MagicMock(), scene_tree=None)

    with patch("welpy.focus.bump_focus_order"):
        windows.on_map(server, client, None)

    assert client.toplevel.base.surface.data == ("CAST", "void *", wrapper)


def test_map_applies_decoration():
    """client_map runs apply_decoration so the mode is set now that the
    initial configure has been sent."""
    server = make_server(monitors=[MagicMock(name="m", fullscreen=None)])
    client = make_client(scene_tree=None)

    with patch("welpy.geometry.reconcile"), \
         patch("welpy.focus.bump_focus_order"), \
         patch("welpy.geometry.apply_decoration") as ad:
        windows.on_map(server, client, None)

    ad.assert_called_once_with(server)


def test_map_focuses_window():
    """First time a window has something to show, we focus it so it can
    start receiving keys immediately."""
    server = make_server()
    client = make_client(toplevel=MagicMock(), scene_tree=MagicMock())

    with patch("welpy.focus.bump_focus_order") as focus_client:
        windows.on_map(server, client, None)

    focus_client.assert_called_once_with(server, client)


def test_map_marks_tiled_edges():
    """Mapping marks every window tiled on all edges -- set once and not
    touched again by arrange or set_floating."""
    server = make_server()
    server.lib.WLR_EDGE_TOP = 1
    server.lib.WLR_EDGE_BOTTOM = 2
    server.lib.WLR_EDGE_LEFT = 4
    server.lib.WLR_EDGE_RIGHT = 8
    client = make_client(toplevel=MagicMock(), scene_tree=MagicMock())

    windows.on_map(server, client, None)

    server.lib.wlr_xdg_toplevel_set_tiled.assert_called_once_with(
        client.toplevel, 15)


def test_map_attaches_tile_layer():
    """Mapped windows attach under the TILE layer so they participate in
    tiling and render below floating windows."""
    server = make_server()
    client = make_client(toplevel=MagicMock(), scene_tree=None)

    with patch("welpy.focus.bump_focus_order"):
        windows.on_map(server, client, None)

    server.lib.wlr_scene_tree_create.assert_called_once_with(
        server.layers[model.Layer.TILE])


def test_map_joins_active_workspace():
    """A newly mapped window joins the active workspace of the active
    monitor."""
    m1 = make_monitor()
    m1.active_workspace = make_workspace(monitor=m1)
    m2 = make_monitor()
    m2.active_workspace = make_workspace(monitor=m2)
    server = make_server(monitors=[m1, m2], active_monitor=m1)
    client = make_client(scene_tree=None)

    with patch("welpy.focus.bump_focus_order"), \
         patch("welpy.geometry.reconcile"):
        windows.on_map(server, client, None)

    assert client.workspace is m1.active_workspace


def test_map_no_monitor_orphans():
    """A newly mapped window with no active monitor is parked as orphaned."""
    server = make_server()
    client = make_client(scene_tree=None)

    with patch("welpy.focus.bump_focus_order"):
        windows.on_map(server, client, None)

    assert client.workspace is None


def test_map_dialog_floats():
    """A window opened as a child of another window (a dialog) lands in the
    FLOAT layer instead of joining the tiling layout."""
    m = make_monitor(window_area=layout.Rect(0, 0, 800, 600))
    m.active_workspace = make_workspace(monitor=m)
    server = make_server(monitors=[m], active_monitor=m)
    toplevel = MagicMock()
    toplevel.base.geometry.width = 400
    toplevel.base.geometry.height = 300
    client = make_client(toplevel=toplevel, scene_tree=None)
    toplevel.parent = MagicMock(name="parent_toplevel")

    with patch("welpy.focus.bump_focus_order"), \
         patch("welpy.geometry.reconcile"):
        windows.on_map(server, client, None)

    assert geometry.client_layer(client) == model.Layer.FLOAT


def test_map_regular_tiles():
    """A regular (unparented) window still joins the tiling layout."""
    m = make_monitor(window_area=layout.Rect(0, 0, 800, 600))
    m.active_workspace = make_workspace(monitor=m)
    server = make_server(monitors=[m], active_monitor=m)
    client = make_client(scene_tree=None)

    with patch("welpy.focus.bump_focus_order"), \
         patch("welpy.geometry.reconcile"):
        windows.on_map(server, client, None)

    assert geometry.client_layer(client) == model.Layer.TILE


def test_map_inserts_leaf_beside_focused():
    """A mapped tiled window joins the workspace tree right after the window
    that was focused when it appeared."""
    m = make_monitor(window_area=layout.Rect(0, 0, 800, 600))
    m.active_workspace = make_workspace(monitor=m)
    old = make_client(workspace=m.active_workspace, focus_order=1)
    m.active_workspace.root = flat_tree(old)
    server = make_server(monitors=[m], active_monitor=m, clients=[old])
    fresh = make_client(scene_tree=None)

    with patch("welpy.focus.bump_focus_order"), \
         patch("welpy.geometry.reconcile"):
        windows.on_map(server, fresh, None)

    assert m.active_workspace.root.children == [old, fresh]


def test_client_map_unfullscreens_existing():
    """A new window on a workspace that already hosts a fullscreen window
    un-fullscreens that window first so the new one isn't buried."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    existing = make_client(workspace=m.active_workspace)
    m.active_workspace.fullscreen = existing
    server = make_server(monitors=[m], active_monitor=m, clients=[existing])
    fresh = make_client(scene_tree=None)

    with patch("welpy.focus.bump_focus_order"), \
         patch("welpy.geometry.reconcile"):
        windows.on_map(server, fresh, None)

    assert m.active_workspace.fullscreen is None
    server.lib.wlr_xdg_toplevel_set_fullscreen.assert_called_once_with(
        existing.toplevel, False)


def test_unmap_focuses_mru():
    """Unmapping a window hands focus to the next-most-recently-focused
    window so closing a terminal leaves the user typing into another one."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    a = make_client(focus_order=2, workspace=m.active_workspace)
    b = make_client(focus_order=1, workspace=m.active_workspace)
    server = make_server(monitors=[m], active_monitor=m, clients=[a, b])

    with patch("welpy.geometry.reconcile"), \
         patch("welpy.focus.bump_focus_order") as focus_client:
        windows.on_unmap(server, a, "DATA")

    focus_client.assert_called_once_with(server, b)


def test_unmap_clears_grab():
    """Unmapping a window in the middle of a drag clears its grab state so
    the user isn't left invisibly dragging a closed window."""
    client = make_client(grab=model.Grab("move", 10, 20))
    server = make_server(clients=[client])

    windows.on_unmap(server, client, "DATA")

    assert client.grab is None


def test_unmap_last_window_keeps_focus():
    """Unmapping the only window leaves focus alone -- nothing to focus."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    only = make_client(
        focus_order=1,
        workspace=m.active_workspace,
    )
    server = make_server(monitors=[m], active_monitor=m, clients=[only])

    with patch("welpy.focus.bump_focus_order") as focus_client:
        windows.on_unmap(server, only, "DATA")

    focus_client.assert_not_called()


def test_unmap_focuses_groupmate():
    """Closing a grouped window hands focus to its groupmate, even when a
    window outside the group was focused more recently."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    a = make_client(focus_order=3, workspace=m.active_workspace)
    b = make_client(focus_order=1, workspace=m.active_workspace)
    c = make_client(focus_order=2, workspace=m.active_workspace)
    inner = layout.Container(layout.ContainerLayout.VERTICAL, [b, c])
    m.active_workspace.root = layout.Container(
        layout.ContainerLayout.HORIZONTAL, [a, inner])
    server = make_server(monitors=[m], active_monitor=m, clients=[a, b, c])

    with patch("welpy.geometry.reconcile"), \
         patch("welpy.focus.bump_focus_order") as focus_client:
        windows.on_unmap(server, b, "DATA")

    focus_client.assert_called_once_with(server, c)


def test_unmap_float_falls_back_mru():
    """Closing a floating window has no container lineage, so focus falls back
    to the most-recently-focused window on the screen."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    a = make_client(focus_order=1, workspace=m.active_workspace)
    b = make_client(focus_order=2, workspace=m.active_workspace)
    m.active_workspace.root = flat_tree(a, b)
    f = make_client(
        focus_order=3, workspace=m.active_workspace,
        floating_geom=layout.Rect(0, 0, 100, 100))
    server = make_server(monitors=[m], active_monitor=m, clients=[a, b, f])

    with patch("welpy.geometry.reconcile"), \
         patch("welpy.focus.bump_focus_order") as focus_client:
        windows.on_unmap(server, f, "DATA")

    focus_client.assert_called_once_with(server, b)


def test_client_unmap_clears_popup_anchor():
    """Unmap clears the popup anchor so later popups don't try to attach
    to a destroyed scene tree."""
    client = make_client()
    server = make_server(clients=[client])

    windows.on_unmap(server, client, None)

    assert client.toplevel.base.surface.data is server.ffi.NULL


def test_unmap_drops_ime_popups_first():
    """IME popups parented under a window are forgotten before the parent
    scene tree is destroyed."""
    client = make_client()
    scene_tree = client.scene_tree
    server = make_server(clients=[client])
    calls = []
    server.lib.wlr_scene_node_destroy.side_effect = \
        lambda *_a: calls.append("destroy")

    with patch(
            "welpy.focus.drop_ime_popups_for_scene_tree",
            side_effect=lambda *_a: calls.append("drop")) as drop_popups:
        windows.on_unmap(server, client, None)

    drop_popups.assert_called_once_with(server, scene_tree)
    assert calls[:2] == ["drop", "destroy"]


def test_fullscreen_request_enters():
    """A tiled client whose app requests fullscreen lands in its
    workspace's fullscreen slot."""
    server = make_server()
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    client = make_client(
        toplevel=MagicMock(),
        scene_tree=MagicMock(),
        workspace=m.active_workspace,
    )
    client.toplevel.requested.fullscreen = True

    with patch("welpy.geometry.apply_tree"), \
         patch("welpy.geometry.reconcile"), \
         patch("welpy.focus.reconcile"):
        windows.on_request_fullscreen(server, client, None)

    assert m.active_workspace.fullscreen is client
    server.lib.wlr_xdg_toplevel_set_fullscreen.assert_called_once_with(
        client.toplevel, True)


def test_fullscreen_request_keeps_float():
    """A floating client that goes fullscreen keeps its floating_geom
    intact, so exit later returns to the same rect."""
    server = make_server()
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    saved = layout.Rect(10, 20, 300, 200)
    client = make_client(
        toplevel=MagicMock(),
        scene_tree=MagicMock(),
        workspace=m.active_workspace,
        floating_geom=saved,
    )
    client.toplevel.requested.fullscreen = True

    with patch("welpy.geometry.apply_tree"), \
         patch("welpy.geometry.reconcile"), \
         patch("welpy.focus.reconcile"):
        windows.on_request_fullscreen(server, client, None)

    assert m.active_workspace.fullscreen is client
    assert client.floating_geom == saved


def test_fullscreen_exit_to_tile():
    """A fullscreen client with no saved float geometry returns to TILE
    when its app requests un-fullscreen."""
    server = make_server()
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    client = make_client(
        toplevel=MagicMock(),
        scene_tree=MagicMock(),
        workspace=m.active_workspace,
    )
    m.active_workspace.fullscreen = client
    client.toplevel.requested.fullscreen = False

    with patch("welpy.geometry.apply_tree"), \
         patch("welpy.geometry.reconcile"), \
         patch("welpy.focus.reconcile"):
        windows.on_request_fullscreen(server, client, None)

    assert m.active_workspace.fullscreen is None
    assert geometry.client_layer(client) == model.Layer.TILE


def test_fullscreen_exit_to_float():
    """A fullscreen client that was floating returns to FLOAT when its app
    requests un-fullscreen."""
    # pylint: disable=duplicate-code
    server = make_server()
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    saved = layout.Rect(10, 20, 300, 200)
    client = make_client(
        toplevel=MagicMock(),
        scene_tree=MagicMock(),
        workspace=m.active_workspace,
        floating_geom=saved,
    )
    m.active_workspace.fullscreen = client
    client.toplevel.requested.fullscreen = False

    with patch("welpy.geometry.apply_tree"), \
         patch("welpy.geometry.reconcile"), \
         patch("welpy.focus.reconcile"):
        windows.on_request_fullscreen(server, client, None)

    assert m.active_workspace.fullscreen is None
    assert client.floating_geom == saved
    assert geometry.client_layer(client) == model.Layer.FLOAT


def test_fullscreen_request_pre_map_deferred():
    """A request that fires before map (scene_tree still None) is deferred;
    client_map then promotes the window using requested.fullscreen."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    server = make_server(monitors=[m], active_monitor=m)
    client = make_client(scene_tree=None)
    client.toplevel.requested.fullscreen = True

    with patch("welpy.geometry.set_fullscreen") as sf:
        windows.on_request_fullscreen(server, client, None)
    sf.assert_not_called()

    with patch("welpy.geometry.set_fullscreen") as sf, \
         patch("welpy.focus.bump_focus_order"):
        windows.on_map(server, client, None)
    sf.assert_called_with(server, m.active_workspace, client)


def test_fullscreen_request_redundant_noop():
    """An already-fullscreen client whose app re-requests fullscreen is a
    no-op so no spurious configure goes out."""
    server = make_server()
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    client = make_client(
        toplevel=MagicMock(),
        scene_tree=MagicMock(),
        workspace=m.active_workspace,
    )
    m.active_workspace.fullscreen = client
    client.toplevel.requested.fullscreen = True

    with patch("welpy.geometry.apply_tree"), \
         patch("welpy.geometry.reconcile"), \
         patch("welpy.focus.reconcile"):
        windows.on_request_fullscreen(server, client, None)

    server.lib.wlr_xdg_toplevel_set_fullscreen.assert_not_called()


def test_maximize_request_acks_when_initialized():
    """An initialized window gets the empty configure xdg-shell requires."""
    server = make_server()
    client = make_client()
    client.toplevel.base.initialized = True
    windows.on_request_maximize(server, client, None)
    server.lib.wlr_xdg_surface_schedule_configure.assert_called_once_with(
        client.toplevel.base)


def test_maximize_request_before_init_ignored():
    """A maximize request before the first commit is ignored; scheduling a
    configure then trips a wlroots assertion (Firefox/Chrome do this)."""
    server = make_server()
    client = make_client()
    client.toplevel.base.initialized = False
    windows.on_request_maximize(server, client, None)
    server.lib.wlr_xdg_surface_schedule_configure.assert_not_called()


def test_maximize_request_schedules_configure():
    """We don't maximize, but xdg-shell still requires a configure in reply,
    so the request schedules an (empty) one to keep clients from stalling."""
    server = make_server()
    client = make_client(toplevel=MagicMock())

    windows.on_request_maximize(server, client, None)

    server.lib.wlr_xdg_surface_schedule_configure.assert_called_once_with(
        client.toplevel.base)


def test_activate_marks_urgent():
    """An activation request flags an unfocused window urgent."""
    monitor = make_monitor()
    monitor.active_workspace = make_workspace(monitor=monitor)
    client = make_client(workspace=monitor.active_workspace)
    server = make_server(
        ext_workspace=None, monitors=[monitor], clients=[client])
    event = MagicMock(name="event")
    event.surface = client.toplevel.base.surface
    server.ffi.cast.return_value = event # pylint: disable=no-member

    windows.on_request_activate(server, "DATA")

    assert client.urgent


def test_activate_focused_skips_urgent():
    """Activating the already-focused window does not mark it urgent."""
    monitor = make_monitor()
    monitor.active_workspace = make_workspace(monitor=monitor)
    client = make_client(workspace=monitor.active_workspace)
    server = make_server(
        ext_workspace=None, monitors=[monitor], clients=[client])
    server.seat.keyboard_state.focused_surface = client.toplevel.base.surface # pylint: disable=no-member
    event = MagicMock(name="event")
    event.surface = client.toplevel.base.surface
    server.ffi.cast.return_value = event # pylint: disable=no-member

    windows.on_request_activate(server, "DATA")

    assert not client.urgent


def test_destroy_detaches_window_listeners():
    """Cleanup detaches every listener so the dying surface doesn't fire
    callbacks into freed state. Scene tree + list entry were already
    released in unmap."""
    server = make_server()
    h1, h2 = MagicMock(name="h1"), MagicMock(name="h2")
    client = make_client(
        toplevel="TL", scene_tree=None, listeners=[h1, h2])

    windows.on_destroy(server, client, None)

    h1.remove.assert_called_once()
    h2.remove.assert_called_once()
    assert not client.listeners
    server.lib.wlr_scene_node_destroy.assert_not_called()


def _stage_popup(server, *, initial_commit=True, parent_data="PDATA",
                 owner=None):
    """Stage a popup so popup_new resolves `data` to it via ffi.cast."""
    popup = MagicMock(name="popup")
    popup.base.initial_commit = initial_commit
    popup.parent.data = parent_data
    parent_tree = MagicMock(name="parent_tree")
    def cast(type_str, val):
        return {
            "struct wlr_xdg_popup *": popup,
            "struct wlr_scene_tree *": parent_tree,
        }.get(type_str, ("CAST", type_str, val))
    server.ffi.cast.side_effect = cast
    if owner is not None:
        server.lib.wlr_surface_get_root_surface.return_value = (
            owner.toplevel.base.surface)
    return popup, parent_tree


def test_popup_defers_scene():
    """popup_new only attaches commit + destroy listeners; the scene node
    is deferred until the popup's first commit."""
    server = make_server()
    _stage_popup(server)

    windows.popup_new(server, "DATA")

    server.lib.wlr_scene_xdg_surface_create.assert_not_called()
    server.lib.wlr_xdg_popup_unconstrain_from_box.assert_not_called()


def test_popup_attaches_on_commit():
    """On the popup's first commit, popup_new attaches it under the parent's
    scene tree and stores the result on the popup surface's `data` so
    nested popups can chain off it."""
    server = make_server()
    popup, parent_tree = _stage_popup(server)
    scene = MagicMock(name="scene")
    server.lib.wlr_scene_xdg_surface_create.return_value = scene

    windows.popup_new(server, "DATA")
    trigger(server, server.lib.welpy_surface_commit, "COMMIT")

    server.lib.wlr_scene_xdg_surface_create.assert_called_once_with(
        parent_tree, popup.base)
    assert popup.base.surface.data == ("CAST", "void *", scene)


def test_popup_subsequent_commit_noop():
    """Subsequent commits don't re-create the scene node."""
    server = make_server()
    _stage_popup(server, initial_commit=False)

    windows.popup_new(server, "DATA")
    trigger(server, server.lib.welpy_surface_commit, "COMMIT")

    server.lib.wlr_scene_xdg_surface_create.assert_not_called()


def test_popup_no_anchor_dropped():
    """A popup whose parent surface has no anchor (e.g. layer-shell, which
    we don't manage yet) is dropped instead of attached."""
    server = make_server()
    _stage_popup(server, parent_data=server.ffi.NULL)

    windows.popup_new(server, "DATA")
    trigger(server, server.lib.welpy_surface_commit, "COMMIT")

    server.lib.wlr_scene_xdg_surface_create.assert_not_called()


def test_popup_unconstrains_to_monitor():
    """After attaching, the popup is unconstrained to the owner monitor's
    box, translated into the parent client's local coordinates."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    owner = make_client(workspace=m.active_workspace)
    owner.scene_tree.node.x = 100
    owner.scene_tree.node.y = 50
    server = make_server(monitors=[m], clients=[owner])
    popup, _ = _stage_popup(server, owner=owner)
    with patch("welpy.geometry.monitor_box",
               return_value=layout.Rect(10, 20, 800, 600)):
        windows.popup_new(server, "DATA")
        trigger(server, server.lib.welpy_surface_commit, "COMMIT")

    server.lib.wlr_xdg_popup_unconstrain_from_box.assert_called_once()
    args, _ = server.lib.wlr_xdg_popup_unconstrain_from_box.call_args
    assert args[0] is popup
    server.ffi.new.assert_any_call(
        "struct wlr_box *", [10 - 100, 20 - 50, 800, 600])


def test_popup_listeners_cleared():
    """After the first valid commit, both popup listeners detach so the
    handler runs once."""
    server = make_server()
    _stage_popup(server)
    server.lib.wlr_scene_xdg_surface_create.return_value = MagicMock()

    handles = []
    def listen(*_a):
        handles.append(MagicMock())
        return handles[-1]
    server.listen.side_effect = listen

    windows.popup_new(server, "DATA")
    trigger(server, server.lib.welpy_surface_commit, "COMMIT")

    assert not server.listeners
    for h in handles:
        h.remove.assert_called_once()


def test_popup_destroy_cleans_up():
    """If the popup is destroyed before its first commit, the destroy
    listener detaches both listeners."""
    server = make_server()
    _stage_popup(server)

    handles = []
    def listen(*_a):
        handles.append(MagicMock())
        return handles[-1]
    server.listen.side_effect = listen

    windows.popup_new(server, "DATA")
    trigger(server, server.lib.welpy_xdg_popup_destroy, "DESTROY")

    assert not server.listeners
    for h in handles:
        h.remove.assert_called_once()


def test_popup_layer_owner_monitor():
    """A popup whose parent is a layer-shell surface unconstrains against
    that surface's monitor, not a client's."""
    monitor = make_monitor()
    server = make_server(monitors=[monitor])
    ls = make_layer_surface(monitor=monitor)
    ls.scene_tree.node.x = 0
    ls.scene_tree.node.y = 0
    ls.layer_surface.surface = MagicMock(name="ls_surface")
    monitor.layers[model.Layer.TOP].append(ls)
    server.lib.wlr_surface_get_root_surface.return_value = (
        ls.layer_surface.surface)
    _stage_popup(server)

    with patch("welpy.geometry.monitor_box",
               return_value=layout.Rect(0, 0, 800, 600)):
        windows.popup_new(server, "DATA")
        trigger(server, server.lib.welpy_surface_commit, "COMMIT")

    server.lib.wlr_xdg_popup_unconstrain_from_box.assert_called_once()
    server.ffi.new.assert_any_call(
        "struct wlr_box *", [0, 0, 800, 600])


def test_unmap_other_monitor():
    """Unmapping a window not on the active monitor leaves focus alone
    when there's nothing to refocus on the active monitor."""
    m1 = make_monitor()
    m1.active_workspace = make_workspace(monitor=m1)
    m2 = make_monitor()
    m2.active_workspace = make_workspace(monitor=m2)
    a = make_client(focus_order=1, workspace=m2.active_workspace)
    server = make_server(monitors=[m1], active_monitor=m1, clients=[a])

    with patch("welpy.focus.bump_focus_order") as focus_client:
        windows.on_unmap(server, a, "DATA")

    focus_client.assert_not_called()


def test_map_adds_borders():
    """A new window gets four edge rects under its wrapper tree so it has
    something to color on focus."""
    server = make_server()
    wrapper = MagicMock(name="wrapper")
    server.lib.wlr_scene_tree_create.return_value = wrapper
    client = make_client(toplevel=MagicMock(), scene_tree=None)

    with patch("welpy.focus.bump_focus_order"):
        windows.on_map(server, client, None)

    assert len(client.borders) == 4
    parents = [
        c.args[0] for c in server.lib.wlr_scene_rect_create.call_args_list
    ]
    assert parents == [wrapper] * 4


def test_map_x11_to_front():
    """A mapped X11 window goes to the front of server.clients, like a
    Wayland one."""
    old = make_client()
    server = make_server(clients=[old])
    fresh = make_x11_client(scene_tree=None)

    with patch("welpy.focus.bump_focus_order"):
        windows.on_map(server, fresh, None)

    assert server.clients[0] is fresh


def test_map_x11_subsurface_tree():
    """An X11 window's content goes into a subsurface tree, not an xdg one."""
    server = make_server()
    client = make_x11_client(scene_tree=None)

    with patch("welpy.focus.bump_focus_order"):
        windows.on_map(server, client, None)

    server.lib.wlr_scene_subsurface_tree_create.assert_called_once_with(
        server.lib.wlr_scene_tree_create.return_value, client.xsurface.surface)
    server.lib.wlr_scene_xdg_surface_create.assert_not_called()


def test_commit_initial_defers_tiling():
    """A tiled client's initial commit defers tiling to map so siblings
    don't reflow before the new window can appear."""
    server = make_server()
    workspace = make_workspace()
    toplevel = MagicMock()
    toplevel.base.initial_commit = True
    client = make_client(toplevel=toplevel, workspace=workspace)

    with patch("welpy.geometry.reconcile") as apply_geom:
        windows.on_commit(server, client, None)

    apply_geom.assert_not_called()
    server.lib.wlr_xdg_toplevel_set_size.assert_called_once_with(
        toplevel, 0, 0)


def test_commit_initial_floating_configure():
    """A floating client falls back to the (0, 0) initial configure."""
    server = make_server()
    workspace = make_workspace()
    toplevel = MagicMock()
    toplevel.base.initial_commit = True
    client = make_client(
        toplevel=toplevel,
        floating_geom=layout.Rect(0, 0, 100, 100),
        workspace=workspace,
    )

    with patch("welpy.geometry.reconcile") as apply_geom:
        windows.on_commit(server, client, None)

    apply_geom.assert_not_called()
    server.lib.wlr_xdg_toplevel_set_size.assert_called_once_with(
        toplevel, 0, 0)


def test_commit_initial_unassigned_configure():
    """A tiled client with no workspace falls back to the (0, 0) initial
    configure so the required configure still goes out."""
    server = make_server()
    toplevel = MagicMock()
    toplevel.base.initial_commit = True
    client = make_client(toplevel=toplevel, workspace=None)

    with patch("welpy.geometry.reconcile") as apply_geom:
        windows.on_commit(server, client, None)

    apply_geom.assert_not_called()
    server.lib.wlr_xdg_toplevel_set_size.assert_called_once_with(
        toplevel, 0, 0)


def test_unmap_reflows_monitor():
    """After a tiled client unmaps, its monitor re-flows so remaining
    tiles expand -- in the same event as the window's removal so it lands
    in a single frame."""
    # pylint: disable=duplicate-code
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    a = make_client(workspace=m.active_workspace)
    b = make_client(workspace=m.active_workspace)
    server = make_server(monitors=[m], active_monitor=m, clients=[a, b])

    with patch("welpy.geometry.reconcile") as apply_geom:
        windows.on_unmap(server, a, None)

    apply_geom.assert_called_once_with(server, m)


def test_unmap_destroys_scene_tree():
    """Unmapping releases the scene tree so the disappearing window, the
    reflow, and the focus shift all happen together."""
    client = make_client()
    server = make_server(clients=[client])

    windows.on_unmap(server, client, None)

    server.lib.wlr_scene_node_destroy.assert_called_once_with(
        server.ffi.addressof.return_value)
    assert client.scene_tree is None
    assert client not in server.clients


def test_unmap_drops_tiled_leaf():
    """Unmapping a tiled window drops its leaf from the workspace tree so the
    siblings reflow."""
    m = make_monitor()
    m.active_workspace = make_workspace(monitor=m)
    a = make_client(workspace=m.active_workspace, focus_order=1)
    b = make_client(workspace=m.active_workspace, focus_order=2)
    m.active_workspace.root = flat_tree(a, b)
    server = make_server(monitors=[m], active_monitor=m, clients=[a, b])

    with patch("welpy.focus.bump_focus_order"), \
         patch("welpy.geometry.reconcile"):
        windows.on_unmap(server, a, None)

    assert m.active_workspace.root.children == [b]


def test_unmap_orphan_no_reflow():
    """Unmapping an orphaned client doesn't trigger an arrange."""
    client = make_client(workspace=None)
    server = make_server(clients=[client])

    with patch("welpy.geometry.reconcile") as apply_geom:
        windows.on_unmap(server, client, None)

    apply_geom.assert_not_called()


def test_unmap_stale_monitor_noop():
    """Unmapping a client whose monitor has already been removed is a
    no-op for arrange."""
    m = make_monitor()  # not in server.monitors
    m.active_workspace = make_workspace(monitor=m)
    client = make_client(workspace=m.active_workspace)
    server = make_server(clients=[client])

    with patch("welpy.geometry.reconcile") as apply_geom:
        windows.on_unmap(server, client, None)

    apply_geom.assert_not_called()

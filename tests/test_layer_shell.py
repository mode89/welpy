"""Unit tests for welpy.layer_shell: shell-surface lifecycle for bars,
wallpaper, panels, and launchers."""

from unittest.mock import MagicMock, patch

from welpy import layer_shell, model
from tests.helpers import (
    make_server, make_client, make_monitor, make_workspace, make_layer_surface,
)


def _stage_layer_surface_new(server, *, layer=0, output=None, monitor=None):
    """Stage layer_surface_new so the cast resolves to a controllable
    wlr_layer_surface_v1 with the given pending layer and output."""
    layer_surface = MagicMock(name="layer_surface")
    layer_surface.pending.layer = layer
    layer_surface.output = output if output is not None else server.ffi.NULL
    server.ffi.cast.side_effect = lambda type_str, val: {
        "struct wlr_layer_surface_v1 *": layer_surface,
    }.get(type_str, ("CAST", type_str, val))
    if monitor is not None and monitor not in server.monitors:
        server.monitors.append(monitor)
        server.active_monitor = monitor
    return layer_surface


def test_layer_new_no_monitor():
    """A surface with no requested output and no monitors connected is
    rejected instead of crashing."""
    server = make_server()
    _stage_layer_surface_new(server)

    layer_shell.on_create(server, "DATA")

    server.lib.wlr_layer_surface_v1_destroy.assert_called_once()
    server.lib.wlr_scene_layer_surface_v1_create.assert_not_called()


def test_layer_new_assigns_monitor():
    """When the app didn't pick a screen, place the surface on the active
    one and write back the output so wlroots agrees."""
    server = make_server()
    monitor = make_monitor()
    layer_surface = _stage_layer_surface_new(server, monitor=monitor)

    layer_shell.on_create(server, "DATA")

    assert layer_surface.output is monitor.output
    assert monitor.layers[model.Layer.BACKGROUND][0].monitor is monitor


def test_layer_new_buckets():
    """The surface lands in the monitor bucket matching its pending layer
    (zwlr 0..3 -> BACKGROUND/BOTTOM/TOP/OVERLAY)."""
    server = make_server()
    monitor = make_monitor()
    _stage_layer_surface_new(server, layer=2, monitor=monitor)  # TOP

    layer_shell.on_create(server, "DATA")

    assert len(monitor.layers[model.Layer.TOP]) == 1
    assert monitor.layers[model.Layer.BACKGROUND] == []


def test_layer_new_popups_high():
    """TOP/OVERLAY surfaces keep their popups in their own scene tree so a
    launcher's menu isn't buried by sibling overlays."""
    server = make_server()
    monitor = make_monitor()
    _stage_layer_surface_new(server, layer=3, monitor=monitor)  # OVERLAY

    layer_shell.on_create(server, "DATA")

    server.lib.wlr_scene_tree_create.assert_called_once_with(
        server.layers[model.Layer.OVERLAY])


def test_layer_new_popups_low():
    """BACKGROUND/BOTTOM popups attach into TOP so e.g. a wallpaper's menu
    isn't hidden behind a bar."""
    server = make_server()
    monitor = make_monitor()
    _stage_layer_surface_new(server, layer=0, monitor=monitor)

    layer_shell.on_create(server, "DATA")

    server.lib.wlr_scene_tree_create.assert_called_once_with(
        server.layers[model.Layer.TOP])


def test_layer_new_send_enter():
    """The surface is told which screen it lives on so apps can scale
    correctly for that monitor."""
    server = make_server()
    monitor = make_monitor()
    layer_surface = _stage_layer_surface_new(server, monitor=monitor)

    layer_shell.on_create(server, "DATA")

    server.lib.wlr_surface_send_enter.assert_called_once_with(
        layer_surface.surface, monitor.output)


def test_layer_commit_moves_bucket():
    """If the app moves the surface to a different layer on commit, the
    bucket reflects the new layer; apply_tree reparents."""
    server = make_server()
    monitor = make_monitor()
    ls = make_layer_surface(monitor=monitor)
    ls.layer_surface.initial_commit = False
    ls.layer_surface.current.layer = 2  # TOP
    monitor.layers[model.Layer.BOTTOM].append(ls)

    with patch("welpy.geometry.arrange_layers"):
        layer_shell.on_commit(server, ls, None)

    assert ls not in monitor.layers[model.Layer.BOTTOM]
    assert ls in monitor.layers[model.Layer.TOP]


def test_layer_commit_skips_content():
    """A plain content commit (no layer-shell state change, mapped unchanged)
    must not re-arrange: wlroots configures on every arrange and the client
    acks with a commit, which would otherwise loop at CPU speed."""
    server = make_server()
    monitor = make_monitor()
    ls = make_layer_surface(monitor=monitor, mapped=True)
    ls.layer_surface.initial_commit = False
    ls.layer_surface.current.committed = 0
    ls.layer_surface.surface.mapped = True

    with patch("welpy.geometry.arrange_layers") as arrange:
        layer_shell.on_commit(server, ls, None)

    arrange.assert_not_called()


def test_layer_unmap_clears_focus():
    """Closing the keyboard-grabbing shell surface releases its keyboard
    hold so clients can be focused again."""
    server = make_server()
    monitor = make_monitor()
    ls = make_layer_surface(monitor=monitor, focused=True)

    with patch("welpy.geometry.arrange_layers"), \
         patch("welpy.focus.bump_focus_order"):
        layer_shell.on_unmap(server, ls, None)

    assert ls.focused is False


def test_layer_unmap_refocuses_client():
    """When the keyboard-grabbing surface goes away, focus returns to the
    top client on the active screen."""
    monitor = make_monitor()
    monitor.active_workspace = make_workspace(monitor=monitor)
    client = make_client(workspace=monitor.active_workspace, focus_order=1)
    server = make_server(
        monitors=[monitor], active_monitor=monitor, clients=[client])
    ls = make_layer_surface(monitor=monitor, focused=True)

    with patch("welpy.geometry.arrange_layers"), \
         patch("welpy.focus.bump_focus_order") as focus_client:
        layer_shell.on_unmap(server, ls, None)

    focus_client.assert_called_once_with(server, client)


def test_layer_unmap_refocuses_monitor():
    """A shell surface on a secondary screen returns focus to that screen's
    top client, not the globally selected screen."""
    selected = make_monitor()
    selected.active_workspace = make_workspace(monitor=selected)
    monitor = make_monitor()
    monitor.active_workspace = make_workspace(monitor=monitor)
    selected_client = make_client(
        workspace=selected.active_workspace, focus_order=10)
    monitor_client = make_client(
        workspace=monitor.active_workspace, focus_order=1)
    server = make_server(
        monitors=[selected, monitor], active_monitor=selected,
        clients=[selected_client, monitor_client])
    ls = make_layer_surface(monitor=monitor, focused=True)

    with patch("welpy.geometry.arrange_layers"), \
         patch("welpy.focus.bump_focus_order") as focus_client:
        layer_shell.on_unmap(server, ls, None)

    focus_client.assert_called_once_with(server, monitor_client)


def test_layer_unmap_unfocused():
    """Closing a non-keyboard layer surface (e.g. wallpaper) doesn't
    disturb whatever has the keyboard."""
    monitor = make_monitor()
    server = make_server(monitors=[monitor])
    ls = make_layer_surface(monitor=monitor, focused=False)

    with patch("welpy.geometry.arrange_layers"), \
         patch("welpy.focus.bump_focus_order") as focus_client:
        layer_shell.on_unmap(server, ls, None)

    focus_client.assert_not_called()


def test_layer_cleanup_removes():
    """Destroying a shell surface drops it from its monitor's bucket so
    later arranges don't trip over a stale entry."""
    server = make_server()
    monitor = make_monitor()
    ls = make_layer_surface(monitor=monitor)
    monitor.layers[model.Layer.TOP].append(ls)
    h = MagicMock()
    ls.listeners.append(h)

    with patch("welpy.geometry.arrange_layers"):
        layer_shell.on_destroy(server, ls, None)

    h.remove.assert_called_once()
    assert ls not in monitor.layers[model.Layer.TOP]
    assert ls.monitor is None


def test_layer_cleanup_trees():
    """Destroying a shell surface releases the popup tree we own; the
    content tree is freed by wlr_scene_layer_surface_v1's own destroy
    listener so we must not touch it."""
    server = make_server()
    monitor = make_monitor()
    ls = make_layer_surface(monitor=monitor)
    server.ffi.addressof.side_effect = lambda node: ("ADDR", node)

    layer_shell.on_destroy(server, ls, None)

    server.lib.wlr_scene_node_destroy.assert_called_once_with(
        ("ADDR", ls.popups_tree.node))

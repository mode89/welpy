"""Layer-shell surface lifecycle for bars, wallpaper, panels, and launchers."""

from __future__ import annotations

from . import focus
from . import geometry
from . import model
from .model import Layer, LayerSurface, Server


def on_create(server: Server, data) -> None:
    """Fires when an app creates a shell-anchored window (bar, wallpaper,
    launcher). Sets up its scene tree; real geometry lands at first commit."""
    ffi, lib, listen = server.ffi, server.lib, server.listen
    layer_surface = ffi.cast("struct wlr_layer_surface_v1 *", data)
    if layer_surface.output == ffi.NULL:
        monitor = server.active_monitor
        if monitor is not None:
            layer_surface.output = monitor.output
    else:
        monitor = next(
            (m for m in server.monitors if m.output == layer_surface.output),
            None)

    if monitor is None:
        lib.wlr_layer_surface_v1_destroy(layer_surface)
    else:
        layer = model.SHELL_LAYERS[layer_surface.pending.layer]
        scene_layer = lib.wlr_scene_layer_surface_v1_create(
            server.layers[layer], layer_surface)
        # Lift popups for BG/BOTTOM into TOP so they aren't buried by a bar.
        popups_parent = server.layers[
            Layer.TOP if layer in (Layer.BACKGROUND, Layer.BOTTOM) else layer]
        popups_tree = lib.wlr_scene_tree_create(popups_parent)
        ls = LayerSurface(
            layer_surface=layer_surface, scene_layer=scene_layer,
            scene_tree=scene_layer.tree, popups_tree=popups_tree,
            monitor=monitor, focused=False, mapped=False, listeners=[])
        # popup_new resolves the parent scene tree via surface.data.
        layer_surface.surface.data = ffi.cast("void *", popups_tree)
        monitor.layers[layer].append(ls)
        ls.listeners.extend([
            listen(lib.welpy_surface_commit(layer_surface.surface),
                lambda data: on_commit(server, ls, data)),
            listen(lib.welpy_surface_unmap(layer_surface.surface),
                lambda data: on_unmap(server, ls, data)),
            listen(lib.welpy_layer_surface_destroy(layer_surface),
                lambda data: on_destroy(server, ls, data)),
        ])
        lib.wlr_surface_send_enter(layer_surface.surface, monitor.output)


def on_commit(server: Server, ls: LayerSurface, _data) -> None:
    """Fires every time the shell surface commits new state."""
    ffi = server.ffi
    layer_surface = ls.layer_surface
    monitor = ls.monitor
    if monitor is not None:
        if layer_surface.initial_commit:
            # Swap pending into current so the initial configure sees real size.
            size = ffi.sizeof("struct wlr_layer_surface_v1_state")
            saved = ffi.new("struct wlr_layer_surface_v1_state *")
            ffi.memmove(saved, ffi.addressof(layer_surface, "current"), size)
            ffi.memmove(ffi.addressof(layer_surface, "current"),
                        ffi.addressof(layer_surface, "pending"), size)
            geometry.arrange_layers(server, monitor)
            ffi.memmove(ffi.addressof(layer_surface, "current"), saved, size)
        else:
            # Re-arrange sends a configure the client acks with a commit, so a
            # plain content commit (clock tick) would loop unless state changed.
            surface = layer_surface.surface
            changed = (
                layer_surface.current.committed != 0
                or ls.mapped != surface.mapped
            )
            if changed:
                ls.mapped = surface.mapped
                geometry.place_in_layer_bucket(
                    monitor, ls,
                    model.SHELL_LAYERS[layer_surface.current.layer])
                geometry.arrange_layers(server, monitor)
                geometry.apply_tree(server)
                geometry.reconcile(server, monitor)
                focus.reconcile(server)


def on_unmap(server: Server, ls: LayerSurface, _data) -> None:
    """Fires when the shell surface stops showing; reclaims its space."""
    was_focused = ls.focused
    ls.focused = False
    if ls.monitor is not None:
        geometry.arrange_layers(server, ls.monitor)
    if was_focused:
        top = focus.top_client(server, ls.monitor)
        if top is not None:
            focus.bump_focus_order(server, top)
    if ls.monitor is not None:
        geometry.reconcile(server, ls.monitor)
    focus.reconcile(server)


def on_destroy(
        server: Server, ls: LayerSurface, _data) -> None:
    """Fires when a shell surface is destroyed (app close, output gone)."""
    ffi, lib = server.ffi, server.lib
    for listener in ls.listeners:
        listener.remove()
    ls.listeners.clear()
    if ls.monitor is not None:
        for bucket in ls.monitor.layers.values():
            if ls in bucket:
                bucket.remove(ls)
                break
        ls.monitor = None
    # wlr_scene_layer_surface_v1's destroy listener fires before ours
    # and frees scene_tree.
    lib.wlr_scene_node_destroy(ffi.addressof(ls.popups_tree.node))

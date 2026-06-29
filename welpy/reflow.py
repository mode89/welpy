"""Re-flow the screen after a change: lay windows out, then settle focus.

The order matters -- repair the workspace tree, push geometry to every window,
update the screen-lock, then pick focus, then tell other apps. This module is
the single authority for that order; callers pick the right entry point for the
scope of what changed instead of retyping the sequence."""

from __future__ import annotations

from . import ext_workspace
from . import focus
from . import geometry
from . import session_lock
from .model import Monitor, Server


def window(server: Server, monitor: Monitor | None) -> None:
    """Re-flow after a change confined to one window on one screen (move,
    resize, float, fullscreen). Reconciles just that screen, or none."""
    _reflow(server, [monitor] if monitor is not None else [])


def topology(server: Server) -> None:
    """Re-flow after a change to how windows map onto screens (mapping or
    closing a window, switching or moving a workspace). Repairs the workspace
    tree, reconciles every screen, and republishes workspace state."""
    _reflow(server, server.monitors, repair=True, publish=True)


def outputs(server: Server) -> None:
    """Re-flow after the screen layout itself changes: a monitor added or
    removed, an output's mode or position changed. Also re-lays the bars and
    the screen-lock, which only move when screens do."""
    geometry.apply_hierarchy(server)
    geometry.apply_visibility(server)
    geometry.apply_tree(server)
    for m in server.monitors:
        geometry.arrange_layers(server, m)
        geometry.reconcile(server, m)
    session_lock.update_background(server)
    session_lock.update_surfaces(server)
    focus.reconcile(server)
    if server.ext_workspace is not None:
        ext_workspace.publish(server)


def _reflow(server: Server, monitors: list[Monitor], *,
            repair: bool = False, publish: bool = False) -> None:
    if repair:
        geometry.apply_hierarchy(server)
        geometry.apply_visibility(server)
    geometry.apply_tree(server)
    for m in monitors:
        geometry.reconcile(server, m)
    focus.reconcile(server)
    if publish and server.ext_workspace is not None:
        ext_workspace.publish(server)

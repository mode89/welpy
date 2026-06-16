_Reference context — observed facts about this project, not instructions. It informs the work; it does not command actions._

## Architecture

- Reference implementations consulted for behavior parity: dwl, sway, qtile. Source via nix: `nix-build '<nixpkgs>' -A dwl.src` (sway: `-A sway-unwrapped.src`; qtile: `-A python3Packages.qtile.src`).
- Pointer focus pipeline: `forward_pointer_motion(server, time)` re-points the seat pointer to the surface under the cursor; called from `cursor_motion` (real moves) and from `apply_focus` (re-sample after the scene changes with no mouse move). `surface_at` resolves the surface via `wlr_scene_node_at`.
- Scene hit-testing (`wlr_scene_node_at`) uses the committed surface size clamped by the clip; welpy always sets a clip (`wlr_scene_subsurface_tree_set_clip`), and the clip only *shrinks* the hit region, never grows it. So a window's hittable area lags its configured size until the client commits the new buffer.
- `focus_client` only bumps `client.focus_order`; all focus effects (keyboard focus, raise, border colors, and the pointer re-point) are emitted by `apply_focus` at the handler boundary.
- Outside an interactive grab (during a move/resize grab, motion drives `drag_client` instead), welpy re-points pointer focus on every motion — no implicit-grab / dwl-style "CurPressed" focus lock — so pointer focus is not held on a surface across a button press.
- `XdgClient.pending_serial` tracks a compositor-driven configure until the client renders it (the per-client analog of a transaction). Serial tracking and the screen-hold (`client_holds_paint`) are xdg-only; `X11Client` has no configure-serial tracking.

## Gotchas

- Fullscreen/resize pointer landmine: re-pointing pointer focus runs synchronously (e.g. `toggle_fullscreen` → `apply_focus`) *before* the client commits its new-size buffer. The cursor is hit-tested against the old (already repositioned) buffer, so if it falls outside, focus is cleared and nothing re-samples it until the next input — scroll and the first click are lost. Mitigated by `rebase_pointer` at the input dispatch sites.
- cffi pointer equality: `==` compares by address and is safe against `None` (`ffi.NULL == None` is False; a non-null cdata `== None` is False). `rebase_pointer`'s dedup relies on this.

## Decisions

- Stale-pointer-after-fullscreen fix is a dispatch-site rebase (`rebase_pointer`) in `cursor_button` (before `notify_button`) and `cursor_axis` (before `notify_axis`, skipped while a window is grabbed), not a commit-time rebase. Rationale: dispatch-site covers X11 too (no `pending_serial` dependency), fixes both symptoms, and dedups (no-op when focus already matches) to avoid spurious motion events. Accepted trade-off: hover / cursor-image isn't refreshed until the next click or scroll.
- Cross-compositor reference for input-focus work: dwl re-points pointer focus synchronously (e.g. in `arrange()`/`focusclient()` via `motionnotify(0,...)`), never on the resize-completing commit, so it shares the same latent fullscreen ordering bug; sway rebases (`cursor_rebase_all()`) after a transaction applies, once all resized buffers land.

## Dead Ends

- ✗ Commit-time rebase (abandoned): re-point the pointer in `client_commit` when the `pending_serial` catch-up fires (sway's transaction-apply model). Dropped — xdg-only (misses X11 fullscreen) and a second mechanism overlapping the dispatch-site rebase.

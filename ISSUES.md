# Issues

Findings from a correctness review of `wel.py`, `bindings.py`,
`ext_workspace.py`, and `libinput.py`. Cross-checked against dwl/sway and the
wlroots 0.19.2 sources. Grouped by severity.

## Bugs

1. **`is not` identity comparison on cffi cdata defeats the focus diff** —
   `wel.py:1583, 1609, 1627`. `target_surface is not current_surface` compares
   Python object identity, but every cdata attribute access returns a *fresh*
   cdata object, so two pointers to the same surface are never `is`-identical.
   The condition is effectively always true and `wlr_seat_keyboard_notify_enter`
   fires on every reconcile pass. Currently harmless only because
   `wlr_seat_keyboard_enter` early-returns on an already-focused surface
   (wlroots `wlr_seat_keyboard.c:237`; the xdg popup grab's enter is a no-op),
   but the stated intent ("emits only the effects needed") is broken. Should be
   `!=` (address comparison), as done correctly elsewhere
   (`node.parent != target`, `client_surface(c) == surface`).

2. **Pointer focus is never reconciled after scene changes** — only
   `forward_pointer_motion` (`wel.py:2224`) updates the seat's pointer focus,
   and it only runs on actual mouse motion. After a workspace switch, window
   unmap/close, layer reflow, or drag end, the seat keeps pointer focus on the
   now-hidden/stale surface; scrolling or clicking without first moving the
   mouse delivers events to an invisible window. dwl calls
   `motionnotify(0, ...)` after every arrange/map/unmap (6 call sites) for
   exactly this. welpy has no equivalent.

3. **`wl_pointer.set_cursor` requests are ignored** — there is no listener for
   the seat's `request_set_cursor` (the binding `welpy_seat_request_set_cursor`
   exists in `bindings.py` but is wired nowhere). Apps can never change the
   cursor image (I-beam over text, resize arrows) or hide it (video players).
   This is core `wl_seat` behavior, distinct from TODO #13's cursor-shape-v1.
   Given the binding already exists, this looks like missed wiring rather than
   deliberate scope.

4. **No reply to `xdg_toplevel.request_maximize`** — xdg-shell requires a
   configure in response to a maximize request; with
   `wlr_xdg_shell_create(display, 3)` clients can't learn maximize is
   unsupported via `wm_capabilities` (v5+), so a client that requests it can
   stall waiting for the configure. dwl's `maximizenotify` schedules an empty
   configure for this reason. The signal accessor
   `welpy_xdg_toplevel_request_maximize` is declared but never used.

## Behavioral / conformance nits

5. **Floating X11 windows can't self-resize** (`x11_request_configure`,
   `wel.py:1028`): once mapped, welpy reasserts its own geometry
   unconditionally. dwl's `configurex11` honors the request for floating
   clients. An X11 dialog that resizes itself fights the compositor forever.

6. **Un-fullscreen request when not fullscreen sends no configure**
   (`client_request_fullscreen`, `wel.py:906`): if `wants=False` and the client
   isn't the workspace fullscreen, neither branch runs, so no configure is
   emitted. dwl always responds via `setfullscreen`. Minor conformance gap.

7. **ext-workspace `output_enter` is only sent at group creation**
   (`ext_workspace.py` phase 2): if a client binds the workspace manager before
   binding `wl_output`, `welpy_extws_output_resource` returns NULL and the group
   is never associated with its output later. Bars binding globals in unlucky
   registry order get output-less groups.

8. **`lock_new` performs side effects before rejecting a duplicate locker**
   (`wel.py:1371`): the background enable, pointer-focus clear, and grab clears
   run even on the early-reject path. Harmless (already locked) but the order is
   misleading.

9. **X11 configure spam**: `set_size` early-outs for unchanged xdg sizes but
   `_configure_x11` has no change check, so every `apply_geometry` sends a
   ConfigureNotify to every visible X11 client.

## Minor / cosmetic

10. `arrange_layers` (`wel.py:1874`): `if new_area != monitor.window_area:
    monitor.window_area = new_area` — the guard is dead (unconditional
    assignment is equivalent), and the docstring's "re-flow client windows if
    anchored bars changed it" is done by callers, not here.

11. `bindings.py:9`: docstring says "required by main.py" — the entry point is
    `wel.py`.

12. Declared-but-unused bindings: `welpy_keyboard_keysym` (incl. its C
    definition), `wlr_seat_get_keyboard`, `welpy_input_device_destroy_signal`,
    `welpy_seat_pointer_focused_client`,
    `wlr_cursor_set_surface`/`warp_closest`/`absolute_to_layout_coords`, the
    whole output-management and idle-notify/idle-inhibit groups,
    `wlr_xdg_toplevel_set_maximized`/`set_bounds`/`set_wm_capabilities`,
    `set_title`/`set_app_id` signals, `BTN_MIDDLE`,
    `wlr_scene_node_place_below`/`coords`, `wlr_output_layout_add`/`get`,
    `wlr_output_test_state`. Most are clearly staged for TODO items 8/9/13, but
    the output-power/maximize/set-cursor ones sit next to half-missing features
    (see #3–4), making the staging ambiguous.

13. `teardown` never destroys the scene, renderer, or allocator (dwl does).
    Exit-time leak only.

14. `apply_geometry` uses truthiness on `fullscreen.scene_tree` while everywhere
    else uses `is not None` — works (cffi NULL and `None` are both falsy) but
    inconsistent.

15. Serial comparisons (`acked >= client.pending_serial`) ignore uint32
    wraparound; practically unreachable.

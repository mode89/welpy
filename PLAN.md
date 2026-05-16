# pywl — Python clone of dwl

Goal: a Python port of [dwl](https://codeberg.org/dwl/dwl) built on the existing `bindings.py` cffi layer.

## Code style

Keep dwl's data model intact (same fields, same lists like `clients` / `fstack` / per-monitor `layers[4]`) so behavior maps one-to-one and the dwl source stays a useful reference. *Within* that structure, aim for idiomatic Python:

- `snake_case` everywhere for names; `PascalCase` for types; `UPPER_SNAKE` for module-level constants.
- Type hints on every function and dataclass field; `from __future__ import annotations` at the top of each module.
- `@dataclass` for state; `Enum`/`IntEnum`/`IntFlag` for closed sets (`CursorMode`, `ClientType`, `Layer`); `IntFlag` for `WLR_MODIFIER_*` combinations if it reads better than raw ints.
- Prefer pure functions and explicit returns over output parameters (`node_at(x, y) -> Hit | None`, not C-style `int *psx, int *psy`).
- Iterate Pythonically: `for client in monitor.clients`, comprehensions, `any` / `all`, `next(..., default)`. No manual `wl_list_for_each` shape.
- Confine cffi at the edges: `ffi.cast` / `ffi.addressof` / `ffi.NULL` only in the thin wrapper functions that touch `lib.*`. Compositor logic above that line should look like normal Python.
- Side effects (spawning processes, touching the seat, mutating wlroots objects) confined to event-handler entry points; helpers stay pure where practical.
- No single-letter names except short loop variables and obvious math (`x`, `y`, `w`, `h`); spell out `client`, `monitor`, `surface`, `geometry`.
- Match the existing `bindings.py` style (the `listen` trampoline, `pywl_*` helpers); don't reinvent it.

## Abstractions

Preserve dwl's abstractions one-to-one as Python `@dataclass`es and `Enum`s, but rename to idiomatic Python (`snake_case` fields, `PascalCase` types, spelled-out names, no Hungarian prefixes). Roughly:

Most structs map directly (`Client` → `@dataclass Client`, `Monitor` → `@dataclass Monitor`, etc.). Non-obvious cases:

```
dwl                                                  pywl
------------------------------------------------------------------------------------------------------------------
Arg (tagged union)                                   plain value (int/float/tuple[str, ...]/Layout); no wrapper
enum { CurNormal, CurPressed, CurMove, CurResize }   class CursorMode(Enum)  (Normal, Pressed, Move, Resize)
enum { XDGShell, LayerShell, X11 }                   class ClientType(Enum)  (XdgShell, LayerShell, X11)
enum { LyrBg, LyrBottom, ... }                       class Layer(IntEnum)  (Background, Bottom, Tile, Float, Fullscreen, Top, Overlay, Block; ordering matters)
```

Field renames follow the same rule (`isfloating` → `floating`, `isfullscreen` → `fullscreen`, `isurgent` → `urgent`, `bw` → `border_width`, `nmaster` → `num_master`, `mfact` → `master_factor`, `mon` → `monitor`, `geom` → `geometry`, `seltags` → `selected_tags`, `tagset` → `tagsets`, `lt` → `layouts`/`layout`, `wlr_group` → `wlr_keyboard_group`).

Global functions get pythonic names too — `snake_case`, spelled out, no run-together words. Most are mechanical (`focusclient` → `focus_client`, `togglefloating` → `toggle_floating`, `arrangelayers` → `arrange_layers`). Non-obvious renames:

```
dwl                pywl
----------------------------------------
setmfact           set_master_factor
incnmaster         inc_num_master
xytonode           node_at
xytomon            monitor_at
dirtomon           monitor_in_direction
focustop           top_client
createnotify       create_client       (xdg new_toplevel; the C name is confusing, the new name says what it does)
```

## Config

All user-tunable settings live in `default_config` function: keybindings, button bindings, rules, layouts, monitor rules, colors, border width, key-repeat timings, libinput options, sloppy-focus toggle, log level, etc.

Config names follow the same pythonic convention as the rest of the codebase (`snake_case`, spelled out, no run-together words). Non-obvious renames from
dwl:

```
dwl                       pywl
----------------------------------------------
borderpx                  border_width
sloppyfocus               sloppy_focus
bordercolor               border_color
focuscolor                focus_color
urgentcolor               urgent_color
rootcolor                 root_color
monrules                  monitor_rules
termcmd                   term_cmd
menucmd                   menu_cmd
MODKEY                    MOD
TAGCOUNT                  TAG_COUNT
```

# Plan

Features below are ranked roughly by criticality. Each tier produces a usable artifact; skipping a tier makes later tiers awkward.

## Tier 0 — "the floor": be a Wayland compositor at all

Without these, nothing renders and no client can connect. (Roughly tinywl, but laid out so later tiers don't have to refactor the foundation.)

1. [DONE] **Event loop & signals** — `wl_display`, `wl_event_loop`, `wl_display_add_socket_auto`, `WAYLAND_DISPLAY` env, run/quit. `sigaction` for SIGINT/SIGTERM (quit), SIGCHLD (reap spawned children so they don't zombie), SIGPIPE (ignore).
2. [DONE] **Backend / renderer / allocator** — `wlr_backend_autocreate`, `wlr_renderer_autocreate`, `wlr_allocator_autocreate`, `wlr_renderer_init_wl_shm`. GPU reset listener on `renderer.events.lost` rebuilds renderer + allocator. (Shm only for now; dmabuf/drm wiring is Tier 4 #45 and reworks this setup.)
3. [DONE] **Core globals** — `wlr_compositor`, `wlr_subcompositor`, `wlr_data_device_manager`.
4. [DONE] **Scene graph & layers** — `wlr_scene`, `root_bg` rect, 8 layer trees (`Layer.Background`, `Bottom`, `Tile`, `Float`, `Fullscreen`, `Top`, `Overlay`, `Block`), `drag_icon` tree placed below `Block`, hidden `locked_bg` rect on `Block`. Every later surface attaches to a specific `Layer`; nothing parents directly to `scene.tree`. Z-order is determined by layer ordering, not by which tier added the surface.
5. [DONE] **Output handling** — new_output (init render, commit preferred mode, add to `wlr_output_layout`, create `wlr_scene_output`), frame → `wlr_scene_output_commit` + `send_frame_done`, request_state, basic destroy. Output destroy is reworked into `cleanup_monitor` (client migration) in Tier 1 #20.
6. [DONE] **xdg-shell** — `wlr_xdg_shell_create` + new_toplevel (attach `wlr_scene_xdg_surface` under `Layer.Tile` or `Layer.Float`), new_popup (parent lookup, `schedule_configure` on initial commit). Per-surface listeners: commit, map, unmap, destroy, `set_title` (the title-change source `print_status` consumes). Per-toplevel: `request_maximize` handler sends empty configure to nak.
7. [DONE] **Seat & cursor** — `wlr_seat` (`seat0`), `wlr_cursor`, `wlr_xcursor_manager`; cursor events: motion rel/abs, button, axis, frame. Seat listeners wired now (handlers fully functional even before the matching protocols arrive in Tier 2): `request_set_cursor` (client-set pointer surface), `request_set_selection`, `request_set_primary_selection`.
8. [DONE] **Keyboard input** — single `wlr_keyboard_group` for all physical keyboards; new_input for a keyboard device just calls `wlr_keyboard_group_add_keyboard`. Group-level listeners for `key` and `modifiers`. xkb keymap from `config.xkb_rules`; repeat info from `config.repeat_rate`/`repeat_delay`. Key-repeat timer for compositor bindings created here so Tier 1 #18 can use it.
9. [DONE] **Spawn** — `spawn()` action (`fork` → `setsid` → dup2 stderr to stdout → `execvp`), `startup_cmd` from CLI. SIGCHLD handler (item 1) reaps children.
10. [DONE] **Focus primitive** — focus a single surface, send keyboard enter, clear focus. No MRU stack yet (added in Tier 1 #17).

End of Tier 0: brings up a screen, lets you launch a terminal, floats windows (all parented to `Layer.Float`). Scene, keyboard-group, and seat-listener shapes already match the rest of the project.

## Tier 1 — what makes it dwl rather than tinywl

"dwm on Wayland": tags, tile layout, borders, multi-monitor.

11. [DONE] **`Monitor` dataclass** — per-output `m`/`w` boxes, layouts, tagsets, `scene_output`, per-layer surface lists. Every later feature hangs off `Monitor`.
12. [DONE] **`Client` dataclass + `clients` / `fstack` lists** — `client_type` (`XdgShell`/`X11`/`LayerShell`), `monitor`, `geometry`/`prev`/`bounds`, `tags`, `floating`/`fullscreen`/`urgent` flags, pending `resize` configure serial. (Note: `urgent` has no source until Tier 2 #29 wires xdg-activation and Tier 5 wires X11 `set_hints`; field exists from Tier 1 so border colors and rules can already reference it.)
13. [DONE] **Tags** — `TAG_COUNT` (=9) tagmask, `tagsets[2]` with `selected_tags` toggle, `view` / `toggle_view` / `tag` / `toggle_tag` actions, `visible_on(client, monitor)`.
14. [DONE] **Layouts** — `Layout` table (`tile`, floating, `monocle`), `set_layout`, layout symbol shown in `print_status`.
15. [DONE] **Tile layout** — `master_factor`, `num_master`, `inc_num_master`, `set_master_factor`, `zoom`.
16. [DONE] **Window borders** — 4 `wlr_scene_rect` per client, `border_width`, focus / urgent / normal colors.
17. [DONE] **Focus model** — `focus_client(client, lift)`, `top_client(monitor)`, `focus_stack(±1)`, MRU `fstack`, raise on focus, urgent clearing on focus.
18. [DONE] **Keybinding table** — `keys` (`mod`, `sym`, `func`, `arg`), `key_binding()` dispatcher, per-binding key repeat via the timer from Tier 0 #8.
19. [DONE] **Button table** — `buttons` (`mod`, `button`, `func`, `arg`); mod+LMB/RMB → `move_resize` grab. `CursorMode` (`Normal` / `Pressed` / `Move` / `Resize`) is queryable from cursor-motion handling so Tier 3 #40 (sloppy focus) can suppress focus changes during grabs.
20. [DONE] **Multi-monitor** — `monitor_rules`, `create_monitor`, `cleanup_monitor` (reworks Tier 0 #5's output-destroy: migrates clients off the dying output, tears down per-monitor scene state), `focus_monitor`, `tag_monitor`, `monitor_in_direction`, `arrange(monitor)`. `update_monitors` listener on `output_layout.events.change` recomputes the overall layout box, runs `arrange` per monitor, and is the hook Tier 2 #30 (xdg-output) and #31 (output-management) consume.
21. [DONE] **Rules** — `rules` matched on `app_id` / `title` → `tags`, `floating`, target monitor; applied at map time.
22. [DONE] **Floating + fullscreen** — `toggle_floating`, `toggle_fullscreen`, `set_fullscreen` (resize to monitor, raise fullscreen-bg rect on `Layer.Fullscreen`, layer change), `request_fullscreen` from client.
23. [DONE] **Resize pipeline** — single `resize(client, geometry, interactive)` that applies bounds, sets scene position, sizes the 4 border rects, sends `wlr_xdg_toplevel_set_size`, tracks configure serial.
24. [DONE] **`update_title`** — the `set_title` listener wired in Tier 0 #6 now updates `Client.title` and calls `print_status` when the focused client's title changes.
25. [DONE] **Clean shutdown** — `quit()` action; `cleanup` + `cleanup_listeners` (symmetric with every `wl_signal_add` in setup).
26. [DONE] **`print_status` IPC** — stdout lines for `selmon` / `title` / `appid` / `tags` / `layout`, called from every state-changing action.

End of Tier 1: daily-driver tiling compositor for someone who lives in a terminal.

## Tier 2 — make it usable as a desktop

Without these you can't have a bar, wallpaper, lockscreen, or proper clipboard.

27. **`wlr_layer_shell_v1`** — `LayerSurface` dataclass, `arrange_layer` / `arrange_layers`, exclusive zones shrinking the monitor's `w` box, popup tree per layer, keyboard interactivity. Required for `waybar`, `swaybg`, `mako`, `wofi`.
28. **`wlr_xdg_decoration_v1`** + **`wlr_server_decoration`** — request SSD by default; suppresses GTK/Qt CSDs.
29. **`wlr_xdg_activation_v1`** — focus-request handler; also the urgency source that drives `Client.urgent` from Tier 1 #12.
30. **`wlr_xdg_output_v1`** — per-output name/description, driven by `update_monitors` from Tier 1 #20.
31. **`wlr_output_management_v1`** + apply/test — needed by `kanshi`, `wlr-randr`.
32. **`wlr_output_power_management_v1`** — DPMS.
33. **`wlr_gamma_control_v1`** — `wlsunset`, `gammastep`; wired via `wlr_scene_set_gamma_control_manager_v1`.
34. **`wlr_primary_selection_v1` manager** — middle-click paste. The seat listener was already wired in Tier 0 #7; this item just adds the protocol global.
35. **`wlr_data_control_v1`** + **`wlr_ext_data_control_v1`** — `wl-clipboard`, `cliphist`.
36. **`wlr_session_lock_v1`** — lock surfaces on `Layer.Block`, enables the `locked_bg` rect created in Tier 0 #4. `swaylock`, `waylock`.
37. **`wlr_idle_notifier_v1`** + **`wlr_idle_inhibit_v1`** — `swayidle`, "inhibit while playing video". Honors `idle_inhibit_ignore_visibility` from `config.py`.

## Tier 3 — input quality

(`wlr_keyboard_group` is no longer here — it's in Tier 0 #8.)

38. **libinput device config** — tap-to-click, tap-and-drag, natural scrolling, accel profile, send-events mode, disable-while-typing, left-handed, click method. Guarded by `wlr_input_device_is_libinput`.
39. **VT switching** — `chvt` action on Ctrl+Alt+F1..F12 via `wlr_session_change_vt`.
40. **Sloppy focus** — focus follows mouse (`sloppy_focus`); skipped when `CursorMode != Normal` (Tier 1 #19) or while a layer surface holds keyboard-interactive focus.
41. **`wlr_pointer_constraints_v1`** + **`wlr_relative_pointer_v1`** — locked/confined pointers, FPS games, Blender. `cursor_warp_to_hint` on constraint commit.
42. **`wlr_virtual_keyboard_v1`** + **`wlr_virtual_pointer_v1`** — `wtype`, `wayvnc`, `ydotool`. Virtual keyboards join the existing keyboard group from Tier 0 #8; virtual pointers attach to `wlr_cursor`.
43. **`wlr_cursor_shape_v1`** — clients set their cursor by shape name (modern alternative to Tier 0 #7's `request_set_cursor`).
44. **Drag-and-drop** — `request_start_drag`, `start_drag`; drag icon attached to the `drag_icon` scene tree created in Tier 0 #4.

## Tier 4 — modern Wayland niceties

Mostly one-liners that unlock client features without much logic. One non-trivial item up front because it reworks Tier 0.

45. **Renderer rework: `wlr_drm` + `wlr_linux_dmabuf_v1` + `wlr_linux_drm_syncobj_v1`** — replace Tier 0 #2's shm-only renderer setup with dwl's full sequence: `wlr_renderer_init_wl_shm` → (if dmabuf-capable) `wlr_drm_create` + `wlr_linux_dmabuf_v1_create_with_renderer` + `wlr_scene_set_linux_dmabuf_v1`; add `wlr_linux_drm_syncobj_manager_v1_create` if both renderer and backend support timelines. Required for explicit sync and modern GL/Vulkan clients.
46. **`wlr_fractional_scale_v1`** — 1.25x / 1.5x scaling.
47. **`wlr_viewporter`**, **`wlr_alpha_modifier_v1`**, **`wlr_single_pixel_buffer_v1`** — surface scaling/cropping, per-surface alpha, solid-color buffers.
48. **`wlr_presentation_time`** — accurate frame timing for clients.
49. **`wlr_screencopy_v1`** + **`wlr_export_dmabuf_v1`** — `grim`, `wf-recorder`, `wayvnc`.

## Tier 5 — optional

50. **XWayland** — `wlr_xwayland`, `Client.surface.xwayland`, override-redirect handling, X-specific listeners (`associate`/`dissociate`/`activate`/`configure`/`set_hints`). Also a second urgency source for Tier 1 #12 (`urgent` flag). Costs a lot of code for a feature many users won't need; defer until everything else works.

---

## Suggested milestones

- **M1 — Floor:** Tier 0. End state: launch a terminal, it floats on `Layer.Float`, can be moved with mod+drag. Scene-layer setup, `wlr_keyboard_group`, and all seat listeners are already in place so later tiers extend rather than refactor.
- **M2 — dwl identity:** Tier 1. End state: tags, tile/float/monocle, borders, multi-monitor (with `update_monitors`), rules, fullscreen, `print_status`. A real tiling compositor.
- **M3 — Desktop:** Tier 2. layer-shell, xdg-decoration, output-management, session-lock, clipboard managers, idle. Usable full-time with `waybar` + `swaylock` + `swaybg`.
- **M4 — Polish:** Tier 3 + Tier 4. libinput config, sloppy focus, pointer constraints, cursor shape, DnD, fractional scale, screencopy, plus the renderer rework for dmabuf.
- **M5 — Optional:** Tier 5. XWayland.

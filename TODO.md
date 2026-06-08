1.  [x] **Console switch** — jump to another virtual terminal with Ctrl+Alt+F1..F12 (`wlr_session_change_vt`).
2.  [x] **Screen names** — per-screen name/description, so apps and bars can tell screens apart (`wlr_xdg_output_v1`).
3.  [x] **Middle-click paste** — primary-selection clipboard (`wlr_primary_selection_v1`).
4.  [x] **Clipboard managers** — privileged clipboard access for `wl-clipboard`, `cliphist` (`wlr_data_control_v1` + `wlr_ext_data_control_v1`).
5.  [x] **GPU rendering** — `wlr_drm` + `wlr_linux_dmabuf_v1` + `wlr_linux_drm_syncobj_v1`: zero-copy GPU buffers and explicit sync for modern GL/Vulkan clients.
6.  [x] **Input tuning** — tune how touchpads and mice feel: tap-to-click, tap-and-drag, natural scrolling, pointer acceleration, disable-while-typing, left-handed, click/scroll method, send-events mode (libinput).
7.  [ ] **Focus on hover** — focus the window under the pointer as it moves, so users switch windows by hovering instead of clicking. Toggled by `sloppy_focus`.
8.  [ ] **Monitor config** — `wlr_output_management_v1`: external tools reconfigure screens (resolution, position, scale, rotation, on/off), so users can set up multi-monitor layouts and switch them per environment (docked, undocked). `kanshi`, `wlr-randr`.
9.  [ ] **Idle handling** — `wlr_idle_notifier_v1` + `wlr_idle_inhibit_v1`: background daemons act on inactivity (dim, lock, suspend), while apps can hold that off when it matters (video playback, presentations). `idle_inhibit_ignore_visibility` (`config.py`) decides whether a hidden app still inhibits. `swayidle`.
10. [x] **Screen lock** — `wlr_session_lock_v1`: a screen-locker takes over every screen and blocks access to running apps until the user authenticates, keeping window contents hidden while locked. `swaylock`, `waylock`.
11. [x] **Screen blanking** — `wlr_output_power_management_v1`: blank screens to save power (DPMS), turning displays off when idle and back on when activity resumes. Pairs with idle daemons.
12. [x] **Night light** — `wlr_gamma_control_v1`: adjust a screen's color curve to warm it at night (reduce blue light) and to calibrate color. `wlsunset`, `gammastep`.
13. [ ] **Themed cursors** — `wlr_cursor_shape_v1`: request a themed cursor by name (resize, text, grab) so the pointer matches the user's cursor theme consistently, without each app shipping its own bitmap.
14. [x] **HiDPI scaling** — `wlr_fractional_scale_v1`: render crisply at in-between scales (1.25x, 1.5x) on HiDPI screens, instead of being forced to 1x or 2x and looking too small or blurry.
15. [ ] **Screen capture** — `wlr_screencopy_v1` + `wlr_export_dmabuf_v1`: capture the screen for screenshots, recording, and streaming, the latter sharing GPU buffers directly for low-overhead capture. `grim`, `wf-recorder`, `wayvnc`.
16. [ ] **Drag and drop** — drag content between apps, with a drag icon that follows the cursor to indicate what's being moved.
17. [x] **Surface effects** — `wlr_viewporter`, `wlr_alpha_modifier_v1`, `wlr_single_pixel_buffer_v1`: common surface optimizations apps rely on: scale/crop a video or buffer to fit, fade a window with per-surface transparency, and fill a solid color cheaply for backgrounds.
18. [x] **Frame timing** — `wlr_presentation_time`: tell apps exactly when their frames appeared on screen, so video players and games can pace rendering smoothly and keep audio in sync with the picture.
19. [ ] **Pointer lock** — `wlr_pointer_constraints_v1` + `wlr_relative_pointer_v1`: capture the pointer (lock it in place or confine it to a region) and read raw, unaccelerated motion, so first-person games and 3D tools get smooth look/rotate. FPS games, Blender.
20. [ ] **Input injection** — `wlr_virtual_keyboard_v1` + `wlr_virtual_pointer_v1`: inject keyboard and pointer input for automation, accessibility, and remote desktop, as if from a real device. `wtype`, `wayvnc`, `ydotool`.
21. [x] **X11 apps** — XWayland: run legacy X11 apps (older toolkits, some games) that don't speak Wayland, by embedding an X server. Also a second source of urgency hints.

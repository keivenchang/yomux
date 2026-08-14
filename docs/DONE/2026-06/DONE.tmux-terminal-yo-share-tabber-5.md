# Tmux, Terminal, YO!share, and Tabber

## YO!share wrapped text/layout parity
- Fixed the recurring share-view indentation/wrap drift class by making the bundled UI/mono fonts block fallback-font layout, waiting for YOLOmux font metrics before the first app render, and remeasuring layout-sensitive surfaces after fonts settle. Preferences autosized textareas, tab strips, session buttons, terminals, and CodeMirror panels now get a shared post-font refresh instead of keeping stale fallback-font geometry.
- Extended the share geometry digest with a generic `textWraps` bucket for native controls and wrapped text surfaces such as Preferences textareas/help text, menu labels, tabs, Finder/Differ rows, YO!info rows, and Tabber labels. Wrapped text/control drift now reports as `textWraps`, so screenshot mismatches caused by local browser wrapping no longer hide behind `mirror ✓`.
- Follow-up fix: first render now waits for the actual font-ready promise instead of a 2.5s timeout race, so the host should not repaint/re-autosize a few seconds after load. Full UI-state snapshots also carry host wrapped-control metrics, and viewers apply host app-space width/height to native settings textareas before scroll replay; this keeps Preferences controls clipped/expanded exactly like the host instead of letting the viewer choose a different native textarea height.
- `docs/specs/GUI.md` now defines typography/wrapped-control parity as a YO!share contract for future widgets.

## YO!share Preferences scroll, YO badge, and viewer status cadence
- Fixed the remaining Preferences/share mismatch by adding current host scroll targets to full YO!share UI snapshots and replaying them after semantic pane state renders. Scroll frames also merge into the server share record, so late-joining viewers and geometry resync no longer fall back to a stale/top Preferences position.
- Fixed the topbar `Tabs` badge and tmux menu YO state on share viewers by carrying compact host auto-approval state in the share UI snapshot and publishing a UI-state frame when YO is toggled or refreshed. Read-only viewers still cannot toggle YO, but their chrome now computes from the host state.
- Confirmed editor refresh for share viewers is push-driven: host saves publish `file-version` frames over the share UI WebSocket, not a high-frequency viewer poll. The read-only viewer `/api/share` status fetch is now a 30s backup for banner metadata while the local countdown still updates every second.

## YO!share modal border polish
- The YO!share modal boundary now uses a normal one-pixel mixed border without the extra green left stripe, and the active-share `Users` list is an inline subsection with row separators instead of a nested framed table. `docs/specs/GUI.md` records that Users must not draw a second card border inside the active share row.
- Verification: `python3 tools/static_build.py`, `python3 tools/static_build.py --check`, `python3 -m pytest tests/test_browser_layout.py::test_share_modal_users_section_is_inline_not_nested_card -q`, full `python3 tools/check.py` (`CHECK PASSED in 14.74s`), and dev1 restart/smoke on port 8001 (`ping: 401 0.122186s`).

## YO!share users list and YO!info mirror follow-up
- YO!share active-share rows now show a `Users` section with one row per distinct connected browser: connected duration, IP address, and summarized browser type. Viewer accounting is registered on the share UI socket and remains refcount-compatible with terminal websockets, so one browser tab is still one viewer even when the layout opens multiple mirrored panes.
- YO!info is now part of the host-owned share snapshot and scroll bus. Branch-table sort column/direction publishes with the UI state, `#info-content` horizontal and vertical scroll frames apply on clients, and read-only local scroll restores to the last host-authored position instead of snapping the client to its own top/left.
- `docs/specs/GUI.md` and `README.md` record the new user-list and YO!info mirror contracts. Verification: `python3 tools/static_build.py`, `python3 tools/static_build.py --check`, `node tests/layout_url.test.js`, focused backend/share browser tests including `tests/test_browser_layout.py::test_share_readonly_info_sort_and_horizontal_scroll_are_host_owned`, full `python3 tools/check.py` (`CHECK PASSED in 14.54s`), and dev1 restart/smoke on port 8001 (`ping: 401 0.048765s`).

## YO!share resize repaint and mirrored chrome follow-up
- Fixed the post-layout terminal blanking race from the 2026-06-13 screenshots. When a share viewer reattaches to an existing shared terminal upstream, the server now requests a bounded `tmux refresh-client`; when the host terminal size changes, the server broadcasts `host-resize` before requesting the repaint so clients resize/reset before the fresh terminal bytes arrive instead of clearing the just-repainted buffer.
- Host layout commits now schedule a full UI-state snapshot after the layout frame, and the mirrored state now includes chrome that was previously browser-local: tab metadata visibility and the active YO!info/YO!agent sub-tab. Per-editor diff expand/collapse overrides now publish immediately, so expanding unchanged regions on the host updates read-only share clients without waiting for an unrelated snapshot.
- The no-regression plan is visual, not only source-grep: `tests/test_browser_layout.py::test_share_readonly_diff_scroll_and_popup_mirror_are_host_owned` renders a real CodeMirror diff in share view and asserts host-owned diff expansion, YO!agent sub-tab, tab metadata visibility, popup mirrors, and read-only scroll restoration. `tests/layout_url.test.js` covers the share snapshot/apply contract, and `tests/test_server_query.py` covers viewer reattach refresh plus resize/repaint ordering.

---

Completed 2026-06-13. Extracted from the 2026-06-13 daily log.

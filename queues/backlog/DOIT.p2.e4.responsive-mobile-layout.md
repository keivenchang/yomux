# DOIT.p2.e4.responsive-mobile-layout.md - Add A Usable Mobile Focus Mode

## Goal

Provide a single-pane mobile focus mode with touch-friendly navigation, larger controls, upload/paste affordances, and enough editor/diff behavior for remote check-ins.

## User-Blocking Failures Reported 2026-08-15

- Opening a Preview on a mobile device makes scrolling feel broken: the viewport snaps backward after the user scrolls, and continued finger scrolling keeps losing position to repeated snap-backs.
- An xterm terminal on mobile accepts no typing at all. Tapping the terminal does not produce usable keyboard input, so the primary agent-control surface is unusable from the device.

Treat these two red reproductions before the broader mobile layout work below. Do not infer the device/browser, active layout, scroll owner, or event owner from desktop behavior; record the exact mobile environment and the exact URL/layout/tabs state that reproduces each failure.

## Existing Owners To Audit

- Root mobile viewport and keyboard geometry live in `static_src/js/yolomux/10_core_utils.js`, including `visualViewport` resize/scroll handling and layout refits.
- Persisted pane/editor/preview scroll state and restoration live in `static_src/js/yolomux/20_layout_state.js`.
- Preview zoom hydration can schedule later `scrollTop` writes in `applyPreviewZoomSurface`; split editor/preview scroll ownership and reflected writes live in `static_src/js/yolomux/92_codemirror_editor.js`.
- Mobile terminal focus, the hidden xterm textarea, native terminal data, the accessory keyboard, and touch routing share the terminal runtime ending in `static_src/js/yolomux/99_terminal_boot.js`. Preserve that shared transport instead of adding a mobile-only input sender.

## Plan

- [ ] Reproduce the Preview snap-back on the reported mobile device/browser with the exact URL, layout, tabs, orientation, viewport dimensions, `visualViewport` state, active pane, preview type, and scroll owner recorded. Capture one touch sequence from Preview open through at least two backward jumps, with timestamped `scrollTop`, layout revision, render/hydration generation, viewport events, and every product-owned scroll write so the first conflicting writer is named before any fix.
- [ ] Fix Preview touch scrolling through one scroll-ownership invariant: after the user claims a native vertical gesture, no stale layout restore, preview hydration/fit frame, split-scroll reflection, async renderer completion, or viewport refit may write an older position over it. Audit every sibling Preview kind and edit/preview/split transition through the same owner, including Markdown, HTML, image, Mermaid, code preview, asynchronous content growth, rotation, and software-keyboard geometry.
- [ ] Add a deterministic browser regression that opens Preview, begins a real touch scroll before deferred render/layout work settles, releases the delayed work, and proves the finger-directed position never moves backward unless the gesture reverses. Assert the actual scroll owner and geometry over multiple animation frames; a final screenshot or final `scrollTop` alone cannot catch repeated snap-back.
- [ ] Reproduce mobile xterm's total input failure through the real UI path. Prove whether a plain terminal tap focuses xterm's hidden textarea, whether the software keyboard can open, which `touch`/`pointer`/synthetic-mouse/default-prevention path wins, and whether `beforeinput`, composition, keydown, and paste reach the existing terminal transport. Record the first event or state transition that diverges; accessory-key unit tests do not prove native typing works.
- [ ] Restore native xterm typing without breaking terminal finger-pan ownership: an unclaimed tap must focus the hidden textarea and accept text, composition/IME, Backspace, and Return; a claimed vertical pan must scroll without opening the keyboard; horizontal/multi-touch cancellation, long-press selection, the 350 ms synthetic-mouse suppression latch, and the movable accessory palette must retain their specified behavior. Route all emitted bytes through the existing terminal input/acknowledgement owner.
- [ ] Add exact mobile browser coverage for terminal tap-to-focus and native input before and after a finger pan, Preview open, pane switch, accessory palette open/close, keyboard viewport resize, and rotation. Assert the hidden textarea's focus, terminal bytes, rendered echo, stable pane height, and absence of duplicate input; include real-device acceptance because desktop device emulation does not prove that iOS or Android opened its software keyboard.
- [ ] Freeze current phone and tablet geometry, focus, keyboard, touch, terminal, editor, diff, menu, upload, and pane-switch behavior with real viewport tests.
- [ ] Add one responsive state owner for pane focus and navigation; preserve desktop layout and URL restoration.
- [ ] Implement touch-sized controls and mobile editor/diff/upload flows without duplicating desktop actions.
- [ ] Update `docs/specs/GUI.md` with the Preview touch-scroll ownership rule and mobile xterm native-input transition, keeping the existing terminal input-routing matrix as the shared contract.

## Done Criteria

- [ ] Opening any supported Preview and repeatedly finger-scrolling it on the reported device/browser produces no product-driven backward jump, including while deferred content, layout, and viewport work settles.
- [ ] A terminal tap opens the device keyboard and exact text, composition/IME, Backspace, Return, and paste bytes reach the PTY once; finger-pan, long-press selection, accessory keys, and desktop terminal input retain their existing behavior.
- [ ] The regressions fail on the captured pre-fix behavior, run through real touch/input events rather than direct helper calls, and identify the single scroll owner and terminal-input owner that enforce convergence.
- [ ] Phone/tablet browser matrices prove pane navigation, terminal input, editor/diff inspection, upload/paste, focus, safe-area, rotation, and desktop parity.
- [ ] Generated assets, accessibility checks, the canonical gate, and real-device or device-emulation acceptance pass on one HEAD.
- [ ] `python3 tools/static_build.py`, the owning Node shards through `node tests/layout_url.test.js`, focused browser tests, `python3 tools/static_build.py --check`, and `python3 tools/check.py` pass; the active 7771 server is restarted with `YOLOMUX_START_LOAD_WAIT_SECONDS=30`, and a hard-reloaded authenticated mobile browser shows no console or rejected-promise errors.

## Gotchas

- Do not hide the Preview defect with debouncing, sleeps, retries, forced smooth scrolling, or by deleting all scroll restoration. Name the stale writer and invalidate it when touch ownership or render generation changes.
- Do not make the whole terminal surface `preventDefault`, focus the hidden textarea during a finger pan, or bypass xterm's input path with a second mobile transport. Those shortcuts trade the typing failure for broken scrolling, selection, IME, or duplicate bytes.
- A synthetic unit call to the mobile accessory keyboard proves only its byte mapping. Acceptance must exercise a real terminal tap and browser input events against the rendered xterm textarea.

## Completion

Record the exact devices/browsers, reproducing URL and state, conflicting event/write owners, focused tests, canonical gate, restart identity, and real-device evidence in `docs/DONE/`; update user-facing mobile instructions if the interaction changes, then delete this queue.

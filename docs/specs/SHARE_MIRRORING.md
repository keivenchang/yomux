# YO!share Mirroring Architecture

This spec defines the current YO!share mirroring model and the migration path away from the old semantic mirror. The goal is not just fewer bugs; it is removing the recurring class where the host and viewer run separate renderers and slowly drift apart.

## Decision

Read-only and write YO!share viewers use host-rendered DOM replay by default. The host browser is the only YOLOmux UI renderer for shared visual state. The host sends sanitized `#appRoot` keyframes and coalesced incremental DOM/style/scroll/pointer deltas to viewers. Viewers replay those frames into an inert mirror surface and do not run normal YOLOmux UI handlers for mirrored app chrome. Terminal panes remain protocol-based: the DOM replay contains terminal placeholders, while viewer xterms are mounted from the existing host-sized tmux byte stream and host rows/cols.

Semantic sharing remains only as a temporary debugging bridge for the legacy read-only path. The normal path does not accept viewer-authored semantic layout, Finder, editor, or popup state. Write viewers send validated input intents and wait for host replay frames for visual feedback. The temporary semantic escape hatch is `shareReplay=0`, `shareSemantic=1`, bootstrap `shareReplay: false`, or `localStorage.yolomux.shareReplaySemantic=1`; it is for migration diagnostics, not a product mode.

## Why The Current Semantic Mirror Keeps Failing

The current implementation sends structured state such as `layout`, `tabs`, `finder`, `editor`, `preferences`, `scroll`, `popup-layer`, `pointer`, terminal dimensions, and geometry digests. The viewer then runs its own YOLOmux renderer and tries to recreate the same visual result.

That design is fragile because each new visible surface needs all of these pieces to stay complete:

- A snapshot field in the host UI-state payload.
- A server storage/merge path for late join and repair.
- A viewer apply path that disables local inference.
- A drift/digest bucket that names the right mismatch.
- A repair action that actually resets the mismatched bucket.
- A Selenium fixture that exercises the real browser geometry.

Miss any one piece and the viewer invents local state. The recent recurring failures came from this exact shape: Finder re-docked after host minimize, YO!info rows came from local scoped metadata, Differ refs lagged, editor cursors used stale CodeMirror state, Preferences scroll snapped to local defaults, host popups resurrected after close, modal boxes reflowed, Safari text metrics moved tab strips by a pixel, and Dockview remounts left xterms bound to stale containers.

The semantic mirror also turns security and transport into drift sources. Token redaction, HTTP share carve-outs, route allowlists, expiry redirects, write-mode blocking, and viewer-count accounting all have to stay aligned with the UI-state model. A missing allowlist route becomes a visible `TypeError: Load failed`; a missing redaction path leaks a share secret; a stale expiry path shows different end states across browsers.

## Current Model

The model is a browser-native replay stream, similar to video keyframes plus delta frames, but with structured DOM instead of pixels.

- Keyframe: a complete sanitized snapshot of the host `#appRoot`, plus mirror metadata such as epoch, viewport, asset versions, font readiness, active terminal placeholders, scroll anchors, and redaction version.
- Delta frame: a coalesced batch of DOM mutations, attribute changes, text changes, style changes, scroll changes, pointer presence, selection/caret mirror state where safe, and popup layer changes.
- Epoch: a monotonic number assigned by the host whenever the replay base changes. Viewers drop frames from old epochs.
- Repair: if a viewer misses frames, falls behind, fails a digest, reconnects, or joins late, the server/host sends a new keyframe for the current epoch.
- Terminal stream: xterm data remains separate from DOM replay. A terminal placeholder in the DOM identifies `{session, rows, cols, terminalEpoch}`; the viewer mounts or rebinds an xterm instance and consumes the existing share terminal byte stream.

This is the MPEG analogy: periodic I-frames reset the exact rendered base, P-frames carry incremental changes, and a new I-frame is sent when the stream cannot be trusted.

## Why DOM Replay Is Better Than Semantic Replay

| Criterion | Semantic Replay | DOM Replay |
| --- | --- | --- |
| Mirror fidelity | Depends on every widget exposing complete semantic state and viewer code rendering identically. | Mirrors the host-rendered DOM directly; a widget that appears in `#appRoot` is captured by default unless explicitly excluded. |
| New UI surfaces | Every new control needs snapshot/apply/digest/test work. | Most new controls mirror automatically as DOM; only interactivity, redaction, and high-churn exclusions need registration. |
| Browser differences | Host and viewer can choose different native layout, font fallback, wrapping, textarea height, and Dockview measurement. | Host layout is serialized after it happened; viewer replays host DOM and host-owned dimensions. |
| Repair model | Generic resync can still miss the bucket that drifted. | Replay repair sends a fresh keyframe for the exact rendered base. |
| Read-only safety | Viewer still has a live YOLOmux app and must block many local handlers. | Viewer app is inert for mirrored DOM; local mutation paths are absent or outside the mirror. |
| Debugging | Drift buckets explain symptoms but require per-widget interpretation. | Compare replay epoch/frame id, DOM digest, terminal epoch, and excluded surface registry. |
| Bandwidth | Small for narrow semantic state. Can grow as fields are added. | Larger keyframes, but deltas are coalesced and keyframes are event-driven. |
| Implementation risk | Already exists, but every fix adds another field and another branch. | Bigger rewrite, but it deletes the cause of recurring mirror drift for read-only clients. |

DOM replay is better for YO!share because the product promise is host-following, not independent collaborative editing. The host has already rendered the correct view; the viewer should not rerender it from secondhand state. Semantic state remains useful for host-side business logic and legacy diagnostics, but it should not be the visual transport.

## Why Not Pixel Streaming

Pure screenshot/video streaming gives the closest literal pixels, but it is the wrong default for YOLOmux:

- Terminal and editor text should remain selectable and copyable where possible.
- Browser accessibility and native text search should not be thrown away for the whole app.
- Full-frame video would burn bandwidth on mostly static developer UI.
- Pixel streaming makes redaction and per-surface policy harder; DOM replay can exclude or sanitize specific nodes.
- YOLOmux already has a good terminal byte stream, so rendering terminal video would duplicate a stronger protocol.

Pixel streaming can be a fallback diagnostic mode later, but not the primary architecture.

## Why Not CRDT Or Full Collaborative State

CRDT-style state sync is useful when multiple clients are co-editing the same data model. YO!share read-only is different: viewers should follow the host, not become equivalent local editors. A CRDT or shared semantic state model preserves the two-renderer problem. It can make model data consistent while DOM layout, popups, native controls, fonts, browser rounding, and Dockview lifecycle still differ.

Write-mode uses input forwarding: a writer sends an intent to the host/server, the host mutates the real UI/session, and all clients receive host replay. That keeps one renderer authoritative even when input comes from another browser.

## Precedents To Borrow From

- rrweb records a DOM snapshot into a serializable structure and replays later DOM mutations. YO!share should borrow the snapshot/delta discipline, node ids, mutation batching, and replay isolation, but adapt it for live streaming, security redaction, and terminal placeholders. Reference: https://github.com/rrweb-io/rrweb and https://github.com/rrweb-io/rrweb/blob/main/docs/observer.md.
- Apache Guacamole proves a browser can be a thin remote-display client while the server owns protocol translation and fanout. YO!share should borrow the thin-client mindset and backpressure discipline, not its exact display protocol. Reference: https://guacamole.apache.org/doc/gug/guacamole-architecture.html.
- noVNC proves a web client can render remote interactive sessions over a browser transport. YO!share should borrow the separation between remote display state and local browser shell. Reference: https://github.com/novnc/noVNC.
- Visual Studio Live Share separates access control, shared terminals, and editor/session following. YO!share should borrow the explicit host-owned access model and avoid implying that read-only viewers own local layout. Reference: https://learn.microsoft.com/en-us/visualstudio/liveshare/overview/features.

## Stream Architecture

The share UI WebSocket carries replay frames alongside share status, replay-control frames, and write-mode input intents.

```text
host browser
  renders normal YOLOmux
  observes #appRoot
  sanitizes and serializes snapshots/deltas
  publishes frames with share id + epoch + sequence

server
  stores latest keyframe per active share
  stores bounded recent delta ring per epoch
  fans frames to viewers with backpressure handling
  requests keyframe resend when a viewer cannot catch up
  keeps terminal upstreams separate

viewer browser
  boots replay shell
  receives keyframe
  builds inert mirror DOM
  receives deltas
  applies deltas in sequence
  mounts xterm placeholders from terminal stream
  sends validated input intents in write mode
  requests keyframe on gap, digest mismatch, or replay error
```

## Frame Types

Replay frames should be explicit and versioned.

```json
{
  "type": "dom-keyframe",
  "version": 1,
  "shareId": "abc123",
  "epoch": 42,
  "sequence": 1000,
  "createdAt": 1781420000.123,
  "viewport": {"width": 2349, "height": 1228},
  "assets": {"js": "hash", "css": "hash", "fonts": "hash"},
  "root": {"nodeId": 1, "tag": "div", "attrs": {"id": "appRoot"}, "children": []},
  "terminals": [{"placeholderId": "term-ph-8001", "session": "8001", "rows": 28, "cols": 106, "terminalEpoch": 7}],
  "scroll": [{"nodeId": 55, "top": 441, "left": 0}],
  "redaction": {"policyVersion": 3, "removedCount": 4},
  "digest": "sha256:..."
}
```

```json
{
  "type": "dom-delta",
  "version": 1,
  "shareId": "abc123",
  "epoch": 42,
  "sequence": 1001,
  "baseSequence": 1000,
  "mutations": [],
  "scroll": [],
  "pointer": {"x": 812, "y": 404, "visible": true},
  "terminalPlaceholders": [],
  "digest": "sha256:..."
}
```

Other required frame types:

- `dom-keyframe-request`: viewer asks for a fresh keyframe with reason `join`, `gap`, `digest`, `replay-error`, `backpressure`, `topology`, or `manual-debug`.
- `dom-keyframe-ack`: viewer confirms keyframe applied and reports DOM digest plus terminal placeholder map.
- `dom-replay-error`: viewer reports failed mutation, missing node id, unsafe attribute, digest mismatch, or xterm placeholder failure.
- `terminal-host-resize`: existing terminal host dimensions, kept separate from DOM mutation frames.
- `share-status`: existing viewer count/expiry/status frame, outside the mirrored DOM.

## Snapshot Boundaries

The keyframe root is `#appRoot`, not the whole document. Viewer-only chrome stays outside it:

- Share banner.
- Fit controls.
- Debug controls.
- Login/expired/share-ended screens.
- Browser-local error UI.

Host-local/private surfaces are excluded or sanitized:

- Share URLs and tokens.
- Any token-bearing field or link.
- Latency meter values.
- Browser-local debug state.
- Native file picker state.
- Password inputs and secret-like values.
- Elements marked with a future `data-share-redact` or `data-share-private`.

The snapshot must include enough style state for replay:

- Class names and inline styles after sanitization.
- Adopted stylesheet identity or a stable CSS asset hash.
- Host viewport dimensions and app-space transform metadata.
- Font readiness and bundled font fingerprint.
- Scroll positions for scrollable mirrored nodes.
- Text selection/caret state only for surfaces explicitly allowed by policy.

## Mutation Capture

The host recorder should observe `#appRoot` with `MutationObserver` and a small set of direct event hooks:

- Child list changes.
- Attribute changes, with an allowlist/denylist sanitizer.
- Character data changes.
- Scroll events on registered mirrored scroll containers.
- Pointer move/click presence for host pointer.
- ResizeObserver events for app root and terminal placeholders.
- Font readiness and theme/language asset changes.

Frames should be coalesced per animation frame or a short debounce window. High-frequency surfaces should have explicit policies:

- The host maintains a mirrored-node registry for the current replay base. A keyframe resets the registry to exactly the nodes serialized in that keyframe; child-list deltas extend or prune it. Attribute and character-data deltas must only target nodes in that registry, and character-data deltas require the exact text node, not only a mirrored parent. Mutations for detached, never-serialized, private, volatile, or ignored nodes are skipped or escalated to a topology keyframe instead of sending impossible node ids that make viewers enter a `viewer behind` loop.
- Terminal content is excluded from DOM deltas and handled by terminal streams.
- CodeMirror content can be mirrored by DOM replay for read-only visibility, but large editor updates should coalesce and can force a keyframe if mutation volume is too high.
- Cursor blink, timers, spinner animation state, and transient measuring nodes should be excluded or normalized.
- Popups are no longer a special semantic layer after replay migration; they are normal DOM inside `#appRoot` unless redaction policy excludes them.

## Epoch Rules

The host increments the replay epoch and sends a keyframe when the base DOM cannot be safely patched by deltas:

- Viewer joins or reconnects.
- Pane resize, split, minimize, restore, or Dockview group rebuild.
- Tab move, close, activate, or reparent across panes.
- Finder/Differ/Tabber root or mode change.
- Theme, language, font, tab-width, pane-spacing, or preview-mode change.
- Terminal host rows/cols change.
- Popup/modal ownership changes in a way that invalidates old node ids.
- Delta ring overflow.
- Viewer reports a missing sequence, digest mismatch, or replay error.
- Server backpressure drops queued frames.
- Periodic safety keyframe fires.

Periodic keyframes should be low frequency and adaptive, not every second by default. A reasonable initial policy: keyframe on join/topology/drift/backpressure, plus a 30-60 second safety keyframe while active viewers exist. Tune with measured keyframe size and mutation rate.

## Viewer Replay Shell

Share viewers load a replay shell, not the full interactive YOLOmux app. The shell needs:

- Share authentication and expiry handling.
- A viewer banner outside the mirror.
- Keyframe/delta WebSocket handling.
- DOM rebuild/apply engine.
- Sanitized event blocker for mirrored DOM.
- Terminal placeholder manager.
- Fit transform and cover/contain controls.
- Debug panel with epoch, sequence, DOM digest, frame size, dropped frames, keyframe requests, and terminal placeholder status.

The replayed DOM must be inert:

- No app event handlers from host HTML.
- No script execution from replayed DOM.
- No `javascript:` URLs.
- No unsanitized inline event attributes.
- No form submission or local mutation handlers.
- Harmless text selection and copy remain allowed where the browser permits it.

The viewer should apply frames only when `epoch` and `sequence` are valid. A gap means request a keyframe. A digest mismatch means request a keyframe and preserve the failing debug report. A digestless mutation targeting an unknown node is treated as a stale mutation from a replaced subtree: the viewer advances the replay cursor, increments stale-frame diagnostics, stays `mirrored`, and waits for the next scheduled keyframe instead of creating a `viewer behind` loop during topology churn.

Repair requests must be rate-limited. A broken viewer or repeated digest mismatch can otherwise create a denial-of-service loop: viewer asks for repair, host serializes a large DOM keyframe, viewer misses or rejects it, and the cycle repeats. Viewers keep one keyframe repair in flight and back off repeated requests; hosts coalesce repeated `dom-keyframe-request` messages and serialize at most one full DOM keyframe per throttle window. Debug health exposes request/keyframe suppression counts so this failure mode is visible before it melts the host or server queues.

## Terminal Placeholder Model

Terminals should stay outside DOM replay for content and lifecycle.

The host DOM contains a placeholder node:

```html
<div data-share-terminal-placeholder="8001" data-rows="28" data-cols="106"></div>
```

The viewer replay shell replaces or overlays that placeholder with a local xterm instance:

- The xterm rows/cols come from host terminal dimensions.
- The byte stream comes from the existing one-upstream-per-session share terminal fanout.
- Viewer resize changes only the mirror transform, never host tmux dimensions.
- Host terminal resize sends `terminal-host-resize`, resizes viewer xterm to host rows/cols, and requests/reuses the bounded tmux repaint path without clearing an already-painted viewer buffer. Any reset must happen before terminal bytes arrive or be paired with a guaranteed repaint.
- If a DOM keyframe moves the placeholder, the xterm instance rebinds to the new connected placeholder without reconnecting unnecessarily.
- If a DOM delta removes and re-adds the placeholder under another pane, the delta carries terminal placeholder metadata and the viewer rebinds the same xterm instance after applying child-list mutations.
- If the placeholder disappears, the viewer detaches or hides the xterm for that session.

This keeps terminal text protocol-correct and avoids serializing xterm's internal DOM/canvas state.

## Write Mode

Write mode uses the same replay shell as read-only mode and avoids a second renderer:

- Writer clicks/types in the replay shell.
- The shell converts allowed actions into input intents, such as terminal input, terminal paste, scroll intent, or explicit host UI command.
- The server validates the share mode, transport, target session, and action. Write mode requires HTTPS.
- Terminal input and paste are applied through the existing share terminal upstream so latency stays close to the old write path.
- Viewer-authored semantic frames such as `layout`, `ui-state`, `popup-layer`, Finder state, and editor state are rejected on `/ws/share-ui`.
- All clients receive host replay frames.

Do not let write clients locally mutate mirrored DOM and then reconcile later. That recreates the current drift class.

## Server Responsibilities

The server should not render YOLOmux DOM. It should coordinate host-authored frames:

- Authenticate share host and viewers.
- Store latest keyframe per share id.
- Store bounded delta ring per share id and epoch.
- Broadcast frames to UI clients and terminal viewers.
- Track per-viewer acked epoch/sequence.
- Drop or disconnect slow viewers using existing queue pressure rules.
- Request host keyframe when the server lacks a usable base for a viewer.
- Redact logs and debug output.
- Keep share status, expiry, and viewer accounting outside mirrored DOM.

If the host disconnects, viewers should enter a host-disconnected state. The server may keep the last keyframe for a short grace period, but it must not pretend the mirror is live.

## Security Requirements

DOM replay makes sanitization mandatory.

- Token-bearing strings and URLs are redacted before frames leave the host.
- Inline event attributes are dropped.
- Script/style injection is blocked unless the source is a known YOLOmux static asset.
- Dangerous URL schemes are removed.
- Password/secret inputs are omitted or replaced with placeholders.
- Nodes marked private are excluded.
- Read-only viewers cannot dispatch app mutations into the replayed DOM.
- Write viewers can only send validated input intents; they cannot publish semantic UI state.
- HTTP read-only shares keep the same route and write restrictions as today.
- Debug frame dumps use the same redaction path as transport frames.

Redaction must happen before server fanout. Server-side redaction remains a defense-in-depth layer, not the first line of protection.

## Performance And Backpressure

The design is bandwidth-feasible because developer UI is mostly static and terminal output is already separated.

Controls:

- Coalesce DOM mutations per animation frame.
- Compress WebSocket frames if available and safe.
- Use keyframes on topology changes, not on a fixed high-rate timer.
- Maintain a bounded delta ring.
- Drop high-churn cosmetic mutations.
- Normalize blinking cursors/timers.
- Exclude terminal internals.
- Send a keyframe instead of a huge delta batch when mutation volume crosses a threshold.
- Coalesce viewer repair requests and host keyframe responses so a replay failure cannot turn into a keyframe storm.
- Disconnect or downgrade slow viewers using existing high-water behavior.

Metrics to collect:

- Keyframe byte size.
- Delta byte size and rate.
- Mutation count by surface.
- Keyframe frequency by reason.
- Viewer replay latency.
- Dropped frame count.
- Stale frame count, separate from dropped frames. A stale frame means an old epoch, already-applied sequence, or digestless unknown-node mutation was ignored; it must not put the viewer into `viewer behind`.
- Digest mismatch rate.
- Terminal placeholder rebind count.

## Digest And Debugging

Read-only replay health checks replace the legacy semantic geometry digest assertions:

- `domDigest`: stable digest of sanitized replay DOM, excluding terminal internals and known volatile attributes.
- `terminalDigest`: session, rows, cols, terminal epoch, socket state, and repaint status.
- `frameDigest`: epoch, sequence, and delta ring continuity.
- `redactionDigest`: policy version and excluded-node counts.

Normal viewer UI does not show internal bucket names like `slots` or `textWraps`. It shows user-facing replay status such as `mirrored`, `resyncing`, `host disconnected`, or `viewer behind`. Debug copy exposes sanitized replay health with DOM digest, epoch, sequence, dropped-frame count, stale-frame count, keyframe request count, redaction policy version, node count, terminal placeholder health, and `lastReplayError`. Keyframe requests and `lastReplayError` must include enough frame detail to explain a repair: frame type, reason, error text, `epoch`, `sequence`, `baseSequence`, expected sequence/base, current epoch, last applied sequence, digest when present, and frame byte size.

## Migration Plan

Status on 2026-06-14: Phases 0-5 are implemented. Default read-only and write share viewers use DOM replay, terminals mount through placeholders, replay health replaces read-only geometry drift, and write clients route terminal input through validated intents. Phase 6 is cleanup, docs, and enforcement.

Phase 0: strengthen semantic bridge until replay exists. Done.

- Add a monotonic layout/replay epoch to semantic `ui-state` frames.
- Make topology changes publish complete snapshots.
- Make drift repair bucket-specific.
- Keep the current Selenium regressions for Finder, Dockview, editor, popup, HTTP share, and expiry.

Phase 1: build replay keyframes behind a flag. Done.

- Add host `#appRoot` serializer.
- Add sanitized keyframe frame type.
- Add viewer replay shell that can render one static keyframe.
- Exclude terminal internals and show placeholders.
- Add debug digest over sanitized DOM.

Phase 2: add live deltas. Done.

- Add MutationObserver capture under `#appRoot`.
- Add delta frame type and sequence handling.
- Add delta ring and keyframe request path.
- Add scroll and pointer replay.
- Add backpressure-triggered keyframe resend.

Phase 3: integrate terminals. Done.

- Map terminal placeholders to viewer xterm instances.
- Rebind xterm on keyframe/delta movement.
- Keep host rows/cols authoritative.
- Verify host resize and tmux repaint ordering.

Phase 4: replace read-only semantic view. Done.

- Route read-only `/share/<id>` to replay shell.
- Remove read-only semantic apply paths that are no longer used.
- Keep a temporary semantic escape hatch for diagnostics.
- Move popup/menu/modal mirroring into normal DOM replay.

Phase 5: migrate write mode to input forwarding. Done.

- Convert writer UI actions into host/server intents.
- Keep host as the only UI mutator.
- Reuse DOM replay for visual feedback.

Phase 6: cleanup and enforcement. In progress.

- Delete dead semantic snapshot fields.
- Add a `static_build.py` or node guard that prevents new mirrored surfaces from bypassing replay/redaction registration.
- Update docs and Selenium tests around replay health rather than per-widget semantic parity.

## Tests

Required test coverage:

- Unit tests for sanitizer/redaction against share URLs, fragments, tokens, inline handlers, dangerous URL schemes, password fields, and private nodes.
- Unit tests for keyframe serialization and rebuild of representative `#appRoot` DOM.
- Unit tests for delta apply ordering, missing node behavior, stale epoch drop, missing sequence keyframe request, and digest mismatch.
- Browser test for a late same-epoch or old-epoch delta after a newer keyframe. It must stay `mirrored`, increment stale-frame diagnostics, and not request another keyframe.
- Browser test for read-only viewer join applying a keyframe with Finder, editors, Preferences, menus, and terminal placeholders.
- Browser test for pane resize/minimize/tab move producing a new epoch keyframe and no viewer-local Finder resurrection.
- Browser test for popup open/close replay without stale resurrection.
- Browser or Node test for replayed tab/menu popovers after keyframe and child-list rebuilds. Serialized replay DOM does not keep original app event listeners, so read-only replay must not create viewer-local hover geometry; the host must publish popovers as replayed DOM with app-space `left/top/width/height`, and browser tests must compare the full host/client popup rectangle.
- Browser test for terminal placeholder move/rebind keeping xterm connected and host rows/cols unchanged.
- Browser test for HTTP read-only share using replay shell and still loading allowed readonly data/terminal streams only.
- Browser test for slow-viewer/backpressure requesting a keyframe instead of showing partial DOM.
- Browser test for write-mode terminal input forwarding: writer input sends one `input-intent`, does not send raw `/ws/share-view` input, and mirrored DOM changes only after a host replay frame.
- Live-server browser tests that need an admin host only to create a share should use `auth_bypass=True` on `start_browser_share_server(...)`. Tests for login redirects, 401/403 behavior, cookies, roles, or share-token scoping must keep real auth enabled.
- Source guard for replay/redaction registration: private surfaces, terminal containers, token-bearing fields, and app-visible popup hosts must be registered with sanitizer or replay exclusions.

Legacy semantic mirror tests remain only for the explicit escape hatch until that path is deleted. A replay test must fail if the viewer runs normal app mutation handlers for mirrored DOM.

## Open Questions

- Whether to implement a small in-house serializer or vendor/adapt a proven rrweb-style serializer.
- Whether keyframes should be host-pushed on a fixed safety cadence or only requested by server/viewers after join/topology/drift/backpressure.
- How much CodeMirror DOM should be replayed directly versus represented as a bounded text/editor placeholder during early phases.
- Which host UI commands should be promoted from protocol constants to user-facing write-mode controls beyond terminal input/paste/scroll and keyframe requests.
- How long the server should retain last keyframes after host disconnect.

## Success Criteria

The rewrite is successful when a read-only viewer no longer needs per-widget semantic fields for normal visual parity. A new host-visible control inside `#appRoot` should mirror by default through DOM replay, require only redaction/exclusion review, and not need a bespoke `shareFooStateSnapshot()` plus `applyShareFooState()` pair. Pane resize, Finder minimize, tab move, editor mode changes, popup open/close, theme/language changes, and terminal remounts should converge by applying host keyframes and deltas, not by adding another local geometry patch.

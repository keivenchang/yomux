# Build, Refactor, Docs, and Tests

## DOIT.81 refactor audit follow-ups
- Completed and removed `DOIT.81.md`. Terminal copy now has one action contract for menu, shortcut, DOM copy-event, URL, tmux-selection, and OSC52 paths; labels/status strings are localized and selection cleanup consistently clears the xterm/browser/OSC52 visible-selection state after copy consumption.
- Browser timing defaults now read server-provided settings defaults through the existing `clientSettingsDefaults` parent instead of duplicating refresh/timing literals in bootstrap and settings reload code; the Node and Selenium fixtures now seed production-shaped settings defaults.
- Finder/Differ row derived state now has one builder/applier shared by full render and lightweight status refresh. The regression mutates changed-file and indexed-directory payloads, then proves the refreshed row matches a fresh full render for classes, status/title, agent slot, count slot, and display name.
- Light-mode YO!agent bubbles/details/action/code blocks, vanilla editor swatches, and command-palette/shortcuts dialog surfaces now route through existing panel/line/light editor tokens plus new `--lt-code-block-*` neutral code-block tokens instead of per-component raw hex copies.
- Verification: `node tests/layout_url.test.js`, focused Selenium checks for terminal selection cleanup, menu hover defaults, light-mode surfaces, focused reruns of unrelated intermittent full-gate failures, and final `python3 tools/check.py` for each item, with the final gate passing after R4.

---

Completed 2026-06-16. Extracted from the 2026-06-16 daily log.

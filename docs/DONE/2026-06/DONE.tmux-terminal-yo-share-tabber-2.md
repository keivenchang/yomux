# Tmux, Terminal, YO!share, and Tabber

## Terminal copy while Claude is running (OSC 52 clipboard bridge + right-click highlight)
- Root cause of "copy does nothing while Claude runs": while a TUI owns the mouse, a drag never makes an xterm selection — the copied text leaves the app as an OSC 52 clipboard escape. tmux's default `set-clipboard external` DROPS application OSC 52, and even when forwarded, xterm.js ignores OSC 52 without a registered handler. Verified empirically on a scratch tmux 3.4 server (`external` drops, `on` forwards) and end-to-end on the live server (OSC 52 from a pane reached the attached client PTY and decoded to the exact text).
- Fix: `bridge_tmux` ensures `tmux set-option -s set-clipboard on` before each attach (idempotent, self-healing); new `installTerminalOsc52Bridge` registers an OSC 52 parser handler that decodes base64 UTF-8 and writes the browser clipboard through one shared chain (`writeTerminalTextToClipboard`: synchronous `copy` event first, then async `navigator.clipboard`). `?` read-queries are consumed unanswered (no clipboard exfiltration).
- Shortcuts: Cmd-C/Ctrl-C copies the xterm/browser selection; Cmd-C with no selection is swallowed with a how-to-select hint (Option/Shift-drag) and never reaches Claude; plain Ctrl-C with no selection still sends SIGINT; explicit tmux copy-mode copy is Cmd-Option-C (Mac) / Ctrl-Alt-C (PC) plus a right-click menu item.
- Right-click no longer clears the terminal highlight: a capture-phase right-mousedown captures the selection and stops xterm from clearing it; the context-menu Copy uses that captured text.
- Opt-in copy-path instrumentation behind storage key `yolomux.debugCopy`.
- Verification beyond the standard gate: live-confirmed by the user (copy while Claude running, Cmd-C on an Option-drag selection, SIGINT fallthrough, right-click highlight, tmux-copy shortcut/menu); node OSC 52 decode/handler/clipboard + right-click + SIGINT guards; full pytest 481 passed.

---

Completed 2026-06-10. Extracted from the 2026-06-10 daily log.

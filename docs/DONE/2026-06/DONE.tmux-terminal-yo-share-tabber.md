# Tmux, Terminal, YO!share, and Tabber

## Session project follows the live pane cwd, not the session-number default (DOIT.32)
- `cd`-ing a tmux pane into a different repo didn't change the displayed project — `candidate_session_cwds` listed the static session-number default workdir (session "1" → `~/dynamo/dynamo1`) FIRST, so `session_git_inventory` returned it before ever reaching the pane's live `cd`'d path. Reordered so the focused pane's live cwd (then agent/other-pane cwds) wins, and `session_workdir`/`numbered_session_workdir` are FALLBACKS used only when no pane/agent sits in a repo (a fresh shell in home still shows the dynamoN convenience default). Now the project/branch/PR follow the pane within one metadata poll. pytest covers the live-cwd-wins ordering + the fallback.

## Dark terminal: light-on-white agent composer forced readable (DOIT.30)
- The Codex composer (light text on an ANSI-white input box) was white-on-white in the DARK terminal theme because `terminalMinimumContrastRatio` returned 1 (no adjustment) for dark — so xterm left the ~1:1 cell alone. (Light already used 4.5, which is why a light terminal auto-darkened it.) Confirmed the white box is real Codex's own composer, not the local mock renderer. Fix: dark now returns a moderate 3:1 floor — enough to force the composer to a readable foreground, low enough that intentionally-dim dark-palette text (already ≥3:1) is mostly untouched; light stays at the stricter 4.5. Node guard updated (dark 1→3).

---

Completed 2026-06-03. Extracted from the 2026-06-03 daily log.

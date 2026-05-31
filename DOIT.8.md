# DOIT.8 — forward product features (moved from DOIT.7, 2026-05-30)

Forward features moved out of `DOIT.7.md` so DOIT.7 can stay focused on codebase health/refactor. Do after the near-term UI and refactor batches are stable. Dev checkout only. Validate `python3 -m pytest tests` + `node tests/layout_url.test.js`; restart `:7778` via `systemd-run`; bump version on commit.

NOTE: the summarizer (Tier 1) shares its per-session log scanner with the AFCA observability follow-up. Build that scanner once; whichever lands first owns it.

## Tier 1 — GLOBAL stateful summarizer (marquee feature; researched in depth)
- [ ] **Stateful incremental summarizer (M2), per-session + global roll-up.** Background loop maintains a rolling per-session summary and rolls them into one GLOBAL "what needs attention / who's blocked / what each is doing" overview; surface it (dedicated summary tab / read-only wall / top-bar line). Build on the existing AI-summary plumbing: `codex_summary_prompt` (`yolomux_lib/transcripts.py` ~134) + `app.py` ~429, `/api/summary-stream`, `SUMMARY_CODEX_MODEL`/EFFORT/SERVICE_TIER (`common.py`). Today's path (`run_codex_summary`, `server.py` ~682) is STATELESS one-shot (`codex exec --json --ephemeral -`, re-sends the whole window every tick).
  - **Use M2 (incremental), not M1/M3.** Research finding: M2 wins (~6.5x fewer tokens, fastest); M1 (re-send window) is the slow/pricey baseline; M3 (`codex exec resume`) is worst (replays the whole conversation). Persist `{rolling_summary, last_processed_offset}` per session; each tick feed only `[prior summary] + [new transcript lines since last_offset]` -> small input, fast/cheap output. Global view = cheap roll-up of the per-session summaries.
  - **Provider selection (cheapest/fastest).** Support BOTH Claude and Codex (one may be unavailable). Claude -> Haiku (`claude-haiku-4-5`); Codex -> gpt-5.x low/fast. A setting picks which to use when both are present; auto-pick the only one available; if neither, the summarizer feature is simply unavailable.

## Tier 2 — YOLO maturity (next layer after the rule-engine fixes)
- [ ] **Per-session YOLO policy modes.** Initial: `off`, `prompt-only`, `safe`, `edit`, `full`; make the active mode visible on the per-session `YO` control. (TODO P1.)
- [ ] **Concrete risk labels** everywhere decisions surface: `read`, `edit`, `network`, `process`, `delete`, `credential`, `unknown`.
- [ ] **Approval queue view** for pending high-risk actions — start read-only if live interception is hard.
- [ ] **Rule scoping dimensions:** global vs per-repo vs per-session, per-agent (`claude`/`codex`), and per prompt-type (`bash`/`file`/`tool`). (Schema A is shipped; this adds the overlay/precedence layer — see TODO "Option C: profiles + scope overrides".)

## Tier 3 — longer roadmap (pull individually from TODO.md when reached)
- [ ] Launch & Resume (P4); Worktrees (P5).
- [ ] Search/Cost/History (P6); Mobile & Network use (P7).
- [ ] Host & process vitals (P8); Multi-machine connector (P9).

Note: many smaller Preferences / File Explorer / Transcript items remain in TODO.md; promote them into a DOIT only when they're the next focused batch.

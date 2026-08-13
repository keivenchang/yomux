# macOS Codex transcript detection and YO!stats token history

- Completed macOS Codex transcript detection. Darwin now uses bounded `lsof` lookups for the process cwd and open rollout file, while Linux keeps the `/proc` path. Cwd fallback selects the newest-mtime matching rollout, fixing resumed Codex sessions whose old rollout filename continues receiving new output. The rollout lookup is cached for 15 seconds with a one-second `lsof` timeout.
- Added a dedicated server-aggregated Agent tokens/min history for wide graph ranges: two-minute points for 4–8 hours and five-minute points for 16–24 hours. Client latency, API/SSE, bandwidth, and narrow token views keep their existing data paths.
- Verified a live macOS `7777:%0` Codex agent resolves to the exact `lsof` rollout and reports 223991 generated tokens. Rebuilt static assets, restarted `bash boot.sh 7777`, confirmed it listens on `7777`, and `/api/ping` returns the expected auth-gated `401`. Focused Python tests (375), browser layout tests (132), static check, diff check, and the repository check gate pass.

---

Completed 2026-06-29. Extracted from the 2026-06-29 daily log.

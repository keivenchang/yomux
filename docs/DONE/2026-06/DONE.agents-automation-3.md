# Agents and Automation

## DOIT.62 agent CLI PATH self-heal and dev1 restart owner
- Completed and removed `DOIT.62.md`. Server startup now self-heals `PATH` with `YOLOMUX_EXTRA_PATH` and an existing `~/.local/bin`, agent availability probes report `unavailable_reason: "not-on-path"` for missing Claude/Codex CLIs, the tmux menu renders that reason, and CLI startup logs one warning per missing agent. Added `tools/yolomux-restart-dev1.sh` as the documented dev1 restart owner and verified the live 8001 process restarted with `/home/keivenc/.local/bin` on `PATH`.
- Verification for this archive entry was focused rather than the full standard gate: `python3 tools/static_build.py`, `python3 -m pytest tests/test_workdir.py -q` (9 passed), `node --check static/yolomux.js`, `node tests/layout_url.test.js`, `bash -n tools/yolomux-restart-dev1.sh`, `tools/yolomux-restart-dev1.sh --print-command`, live restart of 8001, `curl -sk -o /dev/null -w "ping: %{http_code} %{time_total}s\n" https://localhost:8001/api/ping` (`401`), and `/proc/<pid>/environ` PATH inspection.

---

Completed 2026-06-11. Extracted from the 2026-06-11 daily log.

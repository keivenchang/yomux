# Agent status ball reduced-motion pulse

- Completed and removed `/tmp/DOIT.balls_not_pulsating_research.md`. The reduced-motion freeze was a CSS specificity split: status balls kept `animation-name` on a high-specificity dot selector but inherited timing from the low-specificity `.heartbeat-pulse`, so `prefers-reduced-motion` reset the ball duration to `0s` while the `attention` pillbox kept pulsing. The pulse cadence now lives on the shared high-specificity `.status-indicator.heartbeat-pulse` parent, generated `static/yolomux.css` is rebuilt, and `test_status_balls_keep_ask_pill_pulse_cadence_under_reduced_motion` proves working/attention balls keep the same nonzero `attention-ring-fade` duration, delay, timing, and running/pending animation state as the `attention` pillbox under reduced motion. `docs/specs/GUI.md` already records the contract: status balls remain separate from the static agent symbol and must keep the same cadence as `attention` under `prefers-reduced-motion: reduce`.

---

Completed 2026-06-24. Extracted from the 2026-06-24 daily log.

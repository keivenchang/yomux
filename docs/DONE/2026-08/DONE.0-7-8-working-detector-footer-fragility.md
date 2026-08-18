# v0.7.8 Working/footer detector completion

Completed the Working/footer queue after separating live activity from incidental client chrome. Real Codex 0.147.0 captures at 120, 100, 90, 80, 70, 60, and 50 columns did not reproduce the predicted narrow-pane failure, so the work was explicitly re-scoped to the measured general defect: an unrecognized trailing footer could silently cancel a valid Working row.

One structured Working verdict now owns the result, discard reason, evidence line, and row position. Unknown footer shapes are non-authoritative; recognized shell prompts, assistant completion, generic choices, and AskUserQuestion remain authoritative. Live Working outranks sticky historical questions, while current questions outrank stale `Goal blocked`; blocked and idle screens retain discarded-Working diagnostics.

The landing gate exposed one sibling false-positive at the same detector owner: Claude's startup model row `Opus 4.8 (1M context) with xhigh effort` was parsed as a live one-minute counter. Commit `0400dbe8a` prevents context and token capacities from becoming elapsed durations. Focused parser coverage passed 11/11, and the two exact YO!agent E2E journeys that had failed in the integrated gate passed individually.

Composed landing evidence followed Keiven's reduced evidence policy. The 25%-CPU integrated gate passed static, compile, syntax, whitespace, all Node shards, non-browser pytest, the 887.61-second browser lane, and timing-sensitive serial; the two red E2E nodes were fixed and rerun alone instead of repeating every lane. Certification-only then certified 7/7 units on qualified clean SHA `3be481152` in 103.17 seconds. Raw certification evidence is under `/tmp/yolomux-v078-3be481152-certification`.

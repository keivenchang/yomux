# 2026-07-09 YO!agent timeline ordering

- YO!agent now routes persisted messages, streaming messages, and the synthetic current-activity/Recent-agents snapshot through one timestamp-ordered timeline. A newer answer can no longer render above an older activity snapshot merely because the snapshot was appended as special chrome after the transcript.
- The regression reproduces an older persisted answer, a 7:02:38 PM activity snapshot, and a 7:11:52 PM answer, then proves the visible order is chronological with the newest answer last.

# 2026-07-20 Zoomed Reset click reliability

- Completed and removed `DOIT.yostats-reset-replaces-range-label.md`. Zoomed graphs place `Reset zoom` first where `Range:` normally appears, suppress the redundant prefix, and retain a compact domain label. The actual reset handler now waits for a completed click; it no longer rebuilds the toolbar on pointerdown and swallows the activation.
- Verification: focused browser geometry/click coverage, static freshness, the niced eight-lane gate (82.51s), guarded 7772 `/api/ping` 401, and real-device operator confirmation.

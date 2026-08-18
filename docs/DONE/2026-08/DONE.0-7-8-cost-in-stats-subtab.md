# 2026-08-16 Cost moved into YO!stats

Removed the standalone YO!cost surface and made the existing cost descriptor the Cost subtab immediately after Graphs in YO!stats. The final order is `Graphs | Cost | API/SSE | Daemons | Logs`; the unrelated Cost chart tab inside Stats Current remains separate.

Legacy `cost`, `yocost`, `yo!cost`, `yo-cost`, and `__yocost__` layout, URL, and popout references now migrate through one owner to YO!stats with Cost selected. The Cost selection persists through reload, deactivation retires its pricing-status timer, and hidden Cost renders do no polling or computation. `debug.tab.cost` exists in all nineteen source locales and the generated locale assets are current.

Authenticated Chrome against restarted 7771 PID 4130557 opened a legacy `sessions=cost` URL, rendered the five labels in order, persisted Cost through reload, rewrote both legacy identifiers to `__debug__`, produced no standalone panel, and stopped the timer after switching to Graphs; `/tmp/yolomux-cost-live-7771.json` retained zero severe console or app errors and served bundle SHA-256 `f7ae64d9ae614d4f03578291aab6c4f1145315cfbe85c3aa589f5a4ffa846d56`. All nineteen Node shards passed, all nine functional lanes passed at `1406566f9`, and clean `26651ec37` passed all seven exact-SHA certification units.

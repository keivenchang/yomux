# Releases

One evidence page per shipped version. Each page records what that release actually carried and what it shipped without — it is a landing record, not a re-verification. Clearly labeled post-release addenda may record later documentation or operational findings, but they do not redefine the immutable tag contents. The durable per-queue evidence lives in [`../DONE/`](../DONE/README.md) and the `DONE.<desc>.md` files it indexes.

| version | tag | release commit | evidence | note |
| --- | --- | --- | --- | --- |
| 0.7.15 | `v0.7.15` | `v0.7.15^{commit}` | [v0.7.15-evidence.md](v0.7.15-evidence.md) | tmux control-client lifecycle ownership, roster metadata convergence, and preserved topbar refusal state and tooltip ownership |
| 0.7.14 | `v0.7.14` | `v0.7.14^{commit}` | [v0.7.14-evidence.md](v0.7.14-evidence.md) | local-service identity and lease safety, deterministic gate ownership, and YO!stats generation correctness |
| 0.7.13 | `v0.7.13` | `v0.7.13^{commit}` | [v0.7.13-evidence.md](v0.7.13-evidence.md) | corrects watchd health, YO!stats graph/ring and roster convergence, bounded Codex hot-tail fairness, and session-files request ownership |
| 0.7.12 | `v0.7.12` | `v0.7.12^{commit}` | [v0.7.12-evidence.md](v0.7.12-evidence.md) | backend lifetime ownership, Stats correctness and responsiveness, and shared daemon-health severity with a documented row-refresh lag |
| 0.7.8 | `v0.7.8` | `f32ffd898` | [v0.7.8-evidence.md](v0.7.8-evidence.md) | mobile and Stats GUI, bounded background work, macOS compatibility, correctness, and safety fixes |
| 0.7.7 | `v0.7.7` | `fb58cac56` | [v0.7.7-evidence.md](v0.7.7-evidence.md) | complete Markdown and specs overhaul with 331 open requirements preserved in concrete queues |
| 0.7.6 | `v0.7.6` | `35907e6f3` | [v0.7.6-evidence.md](v0.7.6-evidence.md) | bounded tmux mutations, retained window strips, filtered Quick Open, and reduced quiet polling |
| 0.7.5 | `v0.7.5` | `6c59437f8` | [v0.7.5-evidence.md](v0.7.5-evidence.md) | retired collaboration removal, runtime/auth cleanup, and graduated Finder interactivity |
| 0.7.4 | `v0.7.4` | `cbd398ccd` | [v0.7.4-evidence.md](v0.7.4-evidence.md) | behavior-preserving architecture cleanup, fully gated and accepted on 7771 |
| 0.7.3 | `v0.7.3` | `482e59162` | [v0.7.3-evidence.md](v0.7.3-evidence.md) | originally shipped without a tag; tagged during the signed history rewrite |
| 0.7.2 | `v0.7.2` | `63b0b244a` | [v0.7.2-evidence.md](v0.7.2-evidence.md) | shipped by waiver on a red full gate |
| 0.7.1 | `v0.7.1` | `a4209e0fb` | [v0.7.1-evidence.md](v0.7.1-evidence.md) | accepted, tagged, live |
| 0.7.0 | `v0.7.0` | `bba47f31e` | [v0.7.0-evidence.md](v0.7.0-evidence.md) | evidence page audits an earlier rejected candidate, not the rewritten shipped commit |

Verified 2026-08-18 against the local annotated tag objects, `origin/main`, and the direct and peeled origin tag refs in `yolomux.dev7771`.

## Historical note

- **0.7.3 originally shipped without a tag.** The current origin now carries signature-bearing annotated `v0.7.3` on rewritten release commit `482e59162`; its evidence page retains the original deployment history.

# Releases

One evidence page per shipped version. Each page records what that release actually carried and what it shipped without — it is a landing record, not a re-verification. The durable per-queue evidence lives in [`../DONE/`](../DONE/README.md) and the `DONE.<desc>.md` files it indexes.

| version | tag | release commit | evidence | note |
| --- | --- | --- | --- | --- |
| 0.7.4 | `v0.7.4` | `0d0af221a` | [v0.7.4-evidence.md](v0.7.4-evidence.md) | behavior-preserving architecture cleanup, fully gated and accepted on 7771 |
| 0.7.3 | **missing** | `7bf385828` | [v0.7.3-evidence.md](v0.7.3-evidence.md) | shipped and deployed, never tagged |
| 0.7.2 | `v0.7.2` | `926e4a166` | [v0.7.2-evidence.md](v0.7.2-evidence.md) | shipped by waiver on a red full gate |
| 0.7.1 | `v0.7.1` | `9a960fc2c` | [v0.7.1-evidence.md](v0.7.1-evidence.md) | accepted, tagged, live |
| 0.7.0 | `v0.7.0` | `b8e0f61de` | [v0.7.0-evidence.md](v0.7.0-evidence.md) | audits a **rejected** candidate; not evidence for a shipped release |

Verified 2026-08-13 against the local signed annotated tags, `origin/main`, and the peeled origin tag refs in `yolomux.dev7771`.

## Open

- **0.7.3 has no tag.** `git tag -l 'v0.7*'` returns `v0.7.0 v0.7.1 v0.7.2`. The release commit exists and was deployed to 7770 and 7771, so the version is unreachable by tag. Either tag `7bf385828` retroactively or record why not.

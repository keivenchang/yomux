# YOmux documentation

| Document | Covers |
| --- | --- |
| [README](../README.md) | What YOmux is and a quickstart |
| [Usage guide](USAGE.md) | Requirements, first launch, daily use, sharing, accounts, and remote access |
| [Development](DEVELOPMENT.md) | Build, tests, local servers, and releases |
| [YO!agent](YOAGENT.md) | Skills, context files, and send safety rules |
| [UI contract](specs/GUI.md) | Required user-interface behavior |
| [HTML ViewEdit](specs/HTML_VIEWEDIT.md) | Plan and test matrix for editing rendered HTML |
| [Backend architecture](specs/BACKEND_ARCHITECTURE.md) | Processes, storage, credentials, and filesystem rules |
| [Go backend status](../GO_MIGRATION.md) | Capabilities, routes, retired Python features, and open gaps |
| [Contributing](../CONTRIBUTING.md) | Contribution terms and the pre-review gate |

## Release history

Git tags are the authority for exact release contents and historical gate results. These summaries group changes by release family.

- **0.7.x** (`v0.7.0` to `v0.7.35`; there is no `v0.7.27`): filesystem and Git views, indexing, YO!stats, OpenCode support, and later Markdown editing.
- **0.8.x** (`v0.8.1` to `v0.8.10`, with gaps): Markdown editing, Finder and Differ responsiveness, terminal behavior, and refresh cost.
- **0.9.x**: the start of the Go migration. `v0.9.1` is the first tag with the Go backend; `v0.9.4` adds Git history and cache, editor, and release-renderer fixes. These releases still had an explicit Python fallback.
- **0.10.x**: unified Normal and Advanced Tabber window rows, New Chat and per-window kill actions, consistent agent status, terminal refresh sizing and Finder cancellation fixes, and no recursive controls in YO!stats pop-outs. 0.10.6 also keeps distinct session identities, rejects obsolete Lookback responses, keeps mobile minimize controls compact, reports unavailable macOS memory observations, refreshes provider pricing with durable retry state, and requires Python 3.11 or newer.
- **0.11.x**: invitations with scoped file and terminal access, collaborative source editing, and guest chat; live file reviews and merging of external edits; Graphviz and SQLite previews; Office conversion on Linux; synchronized TeX source and PDF previews; and improvements to Finder moves, Repo Diff, and Tabber work context.

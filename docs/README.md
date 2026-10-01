# YOLOmux documentation

The Git tags are the source for exact release contents and historical gate results. The summaries below group changes by release family and do not certify current deployments.

- [Development and operations](DEVELOPMENT.md)
- [YO!agent](YOAGENT.md)
- [User-interface contract](specs/GUI.md)
- [Backend architecture](specs/BACKEND_ARCHITECTURE.md)
- [Go capabilities and migration gaps](../GO_MIGRATION.md)

## Release families

- **0.7.x:** added filesystem/Git views, indexing, YO!stats, OpenCode support, and later Markdown editing; see tags `v0.7.0`–`v0.7.35` (there is no `v0.7.27`).
- **0.8.x:** focused on Markdown editing, Finder/Differ responsiveness, terminal behavior, and refresh cost; tags run from `v0.8.1` through `v0.8.10` with gaps.
- **0.9.x:** began the Go migration. `v0.9.1` is the first tag containing the Go backend; `v0.9.4` includes Git-history/cache, editor, and release-renderer fixes. Go is now the default for managed server launches. The deprecated Python server is a temporary explicit fallback planned for removal around late October 2026.

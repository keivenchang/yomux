# 2026-07-19 Repository structure

- Completed and removed `DOIT.repo-structure.md`. The root tools split separates agent clients from build/gate tooling; browser helper and fixture support files sit beside their consumers; JS partial prefixes are unique and explicit bundle order preserves dependencies; obsolete pointer stubs and orphan diagnostics are removed.
- `yolomux_lib` is grouped into approval, chat, integrations, observability, tmux, search, workspace, and infra packages. The server-side application modules remain flat, while identity-preserving legacy aliases retain public imports, package metadata, and old module entrypoints.
- Verification beyond the standard gate: focused structural suites passed 364, 310, and 394 tests across the three package waves; niced canonical gates passed in 75.66s, 75.93s, and 77.69s; every wave restarted guarded leader 7772 and HTTPS `/api/ping` returned 401. The final documentation link and canonical-path sweeps are clean.

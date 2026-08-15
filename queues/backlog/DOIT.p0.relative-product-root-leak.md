# DOIT.p0.relative-product-root-leak.md - A Relative Product Root Lands Wherever cwd Happens To Be

**p0 for 0.7.6.** Found 2026-08-15 while auditing stray directories in `$HOME`. YOLOmux had written two product roots directly into the user's home directory, including a TLS private key.

## The evidence

`~/state` (1.4M) and `~/runtime` (40K) existed in `$HOME` with unmistakably YOLOmux contents:

```
~/state/tls/self-signed.crt
~/state/tls/self-signed.key                       <-- private key in $HOME
~/state/hosts/c072ea4a72c149e693e37a87dd172d59
~/state/c072ea4a72c149e693e37a87dd172d59/tmux-AI-status.json
~/runtime/services/{statusd,jobd,statsd,approvald}.lock
~/runtime/background-owner/owner.json
~/runtime/server-leases/c072ea4a72c149e693e37a87dd172d59
```

That host id matches this machine's `current_host_identity().stable_host_id`, so this was written by a real run here, not copied in. All timestamps were frozen at 2026-08-04 21:10-21:13, nothing was listening on any socket beneath them, and neither live server referenced them (7770 uses the default `~/.local/state/yolomux`; 7771 runs under `YOLOMUX_ROOT=/tmp/y1776734304/p7771`). They have been moved to `/tmp/home-cleanup-20260815/` pending this fix.

## Root cause: the guard exists on one branch only

`yolomux_lib/infra/root_paths.py:55`

**Rooted branch** (`YOLOMUX_ROOT` set) resolves to absolute and enforces containment, via `resolved_path()` and `rooted_override()` at `:39`:

```python
candidate = resolved_path(configured) if configured else default          # .expanduser().resolve()
if not candidate.is_relative_to(root):
    raise YolomuxRootError(f"{key} resolves outside YOLOMUX_ROOT: {candidate} ...")
```

**Unrooted branch** (`:58-66`) does neither. It calls `.expanduser()` and stops — no `.resolve()`, no absoluteness check, no containment:

```python
Path(values.get("YOLOMUX_STATE_DIR", str(Path.home() / ".local" / "state" / "yolomux"))).expanduser(),
Path(values.get("YOLOMUX_CACHE_DIR", str(default_cache_home / "yolomux"))).expanduser(),
Path(values.get("YOLOMUX_CODEX_HOME") or values.get("CODEX_HOME") or str(Path.home() / ".codex")).expanduser(),
```

A relative value survives as relative and is resolved against **whatever the process cwd is at use time**.

Reproduced:

```
$ YOLOMUX_STATE_DIR=state YOLOMUX_RUNTIME_DIR=runtime  ->  resolve_yolomux_roots(...)
state_dir  : 'state'    absolute? False
runtime_dir: 'runtime'  absolute? False
```

Two divergent copies of one rule: the rooted path validates, the unrooted path does not. This is the same defect shape as the `CODEX_HOME` redirection closed in 0.7.5 — one product-root resolver with two behaviors depending on which branch you enter.

Absent `~/cache` and `~/codex` alongside `~/state` and `~/runtime` indicate the trigger was relative `YOLOMUX_STATE_DIR`/`YOLOMUX_RUNTIME_DIR` values with cwd at `$HOME`, not `YOLOMUX_ROOT=~` (which would have created all four).

## Why p0

- A **TLS private key** was written to `$HOME` rather than a mode-scoped state directory.
- A product root that depends on cwd is not reproducible: the same command run from two directories writes two different states, and neither is discoverable.
- Nothing failed loudly. The server ran, wrote its state somewhere unintended, and no error was ever surfaced.

## Plan

- [ ] Reproduce it end to end before changing code: launch a server with a relative `YOLOMUX_STATE_DIR` from a chosen cwd and confirm the product root materialises there. The unit-level reproduction above shows the resolver returns a relative path; confirm a real run actually writes to it.
- [ ] Resolve every product root through one shared parent that always yields an absolute path. The unrooted branch must use the same `resolved_path()` the rooted branch uses. Delete the second copy of the rule rather than adding a matching check beside it.
- [ ] Reject a relative product root explicitly instead of silently accepting it. A relative `YOLOMUX_STATE_DIR`, `YOLOMUX_CACHE_DIR`, `YOLOMUX_RUNTIME_DIR`, or `YOLOMUX_CODEX_HOME` should raise `YolomuxRootError` naming the variable and the value, the way `rooted_override` already does for an escaping path.
- [ ] Decide deliberately whether `$HOME` itself is an acceptable product root and write the decision down. If it is not, refuse it for the same reason a relative path is refused.
- [ ] Add a regression test per root variable asserting that a relative value is refused and that an absolute value is honoured, and one asserting a resolved root never varies with process cwd.
- [ ] Audit for other leaked roots on this box and in the dev worktrees before closing. `/tmp/home-cleanup-20260815/` holds the recovered copies for comparison; do not delete it until this queue is done.

## Done Criteria

- [ ] A relative value for any product-root variable fails fast with a message naming the variable, verified by test.
- [ ] Exactly one resolver produces product roots; the rooted and unrooted branches share it, proven by a test that the same variable is treated identically with and without `YOLOMUX_ROOT` set.
- [ ] A resolved product root is identical when the same configuration is resolved from two different working directories.
- [ ] No product state is written under `$HOME` outside `~/.config/yolomux`, `~/.local/state/yolomux`, and `~/.cache/yolomux` during a normal run, confirmed by a live restarted server rather than by test alone.
- [ ] Canonical gate green, no new Warnings or Errors.

## Completion

Record in `docs/DONE/` with the reproduction and the `$HOME` decision stated explicitly, then delete this queue and `/tmp/home-cleanup-20260815/`.

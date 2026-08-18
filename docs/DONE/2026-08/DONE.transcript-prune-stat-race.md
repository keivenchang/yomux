# DOIT.p2.e1.transcript-prune-stat-race.md - Prune Logs A Warning For The Outcome It Wanted

Found 2026-08-18. One line, harmless behaviour, noisy log.

## Evidence

About 30 WARNINGs in a single second on p7772 (22:55:21 PT), all:

```
failed to prune transcript scan cache <hash>.json: [Errno 2] No such file or directory
```

## Cause

`yolomux_lib/workspace/session_files.py:1076`:

```python
size = path.stat().st_size      # raises FileNotFoundError -> caught as OSError -> WARNING
...
path.unlink(missing_ok=True)    # the race IS handled here
```

The `unlink` was deliberately written race-safe with `missing_ok=True`. The `stat()` two lines above was not. The pruner enumerates files, then stats them; when another pruner removes one in between, `stat` raises and the warning fires — **for a file that reached exactly the state the pruner wanted.**

Same pattern at `:1085`.

## Why it matters despite being harmless

Thirty WARNINGs at once for a non-problem trains everyone to ignore warnings, which is where real ones go to die.

## Plan

- [x] Treat a missing file during prune as success, not failure, at both `:1076` and `:1085`. Keep genuine OSErrors (permission, IO) loud. DONE: sort metadata plus both prune loops now use one `_transcript_scan_cache_stat()` owner; `FileNotFoundError` returns a silent missing result, while every other `OSError` still reaches the existing warning owner.
- [x] Add a regression that removes the file between enumeration and stat and asserts no warning is logged. DONE: the protected and ordinary size-read cases plus the initial sort-key case each failed red before their fixes; the focused store suite now passes 8 tests.

## Done Criteria

- [x] A vanished cache file during prune logs nothing; a permission error still logs. DONE: `python3 -m pytest tests/test_session_files.py -k 'transcript_scan_store' -q` passed 8 tests, exit 0; the `PermissionError` control still records `failed to prune transcript scan cache unreadable.json: denied`.
- [x] Canonical functional gate green; record exact-SHA certification separately. DONE: all nine functional lanes passed on candidate `80b4bf8ce`, including the 8/8 focused prune owner. Two certification-only attempts were NOT CERTIFIABLE because the shared host exceeded measured I/O/CPU stall limits; no certification pass is claimed. This recorded exception follows Keiven's tiered evidence policy and does not create a tag, push, or production restart claim.

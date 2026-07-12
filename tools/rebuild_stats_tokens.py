#!/usr/bin/env python3
"""Rebuild YO!stats token/cost components from all discoverable JSONL transcripts.

The command is offline-only. By default it performs a read-only dry run and
refuses while any YOLOmux server, statsd process, fresh background owner,
database opener, or statsd socket can touch the target. Use --apply only after
stopping every server sharing the target state directory, or combine it with
--stop-services to stop the recorded shared-state processes first.

Generated-output token scalars and live CPU/memory/GPU/client/agent metrics are
preserved. --include-live explicitly erases those unreconstructible live
metrics; JSONL transcripts cannot restore them. Every applied rebuild creates a
timestamped SQLite backup before changing any bucket.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from yolomux_lib.stats_rebuild import StatsRebuildSafetyError
from yolomux_lib.stats_rebuild import rebuild_stats_tokens
from yolomux_lib.statsd import default_database_path


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    result.add_argument("--database", type=Path, default=default_database_path(), help="stats-history SQLite path")
    result.add_argument("--socket", type=Path, help="statsd socket override (derived from --database by default)")
    result.add_argument("--claude-root", type=Path, help="Claude data root (default: ~/.claude)")
    result.add_argument("--codex-root", type=Path, help="Codex sessions root (default: ~/.codex/sessions)")
    result.add_argument("--start", type=float, help="inclusive transcript timestamp / bucket range start")
    result.add_argument("--end", type=float, help="exclusive transcript timestamp / bucket range end")
    result.add_argument("--include-live", action="store_true", help="also erase unreconstructible live metrics (destructive)")
    result.add_argument("--apply", action="store_true", help="create a backup and perform the rebuild; default is dry-run")
    result.add_argument("--stop-services", action="store_true", help="with --apply, stop recorded shared-state servers and statsd first")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.stop_services and not args.apply:
        parser().error("--stop-services requires --apply; a dry run never stops processes")
    if args.include_live:
        print("WARNING: --include-live permanently erases CPU/memory/GPU/client/agent history; JSONL cannot rebuild it.")
    try:
        report = rebuild_stats_tokens(
            args.database,
            socket_path=args.socket,
            claude_root=args.claude_root,
            codex_root=args.codex_root,
            start=args.start,
            end=args.end,
            include_live=args.include_live,
            dry_run=not args.apply,
            stop_services=args.stop_services,
            progress=lambda message: print(message, flush=True),
        )
    except (StatsRebuildSafetyError, FileNotFoundError, ValueError) as exc:
        print(str(exc))
        return 2
    print(json.dumps(report.payload(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Shared stats gate data builders."""

from pathlib import Path

from yolomux_lib.stats_current import migration, storage


NOW = 1_700_000_000.0
G9_CORRUPTION_BYTES = 4_096


def commit_scan(scanner, result):
    scanner.commit(result.receipt_id)
    return result


def valid_current_database(state_dir: Path) -> Path:
    state_dir.mkdir(parents=True, exist_ok=True)
    target = state_dir / storage.DATABASE_FILENAME
    migration.migrate(migration.MigrationInputs(state_dir), active_database=target, completed_at=NOW)
    return target


def corrupt_in_place(path: Path) -> bytes:
    size = path.stat().st_size
    assert size > G9_CORRUPTION_BYTES * 2, size
    with open(path, "r+b") as handle:
        handle.seek(size // 2)
        handle.write(b"\xff" * G9_CORRUPTION_BYTES)
    return path.read_bytes()


def record_current_database_migration(
    store: storage.Store,
    *,
    observed_at: float,
    observations: int,
    coverage_epochs: int,
    usage_atoms: int,
) -> None:
    assert store.record_migration_reconciliation(storage.MigrationReconciliation(
        migration.MIGRATION_ID,
        observed_at,
        "0" * 64,
        {
            "format": 1,
            "sources": [],
            "counts": {
                "observations": observations,
                "coverage_epochs": coverage_epochs,
                "usage_atoms": usage_atoms,
                "unavailable_spans": 0,
            },
            "issue_counts": {},
            "issues": [],
            "issues_truncated": 0,
            "retirement": {
                "artifacts": 0,
                "bytes": 0,
                "shared_history_rewrites": 0,
            },
        },
    ))


def _seed_realistic_stats(database: Path, *, end: float = NOW) -> int:
    """Create the fixture-owned active database before its sole service starts."""

    usage_atoms = []
    observations = []
    for interval in range(288):
        observed_at = end - (287 - interval) * 300
        observations.append(storage.Observation(
            f"cpu-{interval}", "cpu", "web", observed_at, "cpu-epoch", 1,
            {"process_percent": 7 + interval % 11, "system_percent": 23 + interval % 7},
        ))
        for agent in range(30):
            usage_atoms.append(storage.UsageAtom(
                f"transcript-{agent}-sample-{interval}", "input", "text", "none", "tokens",
                observed_at,
                {
                    "quantity": 25 + agent % 5,
                    "provider": "openai",
                    "model": "gpt-5",
                    "agent_id": f"mock-transcript-agent-{agent:02d}",
                    "telemetry_complete": True,
                },
            ))
    coverage_epochs = (storage.CoverageEpoch(
        "cpu", "web", "cpu-epoch", end - 86400, None, 300, 1,
    ),)
    with storage.Store.open(database) as store:
        appended = store.append_batch(
            observations=observations,
            coverage_epochs=coverage_epochs,
            usage_atoms=usage_atoms,
        )
        assert appended.source_generation == 1
        record_current_database_migration(
            store,
            observed_at=end,
            observations=len(observations),
            coverage_epochs=len(coverage_epochs),
            usage_atoms=len(usage_atoms),
        )
    return len(observations) + len(coverage_epochs) + len(usage_atoms)

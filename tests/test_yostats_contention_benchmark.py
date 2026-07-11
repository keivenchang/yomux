from pathlib import Path

from tools import yostats_contention_benchmark as benchmark


def test_contention_benchmark_isolated_fixture_and_acceptance_budgets(tmp_path):
    summary = benchmark.run_contention_benchmark(tmp_path)

    transcripts = sorted((tmp_path / "transcripts").glob("*.jsonl"))
    assert len(transcripts) == benchmark.TRANSCRIPT_COUNT
    assert transcripts[0].stat().st_size == benchmark.LARGE_TRANSCRIPT_BYTES
    assert transcripts[1].stat().st_size == benchmark.MEDIUM_TRANSCRIPT_BYTES
    assert len(list((tmp_path / "home").iterdir())) == benchmark.HOME_ENTRY_COUNT
    assert len([path for path in tmp_path.glob("repo-*") if (path / ".git").is_dir()]) == benchmark.REPO_COUNT
    assert summary["resources"]["activity"]["transcript_bytes_decoded"]["max"] == benchmark.TRANSCRIPT_COUNT * 4096
    for resource in (
        "stats",
        "stats_24h",
        "activity",
        "activity_warm",
        "index_rebuild",
        "transcript_job",
        "metadata_job",
        "auto_approve",
        "yo_targets",
        "background_status",
        "watch_roots",
        "ping",
        "terminal_echo",
        "terminal_echo_idle",
        "browser_client",
        "follower_stats",
        "terminal_miss",
    ):
        metrics = summary["resources"][resource]
        for metric in (
            "queue_ms",
            "handler_ms",
            "serialization_ms",
            "wire_bytes",
            "subprocess_count",
            "transcript_bytes_decoded",
            "client_long_task_ms",
            "event_loop_ms",
            "main_cpu_percent",
            "worker_cpu_percent",
            "combined_cpu_percent",
            "main_rss_bytes",
            "worker_rss_bytes",
            "combined_rss_bytes",
            "queue_depth",
            "restart_count",
            "parity_mismatch_count",
            "gil_heavy_stack_samples",
        ):
            assert set(metrics[metric]) == {"p50", "p95", "max"}
    assert summary["resources"]["follower_stats"]["transcript_bytes_decoded"]["max"] == 0
    assert summary["resources"]["browser_client"]["requests"] == 4
    assert summary["resource_totals"]["main_cpu_percent_avg"] <= benchmark.MAIN_CPU_AVG_MAX_PERCENT
    assert summary["resource_totals"]["main_cpu_percent"]["p95"] <= benchmark.MAIN_CPU_P95_MAX_PERCENT
    assert summary["resources"]["terminal_echo"]["handler_ms"]["max"] < benchmark.TERMINAL_ECHO_MAX_MS
    assert summary["resource_totals"]["gil_heavy_stack_samples"]["max"] == 0
    assert summary["stats_timeouts"] == 0
    assert summary["stats_retries"] == 0
    worker_matrix = summary["session_files_worker_matrix"]
    assert set(worker_matrix["workers"]) == {"1", "2", "4", "8"}
    assert worker_matrix["selected_workers"] == benchmark.SESSION_FILES_SELECTED_WORKERS
    selected_p95 = worker_matrix["workers"]["2"]["refresh_end_to_end_ms"]["p95"]
    assert selected_p95 < worker_matrix["workers"]["1"]["refresh_end_to_end_ms"]["p95"]
    assert selected_p95 < worker_matrix["workers"]["4"]["refresh_end_to_end_ms"]["p95"]
    assert selected_p95 < worker_matrix["workers"]["8"]["refresh_end_to_end_ms"]["p95"]
    assert worker_matrix["workers"]["2"]["ping_status_ms"]["p95"] < benchmark.PING_STATUS_P95_MS
    benchmark.assert_contention_budgets(summary)

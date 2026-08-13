# Subsystem status design

## Decision

The System panel should report five local service processes, not eleven logical daemon capabilities. The five processes are `indexd`, `statsd`, `jobd`, `statusd`, and `approvald`; each has one lifecycle owner and a real PID, start time, CPU sampler, and RSS source. The historical eleven-item `SubsystemSpec` inventory described logical work inside shared processes. Seven of those logical capabilities have no independent process metrics, so showing eleven CPU/RSS/uptime rows would repeat a parent process measurement and falsely attribute it to several children.

H2 should reject an unqualified null, not require a fabricated number. Instantaneous CPU needs two cumulative samples, an idle service has no uptime, and an OS process read can fail. Each metric therefore carries `state`, `value`, `reason_code`, and `reason`; a null value is valid only with an explicit non-`measured` state and actionable reason. H3 should pin five stable service rows in inventory order, including idle, warming, and unavailable services.

## What the historical eleven meant

The eleven names below are the reconciled logical capability inventory found in the discarded daemon-subsystem history. They are useful for feature/dependency diagnosis, but they are not eleven independently measurable runtimes in the current branch.

| Logical capability | Real current evidence | Honest process-metric verdict |
| --- | --- | --- |
| `daemon.metrics.host` | `StatsCurrentRuntime.status().families` reports the `cpu`, `gpu`, and `system_memory` collector cadence, attempts, failures, and last success. | No independent PID, CPU, RSS, or uptime; collection runs under the elected web owner and publishes through statsd. |
| `daemon.metrics.services` | The `service_load` collector family and `LocalServiceRegistry.resources*()` read registered-service CPU/RSS. | No independent lifecycle; it measures other processes. |
| `daemon.metrics.aggregator` | `StatsCurrentRuntime.status().service` reports statsd PID/start/migration/build/cache health. | Maps to the `statsd` process; CPU/RSS needs the existing statsd registry resource sampler exposed through a public status projection. |
| `daemon.tmux.status` | `StatusClient.runtime_status()` reports statusd PID/start/health/generation and samples its process resources. | Maps to the `statusd` process. |
| `daemon.tmux.approval` | `ApprovalClient.runtime_status()` reports approvald PID/start/health/targets and samples its process resources. | Maps to the `approvald` process. |
| `daemon.fs.watch` | `ClientWatchService` invalidation counts and `runtime_refresh_state().recurring_work` report watcher demand, attempts, useful work, and failures. | No independent PID or memory owner; it is elected-web-process work. |
| `daemon.fs.read` | `/api/fs/*` endpoint performance rows and jobd product counters report bounded read work. | Request-scoped/shared-broker work; no independent uptime, CPU, or RSS. |
| `daemon.fs.index` | `SearchIndexerClient.runtime_status()` reports indexd PID/start/health/queue/generation and samples its process resources. | Maps to the `indexd` process. |
| `daemon.fs.transcript` | Agent-token collector-family status plus jobd transcript product counters/runtime report attempts, failures, and work. | Split between elected-web collection and shared jobd execution; no independent process metrics. |
| `daemon.fs.git` | Jobd `session_files_view`/metadata product counters and endpoint performance rows report Git work. | Shared jobd work; no independent process metrics. |
| `daemon.fs.session_metadata` | Statusd inventory/generation and jobd materialized-product counters report the joined product. | Split across statusd and jobd; no independent process metrics. |

Four historical logical capabilities map to a unique current process (`daemon.metrics.aggregator`, `daemon.tmux.status`, `daemon.tmux.approval`, and `daemon.fs.index`). The current process inventory has a fifth real owner, `jobd`, which intentionally brokers several logical capabilities and therefore was not one historical capability row.

## Five real status rows and metric sources

| Service row | PID/start/health source | CPU/RSS source | Uptime source |
| --- | --- | --- | --- |
| `indexd` | `SearchIndexerClient.runtime_status()` from its registry status response. | `LocalServiceRegistry.resources(pid)`. | `TmuxWebtermApp.runtime_local_services()` derives elapsed wall time from the service's `started_at`. |
| `statsd` | `StatsCurrentRuntime.status().service` from the current stats RPC. | The existing `StatsCurrentClient` transport registry already owns the statsd process sampler; the public runtime projection must expose it instead of hard-coding nulls. | Same shared derivation from statsd `started_at`. |
| `jobd` | `JobClient.runtime_status()` from broker status, including verified worker PIDs. | `LocalServiceRegistry.resources_for_pids(parent_pid, worker_pids)` measures the broker and its verified workers together. | Same shared derivation from jobd `started_at`. |
| `statusd` | `StatusClient.runtime_status()` from its registry status response. | `LocalServiceRegistry.resources(pid)`. | Same shared derivation from statusd `started_at`. |
| `approvald` | `ApprovalClient.runtime_status()` from its registry status response. | `LocalServiceRegistry.resources(pid)`. | Same shared derivation from approvald `started_at`. |

## Proposed `/api/system-status` shape

`TmuxWebtermApp.system_status_payload()` remains the one HTTP payload owner. It should normalize the existing local-service rows without starting another collector or introducing a parallel subsystem registry.

```json
{
  "local_services": {
    "schema_version": 1,
    "inventory": ["indexd", "statsd", "jobd", "statusd", "approvald"],
    "services": [
      {
        "id": "indexd",
        "label": "Quick Open index",
        "state": "running",
        "reason_code": "",
        "reason": "",
        "pid": 4242,
        "started_at": 1785600000.0,
        "metrics": {
          "cpu_now_percent": {"state": "measured", "value": 3.5, "reason_code": "", "reason": ""},
          "rss_bytes": {"state": "measured", "value": 67108864, "reason_code": "", "reason": ""},
          "uptime_seconds": {"state": "measured", "value": 120.0, "reason_code": "", "reason": ""}
        },
        "details": {}
      },
      {
        "id": "approvald",
        "label": "Auto-approval",
        "state": "idle",
        "reason_code": "not_started",
        "reason": "Starts when auto-approval is enabled",
        "pid": 0,
        "started_at": 0.0,
        "metrics": {
          "cpu_now_percent": {"state": "not_running", "value": null, "reason_code": "not_started", "reason": "Service is not running"},
          "rss_bytes": {"state": "not_running", "value": null, "reason_code": "not_started", "reason": "Service is not running"},
          "uptime_seconds": {"state": "not_running", "value": null, "reason_code": "not_started", "reason": "Service is not running"}
        },
        "details": {}
      }
    ]
  },
  "host": {
    "identity": {},
    "roots": [],
    "database_partitions": [],
    "rejected_mutable_paths": [],
    "network_filesystem_escape_hatch": false
  }
}
```

The complete `services` array always contains one record per `inventory` entry in the same order. Allowed service states are `running`, `idle`, `issue`, and `unavailable`. Allowed metric states are `measured`, `warming`, `not_running`, and `unavailable`; `measured` requires a finite numeric value and every other state requires a non-empty `reason_code` and `reason`. A first CPU sample is `warming/baseline_pending`, not zero and not a bare null. Existing queue/cache/product diagnostics remain in `details` or their current bounded fields; this design does not create a second producer for them.

## Renderer contract

The Local Services card should render one row per inventory entry with `data-subsystem-row` and `data-subsystem-id`. Every metric cell renders either its measured number or the metric reason. Idle and unavailable rows remain present, expose their state and reason, and never reuse a previous process's CPU/RSS as though it were current. The table can retain existing details, but the five-row identity and metric-state handling come only from the normalized payload.

## `host_diagnostics` ownership

`yolomux_lib.infra.host_diagnostics` should remain the sole producer for host identity, root filesystem classification, database partitions, rejected mutable paths, and the network-filesystem escape hatch. It should not own process/subsystem state because it has no local-service lifecycle or CPU baseline. `system_status_payload()` should compose its payload under `host` beside the local-service projection, giving the renderer one response without creating a second status endpoint or duplicating either collector.

## Contract changes

`tests/test_gate_panels.py::test_h2_system_status_metrics_are_not_null` should pin the five-entry inventory and typed metric envelopes from the real `/api/system-status` producer. `tests/test_gate_panels.py::test_h3_all_eleven_subsystem_rows_remain_rendered` keeps its historical node ID for census continuity but should assert five rendered rows in inventory order, including idle and unavailable rows. Both remain strict xfails until the normalized API and renderer exist, so either contract becomes an XPASS failure at the implementation boundary.

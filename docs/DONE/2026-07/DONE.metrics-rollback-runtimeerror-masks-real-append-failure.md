# DOIT: metrics rollback RuntimeError masks the real append failure

Root-caused by the dev7771 Claude session against the LIVE daemon, with a captured traceback. This is the same `RuntimeError` that caused the Finder/Differ/Tabber outage: before `cf4f896b` it killed the scheduler pump thread on its first occurrence and wedged the daemon while STATUS still reported `healthy: true`.

Current state: `cf4f896b` makes the pump survive it, so it is no longer user-visible. It is still firing at roughly 0.02/s on a fresh daemon (83 failures over 4953s on the old one; 2 within ~80s on a fresh one at `2a655cad`). Each occurrence aborts a whole pump iteration, so `scheduler.pump()`, every pump callback, and update-status maintenance are skipped on that tick.

## The captured traceback

Instrumented `_run_scheduler_pump` to record the failing stage and full traceback, ran the real dev7771 workload, and caught it twice. Failing stage both times: `metrics_domain.pump` — the FIRST call in the loop.

```
CollectorAppendError: unavailable span overlaps a coverage epoch
  metrics/lifecycle.py:433 append -> append_wire.append_collector_facts
  stats_current/append_wire.py:344 _append_fact_batch -> raise error_factory(...)

During handling of the above exception, another exception occurred:

RuntimeError: transcript scan receipt is not active
  daemon/runtime.py:633 _run_scheduler_pump -> self._metrics_domain.pump()
  daemon/metrics/domain.py:111 pump -> self.lifecycle.pump()
  daemon/metrics/lifecycle.py:304 pump -> response = self._publisher.append(facts)
  daemon/metrics/lifecycle.py:455 append -> return self._reject_batch(...)
  daemon/metrics/lifecycle.py:503 _reject_batch -> batch.receipt.rollback()
  daemon/fs/transcript.py:529 <lambda> -> self._scanner.rollback(scan.receipt_id)
  stats_current/transcripts.py:499 rollback -> self._require_receipt(receipt_id)
  stats_current/transcripts.py:505 _require_receipt -> raise RuntimeError("transcript scan receipt is not active")
```

## What is actually wrong

The error handler throws while handling an error. A recoverable, typed domain failure (`CollectorAppendError: unavailable span overlaps a coverage epoch`) enters `_reject_batch`, whose cleanup then raises a generic `RuntimeError` that escapes the entire metrics pump.

Two distinct defects:

- [x] **1. `rollback()` is not idempotent.** `stats_current/transcripts.py:495` now returns when there is no inflight receipt, while retaining the strict mismatched-active-receipt check. DONE 2026-07-28: the transcript scanner regression rolls back the same receipt twice without raising.

- [x] **2. `_reject_batch` lets cleanup destroy the reason.** The rebase moved the effective cleanup boundary into `stats_current/append_wire.py:316`; `MetricsChildPublishLease` already avoids a second rollback after that boundary. DONE 2026-07-28: rollback cleanup now raises the original `CollectorAppendError` from the cleanup failure, so the exact append reason remains the surfaced type/message and the cleanup exception remains chained.

- [x] **3. Investigate the underlying `unavailable span overlaps a coverage epoch`.** This is not expected churn: the store rejects any unavailable interval that overlaps coverage for the same `(family, source_id)`. DONE 2026-07-28: added a publisher regression proving a retained `append_rejected` interval is clipped at the next coverage boundary before append; the reason remains visible as `error_code` and is not suppressed.

## Tests to add

- A regression test that `rollback()` on an inactive/already-rolled-back receipt does not raise.
- A test that when `append` fails AND rollback also fails, the surfaced error still carries the original `CollectorAppendError` reason rather than a generic `RuntimeError`.
- A test that a failing `metrics_domain.pump()` does not abort the rest of the pump iteration (scheduler, callbacks, maintenance still run). `cf4f896b` keeps the THREAD alive but the remaining stages of that tick are still skipped.

## How to reproduce

Instrument `_run_scheduler_pump` in `yolomux_lib/daemon/runtime.py` to record `stage` plus `traceback.format_exception(...)` on failure, restart the dev7771 daemon (`pkill -f 'daemon.process.*dev7771'` then `./boot.sh 7771`), then drive load:

```
curl -sk -b "yolomux_auth_7771=$COOKIE" "https://127.0.0.1:7771/api/session-metadata?force=1"
curl -sk -b "yolomux_auth_7771=$COOKIE" -X POST https://127.0.0.1:7771/api/fs/batch \
  -H 'Content-Type: application/json' -d '{"requests":[{"op":"list","path":"/home/keivenc"}]}'
```

Two failures landed within ~60s. Read the live counter with `persistent_client_status(DaemonClient(...))` and check `scheduler_pump.failure_count`. Remember to revert the instrumentation.

## Notes

- An idle daemon on a scratch port does NOT reproduce it. It needs the real workload (transcript scanning with active receipts). I burned time on a clean-room repro that stayed at 0 failures.
- `ab69c7b8 "Fix metrics publication after storaged replacement"` does NOT fix this. I checked: a fresh daemon at `2a655cad` hits it at the same rate.

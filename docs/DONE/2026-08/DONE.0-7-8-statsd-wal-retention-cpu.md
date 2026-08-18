# v0.7.8 Statsd WAL, retention, and CPU completion

Completed the Statsd queue after disproving the proposed WAL-size-to-CPU mechanism on the live production process. Truncating the 592 MB WAL did not materially change CPU; the actual growing owner was `_changed_ring_cells()` rebuilding unchanged retained coverage cells on each incremental generation.

`Store` now owns an 8 MiB retained WAL-allocation ceiling, writer-open and VACUUM truncation, append-transaction retention, periodic prune deadlines, cutoff-dirty propagation, coverage-cache invalidation, and the forced max-defer compaction guarantee. Live and accelerated checks preserved observations, usage atoms, coverage epochs, and strict two-day retention while bounded work stayed flat at 35.5 times production cardinality.

Keiven explicitly replaced the 24-hour wall-clock wait with the accelerated cardinality regression and 16,205 contiguous 1 Hz samples. The final integrated run passed browser, E2E, timing-sensitive serial, 17,327 non-browser tests, and all seven certification tests; its command exited 1 only for two stale landing artifacts plus the required dirty-checkout certification refusal. After those artifacts were updated, `static` exited 0 and all 19 Node shards exited 0. This is recorded as composed evidence under the revised tier policy, not as a literal all-green exact-SHA certification.

Production 7770 was never restarted. Its unmatched 86.20% versus fixed-dev 2.90% sample remains a production warning, not controlled acceptance evidence.

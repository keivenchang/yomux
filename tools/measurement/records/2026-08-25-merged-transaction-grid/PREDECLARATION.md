# YOLO-V0717-E3-REMEASURE-08 predeclaration

Written BEFORE any grid arm was run. Timestamp recorded below by the shell, not by hand.

## Predeclared baseline shape

**The baseline is one `append_batch` per family per one-second tick - two transactions per second.**

Reasons, stated before measuring:

1. `/tmp/statsd-audit-048b/measure3.py` computes `commits = seconds * families` and calls
   `st.append_batch(...)` once per family inside its loop. It is the only retained two-family
   harness and it is per-family-per-tick.
2. The queue's own per-sample cost is 25,144.4 B and its baseline total is 181.04 MB.
   `181.04e6 / 25,144.4 = 7,200.0` commits exactly. With `commits = seconds * families` and
   `families = 2`, that is `seconds = 3600`. So the withdrawn baseline was 3,600 seconds of
   acquisition at two commits per second.
3. `measure3_ref.py` (measure3.py with only its scratch directory changed) was run before this
   predeclaration and produced 24,980 B per commit on its default arm, within 0.65% of
   25,144.4 B. That reproduction is what makes reading 1 and 2 concrete rather than inferred.

## Therefore the grid uses `seconds = 3600`, `families = 2`

Every arm acquires one sample per family per second for 3,600 seconds: **7,200 observations in
every arm**, identical facts, only persistence batching varies.

| Arm | Merge | Interval | Expected commits |
| --- | --- | ---: | ---: |
| pf-1 (baseline) | per_family | 1 s | 7,200 |
| mg-1 | merged | 1 s | 3,600 |
| pf-5 | per_family | 5 s | 1,440 |
| mg-5 | merged | 5 s | 720 |
| pf-10 | per_family | 10 s | 720 |
| mg-10 | merged | 10 s | 360 |

## Predeclared hypothesis, so it can be falsified rather than fitted

`measure3_ref.py`'s `autockpt=0` arm wrote 88.18 MB with a final WAL of 88.15 MB - a 1.0x ratio,
so the per-commit cost is genuine WAL append volume, not writeback amplification. At 24,494 B per
commit against a 4,096 B page plus a 24 B frame header, each commit dirties about
`24,494 / 4,120 = 5.9` whole pages for a payload of tens of bytes.

**Prediction: commit COUNT is the lever, and merging matters only through the commit count it
removes.** If that holds, `mg-1` and `pf-5` should differ mostly by their commit counts
(3,600 versus 1,440), `pf-10` and `mg-5` should land close together because both make 720
commits, and the queue's claim that "cadence per family is not the lever; merging families into
one transaction is" is **wrong as stated** - merging is one way to halve commit count, and
interval is a strictly more powerful way.

The `pf-10` versus `mg-5` pair is the falsification test: same commit count, different merge
state. If they differ materially, merging carries cost beyond commit count and the prediction
fails.
written: 2026-08-25 20:07:19 PDT

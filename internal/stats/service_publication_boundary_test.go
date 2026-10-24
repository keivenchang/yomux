// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package stats

import (
	"context"
	"errors"
	"fmt"
	"path/filepath"
	"testing"
	"time"
)

func publishStatsServiceBoundaryWindow(t *testing.T, writer *Writer, key ProductKey, end int64, publishedAt float64, missing, partial bool) {
	t.Helper()
	rows := make([]RingBucketWrite, 0, key.RangeSeconds/key.ResolutionSeconds)
	for start := end - int64(key.RangeSeconds); start < end; start += int64(key.ResolutionSeconds) {
		if partial && start != end-int64(key.ResolutionSeconds) {
			continue
		}
		if missing && start == end-5*int64(key.ResolutionSeconds) {
			continue
		}
		complete := start != end-int64(key.ResolutionSeconds)
		payload, err := encodeRingBucketPayload(MaterializedBucket{Start: float64(start), Duration: float64(key.ResolutionSeconds), Complete: complete, Series: []MaterializedSeries{{Name: "process_cpu_percent", Value: 42}}}, nil, 1, publishedAt)
		if err != nil {
			t.Fatal(err)
		}
		rows = append(rows, RingBucketWrite{ResolutionSeconds: key.ResolutionSeconds, BucketStart: start, BucketJSON: payload, Complete: complete})
	}
	if _, err := writer.PublishRingBuckets(context.Background(), rows, 1, publishedAt, true); err != nil {
		t.Fatal(err)
	}
}

func TestStatsServiceColdRefreshAcrossPublicationBoundary(t *testing.T) {
	for _, key := range []ProductKey{{300, 1}, {900, 10}, {1800, 60}, {3600, 300}} {
		t.Run(fmt.Sprintf("%d_%d", key.RangeSeconds, key.ResolutionSeconds), func(t *testing.T) {
			ctx := context.Background()
			writer, err := OpenWriter(ctx, filepath.Join(t.TempDir(), "stats.sqlite3"))
			if err != nil {
				t.Fatal(err)
			}
			defer writer.Close()
			edge := int64(2_000_000_000)
			edge -= edge % int64(key.ResolutionSeconds)
			windowEnd := edge + int64(key.ResolutionSeconds)
			published := time.Unix(windowEnd, 0).Add(-16 * time.Millisecond)
			now := published.Add(20 * time.Millisecond)
			publishStatsServiceBoundaryWindow(t, writer, key, windowEnd, float64(published.UnixNano())/1e9, false, false)
			durable, err := writer.ReadRingWindowMetadata(ctx, key.RangeSeconds, key.ResolutionSeconds, windowEnd)
			if err != nil {
				t.Fatal(err)
			}
			if len(durable.MissingBucketStarts) != 0 || len(durable.PendingInvalidations) != 0 {
				t.Fatalf("published window incomplete: %+v", durable)
			}
			service := NewStatsService(writer, StatsServiceConfig{Products: []ProductKey{key}, Now: func() time.Time { return now }})
			if err := service.Refresh(ctx); err != nil {
				t.Fatal(err)
			}
			snapshot, err := service.CurrentSnapshot(ctx, key.RangeSeconds, key.ResolutionSeconds)
			if err != nil {
				t.Fatalf("complete published window[%d,%d) became unavailable after clock crossed boundary at%s: %v; state=%+v", windowEnd-int64(key.RangeSeconds), windowEnd, now, err, service.State())
			}
			if snapshot.Request.End != float64(windowEnd) || len(snapshot.Materialized.Buckets) != key.RangeSeconds/key.ResolutionSeconds {
				t.Fatalf("published window changed: end=%v buckets=%d want end%d buckets%d", snapshot.Request.End, len(snapshot.Materialized.Buckets), windowEnd, key.RangeSeconds/key.ResolutionSeconds)
			}
		})
	}
}

func TestStatsServicePublicationHorizonPreservesMissingAndLegacyState(t *testing.T) {
	for _, key := range []ProductKey{{300, 1}, {900, 10}, {1800, 60}, {3600, 300}} {
		t.Run(fmt.Sprintf("%d_%d", key.RangeSeconds, key.ResolutionSeconds), func(t *testing.T) {
			end := int64(2_000_000_000)
			end -= end % int64(key.ResolutionSeconds)
			end += int64(key.ResolutionSeconds)
			published := time.Unix(end, 0).Add(-16 * time.Millisecond)
			for _, test := range []struct {
				name                                 string
				publication                          float64
				now                                  time.Time
				missing, invalidated, empty, partial bool
				ready                                bool
			}{
				{name: "current open published bucket", publication: float64(published.UnixNano()) / 1e9, now: published, ready: true},
				{name: "zero timestamp legacy publication", now: published, ready: true},
				{name: "zero timestamp rollover retains legacy admission", now: published.Add(20 * time.Millisecond)},
				{name: "future publication does not advance wall clock", publication: float64(end + int64(key.ResolutionSeconds)), now: published, ready: true},
				{name: "real interior hole remains pending", publication: float64(published.UnixNano()) / 1e9, now: published.Add(20 * time.Millisecond), missing: true},
				{name: "real invalidation remains pending", publication: float64(published.UnixNano()) / 1e9, now: published.Add(20 * time.Millisecond), invalidated: true},
				{name: "unpublished ring remains pending", now: published, empty: true},
				{name: "partial startup remains pending despite published head", publication: float64(published.UnixNano()) / 1e9, now: published.Add(20 * time.Millisecond), partial: true},
			} {
				t.Run(test.name, func(t *testing.T) {
					ctx := context.Background()
					writer, err := OpenWriter(ctx, filepath.Join(t.TempDir(), "stats.sqlite3"))
					if err != nil {
						t.Fatal(err)
					}
					defer writer.Close()
					if !test.empty {
						publishStatsServiceBoundaryWindow(t, writer, key, end, test.publication, test.missing, test.partial)
					}
					if test.invalidated {
						if _, err := writer.AppendBatch(ctx, AppendBatch{Observations: []Observation{{EventID: "boundary-invalidation", Family: "cpu", SourceID: "host", ObservedAt: float64(end - 5*int64(key.ResolutionSeconds)), EpochID: "boundary", OwnerGeneration: 1, Payload: map[string]any{"process_percent": 43.0, "system_percent": 5.0}}}}); err != nil {
							t.Fatal(err)
						}
					}
					service := NewStatsService(writer, StatsServiceConfig{Products: []ProductKey{key}, Now: func() time.Time { return test.now }})
					if err := service.Refresh(ctx); err != nil {
						t.Fatal(err)
					}
					snapshot, err := service.CurrentSnapshot(ctx, key.RangeSeconds, key.ResolutionSeconds)
					if !test.ready {
						if !errors.Is(err, ErrProductPending) || service.State().Ready {
							t.Fatalf("unpublished/missing state became ready: snapshot=%+v err=%v state=%+v", snapshot, err, service.State())
						}
						return
					}
					if err != nil {
						t.Fatal(err)
					}
					if snapshot.Request.End != float64(end) || len(snapshot.Materialized.Buckets) != key.RangeSeconds/key.ResolutionSeconds {
						t.Fatalf("current published window changed: bounds=%+v buckets=%d", snapshot.Request, len(snapshot.Materialized.Buckets))
					}
					if snapshot.Materialized.Buckets[len(snapshot.Materialized.Buckets)-1].Complete {
						t.Fatalf("current open bucket became complete: %+v", snapshot.Materialized.Buckets[len(snapshot.Materialized.Buckets)-1])
					}
					if snapshot.GeneratedAt != test.publication {
						t.Fatalf("publication age rewritten: generated_at=%v want%v", snapshot.GeneratedAt, test.publication)
					}
					if service.State().LastRefreshAt != float64(test.now.UnixNano())/1e9 {
						t.Fatalf("refresh clock replaced by publication clock: %+v", service.State())
					}
				})
			}

		})
	}
}

func TestStatsServiceElapsedDirtyCarrierRetainsWarmProductUntilRepair(t *testing.T) {
	for _, key := range []ProductKey{{300, 1}, {900, 10}, {1800, 60}, {3600, 300}} {
		t.Run(fmt.Sprintf("%d_%d", key.RangeSeconds, key.ResolutionSeconds), func(t *testing.T) {
			ctx := context.Background()
			writer, err := OpenWriter(ctx, filepath.Join(t.TempDir(), "stats.sqlite3"))
			if err != nil {
				t.Fatal(err)
			}
			defer writer.Close()
			end := int64(2_000_000_000)
			end -= end % int64(key.ResolutionSeconds)
			end += int64(key.ResolutionSeconds)
			published := time.Unix(end, 0).Add(-16 * time.Millisecond)
			now := published
			publishStatsServiceBoundaryWindow(t, writer, key, end, float64(published.UnixNano())/1e9, false, false)
			config := StatsServiceConfig{Products: []ProductKey{key}, Now: func() time.Time { return now }}
			warm := NewStatsService(writer, config)
			if err := warm.Refresh(ctx); err != nil {
				t.Fatal(err)
			}
			previous, err := warm.CurrentSnapshot(ctx, key.RangeSeconds, key.ResolutionSeconds)
			if err != nil {
				t.Fatal(err)
			}
			if _, err := writer.AppendBatch(ctx, AppendBatch{Observations: []Observation{{EventID: "late-open-carrier", Family: "cpu", SourceID: "host", ObservedAt: float64(end) - 0.008, EpochID: "carrier", OwnerGeneration: 1, Payload: map[string]any{"process_percent": 43.0, "system_percent": 5.0}}}}); err != nil {
				t.Fatal(err)
			}
			now = published.Add(10 * time.Millisecond)
			before := NewStatsService(writer, config)
			if err := before.Refresh(ctx); err != nil {
				t.Fatal(err)
			}
			if _, err := before.CurrentSnapshot(ctx, key.RangeSeconds, key.ResolutionSeconds); err != nil || !before.State().Ready {
				t.Fatalf("currently open dirty carrier was rejected: err=%v state=%+v", err, before.State())
			}
			now = published.Add(20 * time.Millisecond)
			cold := NewStatsService(writer, config)
			if err := cold.Refresh(ctx); err != nil {
				t.Fatal(err)
			}
			if _, err := cold.CurrentSnapshot(ctx, key.RangeSeconds, key.ResolutionSeconds); !errors.Is(err, ErrProductPending) || cold.State().Ready {
				t.Fatalf("elapsed invalidated carrier became cold-ready: err=%v state=%+v", err, cold.State())
			}
			if err := warm.Refresh(ctx); err != nil {
				t.Fatal(err)
			}
			retained, err := warm.CurrentSnapshot(ctx, key.RangeSeconds, key.ResolutionSeconds)
			if err != nil || warm.State().Ready || retained.CacheGeneration != previous.CacheGeneration || retained.GeneratedAt != previous.GeneratedAt {
				t.Fatalf("known previous generation was lost or relabeled: err=%v state=%+v retained=%+v", err, warm.State(), retained.Request)
			}
			rows := make([]RingBucketWrite, 0, 2)
			for _, start := range []int64{end - int64(key.ResolutionSeconds), end} {
				bucket := MaterializedBucket{Start: float64(start), Duration: float64(key.ResolutionSeconds), Complete: start < end}
				if start < end {
					bucket.Series = []MaterializedSeries{{Name: "process_cpu_percent", Value: 43}}
				}
				payload, err := encodeRingBucketPayload(bucket, nil, 2, float64(now.UnixNano())/1e9)
				if err != nil {
					t.Fatal(err)
				}
				rows = append(rows, RingBucketWrite{ResolutionSeconds: key.ResolutionSeconds, BucketStart: start, BucketJSON: payload, Complete: bucket.Complete})
			}
			if _, err := writer.PublishRingBuckets(ctx, rows, writer.SourceGeneration(), float64(now.UnixNano())/1e9, true); err != nil {
				t.Fatal(err)
			}
			if err := cold.Refresh(ctx); err != nil {
				t.Fatal(err)
			}
			repaired, err := cold.CurrentSnapshot(ctx, key.RangeSeconds, key.ResolutionSeconds)
			if err != nil || !cold.State().Ready {
				t.Fatalf("repaired publication stayed unavailable: err=%v state=%+v", err, cold.State())
			}
			if repaired.CacheGeneration <= previous.CacheGeneration || repaired.Request.End != float64(end+int64(key.ResolutionSeconds)) || repaired.GeneratedAt != float64(now.UnixNano())/1e9 {
				t.Fatalf("repair did not advance the actual published generation/window: %+v", repaired)
			}
			tail := repaired.Materialized.Buckets[len(repaired.Materialized.Buckets)-2:]
			if !tail[0].Complete || len(tail[0].Series) != 1 || tail[0].Series[0].Value != 43 || tail[1].Complete {
				t.Fatalf("repair lost closed facts or current open bucket: %+v", tail)
			}
		})
	}
}

func TestStatsServiceColdRefreshAtRoundedPublicationBoundary(t *testing.T) {
	for _, key := range []ProductKey{{300, 1}, {900, 10}, {1800, 60}, {3600, 300}} {
		t.Run(fmt.Sprintf("%d_%d", key.RangeSeconds, key.ResolutionSeconds), func(t *testing.T) {
			ctx := context.Background()
			writer, err := OpenWriter(ctx, filepath.Join(t.TempDir(), "stats.sqlite3"))
			if err != nil {
				t.Fatal(err)
			}
			defer writer.Close()
			end := int64(2_000_000_000)
			end -= end % int64(key.ResolutionSeconds)
			end += int64(key.ResolutionSeconds)
			published := time.Unix(end, 0).Add(-time.Nanosecond)
			metadataTime := float64(published.UnixNano()) / 1e9
			if metadataTime != float64(end) {
				t.Fatalf("fixture did not round across boundary: %.9f", metadataTime)
			}
			publishStatsServiceBoundaryWindow(t, writer, key, end, metadataTime, false, false)
			service := NewStatsService(writer, StatsServiceConfig{Products: []ProductKey{key}, Now: func() time.Time { return time.Unix(end, 0).Add(4 * time.Millisecond) }})
			if err := service.Refresh(ctx); err != nil {
				t.Fatal(err)
			}
			snapshot, err := service.CurrentSnapshot(ctx, key.RangeSeconds, key.ResolutionSeconds)
			if err != nil {
				t.Fatalf("complete published window ending%d lost to rounded timestamp%.9f: %v; state=%+v", end, metadataTime, err, service.State())
			}
			if snapshot.Request.End != float64(end) || snapshot.GeneratedAt != metadataTime {
				t.Fatalf("durable bounds/timestamp changed: bounds=%+v generated_at=%v", snapshot.Request, snapshot.GeneratedAt)
			}
		})
	}
}

func TestStatsServiceDurableHeadKeepsActualBoundaryBucketAndWallClockCap(t *testing.T) {
	for _, key := range []ProductKey{{300, 1}, {900, 10}, {1800, 60}, {3600, 300}} {
		t.Run(fmt.Sprintf("%d_%d", key.RangeSeconds, key.ResolutionSeconds), func(t *testing.T) {
			ctx := context.Background()
			writer, err := OpenWriter(ctx, filepath.Join(t.TempDir(), "stats.sqlite3"))
			if err != nil {
				t.Fatal(err)
			}
			defer writer.Close()
			boundary := int64(2_000_000_000)
			boundary -= boundary % int64(key.ResolutionSeconds)
			boundary += int64(key.ResolutionSeconds)
			now := time.Unix(boundary, 0)
			end := boundary + int64(key.ResolutionSeconds)
			publishStatsServiceBoundaryWindow(t, writer, key, end, float64(boundary), false, false)
			service := NewStatsService(writer, StatsServiceConfig{Products: []ProductKey{key}, Now: func() time.Time { return now }})
			if err := service.Refresh(ctx); err != nil {
				t.Fatal(err)
			}
			snapshot, err := service.CurrentSnapshot(ctx, key.RangeSeconds, key.ResolutionSeconds)
			if err != nil {
				t.Fatal(err)
			}
			if snapshot.Request.End != float64(end) || snapshot.GeneratedAt != float64(boundary) {
				t.Fatalf("real publication at boundary lost its new bucket: bounds=%+v generated_at=%v", snapshot.Request, snapshot.GeneratedAt)
			}
			tail := snapshot.Materialized.Buckets[len(snapshot.Materialized.Buckets)-1]
			if tail.Start != float64(boundary) || tail.Complete {
				t.Fatalf("actual current open bucket was dropped/closed: %+v", tail)
			}
			now = now.Add(-time.Nanosecond)
			clockEnd := NextWindowEnd(now, key.ResolutionSeconds)
			window, err := writer.readPublishedRingWindowMetadata(ctx, key.RangeSeconds, key.ResolutionSeconds, clockEnd)
			if err != nil {
				t.Fatal(err)
			}
			if window.WindowEnd != clockEnd {
				t.Fatalf("future head advanced wall clock: end%d want%d", window.WindowEnd, clockEnd)
			}
			// The older current window lacks its first bucket in this fixture. A future
			// published head must neither move that window nor hide the actual hole.
			cold := NewStatsService(writer, StatsServiceConfig{Products: []ProductKey{key}, Now: func() time.Time { return now }})
			if err := cold.Refresh(ctx); err != nil {
				t.Fatal(err)
			}
			if _, err := cold.CurrentSnapshot(ctx, key.RangeSeconds, key.ResolutionSeconds); !errors.Is(err, ErrProductPending) || cold.State().Ready {
				t.Fatalf("future publication bypassed current-window admission: err=%v state=%+v", err, cold.State())
			}
		})
	}
}

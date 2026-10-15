// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/keivenchang/yomux/internal/stats"
)

type pricingFixtureTransport func(*http.Request) (*http.Response, error)

func (f pricingFixtureTransport) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func pricingLifecycleFixture(t *testing.T) (*server, string) {
	t.Helper()
	root := t.TempDir()
	seed := filepath.Join(root, "seed.json")
	if err := os.WriteFile(seed, []byte(`{"schema_version":1,"catalog_revision":7,"models":[]}`), 0600); err != nil {
		t.Fatal(err)
	}
	catalog, err := stats.LoadPricingCatalog(seed)
	if err != nil {
		t.Fatal(err)
	}
	catalog.SetRefreshCachePath(filepath.Join(root, "pricing-refresh.json"))
	writer, err := stats.OpenWriter(context.Background(), filepath.Join(root, "stats.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	s := &server{pricing: catalog, statsWriter: writer, refillContext: ctx, serverCancel: cancel, events: newEventBroker()}
	t.Cleanup(func() {
		cancel()
		s.stopBackgroundWork()
		if err := writer.Close(); err != nil {
			t.Error(err)
		}
	})
	return s, seed
}

func installPricingFixtureTransport(t *testing.T, hook func(*http.Request) error) {
	t.Helper()
	original := http.DefaultTransport
	http.DefaultTransport = pricingFixtureTransport(func(r *http.Request) (*http.Response, error) {
		if hook != nil {
			if err := hook(r); err != nil {
				return nil, err
			}
		}
		status, body := http.StatusServiceUnavailable, "fixture unavailable"
		if r.URL.Host == "raw.githubusercontent.com" {
			status = http.StatusOK
			body = `{"gpt-fixture":{"litellm_provider":"openai","input_cost_per_token":0.000003,"output_cost_per_token":"0.000012"}}`
		}
		return &http.Response{StatusCode: status, Header: make(http.Header), Body: io.NopCloser(strings.NewReader(body)), Request: r}, nil
	})
	t.Cleanup(func() { http.DefaultTransport = original })
}

func TestPricingRouteUsesProvidersCoalescesPersistsAndInvalidates(t *testing.T) {
	s, seed := pricingLifecycleFixture(t)
	ctx := context.Background()
	end := time.Now().Truncate(10 * time.Second).Unix()
	if _, err := s.statsWriter.AppendBatch(ctx, stats.AppendBatch{UsageAtoms: []stats.UsageAtom{{
		EventID: "pricing-test-usage", Direction: "input", Modality: "text", CacheRole: "none", Unit: "tokens", ObservedAt: float64(end - 5),
		Payload: map[string]any{"quantity": 1000.0, "provider": "openai", "model": "gpt-fixture"},
	}}}); err != nil {
		t.Fatal(err)
	}
	service := stats.NewStatsService(s.statsWriter, stats.StatsServiceConfig{Products: []stats.ProductKey{{RangeSeconds: 10, ResolutionSeconds: 10}}, Now: func() time.Time { return time.Unix(end-1, 0) }})
	bucket, err := json.Marshal(stats.MaterializedBucket{Start: float64(end - 10), Duration: 10, Complete: true,
		Cost: stats.MaterializedCostDetail{Tokens: 1000, MicroUSD: 1000, Evidence: []stats.PriceEvidence{{Key: "accepted-revision", CatalogRevision: 7}}}})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := s.statsWriter.PublishRingBuckets(ctx, []stats.RingBucketWrite{{ResolutionSeconds: 10, BucketStart: end - 10, BucketJSON: string(bucket), Complete: true}}, 1, float64(end), true); err != nil {
		t.Fatal(err)
	}
	if err := service.Refresh(ctx); err != nil {
		t.Fatal(err)
	}
	before, err := service.CurrentSnapshot(ctx, 10, 10)
	if err != nil {
		t.Fatal(err)
	}
	entered, release := make(chan struct{}), make(chan struct{})
	var once sync.Once
	installPricingFixtureTransport(t, func(r *http.Request) error {
		once.Do(func() {
			close(entered)
			select {
			case <-release:
			case <-r.Context().Done():
			}
		})
		return r.Context().Err()
	})
	first := httptest.NewRecorder()
	s.pricingCatalogRefresh(first, httptest.NewRequest(http.MethodPost, "/api/pricing-catalog/refresh", nil))
	<-entered
	second := httptest.NewRecorder()
	s.pricingCatalogRefresh(second, httptest.NewRequest(http.MethodPost, "/api/pricing-catalog/refresh", nil))
	if first.Code != 202 || second.Code != 202 || !strings.Contains(second.Body.String(), `"coalesced":true`) || !strings.Contains(second.Body.String(), `"ticket":"pricing-refresh-1"`) {
		t.Fatalf("responses %s / %s", first.Body, second.Body)
	}
	close(release)
	s.backgroundWork.Wait()
	if s.pricing.Revision() != 8 || s.pricing.PricingInvalidationPending() {
		t.Fatalf("revision=%d pending=%v", s.pricing.Revision(), s.pricing.PricingInvalidationPending())
	}
	if err := service.Refresh(ctx); err != nil && !errors.Is(err, stats.ErrProductPending) {
		t.Fatal(err)
	}
	after, err := service.CurrentSnapshot(ctx, 10, 10)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(before.Materialized, after.Materialized) || after.Materialized.Buckets[0].Cost.Evidence[0].CatalogRevision != 7 || service.State().PendingProducts == 0 {
		t.Fatalf("pricing rebuild replaced the last complete product: before=%+v after=%+v state=%+v", before, after, service.State())
	}
	s.events.mu.Lock()
	published := false
	for _, event := range s.events.events {
		if event.Type == "pricing_catalog_changed" {
			published = true
		}
	}
	s.events.mu.Unlock()
	if !published {
		t.Fatal("pricing refresh did not publish its change event")
	}
	status := httptest.NewRecorder()
	s.pricingCatalog(status, httptest.NewRequest(http.MethodGet, "/api/pricing-catalog", nil))
	if !strings.Contains(status.Body.String(), `"status":"done"`) || !strings.Contains(status.Body.String(), "pricing source HTTP 503") {
		t.Fatalf("status %s", status.Body)
	}
	restored, err := stats.LoadPricingCatalog(seed)
	if err != nil {
		t.Fatal(err)
	}
	restored.SetRefreshCachePath(filepath.Join(filepath.Dir(seed), "pricing-refresh.json"))
	if err := restored.RestoreRefreshCache(); err != nil {
		t.Fatal(err)
	}
	if restored.Revision() != 8 || restored.PricingInvalidationPending() {
		t.Fatalf("offline restore revision=%d pending=%v", restored.Revision(), restored.PricingInvalidationPending())
	}
}

func TestPricingStartupRetriesRestoredInvalidationOffline(t *testing.T) {
	s, seed := pricingLifecycleFixture(t)
	installPricingFixtureTransport(t, nil)
	s.pricingInvalidate = func(context.Context) error { return errors.New("fixture writer unavailable") }
	if _, accepted := s.startPricingRefresh("manual"); !accepted {
		t.Fatal("refresh rejected")
	}
	s.backgroundWork.Wait()
	if !s.pricing.PricingInvalidationPending() {
		t.Fatal("failed writer acknowledged pending work")
	}
	restored, err := stats.LoadPricingCatalog(seed)
	if err != nil {
		t.Fatal(err)
	}
	restored.SetRefreshCachePath(filepath.Join(filepath.Dir(seed), "pricing-refresh.json"))
	if err := restored.RestoreRefreshCache(); err != nil {
		t.Fatal(err)
	}
	s.pricing = restored
	s.pricingInvalidate = nil
	t.Setenv("YOMUX_TEST_DISABLE_PRICING_REFRESH", "1")
	s.pricingRefreshRun = func(context.Context) error { t.Error("offline startup fetched provider"); return nil }
	settled := make(chan struct{}, 1)
	s.pricingInvalidate = func(ctx context.Context) error {
		_, err := s.statsWriter.InvalidatePricingCatalog(ctx, float64(time.Now().Unix()))
		settled <- struct{}{}
		return err
	}
	s.startPricingLifecycle()
	select {
	case <-settled:
	case <-time.After(5 * time.Second):
		t.Fatal("pending invalidation did not retry")
	}
	s.serverCancel()
	s.stopBackgroundWork()
	if restored.PricingInvalidationPending() {
		t.Fatal("successful writer did not acknowledge pending work")
	}
}

func TestPricingFailureScheduleBackoffAndCancellation(t *testing.T) {
	s, _ := pricingLifecycleFixture(t)
	installPricingFixtureTransport(t, nil)
	if err := s.pricing.RefreshFromSources(context.Background()); err != nil {
		t.Fatal(err)
	}
	s.pricingInvalidate = func(context.Context) error { return errors.New("fixture writer outage") }
	s.pricingPeriodic = true
	s.pricingRefreshRun = func(context.Context) error { return errors.New("fixture provider outage") }
	for failures := 1; failures <= 10; failures++ {
		before := time.Now()
		if _, accepted := s.startPricingRefresh("manual"); !accepted {
			t.Fatal("refresh rejected")
		}
		s.backgroundWork.Wait()
		s.pricingRefreshMu.Lock()
		count, deadline := s.pricingFailures, s.pricingNextRefresh
		invalidationCount, invalidationDeadline := s.pricingInvalidationFailures, s.pricingNextInvalidation
		s.pricingRefreshMu.Unlock()
		minimum := pricingRetryDelay(failures)
		maximum := min(6*time.Hour, minimum+min(minimum, time.Hour))
		if count != failures || deadline.Before(before.Add(minimum)) || deadline.After(time.Now().Add(maximum)) {
			t.Fatalf("failure=%d deadline=%v", count, deadline)
		}
		if invalidationCount != failures || invalidationDeadline.Before(before.Add(minimum)) || invalidationDeadline.After(time.Now().Add(maximum)) {
			t.Fatalf("invalidation failure=%d deadline=%v", invalidationCount, invalidationDeadline)
		}
	}
	if pricingRetryDelay(10) != 6*time.Hour {
		t.Fatal("backoff cap")
	}
	entered := make(chan struct{})
	s.pricingRefreshRun = func(ctx context.Context) error { close(entered); <-ctx.Done(); return ctx.Err() }
	if _, accepted := s.startPricingRefresh("manual"); !accepted {
		t.Fatal("refresh rejected")
	}
	<-entered
	s.serverCancel()
	s.stopBackgroundWork()
	s.pricingRefreshMu.Lock()
	defer s.pricingRefreshMu.Unlock()
	if s.pricingRefreshing || !strings.Contains(s.pricingRefreshState["error"].(string), "context canceled") {
		t.Fatalf("cancellation state=%v", s.pricingRefreshState)
	}
}

func TestPricingInvalidationCoalescedManualRefreshRunsTrailingProviders(t *testing.T) {
	s, _ := pricingLifecycleFixture(t)
	installPricingFixtureTransport(t, nil)
	if err := s.pricing.RefreshFromSources(context.Background()); err != nil {
		t.Fatal(err)
	}
	entered, release := make(chan struct{}), make(chan struct{})
	s.pricingInvalidate = func(context.Context) error { close(entered); <-release; return nil }
	providers := 0
	s.pricingRefreshRun = func(context.Context) error { providers++; return nil }
	if _, accepted := s.startPricingRefresh("invalidation"); !accepted {
		t.Fatal("settlement rejected")
	}
	<-entered
	for i := 0; i < 2; i++ {
		response := httptest.NewRecorder()
		s.pricingCatalogRefresh(response, httptest.NewRequest(http.MethodPost, "/api/pricing-catalog/refresh", nil))
		if response.Code != http.StatusAccepted || !strings.Contains(response.Body.String(), `"coalesced":true`) {
			t.Fatalf("manual coalescing: %s", response.Body)
		}
	}
	close(release)
	s.backgroundWork.Wait()
	if providers != 1 {
		t.Fatalf("coalesced manual requests ran %d provider refreshes, want 1", providers)
	}
}

func TestPricingStartupOwnerLeasesStateAndRestoresOffline(t *testing.T) {
	for _, failed := range []bool{false, true} {
		t.Run(fmt.Sprintf("failed=%v", failed), func(t *testing.T) {
			s, seed := pricingLifecycleFixture(t)
			s.state = filepath.Dir(seed)
			s.port = 12345
			lease, err := s.initializePricingCache()
			if err != nil {
				t.Fatal(err)
			}
			t.Cleanup(func() { releaseInstanceRootLease(lease) })
			contender := &server{state: s.state, port: s.port, pricing: s.pricing}
			if other, err := contender.initializePricingCache(); err == nil {
				releaseInstanceRootLease(other)
				t.Fatal("second owner acquired pricing cache")
			}
			outage := false
			installPricingFixtureTransport(t, func(*http.Request) error {
				if outage {
					return errors.New("fixture provider outage")
				}
				return nil
			})
			if err := s.pricing.RefreshFromSources(context.Background()); err != nil {
				t.Fatal(err)
			}
			if failed {
				outage = true
				if err := s.pricing.RefreshFromSources(context.Background()); err == nil {
					t.Fatal("provider outage unexpectedly succeeded")
				}
			}
			releaseInstanceRootLease(lease)
			restored, err := stats.LoadPricingCatalog(seed)
			if err != nil {
				t.Fatal(err)
			}
			offline := &server{state: s.state, port: s.port, pricing: restored, statsWriter: s.statsWriter, refillContext: s.refillContext}
			lease2, err := offline.initializePricingCache()
			if err != nil {
				t.Fatal(err)
			}
			t.Cleanup(func() { releaseInstanceRootLease(lease2) })
			if restored.Revision() != 8 || !restored.PricingInvalidationPending() {
				t.Fatalf("offline startup revision=%d pending=%v", restored.Revision(), restored.PricingInvalidationPending())
			}
			status, _ := restored.RefreshResult()
			if (status == "failed") != failed || restored.RefreshDue(time.Now()) != failed {
				t.Fatalf("restored status=%s due=%v failed=%v", status, restored.RefreshDue(time.Now()), failed)
			}
			t.Setenv("YOMUX_TEST_DISABLE_PRICING_REFRESH", "0")
			t.Cleanup(func() { s.serverCancel(); offline.stopBackgroundWork() })
			before := time.Now()
			offline.startPricingLifecycle()
			offline.pricingRefreshMu.Lock()
			deadline := offline.pricingNextRefresh
			offline.pricingRefreshMu.Unlock()
			minimum, maximum := before.Add(24*time.Hour), time.Now().Add(25*time.Hour)
			if failed {
				minimum, maximum = before, time.Now().Add(time.Hour)
			}
			if deadline.Before(minimum) || deadline.After(maximum) {
				t.Fatalf("restored failed=%v deadline=%v outside [%v,%v]", failed, deadline, minimum, maximum)
			}
			s.serverCancel()
		})
	}
}

func TestPricingSchedulerRunsDueProviderAndSchedulesNextDay(t *testing.T) {
	s, _ := pricingLifecycleFixture(t)
	t.Setenv("YOMUX_TEST_DISABLE_PRICING_REFRESH", "0")
	called := make(chan struct{})
	s.pricingRefreshRun = func(context.Context) error { close(called); return nil }
	before := time.Now()
	s.startPricingLifecycle()
	s.pricingRefreshMu.Lock()
	first := s.pricingNextRefresh
	s.pricingNextRefresh = time.Now()
	s.notifyPricingScheduleLocked()
	s.pricingRefreshMu.Unlock()
	if first.Before(before) || first.After(time.Now().Add(time.Hour)) {
		t.Fatalf("initial seed deadline=%v", first)
	}
	select {
	case <-called:
	case <-time.After(5 * time.Second):
		t.Fatal("due provider refresh did not run")
	}
	waitFixtureCondition(t, "daily pricing deadline", 5*time.Second, func() bool {
		s.pricingRefreshMu.Lock()
		defer s.pricingRefreshMu.Unlock()
		return !s.pricingRefreshing && s.pricingNextRefresh.After(before.Add(24*time.Hour))
	})
	s.pricingRefreshMu.Lock()
	next := s.pricingNextRefresh
	s.pricingRefreshMu.Unlock()
	if next.After(time.Now().Add(25 * time.Hour)) {
		t.Fatalf("daily deadline=%v", next)
	}
	s.serverCancel()
	s.stopBackgroundWork()
}

func TestPricingAcknowledgementFailureRetainsPendingWork(t *testing.T) {
	s, seed := pricingLifecycleFixture(t)
	installPricingFixtureTransport(t, nil)
	if err := s.pricing.RefreshFromSources(context.Background()); err != nil {
		t.Fatal(err)
	}
	blocker := filepath.Join(filepath.Dir(seed), "blocked")
	if err := os.WriteFile(blocker, []byte("file"), 0600); err != nil {
		t.Fatal(err)
	}
	s.pricing.SetRefreshCachePath(filepath.Join(blocker, "cache.json"))
	s.pricingInvalidate = func(context.Context) error { return nil }
	if _, ok := s.startPricingRefresh("invalidation"); !ok {
		t.Fatal("retry rejected")
	}
	s.backgroundWork.Wait()
	if !s.pricing.PricingInvalidationPending() || s.pricingInvalidationFailures != 1 {
		t.Fatal("failed acknowledgement lost durable pending state")
	}
	s.pricing.SetRefreshCachePath(filepath.Join(filepath.Dir(seed), "pricing-refresh.json"))
	if _, ok := s.startPricingRefresh("invalidation"); !ok {
		t.Fatal("retry rejected")
	}
	s.backgroundWork.Wait()
	if s.pricing.PricingInvalidationPending() || s.pricingInvalidationFailures != 0 {
		t.Fatal("successful retry did not settle pending state")
	}
}

func TestPricingProviderHTTPObservesServerCancellation(t *testing.T) {
	s, _ := pricingLifecycleFixture(t)
	entered := make(chan struct{})
	var once sync.Once
	installPricingFixtureTransport(t, func(r *http.Request) error {
		once.Do(func() { close(entered) })
		<-r.Context().Done()
		return r.Context().Err()
	})
	if _, ok := s.startPricingRefresh("manual"); !ok {
		t.Fatal("refresh rejected")
	}
	<-entered
	s.serverCancel()
	s.stopBackgroundWork()
	if !strings.Contains(s.pricingRefreshState["error"].(string), "context canceled") {
		t.Fatalf("HTTP cancellation state=%v", s.pricingRefreshState)
	}
}

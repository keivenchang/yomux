// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"syscall"
	"testing"
	"time"

	"github.com/keivenchang/yomux/internal/stats"
)

func TestBackgroundWorkShutdownDrainsAndRejectsLateAdmission(t *testing.T) {
	s := &server{}
	entered, release, finished := make(chan struct{}), make(chan struct{}), make(chan struct{})
	if !s.startBackgroundWork(func() {
		close(entered)
		<-release
		close(finished)
	}) {
		t.Fatal("initial work rejected")
	}
	<-entered
	stopped := make(chan struct{})
	go func() {
		s.stopBackgroundWork()
		close(stopped)
	}()
	deadline := time.NewTimer(3 * time.Second)
	defer deadline.Stop()
	for {
		s.backgroundWorkMu.Lock()
		closed := s.backgroundWorkClosed
		s.backgroundWorkMu.Unlock()
		if closed {
			break
		}
		select {
		case <-deadline.C:
			close(release)
			t.Fatal("shutdown did not close admission")
		default:
			runtime.Gosched()
		}
	}
	if s.startBackgroundWork(func() { t.Error("late work ran") }) {
		t.Error("shutdown accepted late work")
	}
	select {
	case <-stopped:
		t.Error("shutdown returned before accepted work finished")
	default:
	}
	close(release)
	select {
	case <-stopped:
	case <-deadline.C:
		t.Fatal("shutdown did not drain accepted work")
	}
	select {
	case <-finished:
	default:
		t.Error("work was not finished at shutdown return")
	}
	s.stopBackgroundWork()
}

func TestStatsBackgroundAdmissionRejectsAfterStop(t *testing.T) {
	root := t.TempDir()
	if err := os.Chmod(root, 0700); err != nil {
		t.Fatal(err)
	}
	s := &server{
		pricing: &stats.PricingCatalog{}, refillRoot: root, events: newEventBroker(),
		operationState: operationState{operationsPath: filepath.Join(root, "operations.json"), operationsJournal: filepath.Join(root, "journal.jsonl")},
		refillRun: func(context.Context, time.Time, func(string)) (stats.AgentHistoryRefillResult, error) {
			t.Error("refill admitted after stop")
			return stats.AgentHistoryRefillResult{}, nil
		},
	}
	s.stopBackgroundWork()
	if generation := s.queueMetadataRefresh(true, []string{"fixture"}, nil, nil, 1); generation != 0 || s.metadataBuilding {
		t.Fatalf("metadata refresh admitted after stop: generation=%d building=%v", generation, s.metadataBuilding)
	}
	for _, route := range []struct {
		path string
		run  http.HandlerFunc
	}{{"/api/stats-refill", s.statsRefill}, {"/api/pricing-catalog/refresh", s.pricingCatalogRefresh}} {
		response := httptest.NewRecorder()
		route.run(response, httptest.NewRequest(http.MethodPost, route.path, nil))
		if response.Code != http.StatusServiceUnavailable {
			t.Errorf("%s status = %d, want 503", route.path, response.Code)
		}
	}
	lock, err := os.OpenFile(filepath.Join(root, "owner.lock"), os.O_RDWR, 0600)
	if err != nil {
		t.Fatal(err)
	}
	defer lock.Close()
	if err := syscall.Flock(int(lock.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		t.Fatalf("rejected refill retained ownership lease: %v", err)
	}
	defer syscall.Flock(int(lock.Fd()), syscall.LOCK_UN)
}

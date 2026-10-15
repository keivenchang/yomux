// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

func TestTmuxTopologyReadRetainsOwnershipUntilGenerationPublication(t *testing.T) {
	direct := func(s *server) (goTmuxTopology, error) { return s.tmuxTopologySnapshot(true) }
	for _, pair := range []struct {
		name          string
		first, second func(*server) (goTmuxTopology, error)
	}{
		{"snapshot/snapshot", direct, direct},
		{"tmux/tmux", tmuxHTTPTopology, tmuxHTTPTopology},
		{"tmux/snapshot", tmuxHTTPTopology, direct},
		{"snapshot/tmux", direct, tmuxHTTPTopology},
	} {
		t.Run(pair.name, func(t *testing.T) { assertTmuxTopologyReadPublicationOwnership(t, pair.first, pair.second) })
	}
}

func tmuxHTTPTopology(s *server) (goTmuxTopology, error) {
	response := httptest.NewRecorder()
	s.tmux(response, httptest.NewRequest(http.MethodGet, "/api/tmux", nil))
	if response.Code != http.StatusOK {
		return goTmuxTopology{}, fmt.Errorf("tmux status %d: %s", response.Code, response.Body.String())
	}
	var payload struct {
		Sessions   []string `json:"sessions"`
		Generation uint64   `json:"topology_generation"`
	}
	if err := json.Unmarshal(response.Body.Bytes(), &payload); err != nil {
		return goTmuxTopology{}, err
	}
	return goTmuxTopology{sessions: payload.Sessions, generation: payload.Generation}, nil
}

func TestTmuxRosterHTTPDiscoveryFailurePreservesKnownCacheStatus(t *testing.T) {
	for _, fixture := range []struct {
		name    string
		initial []string
		known   bool
		status  int
	}{
		{"unknown", nil, false, http.StatusServiceUnavailable},
		{"known-empty", []string{}, true, http.StatusOK},
		{"known-session", []string{"synthetic-cached"}, true, http.StatusOK},
	} {
		t.Run(fixture.name, func(t *testing.T) {
			s := &server{}
			s.tmuxSessionRosterFn = func() ([]string, error) {
				return nil, errors.New("synthetic roster discovery failure")
			}
			if fixture.known {
				s.rememberTmuxSessionRoster(fixture.initial, nil)
			}
			response := httptest.NewRecorder()
			s.tmux(response, httptest.NewRequest(http.MethodGet, "/api/tmux", nil))
			if response.Code != fixture.status {
				t.Fatalf("status = %d; want %d: %s", response.Code, fixture.status, response.Body.String())
			}
			var payload struct {
				Sessions []string `json:"sessions"`
				Errors   []string `json:"errors"`
			}
			if err := json.Unmarshal(response.Body.Bytes(), &payload); err != nil {
				t.Fatal(err)
			}
			if len(payload.Sessions) != len(fixture.initial) || len(payload.Sessions) > 0 && payload.Sessions[0] != fixture.initial[0] || len(payload.Errors) != 1 || payload.Errors[0] != "synthetic roster discovery failure" {
				t.Fatalf("cached error response = %s", response.Body.String())
			}
		})
	}
}

func assertTmuxTopologyReadPublicationOwnership(t *testing.T, firstReader, secondReader func(*server) (goTmuxTopology, error)) {
	s := &server{events: newEventBroker()}
	firstRead, secondRead := make(chan struct{}), make(chan struct{})
	var reads atomic.Int32
	s.tmuxSessionRosterFn = func() ([]string, error) {
		if reads.Add(1) == 1 {
			close(firstRead)
			return []string{"synthetic-old"}, nil
		}
		// Signal only after the cache changes, making the stale-publication
		// schedule independent of the returning reader's next instruction.
		s.rememberTmuxSessionRoster([]string{"synthetic-new"}, nil)
		close(secondRead)
		return []string{"synthetic-new"}, nil
	}
	// Hold the generation owner, rather than delaying tmux itself: a roster
	// read must retain fetch ownership until this publication is possible.
	s.metadataTopologyMu.Lock()
	var release sync.Once
	unlock := func() { release.Do(s.metadataTopologyMu.Unlock) }
	defer unlock()
	type result struct {
		topology goTmuxTopology
		err      error
	}
	first, second := make(chan result, 1), make(chan result, 1)
	read := func(reader func(*server) (goTmuxTopology, error), done chan<- result) {
		topology, err := reader(s)
		done <- result{topology, err}
	}
	go read(firstReader, first)
	select {
	case <-firstRead:
	case <-time.After(2 * time.Second):
		t.Fatal("first roster read did not start")
	}
	go read(secondReader, second)
	premature := false
	select {
	case <-secondRead:
		premature = true
	case <-time.After(100 * time.Millisecond):
	}
	unlock()
	for index, done := range []<-chan result{first, second} {
		select {
		case got := <-done:
			wantSession := []string{"synthetic-old", "synthetic-new"}[index]
			if got.err != nil || got.topology.generation != uint64(index+1) || len(got.topology.sessions) != 1 || got.topology.sessions[0] != wantSession {
				t.Errorf("read %d = sessions %v, generation %d, error %v; want %s generation %d", index+1, got.topology.sessions, got.topology.generation, got.err, wantSession, index+1)
			}
		case <-time.After(2 * time.Second):
			t.Fatalf("read %d did not complete after publication release", index+1)
		}
	}
	if premature {
		t.Error("second roster read replaced the cache before first topology generation publication")
	}
}

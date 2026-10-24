// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestShutdownRetainsDependenciesUntilRequestReturns(t *testing.T) {
	// Request ordering must not depend on durable owner-record I/O.
	owner := &goBackgroundOwner{owner: true}
	s := &server{background: owner}
	request := httptest.NewRequest(http.MethodGet, "/fixture", nil)
	finish, ok := s.beginRequest(request)
	if !ok {
		t.Fatal("request rejected before shutdown")
	}
	done := make(chan struct{})
	go func() { s.shutdown(); close(done) }()
	select {
	case <-request.Context().Done():
	case <-time.After(time.Second):
		t.Fatal("shutdown did not cancel request")
	}
	owner.mu.Lock()
	alive := owner.owner
	owner.mu.Unlock()
	if !alive {
		t.Fatal("dependency closed before request returned")
	}
	if _, admitted := s.beginRequest(httptest.NewRequest(http.MethodGet, "/late", nil)); admitted {
		t.Fatal("shutdown accepted a new request")
	}
	finish()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("shutdown did not drain")
	}
	owner.mu.Lock()
	alive = owner.owner
	owner.mu.Unlock()
	if alive {
		t.Fatal("dependency remained open after request drain")
	}
}

func TestRequestAdmissionCancelsHijackedTransportContext(t *testing.T) {
	s := &server{}
	request := httptest.NewRequest(http.MethodGet, "/ws", nil)
	finish, ok := s.beginRequest(request)
	if !ok {
		t.Fatal("request rejected")
	}
	defer finish()
	closed := make(chan struct{})
	stop := context.AfterFunc(request.Context(), func() { close(closed) })
	defer stop()
	s.stopRequestAdmission()
	select {
	case <-closed:
	case <-time.After(time.Second):
		t.Fatal("transport cancellation did not run")
	}
}

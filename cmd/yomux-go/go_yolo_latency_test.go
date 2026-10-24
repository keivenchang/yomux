// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"
)

func TestGoYoloWarmReadersDoNotWaitForForcedRosterFetch(t *testing.T) {
	for _, reader := range []string{"get-all", "get-session", "publish-state"} {
		t.Run(reader, func(t *testing.T) {
			const session = "warm-session"
			config := t.TempDir()
			store, err := newGoYoloStore(config)
			if err != nil {
				t.Fatal(err)
			}
			defer store.close()
			rosterStarted := make(chan struct{})
			releaseRoster := make(chan struct{})
			var startedOnce, releaseOnce sync.Once
			release := func() { releaseOnce.Do(func() { close(releaseRoster) }) }
			defer release()
			s := &server{config: config, yolo: store, events: newEventBroker(), tmuxMetadataState: tmuxMetadataState{
				metadataPayload: map[string]any{
					"sessions": map[string]any{session: map[string]any{
						"session_identity": goTmuxSessionIdentity(session, session), "agents": []map[string]any{},
					}},
					"session_order": []string{session}, "topology_generation": uint64(1), "errors": []string{},
				},
				metadataGeneration: 4, metadataTopologyGeneration: 1, metadataBuiltAt: time.Now(),
				metadataTopologySignature: tmuxTopologySignature([]string{session}, map[string]string{session: session}),
				tmuxRoster:                []string{session}, tmuxRosterIdentities: map[string]string{session: session},
				tmuxRosterKnown: true, tmuxRosterSampledAt: time.Now(),
				tmuxSessionRosterFn: func() ([]string, error) {
					startedOnce.Do(func() { close(rosterStarted) })
					<-releaseRoster
					return []string{session}, nil
				},
			}}
			forcedDone := make(chan struct{})
			go func() {
				_, _ = s.tmuxTopologySnapshot(true)
				close(forcedDone)
			}()
			<-rosterStarted
			defer func() { release(); <-forcedDone }()
			readDone := make(chan *httptest.ResponseRecorder, 1)
			go func() {
				response := httptest.NewRecorder()
				if reader == "publish-state" {
					s.publishGoYoloState(session)
				} else {
					path := "/api/auto-approve"
					if reader == "get-session" {
						path += "?session=" + session
					}
					s.autoApprove(response, httptest.NewRequest(http.MethodGet, path, nil))
				}
				readDone <- response
			}()
			select {
			case response := <-readDone:
				if reader != "publish-state" && (response.Code != http.StatusOK || !strings.Contains(response.Body.String(), session)) {
					t.Fatalf("warm YO response = %d %s", response.Code, response.Body.String())
				}
			case <-time.After(time.Second):
				release()
				<-readDone
				t.Fatal("warm YO reader waited for an unrelated forced tmux roster fetch")
			}
		})
	}
}

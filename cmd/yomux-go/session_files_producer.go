// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"context"
	"encoding/json"
	"net/http"
	"time"

	"github.com/keivenchang/yomux/internal/finder"
)

// A producer belongs to the view and incarnation, not to the first browser.
// Subscribers replay an immutable bounded journal; slow/disconnected tabs never
// hold the Git workers or cancel a sibling's request.
type sessionFilesProducer struct {
	chunks      []json.RawMessage
	bytes       int
	subscribers int
	changed     chan struct{}
	done        bool
	payload     map[string]any
	err         *finder.Error
}

func (s *server) sharedSessionFilesProduce(ctx context.Context, plan sessionFilesPlan, roots []string, agents []finder.SessionFileAgent, emit func(map[string]any) error) (map[string]any, *finder.Error) {
	key := plan.viewID + ":" + plan.request.sessionIdentity
	state := &s.sessionFilesCache
	state.mu.Lock()
	if state.producers == nil {
		state.producers = make(map[string]*sessionFilesProducer)
	}
	producer := state.producers[key]
	if producer == nil {
		if len(state.producers) >= sessionFilesCacheMaxEntries {
			state.mu.Unlock()
			return nil, &finder.Error{Status: http.StatusServiceUnavailable, Code: "session_files_busy", Message: "session-files producer capacity reached", Retryable: true}
		}
		producer = &sessionFilesProducer{changed: make(chan struct{})}
		state.producers[key] = producer
		admitted := s.startBackgroundWork(func() {
			parent := s.producerContext
			if parent == nil {
				parent = context.WithoutCancel(ctx)
			}
			workCtx, cancel := context.WithTimeout(parent, 60*time.Second)
			defer cancel()
			payload, err := s.sessionFilesPlanProduceWithInputs(workCtx, plan, roots, agents, func(chunk map[string]any) error {
				encoded, marshalErr := json.Marshal(chunk)
				if marshalErr != nil {
					return marshalErr
				}
				state.mu.Lock()
				defer state.mu.Unlock()
				if producer.bytes+len(encoded) > 4*sessionFilesCacheMaxEntryBytes || state.producerBytes+len(encoded) > sessionFilesCacheMaxBytes {
					return &finder.Error{Code: "session_files_stream_too_large", Message: "session-files stream exceeds replay budget"}
				}
				producer.chunks = append(producer.chunks, encoded)
				producer.bytes += len(encoded)
				state.producerBytes += len(encoded)
				close(producer.changed)
				producer.changed = make(chan struct{})
				return nil
			})
			state.mu.Lock()
			producer.payload, producer.err, producer.done = payload, err, true
			if producer.subscribers == 0 {
				state.producerBytes -= producer.bytes
			}
			delete(state.producers, key)
			close(producer.changed)
			state.mu.Unlock()
		})
		if !admitted {
			producer.done = true
			producer.err = &finder.Error{Status: 503, Code: "server_stopping", Message: "server is stopping", Retryable: true}
			delete(state.producers, key)
			close(producer.changed)
		}
	}
	producer.subscribers++
	state.mu.Unlock()
	defer func() {
		state.mu.Lock()
		producer.subscribers--
		if producer.done && producer.subscribers == 0 {
			state.producerBytes -= producer.bytes
		}
		state.mu.Unlock()
	}()
	for cursor := 0; ; {
		state.mu.Lock()
		chunks := append([]json.RawMessage(nil), producer.chunks[cursor:]...)
		cursor += len(chunks)
		done, changed, payload, producerErr := producer.done, producer.changed, producer.payload, producer.err
		state.mu.Unlock()
		for _, encoded := range chunks {
			if emit != nil {
				var chunk map[string]any
				if err := json.Unmarshal(encoded, &chunk); err != nil {
					return nil, &finder.Error{Status: 500, Code: "session_files_replay_failed", Message: err.Error()}
				}
				if err := emit(chunk); err != nil {
					return nil, &finder.Error{Status: 500, Code: "session_files_stream_failed", Message: err.Error()}
				}
			}
		}
		if done {
			return payload, producerErr
		}
		select {
		case <-ctx.Done():
			return nil, &finder.Error{Status: 499, Code: "cancelled", Message: ctx.Err().Error()}
		case <-changed:
		}
	}
}

// Hints contain repository paths only. The requested Git comparison is always
// recomputed; counts from another comparison never cross this boundary.
func (s *server) sessionFilesHintedRoots(plan sessionFilesPlan, roots []string) []string {
	state := &s.sessionFilesCache
	state.mu.Lock()
	records := make([]sessionFilesCacheRecord, 0)
	if state.records != nil {
		for _, key := range state.records.KeysLeastRecentFirst() {
			record, _ := state.records.Peek(key)
			if record.SessionIdentity == plan.request.sessionIdentity {
				records = append(records, record)
			}
		}
	}
	state.mu.Unlock()
	wanted, _ := json.Marshal(roots)
	result := append([]string(nil), roots...)
	seen := make(map[string]bool)
	for _, root := range roots {
		seen[root] = true
	}
	for _, record := range records {
		payload := sessionFilesCachePayload(record)
		if payload["session"] != plan.request.session {
			continue
		}
		prior, _ := json.Marshal(payload["discovery_roots"])
		if string(prior) != string(wanted) {
			continue
		}
		if hours, ok := payload["hours"].(float64); !ok || hours != plan.request.hours {
			continue
		}
		for _, chunk := range sessionFilesCachedChunks(payload) {
			repos, _ := chunk["repos"].([]map[string]any)
			for _, repo := range repos {
				root, _ := repo["repo"].(string)
				if root != "" && !seen[root] {
					result = append(result, root)
					seen[root] = true
				}
			}
		}
	}
	return result
}

// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/keivenchang/yomux/internal/atomicfile"
)

const (
	attentionAckMaxKeys      = 4096
	attentionAckKeyMaxBytes  = 512
	attentionAckTTL          = 7 * 24 * time.Hour
	attentionAckBodyMaxBytes = 16 * 1024
)

type attentionAckState struct {
	protected map[string]bool
	mu        sync.Mutex
	path      string
	loaded    bool
	keys      map[string]float64
	nowFunc   func() time.Time
}

func (s *server) registerAttentionAckRoute(mux *http.ServeMux) {
	mux.Handle("/api/attention-ack", s.protected(http.HandlerFunc(s.acknowledgeAttention), "admin"))
}

func (s *server) attentionAckStore() *attentionAckState {
	s.attentionAckInitMu.Lock()
	defer s.attentionAckInitMu.Unlock()
	if s.attentionAcks == nil {
		store := &attentionAckState{path: filepath.Join(s.state, "go-attention-acks.json"), keys: map[string]float64{}, nowFunc: time.Now}
		if s.state == "" {
			store.path = filepath.Join(s.config, "go-attention-acks.json")
		}
		s.attentionAcks = store
	}
	return s.attentionAcks
}

func (s *server) attentionAckNow() time.Time {
	if s.statsNow != nil {
		return s.statsNow()
	}
	return time.Now()
}

func (store *attentionAckState) loadLocked() error {
	if store.loaded {
		return nil
	}
	data, err := os.ReadFile(store.path)
	if errors.Is(err, os.ErrNotExist) {
		store.loaded = true
		return nil
	}
	if err != nil {
		return fmt.Errorf("read attention acknowledgements: %w", err)
	}
	var payload struct {
		Keys      map[string]float64 `json:"keys"`
		Protected map[string]bool    `json:"protected"`
	}
	if err := json.Unmarshal(data, &payload); err != nil {
		return fmt.Errorf("decode attention acknowledgements: %w", err)
	}
	keys := make(map[string]float64, len(payload.Keys))
	for key, timestamp := range payload.Keys {
		if validAttentionAckKey(key) && timestamp > 0 {
			keys[key] = timestamp
		}
	}
	store.keys = keys
	store.protected = payload.Protected
	store.loaded = true
	return nil
}

func validAttentionAckKey(key string) bool {
	return strings.TrimSpace(key) == key && key != "" && len([]byte(key)) <= attentionAckKeyMaxBytes
}

func (store *attentionAckState) pruneLocked(now time.Time) {
	cutoff := float64(now.Add(-attentionAckTTL).UnixNano()) / 1e9
	for key, timestamp := range store.keys {
		if timestamp < cutoff && !store.protected[key] {
			delete(store.keys, key)
		}
	}
	if len(store.keys) <= attentionAckMaxKeys {
		return
	}
	keys := make([]string, 0, len(store.keys))
	for key := range store.keys {
		if !store.protected[key] {
			keys = append(keys, key)
		}
	}
	sort.Slice(keys, func(i, j int) bool {
		if store.keys[keys[i]] == store.keys[keys[j]] {
			return keys[i] < keys[j]
		}
		return store.keys[keys[i]] < store.keys[keys[j]]
	})
	for _, key := range keys[:min(len(keys), len(store.keys)-attentionAckMaxKeys)] {
		delete(store.keys, key)
	}
}

func (store *attentionAckState) acknowledged(key string, now time.Time) (float64, bool, error) {
	store.mu.Lock()
	defer store.mu.Unlock()
	if err := store.loadLocked(); err != nil {
		return 0, false, err
	}
	store.pruneLocked(now)
	timestamp, ok := store.keys[key]
	return timestamp, ok, nil
}

// Release protection only after the lifecycle snapshot durably contains settlement.
func (s *server) attentionSettlementsCommitted(pendingKeys []string) error {
	store := s.attentionAckStore()
	store.mu.Lock()
	defer store.mu.Unlock()
	if err := store.loadLocked(); err != nil {
		return err
	}
	changed := false
	pending := map[string]bool{}
	for _, key := range pendingKeys {
		pending[key] = true
	}
	for key := range store.protected {
		if !pending[key] {
			delete(store.protected, key)
			changed = true
		}
	}
	if !changed {
		return nil
	}
	encoded, err := json.Marshal(map[string]any{"keys": store.keys, "protected": store.protected})
	if err != nil {
		return err
	}
	return atomicfile.Write(store.path, encoded)
}

func (s *server) acknowledgeAttention(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	r.Body = http.MaxBytesReader(w, r.Body, attentionAckBodyMaxBytes)
	var payload struct {
		Keys []string `json:"keys"`
		Key  string   `json:"key"`
	}
	decoder := json.NewDecoder(r.Body)
	if err := decoder.Decode(&payload); err != nil {
		jsonResponse(w, http.StatusBadRequest, map[string]any{"error": "invalid attention acknowledgement request"})
		return
	}
	if err := decoder.Decode(new(any)); !errors.Is(err, io.EOF) {
		jsonResponse(w, http.StatusBadRequest, map[string]any{"error": "invalid attention acknowledgement request"})
		return
	}
	keys := payload.Keys
	if len(keys) == 0 && payload.Key != "" {
		keys = []string{payload.Key}
	}
	unique := make([]string, 0, len(keys))
	seen := make(map[string]bool, len(keys))
	for _, key := range keys {
		if !validAttentionAckKey(key) || seen[key] {
			continue
		}
		seen[key] = true
		unique = append(unique, key)
	}
	if len(unique) == 0 {
		jsonResponse(w, http.StatusBadRequest, map[string]any{"error": "attention acknowledgement keys required"})
		return
	}
	liveKeys := map[string]bool{}
	if s.agentStatusCollector != nil {
		liveKeys = s.agentStatusCollector.CurrentAttentionKeys()
	}
	store := s.attentionAckStore()
	store.mu.Lock()
	defer store.mu.Unlock()
	if err := store.loadLocked(); err != nil {
		jsonResponse(w, http.StatusInternalServerError, map[string]any{"error": "attention acknowledgement state unavailable"})
		return
	}
	oldKeys := make(map[string]float64, len(store.keys))
	oldProtected := make(map[string]bool, len(store.protected))
	for key, value := range store.keys {
		oldKeys[key] = value
	}
	for key, value := range store.protected {
		oldProtected[key] = value
	}
	now := s.attentionAckNow()
	store.pruneLocked(now)
	nowSeconds := float64(now.UnixNano()) / 1e9
	newlyAcknowledged := make([]string, 0, len(unique))
	acknowledgedAt := make(map[string]float64, len(unique))
	if store.protected == nil {
		store.protected = map[string]bool{}
	}
	for _, key := range unique {
		if liveKeys[key] {
			store.protected[key] = true
		}
		if _, exists := store.keys[key]; !exists {
			newlyAcknowledged = append(newlyAcknowledged, key)
			store.keys[key] = nowSeconds
		}
		acknowledgedAt[key] = store.keys[key]
	}
	store.pruneLocked(now)
	encoded, err := json.Marshal(map[string]any{"keys": store.keys, "protected": store.protected})
	if err == nil {
		if mkdirErr := os.MkdirAll(filepath.Dir(store.path), 0o700); mkdirErr != nil {
			err = mkdirErr
		} else {
			err = atomicfile.Write(store.path, encoded)
		}
	}
	if err != nil {
		store.keys, store.protected = oldKeys, oldProtected
		jsonResponse(w, http.StatusInternalServerError, map[string]any{"error": "attention acknowledgement state unavailable"})
		return
	}
	response := map[string]any{"ok": true, "acknowledged": unique, "acknowledged_at": acknowledgedAt, "changed": len(newlyAcknowledged) > 0}
	if s.events != nil {
		eventTimes := make(map[string]float64, len(unique))
		for _, key := range unique {
			eventTimes[key] = acknowledgedAt[key]
		}
		if _, err := s.events.publish("attention_acks_changed", map[string]any{"acknowledged": unique, "acknowledged_at": eventTimes}); err != nil {
			jsonResponse(w, http.StatusInternalServerError, map[string]any{"error": "attention acknowledgement update unavailable"})
			return
		}
	}
	jsonResponse(w, http.StatusOK, response)
}

// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"path/filepath"
	"syscall"
	"time"

	"github.com/keivenchang/yomux/internal/atomicfile"
	"github.com/keivenchang/yomux/internal/productenv"
	"github.com/keivenchang/yomux/internal/stats"
)

// The host lease is separate from database ownership: other tabs and server
// instances attach to the same operation instead of starting parallel scans.
func (s *server) statsRefill(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet && r.Method != http.MethodPost {
		http.Error(w, "method not allowed", 405)
		return
	}
	s.refillMu.Lock()
	defer s.refillMu.Unlock()
	root := s.refillRoot
	if root == "" {
		hostTmp := "/tmp"
		// A private tmux fixture represents an isolated host and must never
		// contend with the operator runtime during automated tests.
		if productenv.Value("TMUX_SOCKET") != "" {
			hostTmp = os.TempDir()
		}
		root = filepath.Join(hostTmp, fmt.Sprintf("yomux-refill-%d", os.Getuid()))
	}
	if err := os.MkdirAll(root, 0700); err != nil {
		jsonResponse(w, 503, map[string]any{"error": err.Error()})
		return
	}
	info, rootErr := os.Lstat(root)
	if rootErr != nil || !info.IsDir() || info.Mode().Perm()&0077 != 0 || info.Sys().(*syscall.Stat_t).Uid != uint32(os.Getuid()) {
		jsonResponse(w, 503, map[string]any{"error": "Refill lease directory is not private"})
		return
	}
	lock, err := os.OpenFile(filepath.Join(root, "owner.lock"), os.O_CREATE|os.O_RDWR, 0600)
	if err != nil {
		jsonResponse(w, 503, map[string]any{"error": err.Error()})
		return
	}
	held := false
	defer func() {
		if !held {
			_ = lock.Close()
		}
	}()
	lockErr := syscall.Flock(int(lock.Fd()), syscall.LOCK_EX|syscall.LOCK_NB)
	if lockErr != nil && !errors.Is(lockErr, syscall.EWOULDBLOCK) {
		jsonResponse(w, 503, map[string]any{"error": lockErr.Error()})
		return
	}
	readStatus := func() map[string]any {
		data, err := os.ReadFile(filepath.Join(root, "status.json"))
		if err != nil {
			return map[string]any{"state": "idle"}
		}
		var status map[string]any
		if json.Unmarshal(data, &status) != nil {
			return map[string]any{"state": "idle"}
		}
		// Publication precedes lease release. Do not advertise completion
		// until an immediate retry can acquire ownership of a new refill.
		if lockErr != nil && (status["state"] == "succeeded" || status["state"] == "failed") {
			status["state"] = "running"
			delete(status, "message")
		}
		if lockErr == nil && (status["state"] == "running" || status["state"] == "queued") {
			message := "Couldn’t refill agent history. Your previous data is unchanged."
			if status["phase"] == "Rebuilding charts" {
				message = "Refill was interrupted while rebuilding charts. Try again."
			}
			return map[string]any{"state": "failed", "message": message}
		}
		return status
	}
	if r.Method == http.MethodGet || lockErr != nil {
		jsonResponse(w, 200, readStatus())
		return
	}
	if s.refillRun == nil && (s.tokenCollector == nil || s.statsWriter == nil) {
		jsonResponse(w, 503, map[string]any{"error": "Agent history collector unavailable"})
		return
	}
	end := time.Now()
	id := fmt.Sprintf("stats-refill-%d", end.UnixNano())
	if err := s.acceptOperationWithDetails(id, "", "/api/stats-refill", "stats_refill", end.Add(10*time.Minute).UnixMilli(), map[string]any{"phase": "Reading history"}, nil, map[string]any{"window_start": end.Add(-24*time.Hour).UnixNano() / 1e6, "window_end": end.UnixNano() / 1e6}); err != nil {
		jsonResponse(w, 503, map[string]any{"error": err.Error()})
		return
	}
	status := map[string]any{"state": "running", "operation_id": id, "phase": "Reading history", "window_start": float64(end.Add(-24*time.Hour).UnixNano()) / 1e9, "window_end": float64(end.UnixNano()) / 1e9}
	if err := writeRefillStatus(root, status); err != nil {
		_, _ = s.terminalizeOperation(id, map[string]any{"state": "failed", "error": err.Error()}, 500)
		jsonResponse(w, 503, map[string]any{"error": err.Error()})
		return
	}
	responseStatus := cloneMap(status)
	held = true
	parent := s.refillContext
	if parent == nil {
		parent = context.Background()
	}
	if !s.startBackgroundWork(func() {
		defer lock.Close()
		ctx, cancel := context.WithTimeout(parent, 10*time.Minute)
		defer cancel()
		progress := func(phase string) {
			status["phase"] = phase
			if err := s.refillProgress(id, phase); err != nil {
				slog.Error("refill progress failed", "error", err)
			}
			if err := writeRefillStatus(root, status); err != nil {
				slog.Error("refill status failed", "error", err)
			}
		}
		run := s.refillRun
		if run == nil {
			run = func(ctx context.Context, end time.Time, progress func(string)) (stats.AgentHistoryRefillResult, error) {
				return s.tokenCollector.RefillAgentHistory(ctx, s.statsWriter, end, progress)
			}
		}
		rebuilt, err := run(ctx, end, progress)
		gaps := rebuilt.Gaps
		message := "Agent history refilled."
		state := "succeeded"
		if err != nil {
			state = "failed"
			message = "Couldn’t refill agent history. Your previous data is unchanged."
		} else {
			if len(gaps) > 0 {
				message = "Agent history refilled with gaps."
				for _, gap := range gaps {
					name := map[string]string{"claude": "Claude", "codex": "Codex", "opencode": "OpenCode"}[gap]
					message += " " + name + " history is unavailable."
				}
			}
			// Await only this transaction's finite publication targets; live
			// collection continues to create unrelated and newer invalidations.
			if s.statsLifecycle != nil {
				for ctx.Err() == nil {
					sequence := s.statsLifecycle.CompletedRuns()
					ready, publishErr := s.statsWriter.RefillPublicationReady(ctx, rebuilt.PublicationTargets, time.Now())
					if publishErr != nil {
						cancel()
						break
					}
					if ready {
						break
					}
					s.statsLifecycle.NotifyAppend()
					if waitErr := s.statsLifecycle.WaitForRunAfter(ctx, sequence); waitErr != nil {
						break
					}
				}
				if ctx.Err() != nil {
					state = "failed"
					message = "Agent history was rebuilt, but charts are unavailable. Try again."
				} else if refreshErr := s.statsService.Refresh(ctx); refreshErr != nil {
					state = "failed"
					message = "Agent history was rebuilt, but charts are unavailable. Try again."
				}
			}
		}
		status["state"] = state
		status["message"] = message
		status["gaps"] = gaps
		if err != nil {
			slog.Error("agent history refill failed", "error", err)
		}
		if _, terminalErr := s.terminalizeOperation(id, map[string]any{"state": state, "message": message, "gaps": gaps}, 200); terminalErr != nil {
			slog.Error("refill terminal receipt failed", "error", terminalErr)
		}
		if writeErr := writeRefillStatus(root, status); writeErr != nil {
			slog.Error("refill terminal status failed", "error", writeErr)
		}
	}) {
		held = false
		status["state"] = "failed"
		status["message"] = "Server is shutting down. Try again after restart."
		_, _ = s.terminalizeOperation(id, cloneMap(status), http.StatusServiceUnavailable)
		_ = writeRefillStatus(root, status)
		jsonResponse(w, http.StatusServiceUnavailable, status)
		return
	}
	jsonResponse(w, http.StatusAccepted, responseStatus)
}

func writeRefillStatus(root string, status map[string]any) error {
	data, err := json.Marshal(status)
	if err != nil {
		return err
	}
	path := filepath.Join(root, "status.json")
	return atomicfile.Write(path, data)
}

func (s *server) refillProgress(id, phase string) error {
	s.operationsMu.Lock()
	defer s.operationsMu.Unlock()
	record := s.operations[id]
	if record == nil {
		return errors.New("refill operation missing")
	}
	updated := *record
	updated.State = "running"
	updated.Progress = map[string]any{"phase": phase}
	updated.Receipt = cloneMap(record.Receipt)
	updated.Receipt["state"] = "running"
	if operation, ok := record.Receipt["operation"].(map[string]any); ok {
		wire := cloneMap(operation)
		wire["progress"] = cloneMap(updated.Progress)
		updated.Receipt["operation"] = wire
	}
	updated.UpdatedAt = time.Now().UnixMilli()
	if err := appendOperationJournal(s.operationsJournal, map[string]any{"version": operationJournalVersion, "type": "operation", "epoch": record.Epoch, "record": &updated}); err != nil {
		return err
	}
	*record = updated
	return s.saveOperationsLocked()
}

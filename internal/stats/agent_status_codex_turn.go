// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package stats

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/keivenchang/yomux/internal/tailfile"
)

const maxCodexTurnTailBytes = 256 * 1024
const maxCodexTurnTailLines = 4096

// CodexTurnStateReader uses the existing process-owned transcript discovery and
// bounded tail reader. It keeps no lifecycle cache that could survive PID reuse,
// transcript replacement, or a resumed process. Recent item events establish an
// active turn even when its start has scrolled out of the bounded suffix.
func CodexTurnStateReader(root string) func(context.Context, int, float64) AgentStatus {
	return func(ctx context.Context, pid int, startedAt float64) AgentStatus {
		if ctx.Err() != nil || pid <= 0 || startedAt <= 0 {
			return ""
		}
		targets := ProcessOpenFiles(pid)
		if state, known := codexPaginatedTurnState(ctx, root, targets, startedAt); known {
			if codexWriterThread(root, targets) != codexWriterThread(root, ProcessOpenFiles(pid)) {
				return AgentStatusUnavailable
			}
			return state
		}
		path := CodexTranscriptFromProcess(pid, root)
		if path == "" {
			return AgentStatusUnavailable
		}
		file, err := os.Open(path)
		if err != nil {
			return AgentStatusUnavailable
		}
		defer file.Close()
		before, err := file.Stat()
		if err != nil {
			return AgentStatusUnavailable
		}
		text, err := tailfile.ReadFile(file, maxCodexTurnTailLines, maxCodexTurnTailBytes)
		after, statErr := os.Stat(path)
		if err != nil || statErr != nil || !os.SameFile(before, after) || after.Size() < before.Size() || (after.Size() == before.Size() && !after.ModTime().Equal(before.ModTime())) || CodexTranscriptFromProcess(pid, root) != path {
			return AgentStatusUnavailable
		}
		return codexTurnTailState(text, startedAt)
	}
}

// Paginated-history clients need not keep the rollout open. Their process-held
// writer lock supplies exact thread identity; multiple locks remain ambiguous.
func codexHistoryHome(root string) string {
	home := filepath.Dir(filepath.Clean(root))
	if resolved, err := filepath.EvalSymlinks(home); err == nil {
		home = resolved
	}
	return home
}

func codexWriterThread(root string, targets []string) string {
	home := codexHistoryHome(root)
	thread := ""
	for _, target := range targets {
		if filepath.Dir(target) != filepath.Join(home, "thread-writer-locks") || filepath.Ext(target) != ".lock" {
			continue
		}
		id := strings.TrimSuffix(filepath.Base(target), ".lock")
		if id == "" || strings.HasPrefix(id, ".") || len(id) > MaxIdentityBytes {
			continue
		}
		if thread != "" && thread != id {
			return ""
		}
		thread = id
	}
	return thread
}

func codexPaginatedTurnState(ctx context.Context, root string, targets []string, startedAt float64) (AgentStatus, bool) {
	thread := codexWriterThread(root, targets)
	if thread == "" {
		return "", false
	}
	path := filepath.Join(codexHistoryHome(root), "thread_history_1.sqlite")
	if _, err := os.Stat(path); err != nil {
		return "", false
	}
	queryContext, cancel := context.WithTimeout(ctx, 500*time.Millisecond)
	defer cancel()
	db, err := openSQLiteReadOnly(SQLiteReadOnlyURI(path))
	if err != nil {
		return AgentStatusUnavailable, true
	}
	defer db.Close()
	if err := db.QueryOnly(queryContext); err != nil {
		return AgentStatusUnavailable, true
	}
	rows, err := db.Rows(queryContext, "SELECT status, started_at FROM thread_turns WHERE thread_id = ? ORDER BY rollout_ordinal DESC LIMIT 1", thread)
	if err != nil || len(rows) != 1 || len(rows[0]) != 2 {
		return AgentStatusUnavailable, true
	}
	status, _ := rows[0][0].(string)
	switch status {
	case "inProgress":
		stamp, ok := rows[0][1].(int64)
		if !ok || float64(stamp)+1 < startedAt {
			return AgentStatusUnavailable, true
		}
		return AgentStatusRun, true
	case "completed", "interrupted":
		return AgentStatusIdle, true
	case "failed":
		return AgentStatusError, true
	default:
		return AgentStatusUnavailable, true
	}
}

func codexTurnTailState(text string, startedAt float64) AgentStatus {
	// Ignore a partially appended record; the preceding complete event remains
	// authoritative until its terminal event has actually been published.
	end := strings.LastIndexByte(text, '\n')
	if end < 0 {
		return AgentStatusUnavailable
	}
	state := AgentStatusUnavailable
	turnID := ""
	closed := map[string]bool{}
	for _, line := range strings.Split(text[:end], "\n") {
		var record struct {
			Type      string `json:"type"`
			Timestamp string `json:"timestamp"`
			Payload   struct {
				Type   string `json:"type"`
				TurnID string `json:"turn_id"`
			} `json:"payload"`
		}
		if json.Unmarshal([]byte(line), &record) != nil || record.Type != "event_msg" {
			continue
		}
		stamp, err := time.Parse(time.RFC3339Nano, record.Timestamp)
		if err != nil || float64(stamp.UnixNano())/1e9 < startedAt {
			continue
		}
		switch record.Payload.Type {
		case "task_started", "item_started", "item_completed":
			if record.Payload.TurnID != "" && !closed[record.Payload.TurnID] {
				turnID, state = record.Payload.TurnID, AgentStatusRun
			}
		case "task_complete", "turn_aborted":
			if record.Payload.TurnID != "" {
				closed[record.Payload.TurnID] = true
			} else if turnID != "" {
				closed[turnID] = true
			}
			if turnID == "" || record.Payload.TurnID == "" || record.Payload.TurnID == turnID {
				state = AgentStatusIdle
			}
		}
	}
	return state
}

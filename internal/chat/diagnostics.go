// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package chat

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
)

// Diagnostics exposes counts and maintenance state without message bodies.
func (s *Store) Diagnostics(ctx context.Context) (map[string]any, error) {
	result := map[string]any{"schema_version": SchemaVersion, "search_mode": "fts5"}
	for name, query := range map[string]string{
		"message_rows": "SELECT count(*) FROM chat_messages",
		"reader_rows":  "SELECT count(*) FROM chat_read_cursors",
	} {
		var count int64
		if err := s.db.QueryRowContext(ctx, query).Scan(&count); err != nil {
			return nil, err
		}
		result[name] = count
	}
	var typing int64
	if err := s.db.QueryRowContext(ctx, `SELECT count(*) FROM chat_typing_leases WHERE expires_at_utc>?`, s.clock()).Scan(&typing); err != nil {
		return nil, err
	}
	result["typing_leases"] = typing
	var databaseBytes int64
	for _, suffix := range []string{"", "-wal", "-shm"} {
		info, err := os.Stat(s.path + suffix)
		if os.IsNotExist(err) {
			continue
		}
		if err != nil {
			return nil, err
		}
		databaseBytes += info.Size()
	}
	result["database_bytes"] = databaseBytes
	paths, err := filepath.Glob(filepath.Join(s.historyDir, "????-??-??.jsonl"))
	if err != nil {
		return nil, err
	}
	var historyBytes int64
	historyFiles := 0
	for _, path := range paths {
		info, err := os.Stat(path)
		if err != nil {
			return nil, err
		}
		if !info.Mode().IsRegular() {
			continue
		}
		historyBytes += info.Size()
		historyFiles++
	}
	result["history_files"] = historyFiles
	result["history_bytes"] = historyBytes
	rows, err := s.db.QueryContext(ctx, `SELECT key,value FROM chat_metadata WHERE key IN ('last_prune_at','prune_runs','prune_deleted_expired','prune_deleted_overflow')`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	result["last_prune_at"] = float64(0)
	for _, key := range []string{"prune_runs", "prune_deleted_expired", "prune_deleted_overflow"} {
		result[key] = int64(0)
	}
	for rows.Next() {
		var key, raw string
		if err := rows.Scan(&key, &raw); err != nil {
			return nil, err
		}
		if raw == "" {
			continue
		}
		if key == "last_prune_at" {
			value, err := strconv.ParseFloat(raw, 64)
			if err != nil {
				return nil, fmt.Errorf("invalid chat scheduling metadata: %w", err)
			}
			result[key] = value
		} else {
			value, err := strconv.ParseInt(raw, 10, 64)
			if err != nil {
				return nil, fmt.Errorf("invalid chat maintenance counter: %w", err)
			}
			result[key] = value
		}
	}
	return result, rows.Err()
}

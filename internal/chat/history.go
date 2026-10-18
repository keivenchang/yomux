// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package chat

import (
	"context"
	"encoding/json"
	"errors"
	"math"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/keivenchang/yomux/internal/atomicfile"
	"github.com/keivenchang/yomux/internal/mutablefs"
)

type historyRecord struct {
	Version int `json:"version"`
	Message
}

func (s *Store) historyLock(ctx context.Context) (*atomicfile.Lock, error) {
	if err := mutablefs.RequireLocal(s.historyDir); err != nil {
		return nil, err
	}
	if err := os.MkdirAll(s.historyDir, 0700); err != nil {
		return nil, err
	}
	if err := os.Chmod(s.historyDir, 0700); err != nil {
		return nil, err
	}
	return atomicfile.AcquireLock(ctx, filepath.Join(s.historyDir, ".journal.lock"))
}

func historyDay(timestamp float64) string {
	seconds, fraction := math.Modf(timestamp)
	return time.Unix(int64(seconds), int64(fraction*1e9)).UTC().Format("2006-01-02")
}

func readHistory(path string) ([]string, map[int64]bool, error) {
	raw, err := os.ReadFile(path)
	if os.IsNotExist(err) {
		return []string{}, map[int64]bool{}, nil
	}
	if err != nil {
		return nil, nil, err
	}
	if !utf8.Valid(raw) {
		return nil, nil, errors.New("chat history is not UTF-8")
	}
	lines := strings.Split(strings.TrimSuffix(string(raw), "\n"), "\n")
	if len(raw) == 0 {
		lines = []string{}
	}
	ids := map[int64]bool{}
	for _, line := range lines {
		var record struct {
			ID int64 `json:"id"`
		}
		if err := json.Unmarshal([]byte(line), &record); err == nil {
			ids[record.ID] = true
		}
	}
	return lines, ids, nil
}

func (s *Store) mergeHistory(messages []Message) error {
	byDay := map[string][]Message{}
	for _, message := range messages {
		day := historyDay(message.CreatedAt)
		byDay[day] = append(byDay[day], message)
	}
	days := make([]string, 0, len(byDay))
	for day := range byDay {
		days = append(days, day)
	}
	slices.Sort(days)
	for _, day := range days {
		path := filepath.Join(s.historyDir, day+".jsonl")
		lines, ids, err := readHistory(path)
		if err != nil {
			return err
		}
		changed := false
		for _, message := range byDay[day] {
			if ids[message.ID] {
				continue
			}
			raw, err := json.Marshal(historyRecord{Version: 1, Message: message})
			if err != nil {
				return err
			}
			lines = append(lines, string(raw))
			ids[message.ID] = true
			changed = true
		}
		if changed {
			if err := atomicfile.Write(path, []byte(strings.Join(lines, "\n")+"\n")); err != nil {
				return err
			}
		} else if err := os.Chmod(path, 0600); err != nil {
			return err
		}
	}
	return nil
}

func (s *Store) backfillHistory(ctx context.Context) (err error) {
	lock, err := s.historyLock(ctx)
	if err != nil {
		return err
	}
	defer func() { err = errors.Join(err, lock.Close()) }()
	messages, err := queryMessages(ctx, s.db, `SELECT `+messageColumns+` FROM chat_messages ORDER BY id ASC`)
	if err != nil {
		return err
	}
	return s.mergeHistory(messages)
}

func (s *Store) appendHistory(ctx context.Context, message Message) (err error) {
	lock, err := s.historyLock(ctx)
	if err != nil {
		return err
	}
	defer func() { err = errors.Join(err, lock.Close()) }()
	return s.mergeHistory([]Message{message})
}

func (s *Store) pruneHistory(ctx context.Context, cutoff float64) (err error) {
	lock, err := s.historyLock(ctx)
	if err != nil {
		return err
	}
	defer func() { err = errors.Join(err, lock.Close()) }()
	paths, err := filepath.Glob(filepath.Join(s.historyDir, "????-??-??.jsonl"))
	if err != nil {
		return err
	}
	for _, path := range paths {
		if err := ctx.Err(); err != nil {
			return err
		}
		lines, _, err := readHistory(path)
		if err != nil {
			return err
		}
		retained := []string{}
		for _, line := range lines {
			var record map[string]json.RawMessage
			if err := json.Unmarshal([]byte(line), &record); err != nil {
				continue
			}
			var timestamp float64
			if err := json.Unmarshal(record["created_at_utc"], &timestamp); err == nil && timestamp >= cutoff {
				retained = append(retained, line)
			}
		}
		if len(retained) == 0 {
			if err := os.Remove(path); err != nil {
				return err
			}
		} else if err := atomicfile.Write(path, []byte(strings.Join(retained, "\n")+"\n")); err != nil {
			return err
		}
	}
	return nil
}

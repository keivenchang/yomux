// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package chat

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/json"
	"errors"
	"sync/atomic"
	"time"

	"github.com/keivenchang/yomux/internal/work"
)

type EventSnapshot struct {
	LatestID int64
	Typing   []TypingLease
}

func (s *Store) EventSnapshot(ctx context.Context) (EventSnapshot, error) {
	return transaction(ctx, s.db, func(tx *sql.Tx) (EventSnapshot, error) {
		var snapshot EventSnapshot
		if err := tx.QueryRowContext(ctx, `SELECT COALESCE(MAX(id),0) FROM chat_messages WHERE room_id=?`, DefaultRoom).Scan(&snapshot.LatestID); err != nil {
			return EventSnapshot{}, err
		}
		leases, err := queryTyping(ctx, tx, DefaultRoom, s.clock())
		if err != nil {
			return EventSnapshot{}, err
		}
		snapshot.Typing = leases
		return snapshot, nil
	})
}

type EventPublisher func(context.Context, string, map[string]any) error

// Events owns delivery progress for one server's event broker. Progress cannot
// be shared in SQLite: that would suppress notices on other ports sharing the
// same host database. SQL remains authoritative across all those instances.
type Events struct {
	store       *Store
	publish     EventPublisher
	mu          work.ContextMutex
	latestID    int64
	typingHash  [sha256.Size]byte
	typingKnown bool
	running     atomic.Bool
}

func NewEvents(store *Store, publish EventPublisher) (*Events, error) {
	if store == nil || publish == nil {
		return nil, errors.New("chat events require store and publisher")
	}
	return &Events{store: store, publish: publish}, nil
}

// Flush follows the committed database snapshot, so a failed publication can
// be repaired by a request retry or the recurring owner without another send.
// Notices coalesce to the latest tail; readers recover all rows with /delta.
func (events *Events) Flush(ctx context.Context) error {
	if err := events.mu.LockContext(ctx, nil); err != nil {
		return err
	}
	defer events.mu.Unlock()
	snapshot, err := events.store.EventSnapshot(ctx)
	if err != nil {
		return err
	}
	if snapshot.LatestID > events.latestID {
		if err := events.publish(ctx, "chat_messages_changed", map[string]any{"revision": snapshot.LatestID, "message_id": snapshot.LatestID}); err != nil {
			return err
		}
		events.latestID = snapshot.LatestID
	}
	raw, err := json.Marshal(snapshot.Typing)
	if err != nil {
		return err
	}
	hash := sha256.Sum256(raw)
	if !events.typingKnown || hash != events.typingHash {
		if err := events.publish(ctx, "chat_typing_changed", map[string]any{"revision": time.Now().UnixNano()}); err != nil {
			return err
		}
		events.typingHash = hash
		events.typingKnown = true
	}
	return nil
}

// Run is a supervised recurring owner. The caller launches and joins it once
// and records unexpected unit failures through report; shutdown is ordinary.
func (events *Events) Run(ctx context.Context, interval time.Duration, report func(error)) error {
	if interval <= 0 || report == nil {
		return errors.New("chat event loop requires interval and failure reporter")
	}
	if !events.running.CompareAndSwap(false, true) {
		return errors.New("chat event loop is already running")
	}
	defer events.running.Store(false)
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	for {
		if err := ctx.Err(); err != nil {
			return nil
		}
		if err := events.Flush(ctx); err != nil {
			if ctx.Err() != nil {
				return nil
			}
			report(err)
		}
		select {
		case <-ctx.Done():
			return nil
		case <-ticker.C:
		}
	}
}

// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package chat

import (
	"context"
	"database/sql"
	"errors"
	"math"
	"strconv"
)

func queryTyping(ctx context.Context, tx *sql.Tx, room string, now float64) ([]TypingLease, error) {
	if _, err := tx.ExecContext(ctx, `DELETE FROM chat_typing_leases WHERE room_id=? AND expires_at_utc<=?`, room, now); err != nil {
		return nil, err
	}
	rows, err := tx.QueryContext(ctx, `SELECT room_id,username,browser_instance_id,expires_at_utc FROM chat_typing_leases WHERE room_id=? AND expires_at_utc>? ORDER BY username,browser_instance_id`, room, now)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	leases := []TypingLease{}
	for rows.Next() {
		var lease TypingLease
		if err := rows.Scan(&lease.Room, &lease.Username, &lease.BrowserInstance, &lease.ExpiresAt); err != nil {
			return nil, err
		}
		leases = append(leases, lease)
	}
	return leases, rows.Err()
}

func (s *Store) TypingSnapshot(ctx context.Context, room string) ([]TypingLease, error) {
	room, err := roomIdentity(room)
	if err != nil {
		return nil, err
	}
	return transaction(ctx, s.db, func(tx *sql.Tx) ([]TypingLease, error) { return queryTyping(ctx, tx, room, s.clock()) })
}

func (s *Store) SetTyping(ctx context.Context, room, user, instance string, typing bool) ([]TypingLease, error) {
	room, err := roomIdentity(room)
	if err != nil {
		return nil, err
	}
	user, err = identity(user, "username")
	if err != nil {
		return nil, err
	}
	instance, err = identity(instance, "browser instance ID")
	if err != nil {
		return nil, err
	}
	now := s.clock()
	return transaction(ctx, s.db, func(tx *sql.Tx) ([]TypingLease, error) {
		if typing {
			_, err = tx.ExecContext(ctx, `INSERT INTO chat_typing_leases(room_id,username,browser_instance_id,expires_at_utc,updated_at_utc) VALUES(?,?,?,?,?) ON CONFLICT(room_id,username,browser_instance_id) DO UPDATE SET expires_at_utc=excluded.expires_at_utc,updated_at_utc=excluded.updated_at_utc`, room, user, instance, now+TypingLeaseSeconds, now)
		} else {
			_, err = tx.ExecContext(ctx, `DELETE FROM chat_typing_leases WHERE room_id=? AND username=? AND browser_instance_id=?`, room, user, instance)
		}
		if err != nil {
			return nil, err
		}
		return queryTyping(ctx, tx, room, now)
	})
}

func scanCursor(row *sql.Row) (ReadCursor, error) {
	var cursor ReadCursor
	err := row.Scan(&cursor.Room, &cursor.Username, &cursor.ReadUpTo, &cursor.UpdatedAt)
	return cursor, err
}

func (s *Store) ReadUpTo(ctx context.Context, room, user string, id int64) (ReadCursor, error) {
	room, err := roomIdentity(room)
	if err != nil {
		return ReadCursor{}, err
	}
	user, err = identity(user, "username")
	if err != nil {
		return ReadCursor{}, err
	}
	id = max(int64(0), id)
	return transaction(ctx, s.db, func(tx *sql.Tx) (ReadCursor, error) {
		var latest int64
		if err := tx.QueryRowContext(ctx, `SELECT COALESCE(MAX(id),0) FROM chat_messages WHERE room_id=?`, room).Scan(&latest); err != nil {
			return ReadCursor{}, err
		}
		if id > latest {
			return ReadCursor{}, &ValidationError{Reason: "read cursor exceeds current chat tail"}
		}
		_, err := tx.ExecContext(ctx, `INSERT INTO chat_read_cursors(room_id,username,read_up_to_id,updated_at_utc) VALUES(?,?,?,?) ON CONFLICT(room_id,username) DO UPDATE SET read_up_to_id=MAX(chat_read_cursors.read_up_to_id,excluded.read_up_to_id),updated_at_utc=CASE WHEN excluded.read_up_to_id>chat_read_cursors.read_up_to_id THEN excluded.updated_at_utc ELSE chat_read_cursors.updated_at_utc END`, room, user, id, s.clock())
		if err != nil {
			return ReadCursor{}, err
		}
		return scanCursor(tx.QueryRowContext(ctx, `SELECT room_id,username,read_up_to_id,updated_at_utc FROM chat_read_cursors WHERE room_id=? AND username=?`, room, user))
	})
}

func (s *Store) Bootstrap(ctx context.Context, room, user string, options PageOptions) (Bootstrap, error) {
	room, err := roomIdentity(room)
	if err != nil {
		return Bootstrap{}, err
	}
	user, err = identity(user, "username")
	if err != nil {
		return Bootstrap{}, err
	}
	now, cutoff := s.clock(), s.cutoff(options.RetentionDays)
	return transaction(ctx, s.db, func(tx *sql.Tx) (Bootstrap, error) {
		result := Bootstrap{Unread: []Message{}}
		if err := tx.QueryRowContext(ctx, `SELECT COALESCE(MAX(id),0) FROM chat_messages WHERE room_id=? AND created_at_utc>=?`, room, cutoff).Scan(&result.LatestID); err != nil {
			return Bootstrap{}, err
		}
		cursor, err := scanCursor(tx.QueryRowContext(ctx, `SELECT room_id,username,read_up_to_id,updated_at_utc FROM chat_read_cursors WHERE room_id=? AND username=?`, room, user))
		if errors.Is(err, sql.ErrNoRows) {
			result.FirstRegistration = true
			cursor = ReadCursor{Room: room, Username: user, ReadUpTo: result.LatestID, UpdatedAt: now}
			if _, err = tx.ExecContext(ctx, `INSERT INTO chat_read_cursors(room_id,username,read_up_to_id,updated_at_utc) VALUES(?,?,?,?)`, room, user, result.LatestID, now); err != nil {
				return Bootstrap{}, err
			}
		} else if err != nil {
			return Bootstrap{}, err
		} else {
			result.Unread, err = queryMessages(ctx, tx, `SELECT `+messageColumns+` FROM chat_messages WHERE room_id=? AND id>? AND created_at_utc>=? ORDER BY id ASC LIMIT ?`, room, cursor.ReadUpTo, cutoff, bounded(options.Limit, 50, 1, 200))
			if err != nil {
				return Bootstrap{}, err
			}
		}
		result.ReadCursor = cursor
		result.Typing, err = queryTyping(ctx, tx, room, now)
		if err != nil {
			return Bootstrap{}, err
		}
		covered := result.LatestID + 1
		if len(result.Unread) > 0 {
			covered = result.Unread[0].ID
		}
		if err := tx.QueryRowContext(ctx, `SELECT EXISTS(SELECT 1 FROM chat_messages WHERE room_id=? AND id<? AND created_at_utc>=?)`, room, covered, cutoff).Scan(&result.HasMoreOlder); err != nil {
			return Bootstrap{}, err
		}
		return result, nil
	})
}

type PruneResult struct {
	DeletedExpired  int64  `json:"deleted_expired"`
	DeletedOverflow int64  `json:"deleted_overflow"`
	RemainingRows   int64  `json:"remaining_rows"`
	Ran             bool   `json:"ran"`
	RecoveryReason  string `json:"-"`
}

func (s *Store) Prune(ctx context.Context, days, rowLimitValue *int) (PruneResult, error) {
	cutoff := s.cutoff(days)
	rowLimit := bounded(rowLimitValue, HardRowLimit, 1, HardRowLimit)
	result, err := transaction(ctx, s.db, func(tx *sql.Tx) (PruneResult, error) {
		result := PruneResult{Ran: true}
		for {
			deleted, err := tx.ExecContext(ctx, `DELETE FROM chat_messages WHERE id IN(SELECT id FROM chat_messages WHERE created_at_utc<? ORDER BY id ASC LIMIT 1000)`, cutoff)
			if err != nil {
				return PruneResult{}, err
			}
			count, err := deleted.RowsAffected()
			if err != nil {
				return PruneResult{}, err
			}
			result.DeletedExpired += count
			if count < 1000 {
				break
			}
		}
		if err := tx.QueryRowContext(ctx, `SELECT count(*) FROM chat_messages`).Scan(&result.RemainingRows); err != nil {
			return PruneResult{}, err
		}
		for result.RemainingRows > int64(rowLimit) {
			deleted, err := tx.ExecContext(ctx, `DELETE FROM chat_messages WHERE id IN(SELECT id FROM chat_messages ORDER BY id ASC LIMIT ?)`, min(int64(1000), result.RemainingRows-int64(rowLimit)))
			if err != nil {
				return PruneResult{}, err
			}
			count, err := deleted.RowsAffected()
			if err != nil {
				return PruneResult{}, err
			}
			result.DeletedOverflow += count
			result.RemainingRows -= count
			if count == 0 {
				return PruneResult{}, errors.New("chat overflow prune made no progress")
			}
		}
		if _, err := tx.ExecContext(ctx, `INSERT INTO chat_metadata(key,value) VALUES('last_prune_at',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value`, strconv.FormatFloat(s.clock(), 'g', -1, 64)); err != nil {
			return PruneResult{}, err
		}
		for key, increment := range map[string]int64{"prune_runs": 1, "prune_deleted_expired": result.DeletedExpired, "prune_deleted_overflow": result.DeletedOverflow} {
			if _, err := tx.ExecContext(ctx, `INSERT INTO chat_metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=CAST(chat_metadata.value AS INTEGER)+CAST(excluded.value AS INTEGER)`, key, strconv.FormatInt(increment, 10)); err != nil {
				return PruneResult{}, err
			}
		}
		return result, nil
	})
	if err != nil {
		return PruneResult{}, err
	}
	if err := s.pruneHistory(ctx, cutoff); err != nil {
		return PruneResult{}, err
	}
	return result, nil
}

func (s *Store) PruneIfDue(ctx context.Context, daysValue, previousDays *int) (PruneResult, error) {
	days := bounded(daysValue, DefaultRetentionDays, 1, 365)
	previous := days
	if previousDays != nil {
		previous = bounded(previousDays, DefaultRetentionDays, 1, 365)
	}
	now := s.clock()
	claim, err := transaction(ctx, s.db, func(tx *sql.Tx) (PruneResult, error) {
		var raw string
		last := float64(0)
		err := tx.QueryRowContext(ctx, `SELECT value FROM chat_metadata WHERE key='last_prune_at'`).Scan(&raw)
		if err != nil && !errors.Is(err, sql.ErrNoRows) {
			return PruneResult{}, err
		}
		recoveryReason := ""
		if err == nil {
			parsed, parseErr := strconv.ParseFloat(raw, 64)
			if parseErr != nil || math.IsNaN(parsed) || math.IsInf(parsed, 0) {
				// This value schedules maintenance; it does not authorize data
				// deletion. Repair it under the normal retention policy and
				// preserve an explicit result reason for the service owner.
				recoveryReason = "invalid_prune_timestamp"
			} else {
				last = parsed
			}
		}
		if recoveryReason == "" && days >= previous && now-last < 3600 {
			var count int64
			if err := tx.QueryRowContext(ctx, `SELECT count(*) FROM chat_messages`).Scan(&count); err != nil {
				return PruneResult{}, err
			}
			return PruneResult{RemainingRows: count}, nil
		}
		if _, err := tx.ExecContext(ctx, `INSERT INTO chat_metadata(key,value) VALUES('last_prune_at',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value`, strconv.FormatFloat(now, 'g', -1, 64)); err != nil {
			return PruneResult{}, err
		}
		return PruneResult{Ran: true, RecoveryReason: recoveryReason}, nil
	})
	if err != nil || !claim.Ran {
		return claim, err
	}
	result, err := s.Prune(ctx, &days, nil)
	result.RecoveryReason = claim.RecoveryReason
	return result, err
}

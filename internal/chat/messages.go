// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package chat

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"math"
	"net/netip"
	"slices"
	"strings"
	"unicode/utf8"

	"github.com/keivenchang/yomux/internal/locales"
)

const (
	AgentUsername        = "YO!agent"
	AgentInstance        = "yolomux-yoagent"
	TypingLeaseSeconds   = 5
	DefaultRoom          = "global"
	BodyMaxBytes         = 8 * 1024
	DefaultRetentionDays = 7
	HardRowLimit         = 100000
)

type MessagePayload struct {
	ID             int64   `json:"id"`
	CreatedAt      float64 `json:"created_at_utc"`
	Username       string  `json:"username"`
	SenderIP       string  `json:"sender_ip"`
	SenderInstance string  `json:"sender_instance_id"`
	ClientUUID     string  `json:"client_message_uuid"`
	Body           string  `json:"body"`
	IsQuestion     bool    `json:"is_question"`
}

type Message struct {
	MessagePayload
	Room string `json:"room_id"`
}

type NewMessage struct {
	Message
	Timestamp *float64
}

type ReadCursor struct {
	Room      string  `json:"room_id"`
	Username  string  `json:"username"`
	ReadUpTo  int64   `json:"read_up_to_id"`
	UpdatedAt float64 `json:"updated_at_utc"`
}

type TypingLease struct {
	Room            string  `json:"room_id"`
	Username        string  `json:"username"`
	BrowserInstance string  `json:"browser_instance_id"`
	ExpiresAt       float64 `json:"expires_at_utc"`
}

type MessagePage struct {
	Messages    []Message `json:"messages"`
	OlderCursor *int64    `json:"older_cursor"`
	HasMore     bool      `json:"has_more"`
}

type MessageContext struct {
	Target Message   `json:"target"`
	Before []Message `json:"before"`
	After  []Message `json:"after"`
}

type Bootstrap struct {
	LatestID          int64         `json:"latest_message_id"`
	ReadCursor        ReadCursor    `json:"read_cursor"`
	Unread            []Message     `json:"unread_messages"`
	Typing            []TypingLease `json:"typing"`
	FirstRegistration bool          `json:"first_registration"`
	HasMoreOlder      bool          `json:"has_more_older"`
}

// Optional values distinguish absence from explicit zero, which clamps to the
// minimum in the existing browser contract.
type PageOptions struct{ Limit, RetentionDays *int }

type queryOwner interface {
	QueryContext(context.Context, string, ...any) (*sql.Rows, error)
	QueryRowContext(context.Context, string, ...any) *sql.Row
}

const messageColumns = `id,created_at_utc,room_id,username,sender_ip,sender_instance_id,client_message_uuid,body,is_question`

func scanMessage(row interface{ Scan(...any) error }) (Message, error) {
	var m Message
	err := row.Scan(&m.ID, &m.CreatedAt, &m.Room, &m.Username, &m.SenderIP, &m.SenderInstance, &m.ClientUUID, &m.Body, &m.IsQuestion)
	return m, err
}

func queryMessages(ctx context.Context, owner queryOwner, query string, arguments ...any) ([]Message, error) {
	rows, err := owner.QueryContext(ctx, query, arguments...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	result := []Message{}
	for rows.Next() {
		message, err := scanMessage(rows)
		if err != nil {
			return nil, err
		}
		result = append(result, message)
	}
	return result, rows.Err()
}

func transaction[T any](ctx context.Context, db *sql.DB, operation func(*sql.Tx) (T, error)) (T, error) {
	var zero T
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return zero, err
	}
	defer tx.Rollback()
	result, err := operation(tx)
	if err != nil {
		return zero, err
	}
	if err := tx.Commit(); err != nil {
		return zero, err
	}
	return result, nil
}

func identity(value, field string) (string, error) {
	value = locales.TrimSpace(value)
	if !utf8.ValidString(value) || len([]rune(value)) == 0 || len([]rune(value)) > 128 {
		return "", &ValidationError{Reason: "invalid " + field}
	}
	return value, nil
}

func roomIdentity(room string) (string, error) {
	if room == "" {
		room = DefaultRoom
	}
	return identity(room, "room ID")
}

func bounded(value *int, defaultValue, minimum, maximum int) int {
	number := defaultValue
	if value != nil {
		number = *value
	}
	return max(minimum, min(maximum, number))
}

func (s *Store) cutoff(days *int) float64 {
	return s.clock() - float64(bounded(days, DefaultRetentionDays, 1, 365))*86400
}

func (s *Store) Insert(ctx context.Context, input NewMessage) (Message, bool, error) {
	m := input.Message
	var err error
	if m.Room, err = roomIdentity(m.Room); err != nil {
		return Message{}, false, err
	}
	for _, field := range []struct {
		value *string
		name  string
	}{
		{&m.Username, "username"}, {&m.SenderInstance, "sender instance ID"}, {&m.ClientUUID, "client message UUID"},
	} {
		if *field.value, err = identity(*field.value, field.name); err != nil {
			return Message{}, false, err
		}
	}
	if m.Body == "" {
		return Message{}, false, &ValidationError{Reason: "message body is empty"}
	}
	if !utf8.ValidString(m.Body) {
		return Message{}, false, &ValidationError{Reason: "message body is not UTF-8"}
	}
	if len(m.Body) > BodyMaxBytes {
		return Message{}, false, &ValidationError{Reason: "message body exceeds 8 KiB"}
	}
	m.SenderIP = locales.TrimSpace(m.SenderIP)
	if m.SenderIP != "" {
		address, err := netip.ParseAddr(m.SenderIP)
		if err != nil {
			return Message{}, false, &ValidationError{Reason: "invalid sender IP"}
		}
		m.SenderIP = address.String()
		if address.Is4In6() {
			bytes := address.As16()
			m.SenderIP = fmt.Sprintf("::ffff:%x:%x", uint16(bytes[12])<<8|uint16(bytes[13]), uint16(bytes[14])<<8|uint16(bytes[15]))
			if address.Zone() != "" {
				m.SenderIP += "%" + address.Zone()
			}
		}
	}
	m.CreatedAt = s.clock()
	if input.Timestamp != nil {
		m.CreatedAt = *input.Timestamp
	}
	if math.IsNaN(m.CreatedAt) || math.IsInf(m.CreatedAt, 0) || m.CreatedAt < -62135596800 || m.CreatedAt >= 253402300800 {
		return Message{}, false, &ValidationError{Reason: "invalid message timestamp"}
	}
	type insertResult struct {
		Message Message
		Created bool
	}
	result, err := transaction(ctx, s.db, func(tx *sql.Tx) (insertResult, error) {
		existing, err := scanMessage(tx.QueryRowContext(ctx, `SELECT `+messageColumns+` FROM chat_messages WHERE room_id=? AND username=? AND sender_instance_id=? AND client_message_uuid=?`, m.Room, m.Username, m.SenderInstance, m.ClientUUID))
		if err == nil {
			return insertResult{Message: existing}, nil
		}
		if !errors.Is(err, sql.ErrNoRows) {
			return insertResult{}, err
		}
		insert, err := tx.ExecContext(ctx, `INSERT INTO chat_messages(created_at_utc,room_id,username,sender_ip,sender_instance_id,client_message_uuid,body,is_question) VALUES(?,?,?,?,?,?,?,?)`, m.CreatedAt, m.Room, m.Username, m.SenderIP, m.SenderInstance, m.ClientUUID, m.Body, m.IsQuestion)
		if err != nil {
			return insertResult{}, err
		}
		if m.ID, err = insert.LastInsertId(); err != nil {
			return insertResult{}, err
		}
		if _, err = tx.ExecContext(ctx, `DELETE FROM chat_typing_leases WHERE room_id=? AND username=? AND browser_instance_id=?`, m.Room, m.Username, m.SenderInstance); err != nil {
			return insertResult{}, err
		}
		return insertResult{Message: m, Created: true}, nil
	})
	if err != nil {
		return Message{}, false, err
	}
	if err := s.appendHistory(ctx, result.Message); err != nil {
		return Message{}, false, err
	}
	return result.Message, result.Created, nil
}

func (s *Store) PageBefore(ctx context.Context, room string, before *int64, options PageOptions) (MessagePage, error) {
	room, err := roomIdentity(room)
	if err != nil {
		return MessagePage{}, err
	}
	limit := bounded(options.Limit, 50, 1, 200)
	cursor := int64(math.MaxInt64)
	if before != nil {
		cursor = *before
	}
	messages, err := queryMessages(ctx, s.db, `SELECT `+messageColumns+` FROM chat_messages WHERE room_id=? AND id<? AND created_at_utc>=? ORDER BY id DESC LIMIT ?`, room, cursor, s.cutoff(options.RetentionDays), limit+1)
	if err != nil {
		return MessagePage{}, err
	}
	page := MessagePage{HasMore: len(messages) > limit}
	page.Messages = messages[:min(len(messages), limit)]
	slices.Reverse(page.Messages)
	if page.HasMore {
		value := page.Messages[0].ID
		page.OlderCursor = &value
	}
	return page, nil
}

func (s *Store) MessagesAfter(ctx context.Context, room string, after int64, options PageOptions) ([]Message, error) {
	room, err := roomIdentity(room)
	if err != nil {
		return nil, err
	}
	return queryMessages(ctx, s.db, `SELECT `+messageColumns+` FROM chat_messages WHERE room_id=? AND id>? AND created_at_utc>=? ORDER BY id ASC LIMIT ?`, room, max(int64(0), after), s.cutoff(options.RetentionDays), bounded(options.Limit, 200, 1, 200))
}

func messageContext(ctx context.Context, owner queryOwner, room string, id int64, before, after int, cutoff float64) (*MessageContext, error) {
	target, err := scanMessage(owner.QueryRowContext(ctx, `SELECT `+messageColumns+` FROM chat_messages WHERE room_id=? AND id=? AND created_at_utc>=?`, room, id, cutoff))
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	previous, err := queryMessages(ctx, owner, `SELECT `+messageColumns+` FROM chat_messages WHERE room_id=? AND id<? AND created_at_utc>=? ORDER BY id DESC LIMIT ?`, room, id, cutoff, max(0, min(25, before)))
	if err != nil {
		return nil, err
	}
	slices.Reverse(previous)
	next, err := queryMessages(ctx, owner, `SELECT `+messageColumns+` FROM chat_messages WHERE room_id=? AND id>? AND created_at_utc>=? ORDER BY id ASC LIMIT ?`, room, id, cutoff, max(0, min(25, after)))
	if err != nil {
		return nil, err
	}
	return &MessageContext{Target: target, Before: previous, After: next}, nil
}

func (s *Store) Context(ctx context.Context, room string, id int64, before, after, days *int) (*MessageContext, error) {
	room, err := roomIdentity(room)
	if err != nil {
		return nil, err
	}
	return messageContext(ctx, s.db, room, id, bounded(before, 3, 0, 25), bounded(after, 3, 0, 25), s.cutoff(days))
}

type SearchHit struct {
	Message Message        `json:"message"`
	Context MessageContext `json:"context"`
}
type SearchPage struct {
	Hits       []SearchHit `json:"hits"`
	NextCursor *int64      `json:"next_cursor"`
	HasMore    bool        `json:"has_more"`
	Mode       string      `json:"mode"`
}

func (s *Store) Search(ctx context.Context, room, query string, cursor *int64, limitValue, before, after, days *int) (SearchPage, error) {
	room, err := roomIdentity(room)
	if err != nil {
		return SearchPage{}, err
	}
	query = locales.TrimSpace(query)
	if query == "" {
		return SearchPage{}, &ValidationError{Reason: "search query is empty"}
	}
	limit := bounded(limitValue, 20, 1, 100)
	beforeID := int64(math.MaxInt64)
	if cursor != nil {
		beforeID = *cursor
	}
	tokens := strings.FieldsFunc(strings.ReplaceAll(query, `"`, " "), func(r rune) bool { return locales.TrimSpace(string(r)) == "" })
	for i, token := range tokens {
		tokens[i] = `"` + token + `"`
	}
	ftsQuery := strings.Join(tokens, " AND ")
	cutoff := s.cutoff(days)
	var messages []Message
	if ftsQuery != "" {
		messages, err = queryMessages(ctx, s.db, `SELECT `+messageColumns+` FROM chat_messages WHERE room_id=? AND id<? AND created_at_utc>=? AND (instr(lower(body),lower(?))>0 OR id IN (SELECT rowid FROM chat_messages_fts WHERE chat_messages_fts MATCH ?)) ORDER BY CASE WHEN instr(lower(body),lower(?))>0 THEN 0 ELSE 1 END ASC,id DESC LIMIT ?`, room, beforeID, cutoff, query, ftsQuery, query, limit+1)
	} else {
		escaped := strings.NewReplacer(`\`, `\\`, `%`, `\%`, `_`, `\_`).Replace(query)
		messages, err = queryMessages(ctx, s.db, `SELECT `+messageColumns+` FROM chat_messages WHERE room_id=? AND id<? AND created_at_utc>=? AND body LIKE ? ESCAPE '\' ORDER BY id DESC LIMIT ?`, room, beforeID, cutoff, "%"+escaped+"%", limit+1)
	}
	if err != nil {
		return SearchPage{}, err
	}
	page := SearchPage{Hits: []SearchHit{}, HasMore: len(messages) > limit, Mode: "fts5"}
	for _, message := range messages[:min(len(messages), limit)] {
		context, err := messageContext(ctx, s.db, room, message.ID, bounded(before, 2, 0, 25), bounded(after, 2, 0, 25), cutoff)
		if err != nil {
			return SearchPage{}, err
		}
		if context != nil {
			page.Hits = append(page.Hits, SearchHit{Message: message, Context: *context})
		}
	}
	if page.HasMore && len(page.Hits) > 0 {
		last := page.Hits[len(page.Hits)-1].Message.ID
		page.NextCursor = &last
	}
	return page, nil
}

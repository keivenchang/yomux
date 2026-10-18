// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package chat

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"sync"
	"time"
	"unicode"
	"unicode/utf8"

	"github.com/keivenchang/yomux/internal/locales"
)

type ServiceOptions struct {
	RetentionDays func(context.Context) (int, error)
	Clock         func() time.Time
}

type rateKey struct{ username, action string }
type operationMetric struct {
	Count, Rows, Bytes int64
	TotalMS, MaxMS     float64
}

// Service owns the browser protocol independently of HTTP admission and event
// transport. Its Store and cursor key are shared by every request on this host.
type Service struct {
	store         *Store
	codec         *CursorCodec
	classifier    *QuestionClassifier
	retentionDays func(context.Context) (int, error)
	clock         func() time.Time
	mu            sync.Mutex
	rates         map[rateKey][]time.Time
	metrics       map[string]operationMetric
	repairs       map[string]int64
}

func NewService(store *Store, codec *CursorCodec, classifier *QuestionClassifier, options ServiceOptions) (*Service, error) {
	if store == nil || codec == nil || classifier == nil {
		return nil, errors.New("chat service requires store, cursor codec, and classifier")
	}
	retention := options.RetentionDays
	if retention == nil {
		retention = func(context.Context) (int, error) { return DefaultRetentionDays, nil }
	}
	clock := options.Clock
	if clock == nil {
		clock = time.Now
	}
	return &Service{store: store, codec: codec, classifier: classifier, retentionDays: retention, clock: clock, rates: map[rateKey][]time.Time{}, metrics: map[string]operationMetric{}, repairs: map[string]int64{}}, nil
}

func normalizeClientID(value, field string) (string, error) {
	value = locales.TrimSpace(value)
	if !utf8.ValidString(value) || value == "" || utf8.RuneCountInString(value) > 64 {
		return "", &Error{Code: "invalid_id", Status: 400, Message: "invalid " + field}
	}
	for _, character := range value {
		if !unicode.IsLetter(character) && !unicode.IsNumber(character) && !strings.ContainsRune("._:-", character) {
			return "", &Error{Code: "invalid_id", Status: 400, Message: "invalid " + field}
		}
	}
	return value, nil
}

func (s *Service) checkRate(username, action string, limit int) error {
	now := s.clock()
	cutoff := now.Add(-time.Minute)
	s.mu.Lock()
	defer s.mu.Unlock()
	for key, record := range s.rates {
		first := 0
		for first < len(record) && !record[first].After(cutoff) {
			first++
		}
		if first == len(record) {
			delete(s.rates, key)
		} else if first > 0 {
			s.rates[key] = record[first:]
		}
	}
	key := rateKey{username: username, action: action}
	if len(s.rates[key]) >= limit {
		return &Error{Code: "rate_limited", Status: 429, Message: "chat rate limit exceeded"}
	}
	s.rates[key] = append(s.rates[key], now)
	return nil
}

func (s *Service) prune(ctx context.Context) (int, error) {
	days, err := s.retentionDays(ctx)
	if err != nil {
		return 0, err
	}
	if days < 1 || days > 365 {
		return 0, &Error{Code: "server_error", Status: 500, Message: "invalid chat retention configuration"}
	}
	result, err := s.store.PruneIfDue(ctx, &days, nil)
	if err != nil {
		return 0, err
	}
	if result.RecoveryReason != "" {
		s.mu.Lock()
		s.repairs[result.RecoveryReason]++
		s.mu.Unlock()
	}
	return days, nil
}

func storeError(err error, code string, status int) error {
	var validation *ValidationError
	if errors.As(err, &validation) {
		return &Error{Code: code, Status: status, Message: validation.Reason, Cause: err}
	}
	return err
}

func payloads(messages []Message) []MessagePayload {
	result := make([]MessagePayload, 0, len(messages))
	for _, message := range messages {
		result = append(result, message.MessagePayload)
	}
	return result
}

type ContextPayload struct {
	Target MessagePayload   `json:"target"`
	Before []MessagePayload `json:"before"`
	After  []MessagePayload `json:"after"`
}

func contextPayload(value MessageContext) ContextPayload {
	return ContextPayload{Target: value.Target.MessagePayload, Before: payloads(value.Before), After: payloads(value.After)}
}

func (s *Service) record(operation string, started time.Time, payload any, rows int) error {
	var buffer bytes.Buffer
	encoder := json.NewEncoder(&buffer)
	encoder.SetEscapeHTML(false)
	if err := encoder.Encode(payload); err != nil {
		return err
	}
	ms := max(float64(0), float64(time.Since(started))/float64(time.Millisecond))
	s.mu.Lock()
	defer s.mu.Unlock()
	metric := s.metrics[operation]
	metric.Count++
	metric.Rows += int64(max(0, rows))
	metric.Bytes += int64(buffer.Len() - 1)
	metric.TotalMS += ms
	metric.MaxMS = max(metric.MaxMS, ms)
	s.metrics[operation] = metric
	return nil
}

type BootstrapPayload struct {
	Revision          int64            `json:"revision"`
	NewerCursor       string           `json:"newer_cursor"`
	OlderCursor       *string          `json:"older_cursor"`
	HasMoreOlder      bool             `json:"has_more_older"`
	ReadUpToID        int64            `json:"read_up_to_id"`
	FirstRegistration bool             `json:"first_registration"`
	Messages          []MessagePayload `json:"messages"`
	Typing            []TypingLease    `json:"typing"`
	RetentionDays     int              `json:"retention_days"`
}

func typingForOtherBrowsers(leases []TypingLease, instance string) []TypingLease {
	result := []TypingLease{}
	for _, lease := range leases {
		if lease.BrowserInstance != instance {
			result = append(result, lease)
		}
	}
	return result
}

func (s *Service) Bootstrap(ctx context.Context, username, browser string) (BootstrapPayload, error) {
	instance, err := normalizeClientID(browser, "browser instance ID")
	if err != nil {
		return BootstrapPayload{}, err
	}
	days, err := s.prune(ctx)
	if err != nil {
		return BootstrapPayload{}, err
	}
	result, err := s.store.Bootstrap(ctx, DefaultRoom, username, PageOptions{RetentionDays: &days})
	if err != nil {
		return BootstrapPayload{}, err
	}
	newer, err := s.codec.Encode(ctx, "newer", result.LatestID)
	if err != nil {
		return BootstrapPayload{}, err
	}
	var older *string
	if result.HasMoreOlder {
		start := result.LatestID + 1
		if len(result.Unread) > 0 {
			start = result.Unread[0].ID
		}
		token, err := s.codec.Encode(ctx, "older", start)
		if err != nil {
			return BootstrapPayload{}, err
		}
		older = &token
	}
	return BootstrapPayload{Revision: result.LatestID, NewerCursor: newer, OlderCursor: older, HasMoreOlder: result.HasMoreOlder, ReadUpToID: result.ReadCursor.ReadUpTo, FirstRegistration: result.FirstRegistration, Messages: payloads(result.Unread), Typing: typingForOtherBrowsers(result.Typing, instance), RetentionDays: days}, nil
}

type PagePayload struct {
	Messages    []MessagePayload `json:"messages"`
	OlderCursor *string          `json:"older_cursor"`
	HasMore     bool             `json:"has_more"`
}

func (s *Service) Page(ctx context.Context, username, before string, limit *int) (PagePayload, error) {
	started := time.Now()
	if err := s.checkRate(username, "query", 120); err != nil {
		return PagePayload{}, err
	}
	days, err := s.prune(ctx)
	if err != nil {
		return PagePayload{}, err
	}
	var cursor *int64
	if before != "" {
		id, err := s.codec.Decode(ctx, before, "older")
		if err != nil {
			return PagePayload{}, err
		}
		cursor = &id
	}
	page, err := s.store.PageBefore(ctx, DefaultRoom, cursor, PageOptions{Limit: limit, RetentionDays: &days})
	if err != nil {
		return PagePayload{}, err
	}
	payload := PagePayload{Messages: payloads(page.Messages), HasMore: page.HasMore}
	if page.OlderCursor != nil {
		token, err := s.codec.Encode(ctx, "older", *page.OlderCursor)
		if err != nil {
			return PagePayload{}, err
		}
		payload.OlderCursor = &token
	}
	if err := s.record("page", started, payload, len(page.Messages)); err != nil {
		return PagePayload{}, err
	}
	return payload, nil
}

type DeltaPayload struct {
	Messages    []MessagePayload `json:"messages"`
	Revision    int64            `json:"revision"`
	NewerCursor string           `json:"newer_cursor"`
}

func (s *Service) Delta(ctx context.Context, username, after string, limit *int) (DeltaPayload, error) {
	if err := s.checkRate(username, "query", 120); err != nil {
		return DeltaPayload{}, err
	}
	days, err := s.prune(ctx)
	if err != nil {
		return DeltaPayload{}, err
	}
	var id int64
	if after != "" {
		id, err = s.codec.Decode(ctx, after, "newer")
		if err != nil {
			return DeltaPayload{}, err
		}
	}
	messages, err := s.store.MessagesAfter(ctx, DefaultRoom, id, PageOptions{Limit: limit, RetentionDays: &days})
	if err != nil {
		return DeltaPayload{}, err
	}
	if len(messages) > 0 {
		id = messages[len(messages)-1].ID
	}
	token, err := s.codec.Encode(ctx, "newer", id)
	if err != nil {
		return DeltaPayload{}, err
	}
	return DeltaPayload{Messages: payloads(messages), Revision: id, NewerCursor: token}, nil
}

func (s *Service) Context(ctx context.Context, username string, id int64, before, after *int) (ContextPayload, error) {
	if err := s.checkRate(username, "query", 120); err != nil {
		return ContextPayload{}, err
	}
	days, err := s.prune(ctx)
	if err != nil {
		return ContextPayload{}, err
	}
	value, err := s.store.Context(ctx, DefaultRoom, id, before, after, &days)
	if err != nil {
		return ContextPayload{}, err
	}
	if value == nil {
		return ContextPayload{}, &Error{Code: "expired", Status: 404, Message: "chat message is no longer available"}
	}
	return contextPayload(*value), nil
}

type SearchPayload struct {
	Hits       []ContextPayload `json:"hits"`
	NextCursor *string          `json:"next_cursor"`
	HasMore    bool             `json:"has_more"`
}

func (s *Service) Search(ctx context.Context, username, query, cursor string, limit *int) (SearchPayload, error) {
	started := time.Now()
	if err := s.checkRate(username, "search", 120); err != nil {
		return SearchPayload{}, err
	}
	var id *int64
	if cursor != "" {
		value, err := s.codec.Decode(ctx, cursor, "search")
		if err != nil {
			return SearchPayload{}, err
		}
		id = &value
	}
	days, err := s.prune(ctx)
	if err != nil {
		return SearchPayload{}, err
	}
	page, err := s.store.Search(ctx, DefaultRoom, query, id, limit, nil, nil, &days)
	if err != nil {
		return SearchPayload{}, storeError(err, "invalid", 400)
	}
	payload := SearchPayload{Hits: []ContextPayload{}, HasMore: page.HasMore}
	for _, hit := range page.Hits {
		payload.Hits = append(payload.Hits, contextPayload(hit.Context))
	}
	if page.NextCursor != nil {
		token, err := s.codec.Encode(ctx, "search", *page.NextCursor)
		if err != nil {
			return SearchPayload{}, err
		}
		payload.NextCursor = &token
	}
	if err := s.record("search", started, payload, len(payload.Hits)); err != nil {
		return SearchPayload{}, err
	}
	return payload, nil
}

type SendPayload struct {
	Message  MessagePayload `json:"message"`
	Revision int64          `json:"revision"`
	Created  bool           `json:"created"`
}
type SendRequest struct {
	BrowserInstance string `json:"browser_instance_id"`
	ClientUUID      string `json:"client_message_uuid"`
	Body            string `json:"body"`
}

func (s *Service) Send(ctx context.Context, username string, request SendRequest, locale, senderIP string) (SendPayload, error) {
	started := time.Now()
	if err := s.checkRate(username, "send", 20); err != nil {
		return SendPayload{}, err
	}
	instance, err := normalizeClientID(request.BrowserInstance, "browser instance ID")
	if err != nil {
		return SendPayload{}, err
	}
	uuid, err := normalizeClientID(request.ClientUUID, "client message UUID")
	if err != nil {
		return SendPayload{}, err
	}
	message, created, err := s.store.Insert(ctx, NewMessage{Message: Message{MessagePayload: MessagePayload{Username: username, SenderIP: senderIP, SenderInstance: instance, ClientUUID: uuid, Body: request.Body, IsQuestion: s.classifier.IsQuestion(request.Body, locale)}}})
	if err != nil {
		return SendPayload{}, storeError(err, "invalid_message", 400)
	}
	payload := SendPayload{Message: message.MessagePayload, Revision: message.ID, Created: created}
	if err := s.record("send", started, payload, 1); err != nil {
		return SendPayload{}, err
	}
	return payload, nil
}

type TypingPayload struct {
	Typing []TypingLease `json:"typing"`
}

func (s *Service) Typing(ctx context.Context, username, browser string, typing bool) (TypingPayload, error) {
	instance, err := normalizeClientID(browser, "browser instance ID")
	if err != nil {
		return TypingPayload{}, err
	}
	leases, err := s.store.SetTyping(ctx, DefaultRoom, username, instance, typing)
	if err != nil {
		return TypingPayload{}, err
	}
	return TypingPayload{Typing: typingForOtherBrowsers(leases, instance)}, nil
}

type ReadPayload struct {
	ReadUpToID int64 `json:"read_up_to_id"`
}

func (s *Service) Read(ctx context.Context, username string, id int64) (ReadPayload, error) {
	cursor, err := s.store.ReadUpTo(ctx, DefaultRoom, username, id)
	if err != nil {
		return ReadPayload{}, storeError(err, "invalid", 400)
	}
	return ReadPayload{ReadUpToID: cursor.ReadUpTo}, nil
}

func (s *Service) YOAgentSource(ctx context.Context, username, browser string, id int64) (Message, string, error) {
	instance, err := normalizeClientID(browser, "browser instance ID")
	if err != nil {
		return Message{}, "", err
	}
	days, err := s.prune(ctx)
	if err != nil {
		return Message{}, "", err
	}
	zero := 0
	value, err := s.store.Context(ctx, DefaultRoom, id, &zero, &zero, &days)
	if err != nil {
		return Message{}, "", err
	}
	if value == nil {
		return Message{}, "", &Error{Code: "expired", Status: 404, Message: "chat message is no longer available"}
	}
	source := value.Target
	if source.Username != username || source.SenderInstance != instance {
		return Message{}, "", &Error{Code: "forbidden", Status: 403, Message: "chat message does not belong to this sender"}
	}
	body := locales.TrimSpace(source.Body)
	if strings.HasPrefix(body, "/yo") && len(body) > 3 {
		first, _ := utf8.DecodeRuneInString(body[3:])
		if locales.TrimSpace(string(first)) == "" {
			question := locales.TrimSpace(body[3:])
			if question != "" {
				return source, question, nil
			}
		}
	}
	if source.IsQuestion {
		return source, body, nil
	}
	return Message{}, "", &Error{Code: "invalid_message", Status: 400, Message: "chat message is not a /yo command or question"}
}

func (s *Service) RecordYOAgentReply(ctx context.Context, source Message, answer string) (SendPayload, error) {
	if !utf8.ValidString(answer) {
		return SendPayload{}, &Error{Code: "server_error", Status: 500, Message: "YO!agent returned invalid UTF-8"}
	}
	if len(answer) > BodyMaxBytes {
		answer = answer[:BodyMaxBytes]
		for !utf8.ValidString(answer) {
			answer = answer[:len(answer)-1]
		}
	}
	if answer == "" {
		return SendPayload{}, &Error{Code: "server_error", Status: 500, Message: "YO!agent returned an empty answer"}
	}
	message, created, err := s.store.Insert(ctx, NewMessage{Message: Message{MessagePayload: MessagePayload{Username: AgentUsername, SenderInstance: AgentInstance, ClientUUID: fmt.Sprintf("yo-reply-%d", source.ID), Body: answer}}})
	if err != nil {
		return SendPayload{}, storeError(err, "server_error", 500)
	}
	return SendPayload{Message: message.MessagePayload, Revision: message.ID, Created: created}, nil
}

func (s *Service) Diagnostics(ctx context.Context) (map[string]any, error) {
	store, err := s.store.Diagnostics(ctx)
	if err != nil {
		return nil, err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	operations := map[string]any{}
	for name, metric := range s.metrics {
		operations[name] = map[string]any{"count": metric.Count, "rows": metric.Rows, "bytes": metric.Bytes, "latency_ms_avg": metric.TotalMS / float64(max(int64(1), metric.Count)), "latency_ms_max": metric.MaxMS}
	}
	repairs := map[string]int64{}
	for reason, count := range s.repairs {
		repairs[reason] = count
	}
	return map[string]any{"store": store, "operations": operations, "maintenance_repairs": repairs}, nil
}

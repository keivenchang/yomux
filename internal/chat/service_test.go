// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package chat

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/bazelbuild/rules_go/go/runfiles"
)

func testService(t *testing.T, now *float64) *Service {
	t.Helper()
	registry, err := runfiles.Rlocation("yomux/assets/runtime/locale-registry.json")
	if err != nil {
		t.Fatal(err)
	}
	classifier, err := NewQuestionClassifier(filepath.Dir(filepath.Dir(filepath.Dir(registry))))
	if err != nil {
		t.Fatal(err)
	}
	root := t.TempDir()
	store := openTestStore(t, filepath.Join(root, "chat.sqlite3"), func() float64 { return *now })
	key := make([]byte, 32)
	for index := range key {
		key[index] = byte(index)
	}
	keyPath := filepath.Join(root, "chat-cursor.key")
	if err := os.WriteFile(keyPath, key, 0600); err != nil {
		t.Fatal(err)
	}
	service, err := NewService(store, NewCursorCodec(keyPath), classifier, ServiceOptions{Clock: func() time.Time {
		seconds, fraction := math.Modf(*now)
		return time.Unix(int64(seconds), int64(fraction*1e9))
	}})
	if err != nil {
		t.Fatal(err)
	}
	return service
}

func TestServiceOriginalPythonBrowserContract(t *testing.T) {
	path, err := runfiles.Rlocation("yomux/tests/fixtures/chat_service_contract.json")
	if err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		Steps []struct {
			Now       float64         `json:"now"`
			Method    string          `json:"method"`
			Arguments json.RawMessage `json:"arguments"`
			Result    json.RawMessage `json:"result"`
			Error     *struct {
				Code    string `json:"code"`
				Status  int    `json:"status"`
				Message string `json:"message"`
			} `json:"error"`
		} `json:"steps"`
	}
	if err := json.Unmarshal(raw, &fixture); err != nil {
		t.Fatal(err)
	}
	if len(fixture.Steps) != 33 {
		t.Fatalf("browser contract steps=%d want 33", len(fixture.Steps))
	}
	now := fixture.Steps[0].Now
	service := testService(t, &now)
	ctx := context.Background()
	for index, step := range fixture.Steps {
		t.Run(fmt.Sprintf("%02d_%s", index, step.Method), func(t *testing.T) {
			now = step.Now
			var args struct {
				Username  string          `json:"username"`
				Browser   string          `json:"browser_instance_id"`
				Payload   SendRequest     `json:"payload"`
				Locale    string          `json:"locale"`
				SenderIP  string          `json:"sender_ip"`
				Before    json.RawMessage `json:"before"`
				After     json.RawMessage `json:"after"`
				Limit     *int            `json:"limit"`
				MessageID int64           `json:"message_id"`
				SourceID  int64           `json:"source_id"`
				Query     string          `json:"query"`
				Cursor    string          `json:"cursor"`
				Typing    bool            `json:"typing"`
				Answer    string          `json:"answer"`
			}
			if err := json.Unmarshal(step.Arguments, &args); err != nil {
				t.Fatal(err)
			}
			decodeCursor := func(raw json.RawMessage) string {
				t.Helper()
				if len(raw) == 0 {
					return ""
				}
				var value string
				if err := json.Unmarshal(raw, &value); err != nil {
					t.Fatal(err)
				}
				return value
			}
			var actual any
			var err error
			switch step.Method {
			case "bootstrap":
				actual, err = service.Bootstrap(ctx, args.Username, args.Browser)
			case "send":
				payload, sendErr := service.Send(ctx, args.Username, args.Payload, args.Locale, args.SenderIP)
				actual, err = []any{payload, payload.Created}, sendErr
			case "delta":
				actual, err = service.Delta(ctx, args.Username, decodeCursor(args.After), args.Limit)
			case "page":
				actual, err = service.Page(ctx, args.Username, decodeCursor(args.Before), args.Limit)
			case "read":
				actual, err = service.Read(ctx, args.Username, args.MessageID)
			case "context":
				var before, after *int
				if len(args.Before) > 0 {
					if err := json.Unmarshal(args.Before, &before); err != nil {
						t.Fatal(err)
					}
				}
				if len(args.After) > 0 {
					if err := json.Unmarshal(args.After, &after); err != nil {
						t.Fatal(err)
					}
				}
				actual, err = service.Context(ctx, args.Username, args.MessageID, before, after)
			case "search":
				actual, err = service.Search(ctx, args.Username, args.Query, args.Cursor, args.Limit)
			case "typing":
				actual, err = service.Typing(ctx, args.Username, args.Browser, args.Typing)
			case "yoagent_source":
				message, prompt, sourceErr := service.YOAgentSource(ctx, args.Username, args.Browser, args.MessageID)
				actual, err = []any{message, prompt}, sourceErr
			case "record_yoagent_reply":
				source, sourceErr := service.store.Context(ctx, DefaultRoom, args.SourceID, testInt(0), testInt(0), nil)
				if sourceErr != nil || source == nil {
					t.Fatalf("contract source unavailable: %v", sourceErr)
				}
				payload, replyErr := service.RecordYOAgentReply(ctx, source.Target, args.Answer)
				actual, err = []any{payload, payload.Created}, replyErr
			default:
				t.Fatalf("unknown browser contract method %q", step.Method)
			}
			if step.Error != nil {
				var typed *Error
				if !errors.As(err, &typed) || typed.Code != step.Error.Code || typed.Status != step.Error.Status || typed.Message != step.Error.Message {
					t.Fatalf("error=%v want code=%s status=%d message=%s", err, step.Error.Code, step.Error.Status, step.Error.Message)
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			assertContractJSON(t, actual, step.Result)
		})
	}
	diagnostic, err := service.Diagnostics(ctx)
	if err != nil {
		t.Fatal(err)
	}
	encoded, err := json.Marshal(diagnostic)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(encoded), "How does this work?") {
		t.Fatal("diagnostics exposed a message body")
	}
}

func TestServiceRateAdmissionConcurrentAndExactWindow(t *testing.T) {
	now := time.Unix(1700000000, 0)
	service := &Service{clock: func() time.Time { return now }, rates: map[rateKey][]time.Time{}}
	var admitted atomic.Int64
	var group sync.WaitGroup
	for range 40 {
		group.Go(func() {
			if err := service.checkRate("alice", "send", 20); err == nil {
				admitted.Add(1)
			} else {
				var typed *Error
				if !errors.As(err, &typed) || typed.Code != "rate_limited" {
					t.Errorf("unexpected admission failure: %v", err)
				}
			}
		})
	}
	group.Wait()
	if admitted.Load() != 20 {
		t.Fatalf("admitted=%d want 20", admitted.Load())
	}
	if err := service.checkRate("bob", "send", 20); err != nil {
		t.Fatal(err)
	}
	if err := service.checkRate("alice", "query", 120); err != nil {
		t.Fatal(err)
	}
	now = now.Add(time.Minute)
	if err := service.checkRate("alice", "send", 20); err != nil {
		t.Fatalf("exact window did not expire: %v", err)
	}
}

func TestServiceReplyPreservesUTF8AtByteBoundary(t *testing.T) {
	now := float64(1700000000)
	service := testService(t, &now)
	ctx := context.Background()
	sent, err := service.Send(ctx, "alice", SendRequest{BrowserInstance: "browser", ClientUUID: "question", Body: "Why?"}, "en", "")
	if err != nil {
		t.Fatal(err)
	}
	source, _, err := service.YOAgentSource(ctx, "alice", "browser", sent.Message.ID)
	if err != nil {
		t.Fatal(err)
	}
	prefix := strings.Repeat("x", BodyMaxBytes-1)
	answer, err := service.RecordYOAgentReply(ctx, source, prefix+"😀")
	if err != nil {
		t.Fatal(err)
	}
	if answer.Message.Body != prefix {
		t.Fatal("answer split a Unicode character")
	}
	retry, err := service.RecordYOAgentReply(ctx, source, "retry")
	if err != nil {
		t.Fatal(err)
	}
	if retry.Created || retry.Message.Body != prefix {
		t.Fatal("reply retry replaced the committed answer")
	}
}

func TestServiceRetentionFailurePropagatesBeforePruning(t *testing.T) {
	now := float64(1700000000)
	service := testService(t, &now)
	failure := errors.New("settings unavailable")
	service.retentionDays = func(context.Context) (int, error) { return 0, failure }
	if _, err := service.Bootstrap(context.Background(), "alice", "browser"); !errors.Is(err, failure) {
		t.Fatalf("settings failure=%v", err)
	}
}

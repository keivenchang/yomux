// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package chat

import (
	"context"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"

	"github.com/bazelbuild/rules_go/go/runfiles"
)

type cursorExample struct {
	Kind      string `json:"kind"`
	ID        int64  `json:"id"`
	Token     string `json:"token"`
	DecodedID int64  `json:"decoded_id"`
}

type cursorContract struct {
	Name   string          `json:"name"`
	Secret string          `json:"secret_hex"`
	Cases  []cursorExample `json:"cases"`
}

func cursorFixtures(t *testing.T) []cursorContract {
	t.Helper()
	path, err := runfiles.Rlocation("yomux/tests/fixtures/chat_cursor_contract.json")
	if err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var fixtures []cursorContract
	if err := json.Unmarshal(raw, &fixtures); err != nil {
		t.Fatal(err)
	}
	return fixtures
}

func TestCursorCompatibilityAndTamperRefusal(t *testing.T) {
	for _, fixture := range cursorFixtures(t) {
		t.Run(fixture.Name, func(t *testing.T) {
			raw, err := hex.DecodeString(fixture.Secret)
			if err != nil {
				t.Fatal(err)
			}
			path := filepath.Join(t.TempDir(), "chat-cursor.key")
			if err := os.WriteFile(path, raw, 0o600); err != nil {
				t.Fatal(err)
			}
			codec := NewCursorCodec(path)
			for _, example := range fixture.Cases {
				encoded, err := codec.Encode(context.Background(), example.Kind, example.ID)
				if err != nil {
					t.Fatal(err)
				}
				if encoded != example.Token {
					t.Fatalf("%s/%d cursor wire bytes differ", example.Kind, example.ID)
				}
				for _, token := range []string{encoded, encoded + strings.Repeat("=", (4-len(encoded)%4)%4)} {
					decoded, err := codec.Decode(context.Background(), token, example.Kind)
					if err != nil || decoded != example.DecodedID {
						t.Fatalf("%s/%d decoded=%d error=%v", example.Kind, example.ID, decoded, err)
					}
				}
				for _, token := range []string{encoded[:len(encoded)-1], "!", strings.Repeat("a", 4097)} {
					_, err := codec.Decode(context.Background(), token, example.Kind)
					var protocol *Error
					if !errors.As(err, &protocol) || protocol.Code != "invalid_cursor" || protocol.Status != 400 {
						t.Fatalf("invalid token error=%v", err)
					}
				}
				if _, err := codec.Decode(context.Background(), encoded, "wrong-kind"); err == nil {
					t.Fatal("cross-kind cursor accepted")
				}
			}
		})
	}

}

func TestCursorConcurrentCreationAndInvalidKeyRecovery(t *testing.T) {
	path := filepath.Join(t.TempDir(), "chat-cursor.key")
	codec := NewCursorCodec(path)
	if _, err := os.Stat(path); !os.IsNotExist(err) {
		t.Fatalf("constructor mutated secret: %v", err)
	}
	var workers sync.WaitGroup
	for i := range 20 {
		workers.Add(1)
		go func() {
			defer workers.Done()
			token, err := codec.Encode(context.Background(), "newer", int64(i))
			if err != nil {
				t.Error(err)
				return
			}
			if id, err := NewCursorCodec(path).Decode(context.Background(), token, "newer"); err != nil || id != int64(i) {
				t.Errorf("shared key decode=%d error=%v", id, err)
			}
		}()
	}
	workers.Wait()
	invalid := filepath.Join(t.TempDir(), "invalid-key")
	if err := os.WriteFile(invalid, []byte("invalid"), 0o600); err != nil {
		t.Fatal(err)
	}
	broken := NewCursorCodec(invalid)
	if _, err := broken.Encode(context.Background(), "older", 0); err == nil {
		t.Fatal("invalid key accepted")
	}
	raw, err := os.ReadFile(invalid)
	if err != nil || string(raw) != "invalid" {
		t.Fatal("invalid key replaced")
	}
	valid, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(invalid, valid, 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := broken.Encode(context.Background(), "older", 0); err != nil {
		t.Fatalf("key recovery: %v", err)
	}
}

func TestCursorCancelledOwnershipWaitCannotCreateKey(t *testing.T) {
	path := filepath.Join(t.TempDir(), "nested", "chat-cursor.key")
	codec := NewCursorCodec(path)
	codec.mu.Lock()
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := codec.Encode(ctx, "newer", 1); !errors.Is(err, context.Canceled) {
		t.Fatalf("cancelled key wait: %v", err)
	}
	codec.mu.Unlock()
	if _, err := os.Stat(filepath.Dir(path)); !os.IsNotExist(err) {
		t.Fatal("cancelled waiter created state")
	}
}

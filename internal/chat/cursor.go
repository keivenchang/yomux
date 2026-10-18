// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

// Package chat owns the host-local collaboration protocol and persistence.
package chat

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"strings"

	"github.com/keivenchang/yomux/internal/atomicfile"
	"github.com/keivenchang/yomux/internal/work"
)

// Error carries the existing browser protocol's machine-readable reason.
type Error struct {
	Code    string
	Status  int
	Message string
	Cause   error
}

func (e *Error) Error() string { return e.Message }
func (e *Error) Unwrap() error { return e.Cause }

// CursorCodec keeps existing signed history cursors valid across the migration.
// The secret remains beside the host-partitioned database; opening a codec does
// not create or change any state until the first cursor operation.
type CursorCodec struct {
	path   string
	mu     work.ContextMutex
	secret []byte
}

func NewCursorCodec(path string) *CursorCodec { return &CursorCodec{path: path} }

func (c *CursorCodec) key(ctx context.Context) ([]byte, error) {
	if err := c.mu.LockContext(ctx, nil); err != nil {
		return nil, err
	}
	defer c.mu.Unlock()
	if c.secret != nil {
		return c.secret, nil
	}
	secret, err := atomicfile.LoadOrCreateRawSecret(ctx, c.path)
	if err != nil {
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		return nil, &Error{Code: "server_error", Status: 500, Message: "invalid chat cursor key", Cause: err}
	}
	c.secret = secret
	return secret, nil
}

type cursorPayload struct {
	ID      int64  `json:"id"`
	Kind    string `json:"kind"`
	Version int    `json:"v"`
}

func cursorSignature(secret, payload []byte) []byte {
	digest := hmac.New(sha256.New, secret)
	digest.Write(payload)
	return digest.Sum(nil)[:16]
}

func (c *CursorCodec) Encode(ctx context.Context, kind string, id int64) (string, error) {
	// Field order is intentionally id, kind, v: it matches the previous sorted
	// JSON encoding, including the exact bytes covered by the signature.
	payload, err := json.Marshal(cursorPayload{ID: id, Kind: kind, Version: 1})
	if err != nil {
		return "", err
	}
	secret, err := c.key(ctx)
	if err != nil {
		return "", err
	}
	raw := append(payload, cursorSignature(secret, payload)...)
	return base64.RawURLEncoding.EncodeToString(raw), nil
}

func (c *CursorCodec) Decode(ctx context.Context, token, kind string) (int64, error) {
	if err := ctx.Err(); err != nil {
		return 0, err
	}
	invalid := func(cause error) (int64, error) {
		return 0, &Error{Code: "invalid_cursor", Status: 400, Message: "invalid chat cursor", Cause: cause}
	}
	// Browser cursors are short signed records, not arbitrary request bodies.
	if len(token) > 4096 {
		return invalid(errors.New("chat cursor is too large"))
	}
	raw, err := base64.URLEncoding.DecodeString(token + strings.Repeat("=", (4-len(token)%4)%4))
	if err != nil {
		return invalid(err)
	}
	if len(raw) <= 16 {
		return invalid(errors.New("chat cursor is truncated"))
	}
	payload, signature := raw[:len(raw)-16], raw[len(raw)-16:]
	secret, err := c.key(ctx)
	if err != nil {
		return 0, err
	}
	if !hmac.Equal(signature, cursorSignature(secret, payload)) {
		return invalid(errors.New("chat cursor signature mismatch"))
	}
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(payload, &fields); err != nil {
		return invalid(err)
	}
	var decoded cursorPayload
	for _, field := range []struct {
		name  string
		value any
	}{{"id", &decoded.ID}, {"kind", &decoded.Kind}, {"v", &decoded.Version}} {
		raw, ok := fields[field.name]
		if !ok || string(raw) == "null" {
			return invalid(errors.New("missing or null chat cursor field"))
		}
		if err := json.Unmarshal(raw, field.value); err != nil {
			return invalid(err)
		}
	}
	if decoded.Version != 1 || decoded.Kind != kind {
		return invalid(errors.New("unexpected chat cursor version or kind"))
	}
	return max(0, decoded.ID), nil
}

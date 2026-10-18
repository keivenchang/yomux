// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"bytes"
	"encoding/binary"
	"errors"
	"fmt"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestAuditAuthContract(t *testing.T) {
	s := &server{config: t.TempDir()}
	data := "users:\n  - role: viewer\n    password_hash: 'hash#value'\n    username: 'o''brien'\n    password: ignored\n  - username: default\n    password: secret\n"
	if err := os.WriteFile(filepath.Join(s.config, "auth.yaml"), []byte(data), 0600); err != nil {
		t.Fatal(err)
	}
	users := s.users()
	if len(users) != 2 || users[0].username != "o'brien" || users[0].password != "hash#value" || users[0].role != "readonly" || users[1].role != "readonly" {
		t.Fatal("auth contract mismatch")
	}
}

func TestAuditWebSocketLengths(t *testing.T) {
	for _, n := range []int{125, 126, 65535, 65536, 100000} {
		var out bytes.Buffer
		payload := bytes.Repeat([]byte{'x'}, n)
		if err := writeFrame(&out, 2, payload); err != nil {
			t.Fatal(err)
		}
		raw := out.Bytes()
		offset := 2
		size := uint64(raw[1])
		if raw[1] == 126 {
			size = uint64(binary.BigEndian.Uint16(raw[2:4]))
			offset = 4
		}
		if raw[1] == 127 {
			size = binary.BigEndian.Uint64(raw[2:10])
			offset = 10
		}
		if size != uint64(n) || !bytes.Equal(raw[offset:], payload) {
			t.Fatalf("invalid RFC6455 frame at %d", n)
		}
	}
}

func TestAuditClaudeDiscoveryProgress(t *testing.T) {
	root := t.TempDir()
	for i := 0; i < 600; i++ {
		if err := os.WriteFile(filepath.Join(root, fmt.Sprintf("%04d.jsonl", i)), nil, 0600); err != nil {
			t.Fatal(err)
		}
	}
	wanted := filepath.Join(root, "zz-target.jsonl")
	if err := os.WriteFile(wanted, nil, 0600); err != nil {
		t.Fatal(err)
	}
	foundTarget, foundFirst := false, false
	for i := 0; i < 5; i++ {
		foundTarget = foundTarget || findClaudeTranscript(root, "zz-target") == wanted
		foundFirst = foundFirst || findClaudeTranscript(root, "0000") == filepath.Join(root, "0000.jsonl")
		if foundTarget && foundFirst {
			return
		}
	}
	t.Fatal("discovery never progressed past initial prefix")
}

type auditFlushFailure struct{ *httptest.ResponseRecorder }

func (auditFlushFailure) FlushError() error { return errors.New("delivery failed") }
func TestAuditSSEFlushFailure(t *testing.T) {
	frame, ok := newJSONSSEWriter(&loggingResponseWriter{ResponseWriter: auditFlushFailure{httptest.NewRecorder()}})
	if !ok {
		t.Fatal("no stream")
	}
	stream := statsSSEWriter{stream: frame, startedAt: time.Now()}
	if err := stream.write("ready", map[string]any{}); err == nil {
		t.Fatal("flush error discarded")
	}
}

func TestAuditPaneSelectionOrder(t *testing.T) {
	rows := []string{"session\t0\told\t0\t%1\t/tmp\tbash\ttitle\t0\t1\t0", "session\t1\tcurrent\t0\t%2\t/tmp\tbash\ttitle\t0\t1\t1"}
	s := &server{}
	for _, output := range []string{strings.Join(rows, "\n"), rows[1] + "\n" + rows[0]} {
		result := s.liveSessionMetadataFromOutputWithIndex("session", []byte(output), nil, &processEntryIndex{})
		pane, _ := result["selected_pane"].(map[string]any)
		if pane["pane_id"] != "%2" {
			t.Fatalf("wrong selected pane: %v", pane)
		}
	}
}

func TestAuditStatsStreamReleasesDemandOnFlushFailure(t *testing.T) {
	s := &server{}
	response := auditFlushFailure{httptest.NewRecorder()}
	request := httptest.NewRequest("GET", "/api/stats-stream?range_seconds=300&resolution=10&client_id=audit", nil)
	s.statsStream(response, request)
	if s.statsDemand.Load() != 0 {
		t.Fatal("failed stream retained demand")
	}
	if response.Body.Len() != 0 {
		t.Fatal("snapshot attempted after failed initial flush")
	}
}

func TestAuditCacheFailedStagePreservesReadablePublication(t *testing.T) {
	if os.Geteuid() == 0 {
		t.Skip("requires unprivileged filesystem permissions")
	}
	config := t.TempDir()
	writer := &server{config: config}
	request := sessionFilesRequest{hours: 24, fromRef: "HEAD", toRef: "current"}
	view := sessionFilesCacheViewID(sessionFilesRequestDescriptor(request))
	writer.writeSessionFilesCache(request, view, map[string]any{"marker": "previous"}, 200)
	directory := writer.sessionFilesCacheDirectory()
	if err := os.Chmod(directory, 0500); err != nil {
		t.Fatal(err)
	}
	defer os.Chmod(directory, 0700)
	writer.writeSessionFilesCache(request, view, map[string]any{"marker": "failed"}, 200)
	reader := &server{config: config}
	record, ok := reader.readSessionFilesCache(request, view)
	if !ok || sessionFilesCachePayload(record)["marker"] != "previous" {
		t.Fatalf("failed publication replaced readable state: %v", record.Payload)
	}
}

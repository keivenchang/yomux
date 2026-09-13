package main

import (
	"bufio"
	"bytes"
	"net/http"
	"net/http/httptest"
	"os/exec"
	"testing"
)

func TestWriteAndReadMaskedFrame(t *testing.T) {
	var wire bytes.Buffer
	clientFrame := []byte{0x81, 0x83, 1, 2, 3, 4, 'h' ^ 1, 'i' ^ 2, '!' ^ 3}
	if err := writeFrame(&wire, 1, []byte("ok")); err != nil {
		t.Fatal(err)
	}
	if wire.Len() != 4 {
		t.Fatalf("server frame length = %d, want 4", wire.Len())
	}
	opcode, payload, err := readFrame(bufio.NewReader(bytes.NewReader(clientFrame)))
	if err != nil {
		t.Fatal(err)
	}
	if opcode != 1 || string(payload) != "hi!" {
		t.Fatalf("frame = opcode %d payload %q", opcode, payload)
	}
}

func TestHealthAndCapabilities(t *testing.T) {
	s := &server{root: t.TempDir(), children: make(map[*exec.Cmd]struct{})}
	record := httptest.NewRecorder()
	s.health(record, httptest.NewRequest(http.MethodGet, "/healthz", nil))
	if record.Code != http.StatusOK || !bytes.Contains(record.Body.Bytes(), []byte(`"backend":"go"`)) {
		t.Fatalf("health response = %d %s", record.Code, record.Body.String())
	}
}

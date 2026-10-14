// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package finder

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"io"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestWorkerNotifiesPublicationWithoutStatusPolling(t *testing.T) {
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, "found.go"), []byte("test"), 0600); err != nil {
		t.Fatal(err)
	}
	input, inputWriter := io.Pipe()
	outputReader, output := io.Pipe()
	done := make(chan error, 1)
	go func() {
		done <- RunSearchIndexWorker(input, output, filepath.Join(t.TempDir(), "index.sqlite3"))
		output.Close()
	}()
	request := indexRequest{ID: 1, Action: "ensure", Root: root, MaxFiles: 100, MaxBytes: DefaultIndexBytes, RefreshSec: 60}
	data, err := json.Marshal(request)
	if err != nil {
		t.Fatal(err)
	}
	responses := make(chan indexResponse, 2)
	go func() {
		scanner := bufio.NewScanner(outputReader)
		for scanner.Scan() {
			var response indexResponse
			if json.Unmarshal(scanner.Bytes(), &response) == nil {
				responses <- response
			}
		}
	}()
	if _, err := inputWriter.Write(append(data, '\n')); err != nil {
		t.Fatal(err)
	}
	notified := false
	acknowledged := false
	timeout := time.NewTimer(5 * time.Second)
	defer timeout.Stop()
	for !notified || !acknowledged {
		select {
		case response := <-responses:
			if response.ID == 1 {
				acknowledged = true
			}
			if response.ID == 0 && response.Payload["indexed"] == true {
				notified = true
			}
		case <-timeout.C:
			t.Fatal("worker did not acknowledge and notify independently")
		}
	}
	inputWriter.Close()
	if err := <-done; err != nil {
		t.Fatal(err)
	}
	outputReader.Close()
}

func TestCPUCaptureBounds(t *testing.T) {
	capture := &CPUCapture{}
	if capture.Start(61) == nil || capture.Start(-1) == nil {
		t.Fatal("invalid capture duration accepted")
	}
	buffer := &boundedCPUBuffer{}
	if _, err := buffer.Write(bytes.Repeat([]byte{1}, 32*1024*1024)); err != nil {
		t.Fatal(err)
	}
	if _, err := buffer.Write([]byte{1}); err == nil || !buffer.overflow {
		t.Fatal("CPU profile exceeded bound")
	}
}

func TestStartupMigratesOnlyMatchingLegacySettingsCache(t *testing.T) {
	for _, test := range []struct{ legacyRevision, storedRevision uint64 }{{17, 17}, {18, 17}, {17, 16}} {
		legacyRevision := test.legacyRevision
		root := t.TempDir()
		store, err := openSearchIndexStore(filepath.Join(t.TempDir(), "index.sqlite3"))
		if err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(root, "cached.go"), []byte("fixture"), 0600); err != nil {
			t.Fatal(err)
		}
		info, err := os.Stat(root)
		if err != nil {
			t.Fatal(err)
		}
		engine := newSearchIndexEngine(store)
		if _, err := engine.fenceSettings([]string{root}, 17, false); err != nil {
			t.Fatal(err)
		}
		if err := engine.build(indexBuildJob{root: root, identity: fileID(info), maxFiles: 100, maxBytes: DefaultIndexBytes, settingsRevision: 17}); err != nil {
			t.Fatal(err)
		}
		before, err := engine.status(root, fileID(info))
		if err != nil {
			t.Fatal(err)
		}
		if _, err := store.db.Exec(`UPDATE roots SET settings_revision=? WHERE root=?`, test.storedRevision, root); err != nil {
			t.Fatal(err)
		}
		reloaded := newSearchIndexEngine(store)
		if _, err := reloaded.handleContext(context.Background(), indexRequest{Action: "settings_fence", FenceRoots: []string{root}, SettingsRevision: 23, LegacySettingsRevision: legacyRevision}); err != nil {
			t.Fatal(err)
		}
		after, err := reloaded.search(root, fileID(info), "cached", 10, "", 23)
		if err != nil {
			t.Fatal(err)
		}
		if legacyRevision == 17 && test.storedRevision == 17 {
			if after["indexed"] != true || after["count"] != 1 || after["generation"] != before["generation"] {
				t.Fatalf("matching legacy cache lost: %#v", after)
			}
			if reloaded.fullDiscoveries != 0 {
				t.Fatal("startup migration crawled the filesystem")
			}
		} else if after["indexed"] == true {
			t.Fatal("unrelated settings identity preserved a stale cache")
		}
		if err := store.db.Close(); err != nil {
			t.Fatal(err)
		}
	}
}

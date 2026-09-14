package stats

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

func TestWriterLeaseRejectsSecondOwnerAndReleasesOnClose(t *testing.T) {
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	first, err := OpenWriter(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := OpenWriter(context.Background(), path); err == nil {
		t.Fatal("second writer acquired the singleton lease")
	}
	if err := first.Close(); err != nil {
		t.Fatal(err)
	}
	second, err := OpenWriter(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	if err := second.Close(); err != nil {
		t.Fatal(err)
	}
}

func TestWriterFenceRejectsNewerBuildBeforeSQLiteMutation(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "stats.sqlite3")
	fence := writerFence{
		ApplicationID: ApplicationID, DatabaseFilename: filepath.Base(path),
		SchemaVersion: SchemaVersion, MinimumWriterProtocol: writerProtocol,
		MinimumWriterBuild: writerBuild + 1,
	}
	data, err := json.Marshal(fence)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, writerFenceFilename), data, 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := OpenWriter(context.Background(), path); err == nil {
		t.Fatal("newer writer fence was accepted")
	}
	if _, err := os.Stat(path); !os.IsNotExist(err) {
		t.Fatalf("writer touched database after fence rejection: %v", err)
	}
}

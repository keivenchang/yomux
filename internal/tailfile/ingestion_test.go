// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package tailfile

import (
	"context"
	"os"
	"path/filepath"
	"testing"
)

func TestIngestionEverySplitAndIndependentReplay(t *testing.T) {
	data := []byte("{\"one\":1}\n{\"two\":2}\n")
	for split := 0; split <= len(data); split++ {
		path := filepath.Join(t.TempDir(), "transcript.jsonl")
		if err := os.WriteFile(path, data[:split], 0600); err != nil {
			t.Fatal(err)
		}
		owner := &Ingestion{}
		first, err := owner.ReadBatch(context.Background(), path, Position{}, 1024, 100)
		if err != nil {
			t.Fatal(split, err)
		}
		f, err := os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0600)
		if err != nil {
			t.Fatal(err)
		}
		if _, err = f.Write(data[split:]); err != nil {
			t.Fatal(err)
		}
		f.Close()
		second, err := owner.ReadBatch(context.Background(), path, first.After, 1024, 100)
		if err != nil {
			t.Fatal(split, err)
		}
		if len(first.Records)+len(second.Records) != 2 || !second.Complete || second.Reset {
			t.Fatalf("split %d: first=%+v second=%+v", split, first, second)
		}
		replay, err := owner.ReadBatch(context.Background(), path, Position{}, 1024, 100)
		if err != nil {
			t.Fatal(err)
		}
		if len(replay.Records) != 2 {
			t.Fatalf("replay: %+v", replay)
		}
		bytes := owner.BytesRead
		_, err = owner.ReadBatch(context.Background(), path, Position{}, 1024, 100)
		if err != nil {
			t.Fatal(err)
		}
		if owner.BytesRead != bytes {
			t.Fatal("identical consumer reread physical data")
		}
	}
}
func TestIngestionReplacementTruncationAndCancellation(t *testing.T) {
	path := filepath.Join(t.TempDir(), "transcript.jsonl")
	owner := &Ingestion{}
	if err := os.WriteFile(path, []byte("old\nrecord\n"), 0600); err != nil {
		t.Fatal(err)
	}
	old, err := owner.ReadBatch(context.Background(), path, Position{}, 1024, 100)
	if err != nil {
		t.Fatal(err)
	}
	for _, data := range []string{"new\n", "replacement\n"} {
		if err = os.WriteFile(path, []byte(data), 0600); err != nil {
			t.Fatal(err)
		}
		next, err := owner.ReadBatch(context.Background(), path, old.After, 1024, 100)
		if err != nil {
			t.Fatal(err)
		}
		if !next.Reset || next.Gap == "" || len(next.Records) != 1 {
			t.Fatalf("missing explicit generation reset: %+v", next)
		}
		old = next
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err = owner.ReadBatch(ctx, path, old.After, 1024, 100); err == nil {
		t.Fatal("cancelled batch succeeded")
	}
}

func TestIngestionLongPartialAppendReadsOnlyTailAndSamples(t *testing.T) {
	path := filepath.Join(t.TempDir(), "transcript.jsonl")
	data := make([]byte, 2<<20)
	for n := range data {
		data[n] = 'x'
	}
	if err := os.WriteFile(path, data, 0600); err != nil {
		t.Fatal(err)
	}
	owner := &Ingestion{}
	first, err := owner.ReadBatch(context.Background(), path, Position{}, 4<<20, 4096)
	if err != nil {
		t.Fatal(err)
	}
	before, _ := owner.Metrics()
	f, err := os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0600)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = f.Write([]byte("x\n")); err != nil {
		t.Fatal(err)
	}
	f.Close()
	next, err := owner.ReadBatch(context.Background(), path, first.After, 4<<20, 4096)
	if err != nil {
		t.Fatal(err)
	}
	after, _ := owner.Metrics()
	if after-before > 256<<10 {
		t.Fatalf("append reread old partial: %d bytes", after-before)
	}
	if !next.Complete || len(next.Records) != 1 || len(next.Records[0].Data) != len(data)+2 {
		t.Fatalf("partial completion lost bytes: complete=%v records=%d", next.Complete, len(next.Records))
	}
}

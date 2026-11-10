// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package tailfile

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
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

func TestIngestionChangedSnapshotRereadsPartialWithinBudget(t *testing.T) {
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
	// The former tail-only assertion was incompatible with detecting middle
	// rewrites. A changed snapshot now rereads the 2 MiB unfinished record,
	// while retaining the caller's 4 MiB physical read bound.
	if after-before < int64(len(data)) || after-before > 4<<20 {
		t.Fatalf("changed-snapshot reread = %d bytes; want [2 MiB, 4 MiB]", after-before)
	}
	t.Logf("changed-snapshot physical reads: %d bytes for %d-byte partial plus newline append", after-before, len(data))
	if !next.Complete || len(next.Records) != 1 || len(next.Records[0].Data) != len(data)+2 {
		t.Fatalf("partial completion lost bytes: complete=%v records=%d", next.Complete, len(next.Records))
	}
}

func TestIngestionPartialSnapshotPreservesCurrentJSON(t *testing.T) {
	for _, rewrite := range []bool{false, true} {
		t.Run(map[bool]string{false: "append", true: "middle-rewrite"}[rewrite], func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "source.jsonl")
			row := struct {
				Padding string `json:"padding"`
				Path    string `json:"path"`
				Tail    string `json:"tail"`
			}{strings.Repeat("a", 9000), "/old.md", strings.Repeat("z", 9000)}
			data, err := json.Marshal(row)
			if err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(path, data, 0600); err != nil {
				t.Fatal(err)
			}
			owner := &Ingestion{}
			first, err := owner.ReadBatch(context.Background(), path, Position{}, 65536, 1)
			if err != nil {
				t.Fatal(err)
			}
			if len(first.Records) != 0 || first.After.Offset != 0 {
				t.Fatal("incomplete JSON committed")
			}
			expected := "/old.md"
			if rewrite {
				data = bytes.Replace(data, []byte(expected), []byte("/new.md"), 1)
				expected = "/new.md"
			}
			if rewrite {
				if err := os.WriteFile(path, append(data, '\n'), 0600); err != nil {
					t.Fatal(err)
				}
			} else {
				file, err := os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0600)
				if err != nil {
					t.Fatal(err)
				}
				_, writeErr := file.Write([]byte{'\n'})
				closeErr := file.Close()
				if writeErr != nil {
					t.Fatal(writeErr)
				}
				if closeErr != nil {
					t.Fatal(closeErr)
				}
			}
			second, err := owner.ReadBatch(context.Background(), path, first.After, 65536, 1)
			if err != nil {
				t.Fatal(err)
			}
			if len(second.Records) != 1 || !second.Complete {
				t.Fatal("complete JSON missing")
			}
			var decoded struct {
				Path string `json:"path"`
			}
			if err := json.Unmarshal(second.Records[0].Data, &decoded); err != nil {
				t.Fatal(err)
			}
			if decoded.Path != expected {
				t.Fatalf("source path=%s emitted path=%s", expected, decoded.Path)
			}
		})
	}
}

func TestIngestionImmutablePartialPollsReuseBytes(t *testing.T) {
	path := filepath.Join(t.TempDir(), "source.jsonl")
	if err := os.WriteFile(path, bytes.Repeat([]byte("x"), 20000), 0600); err != nil {
		t.Fatal(err)
	}
	owner := &Ingestion{}
	first, err := owner.ReadBatch(context.Background(), path, Position{}, 65536, 1)
	if err != nil {
		t.Fatal(err)
	}
	before, _ := owner.Metrics()
	for i := 0; i < 3; i++ {
		next, err := owner.ReadBatch(context.Background(), path, first.After, 65536, 1)
		if err != nil {
			t.Fatal(err)
		}
		if len(next.Records) != 0 || next.After.Offset != 0 || len(owner.partial[path].data) != 20000 {
			t.Fatal("immutable partial progress changed")
		}
	}
	after, _ := owner.Metrics()
	if after != before {
		t.Fatalf("immutable partial reread %d bytes", after-before)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := owner.ReadBatch(ctx, path, first.After, 65536, 1); err == nil {
		t.Fatal("cancelled cached partial succeeded")
	}
}

func TestIngestionLargeFramesRespectPerPollBudgetAndRecordLimit(t *testing.T) {
	for _, size := range []int{5 << 20, 9 << 20} {
		t.Run(map[int]string{5 << 20: "bounded-progress", 9 << 20: "oversized"}[size], func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "source.jsonl")
			data := append(bytes.Repeat([]byte("x"), size), '\n')
			data = append(data, []byte("next\n")...)
			if err := os.WriteFile(path, data, 0600); err != nil {
				t.Fatal(err)
			}
			owner := &Ingestion{}
			const budget = 256 << 10
			var position Position
			for poll := 0; poll < 80; poll++ {
				before, _ := owner.Metrics()
				batch, err := owner.ReadBatch(context.Background(), path, position, budget, 1)
				after, _ := owner.Metrics()
				// Page alignment and the validation samples may read adjacent pages;
				// neither may turn an oversized frame into an unbounded single poll.
				if after-before > budget+2*ingestionPageBytes {
					t.Fatalf("poll read %d bytes, budget=%d", after-before, budget)
				}
				if err != nil {
					if size <= 8<<20 || !strings.Contains(err.Error(), "exceeds") {
						t.Fatal(err)
					}
					return
				}
				if len(batch.Records) > 1 {
					t.Fatal("record limit exceeded")
				}
				position = batch.After
				if len(batch.Records) == 1 {
					if size > 8<<20 {
						t.Fatal("oversized frame emitted")
					}
					if batch.Complete || len(batch.Records[0].Data) != size+1 {
						t.Fatal("large frame boundary lost")
					}
					last, err := owner.ReadBatch(context.Background(), path, position, budget, 1)
					if err != nil {
						t.Fatal(err)
					}
					if !last.Complete || len(last.Records) != 1 || string(last.Records[0].Data) != "next\n" {
						t.Fatal("following record lost")
					}
					return
				}
			}
			t.Fatal("large-frame progress never completed")
		})
	}
}

func TestIngestionErrorsReportPhysicalReads(t *testing.T) {
	path := filepath.Join(t.TempDir(), "oversized.jsonl")
	if err := os.WriteFile(path, append(bytes.Repeat([]byte("x"), (8<<20)+1), '\n'), 0600); err != nil {
		t.Fatal(err)
	}
	owner := &Ingestion{}
	before, _ := owner.Metrics()
	batch, err := owner.ReadBatch(context.Background(), path, Position{}, (8<<20)+(256<<10)+1, 1)
	after, _ := owner.Metrics()
	if err == nil || !strings.Contains(err.Error(), "exceeds") {
		t.Fatalf("oversized error=%v", err)
	}
	if batch.BytesRead == 0 || batch.BytesRead != after-before {
		t.Fatalf("error reported %d physical bytes; actual=%d", batch.BytesRead, after-before)
	}
	// Cancellation can arrive after snapshot validation has read a prefix page.
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	owner = &Ingestion{}
	position := Position{Generation: 1, Size: int64(8 << 20), Offset: 0}
	batch, err = owner.ReadBatch(ctx, path, position, 1<<20, 1)
	actual, _ := owner.Metrics()
	if err == nil || batch.BytesRead != actual || actual == 0 {
		t.Fatalf("cancel error=%v reported=%d actual=%d", err, batch.BytesRead, actual)
	}
}

func TestIngestionFullFrameBudgetHandlesAppendsAtRecordBoundary(t *testing.T) {
	for _, size := range []int{MaxRecordBytes, MaxRecordBytes + 1} {
		t.Run(fmt.Sprint(size), func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "source.jsonl")
			data := append(bytes.Repeat([]byte("x"), size-1), '\n')
			if err := os.WriteFile(path, data, 0600); err != nil {
				t.Fatal(err)
			}
			owner := &Ingestion{}
			for poll := 0; poll < 2; poll++ {
				file, err := os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0600)
				if err != nil {
					t.Fatal(err)
				}
				_, writeErr := file.Write([]byte("{}\n"))
				closeErr := file.Close()
				if writeErr != nil {
					t.Fatal(writeErr)
				}
				if closeErr != nil {
					t.Fatal(closeErr)
				}
				before, _ := owner.Metrics()
				batch, err := owner.ReadBatch(context.Background(), path, Position{}, CompleteRecordBatchBytes, 1)
				after, _ := owner.Metrics()
				if batch.BytesRead != after-before || batch.BytesRead > CompleteRecordBatchBytes {
					t.Fatalf("full-frame read=%d reported=%d budget=%d", after-before, batch.BytesRead, CompleteRecordBatchBytes)
				}
				if size > MaxRecordBytes {
					if err == nil || !strings.Contains(err.Error(), "exceeds") {
						t.Fatalf("max+1 frame was not rejected: %v", err)
					}
				} else if err != nil || len(batch.Records) != 1 || batch.After.Offset != int64(size) {
					t.Fatalf("max frame did not complete while later rows append: offset=%d records=%d err=%v", batch.After.Offset, len(batch.Records), err)
				}
			}
		})
	}
}

func TestIngestionBatchPageReferencesSurviveSharedEviction(t *testing.T) {
	path := filepath.Join(t.TempDir(), "source.jsonl")
	if err := os.WriteFile(path, bytes.Repeat([]byte("x"), 3*ingestionPageBytes), 0600); err != nil {
		t.Fatal(err)
	}
	file, err := os.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	owner := &Ingestion{}
	snapshot, err := owner.Snapshot(file)
	if err != nil {
		t.Fatal(err)
	}
	snapshot.batchPages = make(map[int64][]byte)
	for _, offset := range []int64{17, 2*ingestionPageBytes + 31, 4096, 2*ingestionPageBytes + 39, 0} {
		if _, err := snapshot.ReadAt(make([]byte, 100), offset); err != nil {
			t.Fatal(err)
		}
		owner.mu.Lock()
		owner.pages = map[pageKey][]byte{}
		owner.order = nil
		owner.mu.Unlock()
	}
	actual, _ := owner.Metrics()
	if actual != 2*ingestionPageBytes {
		t.Fatalf("batch reread evicted pages: physical=%d want=%d", actual, 2*ingestionPageBytes)
	}
	if len(snapshot.batchPages) != 2 {
		t.Fatalf("pinned page references=%d want2", len(snapshot.batchPages))
	}
}

func TestIngestionFullFrameBudgetIncludesUnalignedValidationRereads(t *testing.T) {
	for _, unfinished := range []bool{false, true} {
		t.Run(fmt.Sprint(unfinished), func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "source.jsonl")
			prefix := append(bytes.Repeat([]byte("p"), ingestionPageBytes+16), '\n')
			data := append([]byte(nil), prefix...)
			expectedOffset := int64(len(prefix) + MaxRecordBytes)
			if unfinished {
				// The final complete boundary remains far behind the following unfinished
				// frame, so its validation tail will seek back after scanning that frame.
				data = append(data, []byte("{}\n")...)
				data = append(data, bytes.Repeat([]byte("x"), MaxRecordBytes-100)...)
				expectedOffset = int64(len(prefix) + 3)
			} else {
				data = append(data, append(bytes.Repeat([]byte("x"), MaxRecordBytes-1), '\n')...)
			}
			if err := os.WriteFile(path, data, 0600); err != nil {
				t.Fatal(err)
			}
			owner := &Ingestion{}
			first, err := owner.ReadBatch(context.Background(), path, Position{}, CompleteRecordBatchBytes, 1)
			if err != nil {
				t.Fatal(err)
			}
			if first.After.Offset != int64(len(prefix)) {
				t.Fatal("fixture prefix boundary lost")
			}
			// Make the prefix/tail pages the oldest shared-cache entries, ensuring
			// the large body evicts them before final validation.
			other := filepath.Join(t.TempDir(), "other.jsonl")
			if err := os.WriteFile(other, bytes.Repeat([]byte("z"), (ingestionCachePages-2)*ingestionPageBytes), 0600); err != nil {
				t.Fatal(err)
			}
			file, err := os.Open(other)
			if err != nil {
				t.Fatal(err)
			}
			snapshot, err := owner.Snapshot(file)
			if err != nil {
				file.Close()
				t.Fatal(err)
			}
			for page := int64(0); page < ingestionCachePages-2; page++ {
				if _, err := snapshot.ReadAt(make([]byte, 1), page*ingestionPageBytes); err != nil {
					file.Close()
					t.Fatal(err)
				}
			}
			file.Close()
			before, _ := owner.Metrics()
			batch, err := owner.ReadBatch(context.Background(), path, first.After, CompleteRecordBatchBytes, 4096)
			after, _ := owner.Metrics()
			if err != nil {
				t.Fatal(err)
			}
			if batch.After.Offset != expectedOffset {
				t.Fatalf("offset=%d want%d", batch.After.Offset, expectedOffset)
			}
			if batch.BytesRead != after-before || batch.BytesRead > CompleteRecordBatchBytes {
				t.Fatalf("unaligned validation physical=%d reported=%d budget=%d", after-before, batch.BytesRead, CompleteRecordBatchBytes)
			}
			t.Logf("unaligned batch unfinished=%v physical=%d budget=%d", unfinished, batch.BytesRead, CompleteRecordBatchBytes)
		})
	}
}

func TestSnapshotTailAndCheckpointPinTheSameGeneration(t *testing.T) {
	path := filepath.Join(t.TempDir(), "transcript.jsonl")
	if err := os.WriteFile(path, []byte("first\nsecond\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	file, err := os.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	owner := &Ingestion{}
	snapshot, err := owner.Snapshot(file)
	if err != nil {
		t.Fatal(err)
	}
	text, err := snapshot.ReadTail(1, 65536)
	if err != nil || text != "second\n" {
		t.Fatalf("tail=%q err=%v", text, err)
	}
	position, err := snapshot.Checkpoint(context.Background(), snapshot.Size())
	if err != nil || position.Offset != snapshot.Size() {
		t.Fatalf("checkpoint=%+v err=%v", position, err)
	}
	for _, offset := range []int64{-1, snapshot.Size() + 1} {
		if _, err := snapshot.Checkpoint(context.Background(), offset); err == nil {
			t.Fatalf("invalid offset %d admitted", offset)
		}
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := snapshot.Checkpoint(ctx, 0); err == nil {
		t.Fatal("cancelled checkpoint admitted")
	}
	if err := os.WriteFile(path, []byte("first\nsecond\nthird\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := snapshot.Checkpoint(context.Background(), snapshot.Size()); err == nil {
		t.Fatal("displayed old snapshot attached to a new generation")
	}
	batch, err := owner.ReadBatch(context.Background(), path, position, CompleteRecordBatchBytes, 100)
	if err != nil || batch.Reset || len(batch.Records) != 1 || string(batch.Records[0].Data) != "third\n" {
		t.Fatalf("append batch=%+v err=%v", batch, err)
	}
}

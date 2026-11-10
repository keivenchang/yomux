// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package stats

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"testing"

	"github.com/keivenchang/yomux/internal/tailfile"
)

func TestSharedIngestionPreservesUsageAndIndependentCommitPositions(t *testing.T) {
	for _, provider := range []string{"codex", "claude"} {
		t.Run(provider, func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "rollout.jsonl")
			records := []map[string]any{{"type": "non_usage", "body": "file attribution must see this"}, usageCodex(100, 20, 3, 5), usageCodex(101, 25, 4, 8)}
			if provider == "claude" {
				records = []map[string]any{{"type": "non_usage"}, usageClaude(100, "m1", 20, 3), usageClaude(101, "m2", 25, 4)}
			}
			writeJSONL(t, path, records...)
			owner := &tailfile.Ingestion{}
			source := TranscriptSource{Path: path, Provider: provider, ThreadID: "parity"}
			baseline, shared := &TranscriptScannerState{}, &TranscriptScannerState{}
			old, err := ScanTranscript(source, baseline, TranscriptScanOptions{MaxRecords: 1})
			if err != nil {
				t.Fatal(err)
			}
			next, err := ScanTranscript(source, shared, TranscriptScanOptions{MaxRecords: 1, Ingestion: owner})
			if err != nil {
				t.Fatal(err)
			}
			if !reflect.DeepEqual(old.Atoms, next.Atoms) {
				t.Fatal("first-record usage drift")
			}
			old.Receipt.Commit()
			next.Receipt.Rollback()
			if shared.Offset != 0 {
				t.Fatal("failed stats commit advanced position")
			}
			// Attribution commits all records while stats is rolled back. Stats can
			// replay from its own original source, including non-usage sequence rows.
			batch, err := owner.ReadBatch(context.Background(), path, tailfile.Position{}, 4<<20, 4096)
			if err != nil || len(batch.Records) != 3 {
				t.Fatal(len(batch.Records), err)
			}
			encoded, err := json.Marshal(shared)
			if err != nil {
				t.Fatal(err)
			}
			shared = &TranscriptScannerState{}
			if err = json.Unmarshal(encoded, shared); err != nil {
				t.Fatal(err)
			}
			var originalAtoms, sharedAtoms []UsageAtom
			whole := &TranscriptScannerState{}
			for rounds := 0; rounds < 10; rounds++ {
				scan, err := ScanTranscript(source, whole, TranscriptScanOptions{MaxRecords: 1})
				if err != nil {
					t.Fatal(err)
				}
				originalAtoms = append(originalAtoms, scan.Atoms...)
				scan.Receipt.Commit()
				if !scan.BudgetExhausted {
					break
				}
			}
			for rounds := 0; rounds < 10; rounds++ {
				scan, err := ScanTranscript(source, shared, TranscriptScanOptions{MaxRecords: 1, Ingestion: owner})
				if err != nil {
					t.Fatal(err)
				}
				sharedAtoms = append(sharedAtoms, scan.Atoms...)
				scan.Receipt.Commit()
				if !scan.BudgetExhausted {
					break
				}
			}
			if len(originalAtoms) == 0 || !reflect.DeepEqual(originalAtoms, sharedAtoms) {
				t.Fatalf("usage or event identity drift: old %d shared %d", len(originalAtoms), len(sharedAtoms))
			}
			if !reflect.DeepEqual(whole, shared) {
				t.Fatalf("durable scanner state drift: baseline %#v shared %#v", whole, shared)
			}
			// The shared boundary retains the fork repair no-symlink admission.
			link := filepath.Join(filepath.Dir(path), "link.jsonl")
			if err = os.Symlink(path, link); err != nil {
				t.Fatal(err)
			}
			if provider == "codex" {
				if _, err = ScanTranscript(TranscriptSource{Path: link, Provider: provider, CodexForkRepairOnly: true}, &TranscriptScannerState{}, TranscriptScanOptions{Ingestion: owner}); err == nil {
					t.Fatal("repair accepted invalid path")
				}
			}
		})
	}
}

func TestSharedIngestionEveryAppendSplitPreservesAccountingState(t *testing.T) {
	for _, provider := range []string{"claude", "codex"} {
		t.Run(provider, func(t *testing.T) {
			records := []map[string]any{{"type": "non_usage"}, usageClaude(100, "message", 20, 3), usageClaude(101, "message", 25, 4)}
			if provider == "codex" {
				records = []map[string]any{
					{"type": "session_meta", "payload": map[string]any{"id": "child", "forked_from_id": "parent", "thread_source": "subagent"}},
					usageCodex(100, 100, 40, 10),
					{"type": "event_msg", "payload": map[string]any{"type": "inter_agent_communication_metadata"}},
					usageCodex(101, 160, 75, 30),
				}
			}
			var data []byte
			for _, record := range records {
				row, err := json.Marshal(record)
				if err != nil {
					t.Fatal(err)
				}
				data = append(data, row...)
				data = append(data, '\n')
			}
			path := filepath.Join(t.TempDir(), "rollout.jsonl")
			for split := 0; split <= len(data); split++ {
				if err := os.WriteFile(path, data[:split], 0600); err != nil {
					t.Fatal(err)
				}
				baseline, shared := &TranscriptScannerState{}, &TranscriptScannerState{}
				owner := &tailfile.Ingestion{}
				source := TranscriptSource{Path: path, Provider: provider, ThreadID: "parity"}
				for phase := 0; phase < 2; phase++ {
					a, err := ScanTranscript(source, baseline, TranscriptScanOptions{})
					if err != nil {
						t.Fatal(split, phase, err)
					}
					b, err := ScanTranscript(source, shared, TranscriptScanOptions{Ingestion: owner})
					if err != nil {
						t.Fatal(split, phase, err)
					}
					a.Receipt.Commit()
					b.Receipt.Commit()
					if !reflect.DeepEqual(a.Atoms, b.Atoms) || !reflect.DeepEqual(a.Tombstones, b.Tombstones) || !reflect.DeepEqual(baseline, shared) {
						t.Fatalf("accounting drift at split %d phase %d", split, phase)
					}
					if phase == 0 {
						f, err := os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0600)
						if err != nil {
							t.Fatal(err)
						}
						_, err = f.Write(data[split:])
						f.Close()
						if err != nil {
							t.Fatal(err)
						}
					}
				}
			}
		})
	}
}

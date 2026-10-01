package stats

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"
)

func TestAgentTokenCollectorLegacyOpenCodeReplayPreservesTotalsAndAdvancesCursor(t *testing.T) {
	testLegacyOpenCodeReplay(t, false, false)
}

func TestAgentTokenCollectorLegacyOpenCodeReplayRejectsQuantityConflict(t *testing.T) {
	testLegacyOpenCodeReplay(t, true, false)
}

func TestAgentTokenCollectorLegacyOpenCodeReplayPreservesHistoricalAttribution(t *testing.T) {
	testLegacyOpenCodeReplay(t, false, true)
}

func testLegacyOpenCodeReplay(t *testing.T, quantityConflict, changeAttribution bool) {
	t.Helper()
	ctx := context.Background()
	root := t.TempDir()
	path := filepath.Join(root, "stats.sqlite3")
	source := copyOpenCodeFixture(t)
	cursorPath := filepath.Join(root, "opencode-cursor.json")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	clock := &agentStatusTestClock{now: time.Unix(1788307300, 0)}
	liveSession := "legacy-opencode"
	config := AgentTokenCollectorConfig{
		OpenCodeDatabase: source, CursorPath: cursorPath, SkipTranscriptScan: true,
		Inventory: func(context.Context) ([]AgentRecord, []error) {
			return []AgentRecord{{Kind: "opencode", OpenCodeSession: "ses-1", TmuxSession: liveSession}}, nil
		},
	}
	seedWriter, err := OpenWriter(ctx, filepath.Join(t.TempDir(), "seed.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer seedWriter.Close()
	seed := NewAgentTokenCollector(seedWriter, clock, config)
	defer seed.Stop()
	batch, receipts, err := seed.collect(ctx, float64(clock.now.Unix()), 10)
	if err != nil || len(batch.UsageAtoms) == 0 || len(receipts) == 0 {
		t.Fatalf("seed collection: atoms=%d receipts=%d err=%v", len(batch.UsageAtoms), len(receipts), err)
	}
	// Persist the exact metadata shape emitted by the prior Go implementation,
	// retaining source-derived event identities and quantities independently of
	// the candidate's new optional metadata fields.
	legacyAtoms := make([]UsageAtom, len(batch.UsageAtoms))
	wantTotal := 0.0
	for index, atom := range batch.UsageAtoms {
		legacyAtoms[index] = atom
		legacyAtoms[index].Payload = map[string]any{}
		for _, key := range []string{"quantity", "provider", "model", "thread_id", "agent_id"} {
			legacyAtoms[index].Payload[key] = atom.Payload[key]
		}
		if quantityConflict && index == 0 {
			legacyAtoms[index].Payload["quantity"] = atom.Payload["quantity"].(float64) + 1
		}
		wantTotal += legacyAtoms[index].Payload["quantity"].(float64)
	}
	if _, err := writer.AppendBatch(ctx, AppendBatch{UsageAtoms: legacyAtoms}); err != nil {
		t.Fatal(err)
	}
	seed.Stop()
	var state openCodeCursorState
	if len(receipts[0].checkpoints) != 1 {
		t.Fatal("missing proposed OpenCode checkpoint")
	}
	if err := json.Unmarshal(receipts[0].checkpoints[0].State, &state); err != nil {
		t.Fatal(err)
	}
	state.StatsDatabaseIdentity = ""
	state.RawScan = openCodeRawScanState{}
	legacyCursor, err := json.Marshal(openCodeCursorDisk{Version: 1, State: state})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(cursorPath, legacyCursor, 0o600); err != nil {
		t.Fatal(err)
	}
	mutateSQLite(t, source, `INSERT INTO message VALUES ('msg-upgrade', 'ses-1', 3000, 3000, '{"role":"assistant"}')`)
	mutateSQLite(t, source, `INSERT INTO part VALUES ('part-upgrade', 'msg-upgrade', 'ses-1', 3000, 3000, '{"type":"step-finish","tokens":{"total":5,"input":2,"output":3,"cache":{"read":0}}}')`)
	if changeAttribution {
		liveSession = "renamed-opencode"
	}
	owner := NewAgentTokenCollector(writer, clock, config)
	defer owner.Stop()
	replayed, pending, err := owner.collect(ctx, float64(clock.now.Unix()), 10)
	if err != nil {
		t.Fatal(err)
	}
	accepted, appendErr := owner.appendCollected(ctx, replayed, pending)
	if quantityConflict {
		if accepted || appendErr == nil || !strings.Contains(appendErr.Error(), "usage atom identity conflicts") {
			t.Fatalf("real quantity conflict was accepted: accepted=%v err=%v", accepted, appendErr)
		}
		after, err := loadCollectorOpenCodeState(owner)
		if err != nil || !reflect.DeepEqual(after, state) {
			t.Fatalf("failed append advanced cursor: before=%+v after=%+v err=%v", state, after, err)
		}
	} else {
		if !accepted || appendErr != nil {
			t.Fatalf("legacy metadata replay blocked fresh usage: accepted=%v err=%v", accepted, appendErr)
		}
		wantTotal += 5
		after, err := loadCollectorOpenCodeState(owner)
		if err != nil || after.StatsDatabaseIdentity == "" || after.Parts["ses-1"].RowID <= state.Parts["ses-1"].RowID {
			t.Fatalf("successful upgrade did not advance durable cursor: before=%+v after=%+v err=%v", state, after, err)
		}
		// A second owner must resume the committed upgrade without counting any
		// old or newly appended atom twice.
		owner.Stop()
		restarted := NewAgentTokenCollector(writer, clock, config)
		defer restarted.Stop()
		restarted.RunOnce(ctx)
	}
	reader, err := OpenReadOnly(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer reader.Close()
	atoms, err := reader.ReadUsageAtoms(ctx, 0, 2e9, MaxFactReadLimit)
	if err != nil {
		t.Fatal(err)
	}
	wantCount := len(legacyAtoms)
	if !quantityConflict {
		wantCount += 2
	}
	total := 0.0
	for _, atom := range atoms {
		total += atom.Payload["quantity"].(float64)
		wantAgent := "legacy-opencode"
		if strings.Contains(atom.EventID, ":part-upgrade:") {
			wantAgent = liveSession
		}
		if atom.Payload["agent_id"] != wantAgent {
			t.Fatalf("event %s attribution=%v want %s", atom.EventID, atom.Payload["agent_id"], wantAgent)
		}
	}
	if len(atoms) != wantCount || total != wantTotal {
		t.Fatalf("upgrade changed durable token totals: rows=%d total=%g want rows=%d total=%g", len(atoms), total, wantCount, wantTotal)
	}
}

func TestWriterOpenCodeReplayCompatibilityBoundary(t *testing.T) {
	for _, test := range []struct {
		name   string
		change func(*UsageAtom, *UsageAtom)
		accept bool
	}{
		{"added_metadata", func(_, _ *UsageAtom) {}, true},
		{"changed_live_agent", func(_, replay *UsageAtom) { replay.Payload["agent_id"] = "new-pane" }, true},
		{"missing_live_agent", func(_, replay *UsageAtom) { delete(replay.Payload, "agent_id") }, false},
		{"quantity", func(_, replay *UsageAtom) { replay.Payload["quantity"] = 13.0 }, false},
		{"provider", func(_, replay *UsageAtom) { replay.Payload["provider"] = "different-provider" }, false},
		{"model", func(_, replay *UsageAtom) { replay.Payload["model"] = "different-model" }, false},
		{"thread", func(_, replay *UsageAtom) { replay.Payload["thread_id"] = "different-session" }, false},
		{"timestamp", func(_, replay *UsageAtom) { replay.ObservedAt++ }, false},
		{"non_opencode_id", func(old, replay *UsageAtom) { old.EventID = "codex:session:part:input"; replay.EventID = old.EventID }, false},
		{"wrong_execution_source", func(_, replay *UsageAtom) { replay.Payload["execution_source"] = "codex" }, false},
		{"missing_execution_source", func(_, replay *UsageAtom) { delete(replay.Payload, "execution_source") }, false},
		{"extra_legacy_key", func(old, _ *UsageAtom) { old.Payload["legacy_extra"] = "preserve" }, false},
		{"modern_metadata_conflict", func(old, replay *UsageAtom) {
			for key, value := range replay.Payload {
				old.Payload[key] = value
			}
			replay.Payload["effort"] = "high"
		}, false},
		// Python's storage owner treats these three fields as compatible
		// replay metadata for modern atoms too, preserving their stored values.
		{"modern_agent_change", func(old, replay *UsageAtom) {
			for key, value := range replay.Payload {
				old.Payload[key] = value
			}
			replay.Payload["agent_id"] = "new-pane"
		}, true},
		{"modern_model_evidence_change", func(old, replay *UsageAtom) {
			for key, value := range replay.Payload {
				old.Payload[key] = value
			}
			replay.Payload["model_evidence"] = "message.providerID+message.modelID"
		}, true},
		{"modern_pricing_profile_change", func(old, replay *UsageAtom) {
			for key, value := range replay.Payload {
				old.Payload[key] = value
			}
			replay.Payload["pricing_profile"] = "batch"
		}, true},
		{"modern_compatible_metadata_with_quantity_conflict", func(old, replay *UsageAtom) {
			for key, value := range replay.Payload {
				old.Payload[key] = value
			}
			replay.Payload["agent_id"] = "new-pane"
			replay.Payload["model_evidence"] = "message.providerID+message.modelID"
			replay.Payload["pricing_profile"] = "batch"
			replay.Payload["quantity"] = 13.0
		}, false},
	} {
		t.Run(test.name, func(t *testing.T) {
			ctx := context.Background()
			path := filepath.Join(t.TempDir(), "stats.sqlite3")
			writer, err := OpenWriter(ctx, path)
			if err != nil {
				t.Fatal(err)
			}
			defer writer.Close()
			old := UsageAtom{EventID: "opencode:session:part:input", Direction: "input", Modality: "text", CacheRole: "none", Unit: "tokens", ObservedAt: 100,
				Payload: map[string]any{"quantity": 12.0, "provider": "openai", "model": "gpt", "thread_id": "session", "agent_id": "old-pane"}}
			replay := old
			replay.Payload = map[string]any{}
			for key, value := range old.Payload {
				replay.Payload[key] = value
			}
			for key, value := range map[string]any{"execution_source": "opencode", "model_evidence": "session.model", "effort": "unknown", "pricing_profile": "default", "service_tier": "default", "telemetry_complete": true} {
				replay.Payload[key] = value
			}
			test.change(&old, &replay)
			if _, err := writer.AppendBatch(ctx, AppendBatch{UsageAtoms: []UsageAtom{old}}); err != nil {
				t.Fatal(err)
			}
			before := writer.SourceGeneration()
			fresh := UsageAtom{EventID: "opencode:session:fresh:input", Direction: "input", Modality: "text", CacheRole: "none", Unit: "tokens", ObservedAt: 101,
				Payload: map[string]any{"quantity": 1.0, "provider": "openai", "model": "gpt"}}
			// Put the new row first to prove a later conflicting replay rolls the
			// whole transaction back, not just the conflicting statement.
			result, err := writer.AppendBatch(ctx, AppendBatch{UsageAtoms: []UsageAtom{fresh, replay}})
			if test.accept {
				if err != nil || result.UsageAccepted != 1 || result.UsageDuplicate != 1 {
					t.Fatalf("compatible replay: result=%+v err=%v", result, err)
				}
			} else if err == nil || !strings.Contains(err.Error(), "usage atom identity conflicts") {
				t.Fatalf("incompatible replay accepted: result=%+v err=%v", result, err)
			}
			reader, err := OpenReadOnly(ctx, path)
			if err != nil {
				t.Fatal(err)
			}
			defer reader.Close()
			rows, err := reader.ReadUsageAtoms(ctx, 0, 200, MaxFactReadLimit)
			wantRows := 1
			if test.accept {
				wantRows++
			}
			if err != nil || len(rows) != wantRows {
				t.Fatalf("durable rows=%d want=%d err=%v", len(rows), wantRows, err)
			}
			if !reflect.DeepEqual(rows[0].Payload, old.Payload) || rows[0].ObservedAt != old.ObservedAt {
				t.Fatalf("stored usage changed: got=%+v want=%+v", rows[0], old)
			}
			if !test.accept && writer.SourceGeneration() != before {
				t.Fatal("rejected replay advanced generation")
			}
		})
	}
}

func TestWriterRepairsUnknownUsageModelWithoutChangingFirstAttribution(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "stats.sqlite3")
	writer, err := OpenWriter(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	original := UsageAtom{
		EventID: "codex:thread:message:input", Direction: "input", Modality: "text", CacheRole: "none", Unit: "tokens", ObservedAt: 100,
		Payload: map[string]any{"quantity": 12.0, "provider": "openai", "model": "unknown", "thread_id": "thread", "agent_id": "first-pane", "pricing_profile": "subscription"},
	}
	if _, err := writer.AppendBatch(ctx, AppendBatch{UsageAtoms: []UsageAtom{original}}); err != nil {
		t.Fatal(err)
	}
	replay := original
	replay.Payload = map[string]any{"quantity": 12.0, "provider": "openai", "model": "gpt-5.6", "thread_id": "thread", "agent_id": "renamed-pane", "model_evidence": "message.model", "pricing_profile": "default"}
	beforeRepair := writer.SourceGeneration()
	result, err := writer.AppendBatch(ctx, AppendBatch{UsageAtoms: []UsageAtom{replay}})
	if err != nil || result.UsageAccepted != 1 || result.UsageDuplicate != 0 || writer.SourceGeneration() <= beforeRepair {
		t.Fatalf("unknown-model repair result=%+v generation=%d before=%d err=%v", result, writer.SourceGeneration(), beforeRepair, err)
	}
	readStored := func() UsageAtom {
		t.Helper()
		reader, readErr := OpenReadOnly(ctx, path)
		if readErr != nil {
			t.Fatal(readErr)
		}
		defer reader.Close()
		rows, readErr := reader.ReadUsageAtoms(ctx, 0, 200, MaxFactReadLimit)
		if readErr != nil || len(rows) != 1 {
			t.Fatalf("stored usage rows=%d err=%v", len(rows), readErr)
		}
		return rows[0]
	}
	stored := readStored()
	if stored.Payload["model"] != "gpt-5.6" || stored.Payload["model_evidence"] != "message.model" || stored.Payload["agent_id"] != "first-pane" || stored.Payload["pricing_profile"] != "subscription" || stored.Payload["quantity"] != 12.0 {
		t.Fatalf("repair did not update only model evidence while preserving first attribution: %+v", stored.Payload)
	}
	repairedGeneration := writer.SourceGeneration()
	duplicate, err := writer.AppendBatch(ctx, AppendBatch{UsageAtoms: []UsageAtom{replay}})
	if err != nil || duplicate.UsageAccepted != 0 || duplicate.UsageDuplicate != 1 || writer.SourceGeneration() != repairedGeneration {
		t.Fatalf("replayed repaired usage changed durable state: result=%+v generation=%d want=%d err=%v", duplicate, writer.SourceGeneration(), repairedGeneration, err)
	}
	conflict := replay
	conflict.Payload = cloneUsagePayload(replay.Payload)
	conflict.Payload["quantity"] = 13.0
	if result, err := writer.AppendBatch(ctx, AppendBatch{UsageAtoms: []UsageAtom{conflict}}); err == nil || !strings.Contains(err.Error(), "usage atom identity conflicts") {
		t.Fatalf("quantity conflict crossed repair boundary: result=%+v err=%v", result, err)
	}
	if writer.SourceGeneration() != repairedGeneration || !reflect.DeepEqual(readStored().Payload, stored.Payload) {
		t.Fatal("rejected conflict changed the repaired atom or source generation")
	}
}

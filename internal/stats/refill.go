// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package stats

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"time"
)

const maxRefillAtoms = 250000

type AgentHistoryRefillResult struct {
	Gaps               []string
	PublicationTargets []InvalidationCell
}

// RefillAgentHistory builds a bounded replacement independently of live
// checkpoints. Missing source kinds retain their recorded facts.
func (c *AgentTokenCollector) RefillAgentHistory(ctx context.Context, w *Writer, end time.Time, progress func(string)) (AgentHistoryRefillResult, error) {
	startAt, endAt := float64(end.Add(-24*time.Hour).UnixNano())/1e9, float64(end.UnixNano())/1e9
	baseline, err := w.refillBaseline(ctx, startAt, endAt)
	if err != nil {
		return AgentHistoryRefillResult{}, err
	}
	existing := make(map[string]UsageAtom, len(baseline))
	for _, atom := range baseline {
		existing[refillAtomKey(atom)] = atom
	}
	progress("Reading history")
	available := map[string]bool{}
	atoms := map[string]UsageAtom{}
	baselineSources := map[string]bool{}
	for _, atom := range baseline {
		if kind, ok := atom.Payload["execution_source"].(string); ok {
			baselineSources[kind] = true
		}
	}
	gaps := []string{}
	for _, source := range []struct{ kind, root string }{{"claude", c.config.ClaudeTranscriptRoot}, {"codex", c.config.CodexTranscriptRoot}, {"opencode", c.config.OpenCodeDatabase}} {
		rebuilt, scanErr := c.refillSource(ctx, source.kind, source.root, startAt, endAt)
		if ctx.Err() != nil {
			return AgentHistoryRefillResult{}, ctx.Err()
		}
		if scanErr != nil {
			gaps = append(gaps, source.kind)
			continue
		}
		if len(rebuilt) == 0 && baselineSources[source.kind] {
			gaps = append(gaps, source.kind)
			continue
		}
		available[source.kind] = true
		for _, atom := range rebuilt {
			key := refillAtomKey(atom)
			if previous, ok := existing[key]; ok {
				if agent, ok := previous.Payload["agent_id"]; ok {
					atom.Payload["agent_id"] = agent
				}
			}
			atoms[key] = atom
			if len(atoms) > maxRefillAtoms {
				return AgentHistoryRefillResult{}, errors.New("refill atom bound exceeded")
			}
		}
	}
	if len(available) == 0 {
		return AgentHistoryRefillResult{Gaps: gaps}, errors.New("no agent history available")
	}
	replacement := []UsageAtom{}
	for _, atom := range baseline {
		kind, _ := atom.Payload["execution_source"].(string)
		if available[kind] {
			replacement = append(replacement, atom)
		}
	}
	batch := AppendBatch{}
	keys := make([]string, 0, len(atoms))
	for key := range atoms {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	for _, key := range keys {
		batch.UsageAtoms = append(batch.UsageAtoms, atoms[key])
	}
	c.applyUsagePricingProfiles(&batch)
	progress("Rebuilding charts")
	published, err := w.appendReplacingUsage(ctx, batch, nil, nil, replacement)
	return AgentHistoryRefillResult{Gaps: gaps, PublicationTargets: published.PublicationTargets}, err
}

func refillAtomKey(atom UsageAtom) string {
	return fmt.Sprintf("%s\x00%s\x00%s\x00%s\x00%s", atom.EventID, atom.Direction, atom.Modality, atom.CacheRole, atom.Unit)
}

func (w *Writer) refillBaseline(ctx context.Context, start, end float64) ([]UsageAtom, error) {
	rows, err := w.db.QueryContext(ctx, "SELECT event_id,direction,modality,cache_role,unit,observed_at,payload_json FROM usage_atoms WHERE observed_at>=? AND observed_at<? LIMIT ?", start, end, maxRefillAtoms+1)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	result := []UsageAtom{}
	for rows.Next() {
		var atom UsageAtom
		var payload string
		if err := rows.Scan(&atom.EventID, &atom.Direction, &atom.Modality, &atom.CacheRole, &atom.Unit, &atom.ObservedAt, &payload); err != nil {
			return nil, err
		}
		if err := json.Unmarshal([]byte(payload), &atom.Payload); err != nil {
			return nil, err
		}
		result = append(result, atom)
		if len(result) > maxRefillAtoms {
			return nil, errors.New("refill baseline bound exceeded")
		}
	}
	return result, rows.Err()
}

func (c *AgentTokenCollector) refillSource(ctx context.Context, kind, root string, start, end float64) ([]UsageAtom, error) {
	if root == "" {
		return nil, errors.New("history root unavailable")
	}
	if _, err := os.Stat(root); err != nil {
		return nil, err
	}
	result := map[string]UsageAtom{}
	add := func(atoms []UsageAtom) error {
		for _, atom := range atoms {
			if atom.ObservedAt >= start && atom.ObservedAt < end {
				result[refillAtomKey(atom)] = atom
			}
		}
		if len(result) > maxRefillAtoms {
			return errors.New("refill source bound exceeded")
		}
		return nil
	}
	if kind == "opencode" {
		db, err := OpenOpenCodeReadOnly(ctx, root)
		if err != nil {
			return nil, err
		}
		defer db.Close()
		rows, err := db.reader.Rows(ctx, "SELECT id FROM session ORDER BY id LIMIT ?", maxOpenCodeSelections+1)
		if err != nil {
			return nil, err
		}
		if len(rows) > maxOpenCodeSelections {
			return nil, errors.New("refill session bound exceeded")
		}
		selections := []OpenCodeStepFinishSelection{}
		for _, row := range rows {
			id, ok := openCodeString(row[0])
			if !ok {
				return nil, errors.New("invalid session id")
			}
			selections = append(selections, OpenCodeStepFinishSelection{SessionID: id, RootSessionID: id})
		}
		if len(selections) > 0 {
			high, err := db.ReadPartHighWaterRowID(ctx)
			if err != nil {
				return nil, err
			}
			var after int64
			for after < high {
				page, err := db.ReadStepFinishPartsPage(ctx, selections, after, high, MaxOpenCodePartReadLimit)
				if err != nil {
					return nil, err
				}
				if len(page.RootErrors) > 0 {
					return nil, errors.New("incomplete OpenCode history")
				}
				batch := AppendBatch{}
				cursor := openCodeCursorState{Events: map[string]string{}}
				for _, part := range page.Candidates {
					c.addOpenCodePart(&batch, cursor, part, "opencode:"+part.SessionID, part.SessionID, end, 5)
				}
				if err := add(batch.UsageAtoms); err != nil {
					return nil, err
				}
				if page.ScannedThroughRowID <= after {
					return nil, errors.New("OpenCode scan did not advance")
				}
				after = page.ScannedThroughRowID
			}
		}
	} else {
		discovery := newTranscriptDiscoveryCursorStore()
		paths := map[string]bool{}
		for pass := 0; ; pass++ {
			if pass >= 128 {
				return nil, errors.New("refill discovery bound exceeded")
			}
			candidates, err := recentTranscriptCandidatesWithCursor(ctx, root, transcriptDiscoveryBounds{maxFiles: 256, maxDirs: 512, maxBytes: 64 << 20}, discovery)
			for _, candidate := range candidates {
				paths[candidate.path] = true
			}
			if len(paths) > 4096 {
				return nil, errors.New("refill file bound exceeded")
			}
			if err == nil {
				break
			}
			if err.Error() == "transcript-discovery-byte-budget-exhausted" {
				return nil, err
			}
			if !isTranscriptDiscoveryBudget(err) {
				return nil, err
			}
		}
		var bytesRead int64
		for path := range paths {
			info, err := os.Stat(path)
			if err != nil {
				return nil, err
			}
			// Older files cannot contain activity inside the frozen window. Recent
			// files are read from the beginning to reconstruct cumulative baselines.
			if float64(info.ModTime().UnixNano())/1e9 < start {
				continue
			}
			source := TranscriptSource{Path: path, Provider: kind, SourceID: kind + ":" + path, AgentID: kind + ":" + filepath.Base(path)}
			state := TranscriptScannerState{}
			for {
				scan, err := ScanTranscript(source, &state, TranscriptScanOptions{Context: ctx, MaxBytes: DefaultTranscriptMaxBytes, MaxRecords: 4096})
				if err != nil {
					return nil, err
				}
				scan.Receipt.Commit()
				bytesRead += scan.BytesRead
				if bytesRead > 4<<30 {
					return nil, errors.New("refill read bound exceeded")
				}
				if err := add(scan.Atoms); err != nil {
					return nil, err
				}
				for _, tombstone := range scan.Tombstones {
					delete(result, refillAtomKey(UsageAtom{EventID: tombstone.EventID, Direction: tombstone.Direction, Modality: tombstone.Modality, CacheRole: tombstone.CacheRole, Unit: tombstone.Unit}))
				}
				if state.Offset >= info.Size() {
					break
				}
				if scan.BytesRead == 0 {
					return nil, errors.New("transcript scan did not advance")
				}
			}
		}
	}
	atoms := make([]UsageAtom, 0, len(result))
	for _, atom := range result {
		atoms = append(atoms, atom)
	}
	return atoms, nil
}

// RefillPublicationReady waits for the frozen mutations, rather than an empty
// global queue that live collection is continually extending.
func (w *Writer) RefillPublicationReady(ctx context.Context, targets []InvalidationCell, now time.Time) (bool, error) {
	windows := map[int]RingWindow{}
	for _, target := range targets {
		resolution := target.ResolutionSeconds
		window, loaded := windows[resolution]
		if !loaded {
			var err error
			window, err = w.ReadRingWindowMetadata(ctx, ringCapacities[resolution]*resolution, resolution, NextWindowEnd(now, resolution))
			if err != nil {
				return false, err
			}
			window.Rows = append(window.Rows, window.PendingRows...)
			windows[resolution] = window
		}
		if target.BucketStart < window.WindowStart || target.BucketStart >= window.WindowEnd {
			continue
		}
		published := false
		for _, row := range window.Rows {
			if row.BucketStart == target.BucketStart && row.SourceGeneration >= target.SourceGeneration {
				published = true
				break
			}
		}
		if !published {
			return false, nil
		}
	}
	return true, nil
}

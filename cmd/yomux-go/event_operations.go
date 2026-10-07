// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/keivenchang/yomux/internal/productenv"
)

type goOperation struct {
	ID                   string         `json:"id"`
	State                string         `json:"state"`
	Epoch                string         `json:"epoch"`
	Seq                  int            `json:"seq"`
	RequestID            string         `json:"request_id,omitempty"`
	Route                string         `json:"route,omitempty"`
	Kind                 string         `json:"kind,omitempty"`
	Context              map[string]any `json:"context,omitempty"`
	Progress             map[string]any `json:"progress,omitempty"`
	Receipt              map[string]any `json:"receipt,omitempty"`
	TerminalEvent        map[string]any `json:"terminal_event,omitempty"`
	CreatedAt            int64          `json:"created_at,omitempty"`
	DeadlineAt           int64          `json:"deadline_at,omitempty"`
	TerminalAt           int64          `json:"terminal_at,omitempty"`
	Producer             map[string]any `json:"producer,omitempty"`
	ProducerGeneration   int            `json:"producer_generation,omitempty"`
	Schedule             map[string]any `json:"schedule,omitempty"`
	Result               map[string]any `json:"result,omitempty"`
	HTTPStatus           int            `json:"http_status,omitempty"`
	Acknowledged         bool           `json:"acknowledged,omitempty"`
	DeliveryAcknowledged bool           `json:"delivery_acknowledged,omitempty"`
	EventPublished       bool           `json:"event_published,omitempty"`
	CancelRequested      bool           `json:"cancel_requested,omitempty"`
	UpdatedAt            int64          `json:"updated_at,omitempty"`
}

type operationJournalEntry struct {
	Version      int            `json:"version"`
	Type         string         `json:"type"`
	Record       *goOperation   `json:"record"`
	Operations   []*goOperation `json:"operations"`
	ID           string         `json:"id"`
	Acknowledged bool           `json:"acknowledged"`
	Acks         []operationAck `json:"acks"`
}

type clientEvent struct {
	ID              int            `json:"id"`
	Type            string         `json:"type"`
	Time            float64        `json:"time"`
	Payload         map[string]any `json:"payload"`
	Epoch           string         `json:"epoch"`
	Resource        string         `json:"resource"`
	Base            int            `json:"base_resource_revision"`
	Revision        int            `json:"resource_revision"`
	Replay          bool           `json:"replay,omitempty"`
	RepairResources []string       `json:"repair_resources,omitempty"`
}

type eventJournalEntry struct {
	Version           int            `json:"version"`
	Type              string         `json:"type"`
	Event             *clientEvent   `json:"event"`
	Events            []clientEvent  `json:"events"`
	ResourceRevisions map[string]int `json:"resource_revisions"`
	NextID            int            `json:"next_id"`
}

type eventBroker struct {
	mu                  sync.Mutex
	epoch               string
	nextID              int
	revisions           map[string]int
	retained            map[string]clientEvent
	terminalEvents      map[string]clientEvent
	events              []clientEvent
	subscribers         map[chan clientEvent]struct{}
	channels            map[chan clientEvent]map[string]bool
	channelFilters      map[chan clientEvent]bool
	clientIDs           map[chan clientEvent]string
	clientStats         map[chan clientEvent]*eventSubscriberStats
	pending             map[chan clientEvent]map[string]clientEvent
	repair              map[chan clientEvent]map[string]bool
	journalPath         string
	published           int
	delivered           int
	filtered            int
	coalesced           int
	dropped             int
	replayed            int
	heartbeats          int
	lastHeartbeatAt     float64
	publishedBytes      int
	deliveredBytes      int
	filteredBytes       int
	publishedByType     map[string]eventMetric
	deliveredByType     map[string]eventMetric
	filteredByType      map[string]eventMetric
	publishedByResource map[string]eventMetric
	deliveredByResource map[string]eventMetric
	coalescedByResource map[string]eventMetric
	droppedByResource   map[string]eventMetric
	queueSize           int
}

type eventMetric struct {
	Events int `json:"events"`
	Bytes  int `json:"bytes"`
}

type eventSubscriberStats struct {
	deliveredEvents int
	deliveredBytes  int
	coalescedEvents int
	droppedEvents   int
}

var eventTypeChannels = map[string][]string{
	"activity_summary_ready": {"activity"}, "backend_health_changed": {"core"},
	"attention_acks_changed": {"status", "attention"}, "auto_approve_changed": {"status", "attention"},
	"background_owner_changed": {"core"}, "background_refresh_done": {"core"},
	"background_refresh_requested": {"core"}, "chat_messages_changed": {"chat"},
	"chat_typing_changed": {"chat"}, "context_changed": {"transcripts"},
	"context_items_ready": {"transcripts"}, "event_log_changed": {"events"},
	"files_changed": {"files"}, "fs_changed": {"files"}, "operation_terminal": {"core", "files"},
	"pricing_catalog_changed": {"core"}, "roots_changed": {"files"}, "search_progress": {"core"},
	"session_files_ready": {"files"}, "settings_changed": {"core"}, "stats_sample": {"stats"},
	"transcripts_changed": {"transcripts"}, "tmux_roster_changed": {"core"}, "tmux_signals_changed": {"status", "attention"},
	"update_available": {"core"}, "watched_prs_changed": {"core", "attention"},
	"yoagent_conversation_changed": {"yoagent"}, "yoagent_jobs_changed": {"yoagent", "attention"},
	"yoagent_skills_changed": {"yoagent"}, "yoagent_stream_delta": {"yoagent"},
}

var eventChannels = []string{"activity", "attention", "chat", "core", "events", "files", "status", "stats", "transcripts", "yoagent"}

var retainedEventTypes = map[string]bool{"backend_health_changed": true, "search_progress": true}

func retainedClientEvent(event clientEvent) bool {
	if retainedEventTypes[event.Type] {
		return true
	}
	if event.Type != "background_refresh_done" {
		return false
	}
	role, _ := event.Payload["role"].(string)
	viewID, _ := event.Payload["cache_view_id"].(string)
	return role == "session-files" && len(viewID) == 64 && strings.Trim(viewID, "0123456789abcdef") == ""
}

func newEventBroker() *eventBroker {
	queueSize := eventQueueSize
	if value, err := strconv.Atoi(strings.TrimSpace(productenv.Value("GO_EVENT_QUEUE_SIZE"))); err == nil && value > 0 && value <= eventQueueSize {
		queueSize = value
	}
	return &eventBroker{
		epoch: fmt.Sprintf("go-%d", time.Now().UnixNano()), revisions: map[string]int{}, retained: map[string]clientEvent{}, terminalEvents: map[string]clientEvent{},
		subscribers: map[chan clientEvent]struct{}{}, channels: map[chan clientEvent]map[string]bool{}, channelFilters: map[chan clientEvent]bool{}, clientIDs: map[chan clientEvent]string{},
		clientStats: map[chan clientEvent]*eventSubscriberStats{}, pending: map[chan clientEvent]map[string]clientEvent{}, repair: map[chan clientEvent]map[string]bool{},
		publishedByType: map[string]eventMetric{}, deliveredByType: map[string]eventMetric{}, filteredByType: map[string]eventMetric{},
		publishedByResource: map[string]eventMetric{}, deliveredByResource: map[string]eventMetric{}, coalescedByResource: map[string]eventMetric{}, droppedByResource: map[string]eventMetric{},
		queueSize: queueSize,
	}
}

func incrementEventMetric(metrics map[string]eventMetric, key string, bytes int) {
	metric := metrics[key]
	metric.Events++
	metric.Bytes += bytes
	metrics[key] = metric
}

func eventBytes(event clientEvent) int {
	data, err := json.Marshal(event)
	if err != nil {
		return 0
	}
	return len(data)
}

func validClientEventRecord(event clientEvent) bool {
	return event.ID > 0 && strings.TrimSpace(event.Type) != "" && strings.TrimSpace(event.Resource) != "" && event.Base >= 0 && event.Revision > 0 && event.Revision == event.Base+1
}

func validEventSnapshot(entry eventJournalEntry) bool {
	if entry.Version != eventJournalSnapshotVersion || entry.Type != "snapshot" || entry.NextID < 0 || entry.Events == nil || entry.ResourceRevisions == nil {
		return false
	}
	for resource, revision := range entry.ResourceRevisions {
		if strings.TrimSpace(resource) == "" || revision < 0 {
			return false
		}
	}
	previousID := 0
	for _, event := range entry.Events {
		if !validClientEventRecord(event) || event.ID <= previousID || event.ID > entry.NextID || entry.ResourceRevisions[event.Resource] < event.Revision {
			return false
		}
		previousID = event.ID
	}
	return true
}

func metricSnapshot(metrics map[string]eventMetric) map[string]map[string]int {
	result := make(map[string]map[string]int, len(metrics))
	for key, metric := range metrics {
		result[key] = map[string]int{"events": metric.Events, "bytes": metric.Bytes}
	}
	return result
}

func loadEventBroker(path string) *eventBroker {
	b := newEventBroker()
	b.journalPath = path
	data, err := os.ReadFile(path)
	if err != nil {
		return b
	}
	validBytes := 0
	legacyRecords := false
	for _, chunk := range strings.SplitAfter(string(data), "\n") {
		line := strings.TrimSuffix(chunk, "\n")
		if strings.TrimSpace(line) == "" {
			validBytes += len(chunk)
			continue
		}
		var event clientEvent
		var entry eventJournalEntry
		if json.Unmarshal([]byte(line), &entry) == nil && validEventSnapshot(entry) {
			b.events = nil
			b.retained = map[string]clientEvent{}
			b.terminalEvents = map[string]clientEvent{}
			b.revisions = map[string]int{}
			b.nextID = entry.NextID
			for resource, revision := range entry.ResourceRevisions {
				b.revisions[resource] = revision
			}
			for _, snapshotEvent := range entry.Events {
				if !validClientEventRecord(snapshotEvent) {
					break
				}
				snapshotEvent.Epoch = b.epoch
				b.nextID = max(b.nextID, snapshotEvent.ID)
				b.events = append(b.events, snapshotEvent)
				if snapshotEvent.Type == "operation_terminal" {
					b.terminalEvents[snapshotEvent.Resource] = snapshotEvent
				}
				if retainedClientEvent(snapshotEvent) && (b.retained[snapshotEvent.Resource].ID != 0 || len(b.retained) < retainedEventLimit) {
					b.retained[snapshotEvent.Resource] = snapshotEvent
				}
			}
			validBytes += len(chunk)
			continue
		} else if json.Unmarshal([]byte(line), &entry) == nil && entry.Version == eventJournalVersion && entry.Type == "event" && entry.Event != nil {
			event = *entry.Event
		} else if json.Unmarshal([]byte(line), &event) != nil {
			break
		} else {
			legacyRecords = true
			var legacy struct {
				Revision int `json:"revision"`
			}
			if event.Revision == 0 && json.Unmarshal([]byte(line), &legacy) == nil && legacy.Revision > 0 {
				event.Revision = legacy.Revision
				if event.Base == 0 {
					event.Base = legacy.Revision - 1
				}
			}
		}
		if !validClientEventRecord(event) {
			break
		}
		validBytes += len(chunk)
		// The journal is durable history, not process identity.  Keep the fresh
		// epoch minted by newEventBroker so reconnecting clients can fence events
		// from the previous server instance after a restart.
		event.Epoch = b.epoch
		b.nextID = max(b.nextID, event.ID)
		b.revisions[event.Resource] = max(b.revisions[event.Resource], event.Revision)
		b.events = append(b.events, event)
		if event.Type == "operation_terminal" {
			b.terminalEvents[event.Resource] = event
		}
		if retainedClientEvent(event) && (b.retained[event.Resource].ID != 0 || len(b.retained) < retainedEventLimit) {
			b.retained[event.Resource] = event
		}
	}
	if len(b.events) > 256 {
		b.events = b.events[len(b.events)-256:]
	}
	if validBytes < len(data) {
		_ = truncateDurable(path, data[:validBytes])
	}
	if legacyRecords {
		if err := rewriteEventJournal(path, b.events); err != nil {
			slog.Warn("event journal legacy migration failed", "path", path, "error", err)
		}
	}
	return b
}

func rewriteEventJournal(path string, events []clientEvent) error {
	return withFileLock(path, func() error {
		var output bytes.Buffer
		for _, event := range events {
			event.Replay = false
			data, err := json.Marshal(map[string]any{"version": eventJournalVersion, "type": "event", "epoch": event.Epoch, "event": event})
			if err != nil {
				return err
			}
			output.Write(data)
			output.WriteByte('\n')
		}
		return writeAtomicFile(path, output.Bytes())
	})
}

func (b *eventBroker) appendDurable(event clientEvent) error {
	if b.journalPath == "" {
		return nil
	}
	data, err := json.Marshal(map[string]any{"version": eventJournalVersion, "type": "event", "epoch": event.Epoch, "event": event})
	if err != nil {
		return err
	}
	return withFileLock(b.journalPath, func() error {
		if err := os.MkdirAll(filepath.Dir(b.journalPath), 0o700); err != nil {
			return err
		}
		f, err := os.OpenFile(b.journalPath, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0600)
		if err != nil {
			return err
		}
		if _, err = f.Write(append(data, '\n')); err == nil {
			err = f.Sync()
		}
		closeErr := f.Close()
		if err == nil {
			err = closeErr
		}
		return err
	})
}

func compactEventJournal(path string, events []clientEvent, revisions map[string]int, nextID int) error {
	if path == "" {
		return nil
	}
	return withFileLock(path, func() error {
		data, err := json.Marshal(map[string]any{"version": eventJournalSnapshotVersion, "type": "snapshot", "next_id": nextID, "resource_revisions": revisions, "events": events})
		if err != nil {
			return err
		}
		return writeAtomicFile(path, append(data, '\n'))
	})
}

func withFileLock(path string, action func() error) error {
	lock, err := os.OpenFile(path+".lock", os.O_CREATE|os.O_RDWR, 0600)
	if err != nil {
		return err
	}
	defer lock.Close()
	if err := syscall.Flock(int(lock.Fd()), syscall.LOCK_EX); err != nil {
		return err
	}
	defer syscall.Flock(int(lock.Fd()), syscall.LOCK_UN)
	return action()
}

func (b *eventBroker) readySnapshot() map[string]int {
	b.mu.Lock()
	defer b.mu.Unlock()
	result := map[string]int{}
	for resource, revision := range b.revisions {
		result[resource] = revision
	}
	return result
}

func (b *eventBroker) hasResource(resource string) bool {
	b.mu.Lock()
	defer b.mu.Unlock()
	for _, event := range b.events {
		if event.Resource == resource {
			return true
		}
	}
	return false
}

func (b *eventBroker) readySnapshotFor(ch chan clientEvent) map[string]int {
	b.mu.Lock()
	defer b.mu.Unlock()
	selected := b.channels[ch]
	result := map[string]int{}
	for resource, revision := range b.revisions {
		if !b.channelFilters[ch] || eventMatchesChannels(strings.SplitN(resource, ":", 2)[0], mapKeys(selected)) {
			result[resource] = revision
		}
	}
	return result
}

func loadOperations(path string) map[string]*goOperation {
	data, err := os.ReadFile(path)
	if err != nil {
		return map[string]*goOperation{}
	}
	var records map[string]*goOperation
	if json.Unmarshal(data, &records) != nil || records == nil {
		return map[string]*goOperation{}
	}
	return records
}
func loadOperationJournal(path string, records map[string]*goOperation) map[string]*goOperation {
	if records == nil {
		records = map[string]*goOperation{}
	}
	if path == "" {
		retainOperationRecords(records)
		return records
	}
	result := records
	if err := withOperationJournalLock(path, func() error {
		result = loadOperationJournalUnlocked(path, records, true)
		if retainOperationRecords(result) {
			return compactOperationJournalUnlocked(path, result)
		}
		return nil
	}); err != nil {
		slog.Error("operation journal recovery failed", "error", err)
	}
	return result
}

func loadOperationJournalUnlocked(path string, records map[string]*goOperation, repairTail bool) map[string]*goOperation {
	data, err := os.ReadFile(path)
	if err != nil {
		return records
	}
	validBytes := 0
	for _, chunk := range strings.SplitAfter(string(data), "\n") {
		line := strings.TrimSuffix(chunk, "\n")
		if strings.TrimSpace(line) == "" {
			validBytes += len(chunk)
			continue
		}
		var entry operationJournalEntry
		if json.Unmarshal([]byte(line), &entry) != nil || !validOperationJournalEntry(entry) {
			break
		}
		validBytes += len(chunk)
		if entry.Version == operationJournalVersion && entry.Type == "snapshot" {
			records = map[string]*goOperation{}
			for _, record := range entry.Operations {
				if record != nil && record.ID != "" {
					records[record.ID] = record
				}
			}
		}
		if entry.Version == operationJournalVersion && entry.Type == "operations" {
			for _, record := range entry.Operations {
				if record != nil && record.ID != "" {
					records[record.ID] = record
				}
			}
		}
		if (entry.Version == 0 || entry.Version == 1 || entry.Version == operationJournalVersion) && entry.Type == "operation" && entry.Record != nil {
			records[entry.Record.ID] = entry.Record
		}
		if (entry.Version == 0 || entry.Version == 1) && entry.Type == "ack" && records[entry.ID] != nil {
			records[entry.ID].Acknowledged = entry.Acknowledged
			records[entry.ID].DeliveryAcknowledged = entry.Acknowledged
		}
		if entry.Version == operationAckJournalVersion && entry.Type == "ack" {
			for _, ack := range entry.Acks {
				record := records[ack.ID]
				epoch, seq, validCursor := operationAckCursor(ack)
				if record != nil && validCursor && operationCursorMatches(record, epoch, seq) {
					record.Acknowledged = true
					record.DeliveryAcknowledged = true
				}
			}
		}
	}
	if repairTail && validBytes < len(data) {
		_ = truncateDurableUnlocked(path, data[:validBytes])
	}
	return records
}

type operationAck struct {
	ID     string         `json:"id"`
	Epoch  string         `json:"-"`
	Seq    int            `json:"-"`
	Cursor map[string]any `json:"cursor"`
}

func validOperationRecord(record *goOperation) bool {
	return record != nil && strings.TrimSpace(record.ID) != "" && len(record.ID) <= 128
}

func validOperationAckRecord(ack operationAck) bool {
	if strings.TrimSpace(ack.ID) == "" || len(ack.ID) > 128 || len(ack.Cursor) != 2 {
		return false
	}
	_, _, ok := operationAckCursor(ack)
	return ok
}

func validOperationJournalEntry(entry operationJournalEntry) bool {
	switch {
	case (entry.Version == 0 || entry.Version == 1) && entry.Type == "operation":
		return validOperationRecord(entry.Record)
	case (entry.Version == 0 || entry.Version == 1) && entry.Type == "ack":
		return strings.TrimSpace(entry.ID) != "" && len(entry.ID) <= 128
	case entry.Version == operationJournalVersion && entry.Type == "operation":
		return validOperationRecord(entry.Record)
	case entry.Version == operationJournalVersion && (entry.Type == "snapshot" || entry.Type == "operations"):
		if entry.Operations == nil {
			return false
		}
		for _, record := range entry.Operations {
			if !validOperationRecord(record) {
				return false
			}
		}
		return true
	case entry.Version == operationAckJournalVersion && entry.Type == "ack":
		if entry.Acks == nil {
			return false
		}
		for _, ack := range entry.Acks {
			if !validOperationAckRecord(ack) {
				return false
			}
		}
		return true
	default:
		return false
	}
}

func operationAckCursor(ack operationAck) (string, int, bool) {
	epoch, _ := ack.Cursor["epoch"].(string)
	seq, ok := ack.Cursor["seq"].(int)
	if !ok {
		if value, floatOK := ack.Cursor["seq"].(float64); floatOK && value == float64(int(value)) {
			seq, ok = int(value), true
		}
	}
	if epoch == "" || !ok {
		return "", 0, false
	}
	return epoch, seq, true
}

func appendOperationJournal(path string, entry any) error {
	if path == "" {
		return nil
	}
	return withOperationJournalLock(path, func() error {
		data, err := json.Marshal(entry)
		if err != nil {
			return err
		}
		f, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0600)
		if err != nil {
			return err
		}
		_, err = f.Write(append(data, '\n'))
		if err == nil {
			err = f.Sync()
		}
		closeErr := f.Close()
		if err == nil {
			err = closeErr
		}
		return err
	})
}

func withOperationJournalLock(path string, action func() error) error {
	return withFileLock(path, action)
}

func compactOperationJournal(path string, records map[string]*goOperation) error {
	if path == "" {
		return nil
	}
	return withOperationJournalLock(path, func() error {
		// Re-read the journal while holding the same lock as append. A compactor that
		// snapshots only its caller's in-memory map can erase a record appended by a
		// different process that won the file lock immediately before compaction.
		merged := loadOperationJournalUnlocked(path, cloneOperationMap(records), false)
		// A snapshot replaces the seed map. Apply the same replay/retention policy
		// after merging so old snapshots cannot resurrect pruned or evicted data.
		retainOperationRecords(merged)
		return compactOperationJournalUnlocked(path, merged)
	})
}

func compactOperationJournalUnlocked(path string, records map[string]*goOperation) error {
	ids := make([]string, 0, len(records))
	for id := range records {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	operations := make([]*goOperation, 0, len(ids))
	for _, id := range ids {
		operations = append(operations, records[id])
	}
	epoch := ""
	if len(operations) > 0 {
		epoch = operations[0].Epoch
	}
	data, marshalErr := json.Marshal(map[string]any{"version": operationJournalVersion, "type": "snapshot", "epoch": epoch, "operations": operations})
	if marshalErr != nil {
		return marshalErr
	}
	return writeAtomicFile(path, append(data, '\n'))
}

func syncDirectory(path string) error {
	directory, err := os.Open(path)
	if err != nil {
		return err
	}
	defer directory.Close()
	return directory.Sync()
}

func truncateDurable(path string, data []byte) error {
	return withFileLock(path, func() error {
		return truncateDurableUnlocked(path, data)
	})
}

func truncateDurableUnlocked(path string, data []byte) error {
	file, err := os.OpenFile(path, os.O_WRONLY|os.O_TRUNC, 0600)
	if err != nil {
		return err
	}
	if _, err := file.Write(data); err != nil {
		_ = file.Close()
		return err
	}
	if err := file.Sync(); err != nil {
		_ = file.Close()
		return err
	}
	if err := file.Close(); err != nil {
		return err
	}
	return syncDirectory(filepath.Dir(path))
}
func (s *server) saveOperationsLocked() error {
	if s.operationsPath == "" {
		return nil
	}
	data, err := json.Marshal(s.operations)
	if err != nil {
		return err
	}
	return writeAtomicFile(s.operationsPath, data)
}

func cloneOperation(record *goOperation) *goOperation {
	if record == nil {
		return nil
	}
	data, err := json.Marshal(record)
	if err != nil {
		return nil
	}
	var result goOperation
	if json.Unmarshal(data, &result) != nil {
		return nil
	}
	return &result
}

func cloneOperationMap(records map[string]*goOperation) map[string]*goOperation {
	result := make(map[string]*goOperation, len(records))
	for id, record := range records {
		if copy := cloneOperation(record); copy != nil {
			result[id] = copy
		}
	}
	return result
}

func (s *server) pruneOperationsLocked() {
	pruneOperationRecords(s.operations)
}

func pruneOperationRecords(records map[string]*goOperation) {
	now := time.Now().UnixMilli()
	for id, record := range records {
		if record == nil || record.State == "queued" {
			continue
		}
		at := record.TerminalAt
		if at == 0 {
			at = record.CreatedAt
		}
		if at > 0 && now-at > operationRetention.Milliseconds() {
			delete(records, id)
		}
	}
	if len(records) <= operationRecordLimit {
		return
	}
	ids := make([]string, 0, len(records))
	for id, record := range records {
		if record != nil && record.State != "queued" {
			ids = append(ids, id)
		}
	}
	sort.Slice(ids, func(i, j int) bool {
		left, right := records[ids[i]], records[ids[j]]
		leftAt, rightAt := left.TerminalAt, right.TerminalAt
		if leftAt == 0 {
			leftAt = left.CreatedAt
		}
		if rightAt == 0 {
			rightAt = right.CreatedAt
		}
		if leftAt == rightAt {
			return ids[i] < ids[j]
		}
		return leftAt < rightAt
	})
	for _, id := range ids[:min(len(ids), len(records)-operationRecordLimit)] {
		delete(records, id)
	}
}

func boundedOperationTerminal(record *goOperation) map[string]any {
	return map[string]any{
		"operation": map[string]any{"id": record.ID, "cursor": map[string]any{"epoch": record.Epoch, "seq": record.Seq}},
		"result": map[string]any{
			"state": "failed", "request": map[string]any{"id": record.RequestID},
			"error": map[string]any{
				"code":    "operation_replay_evicted",
				"message": map[string]any{"key": "common.operationReplayEvicted", "params": map[string]any{"operation_id": record.ID}, "fallback": "The completed operation is no longer available for replay."},
				"origin":  "server.operation_ledger", "retryable": false, "details": map[string]any{"operation_id": record.ID},
				"stack": []any{map[string]any{"component": "server.operation_ledger", "operation": "GET /api/operations/{id}", "code": "operation_replay_evicted"}},
			},
		},
		"status": http.StatusGone,
	}
}

func boundOperationReplays(records map[string]*goOperation) bool {
	exposed := make([]*goOperation, 0)
	totalBytes := 0
	for _, record := range records {
		if record != nil && record.DeliveryAcknowledged && record.TerminalEvent != nil {
			exposed = append(exposed, record)
			totalBytes += eventBytes(clientEvent{Payload: record.TerminalEvent})
		}
	}
	sort.Slice(exposed, func(i, j int) bool {
		left, right := exposed[i].TerminalAt, exposed[j].TerminalAt
		if left == right {
			return exposed[i].ID < exposed[j].ID
		}
		return left < right
	})
	changed := false
	for _, record := range exposed {
		bytes := eventBytes(clientEvent{Payload: record.TerminalEvent})
		if bytes <= operationReplayRecordBytes && totalBytes <= operationReplayTotalBytes {
			continue
		}
		bounded := boundedOperationTerminal(record)
		boundedBytes := eventBytes(clientEvent{Payload: bounded})
		record.TerminalEvent = bounded
		if result, ok := bounded["result"].(map[string]any); ok {
			record.Result = result
		}
		record.HTTPStatus = http.StatusGone
		totalBytes += boundedBytes - bytes
		changed = true
	}
	return changed
}

func retainOperationRecords(records map[string]*goOperation) bool {
	count := len(records)
	pruneOperationRecords(records)
	bounded := boundOperationReplays(records)
	return bounded || len(records) != count
}
func (s *server) operationRecord(id string) *goOperation {
	s.operationsMu.Lock()
	defer s.operationsMu.Unlock()
	if record := s.operations[id]; record != nil {
		return cloneOperation(record)
	}
	return nil
}

func operationIsTerminal(record *goOperation) bool {
	return record != nil && (record.TerminalEvent != nil || record.State == "terminal" || record.State == "cancelled")
}
func (s *server) acceptOperation(id string) error {
	return s.acceptOperationWithMetadata(id, "", "", "")
}

func (s *server) acceptOperationWithMetadata(id, requestID, route, kind string) error {
	return s.acceptOperationWithDetails(id, requestID, route, kind, 0, nil, nil, nil)
}

func (s *server) acceptOperationWithDetails(id, requestID, route, kind string, deadlineAt int64, progress, producer, context map[string]any) error {
	if id == "" {
		return fmt.Errorf("missing operation id")
	}
	if s.events == nil {
		return fmt.Errorf("event broker unavailable")
	}
	s.operationsMu.Lock()
	defer s.operationsMu.Unlock()
	if s.operations == nil {
		s.operations = map[string]*goOperation{}
	}
	if s.operations[id] != nil {
		return fmt.Errorf("operation already exists: %s", id)
	}
	now := time.Now().UnixMilli()
	if requestID == "" {
		requestID = "r-" + strconv.FormatInt(now, 10) + "-" + id
	}
	if deadlineAt <= now {
		deadlineAt = time.Now().Add(10 * time.Minute).UnixMilli()
	}
	if progress == nil {
		progress = map[string]any{}
	}
	if producer == nil {
		producer = map[string]any{}
	}
	if context == nil {
		context = map[string]any{}
	}
	deadlineText := time.UnixMilli(deadlineAt).UTC().Format(time.RFC3339Nano)
	operation := map[string]any{"id": id, "deadline_at": deadlineText, "cursor": map[string]any{"epoch": s.events.epoch, "seq": 0}, "status_url": "/api/operations/" + url.PathEscape(id), "events_url": "/api/client-events?operation_id=" + url.QueryEscape(id), "progress": cloneMap(progress)}
	if kind != "" {
		operation["kind"] = kind
	}
	if len(context) > 0 {
		operation["context"] = cloneMap(context)
	}
	receipt := map[string]any{"state": "queued", "request": map[string]any{"id": requestID}, "operation": operation}
	record := &goOperation{ID: id, State: "queued", Epoch: s.events.epoch, RequestID: requestID, Route: route, Kind: kind, Context: cloneMap(context), Progress: cloneMap(progress), Producer: cloneMap(producer), Receipt: receipt, DeadlineAt: deadlineAt, CreatedAt: now, UpdatedAt: now}
	if err := appendOperationJournal(s.operationsJournal, map[string]any{"version": operationJournalVersion, "type": "operation", "epoch": record.Epoch, "record": record}); err != nil {
		return err
	}
	s.operations[id] = record
	s.pruneOperationsLocked()
	return s.saveOperationsLocked()
}

func (s *server) updateOperationProducers(ids []string, producer map[string]any, generation int) ([]string, error) {
	s.operationsMu.Lock()
	defer s.operationsMu.Unlock()
	updated := make([]*goOperation, 0, len(ids))
	for _, id := range ids {
		record := s.operations[id]
		if record == nil || record.State != "queued" || generation < record.ProducerGeneration {
			continue
		}
		copy := *record
		copy.Producer = cloneMap(producer)
		copy.ProducerGeneration = generation
		copy.UpdatedAt = time.Now().UnixMilli()
		if chain, ok := producer["chain"].([]any); ok && len(chain) > 0 {
			if current, ok := chain[len(chain)-1].(map[string]any); ok {
				copy.Progress = cloneMap(record.Progress)
				copy.Progress["producer_stage"] = fmt.Sprint(current["stage"])
				copy.Progress["producer_state"] = fmt.Sprint(current["state"])
			}
		}
		copy.Receipt = cloneMap(record.Receipt)
		if operation, ok := record.Receipt["operation"].(map[string]any); ok {
			operationCopy := cloneMap(operation)
			operationCopy["progress"] = cloneMap(copy.Progress)
			copy.Receipt["operation"] = operationCopy
		}
		updated = append(updated, &copy)
	}
	if len(updated) == 0 {
		return nil, nil
	}
	if err := appendOperationJournal(s.operationsJournal, map[string]any{"version": operationJournalVersion, "type": "operations", "epoch": s.events.epoch, "operations": updated}); err != nil {
		return nil, err
	}
	for _, record := range updated {
		s.operations[record.ID] = record
	}
	if err := s.saveOperationsLocked(); err != nil {
		return nil, err
	}
	result := make([]string, 0, len(updated))
	for _, record := range updated {
		result = append(result, record.ID)
	}
	return result, nil
}

func (s *server) updateOperationProducer(id string, producer map[string]any, generation int) (bool, error) {
	updated, err := s.updateOperationProducers([]string{id}, producer, generation)
	return len(updated) == 1, err
}

func (s *server) recordOperationSchedule(id string, schedule map[string]any) bool {
	allowed := map[string]bool{"task": true, "priority": true, "lane": true, "submitted_at": true, "running_started_at": true, "completed_at": true, "queue_wait_ms": true, "execution_ms": true, "transient_polls": true}
	bounded := map[string]any{}
	for key, value := range schedule {
		if allowed[key] {
			if number, ok := value.(float64); ok {
				bounded[key] = number
			} else {
				bounded[key] = fmt.Sprint(value)[:min(len(fmt.Sprint(value)), 64)]
			}
		}
	}
	if len(bounded) == 0 {
		return false
	}
	s.operationsMu.Lock()
	defer s.operationsMu.Unlock()
	record := s.operations[id]
	if record == nil {
		return false
	}
	updated := *record
	updated.Schedule = bounded
	updated.UpdatedAt = time.Now().UnixMilli()
	if err := appendOperationJournal(s.operationsJournal, map[string]any{"version": operationJournalVersion, "type": "operation", "epoch": updated.Epoch, "record": &updated}); err != nil {
		return false
	}
	*record = updated
	if err := s.saveOperationsLocked(); err != nil {
		return false
	}
	return true
}
func (s *server) terminalizeOperation(id string, result map[string]any, status int) (clientEvent, error) {
	s.operationsMu.Lock()
	defer s.operationsMu.Unlock()
	record := s.operations[id]
	if record == nil {
		return clientEvent{}, fmt.Errorf("operation not found: %s", id)
	}
	if operationIsTerminal(record) {
		if record.TerminalEvent != nil && !record.EventPublished {
			event, err := s.events.publish("operation_terminal", record.TerminalEvent)
			if err != nil {
				return clientEvent{}, err
			}
			record.EventPublished = true
			if err := appendOperationJournal(s.operationsJournal, map[string]any{"version": operationJournalVersion, "type": "operation", "epoch": record.Epoch, "record": record}); err != nil {
				return event, err
			}
			if err := s.saveOperationsLocked(); err != nil {
				return event, err
			}
			return event, nil
		}
		return clientEvent{}, fmt.Errorf("operation already terminal: %s", id)
	}
	updated := *record
	updated.State = "failed"
	if state, ok := result["state"].(string); ok && state != "" {
		updated.State = state
	}
	updated.Result = result
	updated.HTTPStatus = status
	updated.Seq++
	updated.TerminalAt = time.Now().UnixMilli()
	updated.UpdatedAt = time.Now().UnixMilli()
	payload := map[string]any{"operation": map[string]any{"id": id, "cursor": map[string]any{"epoch": updated.Epoch, "seq": updated.Seq}}, "result": result, "status": status}
	updated.TerminalEvent = payload
	if err := appendOperationJournal(s.operationsJournal, map[string]any{"version": operationJournalVersion, "type": "operation", "epoch": updated.Epoch, "record": &updated}); err != nil {
		return clientEvent{}, err
	}
	*record = updated
	s.pruneOperationsLocked()
	if err := s.saveOperationsLocked(); err != nil {
		return clientEvent{}, err
	}
	event, err := s.events.publish("operation_terminal", payload)
	if err != nil {
		return clientEvent{}, err
	}
	record.EventPublished = true
	if err := appendOperationJournal(s.operationsJournal, map[string]any{"version": operationJournalVersion, "type": "operation", "epoch": record.Epoch, "record": record}); err != nil {
		return event, err
	}
	if err := s.saveOperationsLocked(); err != nil {
		return event, err
	}
	return event, err
}
func (s *server) cancelOperation(id string) error {
	s.operationsMu.Lock()
	defer s.operationsMu.Unlock()
	record := s.operations[id]
	if record == nil {
		return fmt.Errorf("operation not found: %s", id)
	}
	if operationIsTerminal(record) {
		if record.State == "cancelled" && record.TerminalEvent != nil && !record.EventPublished {
			if _, err := s.events.publish("operation_terminal", record.TerminalEvent); err != nil {
				return err
			}
			record.EventPublished = true
			if err := appendOperationJournal(s.operationsJournal, map[string]any{"version": operationJournalVersion, "type": "operation", "epoch": record.Epoch, "record": record}); err != nil {
				return err
			}
			return s.saveOperationsLocked()
		}
		return fmt.Errorf("operation already terminal: %s", id)
	}
	updated := *record
	updated.State = "cancelled"
	updated.CancelRequested = true
	updated.Seq++
	updated.TerminalAt = time.Now().UnixMilli()
	updated.UpdatedAt = time.Now().UnixMilli()
	updated.Result = map[string]any{"state": "cancelled", "request": map[string]any{"id": updated.RequestID}}
	updated.HTTPStatus = http.StatusOK
	updated.TerminalEvent = map[string]any{"operation": map[string]any{"id": id, "cursor": map[string]any{"epoch": updated.Epoch, "seq": updated.Seq}}, "result": updated.Result, "status": updated.HTTPStatus}
	if err := appendOperationJournal(s.operationsJournal, map[string]any{"version": operationJournalVersion, "type": "operation", "epoch": updated.Epoch, "record": &updated}); err != nil {
		return err
	}
	*record = updated
	s.pruneOperationsLocked()
	if err := s.saveOperationsLocked(); err != nil {
		return err
	}
	if _, err := s.events.publish("operation_terminal", updated.TerminalEvent); err != nil {
		return err
	}
	record.EventPublished = true
	if err := appendOperationJournal(s.operationsJournal, map[string]any{"version": operationJournalVersion, "type": "operation", "epoch": record.Epoch, "record": record}); err != nil {
		return err
	}
	return s.saveOperationsLocked()
}
func (b *eventBroker) publish(kind string, payload map[string]any) (clientEvent, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	if payload == nil {
		payload = map[string]any{}
	}
	resource := eventResource(kind, payload)
	if kind == "operation_terminal" {
		if existing, ok := b.terminalEvents[resource]; ok {
			return existing, nil
		}
		for index := len(b.events) - 1; index >= 0; index-- {
			if b.events[index].Resource == resource {
				b.terminalEvents[resource] = b.events[index]
				return b.events[index], nil
			}
		}
	}
	base := b.revisions[resource]
	b.revisions[resource] = base + 1
	b.nextID++
	e := clientEvent{ID: b.nextID, Type: kind, Time: float64(time.Now().UnixNano()) / 1e9, Payload: payload, Epoch: b.epoch, Resource: resource, Base: base, Revision: base + 1}
	if err := b.appendDurable(e); err != nil {
		b.nextID--
		b.revisions[resource] = base
		return clientEvent{}, err
	}
	b.published++
	bytes := eventBytes(e)
	b.publishedBytes += bytes
	incrementEventMetric(b.publishedByType, kind, bytes)
	incrementEventMetric(b.publishedByResource, resource, bytes)
	b.events = append(b.events, e)
	if kind == "operation_terminal" {
		b.terminalEvents[resource] = e
	}
	if len(b.events) > eventQueueHistoryLimit {
		b.events = b.events[len(b.events)-eventQueueHistoryLimit:]
	}
	if b.journalPath != "" && b.nextID%eventJournalCompactEvery == 0 {
		if err := compactEventJournal(b.journalPath, b.events, b.revisions, b.nextID); err != nil {
			slog.Warn("event journal compaction failed", "path", b.journalPath, "error", err)
		}
	}
	if retainedClientEvent(e) && (b.retained[resource].ID != 0 || len(b.retained) < retainedEventLimit) {
		b.retained[resource] = e
	}
	for ch := range b.subscribers {
		if selected := b.channels[ch]; b.channelFilters[ch] && !eventMatchesChannels(kind, mapKeys(selected)) {
			b.filtered++
			b.filteredBytes += bytes
			incrementEventMetric(b.filteredByType, kind, bytes)
			continue
		}
		if pending := b.pending[ch]; pending != nil {
			if _, ok := pending[resource]; ok {
				prior := pending[resource]
				priorPatch, _ := prior.Payload["patch"].(bool)
				currentPatch, _ := payload["patch"].(bool)
				if priorPatch || currentPatch {
					if b.repair[ch] == nil {
						b.repair[ch] = map[string]bool{}
					}
					b.repair[ch][resource] = true
				}
				pending[resource] = e
				b.replaceQueuedResource(ch, resource, e)
				b.coalesced++
				incrementEventMetric(b.coalescedByResource, resource, bytes)
				if stats := b.clientStats[ch]; stats != nil {
					stats.coalescedEvents++
				}
				continue
			}
		}
		select {
		case ch <- b.withRepair(ch, e):
			b.pending[ch][resource] = e
			b.delivered++
			b.deliveredBytes += bytes
			incrementEventMetric(b.deliveredByType, kind, bytes)
			incrementEventMetric(b.deliveredByResource, resource, bytes)
			if stats := b.clientStats[ch]; stats != nil {
				stats.deliveredEvents++
				stats.deliveredBytes += bytes
			}
		default:
			select {
			case dropped := <-ch:
				delete(b.pending[ch], dropped.Resource)
				if dropped.Resource != "" {
					if b.repair[ch] == nil {
						b.repair[ch] = map[string]bool{}
					}
					b.repair[ch][dropped.Resource] = true
				}
				droppedBytes := eventBytes(dropped)
				droppedResource := dropped.Resource
				if droppedResource == "" {
					droppedResource = "event"
				}
				incrementEventMetric(b.droppedByResource, droppedResource, droppedBytes)
				if stats := b.clientStats[ch]; stats != nil {
					stats.droppedEvents++
				}
				b.dropped++
			default:
			}
			select {
			case ch <- b.withRepair(ch, e):
				b.pending[ch][resource] = e
				b.delivered++
				b.deliveredBytes += bytes
				incrementEventMetric(b.deliveredByType, kind, bytes)
				incrementEventMetric(b.deliveredByResource, resource, bytes)
				if stats := b.clientStats[ch]; stats != nil {
					stats.deliveredEvents++
					stats.deliveredBytes += bytes
				}
			default:
				droppedResource := resource
				if droppedResource == "" {
					droppedResource = "event"
				}
				incrementEventMetric(b.droppedByResource, droppedResource, bytes)
				if stats := b.clientStats[ch]; stats != nil {
					stats.droppedEvents++
				}
				b.dropped++
			}
		}
	}
	return e, nil
}
func (b *eventBroker) replaceQueuedResource(ch chan clientEvent, resource string, replacement clientEvent) {
	items := make([]clientEvent, 0, len(ch))
	for {
		select {
		case item := <-ch:
			if item.Resource != resource {
				items = append(items, item)
			}
		default:
			goto drained
		}
	}
drained:
	for _, item := range items {
		select {
		case ch <- item:
		default:
			return
		}
	}
	select {
	case ch <- replacement:
	default:
	}
}
func (b *eventBroker) withRepair(ch chan clientEvent, event clientEvent) clientEvent {
	if values := b.repair[ch]; len(values) > 0 {
		for resource := range values {
			event.RepairResources = append(event.RepairResources, resource)
		}
		sort.Strings(event.RepairResources)
	}
	return event
}
func eventResource(kind string, payload map[string]any) string {
	if kind == "background_refresh_done" {
		role, _ := payload["role"].(string)
		if role == "" {
			role = "unknown"
		}
		if role == "session-files" {
			if viewID, ok := payload["cache_view_id"].(string); ok && len(viewID) == 64 && strings.Trim(viewID, "0123456789abcdef") == "" {
				return "background:" + role + ":" + viewID
			}
		}
		scope, _ := payload["session"].(string)
		if scope == "" {
			scope, _ = payload["root"].(string)
		}
		if scope != "" {
			digest := sha256.Sum256([]byte(scope))
			return "background:" + role + ":" + hex.EncodeToString(digest[:])[:16]
		}
		return "background:" + role
	}
	if kind == "operation_terminal" {
		if op, ok := payload["operation"].(map[string]any); ok {
			if id, ok := op["id"].(string); ok && id != "" {
				return "operation_terminal:" + id
			}
		}
	}
	if kind == "search_progress" {
		if id, ok := payload["scope_id"].(string); ok && id != "" {
			return "search_progress:" + id
		}
	}
	for _, scoped := range []string{"event_log_changed", "session_files_ready", "context_items_ready", "yoagent_conversation_changed", "yoagent_jobs_changed", "yoagent_stream_delta"} {
		if kind != scoped {
			continue
		}
		if session, ok := payload["session"].(string); ok && session != "" {
			return kind + ":" + session
		}
		if request, ok := payload["request"].(map[string]any); ok {
			if session, ok := request["session"].(string); ok && session != "" {
				return kind + ":" + session
			}
		}
	}
	return kind
}

func operationDemandIDs(operationID, operations string) []string {
	seen := map[string]bool{}
	ids := []string{}
	add := func(id string) {
		if id != "" && !seen[id] {
			seen[id] = true
			ids = append(ids, id)
		}
	}
	add(strings.TrimSpace(operationID))
	for _, id := range strings.Split(operations, ",") {
		id = strings.TrimSpace(id)
		if id == "" || len(id) > 128 {
			continue
		}
		add(id)
	}
	return ids
}

func (b *eventBroker) subscribe(requested ...string) (chan clientEvent, func()) {
	return b.subscribeClient("", requested...)
}

func (b *eventBroker) subscribeClient(clientID string, requested ...string) (chan clientEvent, func()) {
	queueSize := b.queueSize
	if queueSize <= 0 {
		queueSize = eventQueueSize
	}
	ch := make(chan clientEvent, queueSize)
	b.mu.Lock()
	if b.subscribers == nil {
		b.subscribers = map[chan clientEvent]struct{}{}
	}
	if b.channels == nil {
		b.channels = map[chan clientEvent]map[string]bool{}
	}
	if b.channelFilters == nil {
		b.channelFilters = map[chan clientEvent]bool{}
	}
	if b.clientIDs == nil {
		b.clientIDs = map[chan clientEvent]string{}
	}
	if b.clientStats == nil {
		b.clientStats = map[chan clientEvent]*eventSubscriberStats{}
	}
	if b.pending == nil {
		b.pending = map[chan clientEvent]map[string]clientEvent{}
	}
	if b.repair == nil {
		b.repair = map[chan clientEvent]map[string]bool{}
	}
	b.subscribers[ch] = struct{}{}
	var selected map[string]bool
	if requested != nil {
		selected = map[string]bool{}
		for _, value := range requested {
			selected[value] = true
		}
	}
	b.channels[ch] = selected
	b.channelFilters[ch] = requested != nil
	b.clientIDs[ch] = normalizeClientID(clientID)
	b.clientStats[ch] = &eventSubscriberStats{}
	b.pending[ch] = map[string]clientEvent{}
	b.repair[ch] = map[string]bool{}
	resources := make([]string, 0, len(b.retained))
	for resource := range b.retained {
		resources = append(resources, resource)
	}
	sort.Strings(resources)
	replay := make([]clientEvent, 0, len(resources))
	for _, resource := range resources {
		replay = append(replay, b.retained[resource])
	}
	for _, e := range replay {
		if b.channelFilters[ch] && !eventMatchesChannels(e.Type, mapKeys(selected)) {
			continue
		}
		e.Replay = true
		select {
		case ch <- e:
			b.pending[ch][e.Resource] = e
			b.replayed++
		default:
			droppedResource := e.Resource
			if droppedResource == "" {
				droppedResource = "event"
			}
			incrementEventMetric(b.droppedByResource, droppedResource, eventBytes(e))
			b.clientStats[ch].droppedEvents++
			if e.Resource != "" {
				b.repair[ch][e.Resource] = true
			}
			b.dropped++
		}
	}
	b.mu.Unlock()
	return ch, func() {
		b.mu.Lock()
		delete(b.subscribers, ch)
		delete(b.channels, ch)
		delete(b.channelFilters, ch)
		delete(b.clientIDs, ch)
		delete(b.clientStats, ch)
		delete(b.pending, ch)
		delete(b.repair, ch)
		close(ch)
		b.mu.Unlock()
	}
}

type operationState struct {
	operationsMu      sync.Mutex
	operations        map[string]*goOperation
	operationsPath    string
	operationsJournal string
}

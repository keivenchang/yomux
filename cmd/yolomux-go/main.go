package main

import (
	"bufio"
	"bytes"
	"context"
	"crypto/hmac"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha1"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/base64"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"encoding/pem"
	"flag"
	"fmt"
	"io"
	"log/slog"
	"math/big"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"
	"unicode"
	"unsafe"
)

const version = "0.1.0-phase2"
const wsGUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

type server struct {
	root              string
	host              string
	port              int
	config            string
	httpMode          bool
	mu                sync.Mutex
	children          map[*exec.Cmd]struct{}
	events            *eventBroker
	operationsMu      sync.Mutex
	operations        map[string]*goOperation
	operationsPath    string
	operationsJournal string
	eventLogPath      string
	background        *goBackgroundOwner
	logRing           *goLogRing
	rootLease         *os.File
	shutdownOnce      sync.Once
	startedAt         time.Time
	authMu            sync.Mutex
	authFailures      map[string]authFailure
}

type authFailure struct {
	count   int
	expires time.Time
}

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
	"transcripts_changed": {"transcripts"}, "tmux_signals_changed": {"status", "attention"},
	"update_available": {"core"}, "watched_prs_changed": {"core", "attention"},
	"yoagent_conversation_changed": {"yoagent"}, "yoagent_jobs_changed": {"yoagent", "attention"},
	"yoagent_skills_changed": {"yoagent"}, "yoagent_stream_delta": {"yoagent"},
}

var eventChannels = []string{"activity", "attention", "chat", "core", "events", "files", "status", "stats", "transcripts", "yoagent"}

var retainedEventTypes = map[string]bool{"backend_health_changed": true, "search_progress": true}

const backgroundRefreshCoalesce = 5 * time.Second
const eventQueueSize = 256
const retainedEventLimit = 8
const operationRecordLimit = 512
const operationRetention = 10 * time.Minute
const operationReplayRecordBytes = 256 * 1024
const operationReplayTotalBytes = 2 * 1024 * 1024
const operationJournalVersion = 2
const operationAckJournalVersion = 3
const eventJournalVersion = 1
const eventJournalSnapshotVersion = 2
const eventQueueHistoryLimit = 256
const eventJournalCompactEvery = eventQueueHistoryLimit
const backgroundHeartbeatInterval = 500 * time.Millisecond
const backgroundOwnerStaleAfter = 3 * time.Second

type goBackgroundOwner struct {
	mu                    sync.Mutex
	config                string
	root                  string
	port                  int
	serverID              string
	hostnameValue         string
	stableHostID          string
	bootID                string
	processStartIdentity  string
	owner                 bool
	status                string
	startedAt             int64
	generationID          string
	lastTransition        string
	lastError             string
	roles                 map[string]map[string]any
	counters              map[string]int
	refreshRequests       map[string]int
	fallbackCounts        map[string]int
	recentRefreshes       map[string]time.Time
	lockFile              *os.File
	ownerPath             string
	generationPath        string
	generationIndexPath   string
	priority              int
	nonce                 string
	lastTransitionDetails map[string]any
	processDiagnostics    []map[string]any
	maintenanceFailures   []map[string]any
	capabilities          map[string]int
	controlSocket         string
	stopHeartbeat         chan struct{}
	heartbeatDone         chan struct{}
	eventPublish          func(string, map[string]any) error
	writeFile             func(string, []byte) error
}

var backgroundRoles = []string{"tabber-activity", "session-files", "search-index", "stats-sampler", "watch-roots"}

func readIdentityValue(environmentName, path, fallback string) string {
	value := ""
	if environmentName != "" {
		value = strings.TrimSpace(os.Getenv(environmentName))
	}
	if value == "" && path != "" {
		if data, err := os.ReadFile(path); err == nil {
			value = strings.TrimSpace(string(data))
		}
	}
	if value == "" {
		value = strings.TrimSpace(fallback)
	}
	value = strings.ToLower(value)
	if value == "" {
		return "unknown"
	}
	for _, character := range value {
		if !(character >= 'a' && character <= 'z') && !(character >= '0' && character <= '9') && !strings.ContainsRune("._-", character) {
			digest := sha256.Sum256([]byte(value))
			return hex.EncodeToString(digest[:])
		}
	}
	return value[:minInt(len(value), 128)]
}

func processStartIdentity(pid int) string {
	data, err := os.ReadFile(filepath.Join("/proc", strconv.Itoa(pid), "stat"))
	if err != nil {
		return ""
	}
	closingParen := strings.LastIndexByte(string(data), ')')
	if closingParen < 0 {
		return ""
	}
	fields := strings.Fields(string(data)[closingParen+1:])
	if len(fields) <= 19 {
		return ""
	}
	return "proc:" + fields[19]
}

func sameOwnerHostBoot(record map[string]any, owner *goBackgroundOwner) bool {
	recordedHost, hostPresent := record["stable_host_id"].(string)
	recordedBoot, bootPresent := record["boot_id"].(string)
	if hostPresent && recordedHost != "" && recordedHost != owner.stableHostID {
		return false
	}
	if bootPresent && recordedBoot != "" && recordedBoot != owner.bootID {
		return false
	}
	return true
}

func sameOwnerProcess(record map[string]any, owner *goBackgroundOwner) bool {
	pid := int(backgroundRecordInt64(record, "pid"))
	if pid <= 0 || !pidIsAlive(pid) {
		return false
	}
	recordedStart, startPresent := record["process_start_identity"].(string)
	if startPresent && recordedStart != "" {
		currentStart := processStartIdentity(pid)
		return currentStart != "" && currentStart == recordedStart
	}
	return true
}

func backgroundOwnerPriority(port int) int {
	preferred, err := strconv.Atoi(strings.TrimSpace(os.Getenv("YOLOMUX_BACKGROUND_OWNER_PRIMARY_PORT")))
	if err == nil && port != 0 && port == preferred {
		return 100
	}
	return 0
}

func newGoBackgroundOwner(config, root string, ports ...int) *goBackgroundOwner {
	startedAt := time.Now().UnixNano()
	port := 0
	if len(ports) > 0 {
		port = ports[0]
	}
	hostname, _ := os.Hostname()
	stableHostID := readIdentityValue("YOLOMUX_HOST_ID", "/etc/machine-id", hostname)
	bootID := readIdentityValue("", "/proc/sys/kernel/random/boot_id", "unknown-boot")
	processStart := processStartIdentity(os.Getpid())
	roles := map[string]map[string]any{}
	refreshRequests := map[string]int{}
	fallbackCounts := map[string]int{}
	for _, role := range backgroundRoles {
		roles[role] = map[string]any{"role": role, "owner": true, "status": "local", "refresh_requests": 0, "fallback_count": 0, "last_error": ""}
		refreshRequests[role] = 0
		fallbackCounts[role] = 0
	}
	owner := &goBackgroundOwner{
		config: config, root: root, port: port, serverID: fmt.Sprintf("go-%d", os.Getpid()), hostnameValue: hostname,
		stableHostID: stableHostID, bootID: bootID, processStartIdentity: processStart,
		owner: true, status: "local", startedAt: startedAt,
		generationID: fmt.Sprintf("%d-%d", startedAt, os.Getpid()), lastTransition: "local",
		roles: roles, counters: map[string]int{
			"avoided_recomputes": 0, "coalesced_refresh_requests": 0, "follower_stale_reads": 0,
			"owner_acquired": 1, "owner_released": 0, "owner_refresh_requests": 0,
			"search_index_bytes_written": 0, "takeover_failed": 0, "takeover_success": 0,
		}, refreshRequests: refreshRequests, fallbackCounts: fallbackCounts, recentRefreshes: map[string]time.Time{},
		lastTransitionDetails: map[string]any{}, capabilities: map[string]int{}, nonce: fmt.Sprintf("%d-%d", startedAt, os.Getpid()),
	}
	owner.priority = backgroundOwnerPriority(port)
	owner.writeFile = writeAtomicFile
	if config != "" {
		if err := os.MkdirAll(config, 0o700); err == nil {
			owner.ownerPath = filepath.Join(config, "go-background-owner.json")
			generationDir := filepath.Join(config, "go-background-generations")
			_ = os.MkdirAll(generationDir, 0o700)
			owner.generationPath = filepath.Join(generationDir, owner.generationID+".json")
			owner.generationIndexPath = filepath.Join(generationDir, "index.json")
			if file, err := os.OpenFile(filepath.Join(config, "go-background-owner.lock"), os.O_CREATE|os.O_RDWR, 0o600); err == nil {
				owner.lockFile = file
				if syscall.Flock(int(file.Fd()), syscall.LOCK_EX|syscall.LOCK_NB) != nil {
					owner.owner = false
					owner.status = "follower"
					// Keep the descriptor open: a follower must be able to acquire the
					// same lock after the incumbent exits.
				}
			}
		}
	}
	_ = owner.writeGenerationRecord()
	if owner.owner {
		if err := owner.writeRecord(); err != nil {
			owner.owner = false
			owner.status = "owner_record_error"
			owner.lastError = err.Error()
			owner.releaseLock()
		} else {
			owner.startHeartbeat()
		}
	}
	if !owner.owner && owner.lockFile != nil {
		owner.startHeartbeat()
	}
	return owner
}

func writeAtomicFile(path string, data []byte) error {
	tmp := path + ".tmp"
	f, err := os.OpenFile(tmp, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0600)
	if err != nil {
		return err
	}
	if _, err = f.Write(data); err == nil {
		err = f.Sync()
	}
	closeErr := f.Close()
	if err == nil {
		err = closeErr
	}
	if err != nil {
		_ = os.Remove(tmp)
		return err
	}
	if err := os.Rename(tmp, path); err != nil {
		_ = os.Remove(tmp)
		return err
	}
	return nil
}

func (o *goBackgroundOwner) roleNames() []string {
	roles := make([]string, 0, len(o.roles))
	for role := range o.roles {
		roles = append(roles, role)
	}
	sort.Strings(roles)
	return roles
}

func (o *goBackgroundOwner) roleStatusPayloadLocked() map[string]any {
	roles := map[string]any{}
	for role, value := range o.roles {
		roleCopy := cloneMap(value)
		roleCopy["owner"] = o.owner
		roleCopy["status"] = o.status
		roleCopy["refresh_requests"] = o.refreshRequests[role]
		roleCopy["fallback_count"] = o.fallbackCounts[role]
		roles[role] = roleCopy
	}
	return roles
}

func (o *goBackgroundOwner) writeRecord() error {
	if o.ownerPath == "" {
		return nil
	}
	record, err := json.Marshal(map[string]any{"server_id": o.serverID, "generation": o.generationID, "generation_id": o.generationID, "pid": os.Getpid(), "hostname": o.hostnameValue, "stable_host_id": o.stableHostID, "boot_id": o.bootID, "process_start_identity": o.processStartIdentity, "port": o.port, "root": o.root, "project_root": o.root, "control_socket": o.controlSocket, "status": o.status, "priority": o.priority, "started_at_ns": o.startedAt, "last_heartbeat": float64(time.Now().UnixNano()) / 1e9, "updated_at": time.Now().UnixMilli(), "nonce": o.nonce, "roles": o.roleNames(), "role_status": o.roleStatusPayloadLocked(), "counters": cloneIntMap(o.counters), "capabilities": cloneIntMap(o.capabilities)})
	if err != nil {
		return err
	}
	if o.writeFile == nil {
		o.writeFile = writeAtomicFile
	}
	return o.writeFile(o.ownerPath, record)
}

func (o *goBackgroundOwner) writeGenerationRecord() error {
	if o.generationPath == "" {
		return nil
	}
	record, err := json.Marshal(map[string]any{"server_id": o.serverID, "generation": o.generationID, "generation_id": o.generationID, "pid": os.Getpid(), "hostname": o.hostnameValue, "stable_host_id": o.stableHostID, "boot_id": o.bootID, "process_start_identity": o.processStartIdentity, "port": o.port, "root": o.root, "project_root": o.root, "control_socket": o.controlSocket, "status": o.status, "owner": o.owner, "priority": o.priority, "started_at_ns": o.startedAt, "last_heartbeat": float64(time.Now().UnixNano()) / 1e9, "updated_at": time.Now().UnixMilli(), "nonce": o.nonce, "roles": o.roleNames(), "role_status": o.roleStatusPayloadLocked(), "counters": cloneIntMap(o.counters), "capabilities": cloneIntMap(o.capabilities), "last_transition": o.lastTransition, "last_error": o.lastError})
	if err != nil {
		return err
	}
	if o.writeFile == nil {
		o.writeFile = writeAtomicFile
	}
	if err := o.writeFile(o.generationPath, record); err != nil {
		return err
	}
	if o.generationIndexPath == "" {
		return nil
	}
	return withFileLock(o.generationIndexPath, func() error {
		entries := map[string]map[string]any{}
		if data, err := os.ReadFile(o.generationIndexPath); err == nil {
			var index struct {
				Records map[string]map[string]any `json:"records"`
			}
			if json.Unmarshal(data, &index) == nil && index.Records != nil {
				entries = index.Records
			}
		}
		var recordMap map[string]any
		if json.Unmarshal(record, &recordMap) != nil {
			return fmt.Errorf("invalid generation record")
		}
		entries[o.generationID] = recordMap
		ids := make([]string, 0, len(entries))
		for id := range entries {
			ids = append(ids, id)
		}
		sort.Slice(ids, func(i, j int) bool { return backgroundGenerationLess(entries[ids[j]], entries[ids[i]]) })
		if len(ids) > 64 {
			ids = ids[:64]
		}
		compacted := map[string]map[string]any{}
		for _, id := range ids {
			compacted[id] = entries[id]
		}
		data, err := json.Marshal(map[string]any{"version": 1, "records": compacted})
		if err != nil {
			return err
		}
		return o.writeFile(o.generationIndexPath, data)
	})
}

func (o *goBackgroundOwner) releaseLock() {
	if o.lockFile != nil {
		_ = syscall.Flock(int(o.lockFile.Fd()), syscall.LOCK_UN)
	}
}

func (o *goBackgroundOwner) startHeartbeat() {
	o.mu.Lock()
	if o.stopHeartbeat != nil {
		o.mu.Unlock()
		return
	}
	stopHeartbeat := make(chan struct{})
	heartbeatDone := make(chan struct{})
	o.stopHeartbeat = stopHeartbeat
	o.heartbeatDone = heartbeatDone
	o.mu.Unlock()
	go func(stop <-chan struct{}, done chan<- struct{}) {
		ticker := time.NewTicker(backgroundHeartbeatInterval)
		defer ticker.Stop()
		defer close(done)
		for {
			select {
			case <-ticker.C:
				o.mu.Lock()
				if o.owner {
					if o.shouldYieldToNewerGenerationLocked() {
						o.demoteLocked("newer_generation", fmt.Errorf("a newer live generation has priority"))
					} else if err := o.writeRecord(); err != nil {
						o.demoteLocked("owner_record_error", err)
					} else if err := o.writeGenerationRecord(); err != nil {
						o.demoteLocked("generation_record_error", err)
					}
				} else {
					o.tryTakeoverLocked()
				}
				o.mu.Unlock()
			case <-stop:
				return
			}
		}
	}(stopHeartbeat, heartbeatDone)
}

func (o *goBackgroundOwner) shouldYieldToNewerGenerationLocked() bool {
	latest := o.latestGeneration()
	if latest == nil {
		return false
	}
	latestID, _ := latest["generation_id"].(string)
	if latestID == "" {
		latestID, _ = latest["generation"].(string)
	}
	if latestID == o.generationID {
		return false
	}
	pid := int(backgroundRecordInt64(latest, "pid"))
	return pid > 0 && pid != os.Getpid() && sameOwnerHostBoot(latest, o) && sameOwnerProcess(latest, o) && !o.ownerRecordStale(latest)
}

func pidIsAlive(pid int) bool {
	if pid <= 0 {
		return false
	}
	err := syscall.Kill(pid, 0)
	return err == nil || err == syscall.EPERM
}

func (o *goBackgroundOwner) demoteLocked(status string, err error) {
	wasOwner := o.owner
	o.owner = false
	o.status = status
	o.lastTransition = "demote"
	o.lastError = err.Error()
	if wasOwner {
		o.releaseLock()
	}
	_ = o.writeGenerationRecord()
	if wasOwner {
		o.publishOwnerChangedLocked()
	}
}

func (o *goBackgroundOwner) readCurrentOwner() map[string]any {
	if o.ownerPath == "" {
		return nil
	}
	data, err := os.ReadFile(o.ownerPath)
	if err != nil {
		return nil
	}
	var record map[string]any
	if json.Unmarshal(data, &record) != nil {
		return nil
	}
	return record
}

func backgroundRecordTime(record map[string]any) time.Time {
	value, ok := record["last_heartbeat"].(float64)
	if !ok || value <= 0 {
		return time.Time{}
	}
	seconds := int64(value)
	return time.Unix(seconds, int64((value-float64(seconds))*1e9))
}

func backgroundRecordInt64(record map[string]any, key string) int64 {
	value, ok := record[key].(float64)
	if !ok {
		return 0
	}
	return int64(value)
}

func backgroundGenerationLess(left, right map[string]any) bool {
	leftPriority, rightPriority := backgroundRecordInt64(left, "priority"), backgroundRecordInt64(right, "priority")
	if leftPriority != rightPriority {
		return leftPriority < rightPriority
	}
	leftStarted, rightStarted := backgroundRecordInt64(left, "started_at_ns"), backgroundRecordInt64(right, "started_at_ns")
	if leftStarted != rightStarted {
		return leftStarted < rightStarted
	}
	leftPID, rightPID := backgroundRecordInt64(left, "pid"), backgroundRecordInt64(right, "pid")
	if leftPID != rightPID {
		return leftPID < rightPID
	}
	return fmt.Sprint(left["generation_id"], left["generation"]) < fmt.Sprint(right["generation_id"], right["generation"])
}

func (o *goBackgroundOwner) latestGeneration() map[string]any {
	if o.generationPath == "" {
		return nil
	}
	if o.generationIndexPath != "" {
		if data, err := os.ReadFile(o.generationIndexPath); err == nil {
			var index struct {
				Records map[string]map[string]any `json:"records"`
			}
			if json.Unmarshal(data, &index) == nil {
				var latest map[string]any
				for _, record := range index.Records {
					if !sameOwnerHostBoot(record, o) {
						continue
					}
					if latest == nil || backgroundGenerationLess(latest, record) {
						latest = record
					}
				}
				if latest != nil {
					return latest
				}
			}
		}
	}
	entries, err := os.ReadDir(filepath.Dir(o.generationPath))
	if err != nil {
		return nil
	}
	var latest map[string]any
	for _, entry := range entries {
		if entry.IsDir() || filepath.Ext(entry.Name()) != ".json" {
			continue
		}
		data, err := os.ReadFile(filepath.Join(filepath.Dir(o.generationPath), entry.Name()))
		if err != nil {
			continue
		}
		var record map[string]any
		if json.Unmarshal(data, &record) != nil || record["generation_id"] == nil && record["generation"] == nil || !sameOwnerHostBoot(record, o) {
			continue
		}
		if latest == nil || backgroundGenerationLess(latest, record) {
			latest = record
		}
	}
	return latest
}

func (o *goBackgroundOwner) ownerRecordStale(record map[string]any) bool {
	when := backgroundRecordTime(record)
	return !when.IsZero() && time.Since(when) > backgroundOwnerStaleAfter
}

func (o *goBackgroundOwner) tryTakeoverLocked() bool {
	if o.owner || o.lockFile == nil || syscall.Flock(int(o.lockFile.Fd()), syscall.LOCK_EX|syscall.LOCK_NB) != nil {
		return false
	}
	latest := o.latestGeneration()
	latestID := ""
	if latest != nil {
		latestID, _ = latest["generation_id"].(string)
		if latestID == "" {
			latestID, _ = latest["generation"].(string)
		}
	}
	if latest != nil && latestID != "" && latestID != o.generationID && sameOwnerHostBoot(latest, o) && sameOwnerProcess(latest, o) && !o.ownerRecordStale(latest) && latest["status"] != "stopped" {
		o.releaseLock()
		o.status = "blocked_by_newer_generation"
		o.lastTransition = "takeover_blocked"
		o.lastError = "a newer live generation is present"
		o.counters["takeover_failed"]++
		return false
	}
	previous := o.readCurrentOwner()
	if previous != nil && !sameOwnerHostBoot(previous, o) {
		o.releaseLock()
		o.status = "blocked_by_foreign_owner"
		o.lastTransition = "takeover_blocked"
		o.lastError = "owner belongs to another host or boot"
		return false
	}
	if previous != nil && previous["generation_id"] != o.generationID && previous["generation"] != o.generationID && sameOwnerProcess(previous, o) && !o.ownerRecordStale(previous) && previous["status"] != "stopped" {
		o.releaseLock()
		o.status = "blocked_by_fresh_owner"
		o.lastTransition = "takeover_blocked"
		return false
	}
	o.owner = true
	o.status = "local"
	o.lastTransition = "takeover"
	if err := o.writeRecord(); err != nil {
		o.demoteLocked("owner_record_error", err)
		return false
	}
	if err := o.writeGenerationRecord(); err != nil {
		o.demoteLocked("generation_record_error", err)
		return false
	}
	if previous == nil || o.ownerRecordStale(previous) || previous["generation_id"] != o.generationID {
		o.publishOwnerChangedLocked()
	}
	return true
}

func (o *goBackgroundOwner) publishOwnerChangedLocked() {
	if o.eventPublish == nil {
		return
	}
	if err := o.eventPublish("background_owner_changed", o.statusPayloadLocked()); err != nil {
		o.lastError = err.Error()
	}
}

func (o *goBackgroundOwner) setEventPublisher(publish func(string, map[string]any) error) {
	o.mu.Lock()
	o.eventPublish = publish
	o.mu.Unlock()
}

func (o *goBackgroundOwner) refreshRequestKey(role string, payload map[string]any) string {
	keyPayload := map[string]any{}
	if cacheKey, ok := payload["cache_key"]; ok && fmt.Sprint(cacheKey) != "" {
		keyPayload["cache_key"] = fmt.Sprint(cacheKey)
	} else if nested, ok := payload["payload"].(map[string]any); ok {
		if cacheKey, nestedOK := nested["cache_key"]; nestedOK && fmt.Sprint(cacheKey) != "" {
			keyPayload["cache_key"] = fmt.Sprint(cacheKey)
		} else {
			keyPayload = nested
		}
	} else {
		for key, value := range payload {
			if key != "action" && key != "reason" && key != "requester" && key != "role" && key != "trigger" {
				keyPayload[key] = value
			}
		}
	}
	data, err := json.Marshal(keyPayload)
	if err != nil {
		data = []byte(fmt.Sprint(keyPayload))
	}
	return role + "\x00" + string(data)
}

func (o *goBackgroundOwner) coalesceRefreshRequestLocked(role string, payload map[string]any) bool {
	now := time.Now()
	for key, expires := range o.recentRefreshes {
		if !expires.After(now) {
			delete(o.recentRefreshes, key)
		}
	}
	key := o.refreshRequestKey(role, payload)
	if expires, ok := o.recentRefreshes[key]; ok && expires.After(now) {
		o.counters["coalesced_refresh_requests"]++
		return true
	}
	o.recentRefreshes[key] = now.Add(backgroundRefreshCoalesce)
	return false
}

func (o *goBackgroundOwner) recordRefreshRequestLocked(role string) {
	o.refreshRequests[role]++
	o.counters["owner_refresh_requests"]++
}

func (o *goBackgroundOwner) requestRefresh(role string, payload map[string]any) map[string]any {
	o.mu.Lock()
	defer o.mu.Unlock()
	if _, ok := o.roles[role]; !ok {
		o.fallbackCounts[role]++
		return map[string]any{"ok": false, "accepted": false, "role": role, "error": "unknown background role", "fallback": true}
	}
	if !o.owner {
		o.fallbackCounts[role]++
		return map[string]any{"ok": false, "accepted": false, "role": role, "error": "background owner is not local", "fallback": true}
	}
	if o.coalesceRefreshRequestLocked(role, payload) {
		return map[string]any{"ok": true, "accepted": true, "role": role, "local_owner": true, "fallback": false, "already_pending": true, "coalesced": true}
	}
	o.recordRefreshRequestLocked(role)
	return map[string]any{"ok": true, "accepted": true, "role": role, "local_owner": true, "fallback": false}
}

func (o *goBackgroundOwner) refreshQueuePayloadLocked() map[string]any {
	now := time.Now()
	pendingByRole := map[string]int{}
	nextExpires := 0.0
	for key, expires := range o.recentRefreshes {
		if !expires.After(now) {
			delete(o.recentRefreshes, key)
			continue
		}
		role, _, _ := strings.Cut(key, "\x00")
		pendingByRole[role]++
		remaining := expires.Sub(now).Seconds()
		if nextExpires == 0 || remaining < nextExpires {
			nextExpires = remaining
		}
	}
	return map[string]any{"coalesce_window_seconds": backgroundRefreshCoalesce.Seconds(), "recent_pending_count": len(o.recentRefreshes), "recent_pending_by_role": pendingByRole, "next_expires_seconds": nextExpires}
}

func (o *goBackgroundOwner) recordMaintenanceFailureLocked(operation, path string, err error) {
	o.maintenanceFailures = append(o.maintenanceFailures, map[string]any{
		"operation": operation, "path": path, "error": err.Error(), "at": time.Now().Unix(), "generation_id": o.generationID,
	})
	if len(o.maintenanceFailures) > 64 {
		o.maintenanceFailures = o.maintenanceFailures[len(o.maintenanceFailures)-64:]
	}
	o.lastError = operation + " failed: " + err.Error()
}

func (o *goBackgroundOwner) removeOwnedRecordsLocked() {
	if o.ownerPath != "" {
		current := o.readCurrentOwner()
		currentID := fmt.Sprint(current["generation_id"])
		if currentID == "" || currentID == "<nil>" {
			currentID = fmt.Sprint(current["generation"])
		}
		if current != nil && currentID == o.generationID {
			if err := os.Remove(o.ownerPath); err != nil && !os.IsNotExist(err) {
				o.recordMaintenanceFailureLocked("stop_owner_record_unlink", o.ownerPath, err)
			}
		}
	}
	if o.generationPath == "" {
		return
	}
	if err := os.Remove(o.generationPath); err != nil && !os.IsNotExist(err) {
		o.recordMaintenanceFailureLocked("stop_generation_record_unlink", o.generationPath, err)
	}
	if o.generationIndexPath == "" {
		return
	}
	if err := withFileLock(o.generationIndexPath, func() error {
		entries := map[string]map[string]any{}
		if data, readErr := os.ReadFile(o.generationIndexPath); readErr == nil {
			var index struct {
				Records map[string]map[string]any `json:"records"`
			}
			if json.Unmarshal(data, &index) == nil && index.Records != nil {
				entries = index.Records
			}
		}
		delete(entries, o.generationID)
		data, marshalErr := json.Marshal(map[string]any{"version": 1, "records": entries})
		if marshalErr != nil {
			return marshalErr
		}
		return o.writeFile(o.generationIndexPath, data)
	}); err != nil {
		o.recordMaintenanceFailureLocked("stop_generation_index_write", o.generationIndexPath, err)
	}
}

func (o *goBackgroundOwner) statusPayloadLocked() map[string]any {
	owner := map[string]any{"server_id": o.serverID, "pid": os.Getpid(), "hostname": o.hostnameValue, "stable_host_id": o.stableHostID, "boot_id": o.bootID, "process_start_identity": o.processStartIdentity, "port": o.port, "project_root": o.root, "control_socket": o.controlSocket, "generation_id": o.generationID, "started_at_ns": o.startedAt, "nonce": o.nonce, "priority": o.priority, "capabilities": cloneIntMap(o.capabilities)}
	latest := o.latestGeneration()
	if latest == nil {
		latest = owner
	}
	currentOwner := any(owner)
	if persisted := o.readCurrentOwner(); persisted != nil {
		currentOwner = persisted
	}
	return map[string]any{"owner": o.owner, "status": o.status, "generation": owner, "latest_generation": latest, "current_owner": currentOwner, "roles": o.roleStatusPayloadLocked(), "counters": cloneIntMap(o.counters), "refresh_queue": o.refreshQueuePayloadLocked(), "last_transition": o.lastTransition, "last_transition_details": cloneMap(o.lastTransitionDetails), "last_error": o.lastError, "process_diagnostics": o.processDiagnostics, "maintenance_failures": o.maintenanceFailures, "search_index": map[string]any{"role": "search-index", "owner": o.owner, "mode": map[bool]string{true: "indexing-server", false: "read-server"}[o.owner], "current_server": owner, "owner_server": currentOwner, "status": o.status}}
}

func (o *goBackgroundOwner) statusPayload() map[string]any {
	o.mu.Lock()
	defer o.mu.Unlock()
	return o.statusPayloadLocked()
}

func (o *goBackgroundOwner) claim() map[string]any {
	o.mu.Lock()
	wasOwner := o.owner
	claimed := !wasOwner && o.tryTakeoverLocked()
	o.mu.Unlock()
	if claimed {
		o.startHeartbeat()
	}
	ok := wasOwner || claimed
	payload := map[string]any{"ok": ok, "claimed": claimed, "was_owner": wasOwner, "status": o.statusPayload()}
	if !ok {
		diagnostic := o.statusPayload()
		message := fmt.Sprint(diagnostic["last_error"])
		if message == "<nil>" || message == "" {
			message = "background owner takeover failed"
		}
		payload["error"] = message
		payload["diagnostic"] = message
	}
	return payload
}

func (o *goBackgroundOwner) close() {
	o.mu.Lock()
	wasOwner := o.owner
	if o.owner {
		o.owner = false
		o.status = "stopped"
		o.lastTransition = "stop"
		_ = o.writeRecord()
		_ = o.writeGenerationRecord()
		if wasOwner {
			o.publishOwnerChangedLocked()
		}
	}
	o.removeOwnedRecordsLocked()
	stop, done := o.stopHeartbeat, o.heartbeatDone
	o.stopHeartbeat, o.heartbeatDone = nil, nil
	if stop != nil {
		close(stop)
	}
	lockFile := o.lockFile
	o.lockFile = nil
	o.mu.Unlock()
	if done != nil {
		<-done
	}
	if lockFile != nil {
		_ = syscall.Flock(int(lockFile.Fd()), syscall.LOCK_UN)
		_ = lockFile.Close()
	}
}

func acquireInstanceRootLease(root string) (*os.File, error) {
	rootPath, err := filepath.Abs(root)
	if err != nil {
		return nil, err
	}
	if err := os.MkdirAll(rootPath, 0o700); err != nil {
		return nil, err
	}
	path := filepath.Join(rootPath, "instance.lock")
	lock, err := os.OpenFile(path, os.O_CREATE|os.O_RDWR, 0o600)
	if err != nil {
		return nil, err
	}
	if err := syscall.Flock(int(lock.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		_ = lock.Close()
		return nil, err
	}
	payload, err := json.Marshal(map[string]any{"pid": os.Getpid(), "root": rootPath, "updated_at": time.Now().UnixMilli()})
	if err != nil {
		releaseInstanceRootLease(lock)
		return nil, err
	}
	if err := lock.Truncate(0); err == nil {
		_, err = lock.Seek(0, io.SeekStart)
	}
	if err == nil {
		_, err = lock.Write(append(payload, '\n'))
	}
	if err == nil {
		err = lock.Sync()
	}
	if err != nil {
		releaseInstanceRootLease(lock)
		return nil, err
	}
	return lock, nil
}

func releaseInstanceRootLease(lock *os.File) {
	if lock == nil {
		return
	}
	_ = syscall.Flock(int(lock.Fd()), syscall.LOCK_UN)
	_ = lock.Close()
}

func cloneMap(value map[string]any) map[string]any {
	result := make(map[string]any, len(value))
	for key, item := range value {
		result[key] = item
	}
	return result
}

func cloneIntMap(value map[string]int) map[string]int {
	result := make(map[string]int, len(value))
	for key, item := range value {
		result[key] = item
	}
	return result
}

type goLogRing struct {
	mu             sync.Mutex
	epoch          string
	sequence       int
	capacity       int
	entries        []map[string]any
	dropped        int
	firstDroppedID *int
	lastDroppedID  *int
	droppedByLevel map[string]int
}

func newGoLogRing() *goLogRing {
	return &goLogRing{epoch: fmt.Sprintf("go-%d", time.Now().UnixNano()), capacity: 500, droppedByLevel: map[string]int{}}
}

func (l *goLogRing) emit(level, source, message string) {
	l.mu.Lock()
	defer l.mu.Unlock()
	l.sequence++
	if len(l.entries) >= l.capacity {
		droppedID, _ := l.entries[0]["id"].(int)
		droppedLevel, _ := l.entries[0]["level"].(string)
		l.entries = l.entries[1:]
		l.dropped++
		if l.firstDroppedID == nil {
			l.firstDroppedID = &droppedID
		}
		l.lastDroppedID = &droppedID
		l.droppedByLevel[droppedLevel]++
	}
	if len(message) > 4096 {
		message = message[:4096]
	}
	now := time.Now()
	location, err := time.LoadLocation("America/Los_Angeles")
	if err != nil {
		location = time.Local
	}
	l.entries = append(l.entries, map[string]any{"id": l.sequence, "timestamp": float64(now.UnixNano()) / 1e9, "wallTime": now.In(location).Format("2006-01-02 15:04:05 MST"), "level": level, "source": source, "category": "go", "message": message})
}

func (l *goLogRing) payload() map[string]any {
	l.mu.Lock()
	defer l.mu.Unlock()
	entries := make([]map[string]any, len(l.entries))
	for i, entry := range l.entries {
		entries[i] = cloneMap(entry)
	}
	var firstID, lastID any
	if l.firstDroppedID != nil {
		firstID = *l.firstDroppedID
	}
	if l.lastDroppedID != nil {
		lastID = *l.lastDroppedID
	}
	return map[string]any{"ok": true, "epoch": l.epoch, "logs": entries, "sequence": l.sequence, "capacity": l.capacity, "dropped": map[string]any{"count": l.dropped, "first_id": firstID, "last_id": lastID, "by_level": cloneIntMap(l.droppedByLevel)}}
}

func newEventBroker() *eventBroker {
	queueSize := eventQueueSize
	if value, err := strconv.Atoi(strings.TrimSpace(os.Getenv("YOLOMUX_GO_EVENT_QUEUE_SIZE"))); err == nil && value > 0 && value <= eventQueueSize {
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
				b.nextID = maxInt(b.nextID, snapshotEvent.ID)
				b.events = append(b.events, snapshotEvent)
				if snapshotEvent.Type == "operation_terminal" {
					b.terminalEvents[snapshotEvent.Resource] = snapshotEvent
				}
				if retainedEventTypes[snapshotEvent.Type] && (b.retained[snapshotEvent.Resource].ID != 0 || len(b.retained) < retainedEventLimit) {
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
		b.nextID = maxInt(b.nextID, event.ID)
		b.revisions[event.Resource] = maxInt(b.revisions[event.Resource], event.Revision)
		b.events = append(b.events, event)
		if event.Type == "operation_terminal" {
			b.terminalEvents[event.Resource] = event
		}
		if retainedEventTypes[event.Type] && (b.retained[event.Resource].ID != 0 || len(b.retained) < retainedEventLimit) {
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
		tmp := path + ".migrate"
		file, err := os.OpenFile(tmp, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0600)
		if err != nil {
			return err
		}
		for _, event := range events {
			event.Replay = false
			data, marshalErr := json.Marshal(map[string]any{"version": eventJournalVersion, "type": "event", "epoch": event.Epoch, "event": event})
			if marshalErr != nil {
				_ = file.Close()
				_ = os.Remove(tmp)
				return marshalErr
			}
			if _, err = file.Write(append(data, '\n')); err != nil {
				_ = file.Close()
				_ = os.Remove(tmp)
				return err
			}
		}
		if err = file.Sync(); err != nil {
			_ = file.Close()
			_ = os.Remove(tmp)
			return err
		}
		if err = file.Close(); err != nil {
			_ = os.Remove(tmp)
			return err
		}
		if err = os.Rename(tmp, path); err != nil {
			_ = os.Remove(tmp)
			return err
		}
		return syncDirectory(filepath.Dir(path))
	})
}
func maxInt(a, b int) int {
	if a > b {
		return a
	}
	return b
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
		tmp := path + ".compact"
		file, err := os.OpenFile(tmp, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0600)
		if err != nil {
			return err
		}
		entry := map[string]any{
			"version":            eventJournalSnapshotVersion,
			"type":               "snapshot",
			"next_id":            nextID,
			"resource_revisions": revisions,
			"events":             events,
		}
		data, marshalErr := json.Marshal(entry)
		if marshalErr == nil {
			_, marshalErr = file.Write(append(data, '\n'))
		}
		if marshalErr == nil {
			marshalErr = file.Sync()
		}
		closeErr := file.Close()
		if marshalErr == nil {
			marshalErr = closeErr
		}
		if marshalErr != nil {
			_ = os.Remove(tmp)
			return marshalErr
		}
		if err := os.Rename(tmp, path); err != nil {
			_ = os.Remove(tmp)
			return err
		}
		return syncDirectory(filepath.Dir(path))
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
		return records
	}
	result := records
	_ = withFileLock(path, func() error {
		result = loadOperationJournalUnlocked(path, records, true)
		return nil
	})
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
		return compactOperationJournalUnlocked(path, merged)
	})
}

func compactOperationJournalUnlocked(path string, records map[string]*goOperation) error {
	tmp := path + ".compact"
	f, err := os.OpenFile(tmp, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0600)
	if err != nil {
		return err
	}
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
		_ = f.Close()
		_ = os.Remove(tmp)
		return marshalErr
	}
	if _, err = f.Write(append(data, '\n')); err != nil {
		_ = f.Close()
		_ = os.Remove(tmp)
		return err
	}
	if err = f.Sync(); err != nil {
		_ = f.Close()
		_ = os.Remove(tmp)
		return err
	}
	if err = f.Close(); err != nil {
		_ = os.Remove(tmp)
		return err
	}
	if err = os.Rename(tmp, path); err != nil {
		_ = os.Remove(tmp)
		return err
	}
	return syncDirectory(filepath.Dir(path))
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
	tmp := s.operationsPath + ".tmp"
	f, err := os.OpenFile(tmp, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0600)
	if err != nil {
		return err
	}
	_, err = f.Write(data)
	if err == nil {
		err = f.Sync()
	}
	_ = f.Close()
	if err != nil {
		return err
	}
	if err := os.Rename(tmp, s.operationsPath); err != nil {
		return err
	}
	return syncDirectory(filepath.Dir(s.operationsPath))
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
	now := time.Now().UnixMilli()
	for id, record := range s.operations {
		if record == nil || record.State == "queued" {
			continue
		}
		at := record.TerminalAt
		if at == 0 {
			at = record.CreatedAt
		}
		if at > 0 && now-at > operationRetention.Milliseconds() {
			delete(s.operations, id)
		}
	}
	if len(s.operations) <= operationRecordLimit {
		return
	}
	ids := make([]string, 0, len(s.operations))
	for id, record := range s.operations {
		if record != nil && record.State != "queued" {
			ids = append(ids, id)
		}
	}
	sort.Slice(ids, func(i, j int) bool {
		left, right := s.operations[ids[i]], s.operations[ids[j]]
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
	for _, id := range ids[:minInt(len(ids), len(s.operations)-operationRecordLimit)] {
		delete(s.operations, id)
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

func (s *server) boundOperationReplaysLocked() bool {
	exposed := make([]*goOperation, 0)
	totalBytes := 0
	for _, record := range s.operations {
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
				bounded[key] = fmt.Sprint(value)[:minInt(len(fmt.Sprint(value)), 64)]
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
	if retainedEventTypes[kind] && (b.retained[resource].ID != 0 || len(b.retained) < retainedEventLimit) {
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

type wsConn struct {
	net.Conn
	writeMu sync.Mutex
}

type authUser struct{ username, password, role string }

type winsize struct{ rows, cols, x, y uint16 }

type ptyPair struct{ master, slave *os.File }

func clampDimension(value, minimum, maximum, fallback int) int {
	if value <= 0 {
		return fallback
	}
	if value < minimum {
		return minimum
	}
	if value > maximum {
		return maximum
	}
	return value
}

func openPTY(rows, cols int) (*ptyPair, error) {
	masterFD, err := syscall.Open("/dev/ptmx", syscall.O_RDWR|syscall.O_NOCTTY, 0)
	if err != nil {
		return nil, err
	}
	unlock := 0
	if _, _, errno := syscall.Syscall(syscall.SYS_IOCTL, uintptr(masterFD), uintptr(0x40045431), uintptr(unsafe.Pointer(&unlock))); errno != 0 {
		_ = syscall.Close(masterFD)
		return nil, errno
	}
	ptyNumber := 0
	if _, _, errno := syscall.Syscall(syscall.SYS_IOCTL, uintptr(masterFD), uintptr(0x80045430), uintptr(unsafe.Pointer(&ptyNumber))); errno != 0 {
		_ = syscall.Close(masterFD)
		return nil, errno
	}
	slaveFD, err := syscall.Open(fmt.Sprintf("/dev/pts/%d", ptyNumber), syscall.O_RDWR|syscall.O_NOCTTY, 0)
	if err != nil {
		_ = syscall.Close(masterFD)
		return nil, err
	}
	pair := &ptyPair{master: os.NewFile(uintptr(masterFD), "pty-master"), slave: os.NewFile(uintptr(slaveFD), "pty-slave")}
	if err := pair.resize(rows, cols); err != nil {
		pair.close()
		return nil, err
	}
	return pair, nil
}

func (p *ptyPair) resize(rows, cols int) error {
	size := winsize{rows: uint16(rows), cols: uint16(cols)}
	_, _, errno := syscall.Syscall(syscall.SYS_IOCTL, p.master.Fd(), uintptr(0x5414), uintptr(unsafe.Pointer(&size)))
	if errno != 0 {
		return errno
	}
	return nil
}

func (p *ptyPair) close() { _ = p.master.Close(); _ = p.slave.Close() }

func (c *wsConn) frame(opcode byte, payload []byte) error {
	c.writeMu.Lock()
	defer c.writeMu.Unlock()
	return writeFrame(c.Conn, opcode, payload)
}

func main() {
	port := flag.Int("port", 7221, "HTTP port")
	host := flag.String("host", "127.0.0.1", "bind address")
	root := flag.String("root", ".", "YOLOmux checkout root")
	configDir := flag.String("config", os.Getenv("YOLOMUX_CONFIG_DIR"), "YOLOmux config directory")
	httpMode := flag.Bool("http", false, "serve plain HTTP instead of the default HTTPS")
	certPath := flag.String("cert", "", "TLS certificate PEM path")
	keyPath := flag.String("key", "", "TLS private key PEM path")
	showVersion := flag.Bool("version", false, "print version")
	flag.Parse()
	if *showVersion {
		fmt.Printf("yolomux-go %s\n", version)
		return
	}
	if *port < 1 || *port > 65535 {
		fatal("invalid port")
	}
	if *configDir == "" {
		*configDir = defaultConfigDir()
	}
	rootPath, err := filepath.Abs(*root)
	if err != nil {
		fatal("invalid root: " + err.Error())
	}
	rootLease, err := acquireInstanceRootLease(rootPath)
	if err != nil {
		fatal("YOLOmux product root is already owned by another server or unavailable: " + err.Error())
	}
	eventPath := filepath.Join(*configDir, "go-events.jsonl")
	s := &server{root: rootPath, host: *host, port: *port, config: *configDir, httpMode: *httpMode, children: make(map[*exec.Cmd]struct{}), events: loadEventBroker(eventPath), eventLogPath: filepath.Join(*configDir, "event-log.jsonl"), operationsPath: filepath.Join(*configDir, "go-operations.json"), operationsJournal: filepath.Join(*configDir, "go-operations.jsonl"), logRing: newGoLogRing(), rootLease: rootLease}
	s.background = newGoBackgroundOwner(*configDir, rootPath, *port)
	s.background.setEventPublisher(func(kind string, payload map[string]any) error {
		_, err := s.events.publish(kind, payload)
		return err
	})
	s.operations = loadOperationJournal(s.operationsJournal, loadOperations(s.operationsPath))
	s.operationsMu.Lock()
	s.pruneOperationsLocked()
	s.operationsMu.Unlock()
	s.repairMissingTerminalEvents()
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", s.health)
	mux.HandleFunc("/livez", s.health)
	mux.HandleFunc("/login", s.login)
	mux.HandleFunc("/logout", s.logout)
	mux.HandleFunc("/api/auth-setup", s.authSetup)
	mux.Handle("/api/ping", s.protected(http.HandlerFunc(s.ping), "readonly"))
	mux.Handle("/api/client-events", s.protected(http.HandlerFunc(s.clientEvents), "readonly"))
	mux.Handle("/api/operations/ack", s.protected(http.HandlerFunc(s.operationAck), "readonly"))
	mux.Handle("/api/operations/cancel", s.protected(http.HandlerFunc(s.operationCancel), "admin"))
	mux.Handle("/api/background/status", s.protected(http.HandlerFunc(s.backgroundStatus), "readonly"))
	mux.Handle("/api/background/claim", s.protected(http.HandlerFunc(s.backgroundClaim), "admin"))
	mux.Handle("/api/operations/accept", s.protected(http.HandlerFunc(s.operationAccept), "admin"))
	mux.Handle("/api/operations/terminalize", s.protected(http.HandlerFunc(s.operationTerminalize), "admin"))
	mux.Handle("/api/system-status", s.protected(http.HandlerFunc(s.systemStatus), "readonly"))
	mux.Handle("/api/system-status/advanced", s.protected(http.HandlerFunc(s.systemStatus), "readonly"))
	mux.Handle("/api/logs", s.protected(http.HandlerFunc(s.logs), "readonly"))
	mux.Handle("/api/events", s.protected(http.HandlerFunc(s.eventsList), "readonly"))
	mux.Handle("/api/event", s.protected(http.HandlerFunc(s.eventPost), "readonly"))
	mux.Handle("/api/tmux", s.protected(http.HandlerFunc(s.tmux), "readonly"))
	mux.Handle("/api/tmux-session-exists", s.protected(http.HandlerFunc(s.sessionExists), "readonly"))
	mux.Handle("/api/ensure-session", s.protected(http.HandlerFunc(s.ensureSession), "admin"))
	mux.Handle("/api/create-session-plan", s.protected(http.HandlerFunc(s.createSessionPlan), "admin"))
	mux.Handle("/api/create-session", s.protected(http.HandlerFunc(s.createSession), "admin"))
	mux.Handle("/api/kill-session", s.protected(http.HandlerFunc(s.killSession), "admin"))
	mux.Handle("/api/operations/", s.protected(http.HandlerFunc(s.operation), "readonly"))
	mux.Handle("/", s.protected(http.HandlerFunc(s.index), "readonly"))
	mux.HandleFunc("/ws", s.websocket)
	mux.HandleFunc("/static/", s.static)
	addr := fmt.Sprintf("%s:%d", *host, *port)
	httpServer := &http.Server{Addr: addr, Handler: logging(mux), ReadHeaderTimeout: 10 * time.Second, ReadTimeout: 30 * time.Second, IdleTimeout: 120 * time.Second}
	go s.reaper()
	listener, err := net.Listen("tcp", addr)
	if err != nil {
		s.shutdown()
		fatal(err.Error())
	}
	if !*httpMode {
		certFile, keyFile, err := tlsFiles(*certPath, *keyPath, *host, *port)
		if err != nil {
			_ = listener.Close()
			s.shutdown()
			fatal("TLS setup failed: " + err.Error())
		}
		config, err := tlsConfig(certFile, keyFile)
		if err != nil {
			_ = listener.Close()
			s.shutdown()
			fatal("TLS setup failed: " + err.Error())
		}
		listener = tls.NewListener(listener, config)
	}
	scheme := "https"
	if *httpMode {
		scheme = "http"
	}
	slog.Info("YOLOmux Go backend", "version", version, "backend", "go", "addr", addr, "scheme", scheme)
	serveErr := make(chan error, 1)
	go func() { serveErr <- httpServer.Serve(listener) }()
	shutdownContext, stopSignals := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stopSignals()
	select {
	case <-shutdownContext.Done():
		s.shutdown()
		gracefulContext, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		if err := httpServer.Shutdown(gracefulContext); err != nil {
			_ = httpServer.Close()
		}
		cancel()
	case err := <-serveErr:
		if err != nil && err != http.ErrServerClosed {
			s.shutdown()
			fatal(err.Error())
		}
		s.shutdown()
	}
}

func fatal(message string) { slog.Error(message); os.Exit(1) }

func defaultConfigDir() string {
	if configured := os.Getenv("YOLOMUX_CONFIG_DIR"); configured != "" {
		return configured
	}
	if root := os.Getenv("YOLOMUX_ROOT"); root != "" {
		return filepath.Join(root, "config")
	}
	return filepath.Join(os.Getenv("HOME"), ".config", "yolomux")
}

func (s *server) repairMissingTerminalEvents() {
	if s.events == nil {
		return
	}
	s.operationsMu.Lock()
	records := make([]goOperation, 0, len(s.operations))
	for _, record := range s.operations {
		if operationIsTerminal(record) && !record.EventPublished {
			records = append(records, *record)
		}
	}
	s.operationsMu.Unlock()
	for _, record := range records {
		_, err := s.events.publish("operation_terminal", record.TerminalEvent)
		if err != nil {
			s.logState().emit("error", "operations", "terminal event recovery failed")
			continue
		}
		s.operationsMu.Lock()
		if current := s.operations[record.ID]; current != nil && !current.EventPublished {
			current.EventPublished = true
			if err := appendOperationJournal(s.operationsJournal, map[string]any{"version": operationJournalVersion, "type": "operation", "epoch": current.Epoch, "record": current}); err == nil {
				_ = s.saveOperationsLocked()
			}
		}
		s.operationsMu.Unlock()
	}
}

func (s *server) shutdown() {
	s.shutdownOnce.Do(func() {
		if s.background != nil {
			s.background.close()
		}
		s.mu.Lock()
		rootLease := s.rootLease
		s.rootLease = nil
		children := make([]*exec.Cmd, 0, len(s.children))
		for command := range s.children {
			children = append(children, command)
		}
		s.mu.Unlock()
		releaseInstanceRootLease(rootLease)
		for _, command := range children {
			if command.Process == nil || command.ProcessState != nil {
				continue
			}
			_ = syscall.Kill(-command.Process.Pid, syscall.SIGTERM)
			_ = command.Process.Kill()
		}
	})
}

func (s *server) users() []authUser {
	b, err := os.ReadFile(filepath.Join(s.config, "auth.yaml"))
	if err != nil {
		return nil
	}
	var users []authUser
	var current authUser
	seen := false
	for _, line := range strings.Split(string(b), "\n") {
		line = strings.TrimSpace(strings.SplitN(line, "#", 2)[0])
		if strings.HasPrefix(line, "- username:") {
			if seen && current.username != "" && current.password != "" {
				users = append(users, current)
			}
			current = authUser{username: yamlValue(strings.TrimSpace(strings.TrimPrefix(line, "- username:")))}
			seen = true
		} else if strings.HasPrefix(line, "password:") || strings.HasPrefix(line, "password_hash:") {
			current.password = yamlValue(strings.TrimSpace(strings.SplitN(line, ":", 2)[1]))
		} else if strings.HasPrefix(line, "role:") {
			current.role = strings.ToLower(yamlValue(strings.TrimSpace(strings.SplitN(line, ":", 2)[1])))
		}
	}
	if seen && current.username != "" && current.password != "" {
		users = append(users, current)
	}
	return users
}

func yamlValue(value string) string {
	value = strings.TrimSpace(value)
	if len(value) >= 2 && value[0] == '"' && value[len(value)-1] == '"' {
		value = value[1 : len(value)-1]
	}
	return strings.ReplaceAll(value, `\"`, `"`)
}

func (s *server) identity(r *http.Request) (authUser, bool) {
	cookieValue := ""
	if cookie, err := r.Cookie(s.authCookieName()); err == nil {
		cookieValue = cookie.Value
	}
	for _, user := range s.users() {
		if cookieValue != "" && hmac.Equal([]byte(cookieValue), []byte(s.cookieValue(user))) {
			return user, true
		}
	}
	if cookie, err := r.Cookie("yolomux_logged_out"); err == nil && cookie.Value == "1" {
		return authUser{}, false
	}
	basic := r.Header.Get("Authorization")
	if strings.HasPrefix(strings.ToLower(basic), "basic ") {
		decoded, err := base64.StdEncoding.DecodeString(strings.TrimSpace(basic[6:]))
		if err == nil {
			username, password, ok := strings.Cut(string(decoded), ":")
			if ok {
				for _, user := range s.users() {
					if user.username == username && passwordMatches(password, user.password) {
						return user, true
					}
				}
			}
		}
	}
	return authUser{}, false
}

func (s *server) basicIdentity(r *http.Request) (authUser, bool) {
	basic := r.Header.Get("Authorization")
	if !strings.HasPrefix(strings.ToLower(basic), "basic ") {
		return authUser{}, false
	}
	decoded, err := base64.StdEncoding.DecodeString(strings.TrimSpace(basic[6:]))
	if err != nil {
		return authUser{}, false
	}
	username, password, ok := strings.Cut(string(decoded), ":")
	if !ok {
		return authUser{}, false
	}
	for _, user := range s.users() {
		if user.username == username && passwordMatches(password, user.password) {
			return user, true
		}
	}
	return authUser{}, false
}

func (s *server) cookieValue(user authUser) string {
	secret := s.authSecret()
	if len(secret) != 32 {
		return ""
	}
	h := hmac.New(sha256.New, secret)
	_, _ = h.Write([]byte(user.username + ":" + user.password))
	return hex.EncodeToString(h.Sum(nil))
}

func (s *server) authCookieName() string {
	return "yolomux_auth_" + strconv.Itoa(s.port)
}

func (s *server) authSecret() []byte {
	if s.config == "" {
		return nil
	}
	path := filepath.Join(s.config, "auth-cookie-secret")
	if raw, err := os.ReadFile(path); err == nil {
		secret, decodeErr := hex.DecodeString(strings.TrimSpace(string(raw)))
		if decodeErr == nil && len(secret) == 32 {
			return secret
		}
	}
	secret := make([]byte, 32)
	if _, err := rand.Read(secret); err != nil {
		return nil
	}
	if err := os.MkdirAll(s.config, 0o700); err != nil {
		return nil
	}
	file, err := os.OpenFile(path, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if err == nil {
		_, writeErr := file.WriteString(hex.EncodeToString(secret) + "\n")
		syncErr := file.Sync()
		closeErr := file.Close()
		if writeErr == nil {
			writeErr = syncErr
		}
		if writeErr == nil {
			writeErr = closeErr
		}
		if writeErr == nil {
			return secret
		}
		return nil
	}
	// Another server may have initialized the shared secret between the read and
	// create.  Never replace it; reload the existing value instead.
	if raw, readErr := os.ReadFile(path); readErr == nil {
		secret, decodeErr := hex.DecodeString(strings.TrimSpace(string(raw)))
		if decodeErr == nil && len(secret) == 32 {
			return secret
		}
	}
	return nil
}

func (s *server) secureCookies() bool { return !s.httpMode }

func passwordMatches(password, stored string) bool {
	parts := strings.Split(stored, "$")
	if len(parts) != 4 || parts[0] != "pbkdf2_sha256" {
		return hmac.Equal([]byte(password), []byte(stored))
	}
	iterations, err := strconv.Atoi(parts[1])
	if err != nil || iterations < 1 {
		return false
	}
	salt := []byte(parts[2])
	digest := pbkdf2SHA256([]byte(password), salt, iterations, 32)
	expected, err := hex.DecodeString(parts[3])
	return err == nil && hmac.Equal(digest, expected)
}

func pbkdf2SHA256(password, salt []byte, iterations, length int) []byte {
	result := make([]byte, 0, length)
	for block := 1; len(result) < length; block++ {
		mac := hmac.New(sha256.New, password)
		_, _ = mac.Write(salt)
		mac.Write([]byte{byte(block >> 24), byte(block >> 16), byte(block >> 8), byte(block)})
		u := mac.Sum(nil)
		t := append([]byte(nil), u...)
		for i := 1; i < iterations; i++ {
			mac = hmac.New(sha256.New, password)
			_, _ = mac.Write(u)
			u = mac.Sum(nil)
			for j := range t {
				t[j] ^= u[j]
			}
		}
		result = append(result, t...)
	}
	return result[:length]
}

func (s *server) protected(next http.Handler, role string) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		user, ok := s.identity(r)
		if !ok {
			s.rejectUnauthorized(w, r)
			return
		}
		if !roleAllows(user, role) {
			s.rejectForbidden(w, user, role)
			return
		}
		// Basic auth is a compatibility path. Mint the port-scoped cookie before the
		// downstream handler writes headers so polling can move to cookie auth without
		// exposing credentials on every request. A logout marker deliberately blocks
		// this path until the user submits the login form again.
		if _, cookieErr := r.Cookie(s.authCookieName()); cookieErr != nil {
			if _, markerErr := r.Cookie("yolomux_logged_out"); markerErr != nil {
				if basicUser, basicOK := s.basicIdentity(r); basicOK {
					http.SetCookie(w, &http.Cookie{Name: s.authCookieName(), Value: s.cookieValue(basicUser), Path: "/", MaxAge: 90 * 24 * 60 * 60, HttpOnly: true, SameSite: http.SameSiteLaxMode, Secure: s.secureCookies()})
					http.SetCookie(w, &http.Cookie{Name: "yolomux_logged_out", Value: "", Path: "/", MaxAge: -1, HttpOnly: true, SameSite: http.SameSiteLaxMode, Secure: s.secureCookies()})
				}
			}
		}
		next.ServeHTTP(w, r)
	})
}

func requestWantsHTML(r *http.Request) bool {
	if r.URL.Path == "/" || r.URL.Path == "/login" {
		return true
	}
	accept := r.Header.Get("Accept")
	return strings.Contains(accept, "text/html") && !strings.Contains(accept, "application/json")
}

func roleAllows(user authUser, requiredRole string) bool {
	switch requiredRole {
	case "readonly":
		return user.role == "admin" || user.role == "readonly"
	case "admin":
		return user.role == "admin"
	default:
		return false
	}
}

func safeLoginNextValue(next string) string {
	if next == "" || !strings.HasPrefix(next, "/") || strings.HasPrefix(next, "//") || strings.ContainsAny(next, "\\\r\n") {
		return "/"
	}
	return next
}

func safeLoginNext(r *http.Request) string {
	return safeLoginNextValue(r.URL.RequestURI())
}

func (s *server) rejectUnauthorized(w http.ResponseWriter, r *http.Request) {
	if requestWantsHTML(r) {
		http.Redirect(w, r, "/login?next="+url.QueryEscape(safeLoginNext(r)), http.StatusSeeOther)
		return
	}
	jsonResponse(w, http.StatusUnauthorized, map[string]any{
		"user_message": map[string]any{
			"key": "auth.error.authenticationRequired", "params": map[string]any{}, "fallback": "authentication required",
		},
		"login_url": "/login?next=" + url.QueryEscape("/"),
	})
}

func (s *server) rejectForbidden(w http.ResponseWriter, user authUser, role string) {
	fallback := role + " access required"
	jsonResponse(w, http.StatusForbidden, map[string]any{
		"user_message": map[string]any{
			"key": "auth.error.accessRequired", "params": map[string]any{"role": role}, "fallback": fallback,
		},
		"role": user.role,
	})
}

func (s *server) authSetup(w http.ResponseWriter, _ *http.Request) {
	jsonResponse(w, http.StatusOK, map[string]any{"setup_required": len(s.users()) == 0})
}

func (s *server) login(w http.ResponseWriter, r *http.Request) {
	if r.Method == http.MethodGet {
		next := safeLoginNextValue(r.URL.Query().Get("next"))
		s.writePythonLogin(w, r, next, "")
		return
	}
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	r.Body = http.MaxBytesReader(w, r.Body, 16*1024)
	if err := r.ParseForm(); err != nil {
		jsonResponse(w, http.StatusBadRequest, typedError("invalid login request", "request.error.invalidForm", false))
		return
	}
	username, password := r.FormValue("username"), r.FormValue("password")
	if !s.loginAllowed(r, username) {
		jsonResponse(w, http.StatusTooManyRequests, typedError("too many requests", "auth.error.rateLimitedMinutes", true))
		return
	}
	for _, user := range s.users() {
		if user.username == username && passwordMatches(password, user.password) {
			s.loginSucceeded(r, username)
			http.SetCookie(w, &http.Cookie{Name: s.authCookieName(), Value: s.cookieValue(user), Path: "/", MaxAge: 90 * 24 * 60 * 60, HttpOnly: true, SameSite: http.SameSiteLaxMode, Secure: s.secureCookies()})
			http.SetCookie(w, &http.Cookie{Name: "yolomux_logged_out", Value: "", Path: "/", MaxAge: -1, HttpOnly: true, SameSite: http.SameSiteLaxMode, Secure: s.secureCookies()})
			next := safeLoginNextValue(r.FormValue("next"))
			http.Redirect(w, r, next, http.StatusSeeOther)
			return
		}
	}
	s.loginFailed(r, username)
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(http.StatusUnauthorized)
	s.writePythonLogin(w, r, r.FormValue("next"), "Invalid username or password.")
}

func (s *server) loginKey(r *http.Request, username string) string {
	peer, _, _ := strings.Cut(r.RemoteAddr, ":")
	if peer == "" {
		peer = r.RemoteAddr
	}
	return peer + "\x00" + boundedText(username, 128)
}

func (s *server) loginAllowed(r *http.Request, username string) bool {
	s.authMu.Lock()
	defer s.authMu.Unlock()
	if s.authFailures == nil {
		s.authFailures = map[string]authFailure{}
	}
	key := s.loginKey(r, username)
	entry, ok := s.authFailures[key]
	if !ok || time.Now().After(entry.expires) {
		return true
	}
	return entry.count < 5
}

func (s *server) loginFailed(r *http.Request, username string) {
	s.authMu.Lock()
	defer s.authMu.Unlock()
	if s.authFailures == nil {
		s.authFailures = map[string]authFailure{}
	}
	key := s.loginKey(r, username)
	entry := s.authFailures[key]
	if time.Now().After(entry.expires) {
		entry = authFailure{}
	}
	entry.count++
	entry.expires = time.Now().Add(time.Minute)
	s.authFailures[key] = entry
}

func (s *server) loginSucceeded(r *http.Request, username string) {
	s.authMu.Lock()
	defer s.authMu.Unlock()
	if s.authFailures != nil {
		delete(s.authFailures, s.loginKey(r, username))
	}
}

func (s *server) writePythonLogin(w http.ResponseWriter, r *http.Request, next, message string) {
	next = safeLoginNextValue(next)
	locale := ""
	if cookie, err := r.Cookie("yolomux_locale"); err == nil {
		locale = cookie.Value
	}
	script := `import sys
from yolomux_lib.web import login_html
print(login_html(next_path=sys.argv[1], error=sys.argv[2], secure=sys.argv[3] == "1", current_locale=sys.argv[4], accept_language=sys.argv[5]), end="")`
	secure := "0"
	if s.secureCookies() {
		secure = "1"
	}
	command := exec.Command("python3", "-c", script, next, message, secure, locale, r.Header.Get("Accept-Language"))
	command.Dir = s.root
	command.Env = append(os.Environ(), "PYTHONPATH="+s.root)
	body, err := command.Output()
	if err != nil {
		http.Error(w, "login page unavailable", http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	_, _ = w.Write(body)
}

func (s *server) logout(w http.ResponseWriter, r *http.Request) {
	for _, name := range []string{s.authCookieName(), "yolomux_auth"} {
		http.SetCookie(w, &http.Cookie{Name: name, Value: "", Path: "/", MaxAge: 0, HttpOnly: true, SameSite: http.SameSiteLaxMode, Secure: false, Expires: time.Unix(0, 0)})
		if s.secureCookies() {
			http.SetCookie(w, &http.Cookie{Name: name, Value: "", Path: "/", MaxAge: 0, HttpOnly: true, SameSite: http.SameSiteLaxMode, Secure: true, Expires: time.Unix(0, 0)})
		}
	}
	http.SetCookie(w, &http.Cookie{Name: "yolomux_logged_out", Value: "1", Path: "/", MaxAge: 90 * 24 * 60 * 60, HttpOnly: true, SameSite: http.SameSiteLaxMode, Secure: s.secureCookies()})
	http.Redirect(w, r, "/login", http.StatusSeeOther)
}

func tlsFiles(certPath, keyPath, host string, port int) (string, string, error) {
	if (certPath == "") != (keyPath == "") {
		return "", "", fmt.Errorf("--cert and --key must be provided together")
	}
	if certPath != "" {
		return certPath, keyPath, nil
	}
	dir := filepath.Join(os.TempDir(), "yolomux-go-tls-"+strconv.Itoa(port))
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return "", "", err
	}
	certFile := filepath.Join(dir, "self-signed.crt")
	keyFile := filepath.Join(dir, "self-signed.key")
	if _, certErr := os.Stat(certFile); certErr == nil {
		if _, keyErr := os.Stat(keyFile); keyErr == nil {
			return certFile, keyFile, nil
		}
	}
	privateKey, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		return "", "", err
	}
	serial, err := rand.Int(rand.Reader, new(big.Int).Lsh(big.NewInt(1), 120))
	if err != nil {
		return "", "", err
	}
	template := &x509.Certificate{
		SerialNumber:          serial,
		Subject:               pkix.Name{CommonName: host},
		NotBefore:             time.Now().Add(-time.Minute),
		NotAfter:              time.Now().Add(365 * 24 * time.Hour),
		KeyUsage:              x509.KeyUsageDigitalSignature | x509.KeyUsageKeyEncipherment,
		ExtKeyUsage:           []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
		BasicConstraintsValid: true,
	}
	if ip := net.ParseIP(host); ip != nil {
		template.IPAddresses = []net.IP{ip}
	} else {
		template.DNSNames = []string{host, "localhost"}
	}
	if host == "0.0.0.0" || host == "::" {
		template.DNSNames = append(template.DNSNames, "localhost")
		if interfaces, err := net.Interfaces(); err == nil {
			for _, iface := range interfaces {
				addresses, _ := iface.Addrs()
				for _, address := range addresses {
					text := strings.Split(address.String(), "/")[0]
					if ip := net.ParseIP(text); ip != nil && !ip.IsLoopback() {
						template.IPAddresses = append(template.IPAddresses, ip)
					}
				}
			}
		}
	}
	der, err := x509.CreateCertificate(rand.Reader, template, template, &privateKey.PublicKey, privateKey)
	if err != nil {
		return "", "", err
	}
	certBytes := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})
	keyBytes := pem.EncodeToMemory(&pem.Block{Type: "RSA PRIVATE KEY", Bytes: x509.MarshalPKCS1PrivateKey(privateKey)})
	if err := os.WriteFile(certFile, certBytes, 0o600); err != nil {
		return "", "", err
	}
	if err := os.WriteFile(keyFile, keyBytes, 0o600); err != nil {
		return "", "", err
	}
	return certFile, keyFile, nil
}

func tlsConfig(certFile, keyFile string) (*tls.Config, error) {
	certificate, err := tls.LoadX509KeyPair(certFile, keyFile)
	if err != nil {
		return nil, err
	}
	return &tls.Config{Certificates: []tls.Certificate{certificate}, MinVersion: tls.VersionTLS12}, nil
}

func logging(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		next.ServeHTTP(w, r)
		slog.Info("request", "method", r.Method, "path", r.URL.Path, "duration_ms", time.Since(start).Milliseconds())
	})
}

func (s *server) health(w http.ResponseWriter, _ *http.Request) {
	jsonResponse(w, http.StatusOK, map[string]any{"ok": true, "backend": "go", "version": version})
}

func (s *server) ping(w http.ResponseWriter, _ *http.Request) {
	jsonResponse(w, http.StatusOK, map[string]any{"ok": true, "backend": "go", "capabilities": map[string]any{"terminal.websocket.v1": true, "session.roster.v1": true, "session.create.v1": false, "session.kill.v1": false}})
}

func (s *server) clientEvents(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	flusher, ok := w.(http.Flusher)
	if !ok {
		http.Error(w, "streaming unavailable", http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "text/event-stream; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Connection", "keep-alive")
	w.Header().Set("X-Accel-Buffering", "no")
	writeSSE := func(event string, payload any) {
		data, _ := json.Marshal(payload)
		fmt.Fprintf(w, "event: %s\ndata: %s\n\n", event, data)
		flusher.Flush()
	}
	channels := normalizeEventChannels(r.URL.Query().Get("channels"))
	clientID := normalizeClientID(r.URL.Query().Get("client_id"))
	stream, unsubscribe := s.events.subscribeClient(clientID, channels...)
	defer unsubscribe()
	revisions := s.events.readySnapshotFor(stream)
	s.events.mu.Lock()
	epoch := s.events.epoch
	s.events.mu.Unlock()
	writeSSE("ready", map[string]any{"time": float64(time.Now().UnixNano()) / 1e9, "epoch": epoch, "resource_revisions": revisions})
	operationID := strings.TrimSpace(r.URL.Query().Get("operation_id"))
	demandedIDs := operationDemandIDs(operationID, r.URL.Query().Get("operations"))
	demanded := make(map[string]bool, len(demandedIDs))
	for _, id := range demandedIDs {
		demanded[id] = true
	}
	globalOperationStream := clientID != "" && operationID == ""
	if len(demandedIDs) > 0 {
		for _, id := range demandedIDs {
			if record := s.operationRecord(id); operationIsTerminal(record) && !record.Acknowledged && !record.DeliveryAcknowledged {
				if event := record.TerminalEvent; event != nil {
					writeSSE("operation_terminal", operationTerminalWire(event))
				}
			}
		}
	}
	if value, err := strconv.Atoi(strings.TrimSpace(os.Getenv("YOLOMUX_GO_EVENT_STREAM_DELAY_MS"))); err == nil && value > 0 {
		timer := time.NewTimer(time.Duration(value) * time.Millisecond)
		select {
		case <-timer.C:
		case <-r.Context().Done():
			if !timer.Stop() {
				<-timer.C
			}
			return
		}
	}
	ticker := time.NewTicker(15 * time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-r.Context().Done():
			return
		case event, ok := <-stream:
			if !ok {
				return
			}
			event = s.events.decorateEvent(stream, event)
			if event.Type == "operation_terminal" {
				operation, _ := event.Payload["operation"].(map[string]any)
				id, _ := operation["id"].(string)
				if !globalOperationStream && !demanded[id] {
					continue
				}
				writeSSE("operation_terminal", operationTerminalWire(event.Payload))
				continue
			}
			if operationID == "" && eventMatchesChannels(event.Type, channels) {
				writeSSE(event.Type, event)
			}
		case <-ticker.C:
			writeSSE("ping", map[string]any{"epoch": epoch})
			s.events.recordHeartbeat()
		}
	}
}

func operationTerminalWire(payload map[string]any) map[string]any {
	return map[string]any{"type": "operation_terminal", "time": float64(time.Now().UnixNano()) / 1e9, "payload": payload}
}

func normalizeEventChannels(raw string) []string {
	if strings.TrimSpace(raw) == "" {
		return append([]string(nil), eventChannels...)
	}
	allowed := map[string]bool{}
	for _, channel := range eventChannels {
		allowed[channel] = true
	}
	seen := map[string]bool{}
	result := []string{}
	for _, value := range strings.Split(raw, ",") {
		value = strings.TrimSpace(value)
		if allowed[value] && !seen[value] {
			seen[value] = true
			result = append(result, value)
		}
	}
	return result
}
func eventMatchesChannels(kind string, channels []string) bool {
	wanted, known := eventTypeChannels[kind]
	if !known {
		wanted = []string{"core"}
	}
	for _, a := range wanted {
		for _, b := range channels {
			if a == b {
				return true
			}
		}
	}
	return false
}
func mapKeys(values map[string]bool) []string {
	result := make([]string, 0, len(values))
	for key := range values {
		result = append(result, key)
	}
	sort.Strings(result)
	return result
}

func normalizeClientID(value string) string {
	runes := []rune(value)
	if len(runes) > 128 {
		runes = runes[:128]
	}
	result := strings.Builder{}
	for _, character := range runes {
		if unicode.IsLetter(character) || unicode.IsDigit(character) || strings.ContainsRune("._:-", character) {
			result.WriteRune(character)
		}
		if len([]rune(result.String())) >= 64 {
			break
		}
	}
	return result.String()
}

func minInt(a, b int) int {
	if a < b {
		return a
	}
	return b
}

func (b *eventBroker) nextEvent(ch chan clientEvent) (clientEvent, bool) {
	event, ok := <-ch
	if !ok {
		return clientEvent{}, false
	}
	b.mu.Lock()
	if latest, exists := b.pending[ch][event.Resource]; exists {
		event = latest
	}
	delete(b.pending[ch], event.Resource)
	if len(b.repair[ch]) > 0 {
		event.RepairResources = mapKeys(b.repair[ch])
		sort.Strings(event.RepairResources)
		b.repair[ch] = map[string]bool{}
	}
	b.mu.Unlock()
	return event, true
}

func (b *eventBroker) decorateEvent(ch chan clientEvent, event clientEvent) clientEvent {
	b.mu.Lock()
	defer b.mu.Unlock()
	if latest, exists := b.pending[ch][event.Resource]; exists {
		event = latest
	}
	delete(b.pending[ch], event.Resource)
	if len(b.repair[ch]) > 0 {
		event.RepairResources = mapKeys(b.repair[ch])
		sort.Strings(event.RepairResources)
		b.repair[ch] = map[string]bool{}
	}
	return event
}

func (b *eventBroker) snapshot() map[string]any {
	b.mu.Lock()
	defer b.mu.Unlock()
	channelCounts := map[string]int{}
	for _, selected := range b.channels {
		for channel := range selected {
			channelCounts[channel]++
		}
	}
	retained := make([]string, 0, len(b.retained))
	for resource := range b.retained {
		retained = append(retained, resource)
	}
	sort.Strings(retained)
	revisions := map[string]int{}
	for resource, revision := range b.revisions {
		revisions[resource] = revision
	}
	clients := make([]map[string]any, 0, len(b.clientIDs))
	for ch, clientID := range b.clientIDs {
		stats := b.clientStats[ch]
		client := map[string]any{"client_id": clientID, "channels": mapKeys(b.channels[ch])}
		if stats != nil {
			client["delivered_events"] = stats.deliveredEvents
			client["delivered_bytes"] = stats.deliveredBytes
			client["coalesced_events"] = stats.coalescedEvents
			client["dropped_events"] = stats.droppedEvents
		}
		clients = append(clients, client)
	}
	return map[string]any{
		"subscribers": len(b.subscribers), "channel_counts": channelCounts,
		"published_events": b.published, "published_bytes": b.publishedBytes,
		"delivered_events": b.delivered, "delivered_bytes": b.deliveredBytes,
		"filtered_events": b.filtered, "filtered_bytes": b.filteredBytes,
		"coalesced_events": b.coalesced, "dropped_events": b.dropped, "replayed_events": b.replayed,
		"heartbeat_events": b.heartbeats, "last_heartbeat_at": b.lastHeartbeatAt, "retained_resources": retained, "epoch": b.epoch,
		"resource_revisions": revisions, "published_by_type": metricSnapshot(b.publishedByType),
		"delivered_by_type": metricSnapshot(b.deliveredByType), "filtered_by_type": metricSnapshot(b.filteredByType),
		"published_by_resource": metricSnapshot(b.publishedByResource), "delivered_by_resource": metricSnapshot(b.deliveredByResource),
		"coalesced_by_resource": metricSnapshot(b.coalescedByResource), "dropped_by_resource": metricSnapshot(b.droppedByResource),
		"clients": clients,
	}
}

func (b *eventBroker) recordHeartbeat() {
	b.mu.Lock()
	b.heartbeats++
	b.lastHeartbeatAt = float64(time.Now().UnixNano()) / 1e9
	b.mu.Unlock()
}

func (s *server) operation(w http.ResponseWriter, r *http.Request) {
	id, err := url.PathUnescape(strings.TrimPrefix(r.URL.Path, "/api/operations/"))
	if err != nil {
		http.Error(w, "invalid operation id", http.StatusBadRequest)
		return
	}
	record := s.operationRecord(id)
	if record == nil {
		jsonResponse(w, http.StatusNotFound, operationNotFoundPayload(id))
		return
	}
	if operationIsTerminal(record) {
		jsonResponse(w, record.HTTPStatus, record.Result)
		return
	}
	if record.Receipt != nil {
		jsonResponse(w, http.StatusAccepted, record.Receipt)
		return
	}
	jsonResponse(w, http.StatusAccepted, map[string]any{"state": record.State, "request": map[string]any{"id": record.RequestID}, "operation": map[string]any{"id": record.ID, "cursor": map[string]any{"epoch": record.Epoch, "seq": record.Seq}}})
}

func operationNotFoundPayload(id string) map[string]any {
	requestID := fmt.Sprintf("r-%d", time.Now().UnixNano())
	return map[string]any{
		"state":   "failed",
		"request": map[string]any{"id": requestID},
		"error": map[string]any{
			"code":    "operation_not_found",
			"message": map[string]any{"key": "common.notFound", "params": map[string]any{}, "fallback": "operation not found"},
			"origin":  "server.http", "retryable": false,
			"details": map[string]any{"operation_id": id},
			"stack":   []any{map[string]any{"component": "server.http", "operation": "GET /api/operations/{id}", "code": "operation_not_found"}},
		},
	}
}

func (s *server) operationAccept(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	var body struct {
		ID         string         `json:"id"`
		RequestID  string         `json:"request_id"`
		Route      string         `json:"route"`
		Kind       string         `json:"kind"`
		DeadlineAt int64          `json:"deadline_at"`
		Progress   map[string]any `json:"progress"`
		Producer   map[string]any `json:"producer"`
		Context    map[string]any `json:"context"`
	}
	if err := decodeJSONBody(r.Body, 16*1024, &body); err != nil {
		w.Header().Set("Connection", "close")
		jsonResponse(w, http.StatusBadRequest, map[string]any{"error": "invalid operation acceptance"})
		return
	}
	if body.ID == "" || len(body.ID) > 128 {
		jsonResponse(w, http.StatusBadRequest, map[string]any{"error": "invalid operation acceptance"})
		return
	}
	if err := s.acceptOperationWithDetails(body.ID, body.RequestID, body.Route, body.Kind, body.DeadlineAt, body.Progress, body.Producer, body.Context); err != nil {
		jsonResponse(w, http.StatusConflict, map[string]any{"error": err.Error()})
		return
	}
	record := s.operationRecord(body.ID)
	jsonResponse(w, http.StatusAccepted, record.Receipt)
}

func (s *server) operationTerminalize(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	var body struct {
		ID     string         `json:"id"`
		Result map[string]any `json:"result"`
		Status int            `json:"status"`
	}
	if err := decodeJSONBody(r.Body, 64*1024, &body); err != nil {
		w.Header().Set("Connection", "close")
		jsonResponse(w, http.StatusBadRequest, map[string]any{"error": "invalid operation terminalization"})
		return
	}
	if body.ID == "" || len(body.ID) > 128 || body.Result == nil {
		jsonResponse(w, http.StatusBadRequest, map[string]any{"error": "invalid operation terminalization"})
		return
	}
	if body.Status == 0 {
		body.Status = http.StatusOK
	}
	event, err := s.terminalizeOperation(body.ID, body.Result, body.Status)
	if err != nil {
		jsonResponse(w, http.StatusConflict, map[string]any{"error": err.Error()})
		return
	}
	jsonResponse(w, http.StatusOK, event.Payload)
}
func (s *server) operationCancel(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", 405)
		return
	}
	id := r.URL.Query().Get("id")
	if id == "" {
		id = r.FormValue("id")
	}
	if err := s.cancelOperation(id); err != nil {
		jsonResponse(w, http.StatusConflict, map[string]any{"error": err.Error()})
		return
	}
	jsonResponse(w, http.StatusOK, map[string]any{"ok": true, "id": id, "state": "cancelled"})
}
func (s *server) operationAck(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	acks, err := decodeOperationAcks(r.Body)
	if err != nil {
		w.Header().Set("Connection", "close")
		jsonResponse(w, http.StatusBadRequest, map[string]any{"error": "operation acknowledgments required"})
		return
	}
	if len(acks) < 1 || len(acks) > 64 {
		jsonResponse(w, http.StatusBadRequest, map[string]any{"error": "operation acknowledgments required"})
		return
	}
	acknowledged := []string{}
	ignored := []string{}
	seen := map[string]bool{}
	valid := make([]operationAck, 0, len(acks))
	s.operationsMu.Lock()
	defer s.operationsMu.Unlock()
	for _, ack := range acks {
		id, _ := ack["id"].(string)
		cursor, _ := ack["cursor"].(map[string]any)
		epoch, _ := cursor["epoch"].(string)
		seq, seqOK := cursor["seq"].(int)
		if id == "" || len(id) > 128 || epoch == "" || len(epoch) > 128 || !seqOK || seq <= 0 || seen[id] {
			jsonResponse(w, http.StatusBadRequest, map[string]any{"error": "invalid operation acknowledgment"})
			return
		}
		seen[id] = true
		record := s.operations[id]
		if record == nil || !operationCursorMatches(record, epoch, seq) {
			ignored = append(ignored, id)
			continue
		}
		if record.DeliveryAcknowledged {
			acknowledged = append(acknowledged, id)
			continue
		}
		valid = append(valid, operationAck{ID: id, Cursor: cursor})
	}
	if len(valid) > 0 {
		if err := appendOperationJournal(s.operationsJournal, map[string]any{"version": operationAckJournalVersion, "type": "ack", "epoch": s.events.epoch, "acks": valid}); err != nil {
			for _, ack := range valid {
				ignored = append(ignored, ack.ID)
			}
			valid = nil
		} else {
			for _, ack := range valid {
				record := s.operations[ack.ID]
				record.Acknowledged = true
				record.DeliveryAcknowledged = true
				acknowledged = append(acknowledged, ack.ID)
			}
			s.boundOperationReplaysLocked()
			s.pruneOperationsLocked()
			if err := s.saveOperationsLocked(); err != nil {
				s.logState().emit("error", "operations", "operation acknowledgement snapshot failed")
			}
		}
	}
	operations := cloneOperationMap(s.operations)
	var compactErr error
	if len(valid) > 0 {
		compactErr = compactOperationJournal(s.operationsJournal, operations)
	}
	jsonResponse(w, http.StatusOK, map[string]any{"ok": true, "acknowledged": acknowledged, "ignored": ignored})
	if compactErr != nil {
		s.logState().emit("error", "operations", "operation journal compaction failed")
	}
}

func operationCursorMatches(record *goOperation, epoch string, seq int) bool {
	if record == nil || record.Epoch != epoch || record.Seq != seq || record.TerminalEvent == nil {
		return false
	}
	operation, _ := record.TerminalEvent["operation"].(map[string]any)
	cursor, _ := operation["cursor"].(map[string]any)
	eventEpoch, _ := cursor["epoch"].(string)
	if eventEpoch != epoch {
		return false
	}
	switch value := cursor["seq"].(type) {
	case int:
		return value == seq
	case float64:
		return value == float64(seq)
	default:
		return false
	}
}

func decodeOperationAcks(reader io.Reader) ([]map[string]any, error) {
	data, err := io.ReadAll(io.LimitReader(reader, 16*1024+1))
	if err != nil || len(data) > 16*1024 {
		return nil, fmt.Errorf("acknowledgement body too large")
	}
	var body map[string]json.RawMessage
	decoder := json.NewDecoder(bytes.NewReader(data))
	if err := decoder.Decode(&body); err != nil || len(body) != 1 {
		return nil, fmt.Errorf("invalid acknowledgement body")
	}
	var trailing any
	if err := decoder.Decode(&trailing); err != io.EOF {
		return nil, fmt.Errorf("trailing acknowledgement data")
	}
	var rawAcks []map[string]json.RawMessage
	if err := json.Unmarshal(body["acks"], &rawAcks); err != nil {
		return nil, err
	}
	acks := make([]map[string]any, 0, len(rawAcks))
	for _, raw := range rawAcks {
		if len(raw) != 2 {
			return nil, fmt.Errorf("invalid acknowledgement fields")
		}
		idRaw, idOK := raw["id"]
		cursorRaw, cursorOK := raw["cursor"]
		if !idOK || !cursorOK {
			return nil, fmt.Errorf("invalid acknowledgement fields")
		}
		var id string
		var cursor map[string]json.RawMessage
		if json.Unmarshal(idRaw, &id) != nil || json.Unmarshal(cursorRaw, &cursor) != nil || len(cursor) != 2 {
			return nil, fmt.Errorf("invalid acknowledgement cursor")
		}
		var epoch string
		var seq int
		epochRaw, epochOK := cursor["epoch"]
		seqRaw, seqOK := cursor["seq"]
		if !epochOK || !seqOK || json.Unmarshal(epochRaw, &epoch) != nil || json.Unmarshal(seqRaw, &seq) != nil {
			return nil, fmt.Errorf("invalid acknowledgement cursor")
		}
		acks = append(acks, map[string]any{"id": id, "cursor": map[string]any{"epoch": epoch, "seq": seq}})
	}
	return acks, nil
}
func (s *server) backgroundStatus(w http.ResponseWriter, _ *http.Request) {
	jsonResponse(w, http.StatusOK, s.backgroundState().statusPayload())
}

func (s *server) backgroundState() *goBackgroundOwner {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.background == nil {
		s.background = newGoBackgroundOwner(s.config, s.root)
	}
	if s.events != nil {
		s.background.setEventPublisher(func(kind string, payload map[string]any) error {
			_, err := s.events.publish(kind, payload)
			return err
		})
	}
	return s.background
}

func (s *server) logState() *goLogRing {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.logRing == nil {
		s.logRing = newGoLogRing()
		s.logRing.emit("info", "go", "server diagnostics initialized")
	}
	return s.logRing
}

func (s *server) backgroundClaim(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	payload := s.backgroundState().claim()
	status := http.StatusOK
	if ok, exists := payload["ok"].(bool); exists && !ok {
		status = http.StatusConflict
	}
	jsonResponse(w, status, payload)
}
func (s *server) logs(w http.ResponseWriter, _ *http.Request) {
	jsonResponse(w, http.StatusOK, s.logState().payload())
}
func (s *server) eventsList(w http.ResponseWriter, r *http.Request) {
	limit := 120
	if value, err := strconv.Atoi(r.URL.Query().Get("limit")); err == nil && value > 0 && value < 1000 {
		limit = value
	}
	s.events.mu.Lock()
	start := len(s.events.events) - limit
	if start < 0 {
		start = 0
	}
	events := make([]clientEvent, len(s.events.events[start:]))
	copy(events, s.events.events[start:])
	s.events.mu.Unlock()
	jsonResponse(w, http.StatusOK, map[string]any{"state": "ready", "events": events, "limit": limit})
}
func (s *server) systemStatus(w http.ResponseWriter, r *http.Request) {
	eventSnapshot := map[string]any{}
	if s.events != nil {
		s.events.mu.Lock()
		eventEpoch := s.events.epoch
		s.events.mu.Unlock()
		eventSnapshot["epoch"] = eventEpoch
		eventSnapshot["resource_revisions"] = s.events.readySnapshot()
	}
	operationCount := 0
	terminalCount := 0
	s.operationsMu.Lock()
	for _, record := range s.operations {
		operationCount++
		if operationIsTerminal(record) {
			terminalCount++
		}
	}
	s.operationsMu.Unlock()
	epoch := ""
	if s.events != nil {
		s.events.mu.Lock()
		epoch = s.events.epoch
		s.events.mu.Unlock()
	}
	payload := map[string]any{
		"schema": "system-status-snapshot", "state": "ready", "epoch": epoch,
		"server":    map[string]any{"backend": "go", "version": version, "pid": os.Getpid(), "root": s.root},
		"refresh":   map[string]any{"events": eventSnapshot, "operations": map[string]any{"total": operationCount, "terminal": terminalCount}},
		"services":  map[string]any{"events": "ready", "operations": "ready", "background": s.backgroundState().statusPayload()},
		"resources": map[string]any{"event_broker": eventSnapshot, "operation_records": operationCount},
	}
	if strings.HasSuffix(r.URL.Path, "/advanced") {
		payload["advanced"] = true
		payload["diagnostics"] = map[string]any{"events": s.events.snapshot(), "logs": s.logState().payload(), "operations": map[string]any{"records": operationCount, "terminal": terminalCount}}
	}
	jsonResponse(w, http.StatusOK, payload)
}
func (s *server) eventPost(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	var body map[string]any
	if err := decodeJSONBody(r.Body, 64*1024, &body); err != nil {
		w.Header().Set("Connection", "close")
		jsonResponse(w, http.StatusBadRequest, map[string]any{"error": "invalid event"})
		return
	}
	if body == nil {
		jsonResponse(w, http.StatusBadRequest, map[string]any{"error": "invalid event"})
		return
	}
	session, _ := body["session"].(string)
	if rawSession, exists := body["session"]; exists && rawSession != nil {
		if _, ok := rawSession.(string); !ok {
			jsonResponse(w, http.StatusBadRequest, map[string]any{"error": "invalid event"})
			return
		}
	}
	eventType, typeOK := body["type"].(string)
	message, messageOK := body["message"].(string)
	if !typeOK || strings.TrimSpace(eventType) == "" || !messageOK || strings.TrimSpace(message) == "" {
		jsonResponse(w, http.StatusBadRequest, map[string]any{"error": "invalid event"})
		return
	}
	if session != "" && s.tmuxCommand("has-session", "-t", session).Run() != nil {
		jsonResponse(w, http.StatusNotFound, map[string]any{"error": "unknown session", "session": session})
		return
	}
	details, _ := body["details"].(map[string]any)
	record := map[string]any{
		"time":    utcEventTime(),
		"session": session,
		"type":    boundedText(eventType, 256),
		"details": safeEventDetails(details),
		"message": boundedText(message, 2000),
	}
	if err := s.appendEventLog(record); err != nil {
		jsonResponse(w, http.StatusServiceUnavailable, map[string]any{"error": "event persistence failed", "retryable": true})
		return
	}
	_, err := s.events.publish("event_log_changed", map[string]any{"session": session})
	if err != nil {
		jsonResponse(w, http.StatusServiceUnavailable, map[string]any{"error": "event persistence failed", "retryable": true})
		return
	}
	jsonResponse(w, http.StatusOK, map[string]any{"ok": true, "event": record})
}

func utcEventTime() string { return time.Now().UTC().Format("2006-01-02T15:04:05Z") }

func boundedText(value string, limit int) string {
	characters := []rune(value)
	if len(characters) > limit {
		characters = characters[:limit]
	}
	return string(characters)
}

func safeEventDetails(details map[string]any) map[string]any {
	result := map[string]any{}
	count := 0
	for key, value := range details {
		if count >= 64 {
			break
		}
		cleanKey := boundedText(key, 128)
		switch item := value.(type) {
		case nil:
			continue
		case string:
			result[cleanKey] = boundedText(item, 2000)
		case bool, float64:
			result[cleanKey] = item
		case []any:
			values := make([]any, 0, minInt(len(item), 20))
			for _, entry := range item {
				switch scalar := entry.(type) {
				case string:
					values = append(values, boundedText(scalar, 1000))
				case bool, float64:
					values = append(values, scalar)
				}
			}
			result[cleanKey] = values
		case map[string]any:
			nested := map[string]any{}
			for nestedKey, nestedValue := range item {
				switch scalar := nestedValue.(type) {
				case string:
					nested[boundedText(nestedKey, 128)] = boundedText(scalar, 1000)
				case bool, float64:
					nested[boundedText(nestedKey, 128)] = scalar
				}
			}
			result[cleanKey] = nested
		default:
			continue
		}
		count++
	}
	return result
}

func (s *server) appendEventLog(record map[string]any) error {
	path := s.eventLogPath
	if path == "" && s.config != "" {
		path = filepath.Join(s.config, "event-log.jsonl")
	}
	if path == "" {
		return nil
	}
	data, err := json.Marshal(record)
	if err != nil {
		return err
	}
	return withFileLock(path, func() error {
		if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
			return err
		}
		file, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0600)
		if err != nil {
			return err
		}
		if _, err = file.Write(append(data, '\n')); err == nil {
			err = file.Sync()
		}
		closeErr := file.Close()
		if err == nil {
			err = closeErr
		}
		return err
	})
}

func (s *server) tmux(w http.ResponseWriter, _ *http.Request) {
	out, err := s.tmuxCommand("list-sessions", "-F", "#{session_name}").Output()
	sessions := []string{}
	if err == nil {
		for _, line := range strings.Split(strings.TrimSpace(string(out)), "\n") {
			if line != "" {
				sessions = append(sessions, line)
			}
		}
	}
	jsonResponse(w, http.StatusOK, map[string]any{"sessions": sessions, "backend": "go"})
}

func (s *server) sessionExists(w http.ResponseWriter, r *http.Request) {
	name := r.URL.Query().Get("session")
	err := s.tmuxCommand("has-session", "-t", name).Run()
	jsonResponse(w, http.StatusOK, map[string]any{"session": name, "exists": err == nil})
}

func (s *server) ensureSession(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	name := r.URL.Query().Get("session")
	if name == "" {
		name = r.FormValue("session")
	}
	if name == "" {
		jsonResponse(w, http.StatusBadRequest, map[string]any{"error": "missing session"})
		return
	}
	exists := s.tmuxCommand("has-session", "-t", name).Run() == nil
	if !exists {
		jsonResponse(w, http.StatusNotFound, map[string]any{"ok": false, "created": false, "session": name, "error": "unknown session"})
		return
	}
	jsonResponse(w, http.StatusOK, map[string]any{"ok": true, "created": false, "session": name})
}

func (s *server) createSession(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	name := r.URL.Query().Get("session")
	if name == "" {
		name = r.FormValue("session")
	}
	if name == "" {
		name = "yo-" + strconv.FormatInt(time.Now().UnixNano(), 10)
	}
	if err := s.tmuxCommand("new-session", "-d", "-s", name).Run(); err != nil {
		jsonResponse(w, http.StatusBadRequest, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	jsonResponse(w, http.StatusOK, map[string]any{"ok": true, "created": true, "session": name, "sessions": s.sessionNames()})
}

func (s *server) createSessionPlan(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	name := "yo-" + strconv.FormatInt(time.Now().UnixNano(), 10)
	jsonResponse(w, http.StatusOK, map[string]any{
		"ok": true, "session": name, "generation": time.Now().UnixNano(), "backend": "go",
	})
}

func (s *server) unsupported(w http.ResponseWriter, _ *http.Request) {
	jsonResponse(w, http.StatusNotImplemented, map[string]any{"error": "feature_unavailable", "reason": map[string]string{"code": "not_implemented", "messageKey": "capabilityUnavailable"}})
}

func (s *server) killSession(w http.ResponseWriter, r *http.Request) {
	name := r.URL.Query().Get("session")
	if name == "" {
		name = r.FormValue("session")
	}
	err := s.tmuxCommand("kill-session", "-t", name).Run()
	if err != nil {
		jsonResponse(w, http.StatusBadRequest, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	jsonResponse(w, http.StatusOK, map[string]any{"ok": true, "session": name})
}

func (s *server) index(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path != "/" {
		http.NotFound(w, r)
		return
	}
	user, _ := s.identity(r)
	s.writePythonHome(w, r, user)
}

func (s *server) writePythonHome(w http.ResponseWriter, r *http.Request, user authUser) {
	script := `import json,sys
from yolomux_lib.web import html_page
print(html_page(json.loads(sys.argv[1]), access_role=sys.argv[2], auth_username=sys.argv[3], accept_language=sys.argv[4], terminal_only=True), end="")`
	sessions, _ := json.Marshal(s.sessionNames())
	command := exec.Command("python3", "-c", script, string(sessions), user.role, user.username, r.Header.Get("Accept-Language"))
	command.Dir = s.root
	command.Env = append(os.Environ(), "PYTHONPATH="+s.root)
	body, err := command.Output()
	if err != nil {
		http.Error(w, "application unavailable", http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	_, _ = w.Write(body)
}

func (s *server) static(w http.ResponseWriter, r *http.Request) {
	relative := filepath.Clean(strings.TrimPrefix(r.URL.Path, "/static/"))
	staticRoot := filepath.Join(s.root, "static")
	path := filepath.Join(staticRoot, relative)
	if path != staticRoot && !strings.HasPrefix(path, staticRoot+string(filepath.Separator)) {
		http.NotFound(w, r)
		return
	}
	http.ServeFile(w, r, path)
}

func (s *server) sessionNames() []string {
	out, err := s.tmuxCommand("list-sessions", "-F", "#{session_name}").Output()
	if err != nil {
		return []string{}
	}
	var result []string
	for _, line := range strings.Split(strings.TrimSpace(string(out)), "\n") {
		if line != "" {
			result = append(result, line)
		}
	}
	return result
}

func (s *server) tmuxCommand(args ...string) *exec.Cmd {
	values := []string{}
	if socket := strings.TrimSpace(os.Getenv("YOLOMUX_TMUX_SOCKET")); socket != "" {
		values = append(values, "-S", socket)
	}
	return exec.Command("tmux", append(values, args...)...)
}

func (s *server) exactTarget(session string) string { return "=" + session + ":" }

func (s *server) supportsIgnoreSize() bool {
	out, err := s.tmuxCommand("attach-session", "-?").CombinedOutput()
	return err == nil && strings.Contains(string(out), "ignore-size")
}

func (s *server) configureTmuxSession(session string) {
	target := s.exactTarget(session)
	for _, args := range [][]string{{"set-option", "-s", "set-clipboard", "on"}, {"set-option", "-t", target, "window-size", "largest"}, {"set-option", "-wg", "aggressive-resize", "on"}} {
		_ = s.tmuxCommand(args...).Run()
	}
}

func (s *server) claimResizeAuthority(session, activeClient string, cols, rows int) {
	if activeClient == "" || !s.supportsIgnoreSize() {
		return
	}
	target := s.exactTarget(session)
	format := "#{client_tty}\t#{client_session}\t#{client_width}\t#{client_height}"
	out, err := s.tmuxCommand("list-clients", "-F", format).Output()
	if err != nil {
		return
	}
	for _, line := range strings.Split(strings.TrimSpace(string(out)), "\n") {
		fields := strings.Split(line, "\t")
		if len(fields) != 4 || fields[1] != session {
			continue
		}
		client := fields[0]
		width, _ := strconv.Atoi(fields[2])
		height, _ := strconv.Atoi(fields[3])
		if client == activeClient || (width <= cols && height <= rows) {
			_ = s.tmuxCommand("set-option", "-t", client, "-f", "ignore-size", "off").Run()
		} else {
			_ = s.tmuxCommand("set-option", "-t", client, "-f", "ignore-size", "on").Run()
		}
	}
	_ = s.tmuxCommand("refresh-client", "-t", target).Run()
}

func (s *server) refreshAfterAttach(session string) {
	target := "=" + session + ":"
	_ = s.tmuxCommand("refresh-client", "-t", target).Run()
	for _, delay := range []time.Duration{100 * time.Millisecond, 500 * time.Millisecond} {
		go func(wait time.Duration) {
			time.Sleep(wait)
			_ = s.tmuxCommand("refresh-client", "-t", target).Run()
		}(delay)
	}
}

func (s *server) websocket(w http.ResponseWriter, r *http.Request) {
	user, ok := s.identity(r)
	if !ok {
		http.Error(w, "authentication required", http.StatusUnauthorized)
		return
	}
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	if r.Header.Get("Origin") != "" && r.Header.Get("Origin") != "http://"+r.Host && r.Header.Get("Origin") != "https://"+r.Host {
		http.Error(w, "origin denied", http.StatusForbidden)
		return
	}
	session := r.URL.Query().Get("session")
	if session == "" || s.tmuxCommand("has-session", "-t", session).Run() != nil {
		http.Error(w, "unknown session: "+session, http.StatusNotFound)
		return
	}
	readonly := user.role != "admin"
	clientID := strings.TrimSpace(r.URL.Query().Get("client"))
	key := r.Header.Get("Sec-WebSocket-Key")
	if key == "" {
		http.Error(w, "missing Sec-WebSocket-Key", http.StatusBadRequest)
		return
	}
	if r.Header.Get("Sec-WebSocket-Version") != "13" {
		http.Error(w, "unsupported websocket version", http.StatusBadRequest)
		return
	}
	hijack, ok := w.(http.Hijacker)
	if !ok {
		http.Error(w, "websocket unavailable", http.StatusInternalServerError)
		return
	}
	conn, rw, err := hijack.Hijack()
	if err != nil {
		return
	}
	accept := sha1.Sum([]byte(key + wsGUID))
	fmt.Fprintf(rw, "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: %s\r\n\r\n", base64.StdEncoding.EncodeToString(accept[:]))
	if err := rw.Flush(); err != nil {
		conn.Close()
		return
	}
	s.bridge(&wsConn{Conn: conn}, session, readonly, clientID)
}

func (s *server) bridge(conn *wsConn, session string, readonly bool, clientID string) {
	defer conn.Close()
	rows, cols := 36, 120
	reader := bufio.NewReader(conn)
	_ = conn.SetReadDeadline(time.Now().Add(750 * time.Millisecond))
	var pending [][]byte
	for {
		opcode, payload, err := readFrame(reader)
		if err != nil {
			break
		}
		if opcode == 8 {
			return
		}
		if opcode == 9 {
			_ = conn.frame(10, payload)
			continue
		}
		if opcode != 1 && opcode != 2 {
			continue
		}
		var message struct {
			Type       string `json:"type"`
			Rows       int    `json:"rows"`
			Cols       int    `json:"cols"`
			Foreground *bool  `json:"foreground"`
		}
		if json.Unmarshal(payload, &message) == nil && message.Type == "resize" {
			if message.Foreground == nil || *message.Foreground {
				rows = clampDimension(message.Rows, 2, 300, rows)
				cols = clampDimension(message.Cols, 20, 500, cols)
			}
			continue
		}
		pending = append(pending, payload)
		break
	}
	_ = conn.SetReadDeadline(time.Time{})
	pty, err := openPTY(rows, cols)
	if err != nil {
		slog.Error("pty allocation failed", "session", session, "error", err)
		return
	}
	defer pty.close()
	s.configureTmuxSession(session)
	args := []string{"attach-session", "-t", session}
	if s.supportsIgnoreSize() {
		args = []string{"attach-session", "-f", "ignore-size", "-t", s.exactTarget(session)}
	}
	if readonly {
		args = []string{"attach-session", "-r", "-t", s.exactTarget(session)}
		if s.supportsIgnoreSize() {
			args = []string{"attach-session", "-r", "-f", "ignore-size", "-t", s.exactTarget(session)}
		}
	}
	cmd := s.tmuxCommand(args...)
	cmd.SysProcAttr = &syscall.SysProcAttr{
		Setsid:  true,
		Setctty: true,
		Ctty:    0,
	}
	cmd.Env = append(os.Environ(), "TERM=xterm-256color")
	cmd.Stdin = pty.slave
	cmd.Stdout = pty.slave
	cmd.Stderr = pty.slave
	if err := cmd.Start(); err != nil {
		slog.Error("tmux attach failed", "session", session, "error", err)
		return
	}
	activeTTY := pty.slave.Name()
	s.claimResizeAuthority(session, activeTTY, cols, rows)
	s.refreshAfterAttach(session)
	s.mu.Lock()
	s.children[cmd] = struct{}{}
	s.mu.Unlock()
	defer func() {
		if cmd.ProcessState == nil {
			_ = syscall.Kill(-cmd.Process.Pid, syscall.SIGTERM)
			_ = cmd.Process.Kill()
		}
		err := cmd.Wait()
		slog.Info("tmux attach exited", "session", session, "error", err)
		s.mu.Lock()
		delete(s.children, cmd)
		s.mu.Unlock()
	}()
	go func() {
		buf := make([]byte, 32*1024)
		for {
			n, err := pty.master.Read(buf)
			if n > 0 {
				_ = conn.frame(2, buf[:n])
			}
			if err != nil {
				return
			}
		}
	}()
	_ = s.tmuxCommand("refresh-client", "-t", "="+session+":").Run()
	for _, payload := range pending {
		s.handleMessage(pty.master, pty, cmd, conn, session, clientID, payload, !readonly)
	}
	for {
		opcode, payload, err := readFrame(reader)
		if err != nil {
			break
		}
		if opcode == 8 {
			return
		}
		if opcode == 9 {
			_ = conn.frame(10, payload)
			continue
		}
		if opcode != 1 && opcode != 2 {
			continue
		}
		s.handleMessage(pty.master, pty, cmd, conn, session, clientID, payload, !readonly)
	}
}

func (s *server) handleMessage(stdin io.Writer, pty *ptyPair, process *exec.Cmd, conn *wsConn, session, clientID string, payload []byte, writable bool) {
	var msg struct {
		Type       string `json:"type"`
		Data       string `json:"data"`
		Rows       int    `json:"rows"`
		Cols       int    `json:"cols"`
		Txn        int    `json:"txn"`
		Direction  string `json:"direction"`
		Lines      int    `json:"lines"`
		Reason     string `json:"reason"`
		Foreground *bool  `json:"foreground"`
		Activate   bool   `json:"activate"`
	}
	if json.Unmarshal(payload, &msg) != nil {
		if writable {
			_, _ = stdin.Write(payload)
		}
		return
	}
	if !writable && msg.Type != "refresh" {
		return
	}
	switch msg.Type {
	case "input":
		_, _ = io.WriteString(stdin, msg.Data)
	case "resize":
		if msg.Foreground != nil && !*msg.Foreground {
			return
		}
		if msg.Rows > 0 && msg.Cols > 0 {
			rows := clampDimension(msg.Rows, 2, 300, 36)
			cols := clampDimension(msg.Cols, 20, 500, 120)
			if msg.Activate || (msg.Foreground != nil && *msg.Foreground) {
				s.claimResizeAuthority(session, pty.slave.Name(), cols, rows)
			}
			if pty.resize(rows, cols) == nil && process.Process != nil {
				_ = process.Process.Signal(syscall.SIGWINCH)
			}
		}
	case "tmux-scroll":
		if (msg.Direction == "up" || msg.Direction == "down") && msg.Lines > 0 {
			lines := clampDimension(msg.Lines, 1, 80, 1)
			if msg.Direction == "up" {
				_ = s.tmuxCommand("copy-mode", "-e", "-t", s.exactTarget(session)).Run()
			}
			key := "scroll-down"
			if msg.Direction == "up" {
				key = "scroll-up"
			}
			_ = s.tmuxCommand("send-keys", "-X", "-N", strconv.Itoa(lines), key, "-t", s.exactTarget(session)).Run()
		}
	case "refresh":
		_ = s.tmuxCommand("refresh-client", "-t", "="+session+":").Run()
		if msg.Txn > 0 {
			b, _ := json.Marshal(map[string]any{"type": "refresh-ack", "txn": msg.Txn})
			_ = conn.frame(1, b)
		}
	}
}

func shellQuote(value string) string { return "'" + strings.ReplaceAll(value, "'", "'\\''") + "'" }

func (s *server) reaper() {
	for {
		time.Sleep(10 * time.Second)
		s.mu.Lock()
		for cmd := range s.children {
			if cmd.ProcessState != nil {
				delete(s.children, cmd)
			}
		}
		s.mu.Unlock()
	}
}

func jsonResponse(w http.ResponseWriter, status int, value any) {
	b, _ := json.Marshal(value)
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	_, _ = w.Write(b)
}

func typedError(fallback, key string, retryable bool) map[string]any {
	return map[string]any{
		"error": fallback,
		"user_message": map[string]any{
			"key": key, "params": map[string]any{}, "fallback": fallback,
		},
		"retryable": retryable,
	}
}

func decodeJSONBody(reader io.Reader, limit int64, destination any) error {
	data, err := io.ReadAll(io.LimitReader(reader, limit+1))
	if err != nil || int64(len(data)) > limit {
		return fmt.Errorf("request body exceeds limit")
	}
	decoder := json.NewDecoder(bytes.NewReader(data))
	if err := decoder.Decode(destination); err != nil {
		return err
	}
	var trailing any
	if err := decoder.Decode(&trailing); err != io.EOF {
		return fmt.Errorf("trailing request data")
	}
	return nil
}

func readFrame(r *bufio.Reader) (byte, []byte, error) {
	b1, err := r.ReadByte()
	if err != nil {
		return 0, nil, err
	}
	b2, err := r.ReadByte()
	if err != nil {
		return 0, nil, err
	}
	opcode := b1 & 0x0f
	masked := b2&0x80 != 0
	length := int64(b2 & 0x7f)
	if length == 126 {
		var x uint16
		if err := binaryRead(r, &x); err != nil {
			return 0, nil, err
		}
		length = int64(x)
	} else if length == 127 {
		var x uint64
		if err := binaryRead(r, &x); err != nil {
			return 0, nil, err
		}
		if x > 16<<20 {
			return 0, nil, fmt.Errorf("frame too large")
		}
		length = int64(x)
	}
	if length > 16<<20 {
		return 0, nil, fmt.Errorf("frame too large")
	}
	var mask [4]byte
	if masked {
		if _, err := io.ReadFull(r, mask[:]); err != nil {
			return 0, nil, err
		}
	}
	p := make([]byte, length)
	if _, err := io.ReadFull(r, p); err != nil {
		return 0, nil, err
	}
	if masked {
		for i := range p {
			p[i] ^= mask[i%4]
		}
	}
	return opcode, p, nil
}

func binaryRead(r io.Reader, data any) error { return binary.Read(r, binary.BigEndian, data) }
func writeFrame(w io.Writer, opcode byte, payload []byte) error {
	h := []byte{0x80 | opcode}
	n := len(payload)
	if n < 126 {
		h = append(h, byte(n))
	} else if n <= 65535 {
		h = append(h, 126, byte(n>>8), byte(n))
	} else {
		h = append(h, 127, 0, 0, 0, 0, 0, 0, 0, 0, byte(n>>24), byte(n>>16), byte(n>>8), byte(n))
	}
	_, err := w.Write(append(h, payload...))
	return err
}

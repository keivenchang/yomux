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
	"errors"
	"flag"
	"fmt"
	"io"
	"log/slog"
	"math"
	"math/big"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"runtime"
	"sort"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"
	"unicode"

	"github.com/keivenchang/yomux/internal/capabilities"
	"github.com/keivenchang/yomux/internal/finder"
	"github.com/keivenchang/yomux/internal/stats"
	"github.com/keivenchang/yomux/internal/work"
)

const version = "0.10.1"
const wsGUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
const maxYolomuxSessionTabs = 99
const goServiceID = "yomux-go"

func goRuntimeLabel() string {
	return fmt.Sprintf("Go %s %s/%s", strings.TrimPrefix(runtime.Version(), "go"), runtime.GOOS, runtime.GOARCH)
}

type server struct {
	runtimeMemory                      runtimeMemoryState
	root                               string
	host                               string
	port                               int
	sessionFilesListener               string
	dangerouslyYolo                    bool
	config                             string
	httpMode                           bool
	mu                                 sync.Mutex
	children                           map[*exec.Cmd]struct{}
	events                             *eventBroker
	operationsMu                       sync.Mutex
	operations                         map[string]*goOperation
	operationsPath                     string
	operationsJournal                  string
	eventLogPath                       string
	background                         *goBackgroundOwner
	finder                             *finder.Service
	finderJobs                         *work.Scheduler
	finderBulkJobs                     *work.Scheduler
	finderStreamJobs                   *work.Scheduler
	capabilities                       *capabilities.Catalog
	statsPath                          string
	statsNow                           func() time.Time
	statsHeartbeat                     time.Duration
	statsWriter                        *stats.Writer
	statsService                       *stats.StatsService
	statsLifecycle                     *stats.Lifecycle
	statsRuntimeRecorder               *statsRuntimeRecorder
	syntheticHostInputs                bool
	statsMigration                     map[string]any
	statsProcessSourceOnce             sync.Once
	statsProcessSource                 string
	statsDeltaMu                       sync.Mutex
	statsDeltaRevisions                map[statsDeltaKey]map[int64]int64
	statsDeltaNextRevision             map[statsDeltaKey]int64
	statsDemand                        atomic.Int64
	hostCollector                      *stats.HostCollector
	agentStatusCollector               *stats.AgentStatusCollector
	tokenCollector                     *stats.AgentTokenCollector
	pricing                            *stats.PricingCatalog
	pricingRefreshMu                   sync.Mutex
	pricingRefreshing                  bool
	pricingRefreshState                map[string]any
	pricingRefreshEpoch                uint64
	metadataMu                         sync.Mutex
	metadataTopologyMu                 sync.Mutex
	metadataGeneration                 uint64
	metadataNextGeneration             uint64
	metadataTopologyGeneration         uint64
	metadataTopologySignature          string
	metadataTopologySessions           []string
	metadataTopologyIdentities         map[string]string
	metadataTopologyRenames            []map[string]any
	metadataPayload                    map[string]any
	metadataBuiltAt                    time.Time
	metadataBuilding                   bool
	metadataBuildingGeneration         uint64
	metadataBuildingTopologyGeneration uint64
	metadataQueuedGeneration           uint64
	metadataQueuedSessions             []string
	metadataQueuedIdentities           map[string]string
	metadataQueuedRosterErr            error
	metadataQueuedTopologyGeneration   uint64
	metadataEnrichmentFn               func([]string) (map[string]map[string]any, error)
	gitMetadataSnapshotFn              func(string) (goGitSnapshot, bool)
	ignoreSizeMu                       sync.Mutex
	ignoreSizeChecked                  bool
	ignoreSizeSupported                bool
	sessionPlanGeneration              atomic.Int64
	logRing                            *goLogRing
	rootLease                          *os.File
	shutdownOnce                       sync.Once
	startedAt                          time.Time
	healthLedgerMu                     sync.Mutex
	healthLedger                       *goHealthLedger
	healthLifecycle                    *goHealthLifecycle
	authMu                             sync.Mutex
	authFailures                       map[string]authFailure
	homeRendererMu                     sync.Mutex
	homeRenderer                       *pythonHomeRenderer
	performanceMu                      sync.Mutex
	performanceRecords                 []goPerformanceRecord
	performanceCaptureTotal            uint64
	state                              string
	finderBackgroundJobs               *work.Scheduler
	sessionFilesCache                  sessionFilesCacheState
	tmuxRosterMu                       sync.RWMutex
	tmuxRosterFetchMu                  sync.Mutex
	tmuxRoster                         []string
	tmuxRosterIdentities               map[string]string
	tmuxRosterKnown                    bool
	tmuxRosterRevision                 uint64
	tmuxRosterSampledAt                time.Time
	tmuxSessionRosterFn                func() ([]string, error)
	tmuxRosterWatchCancel              context.CancelFunc
	tmuxRosterWatchDone                chan struct{}
	tmuxSessionActivityFn              func() (map[string]float64, error)
	tmuxWindowActivityFn               func() (map[string]float64, error)
	processEntriesFn                   func() ([]procEntry, error)
	processEntriesCacheMu              sync.Mutex
	processEntriesCacheAt              time.Time
	processEntriesCache                []procEntry
	processEntriesCacheValid           bool
	processEntriesCacheHits            uint64
	processEntriesCacheMisses          uint64
	processEntriesCacheLastDuration    time.Duration
	processEntriesCacheDurationTotal   time.Duration
	childProcessEntriesFn              func(int) ([]procEntry, error)
	agentStatusClassifier              func(context.Context, stats.AgentStatusRequest) stats.AgentStatusResult
	processMetricsFn                   func(time.Time) goProcessMetrics
	childProcessMetricsFn              func(time.Time) []goChildProcessMetric
	goroutineCountFn                   func() int
	agentInventoryMu                   sync.Mutex
	agentInventoryAt                   time.Time
	agentInventoryRecords              []stats.AgentRecord
	agentInventoryErrors               []error
	agentInventoryCacheValid           bool
	agentInventoryCacheHits            uint64
	agentInventoryCacheMisses          uint64
	agentInventoryLastDuration         time.Duration
	agentInventoryDurationTotal        time.Duration
	processMetricsCacheMu              sync.Mutex
	processMetricsCacheAt              time.Time
	processMetricsCache                goProcessMetrics
	processMetricsCacheValid           bool
	processMetricsCacheHits            uint64
	processMetricsCacheMisses          uint64
	processMetricsLastDuration         time.Duration
	processMetricsDurationTotal        time.Duration
	processMetricsLastThreadRows       uint64
	processMetricsLastChildRows        uint64
	processMetricsMu                   sync.Mutex
	processMetricsAt                   time.Time
	processCPUSeconds                  float64
	processCPUValid                    bool
	childCPU                           map[string]childCPUState
	threadCPU                          map[string]childCPUState
}

type tmuxSessionRosterSnapshot struct {
	sessions   []string
	identities map[string]string
	known      bool
	revision   uint64
}

type goTmuxTopology struct {
	sessions       []string
	identities     map[string]string
	generation     uint64
	rosterRevision uint64
	renames        []map[string]any
}

const goPerformanceRecordLimit = 4096
const goMetadataCacheTTL = 15 * time.Second
const goTmuxRosterWatchInterval = time.Second

// Process/thread/descendant metrics are diagnostic snapshots, not a request
// counter. Sampling them more than once per second only repeats /proc work and
// tmux-independent process-tree reads while the browser is polling the panel.
const goProcessMetricsCacheTTL = time.Second

// The fallback descendant topology changes much more slowly than the status
// endpoint is polled. Keep that fallback roster scan on a bounded diagnostic
// cadence, while still sampling each cached descendant's stat file on every
// process-metrics refresh. Production uses the scoped proc-children reader;
// this cache protects injected/fallback readers from repeated roster work.
const goProcessEntriesCacheTTL = 5 * time.Second

// Status and token collectors wake on the same ten-second cadence and consume
// the same global Claude/Codex inventory. Retain one coherent snapshot for one
// native cadence so collectors that wake a little apart do not duplicate a
// full /proc plus tmux walk. The status product itself is no fresher than this
// cadence, so this does not add a new stale-data interval.
const goAgentInventoryCacheTTL = 10 * time.Second

type goPerformanceRecord struct {
	Role         string
	Surface      string
	Time         float64
	ComputeMS    float64
	PayloadBytes int
	CacheStatus  string
	Details      map[string]any
}

type goProcessMetrics struct {
	PID                 int
	StartedAt           time.Time
	RSSBytes            *uint64
	CPUPercent          *float64
	ThreadCount         *uint64
	GoroutineCount      *uint64
	Threads             []goThreadMetric
	ChildProcesses      []goChildProcessMetric
	CPUReasonCode       string
	CPUReason           string
	RSSReasonCode       string
	RSSReason           string
	ThreadReasonCode    string
	ThreadReason        string
	GoroutineReasonCode string
	GoroutineReason     string
}

// goThreadMetric is an OS-thread view. Go does not expose a stable per-
// goroutine CPU counter, so goroutines remain an aggregate runtime count while
// Linux OS threads can be sampled from /proc/<pid>/task/<tid>/stat.
type goThreadMetric struct {
	TID           int
	Name          string
	State         string
	Identity      string
	CPUPercent    *float64
	CPUReasonCode string
	CPUReason     string
}

// goChildProcessMetric is a verified descendant of the Go server. It is kept
// nested under the Go service rather than promoted to a top-level daemon: these
// processes are short-lived implementation details (tmux attach, git, or the
// HTML renderer), not independently managed YOmux services.
type goChildProcessMetric struct {
	PID           int
	PPID          int
	Label         string
	State         string
	Identity      string
	CPUPercent    *float64
	RSSBytes      *uint64
	CPUReasonCode string
	CPUReason     string
	RSSReasonCode string
	RSSReason     string
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
	"transcripts_changed": {"transcripts"}, "tmux_roster_changed": {"core"}, "tmux_signals_changed": {"status", "attention"},
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
type authContextKey struct{}

type winsize struct{ rows, cols, x, y uint16 }

type ptyPair struct {
	master *os.File
	slave  *os.File
	tty    string
}

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

func (p *ptyPair) close() { _ = p.master.Close(); _ = p.slave.Close() }

func (c *wsConn) frame(opcode byte, payload []byte) error {
	c.writeMu.Lock()
	defer c.writeMu.Unlock()
	return writeFrame(c.Conn, opcode, payload)
}

func (s *server) registerFilesystemRoutes(mux *http.ServeMux) {
	mux.Handle("/api/session-files", s.protected(http.HandlerFunc(s.sessionFiles), "readonly"))
	mux.Handle("/api/session-files-stream", s.protected(http.HandlerFunc(s.sessionFilesStream), "readonly"))
	mux.Handle("/api/session-files-batch", s.protected(http.HandlerFunc(s.sessionFilesBatch), "readonly"))
	mux.Handle("/api/fs/fast/list", s.protected(http.HandlerFunc(s.fsFastList), "readonly"))
	mux.Handle("/api/fs/list", s.protected(http.HandlerFunc(s.fsList), "readonly"))
	mux.Handle("/api/fs/read", s.protected(http.HandlerFunc(s.fsRead), "readonly"))
	mux.Handle("/api/fs/info", s.protected(http.HandlerFunc(s.fsInfo), "readonly"))
	mux.Handle("/api/fs/diff", s.protected(http.HandlerFunc(s.fsDiff), "readonly"))
	mux.Handle("/api/fs/git-history", s.protected(http.HandlerFunc(s.fsGitHistory), "readonly"))
	mux.Handle("/api/fs/git-history-stats-stream", s.protected(http.HandlerFunc(s.fsGitHistoryStatsStream), "readonly"))
	mux.Handle("/api/fs/git-commit", s.protected(http.HandlerFunc(s.fsGitCommit), "readonly"))
	mux.Handle("/api/fs/raw", s.protected(http.HandlerFunc(s.fsRaw), "readonly"))
	mux.Handle("/api/fs/search", s.protected(http.HandlerFunc(s.fsSearch), "readonly"))
	mux.Handle("/api/fs/search-stream", s.protected(http.HandlerFunc(s.fsSearch), "readonly"))
	mux.Handle("/api/fs/index-status", s.protected(http.HandlerFunc(s.fsIndexStatus), "readonly"))
	mux.Handle("/api/fs/watch-diff", s.protected(http.HandlerFunc(s.fsWatchDiff), "readonly"))
	mux.Handle("/api/fs/resolve-file-candidates", s.protected(http.HandlerFunc(s.fsResolveCandidates), "readonly"))
	mux.Handle("/api/fs/write", s.protected(http.HandlerFunc(s.fsWrite), "admin"))
	mux.Handle("/api/fs/delete", s.protected(http.HandlerFunc(s.fsDelete), "admin"))
	mux.Handle("/api/fs/unindex", s.protected(http.HandlerFunc(s.fsUnindex), "admin"))
	mux.Handle("/api/fs/rename", s.protected(http.HandlerFunc(s.fsRename), "admin"))
	mux.Handle("/api/fs/mkdir", s.protected(http.HandlerFunc(s.fsMkdir), "admin"))
	mux.Handle("/api/fs/batch", s.protected(http.HandlerFunc(s.fsBatch), "admin"))
	mux.Handle("/api/upload", s.protected(http.HandlerFunc(s.upload), "admin"))
	mux.Handle("/api/batch/search", s.protected(http.HandlerFunc(s.fsSearch), "readonly"))
	mux.Handle("/api/batch/blame", s.protected(http.HandlerFunc(s.fsBlame), "admin"))
	mux.Handle("/api/batch/zip", s.protected(http.HandlerFunc(s.fsZip), "admin"))
	mux.Handle("/api/batch/count", s.protected(http.HandlerFunc(s.fsCount), "admin"))
	mux.Handle("/api/batch/html-preview", s.protected(http.HandlerFunc(s.fsHTMLPreview), "admin"))
}

func (s *server) registerSessionRoutes(mux *http.ServeMux) {
	mux.Handle("/api/ensure-session", s.protected(http.HandlerFunc(s.ensureSession), "admin"))
	mux.Handle("/api/create-session-plan", s.protected(http.HandlerFunc(s.createSessionPlan), "admin"))
	mux.Handle("/api/create-session", s.protected(http.HandlerFunc(s.createSession), "admin"))
	mux.Handle("/api/rename-session", s.protected(http.HandlerFunc(s.renameSession), "admin"))
	mux.Handle("/api/kill-session", s.protected(http.HandlerFunc(s.killSession), "admin"))
}

// explicitCollectorPaths is deliberately fail-closed. Collector inputs are
// mutable host state, so deriving them from HOME, CODEX_HOME, or XDG_DATA_HOME
// would let a development/test process read an operator's real client data.
// Production may opt in by passing all three absolute paths explicitly.
func explicitCollectorPaths() (claudeRoot, codexRoot, opencodeDatabase string, err error) {
	values := []struct {
		name  string
		value *string
	}{
		{"YOLOMUX_CLAUDE_TRANSCRIPT_ROOT", &claudeRoot},
		{"YOLOMUX_CODEX_TRANSCRIPT_ROOT", &codexRoot},
		{"YOLOMUX_OPENCODE_DATABASE", &opencodeDatabase},
	}
	for _, item := range values {
		*item.value = strings.TrimSpace(os.Getenv(item.name))
		if *item.value == "" {
			return "", "", "", fmt.Errorf("stats collectors require explicit %s", item.name)
		}
		if !filepath.IsAbs(*item.value) {
			return "", "", "", fmt.Errorf("stats collector path %s must be absolute", item.name)
		}
	}
	return claudeRoot, codexRoot, opencodeDatabase, nil
}

func useMachineTmuxForOperatorLaunch() {
	if strings.TrimSpace(os.Getenv("YOLOMUX_TMUX_SOCKET")) != "" {
		return
	}
	_ = os.Unsetenv("TMUX")
	_ = os.Unsetenv("TMUX_PANE")
	_ = os.Setenv("YOLOMUX_TMUX_ALLOW_DEFAULT_SERVER", "1")
}

func main() {
	normalizeYomuxEnvironment()
	useMachineTmuxForOperatorLaunch()
	port := flag.Int("port", 7221, "HTTP port")
	listenFD := flag.Int("listen-fd", -1, "use an already-bound TCP listener file descriptor")
	host := flag.String("host", "127.0.0.1", "bind address")
	root := flag.String("root", ".", "YOmux checkout root")
	configDir := flag.String("config", os.Getenv("YOLOMUX_CONFIG_DIR"), "YOmux config directory")
	statsPath := flag.String("stats", os.Getenv("YOLOMUX_STATS_DATABASE"), "YO!stats database path")
	httpMode := flag.Bool("http", false, "serve plain HTTP instead of the default HTTPS")
	certPath := flag.String("cert", "", "TLS certificate PEM path")
	keyPath := flag.String("key", "", "TLS private key PEM path")
	dangerouslyYolo := flag.Bool("dangerously-yolo", false, "allow new Claude/Codex sessions to use dangerous bypass flags")
	flag.BoolVar(dangerouslyYolo, "dang", false, "alias for --dangerously-yolo")
	showVersion := flag.Bool("version", false, "print version")
	flag.Parse()
	if *showVersion {
		fmt.Printf("yomux-go %s\n", version)
		return
	}
	if *port < 1 || *port > 65535 {
		fatal("invalid port")
	}
	if *configDir == "" {
		*configDir = defaultConfigDir()
	}
	if *statsPath == "" {
		*statsPath = filepath.Join(*configDir, "stats-v9.sqlite3")
	}
	rootPath, err := filepath.Abs(*root)
	if err != nil {
		fatal("invalid root: " + err.Error())
	}
	pricing, pricingErr := stats.LoadRepositoryPricingCatalog(rootPath, os.Getenv("YOLOMUX_PRICING_CATALOG"))
	if pricingErr != nil {
		fatal("pricing catalog setup failed: " + pricingErr.Error())
	}
	rootLease, err := acquireInstanceRootLease(rootPath)
	if err != nil {
		fatal("YOmux product root is already owned by another server or unavailable: " + err.Error())
	}
	eventPath := filepath.Join(*configDir, "go-events.jsonl")
	stateDir := strings.TrimSpace(os.Getenv("YOLOMUX_STATE_DIR"))
	if stateDir == "" {
		stateDir = *configDir
	}
	s := &server{root: rootPath, host: *host, port: *port, dangerouslyYolo: *dangerouslyYolo, config: *configDir, state: stateDir, httpMode: *httpMode, statsPath: *statsPath, pricing: pricing, children: make(map[*exec.Cmd]struct{}), processEntriesFn: processEntries, childProcessEntriesFn: descendantProcessEntries, startedAt: time.Now(), events: loadEventBroker(eventPath), eventLogPath: filepath.Join(*configDir, "event-log.jsonl"), operationsPath: filepath.Join(*configDir, "go-operations.json"), operationsJournal: filepath.Join(*configDir, "go-operations.jsonl"), logRing: newGoLogRing(), rootLease: rootLease, capabilities: capabilities.New(version), finderJobs: newFinderJobScheduler(), finderBackgroundJobs: newFinderBackgroundJobScheduler(), finderBulkJobs: newFinderBulkJobScheduler(), finderStreamJobs: newFinderStreamScheduler()}
	// The Go server has no parent observer to retain lifecycle history. Keep the
	// port-scoped ledger in the configured state directory so normal operator
	// restarts expose measured restart/demand counters instead of zeros or an
	// unconditional unavailable envelope. A launch without an explicit reason
	// remains partial rather than being guessed as demand-driven.
	healthEpoch := fmt.Sprintf("pid:%d:start:%d", os.Getpid(), s.startedAt.UnixNano())
	healthHistoryPath := filepath.Join(stateDir, "go-backend-health", fmt.Sprintf("%d.json", *port))
	if lifecycle, lifecycleErr := newGoHealthLifecycle(healthHistoryPath, healthEpoch, os.Getpid(), s.startedAt, os.Getenv("YOLOMUX_GO_START_REASON")); lifecycleErr != nil {
		slog.Warn("Go backend-health history unavailable", "error", lifecycleErr)
	} else {
		s.healthLifecycle = lifecycle
	}
	// Stats routes are implemented as soon as the server starts. Materialization readiness is a
	// separate runtime state: capability discovery and the typed pending response must reach the
	// stats handlers while the first ring build is still in flight, or the browser cannot ever learn
	// the matrix it needs to start that build's read path.
	s.capabilities.Set("pane.stats.v1", capabilities.Entry{Available: true, Degraded: true, Reason: &capabilities.Reason{Code: "stats_pending", MessageKey: "capability.statsPending", MessageParams: map[string]any{}}})
	// Settings use the shared Python schema/serializer below, so the Go backend exposes the
	// same validated payload and persistence contract as the Python backend.
	s.capabilities.Set("core.settings.v1", capabilities.Entry{Available: true})
	finderService, finderErr := finder.NewWithLimits(rootPath, func(kind string, payload map[string]any) error {
		_, err := s.events.publish(kind, payload)
		return err
	}, finder.UploadLimits{MaxBytes: s.fileTransferMaxBytes(), MaxFiles: s.fileTransferMaxFiles(), FilenameTemplate: s.fileTransferFilenameTemplate()})
	if finderErr != nil {
		if !s.finderJobs.CloseWithTimeout(5 * time.Second) {
			slog.Warn("finder job scheduler did not stop before shutdown timeout")
		}
		if !s.finderBackgroundJobs.CloseWithTimeout(5 * time.Second) {
			slog.Warn("finder background scheduler did not stop before shutdown timeout")
		}
		if !s.finderStreamJobs.CloseWithTimeout(5 * time.Second) {
			slog.Warn("finder stream scheduler did not stop before shutdown timeout")
		}
		releaseInstanceRootLease(rootLease)
		fatal("Finder initialization failed: " + finderErr.Error())
	}
	s.finder = finderService
	s.syncFinderWatchCapability()
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
	mux.Handle("/api/capabilities", s.protected(http.HandlerFunc(s.capabilitySnapshot), "readonly"))
	mux.Handle("/api/client-events", s.protected(http.HandlerFunc(s.clientEvents), "readonly"))
	mux.Handle("/api/stats-agent-status", s.protected(http.HandlerFunc(s.statsAgentStatus), "readonly"))
	mux.Handle("/api/stats-capabilities", s.protected(http.HandlerFunc(s.statsCapabilities), "readonly"))
	mux.Handle("/api/stats-observations", s.protected(http.HandlerFunc(s.statsObservations), "readonly"))
	mux.Handle("/api/stats-retry", s.protected(http.HandlerFunc(s.statsRetry), "readonly"))
	mux.Handle("/api/stats-snapshot", s.protected(http.HandlerFunc(s.statsSnapshot), "readonly"))
	mux.Handle("/api/stats-delta", s.protected(http.HandlerFunc(s.statsDelta), "readonly"))
	mux.Handle("/api/stats-stream", s.protected(http.HandlerFunc(s.statsStream), "readonly"))
	mux.Handle("/api/pricing-catalog", s.protected(http.HandlerFunc(s.pricingCatalog), "readonly"))
	mux.Handle("/api/pricing-catalog/refresh", s.protected(http.HandlerFunc(s.pricingCatalogRefresh), "admin"))
	mux.Handle("/readyz", s.protected(http.HandlerFunc(s.ready), "readonly"))
	mux.Handle("/api/operations/ack", s.protected(http.HandlerFunc(s.operationAck), "readonly"))
	mux.Handle("/api/operations/cancel", s.protected(http.HandlerFunc(s.operationCancel), "admin"))
	mux.Handle("/api/background/status", s.protected(http.HandlerFunc(s.backgroundStatus), "readonly"))
	mux.Handle("/api/diagnostics/heap", s.protected(http.HandlerFunc(s.runtimeHeapProfile), "admin"))
	mux.Handle("/api/diagnostics/performance", s.protected(http.HandlerFunc(s.diagnosticsPerformance), "admin"))
	mux.Handle("/api/settings", s.protected(http.HandlerFunc(s.settings), "readonly"))
	// The browser requests session metadata during boot, before it opens the
	// stats surface. Keep this route on the Go mux so a stats-capable process
	// does not turn a boot identity probe into the generic unsupported response.
	mux.Handle("/api/session-metadata", s.protected(http.HandlerFunc(s.sessionMetadata), "readonly"))
	mux.Handle("/api/activity", s.protected(http.HandlerFunc(s.activity), "readonly"))
	mux.Handle("/api/background/claim", s.protected(http.HandlerFunc(s.backgroundClaim), "admin"))
	mux.Handle("/api/operations/accept", s.protected(http.HandlerFunc(s.operationAccept), "admin"))
	mux.Handle("/api/operations/terminalize", s.protected(http.HandlerFunc(s.operationTerminalize), "admin"))
	mux.Handle("/api/system-status", s.protected(http.HandlerFunc(s.systemStatus), "readonly"))
	mux.Handle("/api/system-status/advanced", s.protected(http.HandlerFunc(s.systemStatus), "readonly"))
	mux.Handle("/api/logs", s.protected(http.HandlerFunc(s.logs), "readonly"))
	mux.Handle("/api/watch/roots", s.protected(http.HandlerFunc(s.watchRoots), "admin"))
	s.registerFilesystemRoutes(mux)
	mux.Handle("/api/event", s.protected(http.HandlerFunc(s.eventPost), "readonly"))
	mux.Handle("/api/events", s.protected(http.HandlerFunc(s.eventsList), "readonly"))
	mux.Handle("/api/transcripts", s.protected(http.HandlerFunc(s.sessionMetadata), "readonly"))
	mux.Handle("/api/transcript", s.protected(http.HandlerFunc(s.transcript), "readonly"))
	mux.Handle("/api/context", s.protected(http.HandlerFunc(s.context), "readonly"))
	mux.Handle("/api/context-items", s.protected(http.HandlerFunc(s.contextItems), "readonly"))
	mux.Handle("/api/context-stream", s.protected(http.HandlerFunc(s.contextStream), "readonly"))
	mux.Handle("/api/tmux-status", s.protected(http.HandlerFunc(s.tmuxStatus), "readonly"))
	mux.Handle("/api/tmux-signals", s.protected(http.HandlerFunc(s.tmuxSignals), "readonly"))
	mux.Handle("/api/tmux", s.protected(http.HandlerFunc(s.tmux), "readonly"))
	mux.Handle("/api/tmux-window", s.protected(http.HandlerFunc(s.tmuxWindow), "admin"))
	mux.Handle("/api/tmux-session-exists", s.protected(http.HandlerFunc(s.sessionExists), "readonly"))
	s.registerSessionRoutes(mux)
	mux.Handle("/api/operations/", s.protected(http.HandlerFunc(s.operation), "readonly"))
	// Keep stale clients on a typed capability error instead of an unexplained 404. More specific
	// handlers above win under ServeMux's longest-pattern rule.
	mux.Handle("/api/", s.protected(http.HandlerFunc(s.unsupported), "readonly"))
	mux.Handle("/", s.protected(http.HandlerFunc(s.index), "readonly"))
	mux.HandleFunc("/ws", s.websocket)
	mux.HandleFunc("/static/", s.static)
	addr := fmt.Sprintf("%s:%d", *host, *port)
	httpServer := &http.Server{Addr: addr, Handler: s.logging(mux), ReadHeaderTimeout: 10 * time.Second, ReadTimeout: 30 * time.Second, IdleTimeout: 120 * time.Second}
	go s.reaper()
	var listener net.Listener
	if *listenFD >= 0 {
		listenerFile := os.NewFile(uintptr(*listenFD), "yolomux-listener")
		if listenerFile == nil {
			s.shutdown()
			fatal("invalid listener file descriptor")
		}
		listener, err = net.FileListener(listenerFile)
		_ = listenerFile.Close()
	} else {
		listener, err = net.Listen("tcp", addr)
	}
	if err != nil {
		s.shutdown()
		fatal(err.Error())
	}
	s.sessionFilesListener = listener.Addr().String()
	if tcpAddr, ok := listener.Addr().(*net.TCPAddr); ok && tcpAddr.Port != *port {
		// An inherited listener is authoritative for the port, while the normal path keeps the
		// command-line value. The test harness uses this to reserve the port through bind/listen.
		*port = tcpAddr.Port
		s.port = tcpAddr.Port
		addr = fmt.Sprintf("%s:%d", *host, *port)
		httpServer.Addr = addr
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
	shutdownContext, stopSignals := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stopSignals()
	if err := os.MkdirAll(*configDir, 0o700); err != nil {
		_ = listener.Close()
		s.shutdown()
		fatal("stats config setup failed: " + err.Error())
	}
	statsWriter, err := stats.OpenWriter(shutdownContext, s.statsPath)
	if err != nil {
		_ = listener.Close()
		s.shutdown()
		fatal("stats setup failed: " + err.Error())
	}
	defer statsWriter.Close()
	if migrationStatus, migrationErr := statsWriter.MigrationStatus(shutdownContext); migrationErr != nil {
		_ = listener.Close()
		s.shutdown()
		fatal("stats migration status failed: " + migrationErr.Error())
	} else if migrationStatus.Present {
		s.statsMigration = map[string]any{
			"state":       migrationStatus.State,
			"result":      migrationStatus.Result,
			"issue_kinds": migrationStatus.IssueKinds,
		}
	}
	// Live Go collectors and browser uploads are current-family producers. Keep
	// legacy/sparse fixture compatibility in the library writer, but enforce the
	// typed Python family contract for this serving process.
	statsWriter.EnableCurrentFamilyValidation()
	s.statsWriter = statsWriter
	productKeys := make([]stats.ProductKey, 0)
	for _, window := range statsRanges {
		for _, resolution := range []int{1, 10, 60, 300} {
			if supportedResolution(window, resolution) {
				productKeys = append(productKeys, stats.ProductKey{RangeSeconds: window, ResolutionSeconds: resolution})
			}
		}
	}
	statsService := stats.NewStatsService(statsWriter, stats.StatsServiceConfig{
		Products: productKeys, Price: stats.PricingResolver(pricing),
		AgentStatus: func(ctx context.Context) (stats.AgentStatusSnapshot, error) {
			return stats.ReadAgentStatus(ctx, s.statsPath, stats.AgentStatusQuery{})
		},
	})
	s.statsService = statsService
	var statsRefreshGate statsProjectionRefreshGate
	statsLifecycle := stats.NewLifecycle(statsWriter, stats.LifecycleConfig{Cadence: time.Second,
		// Prepare a fixed startup horizon eagerly, then resume the ordinary
		// bounded background cadence. Explicit requests retain priority.
		StartupProducts:                productKeys,
		BackgroundBackfillCadence:      30 * time.Second,
		BackgroundBackfillInitialDelay: 30 * time.Second,
		Busy: func() bool {
			return s.statsDemand.Load() > 0
		}, StateFunc: func(counters stats.LifecycleCounters) {
			if statsRefreshGate.needsRefresh(counters) {
				_ = statsService.Refresh(shutdownContext)
				statsRefreshGate.record(counters)
			} else {
				// Keep the independently-cadenced status projection fresh while
				// avoiding a ring metadata/read pass when no bucket was published.
				_ = statsService.RefreshAgentStatusIfDue(shutdownContext)
			}
			serviceState := statsService.State()
			pending := 0
			if cells, pendingErr := statsWriter.PendingInvalidationCells(shutdownContext); pendingErr == nil {
				pending = len(cells)
			}
			if counters.Runs > 0 && (counters.LastRunSucceeded || counters.Errors == 0) && pending == 0 && serviceState.Ready {
				s.capabilities.Set("pane.stats.v1", capabilities.Entry{Available: true})
				return
			}
			// Availability describes the implemented route surface; readiness is reported separately by
			// /readyz and by the typed stats pending response. Keep the route admitted while materialization
			// catches up so a fresh browser can discover capabilities and receive a retryable state.
			s.capabilities.Set("pane.stats.v1", capabilities.Entry{Available: true, Degraded: true, Reason: &capabilities.Reason{Code: "stats_pending", MessageKey: "capability.statsPending", MessageParams: map[string]any{"pending_cells": pending, "pending_products": serviceState.PendingProducts}}})
		}, Price: stats.PricingResolver(pricing)})
	statsWriter.AttachLifecycle(statsLifecycle)
	s.statsLifecycle = statsLifecycle
	if err := statsLifecycle.Start(shutdownContext); err != nil {
		_ = listener.Close()
		s.shutdown()
		fatal("stats lifecycle setup failed: " + err.Error())
	}
	defer statsLifecycle.Stop()
	collectorsDisabled := os.Getenv("YOLOMUX_STATS_DISABLE_COLLECTORS") == "1"
	s.syntheticHostInputs = os.Getenv("YOLOMUX_STATS_SYNTHETIC_HOST") == "1"
	if s.syntheticHostInputs {
		s.processMetricsFn = syntheticGoProcessMetrics
	}
	claudeTranscriptRoot, codexTranscriptRoot, opencodeDatabase := "", "", ""
	if !collectorsDisabled {
		var collectorErr error
		claudeTranscriptRoot, codexTranscriptRoot, opencodeDatabase, collectorErr = explicitCollectorPaths()
		if collectorErr != nil {
			_ = listener.Close()
			s.shutdown()
			fatal("stats collector setup failed: " + collectorErr.Error())
		}
	}
	// The live Tabber metadata path and the persisted YO!stats collector must
	// use the same screen classifier. Tests replace this seam with a synthetic
	// classifier so no host pane, process, transcript, or database is needed.
	statusSources := stats.CurrentPlatformAgentStatusSources(opencodeDatabase)
	if os.Getenv("YOLOMUX_STATS_SYNTHETIC_INVENTORY") == "1" {
		statusSources = stats.SyntheticAgentStatusSources(opencodeDatabase)
	}
	s.agentStatusClassifier = func(ctx context.Context, request stats.AgentStatusRequest) stats.AgentStatusResult {
		return stats.ClassifyAgentStatus(ctx, request, statusSources)
	}
	if !collectorsDisabled {
		agentInventorySources := stats.CurrentPlatformAgentInventorySources()
		if os.Getenv("YOLOMUX_STATS_SYNTHETIC_INVENTORY") == "1" {
			agentInventorySources = stats.SyntheticAgentInventorySources()
		}
		agentInventory := func(ctx context.Context) ([]stats.AgentRecord, []error) {
			return s.cachedAgentInventory(ctx, agentInventorySources)
		}
		// Reuse the full machine-wide inventory for OpenCode token collection.
		// The status collector already pays for the bounded /proc and global-tmux
		// snapshot; running LinuxOpenCodeInventorySources separately would repeat
		// the process census on the same cadence. Filtering the shared records
		// preserves the global tmux scope because the token collector applies its
		// existing RequireTmuxPane admission after this callback returns.
		openCodeInventory := func(ctx context.Context) ([]stats.AgentRecord, []error) {
			records, inventoryErrors := s.cachedAgentInventory(ctx, agentInventorySources)
			return filterOpenCodeAgentRecords(records), inventoryErrors
		}
		statusCollector := stats.NewAgentStatusCollector(agentInventory, s.agentStatusClassifier, statsWriter, nil, stats.AgentStatusCollectorConfig{
			// Agent activity is a persisted product, not a browser-only cache.
			// Keep polling at the native cadence even without an SSE subscriber:
			// direct status reads are freshness consumers and go stale after 10s.
			ActiveCadence: 10 * time.Second, IdleCadence: time.Minute,
			EpochID: fmt.Sprintf("agent-status-%d", time.Now().UnixNano()), EpochStartedAt: float64(time.Now().UnixNano()) / 1e9,
			OwnerGeneration: uint64(os.Getpid()), SourceID: "host",
			SnapshotRevision: func() uint64 { return uint64(time.Now().UnixNano()) },
		})
		if err := statusCollector.Start(shutdownContext); err != nil {
			_ = listener.Close()
			s.shutdown()
			fatal("agent status collector setup failed: " + err.Error())
		}
		s.agentStatusCollector = statusCollector
		defer statusCollector.Stop()
		tokenCollector := stats.NewAgentTokenCollector(statsWriter, nil, stats.AgentTokenCollectorConfig{
			Inventory: agentInventory, OpenCodeInventory: openCodeInventory,
			ClaudeTranscriptRoot: claudeTranscriptRoot, CodexTranscriptRoot: codexTranscriptRoot,
			OpenCodeDatabase: opencodeDatabase, CursorPath: filepath.Join(*configDir, "opencode-token-cursors.json"),
			RequireTmuxPane: true,
			EpochID:         fmt.Sprintf("agent-tokens-%d", time.Now().UnixNano()), EpochStartedAt: float64(time.Now().UnixNano()) / 1e9,
			OwnerGeneration: uint64(os.Getpid()), SourceID: "host",
		})
		s.tokenCollector = tokenCollector
		if err := tokenCollector.Start(shutdownContext); err != nil {
			_ = listener.Close()
			s.shutdown()
			fatal("agent token collector setup failed: " + err.Error())
		}
		defer tokenCollector.Stop()
		hostSources := stats.NewHostSources()
		if os.Getenv("YOLOMUX_STATS_SYNTHETIC_HOST") == "1" {
			// Development and integration fixtures must not inspect the host's
			// /proc, process table, memory, or GPU state. The explicit opt-in
			// source is deterministic and carries a same-named co-tenant so the
			// host/owned CPU split remains exercised without host input.
			hostSources = stats.NewSyntheticHostSources(processIdentityForRuntime(), processKeyForRuntime())
		}
		hostCollector := stats.NewHostCollector(hostSources, statsWriter, nil, stats.HostCollectorConfig{
			ActiveCadence: stats.DefaultHostCPUBackgroundCadence, IdleCadence: stats.DefaultHostCPUBackgroundCadence, MemoryCadence: stats.DefaultHostMemoryCadence, SourceTimeout: 900 * time.Millisecond,
			EpochID: fmt.Sprintf("host-%d", time.Now().UnixNano()), EpochStartedAt: float64(time.Now().UnixNano()) / 1e9,
			OwnerGeneration: uint64(os.Getpid()), SourceID: "host", ProcessKey: processKeyForRuntime(), ProcessIdentity: processIdentityForRuntime(),
			// CPU is a host view: keep the bounded top process binaries so the Go
			// chart retains Python's top-user behavior. Daemon ownership is applied
			// separately by the local-services payload, which samples this Go PID
			// and its verified descendants.
			ActiveFunc: func() bool { return s.statsDemand.Load() > 0 },
		})
		if err := hostCollector.Start(shutdownContext); err != nil {
			_ = listener.Close()
			s.shutdown()
			fatal("stats host collector setup failed: " + err.Error())
		}
		s.hostCollector = hostCollector
		defer hostCollector.Stop()
	}
	s.startTmuxRosterWatcher(shutdownContext)
	if !collectorsDisabled {
		recorder := newStatsRuntimeRecorder(statsWriter, s.statsRuntimeServiceLoadSamples, time.Now, 5*time.Second, fmt.Sprintf("runtime-load-%d", time.Now().UnixNano()), uint64(os.Getpid()))
		if err := recorder.Start(shutdownContext); err != nil {
			_ = listener.Close()
			s.shutdown()
			fatal("stats runtime recorder setup failed: " + err.Error())
		}
		s.statsRuntimeRecorder = recorder
		defer recorder.Stop()
	}
	scheme := "https"
	if *httpMode {
		scheme = "http"
	}
	slog.Info("YOmux Go backend", "version", version, "backend", "go", "addr", addr, "scheme", scheme)
	defer s.markGoHealthCleanShutdown()
	serveErr := make(chan error, 1)
	go func() { serveErr <- httpServer.Serve(listener) }()
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

func normalizeYomuxEnvironment() bool {
	legacyUsed := false
	for _, entry := range os.Environ() {
		legacyKey, _, ok := strings.Cut(entry, "=")
		if ok && strings.HasPrefix(legacyKey, "YOLOMUX_") {
			legacyUsed = true
			break
		}
	}
	for _, entry := range os.Environ() {
		newKey, newValue, ok := strings.Cut(entry, "=")
		if !ok || !strings.HasPrefix(newKey, "YOMUX_") {
			continue
		}
		legacyKey := "YOLOMUX_" + strings.TrimPrefix(newKey, "YOMUX_")
		if _, exists := os.LookupEnv(legacyKey); !exists {
			_ = os.Setenv(legacyKey, newValue)
		}
	}
	for _, entry := range os.Environ() {
		legacyKey, _, ok := strings.Cut(entry, "=")
		if !ok || !strings.HasPrefix(legacyKey, "YOLOMUX_") {
			continue
		}
		newKey := "YOMUX_" + strings.TrimPrefix(legacyKey, "YOLOMUX_")
		if newValue, exists := os.LookupEnv(newKey); exists {
			_ = os.Setenv(legacyKey, newValue)
			continue
		}
		legacyValue, _ := os.LookupEnv(legacyKey)
		_ = os.Setenv(newKey, legacyValue)
	}
	if legacyUsed && os.Getenv("YOMUX_ENVIRONMENT_NORMALIZED") != "1" {
		slog.Warn("YOLOMUX_* environment names are deprecated; use YOMUX_*")
	}
	return legacyUsed
}

func processKeyForRuntime() string {
	return processKeyForExecutable(os.Args[0])
}

func processKeyForExecutable(executable string) string {
	name := filepath.Base(executable)
	if name == "" || name == "." || name == string(filepath.Separator) {
		return goServiceID
	}
	return name
}

// processIdentityForRuntime scopes the Go CPU series to this server instance.
// The host census contains every process on the machine, and multiple Go
// previews or test fixtures may share the same executable basename. A PID
// prefix still lets the sampler detect PID reuse through the full
// PID:start-time identity carried by each census row.
func processIdentityForRuntime() string {
	return fmt.Sprintf("%d:", os.Getpid())
}

// statsProjectionRefreshGate keeps the immutable ring projection aligned with
// durable publications without rereading every ring on every one-second
// lifecycle tick. Agent-status freshness is handled separately by the
// cadence-aware status refresh below.
type statsProjectionRefreshGate struct {
	initialized  bool
	publications uint64
	repairs      uint64
	errors       uint64
}

func (g *statsProjectionRefreshGate) needsRefresh(counters stats.LifecycleCounters) bool {
	return !g.initialized || g.publications != counters.Publications || g.repairs != counters.Repairs || g.errors != counters.Errors
}

func (g *statsProjectionRefreshGate) record(counters stats.LifecycleCounters) {
	g.initialized = true
	g.publications = counters.Publications
	g.repairs = counters.Repairs
	g.errors = counters.Errors
}

func yomuxEnvironmentValue(legacyName string) string {
	if !strings.HasPrefix(legacyName, "YOLOMUX_") {
		return os.Getenv(legacyName)
	}
	if value, present := os.LookupEnv("YOMUX_" + strings.TrimPrefix(legacyName, "YOLOMUX_")); present {
		return value
	}
	return os.Getenv(legacyName)
}

func defaultConfigDir() string {
	if configured := yomuxEnvironmentValue("YOLOMUX_CONFIG_DIR"); configured != "" {
		return configured
	}
	if root := yomuxEnvironmentValue("YOLOMUX_ROOT"); root != "" {
		return filepath.Join(root, "config")
	}
	return filepath.Join(os.Getenv("HOME"), ".config", "yomux")
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
		if s.tmuxRosterWatchCancel != nil {
			s.tmuxRosterWatchCancel()
			<-s.tmuxRosterWatchDone
		}
		s.homeRendererMu.Lock()
		if s.homeRenderer != nil {
			s.homeRenderer.close()
			s.homeRenderer = nil
		}
		s.homeRendererMu.Unlock()
		if s.finderJobs != nil {
			if !s.finderJobs.CloseWithTimeout(5 * time.Second) {
				slog.Warn("finder job scheduler did not stop before shutdown timeout")
			}
		}
		if s.finderBackgroundJobs != nil {
			if !s.finderBackgroundJobs.CloseWithTimeout(5 * time.Second) {
				slog.Warn("finder background scheduler did not stop before shutdown timeout")
			}
		}
		if s.finderBulkJobs != nil {
			if !s.finderBulkJobs.CloseWithTimeout(5 * time.Second) {
				slog.Warn("finder bulk job scheduler did not stop before shutdown timeout")
			}
		}
		if s.finderStreamJobs != nil {
			if !s.finderStreamJobs.CloseWithTimeout(5 * time.Second) {
				slog.Warn("finder stream scheduler did not stop before shutdown timeout")
			}
		}
		if s.finder != nil {
			s.finder.Close()
		}
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
	if len(value) >= 2 && ((value[0] == '"' && value[len(value)-1] == '"') || (value[0] == '\'' && value[len(value)-1] == '\'')) {
		value = value[1 : len(value)-1]
	}
	return strings.ReplaceAll(value, `\"`, `"`)
}

func (s *server) identity(r *http.Request) (authUser, bool) {
	for _, cookieName := range []string{s.authCookieName(), s.legacyAuthCookieName()} {
		cookie, err := r.Cookie(cookieName)
		if err != nil {
			continue
		}
		for _, user := range s.users() {
			if cookie.Value != "" && hmac.Equal([]byte(cookie.Value), []byte(s.cookieValue(user))) {
				return user, true
			}
		}
	}
	for _, marker := range []string{"yomux_logged_out", "yolomux_logged_out"} {
		if cookie, err := r.Cookie(marker); err == nil && cookie.Value == "1" {
			return authUser{}, false
		}
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
	return "yomux_auth_" + strconv.Itoa(s.port)
}

func (s *server) legacyAuthCookieName() string {
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
		if filesystemRouteRequiresAdmin(r) && user.role == "readonly" {
			s.rejectForbidden(w, user, "admin")
			return
		}
		if !roleAllows(user, role) {
			s.rejectForbidden(w, user, role)
			return
		}
		if s.capabilities != nil {
			capability := capabilities.RouteCapability(r.Method, r.URL.RequestURI())
			if capability != "" && !s.capabilities.Available(capability) {
				entry := s.capabilities.Entry(capability)
				jsonResponse(w, http.StatusNotImplemented, map[string]any{
					"error": "feature_unavailable", "reason": entry.Reason, "capability": capability,
				})
				return
			}
		}
		// Basic auth is a compatibility path. Mint the port-scoped cookie before the
		// downstream handler writes headers so polling can move to cookie auth without
		// exposing credentials on every request. A logout marker deliberately blocks
		// this path until the user submits the login form again.
		canonicalValid := false
		if cookie, err := r.Cookie(s.authCookieName()); err == nil {
			canonicalValid = hmac.Equal([]byte(cookie.Value), []byte(s.cookieValue(user)))
		}
		legacyValid := false
		if cookie, err := r.Cookie(s.legacyAuthCookieName()); err == nil {
			legacyValid = hmac.Equal([]byte(cookie.Value), []byte(s.cookieValue(user)))
		}
		if legacyValid && !canonicalValid {
			http.SetCookie(w, &http.Cookie{Name: s.authCookieName(), Value: s.cookieValue(user), Path: "/", MaxAge: 90 * 24 * 60 * 60, HttpOnly: true, SameSite: http.SameSiteLaxMode, Secure: s.secureCookies()})
		}
		if !canonicalValid {
			_, newMarkerErr := r.Cookie("yomux_logged_out")
			_, legacyMarkerErr := r.Cookie("yolomux_logged_out")
			if !legacyValid && newMarkerErr != nil && legacyMarkerErr != nil {
				if basicUser, basicOK := s.basicIdentity(r); basicOK {
					http.SetCookie(w, &http.Cookie{Name: s.authCookieName(), Value: s.cookieValue(basicUser), Path: "/", MaxAge: 90 * 24 * 60 * 60, HttpOnly: true, SameSite: http.SameSiteLaxMode, Secure: s.secureCookies()})
				}
			}
		}
		next.ServeHTTP(w, r.WithContext(context.WithValue(r.Context(), authContextKey{}, user)))
	})
}

func filesystemRouteRequiresAdmin(r *http.Request) bool {
	if r.URL.Path == "/api/watch/roots" || r.URL.Path == "/api/fs/batch" {
		return true
	}
	if r.URL.Path == "/api/fs/write" || r.URL.Path == "/api/fs/delete" || r.URL.Path == "/api/fs/unindex" || r.URL.Path == "/api/fs/rename" || r.URL.Path == "/api/fs/mkdir" || r.URL.Path == "/api/upload" {
		return true
	}
	if r.URL.Path == "/api/settings" {
		return r.Method == http.MethodPost
	}
	return r.URL.Path == "/api/batch/zip" || r.URL.Path == "/api/batch/count" || r.URL.Path == "/api/batch/html-preview"
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

func (s *server) authSetupRequired() bool {
	users := s.users()
	return len(users) == 0 || len(users) == 1 && users[0].username == "user" && users[0].password == "password"
}

func (s *server) authSetup(w http.ResponseWriter, _ *http.Request) {
	jsonResponse(w, http.StatusOK, map[string]any{"setup_required": s.authSetupRequired()})
}

func (s *server) login(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet && r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	if s.authSetupRequired() {
		s.writePythonSetupAuth(w, r)
		return
	}
	if r.Method == http.MethodGet {
		next := safeLoginNextValue(r.URL.Query().Get("next"))
		s.writePythonLogin(w, r, next, "")
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
			http.SetCookie(w, &http.Cookie{Name: "yomux_logged_out", Value: "", Path: "/", MaxAge: -1, HttpOnly: true, SameSite: http.SameSiteLaxMode, Secure: s.secureCookies()})
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
	locale := requestLocale(r)
	script := `import sys
from yomux_lib.web import login_html
print(login_html(next_path=sys.argv[1], error=sys.argv[2], secure=sys.argv[3] == "1", current_locale=sys.argv[4], accept_language=sys.argv[5]), end="")`
	secure := "0"
	if s.secureCookies() {
		secure = "1"
	}
	command := exec.Command("python3", "-c", script, next, message, secure, locale, r.Header.Get("Accept-Language"))
	command.Dir = s.root
	command.Env = pythonBridgeEnvironment(s.root, s.config)
	body, err := command.Output()
	if err != nil {
		http.Error(w, "login page unavailable", http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	_, _ = w.Write(body)
}

func (s *server) writePythonSetupAuth(w http.ResponseWriter, r *http.Request) {
	locale := requestLocale(r)
	secure := "0"
	if s.secureCookies() {
		secure = "1"
	}
	script := `import sys
from yomux_lib.web import setup_auth_html
print(setup_auth_html(current_locale=sys.argv[1], accept_language=sys.argv[2], secure=sys.argv[3] == "1"), end="")`
	command := exec.Command("python3", "-c", script, locale, r.Header.Get("Accept-Language"), secure)
	command.Dir = s.root
	command.Env = pythonBridgeEnvironment(s.root, s.config)
	body, err := command.Output()
	if err != nil {
		http.Error(w, "authentication setup page unavailable", http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	_, _ = w.Write(body)
}

func requestLocale(r *http.Request) string {
	if cookie, err := r.Cookie("yomux_locale"); err == nil {
		return cookie.Value
	}
	if cookie, err := r.Cookie("yolomux_locale"); err == nil {
		return cookie.Value
	}
	return ""
}

func (s *server) logout(w http.ResponseWriter, r *http.Request) {
	for _, name := range []string{s.authCookieName(), s.legacyAuthCookieName(), "yolomux_auth"} {
		http.SetCookie(w, &http.Cookie{Name: name, Value: "", Path: "/", MaxAge: 0, HttpOnly: true, SameSite: http.SameSiteLaxMode, Secure: false, Expires: time.Unix(0, 0)})
		if s.secureCookies() {
			http.SetCookie(w, &http.Cookie{Name: name, Value: "", Path: "/", MaxAge: 0, HttpOnly: true, SameSite: http.SameSiteLaxMode, Secure: true, Expires: time.Unix(0, 0)})
		}
	}
	http.SetCookie(w, &http.Cookie{Name: "yomux_logged_out", Value: "1", Path: "/", MaxAge: 90 * 24 * 60 * 60, HttpOnly: true, SameSite: http.SameSiteLaxMode, Secure: s.secureCookies()})
	http.SetCookie(w, &http.Cookie{Name: "yolomux_logged_out", Value: "", Path: "/", MaxAge: -1, HttpOnly: true, SameSite: http.SameSiteLaxMode, Secure: s.secureCookies()})
	http.Redirect(w, r, "/login", http.StatusSeeOther)
}

func tlsFiles(certPath, keyPath, host string, port int) (string, string, error) {
	if (certPath == "") != (keyPath == "") {
		return "", "", fmt.Errorf("--cert and --key must be provided together")
	}
	if certPath != "" {
		return certPath, keyPath, nil
	}
	dir := filepath.Join(os.TempDir(), "yomux-go-tls-"+strconv.Itoa(port))
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

type loggingResponseWriter struct {
	http.ResponseWriter
	status int
	bytes  int
}

func (w *loggingResponseWriter) WriteHeader(status int) {
	if w.status != 0 {
		return
	}
	w.status = status
	w.ResponseWriter.WriteHeader(status)
}

func (w *loggingResponseWriter) Write(value []byte) (int, error) {
	if w.status == 0 {
		w.WriteHeader(http.StatusOK)
	}
	n, err := w.ResponseWriter.Write(value)
	w.bytes += n
	return n, err
}

func (w *loggingResponseWriter) Flush() {
	if w.status == 0 {
		w.WriteHeader(http.StatusOK)
	}
	if flusher, ok := w.ResponseWriter.(http.Flusher); ok {
		flusher.Flush()
	}
}

func (w *loggingResponseWriter) Hijack() (net.Conn, *bufio.ReadWriter, error) {
	hijacker, ok := w.ResponseWriter.(http.Hijacker)
	if !ok {
		return nil, nil, fmt.Errorf("response writer does not support hijacking")
	}
	if w.status == 0 {
		w.status = http.StatusSwitchingProtocols
	}
	return hijacker.Hijack()
}

func (w *loggingResponseWriter) ReadFrom(reader io.Reader) (int64, error) {
	if w.status == 0 {
		w.WriteHeader(http.StatusOK)
	}
	if optimized, ok := w.ResponseWriter.(io.ReaderFrom); ok {
		n, err := optimized.ReadFrom(reader)
		w.bytes += int(n)
		return n, err
	}
	n, err := io.Copy(struct{ io.Writer }{Writer: w.ResponseWriter}, reader)
	w.bytes += int(n)
	return n, err
}

func (s *server) logging(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		finishHealth := s.ensureGoHealthLedger(start).begin(goHealthRequestExcluded(r.URL.Path))
		logged := &loggingResponseWriter{ResponseWriter: w}
		completed := false
		status := 0
		defer func() {
			if status == 0 {
				status = http.StatusInternalServerError
			}
			finishHealth(status, completed, time.Since(start))
		}()
		next.ServeHTTP(logged, r)
		elapsed := time.Since(start)
		status = logged.status
		if status == 0 {
			status = http.StatusOK
		}
		completed = true
		slog.Info("request", "method", r.Method, "path", r.URL.Path, "status", status, "duration_ms", elapsed.Milliseconds())
		s.recordPerformance(r, status, logged.bytes, elapsed)
	})
}

func (s *server) recordPerformance(r *http.Request, status, payloadBytes int, elapsed time.Duration) {
	marker := strings.TrimSpace(r.Header.Get("X-YOmux-Measurement"))
	if marker == "" {
		return
	}
	requestID := strings.TrimSpace(r.Header.Get("X-YOmux-Request-ID"))
	digestBytes := sha256.Sum256([]byte(marker))
	digest := hex.EncodeToString(digestBytes[:])[:16]
	record := goPerformanceRecord{
		Role: "http-endpoint", Surface: r.Method + " " + r.URL.Path,
		Time: float64(time.Now().UnixNano()) / 1e9, ComputeMS: float64(elapsed.Microseconds()) / 1000,
		PayloadBytes: payloadBytes,
		Details: map[string]any{
			"measurement_scope": "capture", "measurement_request_id": digest,
			"transport_request_id": requestID, "method": r.Method, "path": r.URL.Path,
			"status": status, "request_total_ms": float64(elapsed.Microseconds()) / 1000,
		},
	}
	s.performanceMu.Lock()
	defer s.performanceMu.Unlock()
	s.performanceCaptureTotal++
	s.performanceRecords = append(s.performanceRecords, record)
	if len(s.performanceRecords) > goPerformanceRecordLimit {
		s.performanceRecords = s.performanceRecords[len(s.performanceRecords)-goPerformanceRecordLimit:]
	}
}

func (s *server) diagnosticsPerformance(w http.ResponseWriter, r *http.Request) {
	scope := strings.TrimSpace(r.URL.Query().Get("measurement_scope"))
	if scope != "" && scope != "capture" {
		jsonResponse(w, http.StatusBadRequest, map[string]any{"ok": false, "error": "unsupported measurement scope"})
		return
	}
	s.performanceMu.Lock()
	records := append([]goPerformanceRecord(nil), s.performanceRecords...)
	captureTotal := s.performanceCaptureTotal
	s.performanceMu.Unlock()
	recent := make([]map[string]any, 0, len(records))
	for _, record := range records {
		details := map[string]any{}
		for key, value := range record.Details {
			details[key] = value
		}
		recent = append(recent, map[string]any{
			"role": record.Role, "surface": record.Surface, "time": record.Time,
			"compute_ms": record.ComputeMS, "payload_bytes": record.PayloadBytes,
			"cache_status": record.CacheStatus, "details": details,
		})
	}
	perf := map[string]any{
		"window_seconds": 60.0, "record_limit": goPerformanceRecordLimit,
		"record_count": len(records), "summary": []any{}, "top_payload_bytes": []any{}, "recent": recent,
		"capture": map[string]any{"capacity": goPerformanceRecordLimit, "retained": len(recent), "total": captureTotal, "evicted": maxInt(0, int(captureTotal)-len(recent)), "first_sequence": maxUint64(1, captureTotal-uint64(len(recent))+1), "last_sequence": captureTotal},
	}
	jsonResponse(w, http.StatusOK, map[string]any{"perf": perf, "transport": map[string]any{"teardowns_total": 0, "teardowns_by_exception": map[string]any{}}, "shared_phase_counters": []any{}, "repeated_work": []any{}, "browser_profiles": map[string]any{}, "browser_observation_status": map[string]any{}})
}

func maxUint64(left, right uint64) uint64 {
	if left > right {
		return left
	}
	return right
}

func (s *server) health(w http.ResponseWriter, _ *http.Request) {
	jsonResponse(w, http.StatusOK, map[string]any{"ok": true, "backend": "go", "version": version})
}

func (s *server) ping(w http.ResponseWriter, _ *http.Request) {
	jsonResponse(w, http.StatusOK, map[string]any{"ok": true, "backend": "go", "capabilities": s.capabilityEntries()})
}

func (s *server) capabilityEntries() map[string]any {
	if s.capabilities == nil {
		return map[string]any{}
	}
	document := s.capabilities.Snapshot()
	entries, _ := document["capabilities"].(map[string]capabilities.Entry)
	result := make(map[string]any, len(entries))
	for name, entry := range entries {
		result[name] = entry
	}
	return result
}

func (s *server) capabilitySnapshot(w http.ResponseWriter, _ *http.Request) {
	if s.capabilities == nil {
		jsonResponse(w, http.StatusServiceUnavailable, map[string]any{"error": "capabilities unavailable"})
		return
	}
	jsonResponse(w, http.StatusOK, s.capabilities.Snapshot())
}

func (s *server) fsFastList(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	path := r.URL.Query().Get("path")
	if path == "" {
		path = s.root
	}
	// This is the first-paint route. Keep it independent of the Finder Git/session worker pool so a
	// slow backfill cannot delay the plain directory rows. FastList performs only one-level
	// descriptor-bound filesystem metadata reads; repository history, status, and +/- counts arrive
	// through the later session-files/enrichment requests.
	payload, finderErr := s.finder.FastList(r.Context(), path)
	finderHTTPJSON(w, finderRequestIDForHTTP(), payload, finderErr)
}

func (s *server) fsList(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	path := r.URL.Query().Get("path")
	if path == "" {
		path = s.root
	}
	s.enqueueFinderList(w, r.Context(), path, true, "list")
}

func (s *server) fsRead(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	path := r.URL.Query().Get("path")
	if path == "" {
		finderHTTPJSON(w, finderRequestIDForHTTP(), nil, &finder.Error{Status: http.StatusBadRequest, Code: "invalid_request", MessageKey: "request.error.pathRequired", Message: "path is required"})
		return
	}
	product, finderErr := s.runFinderProduct(r.Context(), func(ctx context.Context) (finderProduct, *finder.Error) {
		payload, err := s.finder.Read(ctx, path, queryTrue(r.URL.Query().Get("include_git")))
		return finderProduct{data: payload}, err
	})
	if finderErr != nil {
		finderHTTPJSON(w, finderRequestIDForHTTP(), nil, finderErr)
		return
	}
	finderHTTPJSON(w, finderRequestIDForHTTP(), product.data, nil)
}

func (s *server) fsInfo(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	path := r.URL.Query().Get("path")
	if path == "" {
		finderHTTPJSON(w, finderRequestIDForHTTP(), nil, &finder.Error{Status: http.StatusBadRequest, Code: "invalid_request", MessageKey: "request.error.pathRequired", Message: "path is required"})
		return
	}
	product, finderErr := s.runFinderProduct(r.Context(), func(ctx context.Context) (finderProduct, *finder.Error) {
		payload, err := s.finder.Info(ctx, path, queryTrue(r.URL.Query().Get("include_git")))
		return finderProduct{data: payload}, err
	})
	if finderErr != nil {
		finderHTTPJSON(w, finderRequestIDForHTTP(), nil, finderErr)
		return
	}
	finderHTTPJSON(w, finderRequestIDForHTTP(), product.data, nil)
}

func (s *server) fsDiff(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	path := r.URL.Query().Get("path")
	if path == "" {
		finderHTTPJSON(w, finderRequestIDForHTTP(), nil, &finder.Error{Status: http.StatusBadRequest, Code: "invalid_request", MessageKey: "request.error.pathRequired", Message: "path is required"})
		return
	}
	product, finderErr := s.runFinderProduct(r.Context(), func(ctx context.Context) (finderProduct, *finder.Error) {
		payload, err := s.finder.Diff(ctx, path, r.URL.Query().Get("from"), r.URL.Query().Get("to"))
		return finderProduct{data: payload}, err
	})
	if finderErr != nil {
		finderHTTPJSON(w, finderRequestIDForHTTP(), nil, finderErr)
		return
	}
	finderHTTPJSON(w, finderRequestIDForHTTP(), product.data, nil)
}

func (s *server) fsGitHistory(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	path := r.URL.Query().Get("path")
	if path == "" {
		finderHTTPJSON(w, finderRequestIDForHTTP(), nil, &finder.Error{Status: http.StatusBadRequest, Code: "invalid_request", MessageKey: "request.error.pathRequired", Message: "path is required"})
		return
	}
	limit := r.URL.Query().Get("limit")
	cursor := r.URL.Query().Get("cursor")
	withStats := r.URL.Query().Get("stats") == "1"
	if r.URL.Query().Get("worktree") == "only" {
		payload, finderErr := s.finder.GitHistoryWorktree(r.Context(), path)
		finderHTTPJSON(w, finderRequestIDForHTTP(), payload, finderErr)
		return
	}
	statsOffsetRaw := r.URL.Query().Get("stats_offset")
	startOffsetRaw := r.URL.Query().Get("start_offset")
	startOffset := -1
	if startOffsetRaw != "" {
		parsed, err := strconv.Atoi(startOffsetRaw)
		if err != nil || parsed < 0 || parsed > finder.MaxGitHistoryOffset() || cursor == "" || withStats || statsOffsetRaw != "" {
			finderHTTPJSON(w, finderRequestIDForHTTP(), nil, &finder.Error{Status: http.StatusBadRequest, Code: "invalid_request", MessageKey: "request.error.invalidQuery", Path: path, Message: "start_offset requires a frozen cursor and a valid offset"})
			return
		}
		startOffset = parsed
	}
	statsOffset := -1
	if statsOffsetRaw != "" {
		if !withStats {
			finderHTTPJSON(w, finderRequestIDForHTTP(), nil, &finder.Error{Status: http.StatusBadRequest, Code: "invalid_request", MessageKey: "request.error.invalidQuery", Path: path, Message: "stats_offset requires stats=1"})
			return
		}
		parsed, err := strconv.Atoi(statsOffsetRaw)
		if err != nil || parsed < 0 {
			finderHTTPJSON(w, finderRequestIDForHTTP(), nil, &finder.Error{Status: http.StatusBadRequest, Code: "invalid_request", MessageKey: "request.error.invalidQuery", Path: path, Message: "stats_offset must be a non-negative integer"})
			return
		}
		statsOffset = parsed
	}
	scheduler := s.finderJobs
	if withStats {
		scheduler = s.finderBackgroundScheduler()
	}
	if !withStats && r.URL.Query().Get("worktree") == "0" {
		payload, finderErr := s.finder.GitHistoryMetadata(r.Context(), path, limit, cursor, startOffset)
		finderHTTPJSON(w, finderRequestIDForHTTP(), payload, finderErr)
		return
	}
	s.enqueueFinderJSONWithScheduler(w, r.Context(), scheduler, r.URL.Path, "git_history", path, 0, func(ctx context.Context) (map[string]any, *finder.Error) {
		if startOffset >= 0 {
			return s.finder.GitHistoryPageAtOffset(ctx, path, limit, cursor, startOffset)
		}
		if statsOffset >= 0 {
			return s.finder.GitHistoryAtOffset(ctx, path, limit, cursor, statsOffset)
		}
		return s.finder.GitHistory(ctx, path, limit, cursor, withStats)
	})
}

func parseGitHistoryBatchSizes(raw string) ([]int, error) {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return nil, nil
	}
	raw = strings.ReplaceAll(raw, "+", " ")
	fields := strings.Fields(strings.ReplaceAll(raw, ",", " "))
	if len(fields) == 0 || len(fields) > finder.MaxGitHistoryScheduledBatches() {
		return nil, fmt.Errorf("batch_sizes must contain 1-%d positive integers", finder.MaxGitHistoryScheduledBatches())
	}
	batchSizes := make([]int, 0, len(fields))
	total := 0
	for _, field := range fields {
		batchSize, err := strconv.Atoi(field)
		if err != nil || batchSize < 1 || batchSize > finder.MaxGitHistoryLimit() {
			return nil, fmt.Errorf("batch_sizes entries must be integers from 1 to %d", finder.MaxGitHistoryLimit())
		}
		if total > finder.MaxGitHistoryOffset()-batchSize {
			return nil, fmt.Errorf("batch_sizes total is too large")
		}
		total += batchSize
		batchSizes = append(batchSizes, batchSize)
	}
	return batchSizes, nil
}

func (s *server) fsGitHistoryStatsStream(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	path := r.URL.Query().Get("path")
	cursor := r.URL.Query().Get("cursor")
	if path == "" || cursor == "" {
		finderHTTPJSON(w, finderRequestIDForHTTP(), nil, &finder.Error{Status: http.StatusBadRequest, Code: "invalid_request", MessageKey: "request.error.invalidQuery", Path: path, Message: "path and cursor are required"})
		return
	}
	count, err := strconv.Atoi(r.URL.Query().Get("count"))
	batchSizes, scheduleErr := parseGitHistoryBatchSizes(r.URL.Query().Get("batch_sizes"))
	offsetRaw := r.URL.Query().Get("offset")
	offset := -1
	if offsetRaw != "" {
		parsed, parseErr := strconv.Atoi(offsetRaw)
		if parseErr != nil || parsed < 0 || parsed > finder.MaxGitHistoryOffset() || len(batchSizes) == 0 {
			finderHTTPJSON(w, finderRequestIDForHTTP(), nil, &finder.Error{Status: http.StatusBadRequest, Code: "invalid_request", MessageKey: "request.error.invalidQuery", Path: path, Message: "offset requires batch_sizes and a valid offset"})
			return
		}
		offset = parsed
	}
	if scheduleErr != nil || (len(batchSizes) == 0 && (err != nil || count < 1 || count > finder.MaxGitHistoryOffset())) {
		message := "count must be a positive integer"
		if r.URL.Query().Get("batch_sizes") != "" {
			message = scheduleErr.Error()
		}
		finderHTTPJSON(w, finderRequestIDForHTTP(), nil, &finder.Error{Status: http.StatusBadRequest, Code: "invalid_request", MessageKey: "request.error.invalidQuery", Path: path, Message: message})
		return
	}
	batchSize := 0
	if len(batchSizes) == 0 {
		batchSize, err = strconv.Atoi(r.URL.Query().Get("batch_size"))
		if err != nil || batchSize < 1 || batchSize > finder.MaxGitHistoryLimit() {
			finderHTTPJSON(w, finderRequestIDForHTTP(), nil, &finder.Error{Status: http.StatusBadRequest, Code: "invalid_request", MessageKey: "request.error.invalidQuery", Path: path, Message: "batch_size must be a positive integer"})
			return
		}
	}
	stream, ok := newJSONSSEWriter(w)
	if !ok {
		finderHTTPJSON(w, finderRequestIDForHTTP(), nil, &finder.Error{Status: http.StatusInternalServerError, Code: "streaming_unavailable", MessageKey: "common.requestFailed", Path: path, Message: "streaming unavailable"})
		return
	}
	writeSSE := stream.write
	startPayload := map[string]any{"path": path}
	if len(batchSizes) > 0 {
		startPayload["batch_sizes"] = batchSizes
		if offset >= 0 {
			startPayload["offset"] = offset
		}
	} else {
		startPayload["count"], startPayload["batch_size"] = count, batchSize
	}
	if writeErr := writeSSE("start", startPayload); writeErr != nil {
		return
	}
	emitted := 0
	var nextCursor string
	_, finderErr := s.runFinderStreamProduct(r.Context(), func(ctx context.Context) (finderProduct, *finder.Error) {
		emit := func(payload map[string]any) error {
			commits, _ := payload["commits"].([]map[string]any)
			emitted += len(commits)
			return writeSSE("chunk", payload)
		}
		var finderErr *finder.Error
		if len(batchSizes) > 0 {
			if offset >= 0 {
				nextCursor, finderErr = s.finder.GitHistoryStatsScheduled(ctx, path, cursor, batchSizes, emit, offset)
			} else {
				nextCursor, finderErr = s.finder.GitHistoryStatsScheduled(ctx, path, cursor, batchSizes, emit)
			}
		} else {
			finderErr = s.finder.GitHistoryStatsStream(ctx, path, cursor, count, batchSize, func(payload map[string]any) error {
				nextCursor, _ = payload["next_cursor"].(string)
				return emit(payload)
			})
		}
		return finderProduct{}, finderErr
	})
	if finderErr != nil {
		if r.Context().Err() == nil {
			_ = writeSSE("error", finderErr.Payload())
		}
		return
	}
	_ = writeSSE("done", map[string]any{"count": emitted, "next_cursor": nextCursor})
}

func (s *server) fsGitCommit(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	path := r.URL.Query().Get("path")
	if path == "" {
		finderHTTPJSON(w, finderRequestIDForHTTP(), nil, &finder.Error{Status: http.StatusBadRequest, Code: "invalid_request", MessageKey: "request.error.pathRequired", Message: "path is required"})
		return
	}
	commit := r.URL.Query().Get("commit")
	head := r.URL.Query().Get("head")
	repositoryIdentity := r.URL.Query().Get("repository_identity")
	s.enqueueFinderJSON(w, r.Context(), r.URL.Path, "git_commit", path, func(ctx context.Context) (map[string]any, *finder.Error) {
		return s.finder.GitCommit(ctx, path, commit, head, repositoryIdentity)
	})
}

func (s *server) fsRaw(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	path := r.URL.Query().Get("path")
	if path == "" {
		finderHTTPJSON(w, finderRequestIDForHTTP(), nil, &finder.Error{Status: http.StatusBadRequest, Code: "invalid_request", MessageKey: "request.error.pathRequired", Message: "path is required"})
		return
	}
	maxBytes := s.fileTransferMaxBytes()
	rangeStart, rangeEnd, hasRange, rangeErr := parseByteRange(r.Header.Get("Range"))
	if rangeErr != nil {
		finderHTTPJSON(w, finderRequestIDForHTTP(), nil, &finder.Error{Status: http.StatusRequestedRangeNotSatisfiable, Code: "range_not_satisfiable", MessageKey: "fs.error.rangeNotSatisfiable", Path: path, Message: rangeErr.Error()})
		return
	}
	product, finderErr := s.runFinderProduct(r.Context(), func(ctx context.Context) (finderProduct, *finder.Error) {
		if hasRange {
			data, contentType, filename, size, err := s.finder.RawRange(ctx, path, maxBytes, rangeStart, rangeEnd)
			return finderProduct{body: data, contentType: contentType, filename: filename, size: size}, err
		}
		data, contentType, filename, err := s.finder.Raw(ctx, path, maxBytes)
		return finderProduct{body: data, contentType: contentType, filename: filename, size: int64(len(data))}, err
	})
	if finderErr != nil {
		if hasRange && finderErr.Code == "range_not_satisfiable" {
			w.Header().Set("Content-Range", fmt.Sprintf("bytes */%d", product.size))
		}
		finderHTTPJSON(w, finderRequestIDForHTTP(), nil, finderErr)
		return
	}
	data := product.body
	w.Header().Set("Content-Type", product.contentType)
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Content-Length", strconv.Itoa(len(data)))
	w.Header().Set("Accept-Ranges", "bytes")
	w.Header().Set("ETag", `"`+fmt.Sprintf("%x", sha256.Sum256(data))+`"`)
	status := http.StatusOK
	if hasRange {
		status = http.StatusPartialContent
		if rangeEnd >= product.size {
			rangeEnd = product.size - 1
		}
		w.Header().Set("Content-Range", fmt.Sprintf("bytes %d-%d/%d", rangeStart, rangeEnd, product.size))
	}
	if r.URL.Query().Get("download") == "1" || r.URL.Query().Get("download") == "true" {
		w.Header().Set("Content-Disposition", `attachment; filename="`+strings.ReplaceAll(product.filename, `"`, "")+`"`)
	} else {
		w.Header().Set("Content-Disposition", "inline")
	}
	w.WriteHeader(status)
	_, _ = w.Write(data)
}

func (s *server) fsWatchDiff(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	product, finderErr := s.runFinderProduct(r.Context(), func(ctx context.Context) (finderProduct, *finder.Error) {
		payload, err := s.finder.WatchDiff(ctx, r.URL.Query().Get("since"), queryTrue(r.URL.Query().Get("full")))
		return finderProduct{data: payload}, err
	})
	if finderErr != nil {
		finderHTTPJSON(w, finderRequestIDForHTTP(), nil, finderErr)
		return
	}
	finderHTTPJSON(w, finderRequestIDForHTTP(), product.data, nil)
}

func (s *server) fsSearch(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	root := r.URL.Query().Get("root")
	if root == "" {
		root = r.URL.Query().Get("path")
	}
	if root == "" {
		root = s.root
	}
	limit := normalizeFinderSearchLimit(r.URL.Query().Get("limit"))
	if r.URL.Path == "/api/fs/search-stream" {
		if raw := r.URL.Query().Get("limit"); raw != "" {
			parsed, err := strconv.Atoi(raw)
			if err != nil || parsed < 1 {
				finderHTTPJSON(w, finderRequestIDForHTTP(), nil, &finder.Error{Status: http.StatusBadRequest, Code: "invalid_request", MessageKey: "request.error.integer", Path: "limit", Message: "limit must be an integer"})
				return
			}
			limit = parsed
			if limit > finder.MaxSearchResults {
				limit = finder.MaxSearchResults
			}
		}
	}
	recursive := queryTrue(r.URL.Query().Get("recursive")) || r.URL.Path == "/api/batch/search" || r.URL.Path == "/api/fs/search-stream"
	directOnly := queryTrue(r.URL.Query().Get("direct_only"))
	batchSearch := r.URL.Path == "/api/batch/search"
	indexedOnly := batchSearch || queryTrue(r.URL.Query().Get("indexed_only"))
	cursor := r.URL.Query().Get("cursor")
	searchOptions := finder.SearchOptions{Cursor: cursor, IndexedOnly: indexedOnly}
	if r.URL.Path == "/api/fs/search-stream" {
		stream, ok := newJSONSSEWriter(w)
		if !ok {
			jsonResponse(w, http.StatusInternalServerError, map[string]any{"error": "streaming unavailable", "error_code": "streaming_unavailable"})
			return
		}
		writeFinderSSE := stream.write
		if err := writeFinderSSE("start", map[string]any{"root": root, "query": r.URL.Query().Get("query"), "limit": limit}); err != nil {
			return
		}
		product, finderErr := s.runFinderStreamProduct(r.Context(), func(ctx context.Context) (finderProduct, *finder.Error) {
			payload, err := s.finder.SearchStream(ctx, root, r.URL.Query().Get("query"), limit, recursive, queryTrue(r.URL.Query().Get("direct_only")), queryTrue(r.URL.Query().Get("minimal")), func(files []map[string]any, coverage map[string]any) error {
				return writeFinderSSE("chunk", map[string]any{"files": files, "coverage": coverage})
			})
			return finderProduct{data: payload}, err
		})
		if finderErr != nil {
			_ = writeFinderSSE("error", finderErr.Payload())
			return
		}
		_ = writeFinderSSE("done", map[string]any{"files": product.data["count"], "coverage": product.data["progressive_coverage"]})
		return
	}
	if recursive && !directOnly && cursor == "" && !indexedOnly {
		s.enqueueFinderJSON(w, r.Context(), r.URL.Path, "search", root, func(ctx context.Context) (map[string]any, *finder.Error) {
			return s.finder.SearchWithOptions(ctx, root, r.URL.Query().Get("query"), limit, recursive, false, queryTrue(r.URL.Query().Get("minimal")), searchOptions)
		})
		return
	}
	if batchSearch {
		s.enqueueFinderJSON(w, r.Context(), r.URL.Path, "search", root, func(ctx context.Context) (map[string]any, *finder.Error) {
			return s.finder.SearchWithOptions(ctx, root, r.URL.Query().Get("query"), limit, true, false, queryTrue(r.URL.Query().Get("minimal")), finder.SearchOptions{Cursor: cursor})
		})
		return
	}
	if cursor != "" {
		s.enqueueFinderJSON(w, r.Context(), r.URL.Path, "search", root, func(ctx context.Context) (map[string]any, *finder.Error) {
			return s.finder.SearchWithOptions(ctx, root, r.URL.Query().Get("query"), limit, recursive, directOnly, queryTrue(r.URL.Query().Get("minimal")), searchOptions)
		})
		return
	}
	product, finderErr := s.runFinderProduct(r.Context(), func(ctx context.Context) (finderProduct, *finder.Error) {
		payload, err := s.finder.SearchWithOptions(ctx, root, r.URL.Query().Get("query"), limit, recursive, directOnly, queryTrue(r.URL.Query().Get("minimal")), searchOptions)
		return finderProduct{data: payload}, err
	})
	if finderErr != nil {
		finderHTTPJSON(w, finderRequestIDForHTTP(), nil, finderErr)
		return
	}
	finderHTTPJSON(w, finderRequestIDForHTTP(), product.data, nil)
}

func (s *server) fsIndexStatus(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	root := r.URL.Query().Get("root")
	if root == "" {
		root = r.URL.Query().Get("path")
	}
	if root == "" {
		root = s.root
	}
	product, finderErr := s.runFinderProduct(r.Context(), func(ctx context.Context) (finderProduct, *finder.Error) {
		payload, err := s.finder.IndexStatus(ctx, root)
		return finderProduct{data: payload}, err
	})
	if finderErr != nil {
		finderHTTPJSON(w, finderRequestIDForHTTP(), nil, finderErr)
		return
	}
	finderHTTPJSON(w, finderRequestIDForHTTP(), product.data, nil)
}

func (s *server) fsResolveCandidates(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	var body struct {
		Paths []string `json:"paths"`
	}
	if err := decodeJSONBody(r.Body, 32*1024, &body); err != nil {
		finderHTTPJSON(w, finderRequestIDForHTTP(), nil, &finder.Error{Status: http.StatusBadRequest, Code: "invalid_request", MessageKey: "request.error.list", Message: "paths must contain 1 to 8 strings"})
		return
	}
	product, finderErr := s.runFinderProduct(r.Context(), func(ctx context.Context) (finderProduct, *finder.Error) {
		payload, err := s.finder.ResolveCandidates(ctx, body.Paths)
		return finderProduct{data: payload}, err
	})
	if finderErr != nil {
		finderHTTPJSON(w, finderRequestIDForHTTP(), nil, finderErr)
		return
	}
	finderHTTPJSON(w, finderRequestIDForHTTP(), product.data, nil)
}

func (s *server) fsWrite(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	var body struct {
		Path          string `json:"path"`
		Content       string `json:"content"`
		ExpectedMtime *int64 `json:"expected_mtime"`
	}
	if err := decodeJSONBody(r.Body, finder.MaxWriteBytes+4096, &body); err != nil || body.Path == "" {
		finderHTTPJSON(w, finderRequestIDForHTTP(), nil, &finder.Error{Status: http.StatusBadRequest, Code: "invalid_request", MessageKey: "request.error.invalid", Message: "invalid write request"})
		return
	}
	s.enqueueFinderJSON(w, r.Context(), r.URL.Path, "write", body.Path, func(ctx context.Context) (map[string]any, *finder.Error) {
		return s.finder.Write(ctx, body.Path, body.Content, body.ExpectedMtime)
	})
}

func (s *server) fsDelete(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	var body struct {
		Path      string `json:"path"`
		Recursive bool   `json:"recursive"`
	}
	if err := decodeJSONBody(r.Body, 8*1024, &body); err != nil || body.Path == "" {
		finderHTTPJSON(w, finderRequestIDForHTTP(), nil, &finder.Error{Status: http.StatusBadRequest, Code: "invalid_request", MessageKey: "request.error.invalid", Message: "invalid delete request"})
		return
	}
	s.enqueueFinderDelete(w, r.Context(), body.Path, body.Recursive)
}

func (s *server) fsUnindex(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	var body struct {
		Path string `json:"path"`
		Root string `json:"root"`
	}
	if err := decodeJSONBody(r.Body, 8*1024, &body); err != nil {
		finderHTTPJSON(w, finderRequestIDForHTTP(), nil, &finder.Error{Status: http.StatusBadRequest, Code: "invalid_request", MessageKey: "request.error.invalid", Message: "invalid unindex request"})
		return
	}
	path := body.Path
	if path == "" {
		path = body.Root
	}
	if path == "" {
		finderHTTPJSON(w, finderRequestIDForHTTP(), nil, &finder.Error{Status: http.StatusBadRequest, Code: "invalid_request", MessageKey: "request.error.invalid", Message: "invalid unindex request"})
		return
	}
	s.enqueueFinderJSON(w, r.Context(), r.URL.Path, "unindex", path, func(ctx context.Context) (map[string]any, *finder.Error) {
		return s.finder.Unindex(ctx, path)
	})
}

func (s *server) fsRename(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	var body struct {
		Path    string `json:"path"`
		NewName string `json:"new_name"`
	}
	if err := decodeJSONBody(r.Body, 8*1024, &body); err != nil {
		finderHTTPJSON(w, finderRequestIDForHTTP(), nil, &finder.Error{Status: http.StatusBadRequest, Code: "invalid_request", MessageKey: "request.error.invalid", Message: "invalid rename request"})
		return
	}
	s.enqueueFinderJSON(w, r.Context(), r.URL.Path, "rename", body.Path, func(ctx context.Context) (map[string]any, *finder.Error) {
		return s.finder.Rename(ctx, body.Path, body.NewName)
	})
}

func (s *server) fsMkdir(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	var body struct {
		Path string `json:"path"`
	}
	if err := decodeJSONBody(r.Body, 8*1024, &body); err != nil {
		finderHTTPJSON(w, finderRequestIDForHTTP(), nil, &finder.Error{Status: http.StatusBadRequest, Code: "invalid_request", MessageKey: "request.error.invalid", Message: "invalid mkdir request"})
		return
	}
	s.enqueueFinderJSON(w, r.Context(), r.URL.Path, "mkdir", body.Path, func(ctx context.Context) (map[string]any, *finder.Error) {
		return s.finder.Mkdir(ctx, body.Path)
	})
}

func (s *server) fsCount(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	path := r.URL.Query().Get("path")
	if path == "" {
		path = s.root
	}
	s.enqueueFinderJSON(w, r.Context(), r.URL.Path, "count", path, func(ctx context.Context) (map[string]any, *finder.Error) {
		return s.finder.Count(ctx, path)
	})
}

func (s *server) fsBatch(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	var body map[string]any
	if err := decodeJSONBody(r.Body, 64*1024, &body); err != nil || body == nil {
		finderHTTPJSON(w, finderRequestIDForHTTP(), nil, &finder.Error{Status: http.StatusBadRequest, Code: "invalid_request", MessageKey: "request.error.invalid", Message: "invalid filesystem batch"})
		return
	}
	requestsValue, present := body["requests"]
	if !present {
		requestsValue = []any{}
	}
	requests, ok := requestsValue.([]any)
	if !ok || len(requests) > finder.MaxBatchRequests {
		message := "requests must be a list"
		messageKey := "request.error.list"
		messageParams := map[string]any{"field": "requests"}
		details := map[string]any{"requests_type": fmt.Sprintf("%T", requestsValue)}
		if values, isList := requestsValue.([]any); isList {
			message = fmt.Sprintf("requests must contain at most %d items", finder.MaxBatchRequests)
			messageKey = "request.error.tooManyItems"
			messageParams["max"] = finder.MaxBatchRequests
			details = map[string]any{"requests": len(values), "maximum": finder.MaxBatchRequests}
		}
		finderHTTPJSON(w, finderRequestIDForHTTP(), nil, &finder.Error{
			Status: http.StatusBadRequest, Code: "invalid_request", MessageKey: messageKey, Message: message,
			MessageParams: messageParams, Details: details, Origin: "server.http",
			Stack: []map[string]any{{"component": "server.http", "operation": "POST /api/fs/batch", "code": "invalid_request"}},
		})
		return
	}
	if s.finderJobs != nil && s.events != nil {
		s.enqueueFinderJSONWithBudget(w, r.Context(), r.URL.Path, "batch", s.root, finderFastListBudget, func(ctx context.Context) (map[string]any, *finder.Error) {
			return s.buildFsBatch(ctx, body, requests)
		})
		return
	}
	product, finderErr := s.runFinderProduct(r.Context(), func(ctx context.Context) (finderProduct, *finder.Error) {
		payload, batchErr := s.buildFsBatch(ctx, body, requests)
		return finderProduct{data: payload}, batchErr
	})
	if finderErr != nil {
		finderHTTPJSON(w, finderRequestIDForHTTP(), nil, finderErr)
		return
	}
	finderHTTPJSON(w, finderRequestIDForHTTP(), product.data, nil)
}

func (s *server) buildFsBatch(ctx context.Context, body map[string]any, requests []any) (map[string]any, *finder.Error) {
	started := time.Now()
	responses := make([]map[string]any, 0, len(requests))
	listMilliseconds := 0.0
	infoMilliseconds := 0.0
	for index, rawRequest := range requests {
		if err := ctx.Err(); err != nil {
			return nil, &finder.Error{Status: http.StatusRequestTimeout, Code: "cancelled", MessageKey: "request.error.cancelled", Retryable: true, Message: err.Error()}
		}
		requestID := any(index)
		request, isObject := rawRequest.(map[string]any)
		if isObject && request["id"] != nil {
			requestID = request["id"]
		}
		response := map[string]any{"id": requestID, "ok": false, "status": http.StatusBadRequest}
		if !isObject {
			for key, value := range batchItemError("request must be an object", "request.error.object", map[string]any{"field": "request"}) {
				if key != "error_code" && key != "retryable" {
					response[key] = value
				}
			}
			responses = append(responses, response)
			continue
		}
		requestType := stringValue(request["type"])
		if requestType == "" {
			requestType = stringValue(request["op"])
		}
		requestPath := stringValue(request["path"])
		validTriggers, _ := batchTriggerCounts(request)
		if !validTriggers {
			if requestPath != "" {
				response["path"] = requestPath
			}
			for key, value := range batchItemError("invalid fs batch trigger", "request.error.unsupportedFsBatchOperation", map[string]any{"operation": "trigger"}) {
				if key != "error_code" && key != "retryable" {
					response[key] = value
				}
			}
			responses = append(responses, response)
			continue
		}
		if requestType != "list" && requestType != "info" {
			if requestPath != "" {
				response["path"] = requestPath
			}
			for key, value := range batchItemError("unsupported fs batch operation", "request.error.unsupportedFsBatchOperation", map[string]any{"operation": requestType}) {
				if key != "error_code" && key != "retryable" {
					response[key] = value
				}
			}
			responses = append(responses, response)
			continue
		}
		var payload map[string]any
		var finderErr *finder.Error
		itemStarted := time.Now()
		switch requestType {
		case "list":
			includeRepoInfo := true
			if value, exists := request["include_repo_info"]; exists {
				includeRepoInfo = value != false
			}
			payload, finderErr = s.finder.ListWithOptions(ctx, requestPath, includeRepoInfo)
		case "info":
			payload, finderErr = s.finder.Info(ctx, requestPath, true)
		}
		elapsed := float64(time.Since(itemStarted).Microseconds()) / 1000
		if requestType == "list" {
			listMilliseconds += elapsed
		} else {
			infoMilliseconds += elapsed
		}
		if finderErr != nil {
			response["status"] = finderErr.Status
			response["error"] = finderErr.Message
			params := map[string]any{}
			if finderErr.Path != "" {
				params["path"] = finderErr.Path
				response["path"] = finderErr.Path
			}
			response["user_message"] = map[string]any{"key": finderErr.MessageKey, "params": params, "fallback": finderErr.Message}
		} else {
			response["ok"] = true
			response["status"] = http.StatusOK
			response["payload"] = payload
			if request["include_watch_signature"] == true {
				if signature, signatureErr := s.finder.WatchSignature(ctx, requestPath); signatureErr != nil {
					response["ok"] = false
					response["status"] = signatureErr.Status
					response["error"] = signatureErr.Payload()
					delete(response, "payload")
				} else {
					response["watch_signature"] = signature
				}
			}
		}
		responses = append(responses, response)
	}
	summary := filesystemBatchSummary(body, requests)
	performance := map[string]any{}
	for key, value := range summary {
		performance[key] = value
	}
	performance["operation_ms"] = float64(time.Since(started).Microseconds()) / 1000
	performance["list_ms"] = listMilliseconds
	performance["info_ms"] = infoMilliseconds
	performance["list_details"] = map[string]any{}
	return map[string]any{"responses": responses, "performance": performance}, nil
}

var allowedFilesystemBatchTriggers = map[string]bool{
	"legacy": true, "tree-render": true, "explicit-user": true, "fresh-repair": true,
	"watch-diff": true, "watch-diff-fallback": true, "deferred-interaction": true,
	"repo-enrichment": true, "sync-revalidation": true,
}

func stringValue(value any) string {
	if text, ok := value.(string); ok {
		return text
	}
	return ""
}

func batchTriggerCounts(item map[string]any) (bool, map[string]int) {
	raw, present := item["trigger_counts"]
	if !present || raw == nil {
		trigger := stringValue(item["trigger"])
		if trigger == "" {
			trigger = "legacy"
		}
		if !allowedFilesystemBatchTriggers[trigger] {
			return false, nil
		}
		return true, map[string]int{trigger: 1}
	}
	counts, ok := raw.(map[string]any)
	if !ok || len(counts) == 0 {
		return false, nil
	}
	result := make(map[string]int, len(counts))
	for trigger, rawCount := range counts {
		if !allowedFilesystemBatchTriggers[trigger] {
			return false, nil
		}
		count, ok := rawCount.(float64)
		if !ok || count < 1 || count > finder.MaxBatchRequests || count != float64(int(count)) {
			return false, nil
		}
		result[trigger] = int(count)
	}
	return true, result
}

func batchItemError(message, messageKey string, params map[string]any) map[string]any {
	return map[string]any{
		"error":        message,
		"error_code":   "invalid_request",
		"message_key":  messageKey,
		"user_message": map[string]any{"key": messageKey, "params": params, "fallback": message},
		"retryable":    false,
	}
}

func filesystemBatchSummary(body map[string]any, requests []any) map[string]any {
	operations := map[string]int{}
	triggers := map[string]int{}
	paths := make([]string, 0, 8)
	for _, raw := range requests {
		item, ok := raw.(map[string]any)
		if !ok {
			operations["invalid"]++
			continue
		}
		operation := stringValue(item["type"])
		if operation == "" {
			operation = stringValue(item["op"])
		}
		if operation != "list" && operation != "info" {
			operation = "invalid"
		}
		operations[operation]++
		valid, itemTriggers := batchTriggerCounts(item)
		if !valid {
			triggers["invalid"]++
		} else {
			for trigger, count := range itemTriggers {
				triggers[trigger] += count
			}
		}
		path := stringValue(item["path"])
		if path != "" && len(paths) < 8 {
			digest := sha256.Sum256([]byte(path))
			fingerprint := hex.EncodeToString(digest[:])[:16]
			seen := false
			for _, existing := range paths {
				if existing == fingerprint {
					seen = true
					break
				}
			}
			if !seen {
				paths = append(paths, fingerprint)
			}
		}
	}
	clientRevision := stringValue(body["client_revision"])
	if !validBatchClientRevision(clientRevision) {
		clientRevision = "unknown"
	}
	clientScope := stringValue(body["client_scope"])
	if clientScope != "browser" {
		clientScope = "legacy"
	}
	return map[string]any{"batch_size": len(requests), "operations": operations, "path_fingerprints": paths, "triggers": triggers, "client_revision": clientRevision, "client_scope": clientScope}
}

func validBatchClientRevision(value string) bool {
	if len(value) < 1 || len(value) > 80 {
		return false
	}
	for _, character := range value {
		if !((character >= 'A' && character <= 'Z') || (character >= 'a' && character <= 'z') || (character >= '0' && character <= '9') || character == '.' || character == '_' || character == '-') {
			return false
		}
	}
	return true
}

func (s *server) fsZip(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	path := r.URL.Query().Get("path")
	product, finderErr := s.runFinderProduct(r.Context(), func(ctx context.Context) (finderProduct, *finder.Error) {
		data, filename, finderErr := s.finder.Zip(ctx, path, s.fileTransferMaxBytes())
		return finderProduct{body: data, contentType: "application/zip", filename: filename}, finderErr
	})
	if finderErr != nil {
		finderHTTPJSON(w, finderRequestIDForHTTP(), nil, finderErr)
		return
	}
	w.Header().Set("Content-Type", "application/zip")
	w.Header().Set("Content-Disposition", `attachment; filename="`+strings.ReplaceAll(product.filename, `"`, "")+`"`)
	w.Header().Set("Content-Length", strconv.Itoa(len(product.body)))
	_, _ = w.Write(product.body)
}

func (s *server) fsHTMLPreview(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	path := r.URL.Query().Get("path")
	product, finderErr := s.runFinderProduct(r.Context(), func(ctx context.Context) (finderProduct, *finder.Error) {
		data, finderErr := s.finder.HTMLPreview(ctx, path, r.Header.Get("Accept-Language"), s.fileTransferMaxBytes())
		return finderProduct{body: data, contentType: "text/html; charset=utf-8"}, finderErr
	})
	if finderErr != nil {
		finderHTTPJSON(w, finderRequestIDForHTTP(), nil, finderErr)
		return
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.Header().Set("Content-Disposition", "inline")
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Content-Length", strconv.Itoa(len(product.body)))
	_, _ = w.Write(product.body)
}

func (s *server) fsBlame(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	path := r.URL.Query().Get("path")
	if path == "" {
		finderHTTPJSON(w, finderRequestIDForHTTP(), nil, &finder.Error{Status: http.StatusBadRequest, Code: "invalid_request", MessageKey: "request.error.pathRequired", Message: "path is required"})
		return
	}
	ref := r.URL.Query().Get("ref")
	s.enqueueFinderJSON(w, r.Context(), r.URL.Path, "blame", path, func(ctx context.Context) (map[string]any, *finder.Error) {
		return s.finder.Blame(ctx, path, ref)
	})
}

func (s *server) upload(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	maxUploadBytes := s.fileTransferMaxBytes()
	if r.ContentLength < 0 {
		finderHTTPJSON(w, finderRequestIDForHTTP(), nil, &finder.Error{Status: http.StatusLengthRequired, Code: "length_required", MessageKey: "request.error.lengthRequired", Message: "missing Content-Length"})
		return
	}
	if r.ContentLength > maxUploadBytes {
		finderHTTPJSON(w, finderRequestIDForHTTP(), nil, &finder.Error{Status: http.StatusRequestEntityTooLarge, Code: "too_large", MessageKey: "fs.error.tooLarge", Message: "upload is too large"})
		return
	}
	r.Body = http.MaxBytesReader(w, r.Body, maxUploadBytes)
	if err := r.ParseMultipartForm(maxUploadBytes); err != nil {
		if _, tooLarge := err.(*http.MaxBytesError); tooLarge {
			finderHTTPJSON(w, finderRequestIDForHTTP(), nil, &finder.Error{Status: http.StatusRequestEntityTooLarge, Code: "too_large", MessageKey: "fs.error.tooLarge", Message: "upload is too large"})
		} else {
			finderHTTPJSON(w, finderRequestIDForHTTP(), nil, &finder.Error{Status: http.StatusBadRequest, Code: "invalid_request", MessageKey: "request.error.invalid", Message: "invalid upload data"})
		}
		return
	}
	formFiles := r.MultipartForm.File["files"]
	if len(formFiles) == 0 {
		finderHTTPJSON(w, finderRequestIDForHTTP(), nil, &finder.Error{Status: http.StatusBadRequest, Code: "invalid_request", MessageKey: "upload.error.filesRequired", Message: "no files supplied"})
		return
	}
	if len(formFiles) > s.fileTransferMaxFiles() {
		finderHTTPJSON(w, finderRequestIDForHTTP(), nil, &finder.Error{Status: http.StatusRequestEntityTooLarge, Code: "too_many_files", MessageKey: "upload.error.tooManyFiles", Message: "too many files"})
		return
	}
	files := make([]finder.UploadFile, 0, len(formFiles))
	var totalBytes int64
	for _, header := range formFiles {
		file, err := header.Open()
		if err != nil {
			finderHTTPJSON(w, finderRequestIDForHTTP(), nil, &finder.Error{Status: http.StatusBadRequest, Code: "invalid_request", MessageKey: "request.error.invalid", Message: "invalid upload data"})
			return
		}
		data, readErr := io.ReadAll(io.LimitReader(file, maxUploadBytes+1))
		closeErr := file.Close()
		totalBytes += int64(len(data))
		if readErr != nil || closeErr != nil || int64(len(data)) > maxUploadBytes || totalBytes > maxUploadBytes {
			finderHTTPJSON(w, finderRequestIDForHTTP(), nil, &finder.Error{Status: http.StatusRequestEntityTooLarge, Code: "too_large", MessageKey: "fs.error.tooLarge", Message: "upload is too large"})
			return
		}
		files = append(files, finder.UploadFile{Name: header.Filename, Data: data})
	}
	user, _ := s.identity(r)
	session := r.URL.Query().Get("session")
	editorPath := r.URL.Query().Get("editor_path")
	baseDir := r.URL.Query().Get("base_dir")
	if editorPath == "" && baseDir == "" {
		if session == "" || s.tmuxCommand("has-session", "-t", session).Run() != nil {
			finderHTTPJSON(w, finderRequestIDForHTTP(), nil, &finder.Error{Status: http.StatusNotFound, Code: "session_not_found", MessageKey: "common.sessionNotFound", Path: session, Message: "unknown session"})
			return
		}
	}
	product, finderErr := s.runFinderProduct(r.Context(), func(ctx context.Context) (finderProduct, *finder.Error) {
		payload, err := s.finder.Upload(ctx, session, user.username, editorPath, baseDir, files)
		return finderProduct{data: payload}, err
	})
	if finderErr != nil {
		finderHTTPJSON(w, finderRequestIDForHTTP(), nil, finderErr)
		return
	}
	finderHTTPJSON(w, finderRequestIDForHTTP(), product.data, nil)
}

func (s *server) fileTransferMaxBytes() int64 {
	const defaultLimit = int64(300 * 1024 * 1024)
	const legacyDefaultLimit = int64(20 * 1024 * 1024)
	const minimumLimit = int64(1 * 1024 * 1024)
	const maximumLimit = int64(512 * 1024 * 1024)
	limit := defaultLimit
	for _, name := range []string{"YOLOMUX_FILE_TRANSFER_MAX_BYTES", "YOLOMUX_UPLOAD_MAX_BYTES"} {
		if value, present := os.LookupEnv(name); present {
			if parsed, parseErr := strconv.ParseInt(strings.TrimSpace(value), 10, 64); parseErr == nil && parsed > 0 {
				limit = parsed
			}
			break
		}
	}
	defaultLimitForSettings := limit
	data, err := os.ReadFile(filepath.Join(s.config, "settings.yaml"))
	if err != nil {
		if limit < minimumLimit {
			return minimumLimit
		}
		if limit > maximumLimit {
			return maximumLimit
		}
		return limit
	}
	inUploads := false
	for _, line := range strings.Split(string(data), "\n") {
		trimmed := strings.TrimSpace(strings.SplitN(line, "#", 2)[0])
		if trimmed == "uploads:" {
			inUploads = true
			continue
		}
		if len(line) > 0 && line[0] != ' ' && line[0] != '\t' {
			inUploads = false
		}
		if !inUploads {
			continue
		}
		key, value, ok := strings.Cut(trimmed, ":")
		if !ok || strings.TrimSpace(key) != "max_bytes" {
			continue
		}
		parsed, parseErr := strconv.ParseInt(strings.TrimSpace(value), 10, 64)
		if parseErr == nil {
			if parsed == legacyDefaultLimit {
				limit = defaultLimitForSettings
			} else {
				limit = parsed
			}
		}
		break
	}
	if limit < minimumLimit {
		return minimumLimit
	}
	if limit > maximumLimit {
		return maximumLimit
	}
	return limit
}

func (s *server) fileTransferMaxFiles() int {
	const defaultLimit = 16
	limit := defaultLimit
	if value, present := os.LookupEnv("YOLOMUX_UPLOAD_MAX_FILES"); present {
		if parsed, parseErr := strconv.Atoi(strings.TrimSpace(value)); parseErr == nil && parsed > 0 {
			limit = parsed
		}
	}
	return limit
}

func (s *server) fileTransferFilenameTemplate() string {
	if value, present := os.LookupEnv("YOLOMUX_UPLOAD_FILENAME_TEMPLATE"); present {
		return strings.TrimSpace(value)
	}
	data, err := os.ReadFile(filepath.Join(s.config, "settings.yaml"))
	if err != nil {
		return ""
	}
	inUploads := false
	for _, line := range strings.Split(string(data), "\n") {
		trimmed := strings.TrimSpace(strings.SplitN(line, "#", 2)[0])
		if trimmed == "uploads:" {
			inUploads = true
			continue
		}
		if len(line) > 0 && line[0] != ' ' && line[0] != '\t' {
			inUploads = false
		}
		if !inUploads {
			continue
		}
		key, value, ok := strings.Cut(trimmed, ":")
		if ok && strings.TrimSpace(key) == "filename_template" {
			return strings.TrimSpace(yamlValue(value))
		}
	}
	return ""
}

func queryTrue(value string) bool {
	switch strings.ToLower(strings.TrimSpace(value)) {
	case "1", "true", "yes", "on":
		return true
	default:
		return false
	}
}

func parseByteRange(value string) (int64, int64, bool, error) {
	value = strings.TrimSpace(value)
	if value == "" {
		return 0, 0, false, nil
	}
	if !strings.HasPrefix(value, "bytes=") {
		return 0, 0, false, fmt.Errorf("unsupported range")
	}
	parts := strings.Split(strings.TrimPrefix(value, "bytes="), "-")
	if len(parts) != 2 || parts[0] == "" {
		return 0, 0, false, fmt.Errorf("invalid byte range")
	}
	start, err := strconv.ParseInt(parts[0], 10, 64)
	if err != nil || start < 0 {
		return 0, 0, false, fmt.Errorf("invalid byte range")
	}
	end := int64(^uint64(0) >> 1)
	if parts[1] != "" {
		end, err = strconv.ParseInt(parts[1], 10, 64)
		if err != nil || end < start {
			return 0, 0, false, fmt.Errorf("invalid byte range")
		}
	}
	return start, end, true, nil
}

func (s *server) watchRoots(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	var body map[string]any
	if err := decodeJSONBody(r.Body, 64*1024, &body); err != nil || body == nil {
		finderHTTPJSON(w, finderRequestIDForHTTP(), nil, &finder.Error{Status: http.StatusBadRequest, Code: "invalid_request", MessageKey: "request.error.invalidJson", Message: "invalid JSON"})
		return
	}
	product, finderErr := s.runFinderProduct(r.Context(), func(ctx context.Context) (finderProduct, *finder.Error) {
		payload, err := s.finder.WatchRoots(ctx, body)
		return finderProduct{data: payload}, err
	})
	if finderErr != nil {
		finderHTTPJSON(w, finderRequestIDForHTTP(), nil, finderErr)
		return
	}
	s.syncFinderWatchCapability()
	finderHTTPJSON(w, finderRequestIDForHTTP(), product.data, nil)
}

func (s *server) syncFinderWatchCapability() {
	if s.capabilities == nil || s.finder == nil {
		return
	}
	available, degraded, reason := s.finder.WatchStatus()
	entry := capabilities.Entry{Available: true, Degraded: degraded}
	if !available {
		entry.Reason = &capabilities.Reason{
			Code: "watch_fallback", MessageKey: "capability.finderWatchDegraded",
			MessageParams: map[string]any{"reason": reason},
		}
	}
	s.capabilities.Set("finder.watch.v1", entry)
}

func finderErrorPayload(errorValue finder.Error) map[string]any { return errorValue.Payload() }

func (s *server) clientEvents(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	sseWriter, ok := newJSONSSEWriter(w)
	if !ok {
		http.Error(w, "streaming unavailable", http.StatusInternalServerError)
		return
	}
	writeSSE := func(event string, payload any) {
		_ = sseWriter.write(event, payload)
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
	writeDemandedOperationTerminals := func() {
		for _, id := range demandedIDs {
			if record := s.operationRecord(id); operationIsTerminal(record) && !record.Acknowledged && !record.DeliveryAcknowledged {
				if event := record.TerminalEvent; event != nil {
					writeSSE("operation_terminal", operationTerminalWire(event))
				}
			}
		}
	}
	writeDemandedOperationTerminals()
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
	// A bounded or deliberately delayed stream can receive the terminal event while its startup
	// delay is active, then lose it to a later queue replacement before the read loop begins. Recheck
	// demanded receipts at the handoff boundary so an accepted operation cannot strand its waiter.
	writeDemandedOperationTerminals()
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
	if s.finderJobs != nil {
		s.finderJobs.Cancel(id)
	}
	if s.finderBackgroundJobs != nil {
		s.finderBackgroundJobs.Cancel(id)
	}
	if s.finderBulkJobs != nil {
		s.finderBulkJobs.Cancel(id)
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
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	limit := 120
	if value, err := strconv.Atoi(r.URL.Query().Get("limit")); err == nil && value > 0 && value < 1000 {
		limit = value
	}
	session := strings.TrimSpace(r.URL.Query().Get("session"))
	if session != "" && s.tmuxCommand("has-session", "-t", session).Run() != nil {
		jsonResponse(w, http.StatusNotFound, map[string]any{"error": "unknown session", "session": session})
		return
	}
	jsonResponse(w, http.StatusOK, map[string]any{"events": s.eventLogTail(session, limit), "session": session, "limit": minInt(limit, 1000)})
}

func goMetric(value any, reasonCode, reason string) map[string]any {
	if number, ok := value.(float64); ok && !math.IsNaN(number) && !math.IsInf(number, 0) {
		return map[string]any{"state": "measured", "value": number, "reason_code": "", "reason": ""}
	}
	if number, ok := value.(uint64); ok {
		return map[string]any{"state": "measured", "value": number, "reason_code": "", "reason": ""}
	}
	return map[string]any{"state": "unavailable", "value": nil, "reason_code": reasonCode, "reason": reason}
}

func (s *server) readGoProcessMetrics(now time.Time) goProcessMetrics {
	s.processMetricsCacheMu.Lock()
	defer s.processMetricsCacheMu.Unlock()
	if s.processMetricsCacheValid && !now.Before(s.processMetricsCacheAt) && now.Sub(s.processMetricsCacheAt) < goProcessMetricsCacheTTL {
		s.processMetricsCacheHits++
		return s.processMetricsCache
	}
	started := time.Now()
	result := s.readGoProcessMetricsUncached(now)
	duration := time.Since(started)
	s.processMetricsCacheMisses++
	s.processMetricsLastDuration = duration
	s.processMetricsDurationTotal += duration
	s.processMetricsLastThreadRows = uint64(len(result.Threads))
	s.processMetricsLastChildRows = uint64(len(result.ChildProcesses))
	s.processMetricsCache = result
	s.processMetricsCacheAt = now
	s.processMetricsCacheValid = true
	return result
}

func (s *server) readGoProcessMetricsUncached(now time.Time) goProcessMetrics {
	result := goProcessMetrics{
		PID:                 os.Getpid(),
		StartedAt:           s.startedAt,
		CPUReasonCode:       "process_cpu_unavailable",
		CPUReason:           "The operating system did not return a usable Go process CPU sample",
		RSSReasonCode:       "process_read_failed",
		RSSReason:           "The operating system did not return Go process memory",
		ThreadReasonCode:    "process_threads_unavailable",
		ThreadReason:        "The operating system did not return Go process thread details",
		GoroutineReasonCode: "process_goroutines_unavailable",
		GoroutineReason:     "The Go runtime did not return a goroutine count",
	}
	if result.StartedAt.IsZero() {
		result.StartedAt = now
	}
	goroutineCount := runtime.NumGoroutine()
	if s.goroutineCountFn != nil {
		goroutineCount = s.goroutineCountFn()
	}
	if goroutineCount > 0 {
		count := uint64(goroutineCount)
		result.GoroutineCount = &count
		result.GoroutineReasonCode = ""
		result.GoroutineReason = ""
	}
	if s.childProcessMetricsFn != nil {
		result.ChildProcesses = s.childProcessMetricsFn(now)
	} else {
		result.ChildProcesses = s.readGoChildProcessMetrics(now)
	}
	if runtime.GOOS == "darwin" {
		row, ok := currentDarwinGoProcess(result.PID)
		if !ok {
			return result
		}
		rss := row.RSSBytes
		result.RSSBytes = &rss
		result.RSSReasonCode, result.RSSReason = "", ""
		s.processMetricsMu.Lock()
		if s.processCPUValid && now.After(s.processMetricsAt) && row.CPUSeconds >= s.processCPUSeconds {
			elapsed := now.Sub(s.processMetricsAt).Seconds()
			if elapsed > 0 {
				percent := (row.CPUSeconds - s.processCPUSeconds) / elapsed * 100
				if !math.IsNaN(percent) && !math.IsInf(percent, 0) && percent >= 0 {
					result.CPUPercent = &percent
					result.CPUReasonCode = ""
					result.CPUReason = ""
				}
			}
		}
		s.processCPUSeconds, s.processMetricsAt, s.processCPUValid = row.CPUSeconds, now, true
		s.processMetricsMu.Unlock()
		return result
	}
	if runtime.GOOS != "linux" {
		return result
	}
	data, err := os.ReadFile(filepath.Join("/proc", strconv.Itoa(result.PID), "stat"))
	if err != nil {
		return result
	}
	row, ok := stats.ParseLinuxProcessStat(string(data), "", uint64(os.Getpagesize()), 100)
	if !ok {
		return result
	}
	rss := row.RSSBytes
	result.RSSBytes = &rss
	result.RSSReasonCode = ""
	result.RSSReason = ""
	tasks, taskErr := os.ReadDir(filepath.Join("/proc", strconv.Itoa(result.PID), "task"))
	if taskErr == nil {
		var threadCount uint64
		for _, task := range tasks {
			if task.IsDir() {
				if _, parseErr := strconv.ParseUint(task.Name(), 10, 32); parseErr == nil {
					threadCount++
				}
			}
		}
		if threadCount > 0 {
			result.ThreadCount = &threadCount
			result.ThreadReasonCode = ""
			result.ThreadReason = ""
		}
		result.Threads = s.readGoThreadMetrics(now, result.PID, tasks)
	}
	s.processMetricsMu.Lock()
	if s.processCPUValid && now.After(s.processMetricsAt) && row.CPUSeconds >= s.processCPUSeconds {
		elapsed := now.Sub(s.processMetricsAt).Seconds()
		if elapsed > 0 {
			percent := (row.CPUSeconds - s.processCPUSeconds) / elapsed * 100
			if !math.IsNaN(percent) && !math.IsInf(percent, 0) && percent >= 0 {
				result.CPUPercent = &percent
				result.CPUReasonCode = ""
				result.CPUReason = ""
			}
		}
	}
	s.processCPUSeconds = row.CPUSeconds
	s.processMetricsAt = now
	s.processCPUValid = true
	s.processMetricsMu.Unlock()
	return result
}

func currentDarwinGoProcess(pid int) (procEntry, bool) {
	row, ok := stats.DarwinProcessMetricsForPID(pid)
	if !ok {
		return procEntry{}, false
	}
	return procEntry{PID: row.PID, Identity: row.Identity, CPUSeconds: row.CPUSeconds, RSSBytes: row.RSSBytes}, true
}

const maxGoThreads = 256

func (s *server) readGoThreadMetrics(now time.Time, pid int, tasks []os.DirEntry) []goThreadMetric {
	threads := make([]goThreadMetric, 0, minInt(len(tasks), maxGoThreads))
	for _, task := range tasks {
		if len(threads) >= maxGoThreads || !task.IsDir() {
			continue
		}
		tid, err := strconv.Atoi(task.Name())
		if err != nil || tid <= 0 {
			continue
		}
		data, err := os.ReadFile(filepath.Join("/proc", strconv.Itoa(pid), "task", task.Name(), "stat"))
		if err != nil {
			continue
		}
		row, ok := stats.ParseLinuxProcessStat(string(data), "", uint64(os.Getpagesize()), 100)
		if !ok {
			continue
		}
		metric := goThreadMetric{
			TID: tid, Name: row.Binary, State: linuxChildProcessState(string(data)),
			CPUReasonCode: "thread_cpu_first_sample",
			CPUReason:     "The operating system needs a second thread sample to calculate CPU",
		}
		if percent := s.threadCPUPercent(now, row.Identity, row.CPUSeconds); percent != nil {
			metric.CPUPercent = percent
			metric.CPUReasonCode = ""
			metric.CPUReason = ""
		}
		threads = append(threads, metric)
	}
	sort.Slice(threads, func(i, j int) bool {
		if threads[i].Name != threads[j].Name {
			return threads[i].Name < threads[j].Name
		}
		return threads[i].TID < threads[j].TID
	})
	return threads
}

const maxGoChildProcesses = 64

// readGoChildProcessMetrics samples only the Go server's descendant tree. It
// deliberately does not scan all host processes: a machine-wide process list
// would make unrelated agents appear to be YOmux daemons. The process table
// seam is injectable in tests, so production /proc state is never needed by a
// deterministic test.
func (s *server) readGoChildProcessMetrics(now time.Time) []goChildProcessMetric {
	var entries []procEntry
	var err error
	if s.childProcessEntriesFn != nil {
		entries, err = s.cachedProcessEntriesWith(now, func() ([]procEntry, error) {
			return s.childProcessEntriesFn(os.Getpid())
		})
	} else {
		entries, err = s.cachedProcessEntries(now)
	}
	if err != nil {
		return nil
	}
	byParent := make(map[int][]procEntry)
	for _, entry := range entries {
		if entry.PID > 0 && entry.PPID > 0 && entry.PID != os.Getpid() {
			byParent[entry.PPID] = append(byParent[entry.PPID], entry)
		}
	}
	queue := append([]procEntry(nil), byParent[os.Getpid()]...)
	result := make([]goChildProcessMetric, 0, len(queue))
	seen := make(map[int]struct{})
	for len(queue) > 0 && len(result) < maxGoChildProcesses {
		entry := queue[0]
		queue = queue[1:]
		if _, ok := seen[entry.PID]; ok {
			continue
		}
		seen[entry.PID] = struct{}{}
		queue = append(queue, byParent[entry.PID]...)
		metric := goChildProcessMetric{
			PID: entry.PID, PPID: entry.PPID, Label: childProcessLabel(entry), State: "running",
			CPUReasonCode: "child_process_cpu_unavailable",
			CPUReason:     "The operating system did not return a usable child-process CPU sample",
			RSSReasonCode: "child_process_memory_unavailable",
			RSSReason:     "The operating system did not return child-process memory",
		}
		if metric.Label == "" {
			metric.Label = fmt.Sprintf("pid:%d", entry.PID)
		}
		if runtime.GOOS == "darwin" {
			if row, ok := currentDarwinGoProcess(entry.PID); ok {
				metric.State = "running"
				rss := row.RSSBytes
				metric.RSSBytes = &rss
				metric.RSSReasonCode, metric.RSSReason = "", ""
				metric.CPUPercent = s.childCPUPercent(now, row.Identity, row.CPUSeconds)
				if metric.CPUPercent != nil {
					metric.CPUReasonCode, metric.CPUReason = "", ""
				}
			}
		} else if runtime.GOOS == "linux" {
			data, readErr := os.ReadFile(filepath.Join("/proc", strconv.Itoa(entry.PID), "stat"))
			if readErr == nil {
				metric.State = linuxChildProcessState(string(data))
				row, ok := stats.ParseLinuxProcessStat(string(data), entry.Executable, uint64(os.Getpagesize()), 100)
				if ok {
					rss := row.RSSBytes
					metric.RSSBytes = &rss
					metric.RSSReasonCode = ""
					metric.RSSReason = ""
					metric.CPUPercent = s.childCPUPercent(now, row.Identity, row.CPUSeconds)
					if metric.CPUPercent != nil {
						metric.CPUReasonCode = ""
						metric.CPUReason = ""
					}
				}
			}
		}
		result = append(result, metric)
	}
	sort.Slice(result, func(i, j int) bool {
		if result[i].Label != result[j].Label {
			return result[i].Label < result[j].Label
		}
		return result[i].PID < result[j].PID
	})
	return result
}

func cloneProcEntries(entries []procEntry) []procEntry {
	if entries == nil {
		return nil
	}
	return append([]procEntry(nil), entries...)
}

func cloneAgentRecords(records []stats.AgentRecord) []stats.AgentRecord {
	if records == nil {
		return nil
	}
	return append([]stats.AgentRecord(nil), records...)
}

func filterOpenCodeAgentRecords(records []stats.AgentRecord) []stats.AgentRecord {
	filtered := make([]stats.AgentRecord, 0, len(records))
	for _, record := range records {
		if record.Kind == "opencode" {
			filtered = append(filtered, record)
		}
	}
	return filtered
}

// cachedAgentInventory shares the short-lived global process/tmux snapshot
// between the status and token collectors. The cache is deliberately tiny:
// it removes same-cadence duplicate scans without turning agent attribution
// into a stale long-lived roster.
func (s *server) cachedAgentInventory(ctx context.Context, sources stats.AgentInventorySources) ([]stats.AgentRecord, []error) {
	if err := ctx.Err(); err != nil {
		return nil, []error{err}
	}
	s.agentInventoryMu.Lock()
	defer s.agentInventoryMu.Unlock()
	now := time.Now()
	if s.agentInventoryCacheValid && !now.Before(s.agentInventoryAt) && now.Sub(s.agentInventoryAt) < goAgentInventoryCacheTTL {
		s.agentInventoryCacheHits++
		return cloneAgentRecords(s.agentInventoryRecords), append([]error(nil), s.agentInventoryErrors...)
	}
	s.agentInventoryCacheMisses++
	started := time.Now()
	records, inventoryErrors := stats.BuildAgentInventory(ctx, sources)
	duration := time.Since(started)
	s.agentInventoryLastDuration = duration
	s.agentInventoryDurationTotal += duration
	if len(inventoryErrors) == 0 {
		s.agentInventoryRecords = cloneAgentRecords(records)
		s.agentInventoryErrors = nil
		s.agentInventoryAt = now
		s.agentInventoryCacheValid = true
	}
	return records, inventoryErrors
}

// cachedProcessEntries is the fallback topology reader used when a scoped
// descendant reader is not installed. The per-child stat files are still read
// by readGoChildProcessMetrics on every uncached metrics sample, so CPU/RSS
// values remain current; only roster discovery is bounded to five seconds.
// Tests inject processEntriesFn and therefore never touch host /proc.
func (s *server) cachedProcessEntries(now time.Time) ([]procEntry, error) {
	entriesFn := s.processEntriesFn
	if entriesFn == nil {
		entriesFn = processEntries
	}
	return s.cachedProcessEntriesWith(now, entriesFn)
}

func (s *server) cachedProcessEntriesWith(now time.Time, entriesFn func() ([]procEntry, error)) ([]procEntry, error) {
	s.processEntriesCacheMu.Lock()
	defer s.processEntriesCacheMu.Unlock()
	if s.processEntriesCacheValid && !now.Before(s.processEntriesCacheAt) && now.Sub(s.processEntriesCacheAt) < goProcessEntriesCacheTTL {
		s.processEntriesCacheHits++
		return cloneProcEntries(s.processEntriesCache), nil
	}
	started := time.Now()
	entries, err := entriesFn()
	duration := time.Since(started)
	s.processEntriesCacheMisses++
	s.processEntriesCacheLastDuration = duration
	s.processEntriesCacheDurationTotal += duration
	if err != nil {
		return nil, err
	}
	s.processEntriesCache = cloneProcEntries(entries)
	s.processEntriesCacheAt = now
	s.processEntriesCacheValid = true
	return cloneProcEntries(entries), nil
}

func linuxChildProcessState(stat string) string {
	closeParen := strings.LastIndex(stat, ") ")
	if closeParen < 0 {
		return "unknown"
	}
	fields := strings.Fields(stat[closeParen+2:])
	if len(fields) == 0 {
		return "unknown"
	}
	switch fields[0] {
	case "R":
		return "running"
	case "S", "D", "I":
		return "sleeping"
	case "T", "t":
		return "stopped"
	case "Z":
		return "zombie"
	case "X", "x":
		return "dead"
	default:
		return fields[0]
	}
}

type childCPUState struct {
	At         time.Time
	Identity   string
	CPUSeconds float64
}

func (s *server) childCPUPercent(now time.Time, identity string, cpuSeconds float64) *float64 {
	// Reuse the process-metrics mutex for this small bounded map. It is sampled
	// only from the status path and avoids a second mutable sampler owner.
	s.processMetricsMu.Lock()
	defer s.processMetricsMu.Unlock()
	if s.childCPU == nil {
		s.childCPU = make(map[string]childCPUState)
	}
	previous, ok := s.childCPU[identity]
	s.childCPU[identity] = childCPUState{At: now, Identity: identity, CPUSeconds: cpuSeconds}
	if !ok || !now.After(previous.At) || previous.Identity != identity || cpuSeconds < previous.CPUSeconds {
		return nil
	}
	percent := (cpuSeconds - previous.CPUSeconds) / now.Sub(previous.At).Seconds() * 100
	if math.IsNaN(percent) || math.IsInf(percent, 0) || percent < 0 {
		return nil
	}
	return &percent
}

func (s *server) threadCPUPercent(now time.Time, identity string, cpuSeconds float64) *float64 {
	s.processMetricsMu.Lock()
	defer s.processMetricsMu.Unlock()
	if s.threadCPU == nil {
		s.threadCPU = make(map[string]childCPUState)
	}
	previous, ok := s.threadCPU[identity]
	s.threadCPU[identity] = childCPUState{At: now, Identity: identity, CPUSeconds: cpuSeconds}
	if !ok || !now.After(previous.At) || previous.Identity != identity || cpuSeconds < previous.CPUSeconds {
		return nil
	}
	percent := (cpuSeconds - previous.CPUSeconds) / now.Sub(previous.At).Seconds() * 100
	if math.IsNaN(percent) || math.IsInf(percent, 0) || percent < 0 {
		return nil
	}
	return &percent
}

func childProcessLabel(entry procEntry) string {
	if executable := strings.TrimSpace(entry.Executable); executable != "" {
		if base := filepath.Base(executable); base != "." && base != string(filepath.Separator) {
			return base
		}
	}
	command := strings.TrimSpace(entry.Command)
	if command == "" {
		return ""
	}
	return strings.Fields(command)[0]
}

func (s *server) goLocalServicesPayload(now time.Time) map[string]any {
	metricsFn := s.processMetricsFn
	if metricsFn == nil {
		metricsFn = s.readGoProcessMetrics
	}
	return s.goLocalServicesPayloadFromMetrics(now, metricsFn(now))
}

func (s *server) goLocalServicesPayloadFromMetrics(now time.Time, process goProcessMetrics) map[string]any {
	startedAt := process.StartedAt
	if startedAt.IsZero() {
		startedAt = now
	}
	uptime := now.Sub(startedAt).Seconds()
	if uptime < 0 {
		uptime = 0
	}
	threadReasonCode, threadReason := process.ThreadReasonCode, process.ThreadReason
	if process.ThreadCount == nil && threadReasonCode == "" {
		threadReasonCode = "process_threads_unavailable"
		threadReason = "The operating system did not return Go process thread details"
	}
	goroutineReasonCode, goroutineReason := process.GoroutineReasonCode, process.GoroutineReason
	if process.GoroutineCount == nil && goroutineReasonCode == "" {
		goroutineReasonCode = "process_goroutines_unavailable"
		goroutineReason = "The Go runtime did not return a goroutine count"
	}
	threadDetails := make([]any, 0, len(process.Threads))
	for _, thread := range process.Threads {
		threadDetails = append(threadDetails, map[string]any{
			"tid": thread.TID, "name": thread.Name, "state": thread.State,
			"cpu_now_percent": goMetric(valueOrNil(thread.CPUPercent), thread.CPUReasonCode, thread.CPUReason),
		})
	}
	childProcesses := make([]any, 0, len(process.ChildProcesses))
	for _, child := range process.ChildProcesses {
		childProcesses = append(childProcesses, map[string]any{
			"pid": child.PID, "parent_pid": child.PPID, "label": child.Label, "state": child.State,
			"cpu_now_percent": goMetric(valueOrNil(child.CPUPercent), child.CPUReasonCode, child.CPUReason),
			"rss_bytes":       goMetric(valueOrNil(child.RSSBytes), child.RSSReasonCode, child.RSSReason),
		})
	}
	// Keep the measured owner timings beside the Go service that owns them. The
	// top-level system-status copy remains for machine readers; this nested copy
	// lets the expanded Daemons row answer what is consuming CPU without making
	// the browser infer work from the aggregate process percentage.
	runtimeDiagnostics := s.statsRuntimeDiagnostics()
	processCount := uint64(1 + len(process.ChildProcesses))
	ledger := s.ensureGoHealthLedger(now)
	ledger.observe()
	health := s.goHealthSnapshot(now)
	lifecycle := s.goHealthLifecycleSnapshot()
	unavailableRestart := func(code, reason string) map[string]any {
		return goMetric(nil, code, reason)
	}
	measuredCount := func(value uint64) map[string]any {
		return goMetric(float64(value), "", "")
	}
	lifecycleMetric := func(value uint64, available bool, reasonCode, reason string) map[string]any {
		if !available {
			if reasonCode == "" {
				reasonCode = "go_health_history_unavailable"
			}
			if reason == "" {
				reason = "Go backend-health history is not available for this counter"
			}
			return unavailableRestart(reasonCode, reason)
		}
		return measuredCount(value)
	}
	latencyMetric := func() map[string]any {
		if health.completedCount == 0 {
			return goMetric(nil, "no_completed_request", "No completed request has been timed in this web process")
		}
		return goMetric(float64(health.latencyMax)/float64(time.Millisecond), "", "")
	}
	latencyAverageMetric := func() map[string]any {
		if health.completedCount == 0 {
			return goMetric(nil, "no_completed_request", "No completed request has been timed in this web process")
		}
		average := float64(health.latencyTotal) / float64(time.Millisecond) / float64(health.completedCount)
		return goMetric(average, "", "")
	}
	healthMetrics := map[string]any{
		"restart_count":            lifecycleMetric(lifecycle.RestartCount, lifecycle.RestartAvailable, "go_health_history_unavailable", "Go restart history is not available"),
		"process_start_count":      measuredCount(health.processStartCount),
		"demand_start_count":       lifecycleMetric(lifecycle.DemandStartCount, lifecycle.DemandAvailable, "demand_classification_unavailable", "Go launch reason was not persisted for this history"),
		"unexpected_restart_count": lifecycleMetric(lifecycle.UnexpectedRestartCount, lifecycle.UnexpectedAvailable, "restart_classification_unavailable", "Go restart history is not complete enough to classify this start"),
		"observations":             measuredCount(health.observations),
		"request_count":            measuredCount(health.requestCount),
		"error_count":              measuredCount(health.errorCount),
		"completed_count":          measuredCount(health.completedCount),
		"latency_average_ms":       latencyAverageMetric(),
		"latency_max_ms":           latencyMetric(),
	}
	service := map[string]any{
		"id": goServiceID, "service": goServiceID, "label": "YO!stats Go backend",
		"parent_id": "web", "state": "running", "reason_code": "", "reason": "",
		"pid": process.PID, "started_at": unixSeconds(startedAt), "essential": true, "alerting": false,
		"metrics": map[string]any{
			"cpu_now_percent": goMetric(valueOrNil(process.CPUPercent), process.CPUReasonCode, process.CPUReason),
			"rss_bytes":       goMetric(valueOrNil(process.RSSBytes), process.RSSReasonCode, process.RSSReason),
			"uptime_seconds":  goMetric(uptime, "", ""),
		},
		"health": map[string]any{
			"observed": true, "unavailable_reason_code": "",
			"state": "ready", "reason_code": "none", "recovery_outcome": "none", "process_epoch": health.epoch,
			"pid": process.PID, "observed_at": unixSeconds(now), "since_revision": 1, "since_wall_time": unixSeconds(health.epochStartedAt),
			"state_age_seconds": maxGoHealthFloat(0, now.Sub(health.epochStartedAt).Seconds()), "transitions_truncated": false,
			"coverage": map[string]any{
				"retained_counters": lifecycleCoverage(lifecycle), "retained_counter_reasons": lifecycleCoverageReasons(lifecycle),
				"lifecycle": lifecycleCoverage(lifecycle), "lifecycle_reasons": lifecycleCoverageReasons(lifecycle),
				"counters": "full", "counter_reasons": []string{}, "counter_scope": "web_process",
			},
			"metrics": healthMetrics, "transitions": []any{}, "transitions_total": 0, "transitions_total_exact": true,
			"errors_by_reason": goHealthErrorPayload(health.errorsByReason),
		},
		"details": map[string]any{
			"backend":                "go",
			"observer":               "in-process-ledger",
			"stats_runtime":          runtimeDiagnostics,
			"cpu_scope":              "Go process plus verified descendant processes",
			"cpu_normalization":      "100% means one busy wall-clock core; values above 100% mean concurrent cores",
			"process_count":          goMetric(processCount, "", ""),
			"goroutine_count":        goMetric(valueOrNil(process.GoroutineCount), goroutineReasonCode, goroutineReason),
			"goroutine_scope":        "aggregate_only",
			"goroutine_scope_reason": "Go exposes an aggregate runtime goroutine count; per-goroutine CPU requires a profiler and is not a stable live metric",
			"thread_count":           goMetric(valueOrNil(process.ThreadCount), threadReasonCode, threadReason),
			"threads":                threadDetails,
			"child_processes":        childProcesses,
		},
	}
	cpuTotal := float64(0)
	if process.CPUPercent != nil {
		cpuTotal = *process.CPUPercent
	}
	for _, child := range process.ChildProcesses {
		if child.CPUPercent != nil {
			cpuTotal += *child.CPUPercent
		}
	}
	rssTotal := uint64(0)
	if process.RSSBytes != nil {
		rssTotal = *process.RSSBytes
	}
	for _, child := range process.ChildProcesses {
		if child.RSSBytes != nil {
			rssTotal += *child.RSSBytes
		}
	}
	return map[string]any{
		"schema_version": 5, "inventory": []string{goServiceID}, "services": []any{service},
		"totals": map[string]any{"processes": processCount, "cpu_percent": cpuTotal, "rss_bytes": rssTotal},
		"ledger": map[string]any{}, "recovery_events": []any{},
		"health": map[string]any{
			"available": true, "reason_code": "", "schema_version": 1,
			"port": s.port, "observer_epoch": health.epoch, "observer_epoch_started_at": unixSeconds(health.epochStartedAt), "revision": health.observations,
			"written_at": unixSeconds(now), "age_seconds": 0.0, "history_coverage": lifecycle.HistoryCoverage, "history_reset_reason": lifecycle.HistoryResetReason,
			"persistence_state": lifecycle.PersistenceState, "persistence_reason_code": lifecycle.PersistenceReasonCode, "observer_alive": true,
			"observer_cycle_age_seconds": 0.0, "observer_cycles": health.observations,
			"observer_liveness_reason_code": "in_process_ledger", "resources": 1,
		},
	}
}

func goServerMetricsPayload(now time.Time, process goProcessMetrics) map[string]any {
	startedAt := process.StartedAt
	if startedAt.IsZero() {
		startedAt = now
	}
	uptime := now.Sub(startedAt).Seconds()
	if uptime < 0 {
		uptime = 0
	}
	return map[string]any{
		"uptime_seconds":     goMetric(uptime, "", ""),
		"cpu_percent":        goMetric(valueOrNil(process.CPUPercent), process.CPUReasonCode, process.CPUReason),
		"system_cpu_percent": goMetric(nil, "host_cpu_not_sampled", "The Go backend does not publish a separate host CPU sample in this status response"),
		"rss_bytes":          goMetric(valueOrNil(process.RSSBytes), process.RSSReasonCode, process.RSSReason),
	}
}

func valueOrNil[T any](value *T) any {
	if value == nil {
		return nil
	}
	return *value
}

func unixSeconds(value time.Time) float64 {
	return float64(value.UnixNano()) / 1e9
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
	now := time.Now()
	metricsFn := s.processMetricsFn
	if metricsFn == nil {
		metricsFn = s.readGoProcessMetrics
	}
	process := metricsFn(now)
	serverMetrics := goServerMetricsPayload(now, process)
	serverBlock := map[string]any{"backend": "go", "version": version, "root": s.root, "pid": process.PID, "started_at": unixSeconds(process.StartedAt)}
	if process.StartedAt.IsZero() {
		serverBlock["started_at"] = unixSeconds(now)
	}
	for key, value := range serverMetrics {
		serverBlock[key] = value
	}
	payload := map[string]any{
		"schema": "system-status-snapshot", "state": "ready", "epoch": epoch,
		"server":    serverBlock,
		"refresh":   map[string]any{"events": eventSnapshot, "operations": map[string]any{"total": operationCount, "terminal": terminalCount}},
		"services":  map[string]any{"events": "ready", "operations": "ready", "background": s.backgroundState().statusPayload()},
		"resources": map[string]any{"event_broker": eventSnapshot, "operation_records": operationCount},
		// Go polls the machine-wide tmux roster in-process. Report that subsystem
		// explicitly so the shared health panel does not mistake the absence of a
		// standalone control-client PID for an unsupported or exited watcher.
		"tmux_signal_watcher": s.tmuxSignalWatcherStatus(),
		"local_services":      s.goLocalServicesPayloadFromMetrics(now, process),
		"stats_runtime":       s.statsRuntimeDiagnostics(),
	}
	if strings.HasSuffix(r.URL.Path, "/advanced") {
		payload["advanced"] = true
		payload["diagnostics"] = map[string]any{"events": s.events.snapshot(), "logs": s.logState().payload(), "operations": map[string]any{"records": operationCount, "terminal": terminalCount}}
	}
	jsonResponse(w, http.StatusOK, payload)
}

func (s *server) tmuxSignalWatcherStatus() map[string]any {
	sessions, err := s.tmuxSessionRoster()
	return tmuxSignalWatcherStatusFor(sessions, err)
}

func tmuxSignalWatcherStatusFor(sessions []string, err error) map[string]any {
	if err != nil {
		return map[string]any{
			"state": "exited", "healthy": false, "reason_code": "tmux_unavailable",
			"reason": "The in-process tmux roster poll failed", "sessions": []string{},
			"thread_alive": false, "process_pid": 0, "demanded": true,
		}
	}
	if len(sessions) == 0 {
		return map[string]any{
			"state": "no-sessions", "healthy": true, "reason_code": "no_sessions",
			"reason": "No tmux sessions are available to the in-process poller", "sessions": sessions,
			"thread_alive": true, "process_pid": 0, "demanded": true,
		}
	}
	return map[string]any{
		"state": "attached", "healthy": true, "reason_code": "attached",
		"reason": "Global tmux roster is available to the in-process poller", "sessions": sessions,
		"thread_alive": true, "process_pid": 0, "demanded": true,
	}
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

func (s *server) tmux(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	sessions, rosterErr := s.tmuxSessionRoster()
	if rosterErr != nil {
		cached, known := s.cachedTmuxSessionRoster()
		if !known {
			jsonResponse(w, http.StatusServiceUnavailable, map[string]any{
				"sessions": []string{}, "session_order": []string{}, "session_scope": "all",
				"errors": []string{rosterErr.Error()}, "backend": "go",
			})
			return
		}
		sessions = cached
	}
	// With no session query this is the small, machine-wide roster used by old
	// clients.  Include the explicit order/scope keys so it is also a valid
	// topology response for Tabber: one live tmux session owns one top-level tab.
	session := strings.TrimSpace(r.URL.Query().Get("session"))
	if session == "" {
		topology, topologyErr := s.tmuxTopologyFromRoster(s.cachedTmuxSessionRosterSnapshot(), rosterErr)
		sessions = topology.sessions
		payload := map[string]any{
			"sessions": sessions, "session_order": sessions, "session_scope": "all", "backend": "go",
			"topology_generation": topology.generation, "renames": topology.renames, "errors": []string{},
		}
		if topologyErr != nil {
			payload["errors"] = []string{topologyErr.Error()}
		}
		jsonResponse(w, http.StatusOK, payload)
		return
	}
	knownSession := false
	for _, name := range sessions {
		if name == session {
			knownSession = true
			break
		}
	}
	if !knownSession {
		jsonResponse(w, http.StatusNotFound, map[string]any{"error": "unknown session", "session": session})
		return
	}

	lines := 90
	if raw := strings.TrimSpace(r.URL.Query().Get("lines")); raw != "" {
		parsed, err := strconv.Atoi(raw)
		if err != nil || parsed < 1 {
			jsonResponse(w, http.StatusBadRequest, map[string]any{"error": "invalid lines", "field": "lines"})
			return
		}
		lines = parsed
	}
	if lines > maxContextTranscriptLines {
		lines = maxContextTranscriptLines
	}

	target := session
	metadata := s.liveSessionMetadata(session)
	if selected, ok := metadata["selected_pane"].(map[string]any); ok {
		if candidate, ok := selected["target"].(string); ok && strings.TrimSpace(candidate) != "" {
			target = candidate
		}
	}
	commandContext, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	output, err := s.tmuxCommandContext(commandContext, "capture-pane", "-t", target, "-p", "-J", "-S", fmt.Sprintf("-%d", lines)).CombinedOutput()
	if err != nil {
		diagnostic := strings.TrimSpace(string(output))
		if diagnostic == "" {
			diagnostic = err.Error()
		}
		jsonResponse(w, http.StatusInternalServerError, map[string]any{
			"session": session, "target": target, "errors": []string{"tmux capture-pane failed: " + diagnostic}, "backend": "go",
		})
		return
	}
	jsonResponse(w, http.StatusOK, map[string]any{
		"session": session, "target": target, "lines": lines, "text": strings.TrimRight(string(output), "\n"),
		"unchanged": false, "errors": []string{}, "backend": "go",
	})
}

// tmuxSignals is the Go equivalent of Python's read-only tmux signal snapshot.
// It intentionally uses the global tmux server and the same pane inventory as
// session-metadata, so the browser can reconcile every machine-wide session and
// window without depending on a standalone control-client process.
func (s *server) tmuxSignals(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	payload, status := s.tmuxSignalSnapshot(r.URL.Query().Get("session"))
	jsonResponse(w, status, payload)
}

func (s *server) tmuxWindow(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	session := strings.TrimSpace(r.URL.Query().Get("session"))
	if session == "" {
		jsonResponse(w, http.StatusBadRequest, map[string]any{"error": "missing session"})
		return
	}
	window := strings.TrimSpace(r.URL.Query().Get("window"))
	if window == "" {
		jsonResponse(w, http.StatusBadRequest, map[string]any{
			"session": session,
			"error":   "window must be a non-negative integer",
		})
		return
	}
	for _, character := range window {
		if character < '0' || character > '9' {
			jsonResponse(w, http.StatusBadRequest, map[string]any{
				"session": session,
				"error":   "window must be a non-negative integer",
			})
			return
		}
	}

	commandContext, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	if err := s.tmuxCommandContext(commandContext, "has-session", "-t", s.exactTarget(session)).Run(); err != nil {
		jsonResponse(w, http.StatusNotFound, map[string]any{"error": "unknown session", "session": session})
		return
	}
	target := s.exactTarget(session) + window
	output, err := s.tmuxCommandContext(commandContext, "select-window", "-t", target).CombinedOutput()
	if err != nil {
		jsonResponse(w, http.StatusInternalServerError, map[string]any{
			"session": session,
			"window":  window,
			"error":   goTmuxSignalCommandError("tmux select-window failed", err, output),
		})
		return
	}
	jsonResponse(w, http.StatusOK, map[string]any{"session": session, "window": window, "ok": true})
}

func goIntValue(value any) (int, bool) {
	switch number := value.(type) {
	case int:
		return number, true
	case int64:
		return int(number), true
	case float64:
		return int(number), number == float64(int(number))
	case string:
		parsed, err := strconv.Atoi(strings.TrimSpace(number))
		return parsed, err == nil
	default:
		return 0, false
	}
}

func (s *server) activity(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	hours, err := activityHours(r.URL.Query().Get("hours"))
	if err != nil {
		jsonResponse(w, http.StatusBadRequest, map[string]any{"error": "invalid hours", "field": "hours"})
		return
	}
	// Tabber is a view of the machine-wide tmux roster. Keep this route on the
	// same global inventory as session-metadata so one tmux session always has
	// one stable top-level Tabber row, regardless of which YOmux process owns
	// the browser request.
	// Python accepts scope=all (and its tmux/visible aliases) for this product;
	// Go has no separate configured-session inventory, so every accepted scope
	// resolves to the machine-wide roster.
	// Unlike Python, the Go backend has no separate configured-session list;
	// even a missing or legacy scope parameter therefore means the global
	// machine-wide roster. Report the scope actually served, not the raw query.
	scope := "all"
	topology, rosterErr := s.tmuxTopologySnapshot()
	if rosterErr != nil && len(topology.sessions) == 0 {
		if topology.generation == 0 {
			jsonResponse(w, http.StatusServiceUnavailable, map[string]any{
				"activity": map[string]any{}, "agents": []any{}, "agent_windows": map[string]any{},
				"errors": []string{rosterErr.Error()}, "session_order": []string{}, "session_scope": scope,
				"session_file_hours": hours, "cache": map[string]any{"hit": false, "stale": false, "refreshing": false},
				"backend": "go",
			})
			return
		}
	}
	sessions := topology.sessions
	published, publishedGeneration, stale, metadataObservedAt := s.metadataSnapshotWithBuiltAt()
	metadataSessions := metadataSessionsForTopology(published, publishedGeneration, topology)
	metadataStale := published == nil || stale || metadataPayloadTopologyGeneration(published) != topology.generation
	if metadataStale {
		s.queueMetadataRefresh(false, topology.sessions, topology.identities, rosterErr, topology.generation)
	}
	activity := make(map[string]any)
	activityErrors := make([]string, 0)
	activityAgentStates := map[string]string(nil)
	activityAgentObservedAt := map[string]time.Time(nil)
	if s.statsService != nil {
		statusSnapshot, statusErr := s.statsService.AgentStatus(r.Context())
		if statusErr != nil {
			activityErrors = append(activityErrors, statusErr.Error())
		} else {
			activityAgentStates, activityAgentObservedAt = activityAgentStatusOverrides(statusSnapshot)
		}
	}
	if rosterErr != nil {
		activityErrors = append(activityErrors, rosterErr.Error())
	}
	if published != nil {
		activityErrors = appendMetadataErrorValues(activityErrors, published["errors"])
	}
	activityRoster := s.tmuxSessionActivityFn
	if activityRoster == nil {
		activityRoster = s.tmuxSessionActivityRoster
	}
	if sessionActivity, err := activityRoster(); err == nil {
		for session, timestamp := range sessionActivity {
			activity[session] = map[string]any{"active_recency_ts": timestamp}
		}
	} else {
		// The roster is the topology source of truth. Keep the sessions visible when
		// the optional activity timestamps fail, but preserve the diagnostic instead
		// of publishing a falsely healthy empty activity snapshot.
		activityErrors = append(activityErrors, err.Error())
	}
	windowActivityRoster := s.tmuxWindowActivityFn
	if windowActivityRoster == nil {
		windowActivityRoster = s.tmuxWindowActivityRoster
	}
	if windowActivity, err := windowActivityRoster(); err == nil {
		for key, timestamp := range windowActivity {
			activity[key] = map[string]any{
				"active_recency_ts": timestamp,
				// A tmux window activity timestamp is the only native Go
				// signal available for non-agent panes. Keep the field names
				// used by Python's activity ledger so Tabber's shared recency
				// helper can sort every window consistently.
				"last_output_ts": timestamp,
			}
		}
	} else {
		activityErrors = append(activityErrors, err.Error())
	}
	agents := make([]any, 0)
	agentWindows := make(map[string]any)
	for _, session := range sessions {
		info := metadataSessions[session].(map[string]any)
		rows, _ := info["agents"].([]map[string]any)
		windowRows := make([]any, 0, len(rows))
		for _, agent := range rows {
			window, _ := agent["window_index"].(int)
			state := stringValue(agent["status"])
			statusObservedAt := metadataObservedAt
			if observedAt, ok := agent["status_observed_at"].(float64); ok && observedAt > 0 {
				statusObservedAt = time.Unix(0, int64(observedAt*1e9))
			}
			key := activityAgentStatusKey(session, stringValue(agent["pane_target"]), stringValue(agent["kind"]))
			currentObservedAt := activityAgentObservedAt[key]
			if currentState, ok := activityAgentStates[key]; ok && (currentObservedAt.IsZero() || currentObservedAt.After(statusObservedAt)) {
				state = currentState
			}
			windowRow := map[string]any{
				"session": session, "window": strconv.Itoa(window), "window_index": window,
				"pane_target": agent["pane_target"], "kind": agent["kind"], "agent_kind": agent["kind"], "state": state,
				"current": agent["current"], "window_active": agent["window_active"],
			}
			windowRows = append(windowRows, windowRow)
			agents = append(agents, windowRow)
		}
		if len(windowRows) > 0 {
			agentWindows[session] = windowRows
		}
	}
	jsonResponse(w, http.StatusOK, map[string]any{
		"activity": activity, "agents": agents, "agent_windows": agentWindows,
		// The activity response is also a topology repair path. Python's cached
		// activity product normally follows session-metadata, but a Go-only boot
		// can receive activity first; carrying the same pane shape lets the client
		// render one Tabber session row even when metadata is still unavailable.
		"sessions": metadataSessions,
		"errors":   activityErrors, "session_order": sessions, "session_scope": scope,
		"topology_generation": topology.generation, "renames": topology.renames,
		"session_file_hours": hours,
		"cache": map[string]any{
			"hit": published != nil, "stale": metadataStale,
			"generation": publishedGeneration, "refreshing": s.metadataRefreshInFlight(),
		},
		"backend": "go",
	})
}

const maxActivityHours = 24.0 * 365.0

// activityHours mirrors Python's parse_query_float contract: a missing value uses
// 24 hours, non-finite/negative values are rejected, and values above the product
// cap are bounded rather than turning an otherwise valid Tabber request into an
// error.
func activityHours(raw string) (float64, error) {
	if strings.TrimSpace(raw) == "" {
		return 24.0, nil
	}
	value, err := strconv.ParseFloat(strings.TrimSpace(raw), 64)
	if err != nil || math.IsNaN(value) || math.IsInf(value, 0) || value < 0 {
		return 0, fmt.Errorf("hours must be a finite non-negative number")
	}
	if value > maxActivityHours {
		return maxActivityHours, nil
	}
	return value, nil
}

func (s *server) sessionMetadata(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	force := r.URL.Query().Get("force") == "1" || r.URL.Query().Get("force") == "true"
	published, publishedGeneration, stale := s.metadataSnapshot()
	topology, _ := s.cachedTmuxTopologySnapshot()
	var rosterErr error
	if force || published == nil || !s.tmuxSessionRosterFresh() {
		topology, rosterErr = s.tmuxTopologySnapshot(force)
	}
	sessions := topology.sessions
	if rosterErr != nil && len(sessions) == 0 && topology.generation == 0 {
		jsonResponse(w, http.StatusServiceUnavailable, map[string]any{
			"status": "unavailable", "sessions": map[string]any{}, "session_order": []string{},
			"session_scope": "all", "errors": []string{rosterErr.Error()}, "backend": "go",
		})
		return
	}
	servedGeneration := publishedGeneration
	s.metadataMu.Lock()
	if s.metadataNextGeneration < servedGeneration {
		s.metadataNextGeneration = servedGeneration
	}
	s.metadataMu.Unlock()
	topologyChanged := published != nil && metadataPayloadTopologyGeneration(published) != topology.generation
	metadataSessions := metadataSessionsForTopology(published, servedGeneration, topology)
	cache := map[string]any{"hit": true, "stale": false, "generation": servedGeneration, "refreshing": s.metadataRefreshInFlight()}
	if force || published == nil || stale || topologyChanged {
		pending := s.queueMetadataRefresh(force, sessions, topology.identities, rosterErr, topology.generation)
		cache = map[string]any{
			"hit": published != nil, "stale": true, "refreshing": s.metadataRefreshInFlight(),
			"generation": servedGeneration,
		}
		if force {
			cache["pending_generation"] = pending
			cache["pending_identity"] = map[string]any{"epoch": s.metadataEpoch(), "generation": pending}
		}
	}
	responseErrors := metadataErrors(rosterErr, nil)
	if published != nil {
		responseErrors = appendMetadataErrorValues(responseErrors, published["errors"])
	}
	jsonResponse(w, http.StatusOK, map[string]any{
		"sessions": metadataSessions, "session_order": sessions, "session_scope": "all",
		"metadata_generation": servedGeneration, "metadata_identity": map[string]any{"epoch": s.metadataEpoch(), "generation": servedGeneration},
		"topology_generation": topology.generation, "errors": responseErrors, "cache": cache,
		"renames": topology.renames,
	})
}

func metadataSessionsForTopology(published map[string]any, generation uint64, topology goTmuxTopology) map[string]any {
	publishedSessions := map[string]any{}
	if published != nil {
		publishedSessions, _ = published["sessions"].(map[string]any)
	}
	metadataSessions := make(map[string]any, len(topology.sessions))
	for _, session := range topology.sessions {
		sessionIdentity := goTmuxSessionIdentity(session, topology.identities[session])
		info, cached := publishedSessions[session].(map[string]any)
		if cached && info["session_identity"] == sessionIdentity {
			info = cloneMap(info)
		} else {
			info = map[string]any{
				"panes": []map[string]any{}, "selected_pane": nil, "agents": []map[string]any{}, "window_metadata": []map[string]any{},
			}
			graph := emptyGoWorkGraph(generation)
			graph["loading"] = true
			info["work_graph"] = graph
		}
		info["session"] = session
		info["label"] = session
		info["session_identity"] = sessionIdentity
		metadataSessions[session] = info
	}
	return metadataSessions
}

func metadataPayloadTopologyGeneration(payload map[string]any) uint64 {
	if payload == nil {
		return 0
	}
	generation, _ := payload["topology_generation"].(uint64)
	return generation
}

func (s *server) metadataRefreshInFlight() bool {
	s.metadataMu.Lock()
	defer s.metadataMu.Unlock()
	return s.metadataBuilding
}

func (s *server) metadataEpoch() string {
	if s.events == nil {
		return ""
	}
	s.events.mu.Lock()
	defer s.events.mu.Unlock()
	return s.events.epoch
}

func (s *server) buildMetadataPayload(generation uint64, sessions []string, identities map[string]string, rosterErr error, topologyGeneration uint64) map[string]any {
	metadataSessions := make(map[string]any, len(sessions))
	liveMetadata, metadataErr := s.enrichSessionMetadata(sessions)
	gitCache := map[string]goGitSnapshotResult{}
	for _, session := range sessions {
		info := liveMetadata[session]
		if info == nil {
			info = map[string]any{
				"panes": []map[string]any{}, "selected_pane": nil, "agents": []map[string]any{}, "window_metadata": []map[string]any{},
			}
		}
		info["session"] = session
		info["label"] = session
		info["session_identity"] = goTmuxSessionIdentity(session, identities[session])
		info["work_graph"] = s.sessionWorkGraphWithGitCache(session, info, generation, gitCache)
		metadataSessions[session] = info
	}
	identity := map[string]any{"epoch": s.metadataEpoch(), "generation": generation}
	s.metadataTopologyMu.Lock()
	var renames []map[string]any
	if topologyGeneration == s.metadataTopologyGeneration {
		renames = cloneTmuxRosterRenames(s.metadataTopologyRenames)
	}
	s.metadataTopologyMu.Unlock()
	return map[string]any{
		"sessions":            metadataSessions,
		"session_order":       sessions,
		"session_scope":       "all",
		"metadata_generation": generation,
		"metadata_identity":   identity,
		"topology_generation": topologyGeneration,
		"renames":             renames,
		"errors":              metadataErrors(rosterErr, metadataErr),
	}
}

func (s *server) enrichSessionMetadata(sessions []string) (map[string]map[string]any, error) {
	if s.metadataEnrichmentFn != nil {
		return s.metadataEnrichmentFn(sessions)
	}
	return s.liveSessionMetadataForSessionsWithError(sessions)
}

func metadataErrors(rosterErr, metadataErr error) []string {
	result := make([]string, 0, 2)
	if rosterErr != nil {
		result = append(result, rosterErr.Error())
	}
	if metadataErr != nil {
		result = append(result, metadataErr.Error())
	}
	return result
}

func appendMetadataErrorValues(result []string, value any) []string {
	seen := make(map[string]struct{}, len(result))
	for _, message := range result {
		seen[message] = struct{}{}
	}
	appendMessage := func(message string) {
		if message == "" {
			return
		}
		if _, exists := seen[message]; exists {
			return
		}
		seen[message] = struct{}{}
		result = append(result, message)
	}
	switch messages := value.(type) {
	case []string:
		for _, message := range messages {
			appendMessage(message)
		}
	case []any:
		for _, raw := range messages {
			if message, ok := raw.(string); ok {
				appendMessage(message)
			}
		}
	}
	return result
}

func (s *server) metadataSnapshot() (map[string]any, uint64, bool) {
	payload, generation, stale, _ := s.metadataSnapshotWithBuiltAt()
	return payload, generation, stale
}

func (s *server) metadataSnapshotWithBuiltAt() (map[string]any, uint64, bool, time.Time) {
	s.metadataMu.Lock()
	defer s.metadataMu.Unlock()
	if s.metadataPayload == nil {
		return nil, 0, true, time.Time{}
	}
	return cloneMap(s.metadataPayload), s.metadataGeneration, s.metadataBuiltAt.IsZero() || time.Since(s.metadataBuiltAt) >= goMetadataCacheTTL, s.metadataBuiltAt
}

func (s *server) queueMetadataRefresh(force bool, sessions []string, identities map[string]string, rosterErr error, topologyGeneration uint64) uint64 {
	s.metadataMu.Lock()
	defer s.metadataMu.Unlock()
	latestTopology := uint64(0)
	latestGeneration := uint64(0)
	if s.metadataPayload != nil {
		latestTopology, _ = s.metadataPayload["topology_generation"].(uint64)
		latestGeneration = s.metadataGeneration
	}
	if s.metadataBuilding && s.metadataBuildingTopologyGeneration >= latestTopology {
		latestTopology = s.metadataBuildingTopologyGeneration
		latestGeneration = s.metadataBuildingGeneration
	}
	if s.metadataQueuedGeneration != 0 && s.metadataQueuedTopologyGeneration >= latestTopology {
		latestTopology = s.metadataQueuedTopologyGeneration
		latestGeneration = s.metadataQueuedGeneration
	}
	if topologyGeneration < latestTopology {
		if force {
			return latestGeneration
		}
		return 0
	}
	if s.metadataQueuedGeneration != 0 {
		if force || topologyGeneration > s.metadataQueuedTopologyGeneration {
			if topologyGeneration >= s.metadataQueuedTopologyGeneration {
				s.metadataQueuedSessions = append([]string(nil), sessions...)
				s.metadataQueuedIdentities = identities
				s.metadataQueuedRosterErr = rosterErr
				s.metadataQueuedTopologyGeneration = topologyGeneration
			}
			return s.metadataQueuedGeneration
		}
		return 0
	}
	if s.metadataBuilding {
		if !force && topologyGeneration <= s.metadataBuildingTopologyGeneration {
			return 0
		}
		s.metadataNextGeneration++
		generation := s.metadataNextGeneration
		s.metadataQueuedGeneration = generation
		s.metadataQueuedSessions = append([]string(nil), sessions...)
		s.metadataQueuedIdentities = identities
		s.metadataQueuedRosterErr = rosterErr
		s.metadataQueuedTopologyGeneration = topologyGeneration
		return generation
	}
	s.metadataNextGeneration++
	generation := s.metadataNextGeneration
	s.metadataBuilding = true
	s.metadataBuildingGeneration = generation
	s.metadataBuildingTopologyGeneration = topologyGeneration
	go s.buildMetadataRefresh(generation, sessions, identities, rosterErr, topologyGeneration)
	return generation
}

func (s *server) buildMetadataRefresh(generation uint64, sessions []string, identities map[string]string, rosterErr error, topologyGeneration uint64) {
	payload := s.buildMetadataPayload(generation, sessions, identities, rosterErr, topologyGeneration)
	s.metadataMu.Lock()
	s.metadataTopologyMu.Lock()
	topologyAdvanced := s.metadataTopologyGeneration > topologyGeneration
	s.metadataTopologyMu.Unlock()
	newerBuildQueued := s.metadataQueuedGeneration > generation
	publish := !topologyAdvanced && !newerBuildQueued
	if publish {
		s.metadataPayload = payload
		s.metadataGeneration = generation
		s.metadataBuiltAt = time.Now()
	}
	queued := s.metadataQueuedGeneration
	queuedSessions := s.metadataQueuedSessions
	queuedIdentities := s.metadataQueuedIdentities
	queuedRosterErr := s.metadataQueuedRosterErr
	queuedTopologyGeneration := s.metadataQueuedTopologyGeneration
	s.metadataQueuedGeneration = 0
	s.metadataQueuedSessions = nil
	s.metadataQueuedIdentities = nil
	s.metadataQueuedRosterErr = nil
	s.metadataQueuedTopologyGeneration = 0
	if queued == 0 {
		s.metadataBuilding = false
		s.metadataBuildingGeneration = 0
	} else {
		s.metadataBuildingGeneration = queued
		s.metadataBuildingTopologyGeneration = queuedTopologyGeneration
	}
	s.metadataMu.Unlock()
	if publish && s.events != nil {
		_, _ = s.events.publish("transcripts_changed", map[string]any{"data": cloneMap(payload)})
	}
	if queued != 0 {
		s.buildMetadataRefresh(queued, queuedSessions, queuedIdentities, queuedRosterErr, queuedTopologyGeneration)
	}
}

// tmuxTopologySnapshot serializes the machine-wide roster read and advances its
// generation only when session names or tmux session IDs change. Unchanged
// reads cannot invalidate an enrichment build, while changed snapshots retain
// their read order so an older response cannot roll the browser back.
//
// A cached roster returned after a failed read keeps its successful names and
// session IDs together. If another route refreshed that cache first, reconcile
// its identity here so a warm metadata payload cannot hide the newer roster.
func (s *server) tmuxTopologySnapshot(forceFresh ...bool) (goTmuxTopology, error) {
	force := len(forceFresh) != 0 && forceFresh[0]
	roster, err := s.tmuxSessionRosterSnapshot(force)
	return s.tmuxTopologyFromRoster(roster, err)
}

func (s *server) tmuxTopologyFromRoster(roster tmuxSessionRosterSnapshot, err error) (goTmuxTopology, error) {
	// Never hold the topology lock while waiting for tmux. Reconcile the latest
	// cached roster inside topology serialization so an older caller cannot publish
	// a snapshot copied before a newer topology transition.
	eventEpoch := s.metadataEpoch()
	s.metadataTopologyMu.Lock()
	if latest := s.cachedTmuxSessionRosterSnapshot(); latest.known {
		roster = latest
	}
	var topology goTmuxTopology
	var previous goTmuxTopology
	changed := false
	if err != nil {
		if roster.known {
			previous, changed = s.observeMetadataTopology(roster)
			topology = goTmuxTopology{sessions: roster.sessions, identities: roster.identities, generation: s.metadataTopologyGeneration, rosterRevision: roster.revision}
		} else {
			topology = goTmuxTopology{generation: s.metadataTopologyGeneration}
		}
	} else {
		previous, changed = s.observeMetadataTopology(roster)
		topology = goTmuxTopology{sessions: roster.sessions, identities: roster.identities, generation: s.metadataTopologyGeneration, rosterRevision: roster.revision}
	}
	if changed {
		topology.renames = tmuxRosterRenames(previous, topology)
		s.metadataTopologyRenames = cloneTmuxRosterRenames(topology.renames)
	} else {
		topology.renames = cloneTmuxRosterRenames(s.metadataTopologyRenames)
	}
	if changed && previous.generation != 0 && s.events != nil {
		_, publishErr := s.events.publish("tmux_roster_changed", map[string]any{
			"sessions": append([]string{}, topology.sessions...), "session_order": append([]string{}, topology.sessions...),
			"roster_generation": topology.generation, "topology_generation": topology.generation,
			"server_epoch": eventEpoch, "renames": topology.renames,
		})
		if publishErr != nil {
			slog.Warn("tmux roster event publish failed", "error", publishErr)
		}
	}
	s.metadataTopologyMu.Unlock()
	return topology, err
}

func (s *server) cachedTmuxTopologySnapshot() (goTmuxTopology, bool) {
	roster := s.cachedTmuxSessionRosterSnapshot()
	if !roster.known {
		return goTmuxTopology{}, false
	}
	topology, _ := s.tmuxTopologyFromRoster(roster, nil)
	return topology, true
}

func (s *server) observeMetadataTopology(roster tmuxSessionRosterSnapshot) (goTmuxTopology, bool) {
	signature := tmuxTopologySignature(roster.sessions, roster.identities)
	if signature == s.metadataTopologySignature {
		return goTmuxTopology{}, false
	}
	previous := goTmuxTopology{
		sessions:   append([]string(nil), s.metadataTopologySessions...),
		identities: cloneTmuxSessionIdentities(s.metadataTopologyIdentities), generation: s.metadataTopologyGeneration,
	}
	s.metadataTopologyGeneration++
	s.metadataTopologySignature = signature
	s.metadataTopologySessions = append([]string(nil), roster.sessions...)
	s.metadataTopologyIdentities = cloneTmuxSessionIdentities(roster.identities)
	return previous, true
}

func cloneTmuxRosterRenames(renames []map[string]any) []map[string]any {
	cloned := make([]map[string]any, len(renames))
	for index, rename := range renames {
		cloned[index] = cloneMap(rename)
	}
	return cloned
}

func cloneTmuxSessionIdentities(identities map[string]string) map[string]string {
	cloned := make(map[string]string, len(identities))
	for session, identity := range identities {
		cloned[session] = identity
	}
	return cloned
}

func (s *server) startTmuxRosterWatcher(parent context.Context) {
	s.startTmuxRosterWatcherWithInterval(parent, goTmuxRosterWatchInterval)
}

func (s *server) startTmuxRosterWatcherWithInterval(parent context.Context, interval time.Duration) {
	ctx, cancel := context.WithCancel(parent)
	s.tmuxRosterWatchCancel = cancel
	s.tmuxRosterWatchDone = make(chan struct{})
	go func() {
		defer close(s.tmuxRosterWatchDone)
		s.watchTmuxRoster(ctx, interval)
	}()
}

func (s *server) watchTmuxRoster(ctx context.Context, interval time.Duration) {
	if interval <= 0 {
		interval = goTmuxRosterWatchInterval
	}
	previous, err := s.tmuxTopologySnapshot(true)
	if err != nil {
		slog.Warn("initial tmux roster watch snapshot failed", "error", err)
	}
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
		next, snapshotErr := s.tmuxTopologySnapshot(true)
		if snapshotErr != nil {
			continue
		}
		if tmuxTopologySignature(previous.sessions, previous.identities) == tmuxTopologySignature(next.sessions, next.identities) {
			previous = next
			continue
		}
		s.queueMetadataRefresh(true, next.sessions, next.identities, nil, next.generation)
		previous = next
	}
}

func tmuxRosterRenames(previous, next goTmuxTopology) []map[string]any {
	nextByIdentity := make(map[string]string, len(next.sessions))
	for _, session := range next.sessions {
		if identity := next.identities[session]; identity != "" {
			nextByIdentity[identity] = session
		}
	}
	renamed := make([]map[string]any, 0)
	used := make(map[string]bool, len(nextByIdentity))
	for _, session := range previous.sessions {
		identity := previous.identities[session]
		if identity == "" || next.identities[session] == identity {
			continue
		}
		newSession := nextByIdentity[identity]
		if newSession == "" || used[newSession] {
			continue
		}
		used[newSession] = true
		renamed = append(renamed, map[string]any{
			"old_session": session, "new_session": newSession,
			"old_session_identity": goTmuxSessionIdentity(session, identity),
		})
	}
	return renamed
}

func tmuxTopologySignature(sessions []string, identities map[string]string) string {
	parts := make([]string, 0, len(sessions))
	for _, session := range sessions {
		identity := identities[session]
		if identity == "" {
			identity = session
		}
		parts = append(parts, session+"\x1f"+identity)
	}
	return strings.Join(parts, "\x00")
}

func goTmuxSessionIdentity(session, rosterIdentity string) string {
	if rosterIdentity == "" {
		rosterIdentity = session
	}
	return goGraphID("tmux-session-incarnation", session+"\x1f"+rosterIdentity)
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
	agent := strings.TrimSpace(r.URL.Query().Get("agent"))
	if agent == "" {
		agent = "claude"
	}
	terminal := strings.TrimSpace(r.URL.Query().Get("terminal"))
	launchDangerously := s.dangerouslyYolo
	if rawDangerously := r.URL.Query().Get("dangerously_yolo"); rawDangerously != "" {
		launchDangerously = queryTrue(rawDangerously)
	}
	if launchDangerously && !s.dangerouslyYolo {
		jsonResponse(w, http.StatusForbidden, map[string]any{
			"ok": false, "agent": agent, "error": "full-access agent launches require YOmux --dangerously-yolo",
		})
		return
	}
	command, err := goSessionLaunchCommand(agent, terminal, launchDangerously)
	if err != nil {
		jsonResponse(w, http.StatusNotFound, map[string]any{"ok": false, "agent": agent, "terminal": terminal, "error": err.Error()})
		return
	}
	name := r.URL.Query().Get("session")
	if name == "" {
		name = r.FormValue("session")
	}
	hasRequestedName := name != ""
	roster, err := s.tmuxSessionRosterSnapshot(true)
	if err != nil {
		jsonResponse(w, http.StatusServiceUnavailable, map[string]any{"ok": false, "error": "tmux session roster is unavailable"})
		return
	}
	if len(roster.sessions) >= maxYolomuxSessionTabs {
		jsonResponse(w, http.StatusConflict, map[string]any{
			"ok": false, "error": "maximum session tabs reached: 99",
		})
		return
	}
	nextName := nextNumberedSessionName(roster.sessions)
	if nextName == "" {
		jsonResponse(w, http.StatusConflict, map[string]any{
			"ok": false, "error": "no available numbered session names from 1 to 99",
		})
		return
	}
	if name == "" {
		name = nextName
	} else if name != nextName {
		jsonResponse(w, http.StatusConflict, map[string]any{
			"ok": false, "error": "create-session lifecycle reservation no longer matches the next available name",
		})
		return
	}
	generation := int64(0)
	if rawGeneration := strings.TrimSpace(r.URL.Query().Get("generation")); rawGeneration != "" {
		generation, err = strconv.ParseInt(rawGeneration, 10, 64)
		if err != nil || generation < 0 {
			jsonResponse(w, http.StatusBadRequest, map[string]any{"ok": false, "error": "generation must be a non-negative integer"})
			return
		}
	}
	if hasRequestedName {
		if generation <= 0 {
			jsonResponse(w, http.StatusConflict, map[string]any{
				"ok": false, "error": "create-session lifecycle reservation no longer matches the next available name",
			})
			return
		}
	}
	cwd := s.root
	if cwd == "" {
		cwd = "."
	}
	output, err := s.tmuxCommand("new-session", "-d", "-s", name, "-e", "TERM=xterm-256color", "-c", cwd, command).CombinedOutput()
	if err != nil {
		diagnostic := strings.TrimSpace(string(output))
		if diagnostic == "" {
			diagnostic = err.Error()
		}
		jsonResponse(w, http.StatusBadRequest, map[string]any{"ok": false, "error": diagnostic})
		return
	}
	s.configureTmuxSession(name)
	jsonResponse(w, http.StatusOK, map[string]any{
		"ok": true, "created": true, "session": name, "sessions": s.sessionNames(), "agent": agent,
		"terminal": terminal, "cwd": cwd, "command": command, "dangerously_yolo": launchDangerously,
		"generation": generation,
	})
}

func validSessionLaunchName(value string) bool {
	if value == "" {
		return false
	}
	for _, character := range value {
		if !(unicode.IsLetter(character) || unicode.IsDigit(character) || strings.ContainsRune("._+-", character)) {
			return false
		}
	}
	return true
}

func goSessionLaunchCommand(agent, terminal string, dangerouslyYolo bool) (string, error) {
	command := agent
	switch agent {
	case "claude", "codex", "opencode":
		if _, err := exec.LookPath(agent); err != nil {
			return "", fmt.Errorf("%s is not available on this server PATH", agent)
		}
	case "term":
		if !validSessionLaunchName(terminal) {
			return "", errors.New("choose an explicit terminal command")
		}
		if _, err := exec.LookPath(terminal); err != nil {
			return "", fmt.Errorf("terminal command is not available on this server PATH: %s", terminal)
		}
		command = terminal
	default:
		return "", fmt.Errorf("unsupported agent: %s", agent)
	}
	if dangerouslyYolo {
		switch agent {
		case "claude":
			command += " --dangerously-skip-permissions"
		case "codex":
			command += " --dangerously-bypass-approvals-and-sandbox --dangerously-bypass-hook-trust"
		}
	}
	return command, nil
}

func (s *server) createSessionPlan(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	name, err := s.nextAvailableSessionName()
	if err != nil {
		jsonResponse(w, http.StatusServiceUnavailable, map[string]any{"ok": false, "error": "tmux session roster is unavailable"})
		return
	}
	if name == "" {
		jsonResponse(w, http.StatusConflict, map[string]any{
			"ok": false, "error": "no available numbered session names from 1 to 99",
		})
		return
	}
	generation := s.nextSessionPlanGeneration()
	jsonResponse(w, http.StatusOK, map[string]any{
		"ok": true, "session": name, "generation": generation, "backend": "go",
	})
}

func (s *server) nextAvailableSessionName() (string, error) {
	roster, err := s.tmuxSessionRosterSnapshot(true)
	if err != nil {
		return "", err
	}
	return nextNumberedSessionName(roster.sessions), nil
}

func nextNumberedSessionName(existing []string) string {
	if len(existing) >= maxYolomuxSessionTabs {
		return ""
	}
	present := make(map[string]struct{}, len(existing))
	for _, session := range existing {
		present[session] = struct{}{}
	}
	for number := 1; number <= maxYolomuxSessionTabs; number++ {
		name := strconv.Itoa(number)
		if _, exists := present[name]; !exists {
			return name
		}
	}
	return ""
}

// settings delegates persistence to the repository's Python settings owner. Keeping one owner for
// defaults, validation, migrations, comments, and atomic YAML writes prevents the Go backend from
// silently accepting a different value. The bridge is intentionally JSON-only and uses the server's
// explicit config directory, so tests can provide a synthetic isolated file.
func (s *server) settings(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet && r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	var body string
	if r.Method == http.MethodPost {
		user, _ := r.Context().Value(authContextKey{}).(authUser)
		if user.role != "admin" {
			s.rejectForbidden(w, user, "admin")
			return
		}
		var payload map[string]any
		if err := decodeJSONBody(r.Body, 64*1024, &payload); err != nil {
			jsonResponse(w, http.StatusBadRequest, typedError("invalid settings request", "request.error.invalid", false))
			return
		}
		encoded, err := json.Marshal(payload)
		if err != nil {
			jsonResponse(w, http.StatusBadRequest, typedError("invalid settings request", "request.error.invalid", false))
			return
		}
		body = string(encoded)
	}

	script := `import json,sys
from yomux_lib.workspace.settings import save_settings, settings_payload
if sys.argv[1] == "POST":
    request = json.loads(sys.argv[2])
    patch = request.get("settings", request) if isinstance(request, dict) else request
    result = save_settings(patch)
else:
    result = settings_payload()
print(json.dumps(result, separators=(",", ":"), ensure_ascii=False), end="")`
	args := []string{"python3", "-c", script, r.Method}
	if r.Method == http.MethodPost {
		args = append(args, body)
	}
	command := exec.CommandContext(r.Context(), args[0], args[1:]...)
	command.Dir = s.root
	command.Env = pythonBridgeEnvironment(s.root, s.config)
	output, err := command.Output()
	if err != nil {
		slog.Error("settings bridge failed", "method", r.Method, "error", err)
		jsonResponse(w, http.StatusServiceUnavailable, typedError("settings unavailable", "common.requestFailed", true))
		return
	}
	var payload map[string]any
	if err := json.Unmarshal(output, &payload); err != nil {
		slog.Error("settings bridge returned invalid JSON", "method", r.Method, "error", err)
		jsonResponse(w, http.StatusServiceUnavailable, typedError("settings unavailable", "common.requestFailed", true))
		return
	}
	if errorMessage, ok := payload["error"].(string); ok && strings.TrimSpace(errorMessage) != "" {
		jsonResponse(w, http.StatusServiceUnavailable, map[string]any{
			"error":    errorMessage,
			"code":     "settings_file_malformed",
			"settings": nil,
			"terminal": true,
			"user_message": map[string]any{
				"key": "common.requestFailed", "params": map[string]any{}, "fallback": errorMessage,
			},
		})
		return
	}
	if r.Method == http.MethodPost && s.finder != nil {
		s.finder.SetUploadLimits(finder.UploadLimits{
			MaxBytes:         s.fileTransferMaxBytes(),
			MaxFiles:         s.fileTransferMaxFiles(),
			FilenameTemplate: s.fileTransferFilenameTemplate(),
		})
	}
	if r.Method == http.MethodPost && s.events != nil {
		if _, err := s.events.publish("settings_changed", map[string]any{"mtime_ns": payload["mtime_ns"], "data": payload}); err != nil {
			slog.Warn("settings change event publish failed", "error", err)
		}
	}
	jsonResponse(w, http.StatusOK, payload)
}

func pythonBridgeEnvironment(root, config string) []string {
	env := make([]string, 0, len(os.Environ())+2)
	for _, value := range os.Environ() {
		if strings.HasPrefix(value, "YOMUX_ROOT=") || strings.HasPrefix(value, "YOLOMUX_ROOT=") ||
			strings.HasPrefix(value, "YOMUX_CONFIG_DIR=") || strings.HasPrefix(value, "YOLOMUX_CONFIG_DIR=") ||
			strings.HasPrefix(value, "PYTHONPATH=") {
			continue
		}
		env = append(env, value)
	}
	env = append(env, "PYTHONPATH="+root, "YOMUX_CONFIG_DIR="+config, "YOMUX_AUTH_CONFIG_READ_ONLY=1")
	return env
}

// The browser carries this lifecycle token as a JavaScript Number, so it must stay below
// Number.MAX_SAFE_INTEGER. Milliseconds are sufficient for the short-lived reservation and the
// atomic monotonic step keeps two plans created in the same millisecond distinct.
func (s *server) nextSessionPlanGeneration() int64 {
	candidate := time.Now().UnixMilli()
	for {
		previous := s.sessionPlanGeneration.Load()
		if candidate <= previous {
			candidate = previous + 1
		}
		if s.sessionPlanGeneration.CompareAndSwap(previous, candidate) {
			return candidate
		}
	}
}

func (s *server) unsupported(w http.ResponseWriter, r *http.Request) {
	capability := capabilities.RouteCapability(r.Method, r.URL.RequestURI())
	if capability == "" {
		capability = "core.bootstrap.v1"
	}
	jsonResponse(w, http.StatusNotImplemented, map[string]any{
		"error": "feature_unavailable",
		"reason": map[string]any{
			"code": "not_implemented", "messageKey": "capabilityUnavailable",
			"messageParams": map[string]any{"capability": capability},
		},
		"capability": capability,
	})
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

func (s *server) renameSession(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	session := strings.TrimSpace(r.URL.Query().Get("session"))
	if session == "" {
		session = strings.TrimSpace(r.FormValue("session"))
	}
	newName := r.URL.Query().Get("new_name")
	if newName == "" {
		newName = r.FormValue("new_name")
	}
	newName = strings.NewReplacer(".", "_", ":", "_").Replace(strings.TrimSpace(newName))
	writeRenameError := func(status int, key, fallback string, params map[string]any) {
		jsonResponse(w, status, map[string]any{
			"session": session, "new_name": newName,
			"user_message": map[string]any{"key": key, "params": params, "fallback": fallback},
		})
	}
	if session == "" {
		writeRenameError(http.StatusBadRequest, "session.error.required", "session is required", map[string]any{})
		return
	}
	if newName == "" {
		writeRenameError(http.StatusBadRequest, "rename.error.required", "session name is required", map[string]any{})
		return
	}
	if len(newName) > 64 {
		writeRenameError(http.StatusBadRequest, "rename.error.tooLong", "session name must be 64 characters or fewer", map[string]any{})
		return
	}
	for _, character := range newName {
		if !((character >= 'a' && character <= 'z') || (character >= 'A' && character <= 'Z') || (character >= '0' && character <= '9') || character == ' ' || character == '-' || character == '_') {
			writeRenameError(http.StatusBadRequest, "rename.error.invalidChars", "session name may contain only letters, numbers, spaces, dot, dash, and underscore", map[string]any{})
			return
		}
	}
	// Keep roster reads serialized across the rename command and its confirming
	// read. A publication revision alone cannot distinguish a read that happened
	// before the command from a newer tmux state observed after it.
	s.tmuxRosterFetchMu.Lock()
	defer s.tmuxRosterFetchMu.Unlock()
	roster, err := s.tmuxSessionRosterSnapshotLocked(true)
	topology, err := s.tmuxTopologyFromRoster(roster, err)
	if err != nil {
		jsonResponse(w, http.StatusServiceUnavailable, typedError("session list unavailable", "common.requestFailed", true))
		return
	}
	sourceIdentity := topology.identities[session]
	if sourceIdentity == "" {
		sourceIdentity = session
	}
	sourceRosterRevision := topology.rosterRevision
	sessionFound := false
	nameExists := false
	for _, existing := range topology.sessions {
		sessionFound = sessionFound || existing == session
		nameExists = nameExists || existing == newName
	}
	if !sessionFound {
		writeRenameError(http.StatusNotFound, "common.requestFailed", "unknown session", map[string]any{})
		return
	}
	if newName == session {
		jsonResponse(w, http.StatusOK, map[string]any{
			"session": session, "new_session": newName, "renamed": false, "sessions": topology.sessions,
			"topology_generation": topology.generation, "ok": true,
		})
		return
	}
	if nameExists {
		writeRenameError(http.StatusConflict, "rename.error.exists", "session already exists: "+newName, map[string]any{"name": newName})
		return
	}
	commandContext, cancel := context.WithTimeout(r.Context(), 3*time.Second)
	defer cancel()
	output, err := s.tmuxCommandContext(commandContext, tmuxRenameCommandArgs(topology, session, newName)...).CombinedOutput()
	if err != nil {
		message := strings.TrimSpace(string(output))
		if message == "" {
			message = err.Error()
		}
		writeRenameError(http.StatusInternalServerError, "status.sessionRenameFailed", "session rename failed: "+message, map[string]any{"error": message})
		return
	}
	roster, refreshErr := s.tmuxSessionRosterSnapshotLocked(true)
	topology, refreshErr = s.tmuxTopologyFromRoster(roster, refreshErr)
	if refreshErr != nil {
		slog.Warn("tmux topology refresh failed after session rename", "session", newName, "error", refreshErr)
		cached := s.cachedTmuxSessionRosterSnapshot()
		if cached.revision == sourceRosterRevision && tmuxSessionNameForIdentity(goTmuxTopology{identities: cached.identities}, sourceIdentity) == session {
			s.publishTmuxSessionRenameAfterRosterFailureLocked(session, newName, cached)
			topology, _ = s.tmuxTopologyFromRoster(s.cachedTmuxSessionRosterSnapshot(), nil)
		}
	}
	currentName := tmuxSessionNameForIdentity(topology, sourceIdentity)
	if currentName == "" {
		writeRenameError(http.StatusConflict, "status.sessionRenameFailed", "session changed during rename; refresh and try again", map[string]any{"error": "renamed session is no longer present"})
		return
	}
	jsonResponse(w, http.StatusOK, map[string]any{
		"session": session, "new_session": currentName, "renamed": true, "sessions": topology.sessions,
		"topology_generation": topology.generation, "ok": true,
	})
}

func tmuxSessionNameForIdentity(topology goTmuxTopology, identity string) string {
	for name, currentIdentity := range topology.identities {
		if currentIdentity == identity {
			return name
		}
	}
	return ""
}

func tmuxRenameCommandArgs(topology goTmuxTopology, session, newName string) []string {
	identity := strings.Fields(strings.NewReplacer("\x1f", "\t").Replace(topology.identities[session]))
	if len(identity) == 3 && strings.HasPrefix(identity[0], "$") {
		_, sessionIDErr := strconv.ParseUint(strings.TrimPrefix(identity[0], "$"), 10, 64)
		_, createdErr := strconv.ParseUint(identity[1], 10, 64)
		_, pidErr := strconv.ParseUint(identity[2], 10, 64)
		if sessionIDErr == nil && createdErr == nil && pidErr == nil {
			condition := fmt.Sprintf(
				"#{&&:#{==:#{session_id},%s},#{&&:#{==:#{session_created},%s},#{==:#{pid},%s}}}",
				identity[0], identity[1], identity[2],
			)
			command := "rename-session -t " + strconv.Quote("="+session+":") + " -- " + strconv.Quote(newName)
			return []string{"if-shell", "-F", "-t", "=" + session + ":", condition, command, "run-shell \"exit 1\""}
		}
	}
	return []string{"rename-session", "-t", "=" + session + ":", "--", newName}
}

func (s *server) publishTmuxSessionRenameAfterRosterFailureLocked(session, newName string, cached tmuxSessionRosterSnapshot) {
	renamedSessions := make([]string, 0, len(cached.sessions))
	renamedIdentities := make(map[string]string, len(cached.identities))
	for _, existing := range cached.sessions {
		if existing == session {
			if identity := cached.identities[existing]; identity != "" {
				renamedIdentities[newName] = identity
			}
			existing = newName
		} else if identity := cached.identities[existing]; identity != "" {
			renamedIdentities[existing] = identity
		}
		renamedSessions = append(renamedSessions, existing)
	}
	s.rememberTmuxSessionRoster(renamedSessions, renamedIdentities)
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
	request := map[string]any{
		"sessions": s.sessionNames(), "role": user.role, "username": user.username,
		"language": r.Header.Get("Accept-Language"), "capabilities": s.capabilities.Snapshot(),
		"stats_only": queryTrue(r.URL.Query().Get("stats_only")), "backend": goRuntimeLabel(),
		"dangerously_yolo": s.dangerouslyYolo,
	}
	s.homeRendererMu.Lock()
	if s.homeRenderer == nil {
		s.homeRenderer, _ = startPythonHomeRenderer(s.root, s.config)
	}
	var body []byte
	var err error
	if s.homeRenderer == nil {
		err = fmt.Errorf("Python HTML renderer could not start")
	} else {
		body, err = s.homeRenderer.render(request)
		if err != nil {
			s.homeRenderer.close()
			s.homeRenderer = nil
		}
	}
	s.homeRendererMu.Unlock()
	if err != nil {
		http.Error(w, "application unavailable", http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	_, _ = w.Write(body)
}

func (s *server) static(w http.ResponseWriter, r *http.Request) {
	relative := filepath.Clean(strings.TrimPrefix(r.URL.Path, "/static/"))
	if canonical, ok := map[string]string{
		"yolomux.css":              "yomux.css",
		"yolomux.js":               "yomux.js",
		"fonts/yolomux-ui.woff2":   "fonts/yomux-ui.woff2",
		"fonts/yolomux-mono.woff2": "fonts/yomux-mono.woff2",
	}[relative]; ok {
		relative = canonical
	}
	staticRoot := filepath.Join(s.root, "static")
	path := filepath.Join(staticRoot, relative)
	if path != staticRoot && !strings.HasPrefix(path, staticRoot+string(filepath.Separator)) {
		http.NotFound(w, r)
		return
	}
	http.ServeFile(w, r, path)
}

func (s *server) sessionNames() []string {
	sessions, err := s.tmuxSessionRoster()
	if err != nil {
		cached, known := s.cachedTmuxSessionRoster()
		if known {
			return cached
		}
		return []string{}
	}
	return sessions
}

func (s *server) cachedTmuxSessionRoster() ([]string, bool) {
	s.tmuxRosterMu.RLock()
	defer s.tmuxRosterMu.RUnlock()
	return append([]string(nil), s.tmuxRoster...), s.tmuxRosterKnown
}

func (s *server) cachedTmuxSessionRosterSnapshot() tmuxSessionRosterSnapshot {
	s.tmuxRosterMu.RLock()
	defer s.tmuxRosterMu.RUnlock()
	identities := make(map[string]string, len(s.tmuxRosterIdentities))
	for session, id := range s.tmuxRosterIdentities {
		identities[session] = id
	}
	return tmuxSessionRosterSnapshot{
		sessions: append([]string(nil), s.tmuxRoster...), identities: identities, known: s.tmuxRosterKnown, revision: s.tmuxRosterRevision,
	}
}

func (s *server) tmuxSessionRosterFresh() bool {
	s.tmuxRosterMu.RLock()
	known := s.tmuxRosterKnown
	sampledAt := s.tmuxRosterSampledAt
	s.tmuxRosterMu.RUnlock()
	return known && !sampledAt.IsZero() && time.Since(sampledAt) < goProcessMetricsCacheTTL
}

func (s *server) rememberTmuxSessionRoster(sessions []string, identities map[string]string) {
	s.tmuxRosterMu.Lock()
	s.tmuxRoster = append([]string(nil), sessions...)
	s.tmuxRosterIdentities = make(map[string]string, len(sessions))
	for _, session := range sessions {
		identity := identities[session]
		if identity == "" {
			identity = session
		}
		s.tmuxRosterIdentities[session] = identity
	}
	s.tmuxRosterKnown = true
	s.tmuxRosterRevision++
	s.tmuxRosterSampledAt = time.Now()
	s.tmuxRosterMu.Unlock()
}

func (s *server) tmuxSessionRoster() ([]string, error) {
	roster, err := s.tmuxSessionRosterSnapshot(false)
	return roster.sessions, err
}

func (s *server) tmuxSessionRosterSnapshot(forceFresh bool) (tmuxSessionRosterSnapshot, error) {
	s.tmuxRosterFetchMu.Lock()
	defer s.tmuxRosterFetchMu.Unlock()
	return s.tmuxSessionRosterSnapshotLocked(forceFresh)
}

func (s *server) tmuxSessionRosterSnapshotLocked(forceFresh bool) (tmuxSessionRosterSnapshot, error) {
	if s.tmuxSessionRosterFn != nil {
		sessions, err := s.tmuxSessionRosterFn()
		if err == nil {
			sessions = normalizeTmuxSessionRoster(sessions)
			s.rememberTmuxSessionRoster(sessions, nil)
			return s.cachedTmuxSessionRosterSnapshot(), nil
		}
		return s.cachedTmuxSessionRosterSnapshot(), err
	}
	s.tmuxRosterMu.RLock()
	if !forceFresh && s.tmuxRosterKnown && !s.tmuxRosterSampledAt.IsZero() && time.Since(s.tmuxRosterSampledAt) < goProcessMetricsCacheTTL {
		roster := tmuxSessionRosterSnapshot{
			sessions: append([]string(nil), s.tmuxRoster...), identities: make(map[string]string, len(s.tmuxRosterIdentities)), known: true, revision: s.tmuxRosterRevision,
		}
		for session, id := range s.tmuxRosterIdentities {
			roster.identities[session] = id
		}
		s.tmuxRosterMu.RUnlock()
		return roster, nil
	}
	s.tmuxRosterMu.RUnlock()
	commandContext, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	out, err := s.tmuxCommandContext(commandContext, "list-sessions", "-F", "#{session_name}\t#{session_id}\t#{session_created}\t#{pid}").CombinedOutput()
	if err != nil {
		if stats.TmuxNoServerError(out) {
			s.rememberTmuxSessionRoster([]string{}, nil)
			return s.cachedTmuxSessionRosterSnapshot(), nil
		}
		return s.cachedTmuxSessionRosterSnapshot(), err
	}
	var result []string
	identities := map[string]string{}
	for _, line := range strings.Split(strings.TrimSpace(string(out)), "\n") {
		fields := strings.SplitN(line, "\t", 2)
		if len(fields) == 0 || fields[0] == "" {
			continue
		}
		result = append(result, fields[0])
		if len(fields) > 1 {
			identities[fields[0]] = strings.Join(fields[1:], "\x1f")
		}
	}
	result = normalizeTmuxSessionRoster(result)
	s.rememberTmuxSessionRoster(result, identities)
	return s.cachedTmuxSessionRosterSnapshot(), nil
}

func normalizeTmuxSessionRoster(raw []string) []string {
	seen := make(map[string]struct{}, len(raw))
	result := make([]string, 0, len(raw))
	for _, value := range raw {
		name := strings.TrimSpace(value)
		if name == "" {
			continue
		}
		if _, exists := seen[name]; exists {
			continue
		}
		seen[name] = struct{}{}
		result = append(result, name)
	}
	sort.SliceStable(result, func(i, j int) bool {
		left := tmuxSessionSortKey(result[i])
		right := tmuxSessionSortKey(result[j])
		if left.rank != right.rank {
			return left.rank < right.rank
		}
		if left.group != right.group {
			return left.group < right.group
		}
		return left.number < right.number
	})
	return result
}

type tmuxSessionSortValue struct {
	rank   int
	group  string
	number int
}

// tmuxSessionSortKey mirrors Python's list_tmux_session_names ordering. Numeric
// YOmux/project names stay naturally ordered (2 before 10), while arbitrary
// session names use a case-insensitive lexical order.
func tmuxSessionSortKey(name string) tmuxSessionSortValue {
	for rank, prefix := range []string{"yolomux", "project"} {
		suffix := strings.TrimPrefix(name, prefix)
		if suffix == "" {
			continue
		}
		allDigits := true
		for _, char := range suffix {
			if char < '0' || char > '9' {
				allDigits = false
				break
			}
		}
		if allDigits {
			number, err := strconv.Atoi(suffix)
			if err == nil {
				return tmuxSessionSortValue{rank: rank, group: prefix, number: number}
			}
		}
	}
	return tmuxSessionSortValue{rank: 2, group: strings.ToLower(name)}
}

func (s *server) tmuxSessionActivityRoster() (map[string]float64, error) {
	commandContext, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	out, err := s.tmuxCommandContext(commandContext, "list-sessions", "-F", "#{session_name}\t#{session_activity}").CombinedOutput()
	if err != nil {
		if stats.TmuxNoServerError(out) {
			return map[string]float64{}, nil
		}
		return nil, err
	}
	result := make(map[string]float64)
	for _, line := range strings.Split(strings.TrimSpace(string(out)), "\n") {
		parts := splitTmuxFields(line)
		if len(parts) != 2 || strings.TrimSpace(parts[0]) == "" {
			continue
		}
		value, parseErr := strconv.ParseFloat(strings.TrimSpace(parts[1]), 64)
		if parseErr != nil || value < 0 {
			continue
		}
		result[parts[0]] = value
	}
	return result, nil
}

func (s *server) tmuxWindowActivityRoster() (map[string]float64, error) {
	commandContext, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	out, err := s.tmuxCommandContext(commandContext, "list-windows", "-a", "-F", "#{session_name}\t#{window_index}\t#{window_activity}").CombinedOutput()
	if err != nil {
		if stats.TmuxNoServerError(out) {
			return map[string]float64{}, nil
		}
		return nil, err
	}
	result := make(map[string]float64)
	for _, line := range strings.Split(strings.TrimSpace(string(out)), "\n") {
		parts := splitTmuxFields(line)
		if len(parts) != 3 || strings.TrimSpace(parts[0]) == "" || strings.TrimSpace(parts[1]) == "" {
			continue
		}
		value, parseErr := strconv.ParseFloat(strings.TrimSpace(parts[2]), 64)
		if parseErr != nil || value < 0 {
			continue
		}
		result[parts[0]+":"+parts[1]] = value
	}
	return result, nil
}

func (s *server) tmuxCommand(args ...string) *exec.Cmd {
	return s.tmuxCommandContext(context.Background(), args...)
}

func (s *server) tmuxCommandContext(ctx context.Context, args ...string) *exec.Cmd {
	values := []string{}
	socket := strings.TrimSpace(os.Getenv("YOLOMUX_TMUX_SOCKET"))
	if socket != "" {
		values = append(values, "-S", socket)
	}
	command := exec.CommandContext(ctx, "tmux", append(values, args...)...)
	// A direct Go entrypoint can be started from inside a nested/private tmux client,
	// where tmux would otherwise ignore the machine-wide default server and follow the
	// inherited TMUX socket. Operator launches use the global server unless an explicit
	// fixture socket was supplied; enforce that boundary at every command call so the
	// Tabber roster cannot silently come from the launcher's supervisor server.
	environment := make([]string, 0, len(os.Environ())+1)
	for _, value := range os.Environ() {
		if socket == "" && (strings.HasPrefix(value, "TMUX=") || strings.HasPrefix(value, "TMUX_PANE=")) {
			continue
		}
		// tmux renders whitespace format separators as underscores under the empty
		// locale used by Bazel's hermetic tests. Keep the wire format stable without
		// changing an operator's configured locale when one is already present.
		if os.Getenv("LC_ALL") == "" && os.Getenv("LC_CTYPE") == "" && os.Getenv("LANG") == "" && (strings.HasPrefix(value, "LC_ALL=") || strings.HasPrefix(value, "LC_CTYPE=") || strings.HasPrefix(value, "LANG=")) {
			continue
		}
		environment = append(environment, value)
	}
	if os.Getenv("LC_ALL") == "" && os.Getenv("LC_CTYPE") == "" && os.Getenv("LANG") == "" {
		environment = append(environment, "LC_ALL=C.UTF-8")
	}
	command.Env = environment
	return command
}

func (s *server) exactTarget(session string) string { return "=" + session + ":" }

func (s *server) supportsIgnoreSize() bool {
	s.ignoreSizeMu.Lock()
	defer s.ignoreSizeMu.Unlock()
	if s.ignoreSizeChecked {
		return s.ignoreSizeSupported
	}
	// `attach-session -?` only prints generic usage on current tmux versions and
	// therefore cannot establish whether client flags are supported. Probe the
	// actual flag against a deliberately nonexistent target, matching the Python
	// backend: supported versions reject the target, while older versions reject
	// the option or flag itself.
	out, _ := s.tmuxCommand(
		"attach-session", "-f", "ignore-size", "-t", "__yolomux_ignore_size_capability_probe__",
	).CombinedOutput()
	text := strings.ToLower(string(out))
	s.ignoreSizeSupported = !strings.Contains(text, "unknown option") && !strings.Contains(text, "unknown flag")
	s.ignoreSizeChecked = true
	return s.ignoreSizeSupported
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
		if client == activeClient {
			_ = s.tmuxCommand("refresh-client", "-t", client, "-f", "!ignore-size").Run()
		} else if width > cols || height > rows {
			_ = s.tmuxCommand("refresh-client", "-t", client, "-f", "ignore-size").Run()
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
	cmd.SysProcAttr = terminalProcessAttributes()
	cmd.Env = append(os.Environ(), "TERM=xterm-256color")
	cmd.Stdin = pty.slave
	cmd.Stdout = pty.slave
	cmd.Stderr = pty.slave
	if err := cmd.Start(); err != nil {
		slog.Error("tmux attach failed", "session", session, "error", err)
		return
	}
	activeTTY := pty.tty
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
				s.claimResizeAuthority(session, pty.tty, cols, rows)
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
	// JSON API representations are request-scoped products. Match the Python
	// response writer so browsers and parity clients cannot reuse a stale
	// snapshot, error, or capability response from an intermediary cache.
	w.Header().Set("Cache-Control", "no-store")
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

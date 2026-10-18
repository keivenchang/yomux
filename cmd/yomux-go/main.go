package main

import (
	"bufio"
	"bytes"
	"context"
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
	"github.com/keivenchang/yomux/internal/chat"
	"github.com/keivenchang/yomux/internal/finder"
	"github.com/keivenchang/yomux/internal/productenv"
	"github.com/keivenchang/yomux/internal/stats"
	"github.com/keivenchang/yomux/internal/tmuxcmd"
	"github.com/keivenchang/yomux/internal/work"
	"github.com/keivenchang/yomux/internal/yoagent"
)

const wsGUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
const maxYomuxSessionTabs = 99
const goServiceID = "yomux-go"

func goRuntimeLabel() string {
	return fmt.Sprintf("Go %s %s/%s", strings.TrimPrefix(runtime.Version(), "go"), runtime.GOOS, runtime.GOARCH)
}

type server struct {
	runtimeMemory          runtimeMemoryState
	cpuCaptureMu           sync.Mutex
	cpuCapture             finder.CPUCapture
	root                   string
	host                   string
	port                   int
	sessionFilesListener   string
	dangerouslyYolo        bool
	config                 string
	httpMode               bool
	tlsCertificate         string
	tlsKey                 string
	mu                     sync.Mutex
	clientEventMu          sync.Mutex
	clientEventConnections map[string]int
	children               map[*exec.Cmd]struct{}
	chat                   *nativeChat
	yoagentState           *yoagent.Store
	yoagent                *nativeYOAgent
	agentActions           *nativeYOAgentActions
	yoagentJobMu           sync.Mutex
	events                 *eventBroker

	eventLogPath                        string
	background                          *goBackgroundOwner
	finder                              *finder.Service
	searchIndex                         *finder.SearchIndexWorkerClient
	indexSettingsMu                     sync.RWMutex
	indexSettingsUpdateMu               work.ContextMutex
	indexSettings                       finderIndexSettings
	indexActiveSettings                 finderIndexSettings
	indexApplicationEpoch               uint64
	indexWorkerIncarnation              string
	indexSettingsError                  string
	indexSettingsFileDigest             [32]byte
	indexSettingsFencePending           bool
	indexSettingsFencePreserveSnapshots bool
	indexSettingsFenceRoots             []string
	indexRefreshMu                      sync.Mutex
	indexRefreshDeadlines               map[string]indexRefreshDeadline
	indexRefreshWake                    chan struct{}
	finderJobs                          *work.Scheduler
	finderBulkJobs                      *work.Scheduler
	finderStreamJobs                    *work.Scheduler
	capabilities                        *capabilities.Catalog
	updates                             *updateChecker
	instanceID                          string
	launchIdentity                      map[string]string
	updateJobMu                         sync.Mutex
	updateJobPath                       string
	statsPath                           string
	statsNow                            func() time.Time
	attentionAckInitMu                  sync.Mutex
	attentionAcks                       *attentionAckState
	statsHeartbeat                      time.Duration
	refillMu                            sync.Mutex
	refillRoot                          string
	refillContext                       context.Context
	refillRun                           func(context.Context, time.Time, func(string)) (stats.AgentHistoryRefillResult, error)
	statsWriter                         *stats.Writer
	statsService                        *stats.StatsService
	statsLifecycle                      *stats.Lifecycle
	statsRuntimeRecorder                *statsRuntimeRecorder
	syntheticHostInputs                 bool
	statsMigration                      map[string]any
	statsProcessSourceOnce              sync.Once
	statsProcessSource                  string
	statsDeltaMu                        sync.Mutex
	statsDeltaRevisions                 map[statsDeltaKey]map[int64]int64
	statsDeltaNextRevision              map[statsDeltaKey]int64
	statsDemand                         atomic.Int64
	hostCollector                       *stats.HostCollector
	agentStatusCollector                *stats.AgentStatusCollector
	tokenCollector                      *stats.AgentTokenCollector
	pricing                             *stats.PricingCatalog
	pricingRefreshMu                    sync.Mutex
	pricingRefreshing                   bool
	pricingTrailingReason               string
	pricingRefreshState                 map[string]any
	pricingRefreshEpoch                 uint64
	pricingRefreshRun                   func(context.Context) error
	pricingInvalidate                   func(context.Context) error
	pricingNextRefresh                  time.Time
	pricingNextInvalidation             time.Time
	pricingFailures                     int
	pricingInvalidationFailures         int
	pricingWake                         chan struct{}
	pricingPeriodic                     bool
	yolo                                *goYoloStore

	gitMetadataSnapshotFn func(string) (goGitSnapshot, bool)
	terminalWriteTimeout  time.Duration
	terminalActivity      terminalActivityState
	tabberActivity        tabberActivityState
	ignoreSizeMu          sync.Mutex
	ignoreSizeChecked     bool
	ignoreSizeSupported   bool
	sessionPlanGeneration atomic.Int64
	logRing               *goLogRing
	rootLease             *os.File
	requestMu             sync.Mutex
	requestClosed         bool
	requestCancels        map[*http.Request]context.CancelFunc
	requestWorkers        sync.WaitGroup
	tmuxPointContext      context.Context
	tmuxPointCancel       context.CancelFunc
	serverCancel          context.CancelFunc
	shutdownOnce          sync.Once
	backgroundWorkMu      sync.Mutex
	backgroundWorkClosed  bool
	backgroundWork        sync.WaitGroup
	shutdownClean         bool
	startedAt             time.Time
	healthLedgerMu        sync.Mutex
	healthLedger          *goHealthLedger
	healthLifecycle       *goHealthLifecycle

	performanceMu           sync.Mutex
	performanceRecords      []goPerformanceRecord
	performanceCaptureTotal uint64
	state                   string
	finderBackgroundJobs    *work.Scheduler
	sessionFilesCache       sessionFilesCacheState

	tmuxWindowActivityFn             func() (map[string]float64, error)
	tmuxCommandContextFn             func(context.Context, ...string) *exec.Cmd
	processEntriesFn                 func() ([]procEntry, error)
	processEntriesCacheMu            sync.Mutex
	processEntriesCacheAt            time.Time
	processEntriesCache              []procEntry
	processEntriesCacheValid         bool
	processEntriesCacheHits          uint64
	processEntriesCacheMisses        uint64
	processEntriesCacheLastDuration  time.Duration
	processEntriesCacheDurationTotal time.Duration
	childProcessEntriesFn            func(int) ([]procEntry, error)
	agentStatusClassifier            func(context.Context, stats.AgentStatusRequest) stats.AgentStatusResult
	agentProcessSnapshot             func(context.Context, int) (stats.AgentStatusProcess, error)
	goYoloMetadataRefreshFn          func(string) error
	goYoloPaneOwnsProcessFn          func(context.Context, string, int) (bool, error)
	processMetricsFn                 func(time.Time) goProcessMetrics
	childProcessMetricsFn            func(time.Time) []goChildProcessMetric
	goroutineCountFn                 func() int
	agentInventoryMu                 sync.Mutex
	agentInventoryAt                 time.Time
	agentInventoryRecords            []stats.AgentRecord
	agentInventoryErrors             []error
	agentInventoryCacheValid         bool
	agentInventoryCacheHits          uint64
	agentInventoryCacheMisses        uint64
	agentInventoryLastDuration       time.Duration
	agentInventoryDurationTotal      time.Duration
	processMetricsCacheMu            sync.Mutex
	processMetricsCacheAt            time.Time
	processMetricsCache              goProcessMetrics
	processMetricsCacheValid         bool
	processMetricsCacheHits          uint64
	processMetricsCacheMisses        uint64
	processMetricsLastDuration       time.Duration
	processMetricsDurationTotal      time.Duration
	processMetricsLastThreadRows     uint64
	processMetricsLastChildRows      uint64
	processMetricsMu                 sync.Mutex
	processMetricsAt                 time.Time
	processCPUSeconds                float64
	processCPUValid                  bool
	childCPU                         map[string]childCPUState
	threadCPU                        map[string]childCPUState
	authState
	nativePageState
	tmuxMetadataState
	operationState
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

type wsConn struct {
	net.Conn
	writeMu      sync.Mutex
	writeTimeout time.Duration
}

type winsize struct{ rows, cols, x, y uint16 }

type ptyPair struct {
	rows, cols     int
	master         *os.File
	slave          *os.File
	tty            string
	requestControl func(cols, rows, txn int, authority ...bool)
}

// Ownership renewal must reconcile tmux even when the local PTY size is stable.
// Keep the dimensions here so both platform ioctl implementations share that rule.
func (p *ptyPair) resize(rows, cols int) error {
	if p.rows == rows && p.cols == cols {
		return nil
	}
	if err := p.resizeDimensions(rows, cols); err != nil {
		return err
	}
	p.rows, p.cols = rows, cols
	return nil
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

// Fd puts pollable descriptors back into blocking mode. Control preserves close/deadline cancellation.
func (p *ptyPair) withMasterDescriptor(action func(uintptr) error) error {
	raw, err := p.master.SyscallConn()
	if err != nil {
		return err
	}
	var actionErr error
	if err := raw.Control(func(fd uintptr) { actionErr = action(fd) }); err != nil {
		return err
	}
	return actionErr
}

const terminalWriteTimeout = 5 * time.Second

func (c *wsConn) frame(opcode byte, payload []byte) error {
	c.writeMu.Lock()
	defer c.writeMu.Unlock()
	timeout := c.writeTimeout
	if timeout <= 0 {
		timeout = terminalWriteTimeout
	}
	if err := c.Conn.SetWriteDeadline(time.Now().Add(timeout)); err != nil {
		_ = c.Conn.Close()
		return err
	}
	defer c.Conn.SetWriteDeadline(time.Time{})
	err := writeFrame(c.Conn, opcode, payload)
	if err != nil {
		_ = c.Conn.Close()
	}
	return err
}

func (s *server) registerFilesystemRoutes(mux *http.ServeMux) {
	mux.Handle("/api/runtime/cpu-profile", s.protected(http.HandlerFunc(s.runtimeCPUProfile), "admin"))
	mux.Handle("/api/session-files", s.protected(http.HandlerFunc(s.sessionFiles)))
	mux.Handle("/api/session-files-stream", s.protected(http.HandlerFunc(s.sessionFilesStream)))
	mux.Handle("/api/session-files-batch", s.protected(http.HandlerFunc(s.sessionFilesBatch)))
	mux.Handle("/api/fs/fast/list", s.protected(http.HandlerFunc(s.fsFastList)))
	mux.Handle("/api/fs/list", s.protected(http.HandlerFunc(s.fsList)))
	mux.Handle("/api/fs/read", s.protected(http.HandlerFunc(s.fsRead)))
	mux.Handle("/api/fs/info", s.protected(http.HandlerFunc(s.fsInfo)))
	mux.Handle("/api/fs/diff", s.protected(http.HandlerFunc(s.fsDiff)))
	mux.Handle("/api/fs/git-history", s.protected(http.HandlerFunc(s.fsGitHistory)))
	mux.Handle("/api/fs/git-history-stats-stream", s.protected(http.HandlerFunc(s.fsGitHistoryStatsStream)))
	mux.Handle("/api/fs/git-commit", s.protected(http.HandlerFunc(s.fsGitCommit)))
	mux.Handle("/api/fs/raw", s.protected(http.HandlerFunc(s.fsRaw)))
	mux.Handle("/api/fs/search", s.protected(http.HandlerFunc(s.fsSearch)))
	mux.Handle("/api/fs/search-stream", s.protected(http.HandlerFunc(s.fsSearch)))
	mux.Handle("/api/fs/index-status", s.protected(http.HandlerFunc(s.fsIndexStatus)))
	mux.Handle("/api/fs/watch-diff", s.protected(http.HandlerFunc(s.fsWatchDiff)))
	mux.Handle("/api/fs/resolve-file-candidates", s.protected(http.HandlerFunc(s.fsResolveCandidates)))
	mux.Handle("/api/fs/write", s.protected(http.HandlerFunc(s.fsWrite)))
	mux.Handle("/api/fs/delete", s.protected(http.HandlerFunc(s.fsDelete)))
	mux.Handle("/api/fs/unindex", s.protected(http.HandlerFunc(s.fsUnindex)))
	mux.Handle("/api/fs/rename", s.protected(http.HandlerFunc(s.fsRename)))
	mux.Handle("/api/fs/mkdir", s.protected(http.HandlerFunc(s.fsMkdir)))
	mux.Handle("/api/fs/batch", s.protected(http.HandlerFunc(s.fsBatch)))
	mux.Handle("/api/upload", s.protected(http.HandlerFunc(s.upload)))
	mux.Handle("/api/batch/search", s.protected(http.HandlerFunc(s.fsSearch)))
	mux.Handle("/api/batch/blame", s.protected(http.HandlerFunc(s.fsBlame)))
	mux.Handle("/api/batch/zip", s.protected(http.HandlerFunc(s.fsZip)))
	mux.Handle("/api/batch/count", s.protected(http.HandlerFunc(s.fsCount)))
	mux.Handle("/api/batch/html-preview", s.protected(http.HandlerFunc(s.fsHTMLPreview)))
}

func (s *server) registerSessionRoutes(mux *http.ServeMux) {
	mux.Handle("/api/ensure-session", s.protected(http.HandlerFunc(s.ensureSession)))
	mux.Handle("/api/tabber-description", s.protected(http.HandlerFunc(s.tabberDescription)))
	mux.Handle("/api/tabber-view-session", s.protected(http.HandlerFunc(s.tabberViewSession)))
	mux.Handle("/api/create-session-plan", s.protected(http.HandlerFunc(s.createSessionPlan)))
	mux.Handle("/api/create-session", s.protected(http.HandlerFunc(s.createSession)))
	mux.Handle("/api/rename-session", s.protected(http.HandlerFunc(s.renameSession)))
	mux.Handle("/api/kill-session", s.protected(http.HandlerFunc(s.killSession)))
	mux.Handle("/api/kill-window", s.protected(http.HandlerFunc(s.killWindow)))
}

// explicitCollectorPaths is deliberately fail-closed. Collector inputs are
// mutable host state, so deriving them from HOME, CODEX_HOME, or XDG_DATA_HOME
// would let a development/test process read an operator's real client data.
// Production may opt in by passing all three absolute paths explicitly.
func explicitCollectorPaths() (claudeRoot, codexRoot, opencodeDatabase string, err error) {
	values := []struct {
		setting string
		value   *string
	}{
		{"CLAUDE_TRANSCRIPT_ROOT", &claudeRoot},
		{"CODEX_TRANSCRIPT_ROOT", &codexRoot},
		{"OPENCODE_DATABASE", &opencodeDatabase},
	}
	for _, item := range values {
		*item.value = strings.TrimSpace(productenv.Value(item.setting))
		if *item.value == "" {
			return "", "", "", fmt.Errorf("stats collectors require explicit %s", productenv.Name(item.setting))
		}
		if !filepath.IsAbs(*item.value) {
			return "", "", "", fmt.Errorf("stats collector path %s must be absolute", productenv.Name(item.setting))
		}
	}
	return claudeRoot, codexRoot, opencodeDatabase, nil
}

func useMachineTmuxForOperatorLaunch() {
	if strings.TrimSpace(productenv.Value("TMUX_SOCKET")) != "" {
		return
	}
	_ = os.Unsetenv("TMUX")
	_ = os.Unsetenv("TMUX_PANE")
	productenv.Assign("TMUX_ALLOW_DEFAULT_SERVER", "1")
}

func main() {
	if err := validateNativeBackend(os.Args[1:]); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(2)
	}
	if len(os.Args) > 1 && os.Args[1] == "--operator" {
		if err := runNativeOperator(os.Args[2:]); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(2)
		}
		return
	}
	if len(os.Args) > 1 && os.Args[1] == "--internal-self-update-supervisor" {
		if len(os.Args) != 3 {
			fmt.Fprintln(os.Stderr, "usage: yomux-go --internal-self-update-supervisor <spec.json>")
			os.Exit(2)
		}
		if err := runSelfUpdateSupervisor(os.Args[2]); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		return
	}
	initialLaunchIdentity := [2]string{productenv.Value("ROOT"), productenv.Value("INSTANCE")}
	normalizeYomuxEnvironment()
	useMachineTmuxForOperatorLaunch()
	options := registerServerFlags(flag.CommandLine)
	managedLaunch := options.managedLaunch
	launchPlanJSON := options.launchPlanJSON
	forceLaunch := options.forceLaunch
	port := options.port
	listenFD := options.listenFD
	launchLeaseFDs := options.launchLeaseFDs
	host := options.host
	root := options.root
	configDir := options.configDir
	statsPath := options.statsPath
	httpMode := options.httpMode
	certPath := options.certPath
	keyPath := options.keyPath
	dangerouslyYolo := options.dangerouslyYolo
	searchIndexWorker := options.searchIndexWorker
	searchIndexDB := options.searchIndexDB
	showVersion := options.showVersion
	flag.Parse()
	if err := acceptLaunchDescriptors(*launchLeaseFDs, *listenFD); err != nil {
		fatal("invalid managed launch leases: " + err.Error())
	}
	if *searchIndexWorker {
		if *searchIndexDB == "" {
			fatal("search index worker requires --index-db")
		}
		runtime.GOMAXPROCS(1)
		lowerSearchIndexWorkerPriority()
		if err := finder.RunSearchIndexWorker(os.Stdin, os.Stdout, *searchIndexDB); err != nil {
			fatal("search index worker failed: " + err.Error())
		}
		return
	}
	if *showVersion {
		fmt.Printf("yomux-go %s\n", version)
		return
	}
	if *managedLaunch {
		explicit := make(map[string]bool)
		flag.Visit(func(value *flag.Flag) { explicit[value.Name] = true })
		if err := nativeManagedDefaults(explicit, port, host, dangerouslyYolo); err != nil {
			fatal(err.Error())
		}
	}
	if *launchPlanJSON != "" && !*managedLaunch {
		fatal("--launch-plan-json requires --launch")
	}
	if *port < 1 || *port > 65535 {
		fatal("invalid port")
	}
	var managedListenerFile *os.File
	if *managedLaunch {
		absolute, err := filepath.Abs(*root)
		if err != nil {
			fatal(err.Error())
		}
		*root = absolute
		if err := validateNativeTLS(*httpMode, *certPath, *keyPath); err != nil {
			fatal(err.Error())
		}
		configExplicit := false
		flag.Visit(func(value *flag.Flag) {
			if value.Name == "config" {
				configExplicit = true
			}
		})
		if configExplicit {
			if err := os.Setenv(productenv.Name("CONFIG_DIR"), *configDir); err != nil {
				fatal(err.Error())
			}
		}
		if *listenFD >= 0 {
			if err := nativeInheritedLaunchPreflight(absolute, *host, *port, *listenFD, *launchPlanJSON); err != nil {
				fatal(err.Error())
			}
		} else if err := nativeManagedPreflight(absolute, *host, *port, *httpMode, *forceLaunch, *launchPlanJSON); err != nil {
			fatal(err.Error())
		}
		if initialLaunchIdentity != [2]string{productenv.Value("ROOT"), productenv.Value("INSTANCE")} {
			if err := reexecNativeManagedLaunch(*listenFD); err != nil {
				fatal("publish managed launch environment: " + err.Error())
			}
		}
		if *listenFD < 0 {
			listener, err := net.Listen("tcp", net.JoinHostPort(*host, strconv.Itoa(*port)))
			if err != nil {
				fatal(err.Error())
			}
			file, err := listener.(*net.TCPListener).File()
			if err != nil {
				_ = listener.Close()
				fatal(err.Error())
			}
			_ = listener.Close()
			managedListenerFile = file
			*listenFD = int(file.Fd())
			defer file.Close()
		}
		leases, err := nativeAcquireMutableLeases()
		if err != nil {
			fatal(err.Error())
		}
		defer func() {
			for _, lease := range leases {
				releaseInstanceRootLease(lease)
			}
		}()
		// Root defaults must be selected after applying the captured instance plan.
		*configDir = productenv.Value("CONFIG_DIR")
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
	stateDir, err := defaultStateDir()
	if err != nil {
		fatal("YOmux state directory setup failed: " + err.Error())
	}
	productRootPath := productenv.Value("ROOT")
	if productRootPath == "" {
		productRootPath = rootPath
	}
	productRootPath, err = filepath.Abs(productRootPath)
	if err != nil {
		fatal("invalid product root: " + err.Error())
	}
	pricing, pricingErr := stats.LoadRepositoryPricingCatalog(rootPath, productenv.Value("PRICING_CATALOG"))
	if pricingErr != nil {
		fatal("pricing catalog setup failed: " + pricingErr.Error())
	}
	rootLease, err := acquireInstanceRootLease(productRootPath)
	if err != nil {
		fatal("YOmux product root is already owned by another server or unavailable: " + err.Error())
	}
	configLease, err := acquireConfigLease(*configDir)
	if err != nil {
		releaseInstanceRootLease(rootLease)
		fatal("Go config/journal storage is already owned or unavailable: " + err.Error())
	}
	// Hold this descriptor until process exit, including fatal startup and shutdown.
	// shutdown() precedes HTTP drainage and deferred collector/health writes;
	// releasing here would let a replacement process race those final mutations.
	defer func() { runtime.KeepAlive(configLease) }()
	eventPath := filepath.Join(*configDir, "go-events.jsonl")
	s := &server{root: rootPath, host: *host, port: *port, dangerouslyYolo: *dangerouslyYolo, config: *configDir, state: stateDir, httpMode: *httpMode, statsPath: *statsPath, pricing: pricing, children: make(map[*exec.Cmd]struct{}), processEntriesFn: processEntries, childProcessEntriesFn: descendantProcessEntries, startedAt: time.Now(), events: loadEventBroker(eventPath), eventLogPath: filepath.Join(*configDir, "event-log.jsonl"), logRing: newGoLogRing(), rootLease: rootLease, capabilities: capabilities.New(version), updates: newUpdateChecker(rootPath, version, nil), instanceID: fmt.Sprintf("%d-%d", os.Getpid(), time.Now().UnixNano()), launchIdentity: nativeLaunchIdentityEnvironment(), updateJobPath: filepath.Join(*configDir, "self-update-job.json"), finderJobs: newFinderJobScheduler(), finderBackgroundJobs: newFinderBackgroundJobScheduler(), finderBulkJobs: newFinderBulkJobScheduler(), finderStreamJobs: newFinderStreamScheduler(), operationState:
	// The Go server has no parent observer to retain lifecycle history. Keep the
	// port-scoped ledger in the configured state directory so normal operator
	// restarts expose measured restart/demand counters instead of zeros or an
	// unconditional unavailable envelope. A launch without an explicit reason
	// remains partial rather than being guessed as demand-driven.
	operationState{operationsPath: filepath.Join(*configDir, "go-operations.json"), operationsJournal: filepath.Join(*configDir, "go-operations.jsonl")}}

	pricingLease, err := s.initializePricingCache()
	if err != nil {
		fatal("pricing state is already owned or unavailable: " + err.Error())
	}
	defer func() { runtime.KeepAlive(pricingLease) }()

	healthEpoch := fmt.Sprintf("pid:%d:start:%d", os.Getpid(), s.startedAt.UnixNano())
	healthHistoryPath := filepath.Join(stateDir, "go-backend-health", fmt.Sprintf("%d.json", *port))
	if lifecycle, lifecycleErr := newGoHealthLifecycle(healthHistoryPath, healthEpoch, os.Getpid(), s.startedAt, productenv.Value("GO_START_REASON")); lifecycleErr != nil {
		slog.Warn("Go backend-health history unavailable", "error", lifecycleErr)
	} else {
		s.healthLifecycle = lifecycle
	}
	shutdownSignalName := ""
	shutdownExitStatus := 0
	shutdownClean := false
	defer func() {
		s.markGoHealthShutdown(shutdownSignalName, shutdownExitStatus, shutdownClean, time.Now())
	}()
	// Stats routes are implemented as soon as the server starts. Materialization readiness is a
	// separate runtime state: capability discovery and the typed pending response must reach the
	// stats handlers while the first ring build is still in flight, or the browser cannot ever learn
	// the matrix it needs to start that build's read path.
	s.capabilities.Set("pane.stats.v1", capabilities.Entry{Available: true, Degraded: true, Reason: &capabilities.Reason{Code: "stats_pending", MessageKey: "capability.statsPending", MessageParams: map[string]any{}}})
	// Settings use the native schema and persistence owner shared by request and startup paths.
	s.capabilities.Set("core.settings.v1", capabilities.Entry{Available: true})
	s.updates.readPref = s.updateNotifyPreference
	s.capabilities.Set("core.updates.v1", capabilities.Entry{Available: true})
	finderService, finderErr := finder.NewWithLimits(rootPath, func(kind string, payload map[string]any) error {
		_, err := s.events.publish(kind, payload)
		if err == nil && kind == "fs_changed" && payload["reason"] != "watch-roots-replaced" {
			if paths, ok := payload["paths"].([]string); ok {
				s.queueFinderIndexRepairsForPaths(paths)
			}
		}
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
	s.indexRefreshWake = make(chan struct{}, 1)
	s.searchIndex = finder.NewSearchIndexWorkerClient(stateDir)
	s.searchIndex.OnComplete = s.publishFinderIndexCompletion
	s.finder.SetSearchIndexWorker(s.searchIndex)
	if err := s.loadFinderIndexSettings(); err != nil {
		slog.Warn("Finder index settings unavailable; indexed search is disabled until settings load", "error", err)
	}
	s.syncFinderWatchCapability()
	s.background = newGoBackgroundOwner(*configDir, rootPath, *port)
	s.background.setEventPublisher(func(kind string, payload map[string]any) error {
		_, err := s.events.publish(kind, payload)
		return err
	})
	hostPartitionID, hostPartitionErr := productHostPartitionID()
	if hostPartitionErr != nil {
		err = hostPartitionErr
	} else {
		yoagentDirectory := filepath.Join(stateDir, "hosts", hostPartitionID, "yoagent")
		s.yoagentState, err = yoagent.NewStore(yoagentDirectory)
		if err != nil {
			slog.Error("native YO!agent state unavailable", "error", err)
		} else {
			legacyStatePath := filepath.Join(*configDir, "state.json")
			if err = s.yoagentState.ImportLegacyJobs(context.Background(), legacyStatePath); err != nil {
				slog.Error("legacy YO!agent jobs could not be imported", "error", err)
			} else {
				s.yoagent, err = newNativeYOAgent(s.yoagentState, rootPath, *configDir, s.nativeYOAgentSettings, func(kind string, payload map[string]any) error {
					_, publishErr := s.events.publish(kind, payload)
					return publishErr
				})
				if err != nil {
					slog.Error("native YO!agent backend unavailable", "error", err)
				} else {
					s.agentActions = newNativeYOAgentActions(s)
				}
			}
		}
		chatDatabase := filepath.Join(stateDir, "hosts", hostPartitionID, "yochat.sqlite3")
		chatRuntime, chatErr := newNativeChat(context.Background(), rootPath, chatDatabase, s.chatRetentionDays, func(ctx context.Context, kind string, payload map[string]any) error {
			_, err := s.events.publish(kind, payload)
			return err
		})
		if chatErr != nil {
			err = chatErr
		} else {
			s.chat = chatRuntime
			if s.yoagent != nil {
				replies, replyErr := chat.NewAgentReplies(s.chat.service, s.chat.events, func(ctx context.Context, request chat.AgentRequest) (string, error) {
					sessions, rosterErr := s.tmuxSessionRoster()
					if rosterErr != nil {
						slog.Warn("YO!chat agent session inventory unavailable", "error", rosterErr)
						sessions = nil
					}
					answer, _, answerErr := s.yoagent.AnswerWithSessions(ctx, request.Message, request.Locale, request.RequestID, request.RequestID, request.AccessRole, sessions)
					return answer, answerErr
				})
				if replyErr != nil {
					slog.Error("native chat agent bridge unavailable", "error", replyErr)
				} else {
					s.chat.replies = replies
				}
			}
			err = nil
		}
	}
	if hostPartitionErr != nil || err != nil {
		slog.Error("native chat storage unavailable; chat capability remains disabled", "error", err)
	}
	s.yolo, err = newGoYoloStore(*configDir)
	if err != nil {
		s.shutdown()
		fatal("YO auto-approval state setup failed: " + err.Error())
	}
	s.capabilities.Set("yolo.auto-approve.v1", capabilities.Entry{Available: true})
	s.restoreGoYoloWorkers(context.Background())
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
	mux.Handle("/api/ping", s.protected(http.HandlerFunc(s.ping)))
	mux.Handle("/api/update-status", s.protected(http.HandlerFunc(s.updateStatus)))
	mux.Handle("/api/self-update", s.protected(http.HandlerFunc(s.selfUpdate)))
	mux.Handle("/api/self-update/status", s.protected(http.HandlerFunc(s.selfUpdateStatus)))
	mux.Handle("/api/capabilities", s.protected(http.HandlerFunc(s.capabilitySnapshot)))
	mux.Handle("/api/client-events", s.protected(http.HandlerFunc(s.clientEvents)))
	mux.Handle("/api/stats-agent-status", s.protected(http.HandlerFunc(s.statsAgentStatus)))
	mux.Handle("/api/stats-capabilities", s.protected(http.HandlerFunc(s.statsCapabilities)))
	mux.Handle("/api/stats-observations", s.protected(http.HandlerFunc(s.statsObservations)))
	mux.Handle("/api/stats-refill", s.protected(http.HandlerFunc(s.statsRefill)))
	mux.Handle("/api/stats-retry", s.protected(http.HandlerFunc(s.statsRetry)))
	mux.Handle("/api/stats-session-aliases", s.protected(http.HandlerFunc(s.statsSessionAliases)))
	mux.Handle("/api/stats-snapshot", s.protected(http.HandlerFunc(s.statsSnapshot)))
	mux.Handle("/api/stats-delta", s.protected(http.HandlerFunc(s.statsDelta)))
	mux.Handle("/api/stats-stream", s.protected(http.HandlerFunc(s.statsStream)))
	mux.Handle("/api/pricing-catalog", s.protected(http.HandlerFunc(s.pricingCatalog)))
	mux.Handle("/api/pricing-catalog/refresh", s.protected(http.HandlerFunc(s.pricingCatalogRefresh)))
	mux.Handle("/readyz", s.protected(http.HandlerFunc(s.ready)))
	mux.Handle("/api/operations/ack", s.protected(http.HandlerFunc(s.operationAck)))
	mux.Handle("/api/operations/cancel", s.protected(http.HandlerFunc(s.operationCancel)))
	mux.Handle("/api/background/status", s.protected(http.HandlerFunc(s.backgroundStatus)))
	mux.Handle("/api/diagnostics/heap", s.protected(http.HandlerFunc(s.runtimeHeapProfile)))
	mux.Handle("/api/diagnostics/performance", s.protected(http.HandlerFunc(s.diagnosticsPerformance)))
	mux.Handle("/api/settings", s.protected(http.HandlerFunc(s.settings)))
	mux.Handle("/api/agent-auth", s.protected(http.HandlerFunc(s.agentAuthStatus)))
	mux.Handle("/api/yoagent/skills", s.protected(http.HandlerFunc(s.yoagentSkills)))
	mux.Handle("/api/yoagent/skill-files", s.protected(http.HandlerFunc(s.yoagentSkillFiles)))
	mux.Handle("/api/yoagent/skill-files/upsert", s.protected(http.HandlerFunc(s.yoagentSkillFileUpsert)))
	mux.Handle("/api/yoagent/skill-files/delete", s.protected(http.HandlerFunc(s.yoagentSkillFileDelete)))
	mux.Handle("/api/yoagent/jobs", s.protected(http.HandlerFunc(s.yoagentJobs)))
	mux.Handle("/api/yoagent/jobs/", s.protected(http.HandlerFunc(s.yoagentJobMutation)))
	mux.Handle("/api/yoagent/conversation", s.protected(http.HandlerFunc(s.yoagentConversation)))
	mux.Handle("/api/yoagent/chat", s.protected(http.HandlerFunc(s.yoagentChat)))
	mux.Handle("/api/yoagent/chat/", s.protected(http.HandlerFunc(s.yoagentChatCancel)))
	mux.Handle("/api/yoagent/actions/preview-send", s.protected(http.HandlerFunc(s.yoagentActionPreview)))
	mux.Handle("/api/yoagent/actions/execute-send", s.protected(http.HandlerFunc(s.yoagentActionExecute)))
	mux.Handle("/api/yoagent/waits/", s.protected(http.HandlerFunc(s.yoagentWaitClear)))
	mux.Handle("/api/yoagent/prewarm", s.protected(http.HandlerFunc(s.yoagentPrewarm)))
	mux.Handle("/api/yoagent/reset", s.protected(http.HandlerFunc(s.yoagentReset)))
	// The browser requests session metadata during boot, before it opens the
	// stats surface. Keep this route on the Go mux so a stats-capable process
	// does not turn a boot identity probe into the generic unsupported response.
	mux.Handle("/api/session-metadata", s.protected(http.HandlerFunc(s.sessionMetadata)))
	mux.Handle("/api/activity", s.protected(http.HandlerFunc(s.activity)))
	mux.Handle("/api/activity/touch", s.protected(http.HandlerFunc(s.activityTouch)))
	mux.Handle("/api/auto-approve", s.protected(http.HandlerFunc(s.autoApprove)))
	mux.Handle("/api/yolo-rules", s.protected(http.HandlerFunc(s.yoloRules)))
	mux.Handle("/api/yolo-rules/open", s.protected(http.HandlerFunc(s.yoloRulesOpen)))
	mux.Handle("/api/yolo-rules/reload", s.protected(http.HandlerFunc(s.yoloRulesReload)))
	s.registerChatRoutes(mux)
	s.registerAttentionAckRoute(mux)
	mux.Handle("/api/background/claim", s.protected(http.HandlerFunc(s.backgroundClaim)))
	mux.Handle("/api/operations/accept", s.protected(http.HandlerFunc(s.operationAccept)))
	mux.Handle("/api/operations/terminalize", s.protected(http.HandlerFunc(s.operationTerminalize)))
	mux.Handle("/api/system-status", s.protected(http.HandlerFunc(s.systemStatus)))
	mux.Handle("/api/system-status/advanced", s.protected(http.HandlerFunc(s.systemStatus)))
	mux.Handle("/api/logs", s.protected(http.HandlerFunc(s.logs)))
	mux.Handle("/api/watch/roots", s.protected(http.HandlerFunc(s.watchRoots)))
	s.registerFilesystemRoutes(mux)
	mux.Handle("/api/event", s.protected(http.HandlerFunc(s.eventPost)))
	mux.Handle("/api/events", s.protected(http.HandlerFunc(s.eventsList)))
	mux.Handle("/api/transcripts", s.protected(http.HandlerFunc(s.sessionMetadata)))
	mux.Handle("/api/transcript", s.protected(http.HandlerFunc(s.transcript)))
	mux.Handle("/api/context", s.protected(http.HandlerFunc(s.context)))
	mux.Handle("/api/context-items", s.protected(http.HandlerFunc(s.contextItems)))
	mux.Handle("/api/context-stream", s.protected(http.HandlerFunc(s.contextStream)))
	mux.Handle("/api/tmux-status", s.protected(http.HandlerFunc(s.tmuxStatus)))
	mux.Handle("/api/tmux-signals", s.protected(http.HandlerFunc(s.tmuxSignals)))
	mux.Handle("/api/tmux", s.protected(http.HandlerFunc(s.tmux)))
	mux.Handle("/api/tmux-window", s.protected(http.HandlerFunc(s.tmuxWindow)))
	mux.Handle("/api/tmux-session-exists", s.protected(http.HandlerFunc(s.sessionExists)))
	s.registerSessionRoutes(mux)
	mux.Handle("/api/operations/", s.protected(http.HandlerFunc(s.operation)))
	// Keep stale clients on a typed capability error instead of an unexplained 404. More specific
	// handlers above win under ServeMux's longest-pattern rule.
	mux.Handle("/api/", s.protected(http.HandlerFunc(s.unsupported)))
	mux.Handle("/", s.protected(http.HandlerFunc(s.index)))
	mux.HandleFunc("/ws", s.websocket)
	mux.HandleFunc("/static/", s.static)
	addr := fmt.Sprintf("%s:%d", *host, *port)
	httpServer := &http.Server{Addr: addr, Handler: s.logging(mux), ReadHeaderTimeout: 10 * time.Second, ReadTimeout: 30 * time.Second, IdleTimeout: 120 * time.Second}
	var listener net.Listener
	if *listenFD >= 0 {
		listenerFile := managedListenerFile
		if listenerFile == nil {
			listenerFile = os.NewFile(uintptr(*listenFD), "yolomux-listener")
		}
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
		s.tlsCertificate, s.tlsKey = certFile, keyFile
		config, err := tlsConfig(certFile, keyFile)
		if err != nil {
			_ = listener.Close()
			s.shutdown()
			fatal("TLS setup failed: " + err.Error())
		}
		listener = tls.NewListener(listener, config)
	}
	shutdownSignal := make(chan os.Signal, 1)
	signal.Notify(shutdownSignal, syscall.SIGINT, syscall.SIGTERM)
	defer signal.Stop(shutdownSignal)
	shutdownContext, stopSignals := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	go s.runUpdateChecker(shutdownContext)
	defer stopSignals()
	s.serverCancel = stopSignals
	if s.chat != nil {
		s.capabilities.Set("pane.chat.v1", capabilities.Entry{Available: true})
	}
	if s.yoagent != nil && s.agentActions != nil {
		s.capabilities.Set("pane.agent.v1", capabilities.Entry{Available: true})
	}
	if s.chat != nil && !s.startBackgroundWork(func() {
		if err := s.chat.events.Run(shutdownContext, 250*time.Millisecond, func(err error) {
			slog.Error("native chat event publication failed", "error", err)
		}); err != nil {
			slog.Error("native chat event loop stopped", "error", err)
		}
	}) {
		slog.Error("native chat event loop was not admitted")
	}
	s.startBackgroundWork(func() { s.reconcileFinderIndexes(shutdownContext) })
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
	defer func() {
		if err := statsWriter.Close(); err != nil {
			shutdownClean = false
			slog.Warn("stats writer did not close cleanly", "error", err)
		}
	}()
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
	s.refillContext = shutdownContext
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
	s.startPricingLifecycle()
	collectorsDisabled := productenv.Value("STATS_DISABLE_COLLECTORS") == "1"
	s.syntheticHostInputs = productenv.Value("STATS_SYNTHETIC_HOST") == "1"
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
	if productenv.Value("STATS_SYNTHETIC_INVENTORY") == "1" {
		statusSources = stats.SyntheticAgentStatusSources(opencodeDatabase)
	}
	s.agentProcessSnapshot = stats.CurrentPlatformAgentStatusProcess
	s.agentStatusClassifier = func(ctx context.Context, request stats.AgentStatusRequest) stats.AgentStatusResult {
		return stats.ClassifyAgentStatus(ctx, request, statusSources)
	}
	if !collectorsDisabled {
		agentInventorySources := stats.CurrentPlatformAgentInventorySources()
		if productenv.Value("STATS_SYNTHETIC_INVENTORY") == "1" {
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
			AttentionAcknowledged: func(key string) (bool, error) {
				_, acknowledged, err := s.attentionAckStore().acknowledged(key, s.attentionAckNow())
				return acknowledged, err
			},
			AttentionCommitted: s.attentionSettlementsCommitted,
		})
		if _, statErr := os.Stat(s.statsPath); statErr == nil {
			lifecycles, restoreErr := stats.ReadRecentAgentStatusLifecycles(shutdownContext, s.statsPath, time.Now())
			if restoreErr != nil {
				slog.Warn("could not restore recent agent status lifecycles", "error", restoreErr)
			} else {
				statusCollector.RestoreRecentLifecycles(lifecycles)
			}
		} else if !errors.Is(statErr, os.ErrNotExist) {
			slog.Warn("could not inspect persisted agent status transitions", "error", statErr)
		}
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
		if productenv.Value("STATS_SYNTHETIC_HOST") == "1" {
			// Development and integration fixtures must not inspect the host's
			// /proc, process table, memory, or GPU state. The explicit opt-in
			// source is deterministic and carries a same-named co-tenant so the
			// host/owned CPU split remains exercised without host input.
			hostSources = stats.NewSyntheticHostSources(processIdentityForRuntime(), processKeyForRuntime())
		}
		hostCollector := stats.NewHostCollector(hostSources, statsWriter, nil, stats.HostCollectorConfig{
			ActiveCadence: stats.DefaultHostCPUBackgroundCadence, IdleCadence: stats.DefaultHostCPUBackgroundCadence, MemoryCadence: stats.DefaultHostMemoryCadence, DiskCadence: stats.DefaultHostDiskCadence, SourceTimeout: 900 * time.Millisecond,
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
		defer func() {
			if err := recorder.Stop(); err != nil {
				shutdownClean = false
			}
		}()
	}
	if s.yoagent != nil && !s.startBackgroundWork(func() { s.runNativeYOAgentJobs(shutdownContext) }) {
		slog.Error("native YO!agent job loop was not admitted")
	}
	scheme := "https"
	if *httpMode {
		scheme = "http"
	}
	slog.Info("YOmux Go backend", "version", version, "backend", "go", "addr", addr, "scheme", scheme)
	s.recoverSelfUpdateJob()
	_, _ = s.events.publish("update_available", map[string]any{"available": false, "version": version, "bundle_revision": "go:" + version, "instance_id": s.instanceID})
	serveErr := make(chan error, 1)
	go func() { serveErr <- httpServer.Serve(listener) }()
	select {
	case <-shutdownContext.Done():
		select {
		case received := <-shutdownSignal:
			shutdownSignalName = received.String()
		default:
		}
		s.stopRequestAdmission()
		gracefulContext, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		shutdownErr := httpServer.Shutdown(gracefulContext)
		if shutdownErr != nil {
			_ = httpServer.Close()
		}
		cancel()
		s.shutdown()
		shutdownClean = shutdownErr == nil && s.shutdownCompletedCleanly() && shutdownSignalName != ""
	case err := <-serveErr:
		// Serve can exit while accepted connections still own blocked response writes.
		_ = httpServer.Close()
		if err != nil && err != http.ErrServerClosed {
			s.shutdown()
			fatal(err.Error())
		}
		shutdownClean = false
		s.shutdown()
	}
}

func fatal(message string) { slog.Error(message); os.Exit(1) }

func normalizeYomuxEnvironment() bool {
	legacyUsed := productenv.Normalize()
	if legacyUsed {
		slog.Warn(productenv.DeprecationWarning())
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

func defaultConfigDir() string {
	if configured := productenv.Value("CONFIG_DIR"); configured != "" {
		return configured
	}
	if root := productenv.Value("ROOT"); root != "" {
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
	status                int
	bytes                 int
	representationReadyAt time.Time
}

// Preserve transport controls through the accounting wrapper without bypassing writes.
func (w *loggingResponseWriter) Unwrap() http.ResponseWriter { return w.ResponseWriter }

func (w *loggingResponseWriter) WriteHeader(status int) {
	if w.status != 0 {
		return
	}
	w.representationReadyAt = time.Now()
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

func (w *loggingResponseWriter) Flush() { _ = w.FlushError() }
func (w *loggingResponseWriter) FlushError() error {
	if w.status == 0 {
		w.WriteHeader(http.StatusOK)
	}
	return http.NewResponseController(w.ResponseWriter).Flush()
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
		finishRequest, admitted := s.beginRequest(r)
		if !admitted {
			http.Error(w, "server is shutting down", http.StatusServiceUnavailable)
			return
		}
		defer finishRequest()
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
		var routeToRepresentationReady *time.Duration
		if !logged.representationReadyAt.IsZero() {
			ready := logged.representationReadyAt.Sub(start)
			routeToRepresentationReady = &ready
		}
		s.recordPerformance(r, status, logged.bytes, elapsed, routeToRepresentationReady)
	})
}

func (s *server) recordPerformance(r *http.Request, status, payloadBytes int, elapsed time.Duration, routeToRepresentationReady *time.Duration) {
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
			"process_pid": os.Getpid(),
		},
	}
	if routeToRepresentationReady != nil {
		record.Details["route_to_representation_ready_ms"] = float64(routeToRepresentationReady.Microseconds()) / 1000
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
		"capture": map[string]any{"capacity": goPerformanceRecordLimit, "retained": len(recent), "total": captureTotal, "evicted": max(0, int(captureTotal)-len(recent)), "first_sequence": max(1, captureTotal-uint64(len(recent))+1), "last_sequence": captureTotal},
	}
	jsonResponse(w, http.StatusOK, map[string]any{"perf": perf, "transport": map[string]any{"teardowns_total": 0, "teardowns_by_exception": map[string]any{}}, "shared_phase_counters": []any{}, "repeated_work": []any{}, "browser_profiles": map[string]any{}, "browser_observation_status": map[string]any{}})
}

func (s *server) health(w http.ResponseWriter, _ *http.Request) {
	payload := s.nativeServerIdentity()
	payload["ok"] = true
	if len(s.users()) > 0 {
		delete(payload, "root")
		delete(payload, "launch_environment")
	}
	jsonResponse(w, http.StatusOK, payload)
}

func (s *server) ping(w http.ResponseWriter, _ *http.Request) {
	payload := s.nativeServerIdentity()
	payload["ok"] = true
	payload["capabilities"] = s.capabilityEntries()
	jsonResponse(w, http.StatusOK, payload)
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
	payload, finderErr := s.finder.FastListNameQuery(r.Context(), path, r.URL.Query().Get("name_query"))
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
	stream, ok := newJSONSSEWriter(w, r.Context())
	if !ok {
		finderHTTPJSON(w, finderRequestIDForHTTP(), nil, &finder.Error{Status: http.StatusInternalServerError, Code: "streaming_unavailable", MessageKey: "common.requestFailed", Path: path, Message: "streaming unavailable"})
		return
	}
	defer stream.close()
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
			if err := ctx.Err(); err != nil {
				return err
			}
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
	if r.Context().Err() != nil {
		return
	}
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
	s.queueFinderIndexRepairs(product.data)
	finderHTTPJSON(w, finderRequestIDForHTTP(), product.data, nil)
}

func (s *server) publishFinderIndexCompletion(status map[string]any) {
	root, _ := status["root"].(string)
	if root == "" || s.events == nil {
		return
	}
	scopeDigest := sha256.Sum256([]byte(root))
	_, _ = s.events.publish("search_progress", map[string]any{
		"scope_id": hex.EncodeToString(scopeDigest[:])[:16], "generation": status["generation"],
		"worker_incarnation": status["worker_incarnation"], "index_state": status["index_state"],
		"stale": status["stale"], "revision": status["generation"], "coverage": status["progressive_coverage"],
	})
}

func (s *server) queueFinderIndexRepairs(payload map[string]any) {
	if s.finder == nil || s.searchIndex == nil || payload["mode"] == "full" {
		return
	}
	paths := make([]string, 0)
	for _, field := range []string{"directories", "files"} {
		items, _ := payload[field].([]any)
		for _, item := range items {
			row, _ := item.(map[string]any)
			if path, ok := row["path"].(string); ok && path != "" {
				paths = append(paths, path)
			}
		}
	}
	removed, _ := payload["removed_paths"].([]any)
	for _, item := range removed {
		if path, ok := item.(string); ok && path != "" {
			paths = append(paths, path)
		}
	}
	if len(paths) == 0 {
		return
	}
	s.queueFinderIndexRepairsForPaths(paths)
}

func (s *server) queueFinderIndexRepairsForPaths(paths []string) {
	if s.finder == nil || s.searchIndex == nil || len(paths) == 0 {
		return
	}
	s.indexSettingsMu.RLock()
	roots := append([]string(nil), s.indexSettings.roots...)
	s.indexSettingsMu.RUnlock()
	for _, root := range roots {
		options := s.finderSearchOptions(root, "", false)
		if !options.Indexed {
			continue
		}
		changedPaths := make([]string, 0, len(paths))
		for _, path := range paths {
			relative, relativeErr := filepath.Rel(root, path)
			ancestor, ancestorErr := filepath.Rel(path, root)
			if relativeErr == nil && (relative == "." || (relative != ".." && !strings.HasPrefix(relative, ".."+string(filepath.Separator)))) || ancestorErr == nil && (ancestor == "." || (ancestor != ".." && !strings.HasPrefix(ancestor, ".."+string(filepath.Separator)))) {
				changedPaths = append(changedPaths, path)
			}
		}
		if len(changedPaths) > 0 {
			options.ChangedPaths = changedPaths
			options.ForceRefresh = true
			s.finder.QueueIndexRepair(root, options, nil)
		}
	}
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
	searchOptions := s.finderSearchOptions(root, cursor, indexedOnly)
	if r.URL.Path == "/api/fs/search-stream" {
		stream, ok := newJSONSSEWriter(w, r.Context())
		if !ok {
			jsonResponse(w, http.StatusInternalServerError, map[string]any{"error": "streaming unavailable", "error_code": "streaming_unavailable"})
			return
		}
		defer stream.close()
		writeFinderSSE := stream.write
		if err := writeFinderSSE("start", map[string]any{"root": root, "query": r.URL.Query().Get("query"), "limit": limit}); err != nil {
			return
		}
		product, finderErr := s.runFinderStreamProduct(r.Context(), func(ctx context.Context) (finderProduct, *finder.Error) {
			payload, err := s.finder.SearchStreamWithOptions(ctx, root, r.URL.Query().Get("query"), limit, recursive, queryTrue(r.URL.Query().Get("direct_only")), queryTrue(r.URL.Query().Get("minimal")), searchOptions, func(files []map[string]any, coverage map[string]any) error {
				if err := ctx.Err(); err != nil {
					return err
				}
				return writeFinderSSE("chunk", map[string]any{"files": files, "coverage": coverage})
			})
			return finderProduct{data: payload}, err
		})
		if r.Context().Err() != nil {
			return
		}
		if finderErr != nil {
			if r.Context().Err() == nil {
				_ = writeFinderSSE("error", finderErr.Payload())
			}
			return
		}
		_ = writeFinderSSE("done", map[string]any{"files": product.data["count"], "coverage": product.data["progressive_coverage"], "summary": product.data, "terminal": true})
		return
	}
	if recursive && !directOnly && cursor == "" && !indexedOnly {
		s.enqueueFinderJSON(w, r.Context(), r.URL.Path, "search", root, func(ctx context.Context) (map[string]any, *finder.Error) {
			payload, err := s.finder.SearchWithOptions(ctx, root, r.URL.Query().Get("query"), limit, recursive, false, queryTrue(r.URL.Query().Get("minimal")), searchOptions)
			return payload, err
		})
		return
	}
	if batchSearch {
		s.enqueueFinderJSON(w, r.Context(), r.URL.Path, "search", root, func(ctx context.Context) (map[string]any, *finder.Error) {
			payload, err := s.finder.SearchWithOptions(ctx, root, r.URL.Query().Get("query"), limit, recursive, directOnly, queryTrue(r.URL.Query().Get("minimal")), searchOptions)
			return payload, err
		})
		return
	}
	if cursor != "" {
		s.enqueueFinderJSON(w, r.Context(), r.URL.Path, "search", root, func(ctx context.Context) (map[string]any, *finder.Error) {
			payload, err := s.finder.SearchWithOptions(ctx, root, r.URL.Query().Get("query"), limit, recursive, directOnly, queryTrue(r.URL.Query().Get("minimal")), searchOptions)
			return payload, err
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

func (s *server) ensureFinderIndex(ctx context.Context, root string, options finder.SearchOptions) (map[string]any, *finder.Error) {
	payload, finderErr := s.finder.EnsureIndex(ctx, root, options)
	return payload, finderErr
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
	if payload := product.data; payload != nil {
		if refreshRoot, err := filepath.Abs(root); err == nil {
			payload["refresh_schedule"] = s.indexRefreshDiagnostics(filepath.Clean(refreshRoot))
		}
		for key, value := range s.finderIndexSettingsStatus() {
			payload[key] = value
		}
		options := s.finderSearchOptions(root, "", false)
		if options.Indexed {
			payload["max_files"] = options.MaxFiles
			payload["max_bytes"] = options.MaxBytes
		}
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
	user, status := s.identityStatus(r)
	if status != 0 {
		if !rejectCredentialAdmission(w, status) {
			s.rejectUnauthorized(w, r)
		}
		return
	}
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
	for _, name := range []string{"FILE_TRANSFER_MAX_BYTES", "UPLOAD_MAX_BYTES"} {
		if value, present := productenv.Lookup(name); present {
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
		trimmed := strings.TrimSpace(stripYAMLComment(line))
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
	if value, present := productenv.Lookup("UPLOAD_MAX_FILES"); present {
		if parsed, parseErr := strconv.Atoi(strings.TrimSpace(value)); parseErr == nil && parsed > 0 {
			limit = parsed
		}
	}
	return limit
}

func (s *server) fileTransferFilenameTemplate() string {
	if value, present := productenv.Lookup("UPLOAD_FILENAME_TEMPLATE"); present {
		return strings.TrimSpace(value)
	}
	data, err := os.ReadFile(filepath.Join(s.config, "settings.yaml"))
	if err != nil {
		return ""
	}
	inUploads := false
	for _, line := range strings.Split(string(data), "\n") {
		trimmed := strings.TrimSpace(stripYAMLComment(line))
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

func (s *server) clientEvents(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	sseWriter, ok := newJSONSSEWriter(w, r.Context())
	if !ok {
		http.Error(w, "streaming unavailable", http.StatusInternalServerError)
		return
	}
	defer sseWriter.close()
	writeSSE := func(event string, payload any) {
		_ = sseWriter.write(event, payload)
	}
	channels := normalizeEventChannels(r.URL.Query().Get("channels"))
	clientID := normalizeClientID(r.URL.Query().Get("client_id"))
	stream, unsubscribe := s.events.subscribeClient(clientID, channels...)
	defer unsubscribe()
	if clientID != "" {
		s.clientEventMu.Lock()
		if s.clientEventConnections == nil {
			s.clientEventConnections = make(map[string]int)
		}
		s.clientEventConnections[clientID]++
		s.clientEventMu.Unlock()
		defer func() {
			s.clientEventMu.Lock()
			connections := s.clientEventConnections[clientID] - 1
			if connections <= 0 {
				delete(s.clientEventConnections, clientID)
				if s.finder != nil {
					// Keep reconnect admission serialized with lease release. Otherwise a
					// new stream can renew this client between the count reaching zero
					// and this old stream removing that renewed lease.
					s.finder.ReleaseWatchClient(clientID)
				}
			} else {
				s.clientEventConnections[clientID] = connections
			}
			s.clientEventMu.Unlock()
		}()
	}
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
	if value, err := strconv.Atoi(strings.TrimSpace(productenv.Value("GO_EVENT_STREAM_DELAY_MS"))); err == nil && value > 0 {
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
		case <-sseWriter.done:
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
			retainOperationRecords(s.operations)
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
	jsonResponse(w, http.StatusOK, map[string]any{"events": s.eventLogTail(session, limit), "session": session, "limit": min(limit, 1000)})
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
	threads := make([]goThreadMetric, 0, min(len(tasks), maxGoThreads))
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
	serverBlock := s.nativeServerIdentity()
	if s.processMetricsFn != nil {
		// Preserve injected metrics in status fixtures; launch authority uses ping.
		serverBlock["pid"] = process.PID
	}
	serverBlock["started_at"] = unixSeconds(process.StartedAt)
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
			values := make([]any, 0, min(len(item), 20))
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
	session := strings.TrimSpace(r.URL.Query().Get("session"))
	var sessions []string
	var topology goTmuxTopology
	var rosterErr error
	if session == "" {
		topology, rosterErr = s.tmuxTopologySnapshot()
		sessions = topology.sessions
	} else {
		sessions, rosterErr = s.tmuxSessionRoster()
	}
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
		if session == "" {
			topology, _ = s.cachedTmuxTopologySnapshot()
			sessions = topology.sessions
		}
	}
	// With no session query this is the small, machine-wide roster used by old
	// clients.  Include the explicit order/scope keys so it is also a valid
	// topology response for Tabber: one live tmux session owns one top-level tab.
	if session == "" {
		payload := map[string]any{
			"sessions": sessions, "session_order": sessions, "session_scope": "all", "backend": "go",
			"topology_generation": topology.generation, "renames": topology.renames, "errors": []string{},
		}
		if rosterErr != nil {
			payload["errors"] = []string{rosterErr.Error()}
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
	published, publishedGeneration, stale, _ := s.metadataSnapshotWithBuiltAt()
	metadataSessions := metadataSessionsForTopology(published, publishedGeneration, topology)
	metadataStale := published == nil || stale || metadataPayloadTopologyGeneration(published) != topology.generation
	if metadataStale {
		s.queueMetadataRefresh(false, topology.sessions, topology.identities, rosterErr, topology.generation)
	}
	activity := make(map[string]any)
	activityErrors := make([]string, 0)
	agentStatusSnapshot, statusErr := s.currentAgentStatusSnapshot(r.Context())
	if statusErr != nil {
		activityErrors = append(activityErrors, statusErr.Error())
	}
	projection, projectionErr := s.projectAgentWindows(metadataSessions, topology.generation, metadataStale, agentStatusSnapshot)
	if err := projectionErr; err != nil {
		activityErrors = append(activityErrors, err.Error())
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
				// Keep the tmux output clock separate from accepted input,
				// using the existing activity fields consumed by Tabber.
				"last_output_ts": timestamp,
			}
		}
	} else {
		activityErrors = append(activityErrors, err.Error())
	}
	s.mergeTerminalInputActivity(activity, sessions)
	s.mergeTabberActivity(activity)
	jsonResponse(w, http.StatusOK, map[string]any{
		"activity": activity, "agents": projection.agents, "agent_windows": projection.windows,
		// The activity response is also a topology repair path. Python's cached
		// activity product normally follows session-metadata, but a Go-only boot
		// can receive activity first; carrying the same pane shape lets the client
		// render one Tabber session row even when metadata is still unavailable.
		"sessions": metadataSessions,
		"errors":   activityErrors, "session_order": sessions, "session_scope": scope,
		"topology_generation": topology.generation, "renames": topology.renames,
		"agent_window_snapshot_revision": projection.revision,
		"agent_window_status_fresh":      projection.fresh,
		"session_file_hours":             hours,
		"cache": map[string]any{
			"hit": published != nil, "stale": metadataStale,
			"generation": publishedGeneration, "refreshing": s.metadataRefreshInFlight(),
		},
		"backend": "go",
	})
}

func (s *server) activityTouch(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	var body struct {
		Item string `json:"item"`
	}
	if err := decodeJSONBody(r.Body, 8*1024, &body); err != nil {
		jsonResponse(w, http.StatusBadRequest, map[string]any{"error": "invalid activity item"})
		return
	}
	item := strings.TrimSpace(body.Item)
	validPrefix := strings.HasPrefix(item, "file:") || strings.HasPrefix(item, "gitdiff:")
	if !validPrefix || len(item) <= strings.IndexByte(item, ':')+1 || len(item) > 4096 || strings.IndexFunc(item, func(r rune) bool { return r < 0x20 || r == 0x7f }) >= 0 {
		jsonResponse(w, http.StatusBadRequest, map[string]any{"error": "invalid activity item"})
		return
	}
	s.recordTabberActivity(item)
	jsonResponse(w, http.StatusOK, map[string]any{"ok": true})
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
	// The reservation, mutation, and confirming roster share the same owner as
	// rename. A pre-create read must not remain a fresh post-create cache hit.
	s.tmuxRosterFetchMu.Lock()
	defer s.tmuxRosterFetchMu.Unlock()
	roster, err := s.tmuxSessionRosterSnapshotLocked(true)
	if err != nil {
		jsonResponse(w, http.StatusServiceUnavailable, map[string]any{"ok": false, "error": "tmux session roster is unavailable"})
		return
	}
	if len(roster.sessions) >= maxYomuxSessionTabs {
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
			"ok": false, "error": "create-session lifecycle reservation no longer matches the next available name", "reason_code": "create_session_plan_stale",
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
				"ok": false, "error": "create-session lifecycle reservation no longer matches the next available name", "reason_code": "create_session_plan_stale",
			})
			return
		}
	}
	cwd := s.root
	if cwd == "" {
		cwd = "."
	}
	runtimePath, err := nativeRuntimePath()
	if err != nil {
		slog.Error("session launch environment unavailable", "error", err)
		jsonResponse(w, http.StatusServiceUnavailable, map[string]any{"ok": false, "error": "session launch environment unavailable"})
		return
	}
	// tmux can publish a session PATH while its spawn path still uses the
	// existing server PATH. Set the actual process environment at exec too.
	launchScript := "PATH=" + shellQuote(runtimePath) + " exec " + command
	output, err := s.tmuxCommand("new-session", "-d", "-s", name, "-e", "TERM=xterm-256color", "-e", "PATH="+runtimePath, "-c", cwd, launchScript).CombinedOutput()
	if err != nil {
		diagnostic := strings.TrimSpace(string(output))
		if diagnostic == "" {
			diagnostic = err.Error()
		}
		jsonResponse(w, http.StatusBadRequest, map[string]any{"ok": false, "error": diagnostic})
		return
	}
	s.configureTmuxSession(name)
	topology := s.refreshTmuxMetadataAfterMutationLocked()
	jsonResponse(w, http.StatusOK, map[string]any{
		"ok": true, "created": true, "session": name, "sessions": topology.sessions, "agent": agent,
		"topology_generation": topology.generation,
		"terminal":            terminal, "cwd": cwd, "command": goAgentCommand(agent, terminal, launchDangerously), "dangerously_yolo": launchDangerously,
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
	name := agent
	switch agent {
	case "claude", "codex", "opencode":
	case "term":
		if !validSessionLaunchName(terminal) {
			return "", errors.New("choose an explicit terminal command")
		}
		name = terminal
	default:
		return "", fmt.Errorf("unsupported agent: %s", agent)
	}
	executable, err := nativeCommandPath(name)
	if err != nil {
		return "", fmt.Errorf("%s is not available on this server PATH: %w", name, err)
	}
	return formatGoAgentCommand(agent, shellQuote(executable), dangerouslyYolo), nil
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
	if len(existing) >= maxYomuxSessionTabs {
		return ""
	}
	present := make(map[string]struct{}, len(existing))
	for _, session := range existing {
		present[session] = struct{}{}
	}
	for number := 1; number <= maxYomuxSessionTabs; number++ {
		name := strconv.Itoa(number)
		if _, exists := present[name]; !exists {
			return name
		}
	}
	return ""
}

// Settings HTTP and startup reconciliation share native persistence and validation.
func (s *server) settings(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet && r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	var patch any
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
		patch = payload
		if value, exists := payload["settings"]; exists {
			patch = value
		}
	}

	ctx, cancel := context.WithTimeout(r.Context(), 30*time.Second)
	defer cancel()
	if err := s.indexSettingsUpdateMu.LockContext(ctx, nil); err != nil {
		jsonResponse(w, http.StatusServiceUnavailable, typedError("settings unavailable", "common.requestFailed", true))
		return
	}
	defer s.indexSettingsUpdateMu.Unlock()

	payload, err := s.nativeSettingsPayload(ctx, patch, r.Method == http.MethodPost)
	if err != nil {
		slog.Error("settings unavailable", "method", r.Method, "error", err)
		jsonResponse(w, http.StatusServiceUnavailable, typedError("settings unavailable", "common.requestFailed", true))
		return
	}
	if errorMessage, ok := payload["error"].(string); ok && strings.TrimSpace(errorMessage) != "" {
		s.indexSettingsMu.Lock()
		s.indexSettingsError = errorMessage
		s.indexSettingsMu.Unlock()
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
	if err := s.applyFinderIndexSettings(ctx, payload); err != nil {
		payload["index_synchronization_error"] = err.Error()
	}
	payload["index_settings_status"] = s.finderIndexSettingsStatus()
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
	capability := capabilities.Policy(r.Method, r.URL.RequestURI()).Capability
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
	s.tmuxRosterFetchMu.Lock()
	defer s.tmuxRosterFetchMu.Unlock()
	err := s.tmuxCommand("kill-session", "-t", name).Run()
	if err != nil {
		jsonResponse(w, http.StatusBadRequest, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	topology := s.refreshTmuxMetadataAfterMutationLocked()
	jsonResponse(w, http.StatusOK, map[string]any{"ok": true, "session": name, "sessions": topology.sessions, "topology_generation": topology.generation})
}

func (s *server) killWindow(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	session := strings.TrimSpace(r.URL.Query().Get("session"))
	windowID := strings.TrimSpace(r.URL.Query().Get("window_id"))
	if session == "" || len(windowID) < 2 || windowID[0] != '@' {
		jsonResponse(w, http.StatusBadRequest, map[string]any{"error": "session and a valid window_id are required"})
		return
	}
	for _, character := range windowID[1:] {
		if character < '0' || character > '9' {
			jsonResponse(w, http.StatusBadRequest, map[string]any{"error": "window_id must use the tmux @<number> format"})
			return
		}
	}

	s.tmuxRosterFetchMu.Lock()
	defer s.tmuxRosterFetchMu.Unlock()
	commandContext, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	target := s.exactTarget(session)
	if err := s.tmuxCommandContext(commandContext, "has-session", "-t", target).Run(); err != nil {
		jsonResponse(w, http.StatusNotFound, map[string]any{"error": "unknown session", "session": session})
		return
	}
	output, err := s.tmuxCommandContext(commandContext, "list-windows", "-t", target, "-F", "#{window_id}").Output()
	if err != nil {
		jsonResponse(w, http.StatusInternalServerError, map[string]any{"error": "could not read tmux session windows", "session": session})
		return
	}
	windowIDs := strings.Fields(string(output))
	targetExists := false
	for _, currentID := range windowIDs {
		if currentID == windowID {
			targetExists = true
			break
		}
	}
	if !targetExists {
		jsonResponse(w, http.StatusNotFound, map[string]any{"error": "unknown tmux window", "session": session, "window_id": windowID})
		return
	}
	if len(windowIDs) < 2 {
		jsonResponse(w, http.StatusConflict, map[string]any{"error": "refusing to kill the last window in a tmux session", "session": session, "window_id": windowID})
		return
	}
	if err := s.tmuxCommandContext(
		commandContext,
		"if-shell", "-F", "-t", target, "#{>=:#{session_windows},2}", "kill-window -t "+windowID, "",
	).Run(); err != nil {
		jsonResponse(w, http.StatusBadRequest, map[string]any{"error": err.Error(), "session": session, "window_id": windowID})
		return
	}
	remainingOutput, err := s.tmuxCommandContext(commandContext, "list-windows", "-t", target, "-F", "#{window_id}").Output()
	if err != nil {
		jsonResponse(w, http.StatusNotFound, map[string]any{"error": "unknown session", "session": session})
		return
	}
	remaining := strings.Fields(string(remainingOutput))
	for _, currentID := range remaining {
		if currentID == windowID {
			jsonResponse(w, http.StatusConflict, map[string]any{"error": "tmux window could not be killed without removing the session", "session": session, "window_id": windowID})
			return
		}
	}
	topology := s.refreshTmuxMetadataAfterMutationLocked()
	jsonResponse(w, http.StatusOK, map[string]any{
		"ok": true, "session": session, "window_id": windowID,
		"windows_remaining": len(remaining),
		"sessions":          topology.sessions, "topology_generation": topology.generation,
	})
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
	if isTabberViewSessionName(newName) {
		writeRenameError(http.StatusBadRequest, "rename.error.invalidChars", "session name is reserved for internal window views", map[string]any{})
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
	topology, refreshErr := s.confirmTmuxTopologyAfterMutationLocked()
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
	s.queueMetadataRefresh(false, topology.sessions, topology.identities, refreshErr, topology.generation)
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
	user, status := s.identityStatus(r)
	if status != 0 {
		if !rejectCredentialAdmission(w, status) {
			s.rejectUnauthorized(w, r)
		}
		return
	}
	s.writeNativeHome(w, r, user)
}

func (s *server) writeNativeHome(w http.ResponseWriter, r *http.Request, user authUser) {
	topology, topologyErr := s.tmuxTopologySnapshot(true)
	if topologyErr != nil {
		http.Error(w, "session roster unavailable", http.StatusServiceUnavailable)
		return
	}
	body, err := s.renderNativeHome(r, user, topology)
	if err != nil {
		slog.Error("application page unavailable", "error", err)
		http.Error(w, "application unavailable", http.StatusServiceUnavailable)
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

func (s *server) tmuxCommand(args ...string) *exec.Cmd {
	return s.tmuxPointCommand(nil, args...)
}

// Point probes are bounded; the long-lived attach process uses tmuxCommandContext directly.
func (s *server) tmuxPointCommand(ctx context.Context, args ...string) *exec.Cmd {
	if ctx == nil {
		s.requestMu.Lock()
		if s.tmuxPointContext == nil {
			s.tmuxPointContext, s.tmuxPointCancel = context.WithCancel(context.Background())
			if s.requestClosed {
				s.tmuxPointCancel()
			}
		}
		ctx = s.tmuxPointContext
		s.requestMu.Unlock()
	}
	ctx, cancel := context.WithTimeout(ctx, 3*time.Second)
	command := s.tmuxCommandContext(ctx, args...)
	if command.Cancel != nil {
		interrupt := command.Cancel
		command.Cancel = func() error { defer cancel(); return interrupt() }
	}
	return command
}

func (s *server) tmuxCommandContext(ctx context.Context, args ...string) *exec.Cmd {
	if s.tmuxCommandContextFn != nil {
		return s.tmuxCommandContextFn(ctx, args...)
	}
	return tmuxcmd.Command(ctx, tmuxcmd.Args(args...)[1:]...)
}

func (s *server) exactTarget(session string) string { return "=" + session + ":" }

func (s *server) supportsIgnoreSize(contexts ...context.Context) bool {
	var ctx context.Context
	if len(contexts) > 0 {
		ctx = contexts[0]
	}
	if ctx != nil && ctx.Err() != nil {
		return false
	}
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
	probe := s.tmuxPointCommand(ctx,
		"attach-session", "-f", "ignore-size", "-t", "__yolomux_ignore_size_capability_probe__",
	)
	out, _ := probe.CombinedOutput()
	if probe.ProcessState == nil || !probe.ProcessState.Exited() {
		return false
	}
	if ctx != nil && ctx.Err() != nil {
		return false
	}
	text := strings.ToLower(string(out))
	s.ignoreSizeSupported = !strings.Contains(text, "unknown option") && !strings.Contains(text, "unknown flag")
	s.ignoreSizeChecked = true
	return s.ignoreSizeSupported
}

func (s *server) configureTmuxSession(session string, contexts ...context.Context) {
	var ctx context.Context
	if len(contexts) > 0 {
		ctx = contexts[0]
	}
	target := s.exactTarget(session)
	for _, args := range [][]string{{"set-option", "-s", "set-clipboard", "on"}, {"set-option", "-t", target, "window-size", "largest"}, {"set-option", "-wg", "aggressive-resize", "on"}} {
		_ = s.tmuxPointCommand(ctx, args...).Run()
	}
}

func (s *server) claimResizeAuthority(ctx context.Context, session, activeClient string, cols, rows int) {
	if activeClient == "" || !s.supportsIgnoreSize(ctx) {
		return
	}
	target := s.exactTarget(session)
	format := "#{client_tty}\t#{client_session}\t#{client_width}\t#{client_height}\t#{window_id}"
	out, err := s.tmuxCommandContext(ctx, "list-clients", "-F", format).Output()
	if err != nil {
		return
	}
	lines := strings.Split(strings.TrimSpace(string(out)), "\n")
	activeWindow := ""
	for _, line := range lines {
		fields := strings.Split(line, "\t")
		if len(fields) == 5 && fields[0] == activeClient {
			activeWindow = fields[4]
		}
	}
	for _, line := range lines {
		if ctx.Err() != nil {
			return
		}
		fields := strings.Split(line, "\t")
		if len(fields) < 4 || (fields[1] != session && (len(fields) != 5 || activeWindow == "" || fields[4] != activeWindow)) {
			continue
		}
		client := fields[0]
		width, _ := strconv.Atoi(fields[2])
		height, _ := strconv.Atoi(fields[3])
		if client == activeClient {
			_ = s.tmuxCommandContext(ctx, "refresh-client", "-t", client, "-f", "!ignore-size").Run()
		} else if width > cols || height > rows {
			_ = s.tmuxCommandContext(ctx, "refresh-client", "-t", client, "-f", "ignore-size").Run()
		}
	}
	_ = s.tmuxCommandContext(ctx, "refresh-client", "-t", target).Run()
}

// Terminal controls share the connection's lifetime and latest resize owner. A
// slow tmux client must never hold the goroutine delivering keyboard bytes.
func (s *server) startTerminalControls(conn *wsConn, session, tty string, resize ...func(int, int) error) (func(int, int, int, ...bool), func()) {
	ctx, cancel := context.WithCancel(context.Background())
	wake := make(chan struct{}, 1)
	done := make(chan struct{})
	var mu sync.Mutex
	var cols, rows, txn, generation int
	var claim, pendingResize bool
	var activeCancel context.CancelFunc
	request := func(nextCols, nextRows, nextTxn int, authority ...bool) {
		mu.Lock()
		if nextCols > 0 && nextRows > 0 {
			cols, rows, pendingResize = nextCols, nextRows, true
			claim = len(authority) == 0 || authority[0]
		}
		if nextTxn > 0 {
			txn = nextTxn
		}
		generation++
		if activeCancel != nil {
			activeCancel()
		}
		select {
		case wake <- struct{}{}:
		default:
		}
		mu.Unlock()
	}
	go func() {
		defer close(done)
		for {
			select {
			case <-ctx.Done():
				return
			case <-wake:
			}
			mu.Lock()
			select {
			case <-wake:
			default:
			}
			currentCols, currentRows, currentTxn, currentGeneration, currentClaim, currentResize := cols, rows, txn, generation, claim, pendingResize
			commandCtx, commandCancel := context.WithCancel(ctx)
			activeCancel = commandCancel
			mu.Unlock()
			var controlErr error
			if currentResize && len(resize) > 0 {
				controlErr = s.resizeTerminalNative(commandCtx, session, tty, currentCols, currentRows, resize[0], currentClaim)
			} else if currentClaim && tty != "" && s.supportsIgnoreSize(commandCtx) {
				s.claimResizeAuthority(commandCtx, session, tty, currentCols, currentRows)
			} else {
				_ = s.tmuxCommandContext(commandCtx, "refresh-client", "-t", s.exactTarget(session)).Run()
			}
			if commandCtx.Err() == nil && controlErr == nil && currentTxn > 0 {
				payload, _ := json.Marshal(map[string]any{"type": "refresh-ack", "txn": currentTxn})
				_ = conn.frame(1, payload)
			}
			if controlErr != nil && commandCtx.Err() == nil {
				slog.Warn("terminal resize did not converge", "session", session, "cols", currentCols, "rows", currentRows, "error", controlErr)
			}
			mu.Lock()
			if generation == currentGeneration {
				claim, pendingResize, txn, activeCancel = false, false, 0, nil
			}
			mu.Unlock()
			commandCancel()
		}
	}()
	return request, func() {
		cancel()
		// Closing transport also releases a worker blocked while sending an ack.
		_ = conn.Close()
		<-done
	}
}

func (s *server) websocket(w http.ResponseWriter, r *http.Request) {
	user, status := s.identityStatus(r)
	if status != 0 {
		if rejectCredentialAdmission(w, status) {
			return
		}
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
	if session == "" || s.tmuxPointCommand(r.Context(), "has-session", "-t", session).Run() != nil {
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
	transportClosed := make(chan struct{})
	stopClose := context.AfterFunc(r.Context(), func() { _ = conn.Close(); close(transportClosed) })
	defer func() {
		if !stopClose() {
			<-transportClosed
		}
	}()
	accept := sha1.Sum([]byte(key + wsGUID))
	fmt.Fprintf(rw, "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: %s\r\n\r\n", base64.StdEncoding.EncodeToString(accept[:]))
	if err := rw.Flush(); err != nil {
		conn.Close()
		return
	}
	s.bridge(&wsConn{Conn: conn}, session, readonly, clientID, r.Context())
}

func (s *server) bridge(conn *wsConn, session string, readonly bool, clientID string, contexts ...context.Context) {
	parent := context.Background()
	if len(contexts) > 0 {
		parent = contexts[0]
	}
	ctx, cancel := context.WithCancel(parent)
	defer cancel()
	defer conn.Close()
	if conn.writeTimeout <= 0 {
		conn.writeTimeout = s.terminalWriteTimeout
	}
	rows, cols := 36, 120
	reader := bufio.NewReader(conn)
	_ = conn.SetReadDeadline(time.Now().Add(750 * time.Millisecond))
	var pending [][]byte
	for {
		opcode, payload, err := readFrame(reader)
		if err != nil {
			var networkError net.Error
			if errors.As(err, &networkError) && networkError.Timeout() {
				break
			}
			return
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
			break
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
	cancelledPTY := make(chan struct{})
	stopPTY := context.AfterFunc(ctx, func() { _ = conn.Close(); pty.close(); close(cancelledPTY) })
	defer func() {
		if !stopPTY() {
			<-cancelledPTY
		}
	}()
	if ctx.Err() != nil {
		return
	}
	s.configureTmuxSession(session, ctx)
	args := []string{"attach-session", "-t", session}
	if s.supportsIgnoreSize(ctx) {
		args = []string{"attach-session", "-f", "ignore-size", "-t", s.exactTarget(session)}
	}
	if readonly {
		args = []string{"attach-session", "-r", "-t", s.exactTarget(session)}
		if s.supportsIgnoreSize(ctx) {
			args = []string{"attach-session", "-r", "-f", "ignore-size", "-t", s.exactTarget(session)}
		}
	}
	cmd := s.tmuxCommandContext(ctx, args...)
	cmd.SysProcAttr = terminalProcessAttributes()
	cmd.Env = append(cmd.Env, "TERM=xterm-256color")
	cmd.Stdin = pty.slave
	cmd.Stdout = pty.slave
	cmd.Stderr = pty.slave
	if err := cmd.Start(); err != nil {
		slog.Error("tmux attach failed", "session", session, "error", err)
		return
	}
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
	requestControl, stopControls := s.startTerminalControls(conn, session, pty.tty, func(rows, cols int) error {
		changed := pty.rows != rows || pty.cols != cols
		if err := pty.resize(rows, cols); err != nil {
			return err
		}
		if changed && cmd.Process != nil {
			_ = cmd.Process.Signal(syscall.SIGWINCH)
		}
		return nil
	})
	pty.requestControl = requestControl
	defer stopControls()
	outputDone := make(chan struct{})
	defer func() { _ = conn.Close(); _ = pty.master.Close(); <-outputDone }()
	go func() {
		defer close(outputDone)
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
	requestControl(cols, rows, 0)
	for _, payload := range pending {
		s.handleMessage(pty.master, pty, cmd, conn, session, clientID, payload, !readonly, ctx)
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
		s.handleMessage(pty.master, pty, cmd, conn, session, clientID, payload, !readonly, ctx)
	}
}

func (s *server) handleMessage(stdin io.Writer, pty *ptyPair, process *exec.Cmd, conn *wsConn, session, clientID string, payload []byte, writable bool, contexts ...context.Context) {
	var ctx context.Context
	if len(contexts) > 0 {
		ctx = contexts[0]
	}
	if ctx != nil && ctx.Err() != nil {
		return
	}
	var msg struct {
		Type   string `json:"type"`
		Pane   string `json:"pane"`
		Column int    `json:"column"`
		Row    int    `json:"row"`
		Count  int    `json:"count"`
		Page   bool   `json:"page"`
		Global bool   `json:"global"`

		Data       string `json:"data"`
		UserInput  *bool  `json:"user_input"`
		Rows       int    `json:"rows"`
		Cols       int    `json:"cols"`
		Txn        int    `json:"txn"`
		Direction  string `json:"direction"`
		Lines      int    `json:"lines"`
		Reason     string `json:"reason"`
		Foreground *bool  `json:"foreground"`
		Activate   bool   `json:"activate"`
	}
	if err := json.Unmarshal(payload, &msg); err != nil {
		var control struct {
			Type string `json:"type"`
		}
		if json.Unmarshal(payload, &control) == nil && control.Type == "application-scroll" {
			s.rejectApplicationScroll(conn, session, err)
			return
		}
		if writable {
			if s.writeTerminalInput(stdin, conn, payload) && terminalInputIsActivity(payload) {
				s.recordTerminalInput(session, pty)
			}
		}
		return
	}
	if !writable && msg.Type != "refresh" {
		return
	}
	switch msg.Type {
	case "input":
		if s.writeTerminalInput(stdin, conn, []byte(msg.Data)) && (msg.UserInput == nil || *msg.UserInput) && terminalInputIsActivity([]byte(msg.Data), msg.UserInput != nil && *msg.UserInput) {
			s.recordTerminalInput(session, pty)
		}
	case "resize":
		if msg.Foreground != nil && !*msg.Foreground {
			return
		}
		if msg.Rows > 0 && msg.Cols > 0 {
			rows := clampDimension(msg.Rows, 2, 300, 36)
			cols := clampDimension(msg.Cols, 20, 500, 120)
			pty.requestControl(cols, rows, 0, msg.Activate || msg.Foreground != nil && *msg.Foreground)
		}
	case "application-scroll":
		if err := s.applicationScroll(ctx, session, msg.Pane, msg.Direction, msg.Count, msg.Column, msg.Row, msg.Page, msg.Global); err != nil {
			s.rejectApplicationScroll(conn, session, err)
		}
	case "tmux-scroll":
		if (msg.Direction == "up" || msg.Direction == "down") && msg.Lines > 0 {
			lines := clampDimension(msg.Lines, 1, 80, 1)
			if msg.Direction == "up" {
				_ = s.tmuxPointCommand(ctx, "copy-mode", "-e", "-t", s.exactTarget(session)).Run()
			}
			key := "scroll-down"
			if msg.Direction == "up" {
				key = "scroll-up"
			}
			_ = s.tmuxPointCommand(ctx, "send-keys", "-X", "-t", s.exactTarget(session), "-N", strconv.Itoa(lines), key).Run()
		}
	case "refresh":
		pty.requestControl(0, 0, msg.Txn)
	}
}

// Raw and JSON input share one bounded PTY write so a disconnected peer cannot strand the bridge.
func (s *server) writeTerminalInput(stdin io.Writer, conn *wsConn, payload []byte) bool {
	timeout := s.terminalWriteTimeout
	if timeout <= 0 {
		timeout = terminalWriteTimeout
	}
	if writer, ok := stdin.(interface{ SetWriteDeadline(time.Time) error }); ok {
		if err := writer.SetWriteDeadline(time.Now().Add(timeout)); err != nil {
			if conn != nil {
				_ = conn.Close()
			}
			return false
		}
		defer writer.SetWriteDeadline(time.Time{})
	}
	n, err := stdin.Write(payload)
	if err != nil || n != len(payload) {
		if conn != nil {
			_ = conn.Close()
		}
		return false
	}
	return n > 0
}

func shellQuote(value string) string { return "'" + strings.ReplaceAll(value, "'", "'\\''") + "'" }

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
		h = append(h, 127)
		h = binary.BigEndian.AppendUint64(h, uint64(n))
	}
	_, err := w.Write(append(h, payload...))
	return err
}

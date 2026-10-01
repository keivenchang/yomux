package main

import (
	"database/sql"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/keivenchang/yomux/internal/stats"

	_ "github.com/mattn/go-sqlite3"
)

// statsCollectorInputs is the complete external-input boundary for the Go
// stats collectors. Production resolves these paths from explicit operator
// configuration; synthetic mode creates all three inside one fixture root.
type statsCollectorInputs struct {
	claudeSessionRoot    string
	claudeTranscriptRoot string
	codexTranscriptRoot  string
	opencodeDatabase     string
}

const (
	syntheticStatsFixtureManifest = ".yolomux-stats-fixture"
	syntheticStatsFixtureVersion  = "yolomux-stats-fixture-v1\n"
	syntheticStatsFixtureTimeEnv  = "YOLOMUX_STATS_SYNTHETIC_FIXTURE_TIME"
	syntheticStatsWriterFence     = "stats-writer-compat-v9.json"
	syntheticStatsVacuumBaseline  = "stats-vacuum-v9.json"
	syntheticStatsWriterLock      = "yostats.sqlite3.writer.lock"
)

// resolveStatsCollectorInputs keeps synthetic development runs independent of
// ambient client state. The synthetic switch deliberately ignores configured
// HOME/XDG/Codex/Claude/OpenCode paths and requires a private tmux socket too;
// otherwise an otherwise synthetic run could still inspect the operator's
// default tmux server.
func resolveStatsCollectorInputs() (statsCollectorInputs, error) {
	if os.Getenv("YOLOMUX_STATS_SYNTHETIC") != "1" {
		claude, codex, opencode, err := explicitCollectorPaths()
		if err != nil {
			return statsCollectorInputs{}, err
		}
		return statsCollectorInputs{
			claudeSessionRoot:    filepath.Join(filepath.Dir(filepath.Clean(claude)), "sessions"),
			claudeTranscriptRoot: claude, codexTranscriptRoot: codex, opencodeDatabase: opencode,
		}, nil
	}
	absRoot, err := syntheticStatsRoot()
	if err != nil {
		return statsCollectorInputs{}, err
	}
	if err := validateSyntheticTmuxSocket(absRoot); err != nil {
		return statsCollectorInputs{}, err
	}
	if err := prepareSyntheticStatsInputs(absRoot); err != nil {
		return statsCollectorInputs{}, err
	}
	return statsCollectorInputs{
		claudeSessionRoot:    filepath.Join(absRoot, "claude", "sessions"),
		claudeTranscriptRoot: filepath.Join(absRoot, "claude"),
		codexTranscriptRoot:  filepath.Join(absRoot, "codex"),
		opencodeDatabase:     filepath.Join(absRoot, "opencode.db"),
	}, nil
}

func syntheticStatsRoot() (string, error) {
	root := strings.TrimSpace(os.Getenv("YOLOMUX_STATS_SYNTHETIC_ROOT"))
	if root == "" {
		return "", errors.New("synthetic stats requires an explicit YOLOMUX_STATS_SYNTHETIC_ROOT")
	}
	absRoot, err := filepath.Abs(root)
	if err != nil {
		return "", fmt.Errorf("synthetic stats root is invalid: %w", err)
	}
	if !filepath.IsAbs(absRoot) || filepath.Clean(absRoot) == string(filepath.Separator) {
		return "", errors.New("synthetic stats root must be a non-root absolute path")
	}
	return filepath.Clean(absRoot), nil
}

func syntheticStatsDatabasePath() (string, error) {
	root, err := syntheticStatsRoot()
	if err != nil {
		return "", err
	}
	return filepath.Join(root, "yostats.sqlite3"), nil
}

// syntheticHostInputsEnabled covers the complete host-observation boundary,
// including the separate in-process Daemons process sampler. A fixture that
// opts into synthetic host facts must not leave one collector on live /proc.
func syntheticHostInputsEnabled(syntheticStats bool) bool {
	return syntheticStats || os.Getenv("YOLOMUX_STATS_SYNTHETIC_HOST") == "1"
}

func selectHostSources(synthetic bool, production, fixture func() stats.HostSources) stats.HostSources {
	if synthetic {
		return fixture()
	}
	return production()
}

// prepareSyntheticStatsInputs recreates a small, fixed-shape collector input
// set on every launch. Recreating the exact files avoids stale cursor/database
// state while keeping the fixture portable and independent of the host.
func prepareSyntheticStatsInputs(root string) error {
	if !filepath.IsAbs(root) || filepath.Clean(root) == string(filepath.Separator) {
		return fmt.Errorf("synthetic stats root must be a non-root absolute path")
	}
	now, err := syntheticStatsFixtureNow()
	if err != nil {
		// Validate the optional clock before removing any fixture-owned input so a
		// malformed test setting cannot destroy the last good synthetic fixture.
		return err
	}
	if err := claimSyntheticStatsRoot(root); err != nil {
		return err
	}
	claudeRoot := filepath.Join(root, "claude")
	codexRoot := filepath.Join(root, "codex")
	// The manifest proves that this root belongs to the synthetic fixture. Once
	// claimed, remove only the fixture-owned trees and SQLite sidecars; unknown
	// content was rejected by claimSyntheticStatsRoot before we got here.
	if err := os.RemoveAll(claudeRoot); err != nil {
		return fmt.Errorf("reset synthetic Claude root: %w", err)
	}
	if err := os.RemoveAll(codexRoot); err != nil {
		return fmt.Errorf("reset synthetic Codex root: %w", err)
	}
	if err := resetSyntheticSQLitePath(filepath.Join(root, "opencode.db")); err != nil {
		return fmt.Errorf("reset synthetic OpenCode database: %w", err)
	}
	if err := resetSyntheticSQLitePath(filepath.Join(root, "yostats.sqlite3")); err != nil {
		return fmt.Errorf("reset synthetic stats database: %w", err)
	}
	for _, name := range []string{
		"opencode-token-cursors.json", "opencode-token-cursors.json.lock",
		"transcript-token-cursors.json", "transcript-token-cursors.json.lock",
	} {
		if err := os.Remove(filepath.Join(root, name)); err != nil && !os.IsNotExist(err) {
			return fmt.Errorf("reset synthetic token cursor %s: %w", name, err)
		}
	}
	if err := os.MkdirAll(filepath.Join(claudeRoot, "projects"), 0o700); err != nil {
		return fmt.Errorf("create synthetic Claude root: %w", err)
	}
	if err := os.MkdirAll(filepath.Join(claudeRoot, "sessions"), 0o700); err != nil {
		return fmt.Errorf("create synthetic Claude session metadata root: %w", err)
	}
	if err := os.MkdirAll(filepath.Join(codexRoot, "sessions"), 0o700); err != nil {
		return fmt.Errorf("create synthetic Codex root: %w", err)
	}
	claudeRecord := fmt.Sprintf("{\"type\":\"assistant\",\"timestamp\":%q,\"message\":{\"id\":\"synthetic-claude-message\",\"model\":\"synthetic-claude-model\",\"usage\":{\"input_tokens\":10,\"output_tokens\":5}}}\n", now.Add(-2*time.Second).Format(time.RFC3339Nano))
	if err := os.WriteFile(filepath.Join(claudeRoot, "projects", "synthetic.jsonl"), []byte(claudeRecord), 0o600); err != nil {
		return fmt.Errorf("write synthetic Claude transcript: %w", err)
	}
	codexRecord := fmt.Sprintf("{\"type\":\"session_meta\",\"timestamp\":%q,\"payload\":{\"id\":\"synthetic-codex-thread\",\"cwd\":\"/synthetic/repo\",\"model\":\"synthetic-codex-model\"}}\n{\"type\":\"event_msg\",\"timestamp\":%q,\"payload\":{\"type\":\"token_count\",\"info\":{\"total_token_usage\":{\"input_tokens\":10,\"cached_input_tokens\":2,\"output_tokens\":5,\"reasoning_output_tokens\":5}}}}\n", now.Add(-3*time.Second).Format(time.RFC3339Nano), now.Add(-1*time.Second).Format(time.RFC3339Nano))
	if err := os.WriteFile(filepath.Join(codexRoot, "sessions", "synthetic.jsonl"), []byte(codexRecord), 0o600); err != nil {
		return fmt.Errorf("write synthetic Codex transcript: %w", err)
	}

	databasePath := filepath.Join(root, "opencode.db")
	database, err := sql.Open("sqlite3", databasePath)
	if err != nil {
		return fmt.Errorf("create synthetic OpenCode database: %w", err)
	}
	defer database.Close()
	statements := []string{
		`CREATE TABLE session (id TEXT PRIMARY KEY, directory TEXT, agent TEXT, model TEXT, time_created INTEGER, time_updated INTEGER, cost REAL, parent_id TEXT)`,
		`CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		`CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)`,
		`CREATE TABLE session_input (id TEXT PRIMARY KEY, session_id TEXT, delivery TEXT, admitted_seq INTEGER, promoted_seq INTEGER, time_created INTEGER)`,
	}
	for _, statement := range statements {
		if _, err := database.Exec(statement); err != nil {
			return fmt.Errorf("create synthetic OpenCode schema: %w", err)
		}
	}
	nowMillis := now.UnixMilli()
	if _, err := database.Exec(`INSERT INTO session (id, directory, agent, model, time_created, time_updated, cost, parent_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)`, "ses-synthetic", root, "build", `{"providerID":"synthetic","id":"synthetic-model"}`, nowMillis-3000, nowMillis-1000, 0, nil); err != nil {
		return fmt.Errorf("seed synthetic OpenCode session: %w", err)
	}
	if _, err := database.Exec(`INSERT INTO message (id, session_id, time_created, time_updated, data) VALUES (?, ?, ?, ?, ?)`, "msg-synthetic", "ses-synthetic", nowMillis-2000, nowMillis-1000, `{"role":"assistant"}`); err != nil {
		return fmt.Errorf("seed synthetic OpenCode message: %w", err)
	}
	if _, err := database.Exec(`INSERT INTO part (id, message_id, session_id, time_created, time_updated, data) VALUES (?, ?, ?, ?, ?, ?)`, "part-synthetic", "msg-synthetic", "ses-synthetic", nowMillis-1500, nowMillis-1000, `{"type":"step-finish","tokens":{"total":15,"input":10,"output":5,"reasoning":0,"cache":{"read":2,"write":0}}}`); err != nil {
		return fmt.Errorf("seed synthetic OpenCode part: %w", err)
	}
	return nil
}

// syntheticStatsFixtureNow supplies the source timestamp used by the small
// transcript/OpenCode fixture. The default stays relative to launch so a
// synthetic server has current-window data; tests and parity harnesses can pin
// it to an RFC3339 timestamp when they need byte-stable source inputs.
func syntheticStatsFixtureNow() (time.Time, error) {
	raw := strings.TrimSpace(os.Getenv(syntheticStatsFixtureTimeEnv))
	if raw == "" {
		return time.Now().UTC(), nil
	}
	parsed, err := time.Parse(time.RFC3339Nano, raw)
	if err != nil {
		return time.Time{}, fmt.Errorf("%s must be RFC3339: %w", syntheticStatsFixtureTimeEnv, err)
	}
	return parsed.UTC(), nil
}

// claimSyntheticStatsRoot establishes a fail-closed ownership boundary for
// synthetic inputs. A caller may point the root at any absolute path, but it
// must be empty on first use or carry the fixture's manifest on later uses.
// Unknown files are rejected recursively instead of being silently included by
// transcript discovery. This keeps repeated launches reproducible without
// deleting arbitrary user data.
func claimSyntheticStatsRoot(root string) error {
	if err := os.MkdirAll(root, 0o700); err != nil {
		return fmt.Errorf("create synthetic stats root: %w", err)
	}
	manifestPath := filepath.Join(root, syntheticStatsFixtureManifest)
	manifest, err := os.ReadFile(manifestPath)
	if err != nil && !os.IsNotExist(err) {
		return fmt.Errorf("read synthetic fixture manifest: %w", err)
	}
	if os.IsNotExist(err) {
		entries, readErr := os.ReadDir(root)
		if readErr != nil {
			return fmt.Errorf("inspect synthetic stats root: %w", readErr)
		}
		if len(entries) != 0 {
			return fmt.Errorf("synthetic stats root is not an empty owned fixture: %s", root)
		}
		if writeErr := os.WriteFile(manifestPath, []byte(syntheticStatsFixtureVersion), 0o600); writeErr != nil {
			return fmt.Errorf("write synthetic fixture manifest: %w", writeErr)
		}
		return nil
	}
	if string(manifest) != syntheticStatsFixtureVersion {
		return fmt.Errorf("synthetic stats fixture manifest version is unsupported")
	}
	if err := validateSyntheticStatsTree(root); err != nil {
		return err
	}
	return nil
}

func validateSyntheticStatsTree(root string) error {
	if err := validateSyntheticStatsEntries(root, map[string]bool{
		syntheticStatsFixtureManifest:        true,
		"claude":                             true,
		"codex":                              true,
		"opencode.db":                        true,
		"opencode.db-wal":                    true,
		"opencode.db-shm":                    true,
		"opencode.db-journal":                true,
		"yostats.sqlite3":                    true,
		"yostats.sqlite3-wal":                true,
		"yostats.sqlite3-shm":                true,
		"yostats.sqlite3-journal":            true,
		"opencode-token-cursors.json":        true,
		"opencode-token-cursors.json.lock":   true,
		"transcript-token-cursors.json":      true,
		"transcript-token-cursors.json.lock": true,
		"tmux.sock":                          true,
		syntheticStatsWriterFence:            true,
		syntheticStatsVacuumBaseline:         true,
		syntheticStatsWriterLock:             true,
	}); err != nil {
		return err
	}
	if err := validateSyntheticStatsDirectory(filepath.Join(root, "claude"), map[string]bool{"projects": true, "sessions": true}); err != nil {
		return err
	}
	if err := validateSyntheticStatsDirectory(filepath.Join(root, "claude", "projects"), map[string]bool{"synthetic.jsonl": true}); err != nil {
		return err
	}
	if err := validateSyntheticStatsDirectory(filepath.Join(root, "claude", "sessions"), map[string]bool{}); err != nil {
		return err
	}
	if err := validateSyntheticStatsDirectory(filepath.Join(root, "codex"), map[string]bool{"sessions": true}); err != nil {
		return err
	}
	if err := validateSyntheticStatsDirectory(filepath.Join(root, "codex", "sessions"), map[string]bool{"synthetic.jsonl": true}); err != nil {
		return err
	}
	return nil
}

func validateSyntheticTmuxSocket(root string) error {
	socket := strings.TrimSpace(os.Getenv("YOLOMUX_TMUX_SOCKET"))
	if socket == "" {
		return errors.New("synthetic stats requires an explicit fixture-owned YOLOMUX_TMUX_SOCKET")
	}
	absSocket, err := filepath.Abs(socket)
	if err != nil || !syntheticPathWithinRoot(filepath.Clean(absSocket), root) {
		return errors.New("synthetic stats YOLOMUX_TMUX_SOCKET must be inside YOLOMUX_STATS_SYNTHETIC_ROOT")
	}
	if filepath.Dir(filepath.Clean(absSocket)) != filepath.Clean(root) || filepath.Base(absSocket) != "tmux.sock" {
		return errors.New("synthetic stats YOLOMUX_TMUX_SOCKET must be the fixture-owned tmux.sock at the fixture root")
	}
	if info, err := os.Lstat(absSocket); err == nil && info.Mode()&os.ModeSocket == 0 {
		return errors.New("synthetic stats YOLOMUX_TMUX_SOCKET exists but is not a Unix socket")
	} else if err != nil && !os.IsNotExist(err) {
		return fmt.Errorf("inspect synthetic tmux socket: %w", err)
	}
	return nil
}

func syntheticPathWithinRoot(path, root string) bool {
	relative, err := filepath.Rel(filepath.Clean(root), filepath.Clean(path))
	return err == nil && relative != ".." && !strings.HasPrefix(relative, ".."+string(filepath.Separator)) && !filepath.IsAbs(relative)
}

func validateSyntheticStatsDirectory(path string, allowed map[string]bool) error {
	info, err := os.Stat(path)
	if os.IsNotExist(err) {
		return nil
	}
	if err != nil {
		return fmt.Errorf("inspect synthetic fixture directory %s: %w", path, err)
	}
	if !info.IsDir() {
		return fmt.Errorf("synthetic fixture path is not a directory: %s", path)
	}
	entries, err := os.ReadDir(path)
	if err != nil {
		return fmt.Errorf("read synthetic fixture directory %s: %w", path, err)
	}
	return validateSyntheticStatsEntries(path, allowed, entries)
}

func validateSyntheticStatsEntries(path string, allowed map[string]bool, supplied ...[]os.DirEntry) error {
	entries := []os.DirEntry(nil)
	if len(supplied) > 0 {
		entries = supplied[0]
	} else {
		readEntries, err := os.ReadDir(path)
		if err != nil {
			return fmt.Errorf("read synthetic fixture directory %s: %w", path, err)
		}
		entries = readEntries
	}
	for _, entry := range entries {
		if !allowed[entry.Name()] {
			return fmt.Errorf("unknown synthetic fixture content: %s", filepath.Join(path, entry.Name()))
		}
		if entry.Type()&os.ModeSymlink != 0 {
			return fmt.Errorf("synthetic fixture symlink is not allowed: %s", filepath.Join(path, entry.Name()))
		}
	}
	return nil
}

func resetSyntheticSQLitePath(path string) error {
	for _, candidate := range []string{
		path, path + "-wal", path + "-shm", path + "-journal", path + ".writer.lock",
		filepath.Join(filepath.Dir(path), syntheticStatsWriterFence),
		filepath.Join(filepath.Dir(path), syntheticStatsVacuumBaseline),
	} {
		if err := os.Remove(candidate); err != nil && !os.IsNotExist(err) {
			return err
		}
	}
	return nil
}

// syntheticGoProcessMetrics is used only by the opt-in synthetic server. It
// keeps the runtime Daemons/process rows fixture-owned instead of letting the
// recorder fall back to /proc or ps while the other collectors are synthetic.
func syntheticGoProcessMetrics(time.Time) goProcessMetrics {
	cpu, rss, threadCPU, childCPU, childRSS, threadCount := 12.5, uint64(8<<20), 3.25, 1.5, uint64(2<<20), uint64(2)
	return goProcessMetrics{
		PID: 1001, CPUPercent: &cpu, RSSBytes: &rss, ThreadCount: &threadCount,
		Threads: []goThreadMetric{
			{TID: 1001, Name: "synthetic-main", State: "running", Identity: "1001:synthetic", CPUPercent: &threadCPU},
			{TID: 1002, Name: "synthetic-worker", State: "sleeping", Identity: "1002:synthetic", CPUPercent: &threadCPU},
		},
		ChildProcesses: []goChildProcessMetric{{PID: 1003, PPID: 1001, Label: "synthetic-child", State: "running", Identity: "1003:synthetic", CPUPercent: &childCPU, RSSBytes: &childRSS}},
	}
}

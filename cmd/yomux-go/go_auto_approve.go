// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"sort"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/keivenchang/yomux/internal/atomicfile"
	"github.com/keivenchang/yomux/internal/productenv"
	"github.com/keivenchang/yomux/internal/stats"
	"github.com/keivenchang/yomux/internal/yolo"
	"gopkg.in/yaml.v3"
)

const (
	goYoloPollInterval       = 500 * time.Millisecond
	goYoloCapacityRetryDelay = 10 * time.Second
	goYoloApprovalRetryDelay = 3 * time.Second
	goYoloCaptureTimeout     = 2 * time.Second
)

var goComposerPromptLine = regexp.MustCompile(`^\s*[❯›>](.*)$`)
var goComposerPlaceholder = regexp.MustCompile(`(?i)^(?:ask (?:codex|claude)(?: to do anything)?|ask anything|type your message)$`)
var goYoloPositiveOption = regexp.MustCompile(`(?i)^\s*(?:yes\b|allow\b|approve\b|proceed\b|accept\b)`)
var goYoloNegativeOption = regexp.MustCompile(`(?i)^\s*(?:no\b|reject\b|cancel\b|tell claude what to change\b|tell codex what to do differently\b)`)

func goComposerDraft(visible string) (string, bool) {
	lines := strings.Split(visible, "\n")
	for index := len(lines) - 1; index >= max(0, len(lines)-12); index-- {
		match := goComposerPromptLine.FindStringSubmatch(strings.TrimSpace(lines[index]))
		if len(match) != 2 {
			continue
		}
		if !stats.AgentComposerFooter(lines[index+1:]) {
			return "", false
		}
		draft := strings.TrimSpace(match[1])
		if goComposerPlaceholder.MatchString(draft) {
			draft = ""
		}
		return draft, true
	}
	return "", false
}

func goCapacityRecoveryKeys(agentState, visible string, pending bool) []string {
	state := strings.ToLower(strings.TrimSpace(agentState))
	if (state != "idle" && state != "blocked") || !goCapacityNoticeCurrent(visible) {
		return nil
	}
	draft, valid := goComposerDraft(visible)
	if !valid {
		return nil
	}
	if draft == "" {
		return []string{"continue", "Enter"}
	}
	if pending && draft == "continue" {
		return []string{"Enter"}
	}
	return nil
}

func goCapacityRecoveryAllowed(agentState, visible string) bool {
	return goCapacityRecoveryKeys(agentState, visible, false) != nil
}

func goCapacityNoticeCurrent(visible string) bool {
	return stats.CapacityNoticeCurrent(visible)
}

func goCapacityRetryDue(now, lastSent time.Time) bool {
	return lastSent.IsZero() || now.Sub(lastSent) >= goYoloCapacityRetryDelay
}

// Delivery and pending input belong to this session, pane and process instance.
type goYoloCapacityRecovery struct {
	pending         bool
	pastedAt        time.Time
	sessionIdentity string
	processIdentity string
}

type goYoloWorker struct {
	cancel    context.CancelFunc
	done      chan struct{}
	lock      *os.File
	paneLocks map[string]*os.File
	owner     map[string]any
}

type goYoloStore struct {
	mu                sync.Mutex
	transitionMu      sync.Mutex
	transitions       map[string]*sync.Mutex
	path              string
	enabled           map[string]bool
	identities        map[string]string
	agentIDs          map[string]map[string]string
	workers           map[string]*goYoloWorker
	lockOwners        map[string]map[string]any
	lastAction        map[string]string
	lastError         map[string]string
	lastPrompt        map[string]string
	lastPromptAt      map[string]time.Time
	approved          map[string]int
	declined          map[string]int
	blocked           map[string]int
	attempts          map[string]int
	failures          map[string]int
	lastAttempt       map[string]time.Time
	lastSuccess       map[string]time.Time
	metadataRefreshAt map[string]time.Time
	closed            bool
	lastSent          map[string]time.Time
	capacityRecovery  map[string]goYoloCapacityRecovery
}

func newGoYoloStore(configDir string) (*goYoloStore, error) {
	store := &goYoloStore{
		path:    filepath.Join(configDir, "go-yolo-enabled.json"),
		enabled: make(map[string]bool), identities: make(map[string]string), agentIDs: make(map[string]map[string]string), workers: make(map[string]*goYoloWorker), lockOwners: make(map[string]map[string]any), transitions: make(map[string]*sync.Mutex),
		lastSent: make(map[string]time.Time), capacityRecovery: make(map[string]goYoloCapacityRecovery), lastAction: make(map[string]string), lastError: make(map[string]string),
		lastPrompt: make(map[string]string), lastPromptAt: make(map[string]time.Time), approved: make(map[string]int), declined: make(map[string]int), blocked: make(map[string]int),
		attempts: make(map[string]int), failures: make(map[string]int), lastAttempt: make(map[string]time.Time), lastSuccess: make(map[string]time.Time), metadataRefreshAt: make(map[string]time.Time),
	}
	data, err := os.ReadFile(store.path)
	if err != nil && !os.IsNotExist(err) {
		return nil, fmt.Errorf("read YO state: %w", err)
	}
	if err == nil {
		var saved struct {
			Sessions   []string                     `json:"sessions"`
			Identities map[string]string            `json:"identities"`
			AgentIDs   map[string]map[string]string `json:"agent_identities"`
		}
		if err := json.Unmarshal(data, &saved); err != nil {
			return nil, fmt.Errorf("decode YO state: %w", err)
		}
		for _, session := range saved.Sessions {
			if session = strings.TrimSpace(session); session != "" {
				if identity := strings.TrimSpace(saved.Identities[session]); identity != "" {
					store.enabled[session] = true
					store.identities[session] = identity
					store.agentIDs[session] = cloneGoYoloAgentIDs(saved.AgentIDs[session])
				}
			}
		}
	}
	return store, nil
}

func cloneGoYoloAgentIDs(source map[string]string) map[string]string {
	cloned := make(map[string]string, len(source))
	for target, identity := range source {
		cloned[target] = identity
	}
	return cloned
}

func cloneGoYoloAgentIDMap(source map[string]map[string]string) map[string]map[string]string {
	cloned := make(map[string]map[string]string, len(source))
	for session, identities := range source {
		cloned[session] = cloneGoYoloAgentIDs(identities)
	}
	return cloned
}

func cloneGoYoloEnabled(source map[string]bool) map[string]bool {
	cloned := make(map[string]bool, len(source))
	for session, enabled := range source {
		cloned[session] = enabled
	}
	return cloned
}

func cloneGoYoloIdentities(source map[string]string) map[string]string {
	cloned := make(map[string]string, len(source))
	for session, identity := range source {
		cloned[session] = identity
	}
	return cloned
}

func (store *goYoloStore) persistState(enabledState map[string]bool, identityState map[string]string, agentIDState map[string]map[string]string) error {
	sessions := make([]string, 0, len(enabledState))
	for session, enabled := range enabledState {
		if enabled {
			sessions = append(sessions, session)
		}
	}
	sort.Strings(sessions)
	identities := make(map[string]string, len(sessions))
	agentIDs := make(map[string]map[string]string, len(sessions))
	for _, session := range sessions {
		identities[session] = identityState[session]
		agentIDs[session] = cloneGoYoloAgentIDs(agentIDState[session])
	}
	data, err := json.Marshal(struct {
		Sessions   []string                     `json:"sessions"`
		Identities map[string]string            `json:"identities"`
		AgentIDs   map[string]map[string]string `json:"agent_identities"`
	}{Sessions: sessions, Identities: identities, AgentIDs: agentIDs})
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(store.path), 0o700); err != nil {
		return err
	}
	return atomicfile.Write(store.path, append(data, '\n'))
}

func (store *goYoloStore) set(session string, enabled bool, identity string) error {
	return store.setIfIdentity(session, "", enabled, identity)
}

// setIfIdentity prevents a worker for an old tmux incarnation from disabling a
// newer enablement that replaced it while the worker was checking the target.
func (store *goYoloStore) setIfIdentity(session, expectedIdentity string, enabled bool, identity string) error {
	store.mu.Lock()
	defer store.mu.Unlock()
	if store.closed {
		return errors.New("YO manager is shutting down")
	}
	if expectedIdentity != "" && (!store.enabled[session] || store.identities[session] != expectedIdentity) {
		return nil
	}
	newEnabled := make(map[string]bool, len(store.enabled)+1)
	newIdentities := make(map[string]string, len(store.identities)+1)
	newAgentIDs := make(map[string]map[string]string, len(store.agentIDs)+1)
	for name, value := range store.enabled {
		newEnabled[name] = value
	}
	for name, value := range store.identities {
		newIdentities[name] = value
	}
	for name, values := range store.agentIDs {
		newAgentIDs[name] = cloneGoYoloAgentIDs(values)
	}
	if enabled {
		identity = strings.TrimSpace(identity)
		if identity == "" {
			return errors.New("tmux session identity is required to enable YO")
		}
		newEnabled[session] = true
		newIdentities[session] = identity
		if !store.enabled[session] || store.identities[session] != identity {
			newAgentIDs[session] = make(map[string]string)
		}
	} else {
		delete(newEnabled, session)
		delete(newIdentities, session)
		delete(newAgentIDs, session)
	}
	if err := store.persistState(newEnabled, newIdentities, newAgentIDs); err != nil {
		return err
	}
	store.enabled = newEnabled
	store.identities = newIdentities
	store.agentIDs = newAgentIDs
	if !enabled {
		delete(store.lastAction, session)
		delete(store.lastError, session)
		delete(store.approved, session)
		delete(store.declined, session)
		delete(store.blocked, session)
		delete(store.lockOwners, session)
		delete(store.metadataRefreshAt, session)
		for key := range store.lastPrompt {
			if strings.HasPrefix(key, session+"\x00") {
				delete(store.lastPrompt, key)
				delete(store.lastPromptAt, key)
			}
		}
		for target := range store.lastSent {
			if strings.HasPrefix(target, session+"\x00") {
				delete(store.lastSent, target)
				delete(store.capacityRecovery, target)
			}
		}
	}
	return nil
}

// bindAgentInstance pins a pane to the process instance first observed while YO is enabled.
// Reusing the same tmux pane for a replacement process must not inherit automatic approval.
func (store *goYoloStore) bindAgentInstance(session, expectedSessionIdentity, target, processIdentity string) (bool, error) {
	target = strings.TrimSpace(target)
	processIdentity = strings.TrimSpace(processIdentity)
	if target == "" || processIdentity == "" {
		return false, errors.New("agent pane process identity is unavailable")
	}
	store.mu.Lock()
	defer store.mu.Unlock()
	if store.closed {
		return false, errors.New("YO manager is shutting down")
	}
	if !store.enabled[session] || store.identities[session] != expectedSessionIdentity {
		return false, nil
	}
	if existing := store.agentIDs[session][target]; existing != "" {
		return existing == processIdentity, nil
	}
	newAgentIDs := make(map[string]map[string]string, len(store.agentIDs)+1)
	for name, values := range store.agentIDs {
		newAgentIDs[name] = cloneGoYoloAgentIDs(values)
	}
	if newAgentIDs[session] == nil {
		newAgentIDs[session] = make(map[string]string)
	}
	newAgentIDs[session][target] = processIdentity
	if err := store.persistState(store.enabled, store.identities, newAgentIDs); err != nil {
		return false, err
	}
	store.agentIDs = newAgentIDs
	return true, nil
}

func (store *goYoloStore) disableIfAgentInstance(session, expectedSessionIdentity, target, processIdentity string) (bool, error) {
	store.mu.Lock()
	defer store.mu.Unlock()
	if !store.enabled[session] || store.identities[session] != expectedSessionIdentity || store.agentIDs[session][target] != processIdentity {
		return false, nil
	}
	newEnabled := cloneGoYoloEnabled(store.enabled)
	newEnabled[session] = false
	newIdentities := cloneGoYoloIdentities(store.identities)
	newAgentIDs := cloneGoYoloAgentIDMap(store.agentIDs)
	delete(newAgentIDs, session)
	if err := store.persistState(newEnabled, newIdentities, newAgentIDs); err != nil {
		return false, err
	}
	store.enabled = newEnabled
	store.identities = newIdentities
	store.agentIDs = newAgentIDs
	return true, nil
}

func (store *goYoloStore) lockTransition(session string) func() {
	store.transitionMu.Lock()
	transition := store.transitions[session]
	if transition == nil {
		transition = &sync.Mutex{}
		store.transitions[session] = transition
	}
	store.transitionMu.Unlock()
	transition.Lock()
	return transition.Unlock
}

func (store *goYoloStore) record(session, action, failure string, attempted, succeeded bool) bool {
	store.mu.Lock()
	defer store.mu.Unlock()
	changed := store.lastAction[session] != action || store.lastError[session] != failure
	store.lastAction[session] = action
	store.lastError[session] = failure
	if attempted {
		store.attempts[session]++
		store.lastAttempt[session] = time.Now()
	}
	if failure != "" {
		store.failures[session]++
	}
	if succeeded {
		store.lastSuccess[session] = time.Now()
	}
	return changed
}

func (store *goYoloStore) recoverSuspended(session string) bool {
	store.mu.Lock()
	defer store.mu.Unlock()
	if !strings.HasPrefix(store.lastAction[session], "suspended:") {
		return false
	}
	store.lastAction[session] = "watching for supported YO actions"
	store.lastError[session] = ""
	return true
}

func (s *server) publishGoYoloState(session string) {
	if s.events != nil {
		// The browser reducer treats data as a complete snapshot. Publish the
		// one-session change through its keyed-patch contract so updating one
		// session cannot reset YO state for every other session in that tab.
		_, _ = s.events.publish("auto_approve_changed", map[string]any{
			"patch": true, "collection": "sessions",
			"changes": map[string]any{session: s.goYoloSessionPayload(session)},
			"fields":  map[string]any{"topology_generation": s.goYoloTopologyGeneration()},
		})
	}
}

func (s *server) recordGoYoloStatus(session, action, failure string, attempted, succeeded bool) {
	if s.yolo.record(session, action, failure, attempted, succeeded) {
		s.publishGoYoloState(session)
	}
}

func (s *server) publishGoYoloAuditEvent(session, eventType, message string, details map[string]any) {
	record := map[string]any{
		"time": utcEventTime(), "session": session, "type": boundedText(eventType, 256),
		"details": safeEventDetails(details), "message": boundedText(message, 2000),
	}
	if err := s.appendEventLog(record); err != nil {
		slog.Warn("cannot persist YO event", "session", session, "type", eventType, "error", err)
		return
	}
	s.publishEventLogChanged(map[string]any{"session": session})
}

func (s *server) goYoloDryRun() (bool, error) {
	data, err := os.ReadFile(filepath.Join(s.config, "settings.yaml"))
	if err != nil {
		if os.IsNotExist(err) {
			return false, nil
		}
		return false, fmt.Errorf("read YO settings: %w", err)
	}
	settings, err := parseGoYoloSettings(data)
	if err != nil {
		return false, err
	}
	return settings.DryRun, nil
}

type goYoloSettings struct {
	DryRun       bool
	RuleFilePath string
}

func parseGoYoloSettings(data []byte) (goYoloSettings, error) {
	var root map[string]any
	if err := yaml.Unmarshal(data, &root); err != nil {
		return goYoloSettings{}, fmt.Errorf("decode YO settings: %w", err)
	}
	settings := goYoloSettings{RuleFilePath: "~/.config/yomux/yolo-rules.yaml"}
	if root == nil {
		return settings, nil
	}
	section, ok := root["yolo"]
	if !ok || section == nil {
		return settings, nil
	}
	yoloSettings, ok := section.(map[string]any)
	if !ok {
		return goYoloSettings{}, errors.New("settings.yaml yolo must be a mapping")
	}
	if value, exists := yoloSettings["dry_run"]; exists && value != nil {
		dryRun, ok := value.(bool)
		if !ok {
			return goYoloSettings{}, errors.New("settings.yaml yolo.dry_run must be true or false")
		}
		settings.DryRun = dryRun
	}
	if value, exists := yoloSettings["rule_file_path"]; exists && value != nil {
		path, ok := value.(string)
		if !ok || strings.TrimSpace(path) == "" {
			return goYoloSettings{}, errors.New("settings.yaml yolo.rule_file_path must be a non-empty string")
		}
		settings.RuleFilePath = strings.TrimSpace(path)
	}
	return settings, nil
}

func (s *server) goYoloRulesPath() (string, error) {
	data, err := os.ReadFile(filepath.Join(s.config, "settings.yaml"))
	if err != nil && !os.IsNotExist(err) {
		return "", fmt.Errorf("read YO settings: %w", err)
	}
	settings := goYoloSettings{RuleFilePath: "~/.config/yomux/yolo-rules.yaml"}
	if err == nil {
		settings, err = parseGoYoloSettings(data)
		if err != nil {
			return "", err
		}
	}
	path := os.ExpandEnv(settings.RuleFilePath)
	if path == "~" || strings.HasPrefix(path, "~/") {
		home, homeErr := os.UserHomeDir()
		if homeErr != nil {
			return "", fmt.Errorf("resolve YO rules home directory: %w", homeErr)
		}
		path = filepath.Join(home, strings.TrimPrefix(path, "~/"))
	} else if !filepath.IsAbs(path) {
		path = filepath.Join(s.config, path)
	}
	absolute, err := filepath.Abs(path)
	if err != nil {
		return "", fmt.Errorf("resolve YO rules path: %w", err)
	}
	return filepath.Clean(absolute), nil
}

func (s *server) goYoloPolicyStatus() map[string]any {
	path, pathErr := s.goYoloRulesPath()
	dryRun, dryRunErr := s.goYoloDryRun()
	payload := map[string]any{
		"available": false, "path": path, "display_path": "~/.config/yomux/yolo-rules.yaml",
		"exists": false, "source": "error", "source_key": "yolo.rule.source.error", "source_params": map[string]any{},
		"default": string(yolo.Ask), "rule_count": 0, "mtime_ns": int64(0), "dry_run": dryRun,
		"error": nil,
	}
	var loadErr error
	if pathErr != nil {
		loadErr = pathErr
	} else if dryRunErr != nil {
		loadErr = dryRunErr
	} else {
		if stat, err := os.Stat(path); err == nil {
			payload["exists"] = true
			payload["mtime_ns"] = stat.ModTime().UnixNano()
		}
		var policy *yolo.Policy
		policy, loadErr = yolo.Load(path)
		if loadErr == nil {
			payload["available"] = true
			payload["source"] = "file"
			payload["source_key"] = "yolo.rule.source.file"
			payload["default"] = string(policy.Default)
			payload["rule_count"] = len(policy.Rules)
		}
	}
	if loadErr != nil {
		payload["error"] = loadErr.Error()
		payload["user_message"] = map[string]any{"key": "yolo.error.invalidRules", "fallback": loadErr.Error(), "params": map[string]any{}}
	}
	return payload
}

func (s *server) yoloRules(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		w.Header().Set("Allow", http.MethodGet)
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	jsonResponse(w, http.StatusOK, s.goYoloPolicyStatus())
}

func (s *server) yoloRulesReload(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		w.Header().Set("Allow", http.MethodPost)
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	jsonResponse(w, http.StatusOK, s.goYoloPolicyStatus())
}

func (s *server) yoloRulesOpen(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		w.Header().Set("Allow", http.MethodPost)
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	path, err := s.goYoloRulesPath()
	if err != nil {
		jsonResponse(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		jsonResponse(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}
	file, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if err != nil && !errors.Is(err, os.ErrExist) {
		jsonResponse(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}
	if err == nil {
		const template = "# YOmux auto-approval rules.\n# Rules are evaluated top to bottom; the first match wins.\n# Actions: approve, decline, block, ask, notify, off.\n\ndefault: ask\nrules: []\n"
		if _, writeErr := file.WriteString(template); writeErr != nil {
			_ = file.Close()
			_ = os.Remove(path)
			jsonResponse(w, http.StatusInternalServerError, map[string]any{"error": writeErr.Error()})
			return
		}
		if syncErr := file.Sync(); syncErr != nil {
			_ = file.Close()
			_ = os.Remove(path)
			jsonResponse(w, http.StatusInternalServerError, map[string]any{"error": syncErr.Error()})
			return
		}
		if closeErr := file.Close(); closeErr != nil {
			_ = os.Remove(path)
			jsonResponse(w, http.StatusInternalServerError, map[string]any{"error": closeErr.Error()})
			return
		}
	}
	jsonResponse(w, http.StatusOK, s.goYoloPolicyStatus())
}

func (s *server) goYoloAgentStatusNow(ctx context.Context, agent map[string]any) (string, bool) {
	if s.agentStatusClassifier == nil {
		observedAt, _ := agent["status_observed_at"].(float64)
		return strings.ToLower(stringValue(agent["status"])), observedAt > 0 && time.Since(time.Unix(0, int64(observedAt*float64(time.Second)))) <= 2*time.Second
	}
	pid := 0
	switch value := agent["pid"].(type) {
	case int:
		pid = value
	case int64:
		pid = int(value)
	case float64:
		pid = int(value)
	}
	if pid <= 0 {
		return "", false
	}
	result := s.agentStatusClassifier(ctx, stats.AgentStatusRequest{
		Kind: stringValue(agent["kind"]), PID: pid, Command: stringValue(agent["command"]),
		PaneTarget: stringValue(agent["pane_target"]), Directory: stringValue(agent["cwd"]),
	})
	if result.ObservedAt.IsZero() || time.Since(result.ObservedAt) > 2*time.Second {
		return "", false
	}
	return liveAgentWindowState(result.Status), true
}

// goYoloRevalidateAgentInstance reads the live process start identity at the
// delivery boundary. Metadata is useful for discovery, but can be stale by the
// time a prompt decision is ready to send.
func (s *server) goYoloRevalidateAgentInstance(ctx context.Context, session, sessionIdentity, target string, agent map[string]any) (bool, error) {
	expected := strings.TrimSpace(stringValue(agent["process_instance_id"]))
	pid := 0
	switch value := agent["pid"].(type) {
	case int:
		pid = value
	case int64:
		pid = int(value)
	case float64:
		pid = int(value)
	}
	if expected == "" || pid <= 0 || s.agentProcessSnapshot == nil {
		s.recordGoYoloStatus(session, "suspended: live agent process identity is unavailable", "", false, false)
		return false, nil
	}
	current, err := s.agentProcessSnapshot(ctx, pid)
	currentIdentity := stats.AgentStatusProcessInstanceID(stats.AgentRecord{PID: pid, StartedIdentity: current.StartedIdentity})
	expectedCommand := strings.TrimSpace(stringValue(agent["command"]))
	expectedExecutable := strings.TrimSpace(stringValue(agent["executable"]))
	if err != nil || !current.Alive || currentIdentity == "" || expectedCommand == "" || expectedExecutable == "" {
		detail := ""
		if err != nil {
			detail = err.Error()
		}
		s.recordGoYoloStatus(session, "suspended: live agent process identity could not be verified", detail, false, false)
		return false, nil
	}
	if currentIdentity == expected && strings.TrimSpace(current.Command) == expectedCommand && strings.TrimSpace(current.Executable) == expectedExecutable {
		belongs, ownershipErr := s.goYoloAgentBelongsToPane(ctx, target, pid)
		if ownershipErr != nil {
			s.recordGoYoloStatus(session, "suspended: agent pane ownership could not be verified", ownershipErr.Error(), false, false)
			return false, nil
		}
		if belongs {
			return true, nil
		}
		disabled, disableErr := s.yolo.disableIfAgentInstance(session, sessionIdentity, target, expected)
		if disableErr != nil {
			s.recordGoYoloStatus(session, "suspended: replacement agent identity could not be saved", disableErr.Error(), false, false)
			return false, disableErr
		}
		if disabled {
			s.yolo.record(session, "disabled because the agent process left its pane", "", false, false)
			s.publishGoYoloState(session)
		}
		return false, nil
	}
	disabled, err := s.yolo.disableIfAgentInstance(session, sessionIdentity, target, expected)
	if err != nil {
		s.recordGoYoloStatus(session, "suspended: replacement agent identity could not be saved", err.Error(), false, false)
		return false, err
	}
	if disabled {
		s.yolo.record(session, "disabled because the agent process identity changed", "", false, false)
		s.publishGoYoloState(session)
	}
	return false, nil
}

func goYoloProcessBelongsToPane(processes []procEntry, panePID, agentPID int) bool {
	if panePID <= 0 || agentPID <= 0 {
		return false
	}
	if panePID == agentPID {
		return true
	}
	parents := make(map[int]int, len(processes))
	for _, process := range processes {
		if process.PID > 0 {
			parents[process.PID] = process.PPID
		}
	}
	seen := make(map[int]struct{}, len(parents))
	for pid := agentPID; pid > 0; pid = parents[pid] {
		if pid == panePID {
			return true
		}
		if _, exists := seen[pid]; exists {
			return false
		}
		seen[pid] = struct{}{}
		if _, exists := parents[pid]; !exists {
			return false
		}
	}
	return false
}

func (s *server) goYoloAgentBelongsToPane(ctx context.Context, target string, agentPID int) (bool, error) {
	if s.goYoloPaneOwnsProcessFn != nil {
		return s.goYoloPaneOwnsProcessFn(ctx, target, agentPID)
	}
	if s.processEntriesFn == nil {
		return false, errors.New("process tree reader is unavailable")
	}
	commandContext, cancel := context.WithTimeout(ctx, goYoloCaptureTimeout)
	defer cancel()
	output, err := s.tmuxCommandContext(commandContext, "display-message", "-p", "-t", target, "#{pane_pid}").Output()
	if err != nil {
		return false, err
	}
	panePID, err := strconv.Atoi(strings.TrimSpace(string(output)))
	if err != nil || panePID <= 0 {
		return false, errors.New("tmux returned an invalid pane process ID")
	}
	processes, err := s.processEntriesFn()
	if err != nil {
		return false, err
	}
	return goYoloProcessBelongsToPane(processes, panePID, agentPID), nil
}

type goYoloKeyDelivery struct {
	pasted        bool
	keysAttempted bool
	keysSent      bool
}

// goYoloGuardedSendKeys is the only automated terminal write path. It gathers
// live ownership and agent evidence before the final capture, then sends only
// a small allowlisted key sequence when the caller's exact screen guard holds.
func (s *server) goYoloGuardedSendKeys(ctx context.Context, session, target, expectedIdentity string, agent map[string]any, keys []string, allowedStates []string, screenGuard func(string) bool) (goYoloKeyDelivery, error) {
	if len(keys) == 0 || len(keys) > 3 || screenGuard == nil {
		return goYoloKeyDelivery{}, errors.New("invalid guarded YO key sequence")
	}
	switch strings.Join(keys, "\x00") {
	case "continue\x00Enter", "Enter", "Up", "Down":
	default:
		return goYoloKeyDelivery{}, errors.New("unsupported guarded YO key sequence")
	}
	if ctx.Err() != nil || s.yolo == nil || !s.yolo.isEnabled(session) {
		return goYoloKeyDelivery{}, nil
	}
	guard := func() (bool, error) {
		sessionIdentity, sessionErr := s.goYoloSessionIdentity(ctx, session)
		targetIdentity, targetErr := s.goYoloPaneSessionIdentity(ctx, target)
		if sessionErr != nil || targetErr != nil || sessionIdentity != expectedIdentity || targetIdentity != expectedIdentity {
			return false, nil
		}
		state, fresh := s.goYoloAgentStatusNow(ctx, agent)
		if !fresh || !slices.Contains(allowedStates, state) {
			return false, nil
		}
		instanceCurrent, instanceErr := s.goYoloRevalidateAgentInstance(ctx, session, expectedIdentity, target, agent)
		if instanceErr != nil || !instanceCurrent {
			return false, instanceErr
		}
		dryRun, settingsErr := s.goYoloDryRun()
		if settingsErr != nil || dryRun {
			return false, settingsErr
		}
		// Keep the guarded screen capture directly adjacent to each terminal write. In
		// particular, never clear or submit a draft that appeared during the
		// preceding identity and status checks.
		visible, captureErr := s.goYoloCapture(ctx, target)
		return captureErr == nil && screenGuard(visible) && ctx.Err() == nil && s.yolo.isEnabled(session), captureErr
	}
	if len(keys) == 2 && keys[0] == "continue" {
		pasted, err := s.pasteTerminalText(ctx, target, keys[0], guard)
		if err != nil || !pasted {
			return goYoloKeyDelivery{pasted: pasted}, err
		}
		// Paste and submission are separate writes. Reuse every ownership and
		// admission check before Return, including the exact pasted composer.
		submission, err := s.goYoloGuardedSendKeys(ctx, session, target, expectedIdentity, agent, []string{"Enter"}, allowedStates, func(screen string) bool {
			return slices.Equal(goCapacityRecoveryKeys("idle", screen, true), []string{"Enter"})
		})
		return goYoloKeyDelivery{pasted: true, keysAttempted: submission.keysAttempted, keysSent: submission.keysSent}, err
	} else if allowed, err := guard(); err != nil || !allowed {
		return goYoloKeyDelivery{}, err
	}
	commandArgs := append([]string{"send-keys", "-t", target}, keys...)
	if err := s.tmuxCommandContext(ctx, commandArgs...).Run(); err != nil {
		return goYoloKeyDelivery{keysAttempted: true}, err
	}
	return goYoloKeyDelivery{keysAttempted: true, keysSent: true}, nil
}

func (store *goYoloStore) identity(session string) string {
	store.mu.Lock()
	defer store.mu.Unlock()
	return store.identities[session]
}

func (store *goYoloStore) isEnabled(session string) bool {
	store.mu.Lock()
	defer store.mu.Unlock()
	return store.enabled[session]
}

func (store *goYoloStore) sessions() []string {
	store.mu.Lock()
	defer store.mu.Unlock()
	sessions := make([]string, 0, len(store.enabled))
	for session := range store.enabled {
		sessions = append(sessions, session)
	}
	sort.Strings(sessions)
	return sessions
}

func (store *goYoloStore) close() {
	store.mu.Lock()
	store.closed = true
	workers := make([]*goYoloWorker, 0, len(store.workers))
	for _, worker := range store.workers {
		worker.cancel()
		workers = append(workers, worker)
	}
	store.mu.Unlock()
	for _, worker := range workers {
		<-worker.done
	}
}

func goYoloTargetLockPath(session string) string {
	socket := strings.TrimSpace(productenv.Value("TMUX_SOCKET"))
	if socket == "" {
		socket = "default-tmux-server"
	}
	key := sha256.Sum256([]byte(socket + "\x00" + session))
	return filepath.Join(os.TempDir(), fmt.Sprintf("yomux-auto-approve-%d", os.Getuid()), "auto-approve-"+hex.EncodeToString(key[:16])+".lock")
}

func (s *server) acquireGoYoloTargetLock(session string) (*os.File, map[string]any, error) {
	path := goYoloTargetLockPath(session)
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return nil, nil, err
	}
	file, err := os.OpenFile(path, os.O_CREATE|os.O_RDWR, 0o600)
	if err != nil {
		return nil, nil, err
	}
	if err := syscall.Flock(int(file.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		var owner map[string]any
		data, readErr := os.ReadFile(path)
		if readErr == nil {
			_ = json.Unmarshal(data, &owner)
		}
		_ = file.Close()
		if owner == nil {
			owner = map[string]any{}
		}
		owner["lock_path"] = path
		return nil, owner, nil
	}
	host, _ := os.Hostname()
	owner := map[string]any{
		"target": session, "pid": os.Getpid(), "hostname": host,
		"project_root": s.root, "started_at": time.Now().UTC().Format(time.RFC3339),
		"lock_path": path,
	}
	data, err := json.Marshal(owner)
	if err == nil {
		if err = file.Truncate(0); err == nil {
			_, err = file.WriteAt(append(data, '\n'), 0)
		}
	}
	if err == nil {
		err = file.Sync()
	}
	if err != nil {
		_ = syscall.Flock(int(file.Fd()), syscall.LOCK_UN)
		_ = file.Close()
		return nil, nil, err
	}
	return file, owner, nil
}

func (s *server) ensureGoYoloPaneLock(session, target string) bool {
	store := s.yolo
	if store == nil || strings.TrimSpace(target) == "" {
		return false
	}
	store.mu.Lock()
	worker := store.workers[session]
	if worker == nil || !store.enabled[session] {
		store.mu.Unlock()
		return false
	}
	if worker.paneLocks[target] != nil {
		store.mu.Unlock()
		return true
	}
	store.mu.Unlock()

	lock, owner, err := s.acquireGoYoloTargetLock(target)
	if err != nil {
		store.mu.Lock()
		changed := false
		if store.workers[session] == worker {
			changed = store.lastAction[session] != "suspended: pane ownership lock could not be acquired" || store.lastError[session] != err.Error()
			store.lastAction[session] = "suspended: pane ownership lock could not be acquired"
			store.lastError[session] = err.Error()
		}
		store.mu.Unlock()
		if changed {
			s.publishGoYoloState(session)
		}
		return false
	}
	if lock == nil {
		store.mu.Lock()
		changed := false
		if store.workers[session] == worker {
			changed = !sameGoYoloLockOwner(store.lockOwners[session], owner)
			store.lockOwners[session] = owner
			store.lastAction[session] = goYoloLockMessage(owner)
			store.lastError[session] = ""
		}
		store.mu.Unlock()
		if changed {
			s.publishGoYoloState(session)
		}
		return false
	}
	store.mu.Lock()
	if store.workers[session] != worker || !store.enabled[session] {
		store.mu.Unlock()
		_ = syscall.Flock(int(lock.Fd()), syscall.LOCK_UN)
		_ = lock.Close()
		return false
	}
	worker.paneLocks[target] = lock
	clearedOwner := false
	if owner := store.lockOwners[session]; owner != nil && owner["target"] == target {
		delete(store.lockOwners, session)
		clearedOwner = true
	}
	store.mu.Unlock()
	if clearedOwner {
		s.publishGoYoloState(session)
	}
	return true
}

func sameGoYoloLockOwner(left, right map[string]any) bool {
	if left == nil || right == nil {
		return left == nil && right == nil
	}
	return fmt.Sprint(left["pid"]) == fmt.Sprint(right["pid"]) &&
		fmt.Sprint(left["target"]) == fmt.Sprint(right["target"]) &&
		fmt.Sprint(left["lock_path"]) == fmt.Sprint(right["lock_path"])
}

func (s *server) releaseStaleGoYoloPaneLocks(session string, seenTargets map[string]bool) {
	if s.yolo == nil {
		return
	}
	s.yolo.mu.Lock()
	worker := s.yolo.workers[session]
	if worker == nil {
		s.yolo.mu.Unlock()
		return
	}
	var stale []*os.File
	for target, lock := range worker.paneLocks {
		if seenTargets[target] {
			continue
		}
		delete(worker.paneLocks, target)
		stale = append(stale, lock)
	}
	if len(stale) > 0 {
		if owner := s.yolo.lockOwners[session]; owner != nil && !seenTargets[stringValue(owner["target"])] {
			delete(s.yolo.lockOwners, session)
		}
	}
	s.yolo.mu.Unlock()
	for _, lock := range stale {
		_ = syscall.Flock(int(lock.Fd()), syscall.LOCK_UN)
		_ = lock.Close()
	}
}

func (s *server) startGoYoloWorker(session string) error {
	store := s.yolo
	if store == nil {
		return errors.New("YO manager unavailable")
	}
	store.mu.Lock()
	if store.closed || !store.enabled[session] || store.workers[session] != nil {
		store.mu.Unlock()
		return nil
	}
	store.mu.Unlock()
	lock, owner, err := s.acquireGoYoloTargetLock(session)
	if err != nil {
		s.recordGoYoloStatus(session, "worker start failed", err.Error(), false, false)
		return fmt.Errorf("acquire YO target lock: %w", err)
	}
	if lock == nil {
		store.mu.Lock()
		store.lockOwners[session] = owner
		store.mu.Unlock()
		return fmt.Errorf("target is %s", goYoloLockMessage(owner))
	}
	store.mu.Lock()
	if store.closed || !store.enabled[session] || store.workers[session] != nil {
		store.mu.Unlock()
		_ = syscall.Flock(int(lock.Fd()), syscall.LOCK_UN)
		_ = lock.Close()
		return nil
	}
	ctx, cancel := context.WithCancel(context.Background())
	worker := &goYoloWorker{cancel: cancel, done: make(chan struct{}), lock: lock, paneLocks: make(map[string]*os.File), owner: owner}
	store.workers[session] = worker
	delete(store.lockOwners, session)
	store.mu.Unlock()
	store.record(session, "watching for supported YO actions", "", false, false)
	go func() {
		defer func() {
			store.mu.Lock()
			if store.workers[session] == worker {
				delete(store.workers, session)
			}
			paneLocks := worker.paneLocks
			worker.paneLocks = nil
			store.mu.Unlock()
			for _, paneLock := range paneLocks {
				_ = syscall.Flock(int(paneLock.Fd()), syscall.LOCK_UN)
				_ = paneLock.Close()
			}
			_ = syscall.Flock(int(worker.lock.Fd()), syscall.LOCK_UN)
			_ = worker.lock.Close()
			close(worker.done)
		}()
		ticker := time.NewTicker(goYoloPollInterval)
		defer ticker.Stop()
		for {
			if ctx.Err() != nil || !store.isEnabled(session) {
				return
			}
			if err := s.goYoloCycle(ctx, session); err != nil && ctx.Err() == nil {
				slog.Warn("YO worker cycle failed", "session", session, "error", err)
			}
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
			}
		}
	}()
	return nil
}

func (s *server) stopGoYoloWorker(session string) {
	if s.yolo == nil {
		return
	}
	s.yolo.mu.Lock()
	worker := s.yolo.workers[session]
	delete(s.yolo.workers, session)
	if worker != nil {
		worker.cancel()
	}
	s.yolo.mu.Unlock()
	if worker != nil {
		<-worker.done
	}
}

func goYoloLockMessage(owner map[string]any) string {
	if owner == nil {
		return "locked by another YOmux"
	}
	pid, _ := owner["pid"].(float64)
	root, _ := owner["project_root"].(string)
	if pid != 0 && root != "" {
		return fmt.Sprintf("locked by another YOmux pid %.0f (%s)", pid, root)
	}
	if pid != 0 {
		return fmt.Sprintf("locked by another YOmux pid %.0f", pid)
	}
	return "locked by another YOmux"
}

func (s *server) requestGoYoloMetadataRefresh(session string) error {
	if s.goYoloMetadataRefreshFn != nil {
		return s.goYoloMetadataRefreshFn(session)
	}
	if s.yolo == nil || !s.yolo.isEnabled(session) {
		return nil
	}
	now := time.Now()
	s.yolo.mu.Lock()
	if previous := s.yolo.metadataRefreshAt[session]; !previous.IsZero() && now.Sub(previous) < time.Second {
		s.yolo.mu.Unlock()
		return nil
	}
	s.yolo.metadataRefreshAt[session] = now
	s.yolo.mu.Unlock()
	topology, err := s.tmuxTopologySnapshot()
	if err != nil {
		return err
	}
	if !slices.Contains(topology.sessions, session) {
		return nil
	}
	// Join an equivalent in-flight metadata build rather than superseding it
	// with another machine-wide generation on every worker retry.
	s.queueMetadataRefresh(false, topology.sessions, topology.identities, nil, topology.generation)
	return nil
}

func (s *server) goYoloCycle(ctx context.Context, session string) error {
	if ctx.Err() != nil || s.yolo == nil || !s.yolo.isEnabled(session) {
		return nil
	}
	expectedIdentity := s.yolo.identity(session)
	currentIdentity, identityErr := s.goYoloSessionIdentity(ctx, session)
	if expectedIdentity == "" {
		s.recordGoYoloStatus(session, "suspended: saved tmux identity is unavailable", "", false, false)
		return nil
	}
	if identityErr != nil || currentIdentity == "" {
		if s.goYoloSessionMissingConfirmed(ctx, session) {
			if err := s.yolo.setIfIdentity(session, expectedIdentity, false, ""); err != nil {
				return err
			}
			s.yolo.record(session, "disabled because the tmux session was removed or renamed", "", false, false)
			s.releaseStaleGoYoloPaneLocks(session, map[string]bool{})
			s.publishGoYoloState(session)
			return nil
		}
		s.recordGoYoloStatus(session, "suspended: tmux identity could not be verified", identityErrString(identityErr), false, false)
		return nil
	}
	if currentIdentity != expectedIdentity {
		if err := s.yolo.setIfIdentity(session, expectedIdentity, false, ""); err != nil {
			return err
		}
		if !s.yolo.isEnabled(session) {
			s.yolo.record(session, "disabled because the tmux session identity changed", "", false, false)
			s.publishGoYoloState(session)
		}
		return nil
	}
	payload, _, stale, builtAt := s.metadataSnapshotWithBuiltAt()
	if payload == nil || stale || builtAt.IsZero() || time.Since(builtAt) > 2*time.Second {
		refreshErr := s.requestGoYoloMetadataRefresh(session)
		s.recordGoYoloStatus(session, "suspended: fresh agent status is unavailable", identityErrString(refreshErr), false, false)
		return nil
	}
	sessions, _ := payload["sessions"].(map[string]any)
	info, _ := sessions[session].(map[string]any)
	agents, _ := info["agents"].([]map[string]any)
	hasSupportedAgent := false
	seenTargets := make(map[string]bool, len(agents))
	for _, agent := range agents {
		kind := strings.ToLower(stringValue(agent["kind"]))
		target := strings.TrimSpace(stringValue(agent["pane_target"]))
		if (kind != "codex" && kind != "claude") || target == "" {
			continue
		}
		hasSupportedAgent = true
		if targetIdentity, err := s.goYoloPaneSessionIdentity(ctx, target); err != nil || targetIdentity != expectedIdentity {
			s.recordGoYoloStatus(session, "suspended: pane identity could not be verified", identityErrString(err), false, false)
			continue
		}
		key := session + "\x00" + target
		seenTargets[key] = true
		seenTargets[target] = true
		if !s.ensureGoYoloPaneLock(session, target) {
			continue
		}
		processIdentity := strings.TrimSpace(stringValue(agent["process_instance_id"]))
		bound, bindErr := s.yolo.bindAgentInstance(session, expectedIdentity, target, processIdentity)
		if bindErr != nil {
			s.recordGoYoloStatus(session, "suspended: agent process identity could not be verified", bindErr.Error(), false, false)
			continue
		}
		if !bound {
			if s.yolo.isEnabled(session) && s.yolo.identity(session) == expectedIdentity {
				if err := s.yolo.setIfIdentity(session, expectedIdentity, false, ""); err != nil {
					s.recordGoYoloStatus(session, "suspended: replacement agent identity could not be saved", err.Error(), false, false)
					return err
				}
				if !s.yolo.isEnabled(session) {
					s.yolo.record(session, "disabled because the agent process identity changed", "", false, false)
					s.publishGoYoloState(session)
				}
			}
			return nil
		}
		state := strings.ToLower(stringValue(agent["status"]))
		observedAt, _ := agent["status_observed_at"].(float64)
		if observedAt <= 0 || time.Since(time.Unix(0, int64(observedAt*float64(time.Second)))) > 2*time.Second {
			if refreshErr := s.requestGoYoloMetadataRefresh(session); refreshErr != nil {
				s.recordGoYoloStatus(session, "suspended: fresh agent status is unavailable", refreshErr.Error(), false, false)
			}
			if state == "idle" || state == "needs-input" {
				s.recordGoYoloStatus(session, "suspended: agent status is stale", "", false, false)
			}
			continue
		}
		liveState, statusFresh := s.goYoloAgentStatusNow(ctx, agent)
		if statusFresh && liveState == "working" {
			s.yolo.mu.Lock()
			delivery, delivered := s.yolo.capacityRecovery[key]
			delivery.pending = false
			if delivered {
				s.yolo.capacityRecovery[key] = delivery
			}
			s.yolo.mu.Unlock()
			if !delivered && s.yolo.recoverSuspended(session) {
				s.publishGoYoloState(session)
			}
			if delivered && delivery.pastedAt.IsZero() && delivery.sessionIdentity == expectedIdentity && delivery.processIdentity == processIdentity {
				screen, captureErr := s.goYoloCapture(ctx, target)
				current, instanceErr := s.goYoloRevalidateAgentInstance(ctx, session, expectedIdentity, target, agent)
				if captureErr == nil && instanceErr == nil && current && !goCapacityNoticeCurrent(screen) {
					s.yolo.mu.Lock()
					delete(s.yolo.capacityRecovery, key)
					delete(s.yolo.lastSent, key)
					s.yolo.mu.Unlock()
					s.yolo.record(session, "submitted continue for the capacity notice; agent working confirmed", "", false, true)
					s.publishGoYoloAuditEvent(session, "yolo_capacity_recovery_resumed", "agent resumed work after capacity recovery delivery", map[string]any{"attempted": false, "succeeded": true})
					s.publishGoYoloState(session)
				}
			}
			continue
		}
		if !statusFresh || liveState != state {
			s.yolo.mu.Lock()
			delete(s.yolo.capacityRecovery, key)
			s.yolo.mu.Unlock()
			s.recordGoYoloStatus(session, "suspended: current agent status could not be confirmed", "", false, false)
			continue
		}
		if s.yolo.recoverSuspended(session) {
			s.publishGoYoloState(session)
		}
		if state != "idle" && state != "needs-input" && !(kind == "codex" && state == "blocked") {
			s.yolo.mu.Lock()
			delete(s.yolo.capacityRecovery, key)
			delete(s.yolo.lastSent, key)
			s.yolo.mu.Unlock()
			continue
		}
		visible, err := s.goYoloCapture(ctx, target)
		if err != nil {
			s.recordGoYoloStatus(session, "suspended: terminal capture failed", err.Error(), false, false)
			continue
		}
		if s.yolo.recoverSuspended(session) {
			s.publishGoYoloState(session)
		}
		if prompt := yolo.ClassifyPrompt(visible, ""); state != "blocked" && prompt.Actionable && strings.EqualFold(string(prompt.Agent), kind) {
			s.yolo.mu.Lock()
			delete(s.yolo.capacityRecovery, key)
			delete(s.yolo.lastSent, key)
			s.yolo.mu.Unlock()
			if err := s.goYoloApprovalCycle(ctx, session, target, expectedIdentity, agent, prompt); err != nil {
				return err
			}
			continue
		}
		if kind != "codex" {
			continue
		}
		s.yolo.mu.Lock()
		pendingOwner := s.yolo.capacityRecovery[key]
		pending := pendingOwner.pending && pendingOwner.sessionIdentity == expectedIdentity && pendingOwner.processIdentity == processIdentity
		s.yolo.mu.Unlock()
		if pending && !pendingOwner.pastedAt.IsZero() {
			if time.Since(pendingOwner.pastedAt) >= goYoloCapacityRetryDelay {
				s.yolo.mu.Lock()
				delete(s.yolo.capacityRecovery, key)
				s.yolo.mu.Unlock()
				s.recordGoYoloStatus(session, "suspended: pasted recovery text could not be confirmed", "", false, false)
				continue
			}
			if draft, valid := goComposerDraft(visible); valid && draft == "" && goCapacityNoticeCurrent(visible) {
				continue
			}
		}
		if draft, valid := goComposerDraft(visible); valid && draft == "" {
			s.yolo.mu.Lock()
			if delivery, exists := s.yolo.capacityRecovery[key]; exists {
				delivery.pending = false
				s.yolo.capacityRecovery[key] = delivery
			}
			s.yolo.mu.Unlock()
			pending = false
		}
		keys := goCapacityRecoveryKeys(state, visible, pending)
		if len(keys) == 0 {
			s.yolo.mu.Lock()
			delete(s.yolo.capacityRecovery, key)
			s.yolo.mu.Unlock()
			if !goCapacityNoticeCurrent(visible) {
				s.yolo.mu.Lock()
				delete(s.yolo.lastSent, key)
				delete(s.yolo.capacityRecovery, key)
				s.yolo.mu.Unlock()
			}
			continue
		}
		s.yolo.mu.Lock()
		lastSent := s.yolo.lastSent[key]
		s.yolo.mu.Unlock()
		if !goCapacityRetryDue(time.Now(), lastSent) {
			continue
		}
		// Capture again immediately before sending so an approval or question that
		// replaced the capacity notice cannot inherit this retry decision.
		latest, err := s.goYoloCapture(ctx, target)
		if err != nil || !slices.Equal(keys, goCapacityRecoveryKeys(state, latest, pending)) {
			if err == nil && !goCapacityNoticeCurrent(latest) {
				s.yolo.mu.Lock()
				delete(s.yolo.lastSent, key)
				delete(s.yolo.capacityRecovery, key)
				s.yolo.mu.Unlock()
			}
			continue
		}
		latestSessionIdentity, sessionErr := s.goYoloSessionIdentity(ctx, session)
		latestTargetIdentity, targetErr := s.goYoloPaneSessionIdentity(ctx, target)
		if !s.yolo.isEnabled(session) || sessionErr != nil || targetErr != nil || latestSessionIdentity != expectedIdentity || latestTargetIdentity != expectedIdentity {
			continue
		}
		dryRun, dryRunErr := s.goYoloDryRun()
		if dryRunErr != nil {
			s.recordGoYoloStatus(session, "suspended: YO settings could not be read", dryRunErr.Error(), false, false)
			continue
		}
		if dryRun {
			now := time.Now()
			s.yolo.mu.Lock()
			s.yolo.lastSent[key] = now
			s.yolo.mu.Unlock()
			// Keep worker attempt counters limited to terminal input. The audit
			// event still records this intended action with attempted=false.
			s.yolo.record(session, "dry-run: would submit continue for the current capacity notice", "", false, false)
			s.publishGoYoloAuditEvent(session, "yolo_capacity_recovery_dry_run", "dry-run: would submit continue for the current capacity notice", map[string]any{"attempted": false, "succeeded": false})
			s.publishGoYoloState(session)
			continue
		}
		delivery, err := s.goYoloGuardedSendKeys(ctx, session, target, expectedIdentity, agent, keys, []string{"idle", "blocked"}, func(screen string) bool {
			return slices.Equal(keys, goCapacityRecoveryKeys(state, screen, pending))
		})
		if delivery.pasted {
			s.yolo.mu.Lock()
			if s.yolo.enabled[session] && s.yolo.identities[session] == expectedIdentity && s.yolo.agentIDs[session][target] == processIdentity {
				s.yolo.capacityRecovery[key] = goYoloCapacityRecovery{
					sessionIdentity: expectedIdentity, processIdentity: processIdentity,
					pending: true, pastedAt: time.Now(),
				}
			}
			s.yolo.mu.Unlock()
		}
		if delivery.keysAttempted {
			s.yolo.mu.Lock()
			s.yolo.lastSent[key] = time.Now()
			s.yolo.mu.Unlock()
		}
		if err != nil {
			s.yolo.record(session, "capacity recovery submission failed", err.Error(), delivery.keysAttempted, false)
			s.publishGoYoloAuditEvent(session, "yolo_capacity_recovery_failed", "capacity recovery submission failed", map[string]any{"error": err.Error(), "attempted": delivery.keysAttempted, "succeeded": false})
			s.publishGoYoloState(session)
			return fmt.Errorf("send capacity recovery: %w", err)
		}
		if !delivery.keysSent {
			if delivery.pasted {
				s.recordGoYoloStatus(session, "capacity recovery text pasted; submission not yet confirmed", "", false, false)
			}
			continue
		}
		responseScreen, responseCaptureErr := s.goYoloCapture(ctx, target)
		draft, validDraft := goComposerDraft(responseScreen)
		s.yolo.mu.Lock()
		s.yolo.capacityRecovery[key] = goYoloCapacityRecovery{
			sessionIdentity: expectedIdentity, processIdentity: processIdentity,
			pending: responseCaptureErr == nil && validDraft && draft == "continue" && goCapacityNoticeCurrent(responseScreen),
		}
		s.yolo.mu.Unlock()
		responseState, responseStatusFresh := s.goYoloAgentStatusNow(ctx, agent)
		if responseCaptureErr != nil || !responseStatusFresh || responseState != "working" || goCapacityNoticeCurrent(responseScreen) {
			s.yolo.record(session, "capacity recovery submission sent; agent response not yet confirmed", "", true, false)
			s.publishGoYoloAuditEvent(session, "yolo_capacity_recovery_attempt", "submitted continue for the current capacity notice; response not yet confirmed", map[string]any{"attempted": true, "succeeded": false})
			s.publishGoYoloState(session)
			continue
		}
		s.yolo.mu.Lock()
		delete(s.yolo.capacityRecovery, key)
		s.yolo.mu.Unlock()
		s.yolo.record(session, "submitted continue for the current capacity notice", "", true, true)
		s.publishGoYoloAuditEvent(session, "yolo_capacity_recovery_attempt", "submitted continue for the current capacity notice", map[string]any{"attempted": true, "succeeded": true})
		s.publishGoYoloState(session)
	}
	s.yolo.mu.Lock()
	for key := range s.yolo.lastSent {
		if strings.HasPrefix(key, session+"\x00") && !seenTargets[key] {
			delete(s.yolo.lastSent, key)
			delete(s.yolo.capacityRecovery, key)
		}
	}
	s.yolo.mu.Unlock()
	s.releaseStaleGoYoloPaneLocks(session, seenTargets)
	if !hasSupportedAgent && s.yolo.recoverSuspended(session) {
		s.publishGoYoloState(session)
	}
	return nil
}

func (s *server) goYoloCapacityCycle(ctx context.Context, session string) error {
	return s.goYoloCycle(ctx, session)
}

func goYoloActionOption(prompt yolo.Prompt, action yolo.Action) int {
	for _, option := range prompt.Options {
		label := strings.ToLower(option.Label)
		if action == yolo.Approve {
			if strings.Contains(label, "don't ask again") || strings.Contains(label, "do not ask again") {
				continue
			}
			if goYoloPositiveOption.MatchString(option.Label) {
				return option.Index
			}
		} else if action == yolo.Decline && goYoloNegativeOption.MatchString(option.Label) {
			return option.Index
		}
	}
	return 0
}

func (s *server) goYoloApprovalCycle(ctx context.Context, session, target, expectedIdentity string, agent map[string]any, prompt yolo.Prompt) error {
	key := session + "\x00" + target
	s.yolo.mu.Lock()
	lastHash := s.yolo.lastPrompt[key]
	lastAt := s.yolo.lastPromptAt[key]
	s.yolo.mu.Unlock()
	rulesPath, err := s.goYoloRulesPath()
	if err != nil {
		s.goYoloRecordPrompt(session, key, prompt.Hash, "suspended: YO settings could not be read", err.Error(), false, false)
		s.publishGoYoloState(session)
		return nil
	}
	decision := yolo.LoadAndEvaluate(rulesPath, prompt.RuleInput, s.dangerouslyYolo)
	activeAction := decision.Action == yolo.Approve || decision.Action == yolo.Decline
	if prompt.Hash == lastHash && !lastAt.IsZero() && (!activeAction || time.Since(lastAt) < goYoloApprovalRetryDelay) {
		return nil
	}
	if decision.Error != "" && decision.Action == yolo.Ask {
		s.goYoloRecordPrompt(session, key, prompt.Hash, "suspended: YO rules could not be loaded", decision.Error, false, false)
		s.publishGoYoloState(session)
		return nil
	}
	dryRun, err := s.goYoloDryRun()
	if err != nil {
		s.goYoloRecordPrompt(session, key, prompt.Hash, "suspended: YO settings could not be read", err.Error(), false, false)
		s.publishGoYoloState(session)
		return nil
	}
	if dryRun && (decision.Action == yolo.Approve || decision.Action == yolo.Decline) {
		s.goYoloRecordPrompt(session, key, prompt.Hash, "dry-run: would "+string(decision.Action)+" "+string(prompt.Type)+" prompt by rule "+decision.RuleName, "", false, false)
		s.publishGoYoloState(session)
		return nil
	}
	if decision.Action != yolo.Approve && decision.Action != yolo.Decline {
		action := "manual approval required"
		if decision.Action == yolo.Block {
			action = "blocked approval prompt"
		}
		if decision.Action == yolo.Notify {
			action = "notified about approval prompt"
		}
		if decision.Action == yolo.Off {
			action = "YO disabled by rule for approval prompt"
		}
		s.goYoloRecordPrompt(session, key, prompt.Hash, action+": "+decision.RuleName, "", false, false)
		s.yolo.mu.Lock()
		if decision.Action == yolo.Block {
			s.yolo.blocked[session]++
		}
		s.yolo.mu.Unlock()
		s.publishGoYoloState(session)
		return nil
	}
	desiredOption := goYoloActionOption(prompt, decision.Action)
	if desiredOption == 0 {
		s.goYoloRecordPrompt(session, key, prompt.Hash, "suspended: approval has no unambiguous "+string(decision.Action)+" option", "", false, false)
		s.publishGoYoloState(session)
		return nil
	}
	sent, confirmed, sendErr := s.goYoloSubmitApproval(ctx, session, target, expectedIdentity, agent, prompt, desiredOption)
	if sendErr != nil {
		s.goYoloRecordPrompt(session, key, prompt.Hash, "approval submission failed", sendErr.Error(), sent, false)
		s.publishGoYoloState(session)
		return sendErr
	}
	if !sent {
		return nil
	}
	verb := "approved"
	if decision.Action == yolo.Decline {
		verb = "declined"
	}
	message := verb + " " + string(prompt.Type) + " prompt by rule " + decision.RuleName
	if !confirmed {
		message = "approval submission sent; agent response not yet confirmed"
	}
	s.goYoloRecordPrompt(session, key, prompt.Hash, message, "", true, confirmed)
	s.yolo.mu.Lock()
	if confirmed {
		if decision.Action == yolo.Approve {
			s.yolo.approved[session]++
		} else if decision.Action == yolo.Decline {
			s.yolo.declined[session]++
		}
	}
	s.yolo.mu.Unlock()
	s.publishGoYoloState(session)
	return nil
}

func (s *server) goYoloRecordPrompt(session, key, hash, action, failure string, attempted, succeeded bool) {
	s.yolo.record(session, action, failure, attempted, succeeded)
	s.yolo.mu.Lock()
	s.yolo.lastPrompt[key] = hash
	s.yolo.lastPromptAt[key] = time.Now()
	s.yolo.mu.Unlock()
	eventType := "yolo_prompt_observed"
	if failure != "" {
		eventType = "yolo_action_failed"
	} else if attempted && succeeded {
		eventType = "yolo_action_succeeded"
	} else if attempted {
		eventType = "yolo_action_attempted"
	}
	s.publishGoYoloAuditEvent(session, eventType, action, map[string]any{"error": failure, "attempted": attempted, "succeeded": succeeded})
}

func (s *server) goYoloSubmitApproval(ctx context.Context, session, target, expectedIdentity string, agent map[string]any, expected yolo.Prompt, desiredOption int) (bool, bool, error) {
	if ctx.Err() != nil || !s.yolo.isEnabled(session) {
		return false, false, nil
	}
	current, err := s.goYoloCapture(ctx, target)
	if err != nil {
		return false, false, err
	}
	prompt := yolo.ClassifyPrompt(current, "")
	if !prompt.Actionable || prompt.Hash != expected.Hash || prompt.SelectedOption != expected.SelectedOption {
		return false, false, nil
	}
	selectedPosition := goYoloOptionPosition(prompt.Options, prompt.SelectedOption)
	desiredPosition := goYoloOptionPosition(prompt.Options, desiredOption)
	if selectedPosition < 0 || desiredPosition < 0 || len(prompt.Options) > 6 {
		return false, false, nil
	}
	for selectedPosition != desiredPosition {
		if ctx.Err() != nil || !s.yolo.isEnabled(session) {
			return false, false, nil
		}
		keyName := "Down"
		direction := 1
		if desiredPosition < selectedPosition {
			keyName = "Up"
			direction = -1
		}
		previousPosition := selectedPosition
		sent, sendErr := s.goYoloGuardedSendKeys(ctx, session, target, expectedIdentity, agent, []string{keyName}, []string{"idle", "needs-input"}, func(screen string) bool {
			currentPrompt := yolo.ClassifyPrompt(screen, "")
			return currentPrompt.Actionable && currentPrompt.Hash == expected.Hash && goYoloOptionPosition(currentPrompt.Options, currentPrompt.SelectedOption) == previousPosition
		})
		if sendErr != nil {
			return false, false, fmt.Errorf("move approval selection: %w", sendErr)
		}
		if !sent.keysSent {
			return false, false, nil
		}
		var moved bool
		prompt, moved, err = s.goYoloWaitForSelection(ctx, target, expected.Hash, previousPosition, direction)
		if err != nil {
			return false, false, err
		}
		if !moved {
			return false, false, nil
		}
		selectedPosition = goYoloOptionPosition(prompt.Options, prompt.SelectedOption)
	}
	sent, err := s.goYoloGuardedSendKeys(ctx, session, target, expectedIdentity, agent, []string{"Enter"}, []string{"idle", "needs-input"}, func(screen string) bool {
		finalPrompt := yolo.ClassifyPrompt(screen, "")
		return finalPrompt.Actionable && finalPrompt.Hash == expected.Hash && finalPrompt.SelectedOption == desiredOption
	})
	if err != nil {
		return false, false, fmt.Errorf("submit approval option: %w", err)
	}
	if !sent.keysSent {
		return false, false, nil
	}
	response, responseErr := s.goYoloCapture(ctx, target)
	if responseErr != nil {
		return true, false, nil
	}
	responsePrompt := yolo.ClassifyPrompt(response, "")
	responseState, responseFresh := s.goYoloAgentStatusNow(ctx, agent)
	confirmed := responseFresh && (responseState == "working" || responseState == "idle" && !responsePrompt.Approval && !responsePrompt.Question)
	return true, confirmed, nil
}

func (s *server) goYoloWaitForSelection(ctx context.Context, target, expectedHash string, previousPosition, direction int) (yolo.Prompt, bool, error) {
	ticker := time.NewTicker(20 * time.Millisecond)
	defer ticker.Stop()
	timer := time.NewTimer(300 * time.Millisecond)
	defer timer.Stop()
	for {
		current, err := s.goYoloCapture(ctx, target)
		if err != nil {
			return yolo.Prompt{}, false, err
		}
		prompt := yolo.ClassifyPrompt(current, "")
		if !prompt.Actionable || prompt.Hash != expectedHash {
			return yolo.Prompt{}, false, nil
		}
		position := goYoloOptionPosition(prompt.Options, prompt.SelectedOption)
		if position == previousPosition+direction {
			return prompt, true, nil
		}
		if position < 0 || position != previousPosition {
			return yolo.Prompt{}, false, nil
		}
		select {
		case <-ctx.Done():
			return yolo.Prompt{}, false, ctx.Err()
		case <-timer.C:
			return yolo.Prompt{}, false, nil
		case <-ticker.C:
		}
	}
}

func goYoloOptionPosition(options []yolo.PromptOption, index int) int {
	for position, option := range options {
		if option.Index == index {
			return position
		}
	}
	return -1
}

func identityErrString(err error) string {
	if err == nil {
		return ""
	}
	return err.Error()
}

// restoreGoYoloWorkers reconciles persisted state before the HTTP server starts. A saved toggle
// cannot appear active unless this build owns the capability and a worker can be started against
// the same live tmux incarnation.
func (s *server) restoreGoYoloWorkers(ctx context.Context) {
	if s.yolo == nil {
		return
	}
	for _, session := range s.yolo.sessions() {
		unlockTransition := s.yolo.lockTransition(session)
		if s.capabilities == nil || !s.capabilities.Available("yolo.auto-approve.v1") {
			if err := s.yolo.set(session, false, ""); err != nil {
				slog.Error("cannot clear unavailable persisted YO state", "session", session, "error", err)
			}
			unlockTransition()
			continue
		}
		expectedIdentity := s.yolo.identity(session)
		identity, err := s.goYoloSessionIdentity(ctx, session)
		if err != nil || identity == "" {
			s.yolo.record(session, "suspended: tmux identity could not be verified", identityErrString(err), false, false)
			if startErr := s.startGoYoloWorker(session); startErr != nil {
				s.yolo.record(session, "worker start failed", startErr.Error(), false, false)
				slog.Warn("cannot restore YO worker", "session", session, "error", startErr)
			}
			unlockTransition()
			continue
		}
		if identity != expectedIdentity {
			if disableErr := s.yolo.setIfIdentity(session, expectedIdentity, false, ""); disableErr != nil {
				s.yolo.record(session, "suspended: changed tmux identity could not be cleared", disableErr.Error(), false, false)
				if startErr := s.startGoYoloWorker(session); startErr != nil {
					s.yolo.record(session, "worker start failed", startErr.Error(), false, false)
					slog.Warn("cannot restore YO worker after identity mismatch", "session", session, "error", startErr)
				}
			} else {
				s.yolo.record(session, "disabled because the tmux session identity changed", "", false, false)
				s.publishGoYoloState(session)
			}
			unlockTransition()
			continue
		}
		if err := s.startGoYoloWorker(session); err != nil {
			s.yolo.record(session, "worker start failed", err.Error(), false, false)
			slog.Warn("cannot restore YO worker", "session", session, "error", err)
		}
		unlockTransition()
	}
}

func (s *server) goYoloSessionExists(ctx context.Context, session string) bool {
	commandContext, cancel := context.WithTimeout(ctx, goYoloCaptureTimeout)
	defer cancel()
	return s.tmuxCommandContext(commandContext, "has-session", "-t", s.exactTarget(session)).Run() == nil
}

func (s *server) goYoloSessionMissingConfirmed(ctx context.Context, session string) bool {
	commandContext, cancel := context.WithTimeout(ctx, goYoloCaptureTimeout)
	defer cancel()
	if err := s.tmuxCommandContext(commandContext, "show-options", "-s", "-g").Run(); err != nil {
		return false
	}
	output, err := s.tmuxCommandContext(commandContext, "list-sessions", "-F", "#{session_name}").Output()
	if err != nil {
		return false
	}
	for _, name := range strings.Split(strings.TrimSpace(string(output)), "\n") {
		if name == session {
			return false
		}
	}
	return true
}

func (s *server) goYoloSessionIdentity(ctx context.Context, session string) (string, error) {
	commandContext, cancel := context.WithTimeout(ctx, goYoloCaptureTimeout)
	defer cancel()
	output, err := s.tmuxCommandContext(commandContext, "display-message", "-p", "-t", s.exactTarget(session), "#{socket_path}\t#{session_id}\t#{session_created}\t#{pid}").Output()
	if err != nil {
		return "", err
	}
	identity := strings.TrimSpace(string(output))
	if identity == "" {
		return "", errors.New("tmux returned an empty session identity")
	}
	return identity, nil
}

func (s *server) goYoloPaneSessionIdentity(ctx context.Context, target string) (string, error) {
	commandContext, cancel := context.WithTimeout(ctx, goYoloCaptureTimeout)
	defer cancel()
	output, err := s.tmuxCommandContext(commandContext, "display-message", "-p", "-t", target, "#{socket_path}\t#{session_id}\t#{session_created}\t#{pid}").Output()
	if err != nil {
		return "", err
	}
	identity := strings.TrimSpace(string(output))
	if identity == "" {
		return "", errors.New("tmux returned an empty pane session identity")
	}
	return identity, nil
}

func (s *server) goYoloCapture(ctx context.Context, target string) (string, error) {
	commandContext, cancel := context.WithTimeout(ctx, goYoloCaptureTimeout)
	defer cancel()
	output, err := s.tmuxCommandContext(commandContext, "capture-pane", "-t", target, "-p", "-J").Output()
	if err != nil {
		return "", err
	}
	return string(output), nil
}

func (s *server) autoApprove(w http.ResponseWriter, r *http.Request) {
	if s.yolo == nil {
		jsonResponse(w, http.StatusServiceUnavailable, map[string]any{"error": "YO manager unavailable"})
		return
	}
	session := strings.TrimSpace(r.URL.Query().Get("session"))
	if r.Method == http.MethodGet {
		if session != "" {
			jsonResponse(w, http.StatusOK, s.goYoloSessionPayload(session))
			return
		}
		topology, _ := s.goYoloReadTopologySnapshot()
		projection := s.currentAgentWindowProjection(r.Context(), topology)
		sessionStates := make(map[string]any, len(topology.sessions))
		for _, name := range topology.sessions {
			sessionStates[name] = s.goYoloSessionPayload(name, &projection)
		}
		jsonResponse(w, http.StatusOK, map[string]any{
			"session_order": topology.sessions, "sessions": sessionStates,
			"rules": s.goYoloPolicyStatus(), "backend": "go", "topology_generation": topology.generation,
			"agent_window_snapshot_revision": projection.revision, "agent_window_status_fresh": projection.fresh,
		})
		return
	}
	if r.Method != http.MethodPost {
		w.Header().Set("Allow", "GET, POST")
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	if session == "" {
		jsonResponse(w, http.StatusBadRequest, map[string]any{"error": "session is required"})
		return
	}
	unlockTransition := s.yolo.lockTransition(session)
	defer unlockTransition()
	enabledValue := r.URL.Query().Get("enabled")
	if enabledValue != "0" && enabledValue != "1" {
		jsonResponse(w, http.StatusBadRequest, map[string]any{"error": "enabled must be 0 or 1"})
		return
	}
	enabled := enabledValue == "1"
	ctx, cancel := context.WithTimeout(r.Context(), goYoloCaptureTimeout)
	defer cancel()
	if enabled && !s.goYoloSessionExists(ctx, session) {
		jsonResponse(w, http.StatusNotFound, map[string]any{"session": session, "enabled": false, "error": "unknown session"})
		return
	}
	identity := ""
	if enabled {
		var identityErr error
		identity, identityErr = s.goYoloSessionIdentity(ctx, session)
		if identityErr != nil {
			jsonResponse(w, http.StatusServiceUnavailable, map[string]any{"session": session, "enabled": false, "error": "session identity unavailable"})
			return
		}
	}
	wasEnabled := s.yolo.isEnabled(session)
	previousIdentity := s.yolo.identity(session)
	if enabled {
		if wasEnabled && previousIdentity != identity {
			s.stopGoYoloWorker(session)
		}
		if err := s.yolo.set(session, true, identity); err != nil {
			if wasEnabled && previousIdentity != identity {
				_ = s.startGoYoloWorker(session)
			}
			jsonResponse(w, http.StatusInternalServerError, map[string]any{"session": session, "enabled": wasEnabled, "error": err.Error()})
			return
		}
		if err := s.startGoYoloWorker(session); err != nil {
			s.yolo.mu.Lock()
			lockOwner := s.yolo.lockOwners[session]
			s.yolo.mu.Unlock()
			rollbackIdentity := ""
			if wasEnabled {
				rollbackIdentity = previousIdentity
			}
			if rollbackErr := s.yolo.set(session, wasEnabled, rollbackIdentity); rollbackErr != nil {
				if lockOwner != nil {
					s.yolo.mu.Lock()
					s.yolo.lockOwners[session] = lockOwner
					s.yolo.mu.Unlock()
				}
				s.publishGoYoloState(session)
				jsonResponse(w, http.StatusInternalServerError, map[string]any{"session": session, "enabled": s.yolo.isEnabled(session), "error": err.Error(), "rollback_error": rollbackErr.Error()})
				return
			}
			if lockOwner != nil {
				s.yolo.mu.Lock()
				s.yolo.lockOwners[session] = lockOwner
				s.yolo.mu.Unlock()
			}
			if wasEnabled {
				_ = s.startGoYoloWorker(session)
			}
			s.publishGoYoloState(session)
			payload := s.goYoloSessionPayload(session)
			payload["error"] = err.Error()
			jsonResponse(w, http.StatusConflict, payload)
			return
		}
	} else {
		s.stopGoYoloWorker(session)
		if err := s.yolo.set(session, false, ""); err != nil {
			if wasEnabled {
				_ = s.yolo.set(session, true, previousIdentity)
				_ = s.startGoYoloWorker(session)
			}
			jsonResponse(w, http.StatusInternalServerError, map[string]any{"session": session, "enabled": s.yolo.isEnabled(session), "error": err.Error()})
			return
		}
	}
	s.publishGoYoloState(session)
	jsonResponse(w, http.StatusOK, s.goYoloSessionPayload(session))
}

// YO status readers can use the same fresh cached topology as session metadata;
// they must not wait for an unrelated forced roster fetch to finish.
func (s *server) goYoloReadTopologySnapshot() (goTmuxTopology, error) {
	if s.tmuxSessionRosterFresh() {
		if topology, known := s.cachedTmuxTopologySnapshot(); known {
			return topology, nil
		}
	}
	return s.tmuxTopologySnapshot()
}

func (s *server) goYoloSessionPayload(session string, projections ...*goAgentWindowProjection) map[string]any {
	var projection goAgentWindowProjection
	if len(projections) > 0 {
		projection = *projections[0]
	} else {
		topology, _ := s.goYoloReadTopologySnapshot()
		statusContext, cancel := context.WithTimeout(context.Background(), goYoloCaptureTimeout)
		projection = s.currentAgentWindowProjection(statusContext, topology)
		cancel()
	}
	topologyGeneration := projection.topologyGeneration
	payload := map[string]any{
		"target": session, "session": session, "enabled": s.yolo != nil && s.yolo.isEnabled(session),
		"approved": 0, "declined": 0, "blocked": 0, "backend": "go", "worker_state": "off", "topology_generation": topologyGeneration,
	}
	if payload["enabled"] == true {
		payload["worker_state"] = "starting"
		payload["last_action"] = "starting"
	} else {
		payload["last_action"] = "off"
	}
	if s.yolo != nil {
		s.yolo.mu.Lock()
		if action := s.yolo.lastAction[session]; action != "" {
			payload["last_action"] = action
			switch {
			case strings.HasPrefix(action, "dry-run:"):
				payload["worker_state"] = "dry-run"
			case strings.HasPrefix(action, "suspended:"):
				payload["worker_state"] = "suspended"
			case strings.Contains(action, "not yet confirmed"):
				payload["worker_state"] = "awaiting-confirmation"
			case strings.HasSuffix(action, "failed"):
				payload["worker_state"] = "failed"
			}
		}
		payload["error"] = s.yolo.lastError[session]
		payload["approved"] = s.yolo.approved[session]
		payload["declined"] = s.yolo.declined[session]
		payload["blocked"] = s.yolo.blocked[session]
		payload["attempts"] = s.yolo.attempts[session]
		payload["failures"] = s.yolo.failures[session]
		if value := s.yolo.lastAttempt[session]; !value.IsZero() {
			payload["last_attempt_at"] = float64(value.UnixNano()) / 1e9
		}
		if value := s.yolo.lastSuccess[session]; !value.IsZero() {
			payload["last_success_at"] = float64(value.UnixNano()) / 1e9
		}
		if owner := s.yolo.lockOwners[session]; owner != nil {
			payload["worker_state"] = "locked"
			payload["locked"] = true
			payload["lock_owner"] = owner
			payload["last_action"] = goYoloLockMessage(owner)
		} else if worker := s.yolo.workers[session]; worker != nil && payload["enabled"] == true {
			if payload["worker_state"] == "starting" {
				payload["worker_state"] = "watching"
			}
			payload["lock_owner"] = worker.owner
		}
		s.yolo.mu.Unlock()
	}
	payload["agent_windows"] = []any{}
	if rows, exists := projection.windows[session]; exists {
		payload["agent_windows"] = rows
	}
	payload["agent_window_snapshot_revision"] = projection.revision
	payload["agent_window_status_fresh"] = projection.fresh
	return payload
}

func (s *server) goYoloTopologyGeneration() uint64 {
	s.metadataTopologyMu.Lock()
	defer s.metadataTopologyMu.Unlock()
	return s.metadataTopologyGeneration
}

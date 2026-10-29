// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"context"
	"errors"
	"fmt"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/keivenchang/yomux/internal/stats"
)

// Input clocks use tmux identities rather than names or window ordinals. Renames
// and renumbering retain activity, and a new target cannot inherit a retired clock.
type terminalActivityState struct {
	mu     sync.Mutex
	clocks map[string]float64
}

type tabberActivityState struct {
	mu     sync.Mutex
	clocks map[string]float64
}

// A pane repaint is output too: attaching or resizing an idle TUI advances
// tmux's window_activity. Conversation dates must not inherit that clock.
func (s *server) mergeAgentConversationActivity(ctx context.Context, activity map[string]any, sessions map[string]any) error {
	clocks := make(map[string]float64)
	for session, raw := range sessions {
		info, _ := raw.(map[string]any)
		agents, _ := info["agents"].([]map[string]any)
		for _, agent := range agents {
			index, ok := agent["window_index"].(int)
			if ok && index >= 0 {
				clocks[session+":"+strconv.Itoa(index)] = 0
			}
		}
	}
	if len(clocks) == 0 {
		return nil
	}
	// Failed validation leaves history unavailable, never a recent repaint.
	defer func() {
		for key, ts := range clocks {
			row, _ := activity[key].(map[string]any)
			if row == nil {
				row = map[string]any{}
				activity[key] = row
			}
			row["active_recency_ts"] = ts
		}
	}()
	if s.agentProcessSnapshot == nil || s.processEntriesFn == nil {
		return errors.New("conversation activity process reader unavailable")
	}
	output, err := s.tmuxCommandContext(ctx, "list-panes", "-a", "-F", "#{session_name}\t#{pane_id}\t#{window_index}\t#{pane_pid}").Output()
	if err != nil {
		return fmt.Errorf("conversation activity pane inventory: %w", err)
	}
	processes, err := s.processEntriesFn()
	if err != nil {
		return fmt.Errorf("conversation activity process inventory: %w", err)
	}
	panes := make(map[string][]string)
	for _, line := range strings.Split(strings.TrimSpace(string(output)), "\n") {
		fields := strings.Split(line, "\t")
		if len(fields) == 4 {
			panes[fields[0]+"/"+fields[1]] = fields[2:]
		}
	}
	clocks = make(map[string]float64)
	var validationErrors []error
	for session, raw := range sessions {
		info, _ := raw.(map[string]any)
		agents, _ := info["agents"].([]map[string]any)
		for _, agent := range agents {
			kind := stringValue(agent["kind"])
			if kind != "codex" && kind != "claude" && kind != "opencode" {
				continue
			}
			pane := panes[session+"/"+stringValue(agent["pane_target"])]
			if len(pane) != 2 {
				continue
			}
			key := session + ":" + pane[0]
			panePID, err := strconv.Atoi(pane[1])
			if err != nil {
				clocks[key] = max(clocks[key], 0)
				validationErrors = append(validationErrors, errors.New("conversation activity pane process ID invalid"))
				continue
			}
			pidValue, _ := settingsNumber(agent["pid"])
			pid := int(pidValue)
			process, err := s.agentProcessSnapshot(ctx, pid)
			identity := stats.AgentStatusProcessInstanceID(stats.AgentRecord{PID: pid, StartedIdentity: process.StartedIdentity})
			if err != nil || process.StartedIdentity == "" {
				clocks[key] = max(clocks[key], 0)
				validationErrors = append(validationErrors, fmt.Errorf("conversation activity process %d unavailable", pid))
				continue
			}
			if !process.Alive || identity != stringValue(agent["process_instance_id"]) || !goYoloProcessBelongsToPane(processes, panePID, pid) {
				continue
			}
			ts, _ := agent["last_used_ts"].(float64)
			clocks[key] = max(clocks[key], ts)
		}
	}
	return errors.Join(validationErrors...)
}

func recordActivityClock(clocks map[string]float64, key string, now float64) map[string]float64 {
	if clocks == nil {
		clocks = make(map[string]float64)
	}
	if _, exists := clocks[key]; !exists && len(clocks) >= 4096 {
		oldestKey := ""
		oldest := now
		for candidate, ts := range clocks {
			if ts < oldest {
				oldestKey, oldest = candidate, ts
			}
		}
		delete(clocks, oldestKey)
	}
	if now > clocks[key] {
		clocks[key] = now
	}
	return clocks
}

func (s *server) recordTabberActivity(item string) {
	now := float64(time.Now().UnixNano()) / 1e9
	s.tabberActivity.mu.Lock()
	defer s.tabberActivity.mu.Unlock()
	s.tabberActivity.clocks = recordActivityClock(s.tabberActivity.clocks, item, now)
}

func (s *server) mergeTabberActivity(activity map[string]any) {
	s.tabberActivity.mu.Lock()
	defer s.tabberActivity.mu.Unlock()
	for item, ts := range s.tabberActivity.clocks {
		row, _ := activity[item].(map[string]any)
		if row == nil {
			row = map[string]any{}
			activity[item] = row
		}
		current, _ := row["active_recency_ts"].(float64)
		if input, ok := row["last_user_input_ts"].(float64); ok && input > current {
			current = input
		}
		if ts > current {
			row["active_recency_ts"] = ts
			row["last_user_input_ts"] = ts
		}
	}
}

var terminalPassiveInput = regexp.MustCompile(`\x1b(?:\[[56](?:;[2-8])?~|\[<?[0-9;?=>]*[ -/]*[cRntyIOmM]|\][^\x07\x1b]*(?:\x07|\x1b\\)|P[^\x1b]*(?:\x1b\\))`)

var terminalModifiedF3 = regexp.MustCompile(`\x1b\[1;[2-8]R`)

func terminalInputIsActivity(data []byte, explicit ...bool) bool {
	text := string(data)
	// Legacy mouse coordinates are three raw bytes, which can accidentally form a UTF-8 rune.
	// Consume bytes before applying the rune-based terminal response expressions.
	var keyboard strings.Builder
	for {
		start := strings.Index(text, "\x1b[M")
		if start < 0 || start+6 > len(text) {
			keyboard.WriteString(text)
			break
		}
		keyboard.WriteString(text[:start])
		text = text[start+6:]
	}
	text = keyboard.String()
	if len(explicit) > 0 && explicit[0] {
		text = terminalModifiedF3.ReplaceAllString(text, "key")
	}
	text = terminalPassiveInput.ReplaceAllString(text, "")
	text = strings.ReplaceAll(text, "\x1b[200~", "")
	text = strings.ReplaceAll(text, "\x1b[201~", "")
	return text != ""
}

const terminalActivityFormat = "#{pid}\t#{session_id}\t#{session_created}\t#{window_id}\t#{session_name}\t#{window_index}"

func terminalActivityKeys(fields []string) (string, string) {
	return fields[0] + "/" + fields[1] + "/" + fields[2], fields[0] + "/" + fields[3]
}

func (s *server) recordTerminalInput(session string, pty *ptyPair) {
	now := float64(time.Now().UnixNano()) / 1e9
	ctx, cancel := context.WithTimeout(context.Background(), 250*time.Millisecond)
	defer cancel()
	// Only an exact attached client can identify the window that accepted input.
	// A missing client still permits session attribution, never a guessed window.
	var fields []string
	if pty != nil && pty.tty != "" {
		output, err := s.tmuxCommandContext(ctx, "list-clients", "-F", "#{client_tty}\t"+terminalActivityFormat).Output()
		if err == nil {
			for _, line := range strings.Split(strings.TrimSpace(string(output)), "\n") {
				parts := strings.Split(line, "\t")
				if len(parts) == 7 && parts[0] == pty.tty {
					if fields != nil {
						fields = nil
						break
					}
					fields = parts[1:]
				}
			}
		}
	}
	windowKnown := fields != nil
	if fields == nil {
		output, err := s.tmuxCommandContext(ctx, "display-message", "-p", "-t", s.exactTarget(session), terminalActivityFormat).Output()
		if err != nil {
			return
		}
		fields = strings.Split(strings.TrimSpace(string(output)), "\t")
	}
	if len(fields) != 6 || fields[0] == "" || fields[1] == "" {
		return
	}
	sessionKey, windowKey := terminalActivityKeys(fields)
	s.terminalActivity.mu.Lock()
	defer s.terminalActivity.mu.Unlock()
	keys := []string{sessionKey}
	if windowKnown && fields[3] != "" {
		keys = append(keys, windowKey)
	}
	for _, key := range keys {
		s.terminalActivity.clocks = recordActivityClock(s.terminalActivity.clocks, key, now)
	}
}

func (s *server) mergeTerminalInputActivity(activity map[string]any, sessions []string) {
	s.terminalActivity.mu.Lock()
	clocks := make(map[string]float64, len(s.terminalActivity.clocks))
	for key, ts := range s.terminalActivity.clocks {
		clocks[key] = ts
	}
	s.terminalActivity.mu.Unlock()
	if len(clocks) == 0 {
		return
	}
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	output, err := s.tmuxCommandContext(ctx, "list-windows", "-a", "-F", terminalActivityFormat).Output()
	if err != nil {
		return
	}
	merge := func(key string, inputTS float64) {
		if inputTS <= 0 {
			return
		}
		row, _ := activity[key].(map[string]any)
		if row == nil {
			row = map[string]any{}
			activity[key] = row
		}
		current, _ := row["active_recency_ts"].(float64)
		if inputTS > 0 {
			previousInput, _ := row["last_user_input_ts"].(float64)
			if inputTS > previousInput {
				row["last_user_input_ts"] = inputTS
			}
			if inputTS > current {
				row["active_recency_ts"] = inputTS
			}
		}
	}
	live := make(map[string]bool, len(sessions))
	for _, session := range sessions {
		live[session] = true
	}
	for _, line := range strings.Split(strings.TrimSpace(string(output)), "\n") {
		fields := strings.Split(line, "\t")
		if len(fields) != 6 {
			continue
		}
		sessionKey, windowKey := terminalActivityKeys(fields)
		session, window := fields[4], fields[4]+":"+fields[5]
		if !live[session] {
			continue
		}
		inputTS := clocks[windowKey]
		merge(window, inputTS)
		sessionTS := clocks[sessionKey]
		if inputTS > sessionTS {
			sessionTS = inputTS
		}
		merge(session, sessionTS)
	}
}

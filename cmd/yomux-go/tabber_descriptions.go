// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"bufio"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"os"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/keivenchang/yomux/internal/stats"
)

// Window options follow the physical tmux window across session renames and grouped views.
// Automatic publication is conditional inside tmux, which owns ordering across app instances.
func (s *server) saveTabberDescription(ctx context.Context, windowID, description string, manual bool) (string, error) {
	s.tabberDescriptionMu.Lock()
	defer s.tabberDescriptionMu.Unlock()
	value, err := json.Marshal(map[string]any{"text": description, "manual": manual})
	if err != nil {
		return "", err
	}
	// tmux versions escape dollar signs even in direct option values; JSON unicode keeps
	// the stored record valid and prevents command-parser environment expansion.
	encoded := strings.ReplaceAll(string(value), "$", `\u0024`)
	if manual {
		err = s.tmuxCommandContext(ctx, "set-option", "-w", "-t", windowID, "@yomux_description", encoded).Run()
	} else {
		command := "set-option -w -t " + shellQuote(windowID) + " @yomux_description " + shellQuote(encoded)
		err = s.tmuxCommandContext(ctx, "if-shell", "-F", "-t", windowID, "#{==:#{@yomux_description},}", command).Run()
	}
	if err != nil {
		return "", err
	}
	output, err := s.tmuxCommandContext(ctx, "show-options", "-wqv", "-t", windowID, "@yomux_description").Output()
	return strings.TrimSpace(string(output)), err
}

func tabberDescriptionFields(window map[string]any, raw string) {
	var value struct {
		Text   string `json:"text"`
		Manual bool   `json:"manual"`
	}
	if json.Unmarshal([]byte(raw), &value) == nil {
		window["description"] = value.Text
		window["description_manual"] = value.Manual
	}
}

func compactTabberDescription(text string) string {
	text = strings.Join(strings.Fields(text), " ")
	runes := []rune(text)
	if len(runes) > 96 {
		text = string(runes[:96])
	}
	return text
}

// Read the beginning, never the newest user turn: a later turn must not rename a conversation.
// Reuse the transcript decoder so Claude and Codex content blocks have one interpretation.
func firstTabberConversationDescription(path string) string {
	file, err := os.Open(path)
	if err != nil {
		return ""
	}
	defer file.Close()
	scanner := bufio.NewScanner(io.LimitReader(file, 512*1024))
	scanner.Buffer(make([]byte, 4096), 512*1024)
	first := ""
	for scanner.Scan() {
		var record map[string]any
		if json.Unmarshal(scanner.Bytes(), &record) != nil {
			continue
		}
		for _, key := range []string{"customTitle", "title", "summary"} {
			if text, ok := record[key].(string); ok && strings.TrimSpace(text) != "" {
				return compactTabberDescription(text)
			}
		}
		if first != "" {
			continue
		}
		for _, item := range transcriptItems(record) {
			if item["role"] != "user" {
				continue
			}
			text, _ := item["text"].(string)
			// Codex records injected context as user blocks ahead of the actual submitted prompt.
			if strings.HasPrefix(text, "# AGENTS.md") || strings.HasPrefix(text, "<environment_context>") {
				continue
			}
			first = compactTabberDescription(text)
			if first != "" {
				break
			}
		}
	}
	return first
}

func tabberConversationDescription(kind, path, sessionID string) string {
	if kind != "opencode" {
		return firstTabberConversationDescription(path)
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	database, err := stats.OpenOpenCodeReadOnly(ctx, path)
	if err != nil {
		return ""
	}
	defer database.Close()
	title, err := database.ReadConversationTitle(ctx, sessionID)
	if err != nil || strings.HasPrefix(title, "New session") {
		return ""
	}
	return compactTabberDescription(title)
}

func (s *server) tabberDescription(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	var request struct {
		Session     string `json:"session"`
		Window      string `json:"window"`
		WindowID    string `json:"window_id"`
		Description string `json:"description"`
	}
	if decodeJSONBody(r.Body, 4096, &request) != nil || !utf8.ValidString(request.Description) || utf8.RuneCountInString(request.Description) > 160 || strings.ContainsAny(request.Description, "\r\n\t\x00") {
		jsonResponse(w, http.StatusBadRequest, typedError("invalid description", "common.invalidRequest", false))
		return
	}
	index, err := strconv.Atoi(request.Window)
	window := strconv.Itoa(index)
	if err != nil || index < 0 || request.Session == "" || strings.TrimSpace(request.WindowID) == "" {
		jsonResponse(w, http.StatusBadRequest, typedError("invalid window target", "common.invalidRequest", false))
		return
	}
	s.tmuxRosterFetchMu.Lock()
	defer s.tmuxRosterFetchMu.Unlock()
	ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
	defer cancel()
	owned, err := s.tabberViewSessionOwned(ctx, request.Session)
	if err != nil {
		jsonResponse(w, http.StatusServiceUnavailable, typedError("could not verify tmux view ownership", "common.requestFailed", true))
		return
	}
	if owned {
		jsonResponse(w, http.StatusBadRequest, typedError("invalid window target", "common.invalidRequest", false))
		return
	}
	output, err := s.tmuxCommandContext(ctx, "display-message", "-p", "-t", s.exactTarget(request.Session)+window, "#{window_id}\t#{window_index}").Output()
	fields := strings.Split(strings.TrimSpace(string(output)), "\t")
	if err != nil || len(fields) != 2 || fields[1] != window {
		jsonResponse(w, http.StatusNotFound, typedError("tmux window not found", "common.requestFailed", false))
		return
	}
	if fields[0] != request.WindowID {
		jsonResponse(w, http.StatusConflict, typedError("tmux window changed before rename", "common.requestFailed", false))
		return
	}
	description := strings.TrimSpace(request.Description)
	if _, err := s.saveTabberDescription(ctx, fields[0], description, true); err != nil {
		jsonResponse(w, http.StatusServiceUnavailable, typedError("could not save description", "common.requestFailed", true))
		return
	}
	topology := s.refreshTmuxMetadataAfterMutationLocked()
	generation := s.queueMetadataRefresh(true, topology.sessions, topology.identities, nil, topology.generation)
	jsonResponse(w, http.StatusOK, map[string]any{"ok": true, "window_id": fields[0], "description": description,
		"cache": map[string]any{"pending_identity": map[string]any{"epoch": s.metadataEpoch(), "generation": generation}}})
}

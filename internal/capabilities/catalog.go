// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

// Package capabilities owns the implementation-neutral feature catalog exposed by the Go
// backend.  Route admission, bootstrap presentation, and diagnostics all read this catalog so a
// partially ported surface cannot accidentally become visible through one of those paths.
package capabilities

import (
	"fmt"
	"net/url"
	"strings"
	"sync"
	"time"
)

const ContractVersion = 1

type Reason struct {
	Code          string         `json:"code"`
	MessageKey    string         `json:"messageKey"`
	MessageParams map[string]any `json:"messageParams"`
}

type Entry struct {
	Available bool    `json:"available"`
	Degraded  bool    `json:"degraded,omitempty"`
	Reason    *Reason `json:"reason,omitempty"`
}

type Catalog struct {
	mu           sync.RWMutex
	epoch        string
	revision     uint64
	backendBuild string
	entries      map[string]Entry
}

// New returns the initial Go capability set. Finder is enabled only for route families owned and
// tested by the current branch.
func New(backendBuild string) *Catalog {
	entries := map[string]Entry{}
	for _, name := range []string{
		"core.bootstrap.v1",
		"core.diagnostics.v1",
		"terminal.websocket.v1",
		"terminal.tmux.v1",
		"session.roster.v1",
		"pane.finder.v1",
		"finder.read.v1",
		"finder.watch.v1",
		"session.create.v1",
		"session.kill.v1",
		"session.rename.v1",
		"terminal.status.v1",
		"finder.write.v1",
		"finder.search.v1",
		"finder.batch.v1",
		"finder.git.v1",
		"finder.preview.v1",
		"pane.tabber.v1",
		"pane.differ.v1",
		"pane.stats.v1",
		"pane.chat.v1",
		"pane.agent.v1",
	} {
		entries[name] = unavailable()
	}
	for _, name := range []string{
		"core.bootstrap.v1",
		"core.diagnostics.v1",
		"terminal.websocket.v1",
		"terminal.tmux.v1",
		"session.roster.v1",
		"pane.finder.v1",
		"finder.read.v1",
		"finder.watch.v1",
		"finder.write.v1",
		"finder.search.v1",
		"finder.batch.v1",
		"finder.preview.v1",
		"terminal.status.v1",
		"session.create.v1",
		"session.kill.v1",
		"session.rename.v1",
	} {
		entries[name] = Entry{Available: true}
	}
	return &Catalog{
		epoch:        fmt.Sprintf("cap-%d", time.Now().UnixNano()),
		revision:     1,
		backendBuild: backendBuild,
		entries:      entries,
	}
}

func unavailable() Entry {
	return Entry{Available: false, Reason: &Reason{
		Code:          "not_implemented",
		MessageKey:    "capability.finderUnavailable",
		MessageParams: map[string]any{},
	}}
}

func (c *Catalog) Available(name string) bool {
	c.mu.RLock()
	defer c.mu.RUnlock()
	entry, ok := c.entries[name]
	return ok && entry.Available
}

func (c *Catalog) Entry(name string) Entry {
	c.mu.RLock()
	defer c.mu.RUnlock()
	entry, ok := c.entries[name]
	if !ok {
		return unavailable()
	}
	return cloneEntry(entry)
}

// Set is used by lifecycle/dependency health transitions.  Revision changes are monotonic and
// only observable through the authenticated capabilities endpoint or a capabilities_changed event.
func (c *Catalog) Set(name string, entry Entry) bool {
	if strings.TrimSpace(name) == "" {
		return false
	}
	if !entry.Available && entry.Reason == nil {
		entry = unavailable()
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if current, ok := c.entries[name]; ok && current.Available == entry.Available && current.Degraded == entry.Degraded && sameReason(current.Reason, entry.Reason) {
		return false
	}
	c.entries[name] = cloneEntry(entry)
	c.revision++
	return true
}

func (c *Catalog) Snapshot() map[string]any {
	c.mu.RLock()
	defer c.mu.RUnlock()
	entries := make(map[string]Entry, len(c.entries))
	for name, entry := range c.entries {
		entries[name] = cloneEntry(entry)
	}
	return map[string]any{
		"contract": map[string]any{
			"version":      ContractVersion,
			"backendBuild": c.backendBuild,
			"epoch":        c.epoch,
			"revision":     c.revision,
		},
		"capabilities": entries,
	}
}

func (c *Catalog) Revision() uint64 {
	c.mu.RLock()
	defer c.mu.RUnlock()
	return c.revision
}

func cloneEntry(entry Entry) Entry {
	if entry.Reason == nil {
		return entry
	}
	params := map[string]any{}
	for key, value := range entry.Reason.MessageParams {
		params[key] = value
	}
	reason := *entry.Reason
	reason.MessageParams = params
	entry.Reason = &reason
	return entry
}

func sameReason(left, right *Reason) bool {
	if left == nil || right == nil {
		return left == right
	}
	return left.Code == right.Code && left.MessageKey == right.MessageKey && fmt.Sprint(left.MessageParams) == fmt.Sprint(right.MessageParams)
}

// RouteCapability maps browser-visible paths to the operation-level capability that admits them.
// It is intentionally method-aware: a read-only batch and a mutation do not share permission or
// rollout state merely because they use the same product prefix.
func RouteCapability(method, rawPath string) string {
	method = strings.ToUpper(strings.TrimSpace(method))
	path := rawPath
	if parsed, err := url.Parse(rawPath); err == nil {
		path = parsed.Path
	}
	if method == "GET" && (path == "/api/ping" || path == "/api/capabilities" || path == "/api/background/status" || path == "/api/system-status" || path == "/api/system-status/advanced" || path == "/api/logs" || path == "/api/client-events" || path == "/api/operations/ack") {
		return "core.diagnostics.v1"
	}
	if path == "/ws" || path == "/api/tmux" || path == "/api/tmux-session-exists" {
		return "terminal.websocket.v1"
	}
	if path == "/api/ensure-session" || path == "/api/create-session-plan" || path == "/api/create-session" {
		return "session.create.v1"
	}
	if path == "/api/kill-session" {
		return "session.kill.v1"
	}
	if path == "/api/rename-session" {
		return "session.rename.v1"
	}
	if path == "/api/transcripts" || path == "/api/session-metadata" || path == "/api/activity" || path == "/api/activity-summary" || strings.HasPrefix(path, "/api/session-files") {
		return "pane.tabber.v1"
	}
	if strings.HasPrefix(path, "/api/tmux-") || path == "/api/auto-approve" || path == "/api/context-items" || path == "/api/events" {
		return "terminal.status.v1"
	}
	if strings.HasPrefix(path, "/api/fs/") || path == "/api/watch/roots" || path == "/api/upload" || strings.HasPrefix(path, "/api/batch/") {
		switch path {
		case "/api/fs/diff", "/api/fs/git-history", "/api/fs/git-commit", "/api/batch/blame":
			return "finder.git.v1"
		case "/api/batch/zip", "/api/batch/html-preview":
			return "finder.preview.v1"
		case "/api/fs/write", "/api/fs/delete", "/api/fs/rename", "/api/fs/mkdir", "/api/upload":
			return "finder.write.v1"
		case "/api/fs/search", "/api/fs/search-stream", "/api/fs/index-status", "/api/fs/unindex", "/api/batch/search":
			return "finder.search.v1"
		case "/api/watch/roots", "/api/fs/watch-diff":
			return "finder.watch.v1"
		case "/api/fs/batch", "/api/batch/count":
			return "finder.batch.v1"
		default:
			return "finder.read.v1"
		}
	}
	if strings.HasPrefix(path, "/api/stats") || path == "/api/pricing-catalog" || path == "/api/pricing-catalog/refresh" {
		return "pane.stats.v1"
	}
	if strings.HasPrefix(path, "/api/yoagent/") || path == "/api/agent-auth" || path == "/api/activity-summary" {
		return "pane.agent.v1"
	}
	if strings.HasPrefix(path, "/api/chat/") {
		return "pane.chat.v1"
	}
	return ""
}

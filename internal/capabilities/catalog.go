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
		"core.settings.v1",
		"core.integrations.v1",
		"terminal.websocket.v1",
		"terminal.tmux.v1",
		"terminal.status.v1",
		"session.roster.v1",
		"pane.finder.v1",
		"finder.read.v1",
		"finder.watch.v1",
		"session.create.v1",
		"session.kill.v1",
		"session.rename.v1",
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
		"yolo.auto-approve.v1",
	} {
		entries[name] = unavailable()
	}
	for _, name := range []string{
		"core.bootstrap.v1",
		"core.diagnostics.v1",
		"terminal.websocket.v1",
		"terminal.tmux.v1",
		"terminal.status.v1",
		"session.roster.v1",
		"pane.finder.v1",
		"finder.read.v1",
		"finder.watch.v1",
		"finder.write.v1",
		"finder.search.v1",
		"finder.batch.v1",
		"finder.git.v1",
		"finder.preview.v1",
		"pane.differ.v1",
		"pane.tabber.v1",
		"pane.stats.v1",
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

// RoutePolicy owns method-sensitive role and capability admission. Handlers retain
// their wire-compatible method validation and response bodies.
type RoutePolicy struct {
	Method, Path, Role, Capability string
	Prefix                         bool
}

var routePolicies = []RoutePolicy{
	{"GET", "/api/ping", "readonly", "core.diagnostics.v1", false},
	{"GET", "/api/capabilities", "readonly", "core.diagnostics.v1", false},
	{"GET", "/api/background/status", "readonly", "core.diagnostics.v1", false},
	{"GET", "/api/system-status", "readonly", "core.diagnostics.v1", false},
	{"GET", "/api/system-status/advanced", "readonly", "core.diagnostics.v1", false},
	{"GET", "/api/logs", "readonly", "core.diagnostics.v1", false},
	{"GET", "/api/diagnostics/performance", "admin", "core.diagnostics.v1", false},
	{"GET", "/api/client-events", "readonly", "core.diagnostics.v1", false},
	{"GET", "/api/operations/ack", "readonly", "core.diagnostics.v1", false},
	{"POST", "/api/settings", "admin", "core.settings.v1", false},
	{"*", "/api/settings", "readonly", "core.settings.v1", false},
	{"*", "/api/update-status", "readonly", "core.integrations.v1", false},
	{"*", "/api/watched-prs", "readonly", "core.integrations.v1", false},
	{"*", "/ws", "readonly", "terminal.websocket.v1", false},
	{"*", "/api/tmux", "readonly", "terminal.websocket.v1", false},
	{"*", "/api/tmux-session-exists", "readonly", "terminal.websocket.v1", false},
	{"*", "/api/ensure-session", "admin", "session.create.v1", false},
	{"*", "/api/create-session-plan", "admin", "session.create.v1", false},
	{"*", "/api/create-session", "admin", "session.create.v1", false},
	{"*", "/api/kill-session", "admin", "session.kill.v1", false},
	{"*", "/api/rename-session", "admin", "session.rename.v1", false},
	{"*", "/api/session-metadata", "readonly", "core.bootstrap.v1", false},
	{"*", "/api/transcripts", "readonly", "core.bootstrap.v1", false},
	{"*", "/api/activity", "readonly", "pane.tabber.v1", false},
	{"*", "/api/activity-summary", "readonly", "pane.tabber.v1", false},
	{"POST", "/api/attention-ack", "admin", "terminal.status.v1", false},
	{"GET", "/api/auto-approve", "readonly", "yolo.auto-approve.v1", false},
	{"POST", "/api/auto-approve", "admin", "yolo.auto-approve.v1", false},
	{"GET", "/api/yolo-rules", "readonly", "yolo.auto-approve.v1", false},
	{"POST", "/api/yolo-rules/open", "admin", "yolo.auto-approve.v1", false},
	{"POST", "/api/yolo-rules/reload", "admin", "yolo.auto-approve.v1", false},
	{"*", "/api/transcript", "readonly", "terminal.status.v1", false},
	{"*", "/api/context", "readonly", "terminal.status.v1", false},
	{"*", "/api/context-items", "readonly", "terminal.status.v1", false},
	{"*", "/api/context-stream", "readonly", "terminal.status.v1", false},
	{"*", "/api/events", "readonly", "terminal.status.v1", false},
	{"*", "/api/fs/diff", "readonly", "finder.git.v1", false},
	{"*", "/api/fs/git-history", "readonly", "finder.git.v1", false},
	{"*", "/api/fs/git-history-stats-stream", "readonly", "finder.git.v1", false},
	{"*", "/api/fs/git-commit", "readonly", "finder.git.v1", false},
	{"*", "/api/batch/blame", "admin", "finder.git.v1", false},
	{"*", "/api/batch/zip", "admin", "finder.preview.v1", false},
	{"*", "/api/batch/html-preview", "admin", "finder.preview.v1", false},
	{"*", "/api/fs/write", "admin", "finder.write.v1", false},
	{"*", "/api/fs/delete", "admin", "finder.write.v1", false},
	{"*", "/api/fs/rename", "admin", "finder.write.v1", false},
	{"*", "/api/fs/mkdir", "admin", "finder.write.v1", false},
	{"*", "/api/upload", "admin", "finder.write.v1", false},
	{"*", "/api/fs/search", "readonly", "finder.search.v1", false},
	{"*", "/api/fs/search-stream", "readonly", "finder.search.v1", false},
	{"*", "/api/fs/index-status", "readonly", "finder.search.v1", false},
	{"*", "/api/fs/unindex", "admin", "finder.search.v1", false},
	{"*", "/api/batch/search", "readonly", "finder.search.v1", false},
	{"*", "/api/watch/roots", "admin", "finder.watch.v1", false},
	{"*", "/api/fs/watch-diff", "readonly", "finder.watch.v1", false},
	{"*", "/api/fs/batch", "admin", "finder.batch.v1", false},
	{"*", "/api/batch/count", "admin", "finder.batch.v1", false},
	{"*", "/api/pricing-catalog", "readonly", "pane.stats.v1", false},
	{"*", "/api/pricing-catalog/refresh", "admin", "pane.stats.v1", false},
	{"*", "/api/stats-refill", "admin", "pane.stats.v1", false},
	{"*", "/api/agent-auth", "readonly", "pane.agent.v1", false},
	{"*", "/api/tmux-window", "admin", "terminal.status.v1", false},
	{"*", "/api/operations/cancel", "admin", "", false},
	{"*", "/api/diagnostics/heap", "admin", "", false},
	{"*", "/api/diagnostics/performance", "admin", "", false},
	{"*", "/api/background/claim", "admin", "", false},
	{"*", "/api/operations/accept", "admin", "", false},
	{"*", "/api/operations/terminalize", "admin", "", false},
	{"*", "/api/session-files", "readonly", "finder.git.v1", true},
	{"*", "/api/tmux-", "readonly", "terminal.status.v1", true},
	{"*", "/api/fs/", "readonly", "finder.read.v1", true},
	{"*", "/api/batch/", "readonly", "finder.read.v1", true},
	{"*", "/api/stats", "readonly", "pane.stats.v1", true},
	{"*", "/api/yoagent/", "readonly", "pane.agent.v1", true},
	{"*", "/api/chat/", "readonly", "pane.chat.v1", true},
}

func Policy(method, rawPath string) RoutePolicy {
	method = strings.ToUpper(strings.TrimSpace(method))
	path := rawPath
	if parsed, err := url.Parse(rawPath); err == nil {
		path = parsed.Path
	}
	for _, policy := range routePolicies {
		if policy.Method != "*" && policy.Method != method {
			continue
		}
		if policy.Path == path || policy.Prefix && strings.HasPrefix(path, policy.Path) {
			return policy
		}
	}
	return RoutePolicy{Method: method, Path: path, Role: "readonly"}
}

func RouteCapability(method, rawPath string) string { return Policy(method, rawPath).Capability }

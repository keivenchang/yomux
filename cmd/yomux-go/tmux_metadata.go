// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"context"
	"log/slog"
	"net/http"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/keivenchang/yomux/internal/stats"
)

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
	agentStatusOverrides, statusErr := s.currentAgentStatusOverrides(r.Context())
	if lifecycleErr := s.applyAgentStatusLifecycle(metadataSessions, agentStatusOverrides); lifecycleErr != nil && statusErr == nil {
		statusErr = lifecycleErr
	}
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
	if statusErr != nil {
		responseErrors = append(responseErrors, statusErr.Error())
	}
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
			// Lifecycle fields belong to this response, not the immutable published agents.
			agents, _ := info["agents"].([]map[string]any)
			ownedAgents := make([]map[string]any, len(agents))
			for index, agent := range agents {
				ownedAgents[index] = cloneMap(agent)
			}
			info["agents"] = ownedAgents
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
	out, err := s.tmuxCommandContext(commandContext, "list-sessions", "-F", "#{session_name}\t#{session_id}\t#{session_created}\t#{pid}\t#{@yomux_view_source_id}\t#{@yomux_view_source}\t#{session_group}\t#{@yomux_view_window_id}").CombinedOutput()
	if err != nil {
		if stats.TmuxNoServerError(out) {
			s.rememberTmuxSessionRoster([]string{}, nil)
			return s.cachedTmuxSessionRosterSnapshot(), nil
		}
		return s.cachedTmuxSessionRosterSnapshot(), err
	}
	var result []string
	identities := map[string]string{}
	sessionIDs := map[string]struct{}{}
	sessionNamesByID := map[string]string{}
	tabberViewSources := map[string]string{}
	tabberViewSourceNames := map[string]string{}
	tabberViewGrouped := map[string]bool{}
	tabberViewWindows := map[string]string{}
	for _, line := range strings.Split(strings.TrimSuffix(string(out), "\n"), "\n") {
		fields := strings.SplitN(line, "\t", 8)
		if len(fields) == 0 || fields[0] == "" {
			continue
		}
		result = append(result, fields[0])
		if len(fields) > 1 && fields[1] != "" {
			sessionIDs[fields[1]] = struct{}{}
			sessionNamesByID[fields[1]] = fields[0]
		}
		if len(fields) > 3 {
			identities[fields[0]] = strings.Join(fields[1:4], "\x1f")
		}
		if len(fields) > 6 && strings.TrimSpace(fields[6]) != "" {
			tabberViewGrouped[fields[0]] = true
		}
		if len(fields) > 7 {
			tabberViewWindows[fields[0]] = strings.TrimSpace(fields[7])
		}
		if len(fields) > 5 && isOwnedTabberViewSession(fields[0], fields[4], fields[5]) {
			if len(fields) > 4 {
				tabberViewSources[fields[0]] = fields[4]
			}
			if len(fields) > 5 {
				tabberViewSourceNames[fields[0]] = fields[5]
			}
		}
	}
	// Roster discovery is authoritative independently of optional view maintenance.
	// One complete window inventory serves every view; a failed observation can
	// never justify killing a view or suppress a newly discovered real session.
	var sourceWindows map[string]map[string]bool
	if len(tabberViewSources) > 0 {
		windows, inventoryErr := s.tmuxCommandContext(commandContext, "list-windows", "-a", "-F", "#{session_id}\t#{window_id}").Output()
		if inventoryErr == nil {
			sourceWindows = make(map[string]map[string]bool)
			for _, line := range strings.Split(strings.TrimSpace(string(windows)), "\n") {
				fields := strings.Split(line, "\t")
				if len(fields) != 2 || !strings.HasPrefix(fields[0], "$") || !strings.HasPrefix(fields[1], "@") {
					sourceWindows = nil
					break
				}
				if sourceWindows[fields[0]] == nil {
					sourceWindows[fields[0]] = map[string]bool{}
				}
				sourceWindows[fields[0]][fields[1]] = true
			}
		} else {
			slog.Warn("Tabber window maintenance observation failed", "error", inventoryErr)
		}
	}
	for viewSession, sourceID := range tabberViewSources {
		windowID, observed := tabberViewWindows[viewSession]
		_, sourceLive := sessionIDs[sourceID]
		orphan := !sourceLive || observed && windowID == "" || observed && sourceWindows != nil && !sourceWindows[sourceID][windowID]
		if orphan {
			if killErr := s.tmuxCommandContext(commandContext, "kill-session", "-t", s.exactTarget(viewSession)).Run(); killErr != nil {
				slog.Warn("Tabber orphan retirement failed", "view", viewSession, "error", killErr)
			}
			continue
		}
		if sourceName := sessionNamesByID[sourceID]; sourceName != "" && sourceName != tabberViewSourceNames[viewSession] {
			if setErr := s.tmuxCommandContext(commandContext, "set-option", "-t", s.exactTarget(viewSession), "@yomux_view_source", sourceName).Run(); setErr != nil {
				slog.Warn("Tabber source name maintenance failed", "view", viewSession, "error", setErr)
			}
		}
	}

	visible := make([]string, 0, len(result))
	for _, name := range result {
		// Owned views and partially created grouped views must not leak into
		// session pickers; standalone user sessions with a similar name remain visible.
		_, owned := tabberViewSources[name]
		if owned || isTabberViewSessionName(name) && tabberViewGrouped[name] {
			continue
		}
		visible = append(visible, name)
	}
	result = normalizeTmuxSessionRoster(visible)
	s.rememberTmuxSessionRoster(result, identities)
	return s.cachedTmuxSessionRosterSnapshot(), nil
}

// refreshTmuxMetadataAfterMutationLocked confirms topology before the mutation
// response escapes. Readers and the event publisher then share its revision.
// The caller holds tmuxRosterFetchMu across the tmux command and this read.
func (s *server) refreshTmuxMetadataAfterMutationLocked() goTmuxTopology {
	topology, err := s.confirmTmuxTopologyAfterMutationLocked()
	if err != nil {
		slog.Warn("tmux topology refresh failed after mutation", "error", err)
	}
	s.queueMetadataRefresh(false, topology.sessions, topology.identities, err, topology.generation)
	return topology
}

func (s *server) confirmTmuxTopologyAfterMutationLocked() (goTmuxTopology, error) {
	s.tmuxRosterMu.Lock()
	s.tmuxRosterSampledAt = time.Time{}
	s.tmuxRosterMu.Unlock()
	roster, err := s.tmuxSessionRosterSnapshotLocked(true)
	return s.tmuxTopologyFromRoster(roster, err)
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

func isTabberViewSessionName(name string) bool {
	return strings.HasPrefix(strings.TrimSpace(name), stats.TabberViewSessionPrefix)
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

type tmuxMetadataState struct {
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

	tmuxRosterMu          sync.RWMutex
	tmuxRosterFetchMu     sync.Mutex
	tabberDescriptionMu   sync.Mutex
	tmuxRoster            []string
	tmuxRosterIdentities  map[string]string
	tmuxRosterKnown       bool
	tmuxRosterRevision    uint64
	tmuxRosterSampledAt   time.Time
	tmuxSessionRosterFn   func() ([]string, error)
	tmuxRosterWatchCancel context.CancelFunc
	tmuxRosterWatchDone   chan struct{}
	tmuxSessionActivityFn func() (map[string]float64, error)
}

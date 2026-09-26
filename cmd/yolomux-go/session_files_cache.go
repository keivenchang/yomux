// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"log/slog"
	"math"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/keivenchang/yolomux/internal/cache"
	"github.com/keivenchang/yolomux/internal/finder"
)

const sessionFilesCacheMaxEntries = 128
const sessionFilesCacheTTL = 30 * time.Second

type sessionFilesCacheRecord struct {
	RequestDescriptor string         `json:"request_descriptor"`
	CacheViewID       string         `json:"cache_view_id"`
	StoredAt          int64          `json:"stored_at"`
	Status            int            `json:"status"`
	SessionIdentity   string         `json:"session_identity,omitempty"`
	Payload           map[string]any `json:"payload"`
}

type sessionFilesCacheState struct {
	mu        sync.Mutex
	writeMu   sync.Mutex
	recencyMu sync.Mutex
	// publicationMu fences durable renames against generation changes without blocking cache hits.
	publicationMu sync.Mutex
	records       *cache.LRU[string, sessionFilesCacheRecord]
	generations   *cache.LRU[string, uint64]
	generation    uint64
	recencyTouch  map[string]chan struct{}
	refresh       map[string]uint64
}

func sessionFilesRequestDescriptor(request sessionFilesRequest) string {
	hours := request.hours
	if hours == math.Trunc(hours) {
		// Match the browser/Python descriptor's JSON number normalization: 24 and 24.0
		// identify the same logical session-files view.
		hours = math.Trunc(hours)
	}
	canonical := struct {
		Kind      string                       `json:"kind"`
		Session   string                       `json:"session"`
		Hours     float64                      `json:"hours"`
		FromRef   string                       `json:"from_ref"`
		ToRef     string                       `json:"to_ref"`
		RepoRefs  map[string]map[string]string `json:"repo_refs,omitempty"`
		RepoPaths []string                     `json:"repo_paths,omitempty"`
	}{
		Kind: "session-files-request", Session: request.session, Hours: hours,
		FromRef: request.fromRef, ToRef: request.toRef, RepoRefs: request.repoRefs,
		RepoPaths: append([]string(nil), request.repoPaths...),
	}
	encoded, _ := json.Marshal(canonical)
	digest := sha256.Sum256(encoded)
	return hex.EncodeToString(digest[:])
}

func sessionFilesCacheViewID(requestDescriptor string) string {
	digest := sha256.Sum256([]byte("session-files-view\x1f" + requestDescriptor))
	return hex.EncodeToString(digest[:])
}

func cloneSessionFilesPayload(payload map[string]any) map[string]any {
	// Copy once when a payload enters the cache; published nested rows are immutable to keep hits cheap.
	if payload == nil {
		return nil
	}
	encoded, err := json.Marshal(payload)
	if err != nil {
		return cloneFinderMap(payload)
	}
	var cloned map[string]any
	if json.Unmarshal(encoded, &cloned) != nil {
		return cloneFinderMap(payload)
	}
	return cloned
}

func sessionFilesCacheAge(record sessionFilesCacheRecord, now time.Time) float64 {
	if record.StoredAt <= 0 {
		return 0
	}
	age := now.Sub(time.Unix(0, record.StoredAt)).Seconds()
	if age < 0 {
		return 0
	}
	return age
}

func sessionFilesCacheMetadata(record sessionFilesCacheRecord, requestDescriptor, viewID string, hit, stale bool, now time.Time) map[string]any {
	return map[string]any{
		"hit":                hit,
		"stale":              stale,
		"age_seconds":        math.Round(sessionFilesCacheAge(record, now)*1000) / 1000,
		"refresh_seconds":    sessionFilesCacheTTL.Seconds(),
		"request_descriptor": requestDescriptor,
		"cache_view_id":      viewID,
	}
}

func (s *server) sessionFilesCacheDirectory() string {
	state := strings.TrimSpace(s.state)
	if state == "" {
		state = strings.TrimSpace(s.config)
	}
	if state == "" {
		return ""
	}
	// Refresh generations are process-local, so durable records must be isolated by machine,
	// checkout, and the actual listener address even when callers share a state directory.
	host := strings.TrimSpace(s.host)
	if host == "" {
		host = "127.0.0.1"
	}
	machine, err := os.Hostname()
	if err != nil || strings.TrimSpace(machine) == "" {
		return ""
	}
	listener := strings.TrimSpace(s.sessionFilesListener)
	if listener == "" {
		listener = host + ":" + strconv.Itoa(s.port)
	}
	hostHash := sha256.Sum256([]byte(strings.TrimSpace(machine) + "\x00" + strings.TrimSpace(s.root) + "\x00" + listener))
	return filepath.Join(state, "session-files-cache", "p"+strconv.Itoa(s.port)+"-"+hex.EncodeToString(hostHash[:8]))
}

func isSessionFilesCacheViewFile(name string) bool {
	if len(name) != 69 || !strings.HasSuffix(name, ".json") {
		return false
	}
	viewID := strings.TrimSuffix(name, ".json")
	return len(viewID) == 64 && strings.Trim(viewID, "0123456789abcdef") == ""
}

func evictSessionFilesCacheFiles(directory, preserveViewID string, recentlyUsed []string) error {
	entries, err := os.ReadDir(directory)
	if err != nil {
		return err
	}
	type cacheFile struct {
		name    string
		modTime time.Time
	}
	files := make([]cacheFile, 0, len(entries))
	for _, entry := range entries {
		if !isSessionFilesCacheViewFile(entry.Name()) {
			continue
		}
		info, err := entry.Info()
		if err != nil {
			return err
		}
		if info.Mode().IsRegular() {
			files = append(files, cacheFile{name: entry.Name(), modTime: info.ModTime()})
		}
	}
	if len(files) <= sessionFilesCacheMaxEntries {
		return nil
	}
	recentUseRank := make(map[string]int, len(recentlyUsed))
	for rank, viewID := range recentlyUsed {
		recentUseRank[viewID] = rank
	}
	sort.Slice(files, func(i, j int) bool {
		leftID := strings.TrimSuffix(files[i].name, ".json")
		rightID := strings.TrimSuffix(files[j].name, ".json")
		leftRank, leftKnown := recentUseRank[leftID]
		rightRank, rightKnown := recentUseRank[rightID]
		if leftKnown != rightKnown {
			return !leftKnown
		}
		if leftKnown && leftRank != rightRank {
			return leftRank < rightRank
		}
		if !files[i].modTime.Equal(files[j].modTime) {
			return files[i].modTime.Before(files[j].modTime)
		}
		return files[i].name < files[j].name
	})
	remaining := len(files)
	for _, file := range files {
		if remaining <= sessionFilesCacheMaxEntries {
			break
		}
		if strings.TrimSuffix(file.name, ".json") == preserveViewID {
			continue
		}
		if err := os.Remove(filepath.Join(directory, file.name)); err != nil && !os.IsNotExist(err) {
			return err
		}
		remaining--
	}
	return nil
}

func touchSessionFilesCacheFile(file *os.File) {
	// Durable eviction uses mtime as recency; the descriptor was opened without following links
	// and validated as a regular file before this call.
	recency := syscall.NsecToTimeval(time.Now().UnixNano())
	_ = syscall.Futimes(int(file.Fd()), []syscall.Timeval{recency, recency})
}

func (s *server) touchSessionFilesCacheView(viewID string) {
	directory := s.sessionFilesCacheDirectory()
	if directory == "" {
		return
	}
	// Persist memory-hit recency so a later process can make the same eviction choice.
	s.sessionFilesCache.recencyMu.Lock()
	defer s.sessionFilesCache.recencyMu.Unlock()
	s.sessionFilesCache.publicationMu.Lock()
	defer s.sessionFilesCache.publicationMu.Unlock()
	file, ok := openSessionFilesCacheFile(directory, viewID)
	if !ok {
		return
	}
	touchSessionFilesCacheFile(file)
	_ = file.Close()
}

func (s *server) scheduleSessionFilesCacheViewTouch(viewID string) <-chan struct{} {
	s.sessionFilesCache.mu.Lock()
	if done, pending := s.sessionFilesCache.recencyTouch[viewID]; pending {
		s.sessionFilesCache.mu.Unlock()
		return done
	}
	if s.sessionFilesCache.recencyTouch == nil {
		s.sessionFilesCache.recencyTouch = make(map[string]chan struct{})
	}
	if len(s.sessionFilesCache.recencyTouch) >= sessionFilesCacheMaxEntries {
		s.sessionFilesCache.mu.Unlock()
		return nil
	}
	done := make(chan struct{})
	s.sessionFilesCache.recencyTouch[viewID] = done
	s.sessionFilesCache.mu.Unlock()
	go func() {
		s.touchSessionFilesCacheView(viewID)
		s.sessionFilesCache.mu.Lock()
		if s.sessionFilesCache.recencyTouch[viewID] == done {
			delete(s.sessionFilesCache.recencyTouch, viewID)
			close(done)
		}
		s.sessionFilesCache.mu.Unlock()
	}()
	return done
}

func openSessionFilesCacheFile(directory, viewID string) (*os.File, bool) {
	if !isSessionFilesCacheViewFile(viewID + ".json") {
		return nil, false
	}
	path := filepath.Join(directory, viewID+".json")
	fileDescriptor, err := syscall.Open(path, syscall.O_RDONLY|syscall.O_CLOEXEC|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0)
	if err != nil {
		return nil, false
	}
	file := os.NewFile(uintptr(fileDescriptor), path)
	if file == nil {
		_ = syscall.Close(fileDescriptor)
		return nil, false
	}
	fileInfo, err := file.Stat()
	if err != nil || !fileInfo.Mode().IsRegular() {
		_ = file.Close()
		return nil, false
	}
	return file, true
}

func sessionFilesCacheRecordMatches(record sessionFilesCacheRecord, descriptor, viewID, sessionIdentity string) bool {
	return record.RequestDescriptor == descriptor && record.CacheViewID == viewID && record.StoredAt > 0 &&
		record.Status >= http.StatusOK && record.Status < http.StatusMultipleChoices && record.Payload != nil &&
		(sessionIdentity == "" || record.SessionIdentity == sessionIdentity)
}

func (s *server) readSessionFilesCache(request sessionFilesRequest, viewID string) (sessionFilesCacheRecord, bool) {
	record, ok, _ := s.readSessionFilesCacheWithSource(request, viewID)
	return record, ok
}

func (s *server) readSessionFilesCacheWithSource(request sessionFilesRequest, viewID string) (sessionFilesCacheRecord, bool, bool) {
	descriptor := sessionFilesRequestDescriptor(request)
	if len(viewID) != 64 || strings.Trim(viewID, "0123456789abcdef") != "" {
		return sessionFilesCacheRecord{}, false, false
	}
	s.sessionFilesCache.mu.Lock()
	if s.sessionFilesCache.records != nil {
		if record, ok := s.sessionFilesCache.records.Peek(viewID); ok && sessionFilesCacheRecordMatches(record, descriptor, viewID, request.sessionIdentity) {
			s.sessionFilesCache.records.Get(viewID)
			s.sessionFilesCache.mu.Unlock()
			_ = s.scheduleSessionFilesCacheViewTouch(viewID)
			return record, true, false
		}
	}
	s.sessionFilesCache.mu.Unlock()
	directory := s.sessionFilesCacheDirectory()
	if directory == "" {
		return sessionFilesCacheRecord{}, false, false
	}
	// Serialize durable reads and recency updates with eviction, but keep ordinary memory hits
	// independent of filesystem latency.
	s.sessionFilesCache.recencyMu.Lock()
	defer s.sessionFilesCache.recencyMu.Unlock()
	file, ok := openSessionFilesCacheFile(directory, viewID)
	if !ok {
		return sessionFilesCacheRecord{}, false, false
	}
	data, err := io.ReadAll(io.LimitReader(file, 16<<20))
	if err != nil {
		_ = file.Close()
		return sessionFilesCacheRecord{}, false, false
	}
	var diskRecord sessionFilesCacheRecord
	if json.Unmarshal(data, &diskRecord) != nil || !sessionFilesCacheRecordMatches(diskRecord, descriptor, viewID, request.sessionIdentity) {
		_ = file.Close()
		return sessionFilesCacheRecord{}, false, false
	}
	touchSessionFilesCacheFile(file)
	if err := file.Close(); err != nil {
		return sessionFilesCacheRecord{}, false, false
	}
	return s.publishLoadedSessionFilesCacheRecord(descriptor, viewID, diskRecord, request.sessionIdentity), true, true
}

func (s *server) publishLoadedSessionFilesCacheRecord(descriptor, viewID string, diskRecord sessionFilesCacheRecord, sessionIdentity string) sessionFilesCacheRecord {
	s.sessionFilesCache.mu.Lock()
	defer s.sessionFilesCache.mu.Unlock()
	if s.sessionFilesCache.records == nil {
		s.sessionFilesCache.records = cache.NewLRU[string, sessionFilesCacheRecord](sessionFilesCacheMaxEntries)
	}
	current, exists := s.sessionFilesCache.records.Peek(viewID)
	if exists {
		// The memory publication may have changed incarnation while the disk read
		// was in flight. Memory is the later publication; let the caller's identity
		// fence reject it if it does not match this request.
		s.sessionFilesCache.records.Get(viewID)
		return current
	}
	record := sessionFilesCacheRecord{
		RequestDescriptor: diskRecord.RequestDescriptor, CacheViewID: diskRecord.CacheViewID,
		StoredAt: diskRecord.StoredAt, Status: diskRecord.Status, SessionIdentity: diskRecord.SessionIdentity,
		Payload: diskRecord.Payload,
	}
	s.sessionFilesCache.records.Set(viewID, record)
	return record
}

func (s *server) readLiveSessionFilesCache(request sessionFilesRequest, viewID string) (sessionFilesRequest, sessionFilesCacheRecord, bool) {
	return readLiveSessionFilesCacheWith(request, viewID, s.sessionFilesSessionIdentity, s.readSessionFilesCacheWithSource)
}

func readLiveSessionFilesCacheWith(
	request sessionFilesRequest,
	viewID string,
	readIdentity func(string) (string, bool),
	readCache func(sessionFilesRequest, string) (sessionFilesCacheRecord, bool, bool),
) (sessionFilesRequest, sessionFilesCacheRecord, bool) {
	identity, ok := readIdentity(request.session)
	if !ok {
		return request, sessionFilesCacheRecord{}, false
	}
	request.sessionIdentity = identity
	record, hasRecord, loadedFromDisk := readCache(request, viewID)
	if !hasRecord || record.SessionIdentity != identity {
		return request, sessionFilesCacheRecord{}, false
	}
	if loadedFromDisk {
		currentIdentity, stillExists := readIdentity(request.session)
		if !stillExists || currentIdentity != identity {
			return request, sessionFilesCacheRecord{}, false
		}
	}
	return request, record, true
}

type sessionFilesCacheAdmission struct {
	request    sessionFilesRequest
	record     sessionFilesCacheRecord
	hit        bool
	stale      bool
	refreshing bool
}

func (s *server) admitLiveSessionFilesCache(request sessionFilesRequest, viewID string) sessionFilesCacheAdmission {
	admission := sessionFilesCacheAdmission{request: request}
	if request.freshGit || request.force {
		return admission
	}
	request, record, hit := s.readLiveSessionFilesCache(request, viewID)
	admission.request = request
	if !hit {
		return admission
	}
	admission.record = record
	admission.hit = true
	admission.stale = sessionFilesCacheAge(record, time.Now()) > sessionFilesCacheTTL.Seconds()
	if admission.stale {
		plan := newSessionFilesPlan(request)
		admission.refreshing = s.scheduleSessionFilesRefresh(request, viewID, s.sessionFilesRefreshTask(plan)) || s.sessionFilesRefreshInFlight(viewID)
	}
	return admission
}

func sessionFilesPayloadWithCache(record sessionFilesCacheRecord, requestDescriptor, viewID string, hit, stale, refreshing bool, now time.Time) map[string]any {
	// Cached rows stay immutable; only the response envelope needs a new top-level map.
	payload := cloneFinderMap(record.Payload)
	if payload == nil {
		payload = make(map[string]any)
	}
	metadata := sessionFilesCacheMetadata(record, requestDescriptor, viewID, hit, stale, now)
	if refreshing {
		metadata["refreshing"] = true
	}
	payload["cache"] = metadata
	return payload
}

func (s *server) nextSessionFilesCacheGenerationLocked(viewID string) uint64 {
	if s.sessionFilesCache.generations == nil {
		s.sessionFilesCache.generations = cache.NewLRU[string, uint64](sessionFilesCacheMaxEntries)
	}
	s.sessionFilesCache.generation++
	if s.sessionFilesCache.generation == 0 {
		s.sessionFilesCache.generation++
	}
	s.sessionFilesCache.generations.Set(viewID, s.sessionFilesCache.generation)
	return s.sessionFilesCache.generation
}

func (s *server) beginSessionFilesCacheWrite(viewID string) uint64 {
	s.sessionFilesCache.publicationMu.Lock()
	defer s.sessionFilesCache.publicationMu.Unlock()
	s.sessionFilesCache.mu.Lock()
	defer s.sessionFilesCache.mu.Unlock()
	return s.nextSessionFilesCacheGenerationLocked(viewID)
}

func (s *server) currentSessionFilesCacheGenerationLocked(viewID string) uint64 {
	if s.sessionFilesCache.generations == nil {
		return 0
	}
	generation, _ := s.sessionFilesCache.generations.Get(viewID)
	return generation
}

func (s *server) beginSessionFilesRefresh(viewID string) (uint64, bool) {
	s.sessionFilesCache.publicationMu.Lock()
	defer s.sessionFilesCache.publicationMu.Unlock()
	s.sessionFilesCache.mu.Lock()
	defer s.sessionFilesCache.mu.Unlock()
	if s.sessionFilesCache.refresh == nil {
		s.sessionFilesCache.refresh = make(map[string]uint64)
	}
	if s.sessionFilesCache.refresh[viewID] != 0 {
		return 0, false
	}
	generation := s.nextSessionFilesCacheGenerationLocked(viewID)
	s.sessionFilesCache.refresh[viewID] = generation
	return generation, true
}

func (s *server) finishSessionFilesRefresh(viewID string, generation uint64) {
	s.sessionFilesCache.mu.Lock()
	if s.sessionFilesCache.refresh[viewID] == generation {
		delete(s.sessionFilesCache.refresh, viewID)
	}
	s.sessionFilesCache.mu.Unlock()
}

func (s *server) sessionFilesRefreshInFlight(viewID string) bool {
	s.sessionFilesCache.mu.Lock()
	defer s.sessionFilesCache.mu.Unlock()
	return s.sessionFilesCache.refresh[viewID] != 0
}

func (s *server) sessionFilesRefreshTask(plan sessionFilesPlan) func(context.Context) (map[string]any, *finder.Error) {
	request := plan.request
	return func(ctx context.Context) (map[string]any, *finder.Error) {
		identity, alive := s.sessionFilesSessionIdentity(request.session)
		if !alive || identity != request.sessionIdentity {
			return nil, sessionFilesIncarnationChangedError(request.session)
		}
		roots, agents, finderErr := s.sessionFilesPlanInputs(plan)
		if finderErr != nil {
			return nil, finderErr
		}
		return s.sessionFilesPlanTaskWithInputs(plan, roots, agents)(ctx)
	}
}

func (s *server) publishSessionFilesRefreshCompleted(request sessionFilesRequest, viewID string, generation uint64) {
	if s.events == nil {
		return
	}
	_, _ = s.events.publish("background_refresh_done", map[string]any{
		"role": "session-files", "session": request.session,
		"cache_key_hash": viewID, "cache_view_id": viewID,
		"completion_generation": strconv.Itoa(os.Getpid()) + "." + strconv.FormatInt(s.startedAt.UnixNano(), 10) + "." + strconv.FormatUint(generation, 10),
		"request_descriptor":    sessionFilesRequestDescriptor(request),
	})
}

func (s *server) scheduleSessionFilesRefresh(request sessionFilesRequest, viewID string, task func(context.Context) (map[string]any, *finder.Error)) bool {
	generation, admitted := s.beginSessionFilesRefresh(viewID)
	if !admitted {
		return false
	}
	finish := func() { s.finishSessionFilesRefresh(viewID, generation) }
	parent, cancel := newFinderJobContext(context.Background(), finderJobDeadline)
	writeIfCurrent := func(payload map[string]any) {
		if request.session != "" && (request.sessionIdentity == "" || !s.sessionFilesIdentityMatches(request.session, request.sessionIdentity)) {
			return
		}
		if s.writeSessionFilesCache(request, viewID, payload, http.StatusOK, generation) {
			s.publishSessionFilesRefreshCompleted(request, viewID, generation)
		}
	}
	scheduler := s.finderBackgroundScheduler()
	if scheduler == nil {
		go func() {
			defer cancel()
			defer finish()
			payload, finderErr := task(parent)
			if finderErr == nil {
				writeIfCurrent(payload)
			}
		}()
		return true
	}
	result, err := scheduler.Submit(parent, "", func(ctx context.Context) (any, error) {
		payload, finderErr := task(ctx)
		if finderErr != nil {
			return finderErr, nil
		}
		return payload, nil
	})
	if err != nil {
		cancel()
		finish()
		return false
	}
	go func() {
		defer cancel()
		defer finish()
		completed := <-result
		if completed.Err != nil {
			return
		}
		payload, ok := completed.Value.(map[string]any)
		if ok {
			writeIfCurrent(payload)
		}
	}()
	return true
}

func sessionFilesCacheRecordMatchesRequestIdentity(request sessionFilesRequest, record sessionFilesCacheRecord) bool {
	return request.session == "" || (request.sessionIdentity != "" && record.SessionIdentity == request.sessionIdentity)
}

func (s *server) writeSessionFilesCache(request sessionFilesRequest, viewID string, payload map[string]any, status int, expectedGeneration ...uint64) bool {
	directory := s.sessionFilesCacheDirectory()
	if directory == "" || payload == nil || status < http.StatusOK || status >= http.StatusMultipleChoices || (request.session != "" && request.sessionIdentity == "") {
		return false
	}
	s.sessionFilesCache.writeMu.Lock()
	defer s.sessionFilesCache.writeMu.Unlock()
	s.sessionFilesCache.mu.Lock()
	var existing sessionFilesCacheRecord
	var exists bool
	if s.sessionFilesCache.records != nil {
		existing, exists = s.sessionFilesCache.records.Get(viewID)
	}
	s.sessionFilesCache.mu.Unlock()
	if exists && request.session != "" && existing.SessionIdentity != request.sessionIdentity && !s.sessionFilesIdentityMatches(request.session, request.sessionIdentity) {
		return false
	}
	record := sessionFilesCacheRecord{
		RequestDescriptor: sessionFilesRequestDescriptor(request), CacheViewID: viewID,
		StoredAt: time.Now().UnixNano(), Status: status, SessionIdentity: request.sessionIdentity, Payload: cloneSessionFilesPayload(payload),
	}
	s.sessionFilesCache.publicationMu.Lock()
	s.sessionFilesCache.mu.Lock()
	generation := uint64(0)
	if len(expectedGeneration) > 0 {
		generation = expectedGeneration[0]
	}
	if generation == 0 {
		generation = s.nextSessionFilesCacheGenerationLocked(viewID)
	} else if s.currentSessionFilesCacheGenerationLocked(viewID) != generation {
		s.sessionFilesCache.mu.Unlock()
		s.sessionFilesCache.publicationMu.Unlock()
		return false
	}
	if s.sessionFilesCache.records == nil {
		s.sessionFilesCache.records = cache.NewLRU[string, sessionFilesCacheRecord](sessionFilesCacheMaxEntries)
	}
	s.sessionFilesCache.records.Set(viewID, record)
	s.sessionFilesCache.mu.Unlock()
	s.sessionFilesCache.publicationMu.Unlock()
	if err := os.MkdirAll(directory, 0o700); err != nil {
		return true
	}
	data, err := json.Marshal(record)
	if err != nil {
		return true
	}
	temporary, err := os.CreateTemp(directory, ".session-files-*.tmp")
	if err != nil {
		return true
	}
	temporaryName := temporary.Name()
	defer os.Remove(temporaryName)
	if err := temporary.Chmod(0o600); err == nil {
		if _, err = temporary.Write(data); err == nil {
			err = temporary.Sync()
		}
	}
	if closeErr := temporary.Close(); err == nil {
		err = closeErr
	}
	if err == nil {
		s.sessionFilesCache.publicationMu.Lock()
		s.sessionFilesCache.mu.Lock()
		if s.currentSessionFilesCacheGenerationLocked(viewID) != generation {
			s.sessionFilesCache.mu.Unlock()
			s.sessionFilesCache.publicationMu.Unlock()
			return true
		}
		s.sessionFilesCache.mu.Unlock()
		err = os.Rename(temporaryName, filepath.Join(directory, viewID+".json"))
		s.sessionFilesCache.publicationMu.Unlock()
	}
	if err == nil {
		s.sessionFilesCache.recencyMu.Lock()
		s.sessionFilesCache.mu.Lock()
		var recentlyUsed []string
		if s.sessionFilesCache.records != nil {
			recentlyUsed = s.sessionFilesCache.records.KeysLeastRecentFirst()
		}
		s.sessionFilesCache.mu.Unlock()
		evictionErr := evictSessionFilesCacheFiles(directory, viewID, recentlyUsed)
		s.sessionFilesCache.recencyMu.Unlock()
		if evictionErr != nil {
			slog.Warn("session-files cache eviction failed", "cache_view_id", viewID, "error", evictionErr)
		}
	}
	return true
}

// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/keivenchang/yomux/internal/atomicfile"
	"github.com/keivenchang/yomux/internal/mutablefs"
	"github.com/keivenchang/yomux/internal/productenv"
)

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

func acceptLaunchDescriptors(manifest string, listenerFD int) error {
	if manifest == "" && listenerFD < 0 {
		return nil
	}
	descriptors := []int{}
	seen := map[int]bool{}
	for _, value := range strings.Split(manifest, ",") {
		if manifest == "" {
			break
		}
		fd, err := strconv.Atoi(value)
		if err != nil || fd < 3 || seen[fd] {
			return fmt.Errorf("invalid or duplicate descriptor %q", value)
		}
		var stat syscall.Stat_t
		if err := syscall.Fstat(fd, &stat); err != nil || stat.Mode&syscall.S_IFMT != syscall.S_IFREG {
			return fmt.Errorf("descriptor %d is not an open regular lease file", fd)
		}
		data := make([]byte, 16384)
		n, err := syscall.Pread(fd, data, 0)
		var record struct {
			PID int `json:"pid"`
		}
		if err != nil || json.Unmarshal(data[:n], &record) != nil || record.PID != os.Getpid() {
			return fmt.Errorf("descriptor %d is not owned by this launch", fd)
		}
		seen[fd] = true
		descriptors = append(descriptors, fd)
	}
	if listenerFD >= 0 {
		listening, err := syscall.GetsockoptInt(listenerFD, syscall.SOL_SOCKET, syscall.SO_ACCEPTCONN)
		if listenerFD < 3 || seen[listenerFD] || err != nil || listening != 1 {
			return fmt.Errorf("descriptor %d is not a listening socket", listenerFD)
		}
		descriptors = append(descriptors, listenerFD)
	}
	// Python retains these descriptors across exec; only the server may keep them, not its terminal or worker children.
	for _, fd := range descriptors {
		syscall.CloseOnExec(fd)
	}
	return nil
}

func readIdentityValue(environmentName, path, fallback string) string {
	value := ""
	if environmentName != "" {
		value = strings.TrimSpace(productenv.Value(environmentName))
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
	return value[:min(len(value), 128)]
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
	preferred, err := strconv.Atoi(strings.TrimSpace(productenv.Value("BACKGROUND_OWNER_PRIMARY_PORT")))
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
	stableHostID := readIdentityValue("HOST_ID", "/etc/machine-id", hostname)
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

func writeAtomicFile(path string, data []byte) error { return atomicfile.Write(path, data) }

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
	return acquireDirectoryLease(root, "instance.lock")
}

func acquireConfigLease(config string) (*os.File, error) {
	if err := mutablefs.RequireLocal(config); err != nil {
		return nil, err
	}
	return acquireDirectoryLease(config, "go-storage.lock")
}

func acquireDirectoryLease(root, filename string) (*os.File, error) {
	rootPath, err := mutablefs.Canonical(root)
	if err != nil {
		return nil, err
	}
	if err := os.MkdirAll(rootPath, 0o700); err != nil {
		return nil, err
	}
	path := filepath.Join(rootPath, filename)
	lock, err := os.OpenFile(path, os.O_CREATE|os.O_RDWR|syscall.O_NOFOLLOW, 0o600)
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
	if err = lock.Truncate(0); err == nil {
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

// Accepted background operations survive their request but must finish before
// shutdown releases databases and ownership leases. Admission and Add share a
// lock so Wait cannot observe zero just before a late handler starts work.
func (s *server) startBackgroundWork(run func()) bool {
	s.backgroundWorkMu.Lock()
	defer s.backgroundWorkMu.Unlock()
	if s.backgroundWorkClosed {
		return false
	}
	s.backgroundWork.Add(1)
	go func() {
		defer s.backgroundWork.Done()
		run()
	}()
	return true
}

func (s *server) stopBackgroundWork() {
	s.backgroundWorkMu.Lock()
	s.backgroundWorkClosed = true
	s.backgroundWorkMu.Unlock()
	s.backgroundWork.Wait()
}

func (s *server) backgroundWorkAdmissionClosed() bool {
	s.backgroundWorkMu.Lock()
	defer s.backgroundWorkMu.Unlock()
	return s.backgroundWorkClosed
}

// Requests retain dependency ownership until their handler and logging have returned.
func (s *server) beginRequest(r *http.Request) (func(), bool) {
	s.requestMu.Lock()
	defer s.requestMu.Unlock()
	if s.requestClosed {
		return nil, false
	}
	ctx, cancel := context.WithCancel(r.Context())
	*r = *r.WithContext(ctx)
	if s.requestCancels == nil {
		s.requestCancels = make(map[*http.Request]context.CancelFunc)
	}
	s.requestCancels[r] = cancel
	s.requestWorkers.Add(1)
	return func() {
		cancel()
		s.requestMu.Lock()
		delete(s.requestCancels, r)
		s.requestMu.Unlock()
		s.requestWorkers.Done()
	}, true
}
func (s *server) stopRequestAdmission() {
	s.requestMu.Lock()
	s.requestClosed = true
	if s.tmuxPointCancel != nil {
		s.tmuxPointCancel()
	}
	for _, cancel := range s.requestCancels {
		cancel()
	}
	s.requestMu.Unlock()
}

func (s *server) shutdown() {
	s.shutdownOnce.Do(func() {
		shutdownClean := true
		if s.serverCancel != nil {
			s.serverCancel()
		}
		s.stopRequestAdmission()
		s.requestWorkers.Wait()

		if s.tmuxRosterWatchCancel != nil {
			s.tmuxRosterWatchCancel()
			<-s.tmuxRosterWatchDone
		}
		s.homeRendererMu.Lock()
		s.htmlClosed = true
		if s.htmlCancel != nil {
			s.htmlCancel()
		}
		renderer := s.homeRenderer
		s.homeRenderer = nil
		s.homeRendererMu.Unlock()
		if renderer != nil {
			renderer.close()
		}
		if s.finderJobs != nil {
			if !s.finderJobs.CloseWithTimeout(5 * time.Second) {
				shutdownClean = false
				slog.Warn("finder job scheduler did not stop before shutdown timeout")
				s.finderJobs.WaitStopped()
			}
		}
		if s.finderBackgroundJobs != nil {
			if !s.finderBackgroundJobs.CloseWithTimeout(5 * time.Second) {
				shutdownClean = false
				slog.Warn("finder background scheduler did not stop before shutdown timeout")
				s.finderBackgroundJobs.WaitStopped()
			}
		}
		if s.finderBulkJobs != nil {
			if !s.finderBulkJobs.CloseWithTimeout(5 * time.Second) {
				shutdownClean = false
				slog.Warn("finder bulk job scheduler did not stop before shutdown timeout")
				s.finderBulkJobs.WaitStopped()
			}
		}
		if s.finderStreamJobs != nil {
			if !s.finderStreamJobs.CloseWithTimeout(5 * time.Second) {
				shutdownClean = false
				slog.Warn("finder stream scheduler did not stop before shutdown timeout")
				s.finderStreamJobs.WaitStopped()
			}
		}
		if s.yolo != nil {
			s.yolo.close()
		}
		s.stopBackgroundWork()
		if s.statsRuntimeRecorder != nil {
			if err := s.statsRuntimeRecorder.Stop(); err != nil {
				shutdownClean = false
			}
		}
		if s.hostCollector != nil {
			s.hostCollector.Stop()
		}
		if s.agentStatusCollector != nil {
			s.agentStatusCollector.Stop()
		}
		if s.tokenCollector != nil {
			s.tokenCollector.Stop()
		}
		if s.statsLifecycle != nil {
			s.statsLifecycle.Stop()
		}
		if s.statsWriter != nil {
			if err := s.statsWriter.Close(); err != nil {
				shutdownClean = false
			}
		}
		if s.finder != nil {
			s.finder.Close()
		}
		if s.searchIndex != nil {
			s.searchIndex.Close()
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
			if command.Process == nil {
				continue
			}
			_ = syscall.Kill(-command.Process.Pid, syscall.SIGTERM)
			_ = command.Process.Kill()
		}
		s.shutdownClean = shutdownClean
	})
}

func (s *server) shutdownCompletedCleanly() bool { return s.shutdownClean }

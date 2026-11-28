// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
	"unicode/utf16"

	"github.com/keivenchang/yomux/internal/atomicfile"
	"github.com/keivenchang/yomux/internal/finder"
)

type shareOperation struct {
	Changes  json.RawMessage `json:"changes"`
	ClientID string          `json:"clientID"`
	ID       string          `json:"id"`
}
type sharePresence struct {
	ClientID    string    `json:"clientID"`
	Name        string    `json:"name"`
	Hue         int       `json:"hue"`
	Owner       bool      `json:"owner,omitempty"`
	Surface     string    `json:"surface,omitempty"`
	Approximate bool      `json:"approximate,omitempty"`
	Anchor      int       `json:"anchor"`
	Head        int       `json:"head"`
	Version     int       `json:"version"`
	At          time.Time `json:"at"`
	principal   string
	movedAt     time.Time
	focused     bool
}
type shareDocument struct {
	ID           string
	lineEnding   string
	mu           sync.Mutex
	historyBytes int
	Path         string `json:"path"`
	Text         string `json:"text"`
	Saved        string `json:"saved"`
	Paused       string `json:"paused"`
	file         *finder.GrantedFile
	operations   []shareOperation
	seen         map[string]shareOperation
	presence     map[string]sharePresence
	texCancel    context.CancelFunc
	// texGeneration identifies the latest scheduled compile, so a finished one clears only its own cancel.
	texGeneration uint64
	// diskVersion is the file version Saved was last read from by a poll; nil forces a reread.
	diskVersion os.FileInfo
	// diskReads counts the full rereads polls performed.
	diskReads uint64
}

// The caller holds doc.mu.
func (doc *shareDocument) cancelTeX() {
	if doc.texCancel != nil {
		doc.texCancel()
		doc.texCancel = nil
	}
}

func newShareDocument(file *finder.GrantedFile, record shareRecoveryRecord) (*shareDocument, error) {
	id, err := shareRandom()
	if err != nil {
		return nil, err
	}
	return &shareDocument{
		ID: id, Path: record.Path, Text: record.Text, Saved: record.Saved, lineEnding: record.LineEnding,
		file: file, operations: []shareOperation{},
		seen: make(map[string]shareOperation), presence: make(map[string]sharePresence),
	}, nil
}

type shareChangePart struct {
	length  int
	insert  []uint16
	changed bool
}

func (doc *shareDocument) otherParticipants(principal string, now time.Time) []sharePresence {
	selected := make(map[string]sharePresence)
	for id, cursor := range doc.presence {
		if now.Sub(cursor.At) > 15*time.Second {
			delete(doc.presence, id)
			continue
		}
		if cursor.principal == principal {
			continue
		}
		previous, found := selected[cursor.principal]
		if !found || cursor.focused && !previous.focused ||
			cursor.focused == previous.focused && (cursor.movedAt.After(previous.movedAt) ||
				cursor.movedAt.Equal(previous.movedAt) && cursor.ClientID < previous.ClientID) {
			cursor.Version = len(doc.operations)
			selected[cursor.principal] = cursor
		}
	}
	presence := make([]sharePresence, 0, len(selected))
	for _, cursor := range selected {
		presence = append(presence, cursor)
	}
	sort.Slice(presence, func(i, j int) bool { return presence[i].ClientID < presence[j].ClientID })
	return presence
}

// ChangeSet positions count JavaScript UTF-16 code units, not Go bytes or runes.
// Require the complete old length and reject positions that split a surrogate pair.
func applyShareChanges(text string, raw json.RawMessage) (string, []shareChangePart, error) {
	var sections []json.RawMessage
	if json.Unmarshal(raw, &sections) != nil || sections == nil || len(sections) > 32768 {
		return "", nil, errors.New("invalid_changes")
	}
	source := utf16.Encode([]rune(text))
	out := make([]uint16, 0, len(source))
	parts := []shareChangePart{}
	position := 0
	boundary := func(at int) bool {
		return at >= 0 && at <= len(source) && !(at > 0 && at < len(source) && source[at-1] >= 0xD800 && source[at-1] <= 0xDBFF && source[at] >= 0xDC00 && source[at] <= 0xDFFF)
	}
	for _, section := range sections {
		var n int
		part := shareChangePart{}
		if shareJSONInteger(section, &n) {
			part.length = n
		} else {
			var change []json.RawMessage
			if json.Unmarshal(section, &change) != nil || len(change) == 0 || !shareJSONInteger(change[0], &n) {
				return "", nil, errors.New("invalid_changes")
			}
			part.length = n
			part.changed = true
			lines := make([]string, 0, len(change)-1)
			for _, line := range change[1:] {
				var value string
				if !shareJSONString(line, &value) || strings.ContainsAny(value, "\r\n\x00") {
					return "", nil, errors.New("invalid_changes")
				}
				lines = append(lines, value)
			}
			part.insert = utf16.Encode([]rune(strings.Join(lines, "\n")))
		}
		if part.length < 0 || part.length > len(source)-position || !boundary(position) || !boundary(position+part.length) {
			return "", nil, errors.New("invalid_changes")
		}
		if part.changed {
			out = append(out, part.insert...)
		} else {
			out = append(out, source[position:position+part.length]...)
		}
		position += part.length
		parts = append(parts, part)
		if len(out) > shareTextLimit {
			return "", nil, errors.New("document_too_large")
		}
	}
	if position != len(source) {
		return "", nil, errors.New("invalid_changes")
	}
	result := string(utf16.Decode(out))
	if len(result) > shareTextLimit {
		return "", nil, errors.New("document_too_large")
	}
	return result, parts, nil
}
func mapSharePosition(position int, parts []shareChangePart) int {
	oldAt, newAt := 0, 0
	for _, part := range parts {
		if position < oldAt+part.length {
			if !part.changed {
				return newAt + position - oldAt
			}
			return newAt + len(part.insert)
		}
		oldAt += part.length
		if part.changed {
			newAt += len(part.insert)
		} else {
			newAt += part.length
		}
	}
	return newAt
}

func (doc *shareDocument) mapPresence(parts []shareChangePart) {
	for key, cursor := range doc.presence {
		cursor.Anchor = mapSharePosition(cursor.Anchor, parts)
		cursor.Head = mapSharePosition(cursor.Head, parts)
		doc.presence[key] = cursor
	}
}

func (s *server) reserveShareHistory(bytes int) bool {
	if s.shares.historyBytes.Add(int64(bytes)) <= 64<<20 {
		return true
	}
	s.shares.historyBytes.Add(-int64(bytes))
	return false
}

// Import a disk edit into the existing revision stream so connected editors
// retain their session and CodeMirror can map selections across the change.
// shared reports whether a live share grant includes the document.
func (s *server) refreshShareDocument(ctx context.Context, doc *shareDocument, shared bool) {
	// Every poll lands here while holding doc.mu, so skip rereading up to 1 MiB when the file
	// is still the version Saved came from.
	if doc.Paused == "" && doc.diskVersion != nil {
		if current, err := os.Stat(doc.Path); err == nil && sameFileVersion(doc.diskVersion, current) {
			return
		}
	}
	doc.diskVersion = nil
	doc.diskReads++
	data, info, failure := doc.file.ReadCurrent(ctx, shareTextLimit)
	if failure != nil {
		doc.Paused = "file_unavailable"
		return
	}
	if !shareTextSupported(data) {
		doc.Paused = "unsupported_file"
		return
	}
	text := normalizeShareText(string(data))
	if text != doc.Saved {
		before, after := []rune(doc.Saved), []rune(text)
		start := 0
		for start < len(before) && start < len(after) && before[start] == after[start] {
			start++
		}
		endBefore, endAfter := len(before), len(after)
		for endBefore > start && endAfter > start && before[endBefore-1] == after[endAfter-1] {
			endBefore--
			endAfter--
		}
		prefix := len(utf16.Encode(before[:start]))
		removed := len(utf16.Encode(before[start:endBefore]))
		inserted := string(after[start:endAfter])
		suffix := len(utf16.Encode(before[endBefore:]))
		replacement := []any{removed}
		for _, line := range strings.Split(inserted, "\n") {
			replacement = append(replacement, line)
		}
		changes, err := json.Marshal([]any{prefix, replacement, suffix})
		if err != nil {
			doc.Paused = "external_change"
			return
		}
		operation := shareOperation{ClientID: "external", Changes: changes}
		addedBytes := len(changes) + len(operation.ClientID)
		if len(doc.operations) >= 100000 || doc.historyBytes+addedBytes > 16<<20 || !s.reserveShareHistory(addedBytes) {
			doc.Paused = "document_revision_limit"
			return
		}
		doc.mapPresence([]shareChangePart{{length: prefix}, {length: removed, insert: utf16.Encode([]rune(inserted)), changed: true}, {length: suffix}})
		doc.operations = append(doc.operations, operation)
		doc.historyBytes += addedBytes
		doc.Text, doc.Saved = text, text
		s.scheduleSharedTeX(doc, shared)
	}
	doc.lineEnding = ""
	if strings.Contains(string(data), "\r\n") {
		doc.lineEnding = "\r\n"
	}
	doc.Paused = ""
	if fileVersionSettled(info, fileSettleClock()) {
		doc.diskVersion = info
	}
}

func (s *server) shareJournalPath(path string) string {
	digest := sha256.Sum256([]byte(path))
	return filepath.Join(s.config, "share-recovery", hex.EncodeToString(digest[:])+".json")
}
func (s *server) journalShareDocument(doc *shareDocument, text string) error {
	directory := filepath.Dir(s.shareJournalPath(doc.Path))
	if err := os.MkdirAll(directory, 0700); err != nil {
		return err
	}
	data, err := json.Marshal(map[string]any{"path": doc.Path, "text": text, "saved": doc.Saved, "paused": doc.Paused, "line_ending": doc.lineEnding, "at": time.Now()})
	if err != nil {
		return err
	}
	return atomicfile.Write(s.shareJournalPath(doc.Path), data)
}
func (s *server) saveShareDocument(ctx context.Context, doc *shareDocument, shared bool) {
	if doc.Text == doc.Saved || doc.Paused != "" {
		return
	}
	data, info, failure := doc.file.ReadCurrent(ctx, shareTextLimit)
	if failure != nil {
		doc.Paused = "file_unavailable"
		return
	}
	if string(data) != doc.diskText(doc.Saved) {
		doc.Paused = "external_change"
		return
	}
	doc.diskVersion = nil
	_, failure = doc.file.Write(ctx, doc.diskText(doc.Text), info)
	if failure != nil {
		doc.Paused = failure.Code
		return
	}
	doc.Saved = doc.Text
	// Retain the journal even after saving. A later crash may happen between a
	// successful file publication and its acknowledgment to a reconnecting editor.
	if err := s.journalShareDocument(doc, doc.Text); err != nil {
		doc.Paused = "journal_unavailable"
	}
	s.scheduleSharedTeX(doc, shared)
}
func (s *server) shareDocumentActive(path string) bool {
	return s.shareGrantDocumentActive(path) || s.shareLocalDocumentActive(path)
}
func (s *server) shareGrantDocumentActive(path string) bool {
	return s.shareGrantIncludes(path, true)
}

// shareGrantIncludes reports whether a live grant shares path, writable when required.
// The caller holds the registry lock.
func (s *server) shareGrantIncludes(path string, writable bool) bool {
	for _, g := range s.shares.grants {
		if !shareLive(g) {
			continue
		}
		for _, r := range g.Resources {
			if r.Path == path && (r.Writable || !writable) {
				return true
			}
		}
	}
	return false
}
func (s *server) shareHostDocument(w http.ResponseWriter, r *http.Request) {
	request, err := readShareHTTPRequest(r, 4<<20)
	if err != nil {
		shareFailure(w, 400, "invalid_request")
		return
	}
	r = request
	reply := newShareHTTPResponse(w, r.Context())
	defer reply.publish()
	w = reply

	user, _ := r.Context().Value(authContextKey{}).(authUser)
	s.shares.mu.Lock()
	s.shareInitLocked()
	path := r.URL.Query().Get("path")
	doc := s.shares.documents[path]
	var hue int
	if doc != nil {
		hue = s.shareHostHueLocked(user.username)
	}
	s.shares.mu.Unlock()
	if doc == nil {
		jsonResponse(w, 200, map[string]any{"active": false, "updates": []shareOperation{}, "presence": []sharePresence{}, "saved": false, "paused": "collaboration_ended"})
		return
	}
	// Like a guest browser, each owner page is its own participant: two panels in one page share
	// a caret, while the owner's other browsers and tabs show theirs.
	principal := "host:" + user.username
	if page := r.URL.Query().Get("page"); page != "" {
		if !shareClientToken(page) {
			shareFailure(w, 400, "invalid_page")
			return
		}
		principal += ":" + page
	}
	s.shareDocumentRequest(w, r, doc, user.username, hue, principal, true)
}
func (s *server) shareDocumentRequest(w http.ResponseWriter, r *http.Request, doc *shareDocument, name string, hue int, principal string, host bool, publications ...*shareResource) {
	reply := newShareHTTPResponse(w, r.Context())
	defer reply.publish()
	w = reply

	if doc == nil {
		shareFailure(w, 409, "document_unavailable")
		return
	}
	s.shares.mu.Lock()
	active := s.shareDocumentActive(doc.Path)
	shared := s.shareGrantIncludes(doc.Path, false)
	s.shares.mu.Unlock()
	doc.mu.Lock()
	defer func() { doc.mu.Unlock(); s.shares.mu.Lock(); s.sharePruneLocked(); s.shares.mu.Unlock() }()
	if len(publications) > 0 && publications[0].permission != nil && publications[0].permission.ctx.Err() != nil {
		shareFailure(w, 409, "permission_changed")
		return
	}
	if r.Context().Err() != nil {
		shareFailure(w, 410, "share_ended")
		return
	}
	if !active && !host {
		shareFailure(w, 410, "collaboration_ended")
		return
	}
	if expected := r.URL.Query().Get("session"); expected != "" && expected != doc.ID {
		shareFailure(w, 409, "document_session_changed")
		return
	}
	client := r.URL.Query().Get("client")
	if !shareClientToken(client) {
		shareFailure(w, 400, "invalid_client")
		return
	}
	digest := sha256.Sum256([]byte(doc.ID + "\x00" + principal + "\x00" + client))
	clientID := hex.EncodeToString(digest[:])
	// Ended grants cannot submit more edits. Host access remains for recovery and
	// acknowledging already accepted operations before ordinary editing resumes.
	version := 0
	if raw := r.URL.Query().Get("version"); raw != "" {
		var err error
		version, err = strconv.Atoi(raw)
		if err != nil || version < 0 || version > len(doc.operations) {
			shareFailure(w, 400, "invalid_version")
			return
		}
	}
	if r.Method == http.MethodPost {

		var b struct {
			Version     int              `json:"version"`
			Updates     []shareOperation `json:"updates"`
			Anchor      int              `json:"anchor"`
			Head        int              `json:"head"`
			Focused     bool             `json:"focused"`
			Surface     string           `json:"surface"`
			Approximate bool             `json:"approximate"`
			Resolve     string           `json:"resolve"`
		}
		if decodeJSONBody(r.Body, 4<<20, &b) != nil || len(b.Updates) > 256 || b.Version < 0 || b.Version > len(doc.operations) {
			shareFailure(w, 400, "invalid_updates")
			return
		}
		if !active && (!host || b.Resolve != "resume" || len(b.Updates) > 0) {
			shareFailure(w, 410, "collaboration_ended")
			return
		}
		version = b.Version
		if b.Resolve != "" {
			if !host || b.Resolve != "resume" {
				shareFailure(w, 403, "host_resolution_required")
				return
			}
			data, _, failure := doc.file.Reauthorize(r.Context())
			if failure != nil {
				shareFailure(w, 409, "file_unavailable")
				return
			}
			doc.Saved = normalizeShareText(string(data))
			doc.Paused = ""
			doc.diskVersion = nil

		}
		if version == len(doc.operations) && len(b.Updates) > 0 {
			next := doc.Text
			newOps := []shareOperation{}
			pendingIDs := map[string]bool{}
			addedBytes := 0
			mapped := []([]shareChangePart){}
			for _, operation := range b.Updates {
				if len(operation.ID) < 16 || len(operation.ID) > 128 {
					shareFailure(w, 400, "invalid_operation_id")
					return
				}
				key := clientID + ":" + operation.ID
				if pendingIDs[key] {
					shareFailure(w, 400, "duplicate_operation_id")
					return
				}
				pendingIDs[key] = true
				if previous, ok := doc.seen[key]; ok {
					if string(previous.Changes) != string(operation.Changes) {
						shareFailure(w, 409, "operation_id_conflict")
						return
					}
					continue
				}
				var parts []shareChangePart
				var err error
				next, parts, err = applyShareChanges(next, operation.Changes)
				if err != nil {
					shareFailure(w, 400, err.Error())
					return
				}
				operation.ClientID = clientID
				newOps = append(newOps, operation)
				addedBytes += len(operation.Changes) + len(operation.ID) + len(clientID)
				mapped = append(mapped, parts)
			}
			if len(doc.operations)+len(newOps) > 100000 || doc.historyBytes+addedBytes > 16<<20 {
				shareFailure(w, 409, "document_revision_limit")
				return
			}

			if len(newOps) > 0 {
				diskBytes := len(next)
				if doc.lineEnding == "\r\n" {
					diskBytes += strings.Count(next, "\n")
				}
				if diskBytes > shareTextLimit {
					shareFailure(w, 400, "document_too_large")
					return
				}
				if strings.Contains(next, "\r\n") {
					shareFailure(w, 400, "invalid_changes")
					return
				}
				if r.Context().Err() != nil {
					shareFailure(w, 410, "collaboration_ended")
					return
				}
				if !s.reserveShareHistory(addedBytes) {
					shareFailure(w, 429, "collaboration_capacity")
					return
				}
				if err := s.journalShareDocument(doc, next); err != nil {
					s.shares.historyBytes.Add(-int64(addedBytes))
					shareFailure(w, 503, "journal_unavailable")
					return
				}
				for _, parts := range mapped {
					doc.mapPresence(parts)
				}
				doc.historyBytes += addedBytes
				doc.Text = next
				for _, operation := range newOps {
					doc.operations = append(doc.operations, operation)
					doc.seen[clientID+":"+operation.ID] = operation
				}
			}
		}
		if b.Version == len(doc.operations)-len(b.Updates) || len(b.Updates) == 0 && b.Version == len(doc.operations) {
			length := len(utf16.Encode([]rune(doc.Text)))
			if b.Anchor >= 0 && b.Head >= 0 && b.Anchor <= length && b.Head <= length {
				if b.Surface != "view" {
					b.Surface = "text"
				}
				now := time.Now()
				cursor := doc.presence[clientID]
				if cursor.movedAt.IsZero() || cursor.Anchor != b.Anchor || cursor.Head != b.Head ||
					cursor.Surface != b.Surface || cursor.Approximate != b.Approximate || b.Focused && !cursor.focused {
					cursor.movedAt = now
				}
				cursor.ClientID, cursor.Name, cursor.Hue = clientID, name, hue
				cursor.Owner = host
				cursor.Surface, cursor.Approximate = b.Surface, b.Approximate
				cursor.Anchor, cursor.Head, cursor.At = b.Anchor, b.Head, now
				cursor.principal, cursor.focused = principal, b.Focused
				doc.presence[clientID] = cursor
			}
		}
	} else if r.Method != http.MethodGet {
		shareFailure(w, 405, "method_not_allowed")
		return
	}
	saveContext, cancelSave := context.WithTimeout(context.WithoutCancel(r.Context()), 5*time.Second)
	if active && doc.Text == doc.Saved && (doc.Paused == "" || doc.Paused == "file_unavailable" || doc.Paused == "document_revision_limit") {
		s.refreshShareDocument(saveContext, doc, shared)
	} else {
		s.saveShareDocument(saveContext, doc, shared)
	}
	cancelSave()
	presence := doc.otherParticipants(principal, time.Now())
	end := version
	responseBytes := 0
	for end < len(doc.operations) && end-version < 256 {
		n := len(doc.operations[end].Changes)
		if end > version && responseBytes+n > 2<<20 {
			break
		}
		responseBytes += n
		end++
	}
	response := map[string]any{"session": doc.ID, "active": active, "version": len(doc.operations), "updates": doc.operations[version:end], "presence": presence, "clientID": clientID, "paused": doc.Paused, "saved": doc.Text == doc.Saved}
	if r.URL.Query().Get("initial") == "1" {
		response["text"] = doc.Text
		response["updates"] = []shareOperation{}
	}
	if len(publications) > 0 {
		resource := publications[0]
		response["pdf_available"] = len(resource.pdf) > 0
		response["pdf_revision"] = resource.pdfSerial
		response["pdf_stale"] = resource.pdfRevision != len(doc.operations)
	}
	if r.Context().Err() != nil {
		shareFailure(w, 410, "share_ended")
		return
	}
	jsonResponse(w, 200, response)
}

type shareRecoveryRecord struct {
	Path       string `json:"path"`
	Text       string `json:"text"`
	Saved      string `json:"saved"`
	Paused     string `json:"paused"`
	LineEnding string `json:"line_ending"`
}

func (s *server) shareRecovery(w http.ResponseWriter, r *http.Request) {
	request, err := readShareHTTPRequest(r, 4<<20)
	if err != nil {
		shareFailure(w, 400, "invalid_request")
		return
	}
	r = request
	reply := newShareHTTPResponse(w, r.Context())
	defer reply.publish()
	w = reply

	if r.Method == http.MethodPost {
		var body struct {
			Path   string `json:"path"`
			Action string `json:"action"`
		}
		if decodeJSONBody(r.Body, 16384, &body) != nil || body.Action != "restore" || !filepath.IsAbs(body.Path) {
			shareFailure(w, 400, "invalid_recovery")
			return
		}
		data, err := os.ReadFile(s.shareJournalPath(body.Path))
		var record shareRecoveryRecord
		if err != nil || len(data) > 4<<20 || json.Unmarshal(data, &record) != nil || record.Path != body.Path {
			shareFailure(w, 409, "recovery_unavailable")
			return
		}
		s.shares.mu.Lock()
		defer s.shares.mu.Unlock()
		s.shareInitLocked()
		doc := s.shares.documents[body.Path]
		if doc == nil {
			file, failure := s.finder.GrantFile(r.Context(), body.Path)
			if failure != nil {
				shareFailure(w, 409, failure.Code)
				return
			}
			doc, err = newShareDocument(file, record)
			if err != nil {
				shareFailure(w, 503, "random_unavailable")
				return
			}
			if failure := file.ReserveWrites(r.Context()); failure != nil {
				shareFailure(w, 409, failure.Code)
				return
			}
			s.shares.documents[body.Path] = doc
		}
		doc.mu.Lock()
		defer doc.mu.Unlock()
		current, _, failure := doc.file.Reauthorize(r.Context())
		if failure != nil {
			shareFailure(w, 409, failure.Code)
			return
		}
		doc.Saved = normalizeShareText(string(current))
		doc.Paused = ""
		doc.diskVersion = nil
		if err := s.journalShareDocument(doc, doc.Text); err != nil {
			shareFailure(w, 503, "journal_unavailable")
			return
		}
		ctx, cancel := context.WithTimeout(context.WithoutCancel(r.Context()), 5*time.Second)
		defer cancel()
		s.saveShareDocument(ctx, doc, s.shareGrantIncludes(doc.Path, false))
		if doc.Paused != "" {
			shareFailure(w, 409, doc.Paused)
			return
		}
		if !s.shareDocumentActive(doc.Path) {
			if failure := doc.file.ReleaseWrites(ctx); failure != nil {
				shareFailure(w, 503, failure.Code)
				return
			}
			if !s.shareGrantIncludes(doc.Path, false) {
				doc.cancelTeX()
			}
			delete(s.shares.documents, doc.Path)
			s.shares.historyBytes.Add(-int64(doc.historyBytes))
		}
		jsonResponse(w, 200, map[string]any{"restored": true})
		return
	}
	if r.Method != http.MethodGet {
		shareFailure(w, 405, "method_not_allowed")
		return
	}
	dir := filepath.Join(s.config, "share-recovery")
	entries, err := os.ReadDir(dir)
	if os.IsNotExist(err) {
		jsonResponse(w, 200, map[string]any{"drafts": []any{}})
		return
	}
	if err != nil {
		shareFailure(w, 503, "recovery_unavailable")
		return
	}
	drafts := []shareRecoveryRecord{}
	for _, entry := range entries {
		if len(drafts) >= 128 {
			break
		}
		if !entry.Type().IsRegular() || !strings.HasSuffix(entry.Name(), ".json") {
			continue
		}
		data, err := os.ReadFile(filepath.Join(dir, entry.Name()))
		var record shareRecoveryRecord
		if err != nil || len(data) > 4<<20 || json.Unmarshal(data, &record) != nil {
			shareFailure(w, 503, "recovery_unavailable")
			return
		}
		if record.Text != record.Saved || record.Paused != "" {
			drafts = append(drafts, record)
		}
	}
	jsonResponse(w, 200, map[string]any{"drafts": drafts})
}

func shareJSONInteger(raw []byte, value *int) bool {
	return len(raw) > 0 && ((raw[0] >= '0' && raw[0] <= '9') || raw[0] == '-') && json.Unmarshal(raw, value) == nil
}
func shareJSONString(raw []byte, value *string) bool {
	if len(raw) < 2 || raw[0] != '"' || json.Unmarshal(raw, value) != nil {
		return false
	}
	for i := 1; i < len(raw)-1; i++ {
		if raw[i] != '\\' {
			continue
		}
		i++
		if raw[i] != 'u' {
			continue
		}
		if i+4 >= len(raw) {
			return false
		}
		n, err := strconv.ParseUint(string(raw[i+1:i+5]), 16, 16)
		if err != nil {
			return false
		}
		i += 4
		if n >= 0xD800 && n <= 0xDBFF {
			if i+6 >= len(raw) || raw[i+1] != '\\' || raw[i+2] != 'u' {
				return false
			}
			low, err := strconv.ParseUint(string(raw[i+3:i+7]), 16, 16)
			if err != nil || low < 0xDC00 || low > 0xDFFF {
				return false
			}
			i += 6
		} else if n >= 0xDC00 && n <= 0xDFFF {
			return false
		}
	}
	return true
}

func (doc *shareDocument) diskText(text string) string {
	if doc.lineEnding == "\r\n" {
		return strings.ReplaceAll(text, "\n", "\r\n")
	}
	return text
}

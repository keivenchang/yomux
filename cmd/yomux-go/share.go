// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"embed"
	"encoding/base64"
	"encoding/binary"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"
	"unicode/utf8"

	"github.com/keivenchang/yomux/internal/finder"
)

const shareTextLimit = 1 << 20

func normalizeShareText(text string) string {
	return strings.ReplaceAll(text, "\r\n", "\n")
}

func shareTextSupported(data []byte) bool {
	return len(data) <= shareTextLimit && utf8.Valid(data) && !bytes.ContainsRune(data, 0) && !strings.ContainsRune(normalizeShareText(string(data)), '\r')
}

type shareRegistry struct {
	historyBytes atomic.Int64
	publication  uint64
	mu           sync.Mutex
	grants       map[string]*shareGrant
	documents    map[string]*shareDocument
	hostHues     map[string]int
	// Owner browser tabs that have a file open, by path and tab. Two or more
	// open tabs collaborate through the same document as a share would.
	editors map[string]map[string]shareLocalEditor
}

type shareLocalEditor struct {
	at    time.Time
	clean bool
}

// Background tabs can be throttled to about one timer per minute, so allow
// for that before treating a tab as closed.
const shareLocalEditorWindow = 90 * time.Second

type shareGrant struct {
	lifetime   context.Context
	cancel     context.CancelFunc
	ID         string           `json:"id"`
	Revision   int              `json:"revision"`
	Appearance shareAppearance  `json:"appearance"`
	Guest      string           `json:"guest"`
	Expires    time.Time        `json:"expires"`
	Limit      int              `json:"limit"`
	Resources  []*shareResource `json:"resources"`
	Slots      int              `json:"claimed"`
	owner      string
	inviteURL  string
	secret     [32]byte
	browsers   map[[32]byte]*shareBrowser
	messages   []shareChatMessage
	stopped    bool

	TerminalWriteConfirmed bool `json:"terminal_write_confirmed,omitempty"`
}

const shareMaxMinutes = 86700 / 60
const shareInviteTokenLength = 20

type shareTerminalCursor struct {
	Column int `json:"column"`
	Row    int `json:"row"`
}

type shareTerminalSnapshot struct {
	Text   string               `json:"text"`
	Cursor *shareTerminalCursor `json:"cursor,omitempty"`
}
type shareBrowser struct {
	Name           string
	Hue            int
	IP             string
	Browser        string
	BrowserVersion string
	JoinedAt       time.Time
	LastSeen       time.Time
	credential     [32]byte
}
type shareGuestPeer struct {
	Name           string    `json:"name"`
	Hue            int       `json:"hue"`
	IP             string    `json:"ip"`
	Browser        string    `json:"browser"`
	BrowserVersion string    `json:"browser_version"`
	JoinedAt       time.Time `json:"joined_at"`
	LastSeen       time.Time `json:"last_seen"`
}

// Successive pairs use colors on opposite sides of the hue wheel.
var shareParticipantHues = [...]int{215, 35, 155, 275, 95, 335}

func (s *server) availableShareHueLocked(first int) int {
	used := make(map[int]int, len(shareParticipantHues))
	for _, hue := range s.shares.hostHues {
		used[hue]++
	}
	for _, grant := range s.shares.grants {
		if shareLive(grant) {
			for _, browser := range grant.browsers {
				used[browser.Hue]++
			}
		}
	}
	hue := shareParticipantHues[first]
	for _, candidate := range shareParticipantHues[first+1:] {
		if used[candidate] < used[hue] {
			hue = candidate
		}
	}
	return hue
}

func (s *server) shareHostHueLocked(name string) int {
	if hue, ok := s.shares.hostHues[name]; ok {
		return hue
	}
	if s.shares.hostHues == nil {
		s.shares.hostHues = make(map[string]int)
	}
	hue := s.availableShareHueLocked(0)
	s.shares.hostHues[name] = hue
	return hue
}

// A permission epoch fences requests admitted before a permission change.
// Terminal input holds the gate until the tmux command returns. File writes
// check the epoch while holding the collaboration document lock.
type sharePermission struct {
	mu     sync.Mutex
	ctx    context.Context
	cancel context.CancelFunc
}

type shareResource struct {
	Assets             map[string]string `json:"assets,omitempty"`
	images             map[string]shareImage
	ownerCaret         *shareOwnerCaret
	pdfSource          string
	pdfSerial          uint64
	ID                 string `json:"id"`
	Kind               string `json:"kind"`
	Label              string `json:"label"`
	Writable           bool   `json:"writable"`
	CanWrite           bool   `json:"can_write"`
	PermissionRevision int    `json:"permission_revision"`
	Path               string `json:"-"`
	Pane               string `json:"pane,omitempty"`
	Identity           string `json:"-"`
	file               *finder.GrantedFile
	document           *shareDocument
	permission         *sharePermission
	pdf                []byte
	pdfRevision        int
}
type shareImage struct {
	relative    string
	contentType string
	file        *finder.GrantedFile
}
type shareOwnerCaret struct {
	digest [sha256.Size]byte
	cursor sharePresence
}
type shareAppearance struct {
	Theme  string `json:"theme"`
	Accent string `json:"accent"`
}

func (appearance shareAppearance) valid() bool {
	if appearance.Theme != "" && appearance.Theme != "dark" && appearance.Theme != "light" {
		return false
	}
	switch appearance.Accent {
	case "", "green", "blue", "orange", "yellow", "purple", "white":
		return true
	}
	return false
}

type shareSelection struct {
	ID           string   `json:"id"`
	Kind         string   `json:"kind"`
	Path         string   `json:"path"`
	Pane         string   `json:"pane"`
	Writable     bool     `json:"writable"`
	SavedContent *string  `json:"saved_content"`
	ImagePaths   []string `json:"image_paths"`
}

func shareRandom() (string, error) {
	b := make([]byte, 32)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	return hex.EncodeToString(b), nil
}

func newShareInviteToken() (string, error) {
	var entropy [15]byte
	if _, err := rand.Read(entropy[:]); err != nil {
		return "", err
	}
	return base64.RawURLEncoding.EncodeToString(entropy[:]), nil
}

// shareClientToken validates the browser-generated client and page identifiers.
func shareClientToken(value string) bool {
	return len(value) >= 16 && len(value) <= 100 && !strings.ContainsAny(value, "\x00\r\n\t")
}

// writeShareBody sends raw bytes; a failure means the browser went away, which it cannot be told.
func writeShareBody(w http.ResponseWriter, data []byte) {
	if _, err := w.Write(data); err != nil {
		slog.Debug("share response delivery failed", "error", err)
	}
}

func shareFailure(w http.ResponseWriter, status int, reason string) {
	jsonResponse(w, status, map[string]any{"error": reason})
}

func shareGuestLimitReached(w http.ResponseWriter, appearance shareAppearance) {
	jsonResponse(w, http.StatusConflict, map[string]any{"error": "guest_limit_reached", "appearance": appearance})
}

//go:embed pages/share_*.gohtml
var sharePages embed.FS

func (s *server) registerShareRoutes(mux *http.ServeMux) {
	for route, file := range map[string]string{"/share-client.js": "share_client.gohtml", "/share-style.css": "share_style.gohtml"} {
		mux.HandleFunc(route, func(w http.ResponseWriter, r *http.Request) {
			if r.Method != http.MethodGet {
				shareFailure(w, 405, "method_not_allowed")
				return
			}
			body, err := sharePages.ReadFile("pages/" + file)
			if err != nil {
				shareFailure(w, 503, "asset_unavailable")
				return
			}
			if strings.HasSuffix(route, ".css") {
				w.Header().Set("Content-Type", "text/css; charset=utf-8")
			} else {
				w.Header().Set("Content-Type", "text/javascript; charset=utf-8")
			}
			w.Header().Set("Cache-Control", "no-store")
			writeShareBody(w, body)
		})
	}
	mux.Handle("/api/share", s.protected(http.HandlerFunc(s.shareAdmin), "admin"))
	mux.Handle("/api/share/chat", s.protected(http.HandlerFunc(s.shareHostChat), "admin"))
	mux.Handle("/api/share/document", s.protected(http.HandlerFunc(s.shareHostDocument), "admin"))
	mux.Handle("/api/share/editing", s.protected(http.HandlerFunc(s.shareLocalEditing), "admin"))
	mux.Handle("/api/share/presence", s.protected(http.HandlerFunc(s.shareHostPresence), "admin"))
	mux.Handle("/api/share/recovery", s.protected(http.HandlerFunc(s.shareRecovery), "admin"))
	mux.HandleFunc("/share/", s.shareGuest)
}

func (s *server) shareHostPresence(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		shareFailure(w, 405, "method_not_allowed")
		return
	}
	var updates []struct {
		Grant, Resource, Digest, Surface string
		Anchor, Head                     int
	}
	if decodeJSONBody(r.Body, 24<<10, &updates) != nil || len(updates) > 64 {
		shareFailure(w, 400, "invalid_presence")
		return
	}
	user, _ := r.Context().Value(authContextKey{}).(authUser)
	now := time.Now()
	s.shares.mu.Lock()
	defer s.shares.mu.Unlock()
	for _, update := range updates {
		grant := s.shares.grants[update.Grant]
		if !shareLive(grant) || grant.owner != user.username {
			continue
		}
		digest, err := hex.DecodeString(update.Digest)
		if err != nil || len(digest) != sha256.Size || update.Anchor < 0 || update.Head < 0 ||
			update.Anchor > shareTextLimit || update.Head > shareTextLimit {
			continue
		}
		for _, resource := range grant.Resources {
			if resource.ID != update.Resource || resource.Kind != "file" || resource.Writable || !resource.CanWrite {
				continue
			}
			surface := "text"
			if update.Surface == "view" {
				surface = "view"
			}
			resource.ownerCaret = &shareOwnerCaret{
				digest: [sha256.Size]byte(digest),
				cursor: sharePresence{ClientID: "owner", Name: grant.owner, Owner: true, Hue: s.shareHostHueLocked(grant.owner),
					Anchor: update.Anchor, Head: update.Head, Surface: surface, At: now},
			}
		}
	}
	jsonResponse(w, 200, map[string]bool{"ok": true})
}
func (s *server) shareInitLocked() {
	if s.shares.grants == nil {
		s.shares.grants = make(map[string]*shareGrant)
		s.shares.documents = make(map[string]*shareDocument)
		s.shares.editors = make(map[string]map[string]shareLocalEditor)
	}
}

// shareLocalEditorsLocked counts the owner tabs that recently reported path open,
// and whether all of them had saved their drafts.
func (s *server) shareLocalEditorsLocked(path string, now time.Time) (int, bool) {
	clean := true
	for client, editor := range s.shares.editors[path] {
		if now.Sub(editor.at) > shareLocalEditorWindow {
			delete(s.shares.editors[path], client)
			continue
		}
		clean = clean && editor.clean
	}
	if len(s.shares.editors[path]) == 0 {
		delete(s.shares.editors, path)
	}
	return len(s.shares.editors[path]), clean
}

func (s *server) shareLocalDocumentActive(path string) bool {
	count, _ := s.shareLocalEditorsLocked(path, time.Now())
	return count >= 2
}

// shareLocalEditing records which files this owner tab has open. When two or more
// tabs have the same saved file open, it starts a collaboration document for them.
func (s *server) shareLocalEditing(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		shareFailure(w, 405, "method_not_allowed")
		return
	}
	var b struct {
		Client string `json:"client"`
		Files  []struct {
			Path  string `json:"path"`
			Clean bool   `json:"clean"`
		} `json:"files"`
	}
	if decodeJSONBody(r.Body, 64<<10, &b) != nil || !shareClientToken(b.Client) || len(b.Files) > 64 {
		shareFailure(w, 400, "invalid_editing")
		return
	}
	now := time.Now()
	s.shares.mu.Lock()
	defer s.shares.mu.Unlock()
	s.shareInitLocked()
	for _, editors := range s.shares.editors {
		delete(editors, b.Client)
	}
	failures := map[string]string{}
	for _, file := range b.Files {
		if !filepath.IsAbs(file.Path) {
			failures[file.Path] = "invalid_path"
			continue
		}
		if s.shares.editors[file.Path] == nil {
			s.shares.editors[file.Path] = make(map[string]shareLocalEditor)
		}
		s.shares.editors[file.Path][b.Client] = shareLocalEditor{at: now, clean: file.Clean}
	}
	for _, file := range b.Files {
		count, clean := s.shareLocalEditorsLocked(file.Path, now)
		if count < 2 || !clean || s.shares.documents[file.Path] != nil {
			continue
		}
		if code := s.openShareLocalDocumentLocked(r.Context(), file.Path); code != "" {
			failures[file.Path] = code
		}
	}
	s.sharePruneLocked()
	jsonResponse(w, 200, map[string]any{"ok": true, "failures": failures})
}

func (s *server) openShareLocalDocumentLocked(ctx context.Context, path string) string {
	if len(s.shares.documents) >= 32 {
		return "document_capacity"
	}
	file, failure := s.finder.GrantFile(ctx, path)
	if failure != nil {
		return failure.Code
	}
	data, _, failure := file.ReadCurrent(ctx, shareTextLimit)
	if failure != nil {
		return failure.Code
	}
	if !shareTextSupported(data) {
		return "unsupported_file"
	}
	doc, err := newShareDocument(file, shareDiskRecord(path, data))
	if err != nil {
		return "random_unavailable"
	}
	if failure := file.ReserveWrites(ctx); failure != nil {
		return failure.Code
	}
	s.shares.documents[path] = doc
	return ""
}
func shareLive(g *shareGrant) bool { return g != nil && !g.stopped && time.Now().Before(g.Expires) }
func (s *server) shareAdmin(w http.ResponseWriter, r *http.Request) {
	request, err := readShareHTTPRequest(r, 4<<20)
	if err != nil {
		shareFailure(w, 400, "invalid_request")
		return
	}
	r = request
	reply := newShareHTTPResponse(w, r.Context())
	defer reply.publish()
	w = reply

	s.shares.mu.Lock()
	defer s.shares.mu.Unlock()
	s.shareInitLocked()
	w.Header().Set("Cache-Control", "no-store")
	if r.Method == http.MethodGet {
		s.sharePruneLocked()
		out := []*shareGrant{}
		for _, g := range s.shares.grants {
			if shareLive(g) {
				out = append(out, g)
			}
		}
		paths, localPaths := []string{}, []string{}
		for path := range s.shares.documents {
			if s.shareDocumentActive(path) {
				paths = append(paths, path)
				if !s.shareGrantDocumentActive(path) {
					localPaths = append(localPaths, path)
				}
			}
		}
		generations := map[string]string{}
		for path, doc := range s.shares.documents {
			if s.shareDocumentActive(path) {
				generations[path] = doc.ID
			}
		}
		resourcePaths := map[string]string{}
		pdfs := map[string]sharePDFStatus{}
		if r.URL.Query().Get("pdf") == "1" {
			path := r.URL.Query().Get("path")
			for _, grant := range out {
				for _, resource := range grant.Resources {
					if resource.Path == path && len(resource.pdf) > 0 {
						w.Header().Set("Content-Type", "application/pdf")
						writeShareBody(w, resource.pdf)
						return
					}
				}
			}
			shareFailure(w, 404, "pdf_unavailable")
			return
		}
		peers := map[string][]shareGuestPeer{}
		sort.Slice(out, func(i, j int) bool {
			return out[i].Expires.Before(out[j].Expires) || out[i].Expires.Equal(out[j].Expires) && out[i].ID < out[j].ID
		})
		for _, grant := range out {
			list := make([]shareGuestPeer, 0, len(grant.browsers))
			for _, browser := range grant.browsers {
				list = append(list, shareGuestPeer{
					Name: browser.Name, Hue: browser.Hue, IP: browser.IP, Browser: browser.Browser,
					BrowserVersion: browser.BrowserVersion,
					JoinedAt:       browser.JoinedAt, LastSeen: browser.LastSeen,
				})
			}
			sort.Slice(list, func(i, j int) bool { return list[i].JoinedAt.Before(list[j].JoinedAt) })
			peers[grant.ID] = list
			addShareResourcePaths(resourcePaths, grant.Resources)
			for _, resource := range grant.Resources {
				if resource.Kind == "file" && isShareTeX(resource.Path) {
					status := sharePDFStatus{Revision: resource.pdfSerial}
					if doc := s.shares.documents[resource.Path]; doc != nil {
						doc.mu.Lock()
						status.Stale = resource.pdfSource != doc.Saved
						doc.mu.Unlock()
					}
					if current, ok := pdfs[resource.Path]; !ok || status.Revision > current.Revision {
						pdfs[resource.Path] = status
					}
				}
			}
		}
		body := map[string]any{"shares": out, "documents": paths, "local_documents": localPaths, "resource_paths": resourcePaths, "document_generations": generations, "peers": peers, "tex_pdfs": pdfs}
		if r.URL.Query().Get("invites") == "1" {
			invites := make(map[string]string, len(out))
			for _, grant := range out {
				invites[grant.ID] = grant.inviteURL
			}
			body["invite_urls"] = invites
		}
		jsonResponse(w, 200, body)
		return
	}
	if r.Method == http.MethodPatch {
		s.changeSharePermissionLocked(w, r)
		return
	}
	if r.Method == http.MethodDelete {
		g := s.shares.grants[r.URL.Query().Get("id")]
		if g == nil {
			shareFailure(w, 404, "share_unavailable")
			return
		}
		g.stopped = true
		if g.cancel != nil {
			g.cancel()
		}
		s.sharePruneLocked()
		jsonResponse(w, 200, map[string]any{"stopped": true})
		return
	}
	if r.Method != http.MethodPost {
		shareFailure(w, 405, "method_not_allowed")
		return
	}
	if s.httpMode {
		shareFailure(w, 409, "https_required")
		return
	}
	var b struct {
		Guest                  string           `json:"guest"`
		Minutes                int64            `json:"minutes"`
		Limit                  int              `json:"limit"`
		TerminalWriteConfirmed bool             `json:"terminal_write_confirmed"`
		Resources              []shareSelection `json:"resources"`
		Appearance             shareAppearance  `json:"appearance"`
	}
	if decodeJSONBody(r.Body, 2<<20, &b) != nil || len(b.Guest) > 100 || b.Minutes < 1 || b.Minutes > shareMaxMinutes || b.Limit < 1 || b.Limit > 1024 || len(b.Resources) == 0 || len(b.Resources) > 64 || !b.Appearance.valid() {
		shareFailure(w, 400, "invalid_share")
		return
	}
	b.Guest = strings.TrimSpace(b.Guest)
	if b.Guest == "" {
		var seed [4]byte
		if _, err := rand.Read(seed[:]); err != nil {
			shareFailure(w, 503, "random_unavailable")
			return
		}
		b.Guest = fmt.Sprintf("Guest%04d", binary.BigEndian.Uint32(seed[:])%10000)
	}
	s.sharePruneLocked()
	active := 0
	for _, g := range s.shares.grants {
		if shareLive(g) {
			if strings.EqualFold(g.Guest, b.Guest) {
				shareFailure(w, http.StatusConflict, "guest_name_in_use")
				return
			}
			active++
		}
	}
	if active >= 128 {
		shareFailure(w, 429, "share_capacity")
		return
	}
	id, err := newShareInviteToken()
	if err != nil || s.shares.grants[id] != nil {
		shareFailure(w, 503, "random_unavailable")
		return
	}
	secret, err := newShareInviteToken()
	if err != nil {
		shareFailure(w, 503, "random_unavailable")
		return
	}
	user, _ := r.Context().Value(authContextKey{}).(authUser)
	g := &shareGrant{ID: id, Revision: 1, Appearance: b.Appearance, Guest: b.Guest, Limit: b.Limit, Expires: time.Now().Add(time.Duration(b.Minutes) * time.Minute), secret: sha256.Sum256([]byte(secret)), browsers: make(map[[32]byte]*shareBrowser), owner: user.username}
	g.TerminalWriteConfirmed = b.TerminalWriteConfirmed
	g.inviteURL = "https://" + r.Host + "/share/" + id + "#" + secret
	// Prepare all resources before publishing any grant or collaboration session.
	pending := map[string]*shareDocument{}
	seen := make(map[string]bool, len(b.Resources))
	for _, selection := range b.Resources {
		resource, err := s.prepareShareResource(r.Context(), selection, b.TerminalWriteConfirmed, pending)
		if err != nil {
			shareFailure(w, 400, err.Error())
			return
		}
		if !recordShareResourceIdentity(seen, resource) {
			shareFailure(w, 400, "duplicate_resource")
			return
		}
		g.Resources = append(g.Resources, resource)
	}
	if len(pending)+len(s.shares.documents) > 32 {
		shareFailure(w, 429, "document_capacity")
		return
	}
	reserved := []*finder.GrantedFile{}
	for _, doc := range pending {
		reserved = append(reserved, doc.file)
	}
	if failure := s.finder.ReserveGrantedWrites(r.Context(), reserved...); failure != nil {
		shareFailure(w, 409, failure.Code)
		return
	}
	for path, doc := range pending {
		s.shares.documents[path] = doc
	}
	shareResourceAssets(g.Resources)
	g.lifetime, g.cancel = context.WithDeadline(context.Background(), g.Expires)
	for _, resource := range g.Resources {
		resource.renewPermission(g.lifetime)
	}
	context.AfterFunc(g.lifetime, func() { s.shares.mu.Lock(); defer s.shares.mu.Unlock(); s.sharePruneLocked() })
	s.shares.grants[id] = g
	resourcePaths := make(map[string]string)
	addShareResourcePaths(resourcePaths, g.Resources)
	jsonResponse(w, 201, map[string]any{"share": g, "url": g.inviteURL, "resource_paths": resourcePaths})
}

func recordShareResourceIdentity(seen map[string]bool, resource *shareResource) bool {
	key := resource.Kind + ":" + resource.Path + resource.Pane
	if seen[key] {
		return false
	}
	seen[key] = true
	return true
}

func addShareResourcePaths(paths map[string]string, resources []*shareResource) {
	for _, resource := range resources {
		if resource.Path != "" {
			paths[resource.ID] = resource.Path
		}
	}
}

func (resource *shareResource) renewPermission(lifetime context.Context) {
	ctx, cancel := context.WithCancel(lifetime)
	resource.permission = &sharePermission{ctx: ctx, cancel: cancel}
	resource.PermissionRevision++
}

func (s *server) changeSharePermissionLocked(w http.ResponseWriter, r *http.Request) {
	g := s.shares.grants[r.URL.Query().Get("id")]
	if !shareLive(g) {
		shareFailure(w, 410, "share_ended")
		return
	}
	var change struct {
		Resource               string            `json:"resource"`
		Writable               *bool             `json:"writable"`
		Revision               int               `json:"permission_revision"`
		GrantRevision          int               `json:"revision"`
		Resources              *[]shareSelection `json:"resources"`
		Minutes                *int              `json:"minutes"`
		Limit                  *int              `json:"limit"`
		TerminalWriteConfirmed bool              `json:"terminal_write_confirmed"`
		SavedContent           *string           `json:"saved_content"`
	}
	if decodeJSONBody(r.Body, 2<<20, &change) != nil {
		shareFailure(w, 400, "invalid_permission")
		return
	}
	if change.Minutes != nil || change.Limit != nil {
		if change.Resources != nil || change.Writable != nil || change.Resource != "" {
			shareFailure(w, 400, "invalid_share")
			return
		}
		if change.Minutes != nil && (*change.Minutes < 1 || *change.Minutes > shareMaxMinutes) {
			shareFailure(w, 400, "invalid_share_expiry")
			return
		}
		if change.Limit != nil && (*change.Limit < 1 || *change.Limit > 1024) {
			shareFailure(w, 400, "invalid_guest_limit")
			return
		}
		if change.GrantRevision != g.Revision {
			shareFailure(w, 409, "share_changed")
			return
		}
		if change.Limit != nil && *change.Limit < g.Slots {
			shareFailure(w, 409, "guest_limit_below_claimed")
			return
		}
		if change.Limit != nil {
			g.Limit = *change.Limit
		}
		if change.Minutes != nil {
			s.changeShareExpiryLocked(g, *change.Minutes)
		}
		g.Revision++
		jsonResponse(w, 200, g)
		return
	}
	if change.Resources != nil {
		s.changeShareMembershipLocked(w, r, g, change.GrantRevision, *change.Resources, change.TerminalWriteConfirmed)
		return
	}
	if change.Writable == nil {
		shareFailure(w, 400, "invalid_permission")
		return
	}
	selections := make([]shareSelection, 0, len(g.Resources))
	found := false
	for _, resource := range g.Resources {
		selection := shareSelection{ID: resource.ID, Writable: resource.Writable}
		if resource.ID == change.Resource {
			found = true
			if resource.PermissionRevision != change.Revision {
				shareFailure(w, 409, "permission_changed")
				return
			}
			if resource.Writable == *change.Writable {
				jsonResponse(w, 200, resource)
				return
			}
			selection.Writable = *change.Writable
			selection.SavedContent = change.SavedContent
		}
		selections = append(selections, selection)
	}
	if !found {
		shareFailure(w, 404, "resource_unavailable")
		return
	}
	s.changeShareMembershipLocked(w, r, g, g.Revision, selections, change.TerminalWriteConfirmed, change.Resource)
}

func (s *server) changeShareExpiryLocked(g *shareGrant, minutes int) {
	expires := time.Now().Add(time.Duration(minutes) * time.Minute)
	lifetime, cancel := context.WithDeadline(context.Background(), expires)
	oldCancel := g.cancel
	for _, resource := range g.Resources {
		prior := resource.permission
		prior.mu.Lock()
		if resource.document != nil {
			resource.document.mu.Lock()
		}
		prior.cancel()
		resource.renewPermission(lifetime)
		if resource.document != nil {
			resource.document.mu.Unlock()
		}
		prior.mu.Unlock()
	}
	g.Expires, g.lifetime, g.cancel = expires, lifetime, cancel
	context.AfterFunc(lifetime, func() { s.shares.mu.Lock(); defer s.shares.mu.Unlock(); s.sharePruneLocked() })
	if oldCancel != nil {
		oldCancel()
	}
}

func (s *server) prepareShareResource(ctx context.Context, b shareSelection, writeConfirmed bool, pending map[string]*shareDocument) (*shareResource, error) {
	id, err := shareRandom()
	if err != nil {
		return nil, err
	}
	resource := &shareResource{ID: id, Kind: b.Kind, Writable: b.Writable}
	if b.Kind == "terminal" {
		resource.CanWrite = true
		if b.Writable && !writeConfirmed {
			return nil, errors.New("confirm_terminal_commands_as_host")
		}
		if !sharePaneID(b.Pane) {
			return nil, errors.New("invalid_pane")
		}
		identity, label, err := s.sharePaneIdentity(ctx, b.Pane)
		if err != nil {
			return nil, err
		}
		resource.Pane = b.Pane
		resource.Identity = identity
		resource.Label = label
		return resource, nil
	}
	if b.Kind != "file" {
		return nil, errors.New("unsupported_resource")
	}
	path, err := filepath.Abs(b.Path)
	if err != nil {
		return nil, err
	}
	resource.Path = filepath.Clean(path)
	resource.Label = filepath.Base(path)
	var failure *finder.Error
	resource.file, failure = s.finder.GrantFile(ctx, resource.Path)
	if failure != nil {
		return nil, errors.New(failure.Code)
	}
	data, _, failure := resource.file.Read(ctx, finder.MaxRawBytes)
	if failure != nil {
		return nil, errors.New(failure.Code)
	}
	ext := strings.ToLower(filepath.Ext(path))
	passive := sharePassiveType(ext) != ""
	resource.CanWrite = !passive
	if !passive && !shareTextSupported(data) {
		return nil, errors.New("unsupported_file")
	}
	if ext == ".md" || ext == ".markdown" {
		images, err := s.prepareShareImages(ctx, resource.Path, b.ImagePaths)
		if err != nil {
			return nil, err
		}
		resource.images = images
	}
	if b.Writable {
		if passive {
			return nil, errors.New("file_is_readonly")
		}
		doc, err := s.prepareShareDocument(ctx, resource, b.SavedContent, pending)
		if err != nil {
			return nil, err
		}
		resource.document = doc
		if s.shares.documents[resource.Path] == nil {
			pending[resource.Path] = doc
		}
	}
	return resource, nil
}

const shareMarkdownImageLimit = 64

// Only the authenticated owner's share request can attach image capabilities.
// Guest edits to the Markdown never admit additional filesystem paths.
func (s *server) prepareShareImages(ctx context.Context, markdownPath string, paths []string) (map[string]shareImage, error) {
	if len(paths) > shareMarkdownImageLimit {
		return nil, errors.New("too_many_shared_images")
	}
	if len(paths) == 0 {
		return nil, nil
	}
	images := make(map[string]shareImage)
	seen := make(map[string]bool)
	directory := filepath.Dir(markdownPath)
	for _, path := range paths {
		relative := filepath.Clean(filepath.FromSlash(path))
		contentType := sharePassiveType(strings.ToLower(filepath.Ext(relative)))
		if !filepath.IsLocal(relative) || seen[relative] || !strings.HasPrefix(contentType, "image/") {
			continue
		}
		seen[relative] = true
		file, failure := s.finder.GrantFile(ctx, filepath.Join(directory, relative))
		if failure != nil {
			if ctx.Err() != nil {
				return nil, ctx.Err()
			}
			continue
		}
		token, err := shareRandom()
		if err != nil {
			return nil, err
		}
		images[token] = shareImage{relative: filepath.ToSlash(relative), contentType: contentType, file: file}
	}
	return images, nil
}

func (s *server) prepareShareDocument(ctx context.Context, resource *shareResource, saved *string, pending map[string]*shareDocument) (*shareDocument, error) {
	data, info, failure := resource.file.ReadCurrent(ctx, shareTextLimit)
	if failure != nil {
		return nil, errors.New(failure.Code)
	}
	if !shareTextSupported(data) {
		return nil, errors.New("unsupported_file")
	}
	if saved == nil || normalizeShareText(*saved) != normalizeShareText(string(data)) {
		return nil, errors.New("save_host_draft_first")
	}
	doc := s.shares.documents[resource.Path]
	if doc == nil {
		doc = pending[resource.Path]
	}
	if doc != nil {
		_, current, failure := doc.file.ReadCurrent(ctx, shareTextLimit)
		if failure != nil || !os.SameFile(info, current) {
			return nil, errors.New("file_unavailable")
		}
		return doc, nil
	}
	return newShareDocument(resource.file, shareDiskRecord(resource.Path, data))
}

// shareDiskRecord starts a document from file bytes, editing with LF and saving with the file's line ending.
func shareDiskRecord(path string, data []byte) shareRecoveryRecord {
	text := normalizeShareText(string(data))
	record := shareRecoveryRecord{Path: path, Text: text, Saved: text}
	if strings.Contains(string(data), "\r\n") {
		record.LineEnding = "\r\n"
	}
	return record
}
func sharePassiveType(ext string) string {
	switch ext {
	case ".pdf":
		return "application/pdf"
	case ".png":
		return "image/png"
	case ".jpg", ".jpeg":
		return "image/jpeg"
	case ".gif":
		return "image/gif"
	case ".webp":
		return "image/webp"
	case ".mp3":
		return "audio/mpeg"
	case ".mp4":
		return "video/mp4"
	case ".ogg":
		return "audio/ogg"
	case ".wav":
		return "audio/wav"
	case ".webm":
		return "video/webm"
	}
	return ""
}
func sharePaneID(pane string) bool {
	if len(pane) < 2 || pane[0] != '%' {
		return false
	}
	_, err := strconv.ParseUint(pane[1:], 10, 64)
	return err == nil
}

// A linked window can resolve through a different session after a host opens or
// resizes its Tabber view. The window and pane are the same physical targets.
var sharePaneIdentityFormats = [...]string{"pid", "start_time", "pane_id", "pane_pid", "window_id"}

func (s *server) sharePaneIdentity(ctx context.Context, pane string) (string, string, error) {
	format := "#{" + strings.Join(sharePaneIdentityFormats[:], "}:#{") + "}\t#{session_name}:#{window_index}.#{pane_index}"
	out, _, err := nativeCommandOutput(s.tmuxCommandContext(ctx, "display-message", "-p", "-t", pane, format), 4096)
	if err != nil {
		return "", "", errors.New("pane_unavailable")
	}
	identity, label, ok := strings.Cut(strings.TrimSpace(string(out)), "\t")
	if !ok || !strings.Contains(identity, ":"+pane+":") {
		return "", "", errors.New("pane_unavailable")
	}
	return identity, label, nil
}
func (s *server) sharePaneCommand(ctx context.Context, resource *shareResource, input, key string) (shareTerminalSnapshot, error) {
	// The condition and action execute in the same tmux command queue. A restarted
	// tmux server or replacement pane cannot inherit an old numeric pane target.
	fields := strings.Split(resource.Identity, ":")
	if len(fields) != len(sharePaneIdentityFormats) {
		return shareTerminalSnapshot{}, errors.New("pane_unavailable")
	}
	condition := "1"
	for i, f := range sharePaneIdentityFormats {
		condition = "#{&&:" + condition + ",#{==:#{" + f + "}," + fields[i] + "}}"
	}
	capture := input == "" && key == ""
	action := "display-message -p -t " + resource.Pane + " '#{cursor_x}:#{cursor_y}:#{cursor_flag}' ; capture-pane -p -t " + resource.Pane
	if !capture {
		condition = "#{&&:" + condition + ",#{!=:#{synchronize-panes},1}}"
		if key != "" {
			action = "send-keys -t " + resource.Pane + " " + key
		} else {
			var parts []string
			for _, b := range []byte(input) {
				parts = append(parts, fmt.Sprintf("%02x", b))
			}
			action = "send-keys -H -t " + resource.Pane + " " + strings.Join(parts, " ")
		}
	}
	commandContext, cancel := context.WithTimeout(ctx, time.Second)
	defer cancel()
	output, _, err := nativeCommandOutput(s.tmuxCommandContext(commandContext, "if-shell", "-F", "-t", resource.Pane, condition, action, "display-message -p YOMUX_RESOURCE_UNAVAILABLE"), 256<<10)
	if err != nil || strings.Contains(string(output), "YOMUX_RESOURCE_UNAVAILABLE") {
		return shareTerminalSnapshot{}, errors.New("pane_unavailable_or_broadcast_enabled")
	}
	if !capture {
		return shareTerminalSnapshot{}, nil
	}
	header, screen, ok := strings.Cut(string(output), "\n")
	if !ok {
		return shareTerminalSnapshot{}, errors.New("pane_unavailable")
	}
	snapshot := shareTerminalSnapshot{Text: screen}
	coords := strings.Split(header, ":")
	if len(coords) == 3 && coords[2] == "1" {
		column, columnErr := strconv.Atoi(coords[0])
		row, rowErr := strconv.Atoi(coords[1])
		if columnErr == nil && rowErr == nil && column >= 0 && row >= 0 {
			snapshot.Cursor = &shareTerminalCursor{Column: column, Row: row}
		}
	}
	return snapshot, nil
}
func shareBrowserDetails(userAgent string) (string, string) {
	lower := strings.ToLower(userAgent)
	name, versionMarker := "Unknown", ""
	switch {
	// Chromium-based browsers commonly include Chrome and Safari tokens too.
	case strings.Contains(lower, "edg/"):
		name, versionMarker = "Edge", "edg/"
	case strings.Contains(lower, "edga/"):
		name, versionMarker = "Edge", "edga/"
	case strings.Contains(lower, "edgios/"):
		name, versionMarker = "Edge", "edgios/"
	case strings.Contains(lower, "edge/"):
		name, versionMarker = "Edge", "edge/"
	case strings.Contains(lower, "opr/"):
		name, versionMarker = "Opera", "opr/"
	case strings.Contains(lower, "opios/"):
		name, versionMarker = "Opera", "opios/"
	case strings.Contains(lower, "opera/"):
		name, versionMarker = "Opera", "opera/"
	case strings.Contains(lower, "samsungbrowser/"):
		name, versionMarker = "Samsung Internet", "samsungbrowser/"
	case strings.Contains(lower, "vivaldi/"):
		name, versionMarker = "Vivaldi", "vivaldi/"
	case strings.Contains(lower, "firefox/"):
		name, versionMarker = "Firefox", "firefox/"
	case strings.Contains(lower, "fxios/"):
		name, versionMarker = "Firefox", "fxios/"
	case strings.Contains(lower, "chromium/"):
		name, versionMarker = "Chromium", "chromium/"
	case strings.Contains(lower, "crios/"):
		name, versionMarker = "Chrome", "crios/"
	case strings.Contains(lower, "chrome/"):
		name, versionMarker = "Chrome", "chrome/"
	case strings.Contains(lower, "safari/"):
		name, versionMarker = "Safari", "version/"
	}
	if versionMarker == "" {
		return name, ""
	}
	index := strings.Index(lower, versionMarker)
	if index < 0 {
		return name, ""
	}
	start := index + len(versionMarker)
	end := start
	for end < len(lower) && ((lower[end] >= '0' && lower[end] <= '9') || lower[end] == '.') {
		end++
	}
	return name, lower[start:end]
}

func (s *server) shareGuest(w http.ResponseWriter, r *http.Request) {
	request, err := readShareHTTPRequest(r, 4<<20)
	if err != nil {
		shareFailure(w, 400, "invalid_request")
		return
	}
	r = request
	reply := newShareHTTPResponse(w, r.Context())
	defer reply.publish()
	w = reply

	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Referrer-Policy", "no-referrer")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	if !s.admitBrowserRequest(w, r, r.Method != http.MethodGet) {
		return
	}
	path := strings.Split(strings.TrimPrefix(r.URL.Path, "/share/"), "/")
	if len(path[0]) != shareInviteTokenLength && len(path[0]) != 64 {
		shareFailure(w, 404, "share_unavailable")
		return
	}
	if len(path) == 1 && r.Method == http.MethodGet {
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		body, err := s.renderNativeGuestWorkspace(r)
		if err != nil {
			shareFailure(w, 503, "page_unavailable")
			return
		}
		io.WriteString(w, string(body))
		return
	}
	if len(path) != 2 {
		shareFailure(w, 404, "resource_unavailable")
		return
	}
	s.shares.mu.Lock()
	locked := true
	defer func() {
		if locked {
			s.shares.mu.Unlock()
		}
	}()
	s.shareInitLocked()
	g := s.shares.grants[path[0]]
	if !shareLive(g) {
		shareFailure(w, 410, "share_ended")
		return
	}
	if path[1] == "join" || path[1] == "state" || path[1] == "chat" {
		reply.scope(g.lifetime)
	}
	cookieName := "yomux_guest_" + strconv.Itoa(s.port) + "_" + g.ID
	var browser *shareBrowser
	var cookieValue string
	if cookie, err := r.Cookie(cookieName); err == nil {
		cookieValue = cookie.Value
		browser = g.browsers[sha256.Sum256([]byte(cookieValue))]
	}
	writeGuestState := func() {
		browser.IP, browser.LastSeen = chatPeerIP(r), time.Now()
		browser.Browser, browser.BrowserVersion = shareBrowserDetails(r.UserAgent())
		http.SetCookie(w, &http.Cookie{Name: cookieName, Value: cookieValue, Path: "/share/" + g.ID + "/", Secure: true, HttpOnly: true, SameSite: http.SameSiteStrictMode, Expires: g.Expires})
		jsonResponse(w, 200, map[string]any{"name": browser.Name, "owner": g.owner, "hue": browser.Hue, "share": g})
	}
	if path[1] == "join" && r.Method == http.MethodPost {
		if browser == nil {
			var b struct {
				Secret string `json:"secret"`
			}
			if decodeJSONBody(r.Body, 1024, &b) != nil {
				shareFailure(w, 400, "invalid_join")
				return
			}
			digest := sha256.Sum256([]byte(b.Secret))
			if subtle.ConstantTimeCompare(digest[:], g.secret[:]) != 1 {
				shareFailure(w, 403, "invalid_join")
				return
			}
			if g.Slots >= g.Limit {
				shareGuestLimitReached(w, g.Appearance)
				return
			}
			credential, err := shareRandom()
			if err != nil {
				shareFailure(w, 503, "random_unavailable")
				return
			}
			g.Slots++
			name := g.Guest
			if g.Slots > 1 {
				name += " " + strconv.Itoa(g.Slots)
			}
			browser = &shareBrowser{Name: name, Hue: s.availableShareHueLocked(1), JoinedAt: time.Now(), credential: sha256.Sum256([]byte(credential))}
			g.browsers[browser.credential] = browser
			cookieValue = credential
		}
		writeGuestState()
		return
	}
	if browser == nil {
		if path[1] == "state" && r.Method == http.MethodGet && g.Slots >= g.Limit {
			shareGuestLimitReached(w, g.Appearance)
			return
		}
		shareFailure(w, 401, "join_required")
		return
	}
	if path[1] == "chat" {
		s.shareChatRequest(w, r, g, browser.Name, browser.Hue, false)
		return
	}
	if path[1] == "state" && r.Method == http.MethodGet {
		writeGuestState()
		return
	}
	var resource *shareResource
	for _, candidate := range g.Resources {
		if candidate.ID == path[1] {
			resource = candidate
			break
		}
	}
	if resource == nil {
		shareFailure(w, 404, "resource_unavailable")
		return
	}
	if revision := r.URL.Query().Get("permission"); revision != "" && revision != strconv.Itoa(resource.PermissionRevision) {
		shareFailure(w, 409, "permission_changed")
		return
	}
	reply.scope(resource.permission.ctx)
	ctx, cancel := context.WithDeadline(r.Context(), g.Expires)
	defer cancel()
	if g.lifetime != nil {
		stop := context.AfterFunc(g.lifetime, cancel)
		defer stop()
	}
	stopPermission := context.AfterFunc(resource.permission.ctx, cancel)
	defer stopPermission()
	if resource.permission.ctx.Err() != nil {
		shareFailure(w, 409, "permission_changed")
		return
	}
	snapshot := *resource
	resource = &snapshot
	s.shares.mu.Unlock()
	locked = false
	w = reply.release()
	failCancelled := func() {
		s.shares.mu.Lock()
		live := shareLive(s.shares.grants[path[0]])
		s.shares.mu.Unlock()
		if live {
			shareFailure(w, 409, "permission_changed")
		} else {
			shareFailure(w, 410, "share_ended")
		}
	}
	if r.URL.Query().Has("image") {
		image, ok := resource.images[r.URL.Query().Get("image")]
		if !ok {
			shareFailure(w, 404, "resource_unavailable")
			return
		}
		if r.Method != http.MethodGet {
			shareFailure(w, 403, "read_only")
			return
		}
		data, _, failure := image.file.ReadCurrent(ctx, finder.MaxRawBytes)
		if ctx.Err() != nil {
			failCancelled()
			return
		}
		if failure != nil || http.DetectContentType(data) != image.contentType {
			shareFailure(w, 409, "file_unavailable")
			return
		}
		w.Header().Set("Content-Type", image.contentType)
		writeShareBody(w, data)
		return
	}
	if resource.Kind == "terminal" {
		input, key := "", ""
		var permission *sharePermission
		if r.Method == http.MethodPost {
			if !resource.Writable {
				shareFailure(w, 403, "read_only")
				return
			}
			var b struct {
				Text string `json:"text"`
				Key  string `json:"key"`
			}
			if decodeJSONBody(r.Body, 16<<10, &b) != nil || len(b.Text) > 4096 || (b.Text != "" && b.Key != "") {
				shareFailure(w, 400, "invalid_input")
				return
			}
			if b.Key != "" && !strings.Contains("|Enter|Escape|Tab|BSpace|Up|Down|Left|Right|C-c|C-d|Home|End|PPage|NPage|", "|"+b.Key+"|") {
				shareFailure(w, 400, "invalid_key")
				return
			}
			input, key = b.Text, b.Key
			permission = resource.permission
			permission.mu.Lock()
			if permission.ctx.Err() != nil {
				permission.mu.Unlock()
				shareFailure(w, 409, "permission_changed")
				return
			}
		} else if r.Method != http.MethodGet {
			shareFailure(w, 405, "method_not_allowed")
			return
		}
		snapshot, err := s.sharePaneCommand(ctx, resource, input, key)
		if permission != nil {
			permission.mu.Unlock()
		}
		if err != nil {
			shareFailure(w, 409, err.Error())
			return
		}
		jsonResponse(w, 200, snapshot)
		return
	}
	if r.URL.Query().Get("pdf") == "1" && len(resource.pdf) > 0 && r.Method == http.MethodGet {
		w.Header().Set("Content-Type", "application/pdf")
		writeShareBody(w, resource.pdf)
		return
	}
	if ctx.Err() != nil {
		failCancelled()
		return
	}
	if resource.Writable {
		s.shareDocumentRequest(w, r.WithContext(ctx), resource.document, browser.Name, browser.Hue, hex.EncodeToString(browser.credential[:]), false, resource)
		return
	}
	if r.Method != http.MethodGet {
		shareFailure(w, 403, "read_only")
		return
	}
	readLimit := int64(shareTextLimit)
	contentType := sharePassiveType(strings.ToLower(filepath.Ext(resource.Path)))
	if contentType != "" {
		readLimit = finder.MaxRawBytes
	}
	data, _, failure := resource.file.ReadCurrent(ctx, readLimit)
	if failure != nil {
		shareFailure(w, 409, "file_unavailable")
		return
	}
	if ctx.Err() != nil {
		failCancelled()
		return
	}
	if contentType != "" {
		w.Header().Set("Content-Type", contentType)
		w.Header().Set("Content-Disposition", "inline; filename="+strconv.Quote(resource.Label))
		writeShareBody(w, data)
		return
	}
	if !shareTextSupported(data) {
		shareFailure(w, 409, "file_unavailable")
		return
	}
	text := normalizeShareText(string(data))
	response := map[string]any{"text": string(data), "extension": strings.ToLower(filepath.Ext(resource.Path)), "pdf_available": len(resource.pdf) > 0, "pdf_revision": resource.pdfSerial, "pdf_stale": text != resource.pdfSource}
	if owner := resource.ownerCaret; owner != nil && time.Since(owner.cursor.At) < 8*time.Second &&
		sha256.Sum256([]byte(text)) == owner.digest {
		response["presence"] = []sharePresence{owner.cursor}
	}
	jsonResponse(w, 200, response)
}

// The caller holds doc.mu.
func shareCompiledRevision(doc *shareDocument, source string) int {
	if doc.Text != source {
		return -1
	}
	return len(doc.operations)
}

func (s *server) sharePublishPDF(path, source string, pdf []byte) error {
	s.shares.mu.Lock()
	defer s.shares.mu.Unlock()
	return s.sharePublishPDFLocked(path, source, pdf, nil)
}

// The caller holds the registry lock and may already hold lockedDoc.mu.
func (s *server) sharePublishPDFLocked(path, source string, pdf []byte, lockedDoc *shareDocument) error {
	if len(pdf) > 32<<20 {
		return errors.New("pdf_too_large")
	}
	total := 0
	for _, g := range s.shares.grants {
		for _, resource := range g.Resources {
			if resource.Path != path {
				total += len(resource.pdf)
			} else {
				total += len(pdf)
			}
		}
	}
	if total > 64<<20 {
		return errors.New("publication_capacity")
	}
	s.shares.publication++
	for _, g := range s.shares.grants {
		if !shareLive(g) {
			continue
		}
		for _, r := range g.Resources {
			if r.Path == path {
				r.pdf = pdf
				r.pdfSource = source
				r.pdfSerial = s.shares.publication
				if r.document != nil {
					if r.document != lockedDoc {
						r.document.mu.Lock()
					}
					r.pdfRevision = shareCompiledRevision(r.document, source)
					if r.document != lockedDoc {
						r.document.mu.Unlock()
					}
				}
			}
		}
	}
	return nil
}

type shareChatMessage struct {
	Name  string    `json:"name"`
	Hue   int       `json:"hue"`
	Owner bool      `json:"owner,omitempty"`
	Text  string    `json:"text"`
	At    time.Time `json:"at"`
}

func (s *server) shareChatRequest(w http.ResponseWriter, r *http.Request, g *shareGrant, name string, hue int, owner bool) {
	if r.Method == http.MethodPost {
		var b struct {
			Text string `json:"text"`
		}
		if decodeJSONBody(r.Body, 32<<10, &b) != nil || len(b.Text) > 4096 || strings.TrimSpace(b.Text) == "" {
			shareFailure(w, 400, "invalid_message")
			return
		}
		g.messages = append(g.messages, shareChatMessage{Name: name, Hue: hue, Owner: owner, Text: b.Text, At: time.Now()})
		if len(g.messages) > 256 {
			g.messages = append([]shareChatMessage(nil), g.messages[len(g.messages)-256:]...)
		}
	} else if r.Method != http.MethodGet {
		shareFailure(w, 405, "method_not_allowed")
		return
	}
	messages := g.messages
	if messages == nil {
		messages = []shareChatMessage{}
	}
	jsonResponse(w, 200, map[string]any{"messages": messages})
}
func (s *server) shareHostChat(w http.ResponseWriter, r *http.Request) {
	request, err := readShareHTTPRequest(r, 4<<20)
	if err != nil {
		shareFailure(w, 400, "invalid_request")
		return
	}
	r = request
	reply := newShareHTTPResponse(w, r.Context())
	defer reply.publish()
	w = reply

	s.shares.mu.Lock()
	defer s.shares.mu.Unlock()
	g := s.shares.grants[r.URL.Query().Get("id")]
	if !shareLive(g) {
		shareFailure(w, 410, "share_ended")
		return
	}
	user, _ := r.Context().Value(authContextKey{}).(authUser)
	var hue int
	if r.Method == http.MethodPost {
		hue = s.shareHostHueLocked(user.username)
	}
	s.shareChatRequest(w, r, g, user.username, hue, user.username == g.owner)
}
func (s *server) sharePruneLocked() {
	for id, g := range s.shares.grants {
		if !shareLive(g) {
			if g.cancel != nil {
				g.cancel()
			}
			delete(s.shares.grants, id)
		}
	}
	for path, doc := range s.shares.documents {
		if !doc.mu.TryLock() {
			continue
		}
		active := s.shareDocumentActive(path)
		if !s.shareGrantIncludes(path, false) {
			doc.cancelTeX()
		}
		if !active && doc.Text == doc.Saved {
			ctx, cancel := context.WithTimeout(context.Background(), time.Second)
			failure := doc.file.ReleaseWrites(ctx)
			cancel()
			if failure == nil {
				delete(s.shares.documents, path)
				s.shares.historyBytes.Add(-int64(doc.historyBytes))
			}
		}
		doc.mu.Unlock()
	}
}

func shareResourceAssets(resources []*shareResource) {
	for _, resource := range resources {
		if resource.Kind != "file" {
			continue
		}
		resource.Assets = make(map[string]string)
		for token, image := range resource.images {
			resource.Assets[image.relative] = resource.ID + "?image=" + token
		}
		for _, asset := range resources {
			if asset.Kind != "file" || !strings.HasPrefix(sharePassiveType(strings.ToLower(filepath.Ext(asset.Path))), "image/") {
				continue
			}
			relative, err := filepath.Rel(filepath.Dir(resource.Path), asset.Path)
			if err == nil {
				resource.Assets[filepath.ToSlash(relative)] = asset.ID
			}
		}
	}
}

func (s *server) changeShareMembershipLocked(w http.ResponseWriter, r *http.Request, g *shareGrant, revision int, selections []shareSelection, confirmed bool, responseResource ...string) {
	if revision != g.Revision {
		shareFailure(w, 409, "share_changed")
		return
	}
	if len(selections) > 64 {
		shareFailure(w, 400, "invalid_share")
		return
	}
	existing := make(map[string]*shareResource, len(g.Resources))
	for _, resource := range g.Resources {
		existing[resource.ID] = resource
	}
	pending := map[string]*shareDocument{}
	resources := make([]*shareResource, 0, len(selections))
	changed := make(map[string]bool)
	seen := make(map[string]bool, len(selections))
	for _, selection := range selections {
		var resource *shareResource
		if selection.ID != "" {
			prior := existing[selection.ID]
			if prior == nil {
				shareFailure(w, 409, "resource_unavailable")
				return
			}
			if selection.Kind != "" && selection.Kind != prior.Kind || selection.Path != "" && filepath.Clean(selection.Path) != prior.Path || selection.Pane != "" && selection.Pane != prior.Pane {
				shareFailure(w, 400, "resource_identity_changed")
				return
			}
			clone := *prior
			resource = &clone
			if resource.Writable != selection.Writable {
				changed[resource.ID] = true
				if selection.Writable {
					if !resource.CanWrite {
						shareFailure(w, 403, "file_is_readonly")
						return
					}
					if resource.Kind == "terminal" {
						if !confirmed {
							shareFailure(w, 400, "confirm_terminal_commands_as_host")
							return
						}
						identity, _, err := s.sharePaneIdentity(r.Context(), resource.Pane)
						if err != nil || identity != resource.Identity {
							shareFailure(w, 409, "pane_unavailable")
							return
						}
					} else {
						doc, err := s.prepareShareDocument(r.Context(), resource, selection.SavedContent, pending)
						if err != nil {
							shareFailure(w, 409, err.Error())
							return
						}
						resource.document = doc
						if s.shares.documents[resource.Path] == nil {
							pending[resource.Path] = doc
						}
					}
				} else {
					resource.document = nil
				}
				resource.Writable = selection.Writable
			}
		} else {
			var err error
			resource, err = s.prepareShareResource(r.Context(), selection, confirmed, pending)
			if err != nil {
				shareFailure(w, 400, err.Error())
				return
			}
		}
		if !recordShareResourceIdentity(seen, resource) {
			shareFailure(w, 400, "duplicate_resource")
			return
		}
		resources = append(resources, resource)
	}
	if len(pending)+len(s.shares.documents) > 32 {
		shareFailure(w, 429, "document_capacity")
		return
	}
	reserved := make([]*finder.GrantedFile, 0, len(pending))
	for _, doc := range pending {
		reserved = append(reserved, doc.file)
	}
	if failure := s.finder.ReserveGrantedWrites(r.Context(), reserved...); failure != nil {
		shareFailure(w, 409, failure.Code)
		return
	}
	retained := make(map[string]bool, len(resources))
	for _, resource := range resources {
		retained[resource.ID] = true
	}
	// Fence every request admitted under a removed or changed resource before
	// publishing the new membership. The registry lock excludes new admissions.
	for _, prior := range g.Resources {
		if retained[prior.ID] && !changed[prior.ID] {
			continue
		}
		prior.permission.mu.Lock()
		if prior.document != nil {
			prior.document.mu.Lock()
		}
		prior.permission.cancel()
		if prior.document != nil {
			prior.document.mu.Unlock()
		}
		prior.permission.mu.Unlock()
	}
	for path, doc := range pending {
		s.shares.documents[path] = doc
	}
	for _, resource := range resources {
		if existing[resource.ID] == nil || changed[resource.ID] {
			resource.renewPermission(g.lifetime)
			if resource.document != nil && len(resource.pdf) > 0 {
				resource.document.mu.Lock()
				resource.pdfRevision = shareCompiledRevision(resource.document, resource.pdfSource)
				resource.document.mu.Unlock()
			}
		}
	}
	shareResourceAssets(resources)
	g.Resources = resources
	g.TerminalWriteConfirmed = g.TerminalWriteConfirmed || confirmed
	g.Revision++
	s.sharePruneLocked()
	if len(responseResource) > 0 {
		for _, resource := range resources {
			if resource.ID == responseResource[0] {
				jsonResponse(w, 200, resource)
				return
			}
		}
	}
	jsonResponse(w, 200, g)
}

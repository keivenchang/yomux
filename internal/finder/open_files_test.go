// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package finder

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func registerTestOpenFile(t *testing.T, s *Service, client, path, identity, id string, roots []any) map[string]any {
	t.Helper()
	response, err := s.WatchRoots(context.Background(), map[string]any{"client_id": client, "roots": roots, "open_files": []any{map[string]any{"path": path, "file_id": identity, "tracking_id": id}}})
	if err != nil {
		t.Fatal(err)
	}
	rows := response["open_files"].([]map[string]any)
	if len(rows) != 1 {
		t.Fatalf("open_files=%v", rows)
	}
	return rows[0]
}

func TestOpenFilesFollowRenameSharedLeaseAndReconnect(t *testing.T) {
	s, root := newTestService(t, nil)
	old := filepath.Join(root, "note.md")
	info, err := os.Stat(old)
	if err != nil {
		t.Fatal(err)
	}
	identity := fileID(info)
	first := registerTestOpenFile(t, s, "one", old, identity, "", []any{root})
	second := registerTestOpenFile(t, s, "two", old, identity, "", []any{root})
	if first["tracking_id"] != second["tracking_id"] {
		t.Fatal("clients did not share registry entry")
	}
	next := filepath.Join(root, "src", "moved.md")
	if err := os.Rename(old, next); err != nil {
		t.Fatal(err)
	}
	s.reconcileOpenFiles()
	snapshot := registerTestOpenFile(t, s, "one", old, identity, first["tracking_id"].(string), []any{root})
	if snapshot["path"] != next || snapshot["revision"] != uint64(2) {
		t.Fatalf("snapshot=%v", snapshot)
	}
	if _, err := s.WatchRoots(context.Background(), map[string]any{"client_id": "one", "roots": []any{root}}); err != nil {
		t.Fatal(err)
	}
	s.mu.Lock()
	remaining := len(s.openFiles)
	s.mu.Unlock()
	if remaining != 1 {
		t.Fatalf("second client's lease lost: %d", remaining)
	}
	s.pruneExpiredWatches(time.Now().Add(watchLeaseDuration + time.Second))
	s.mu.Lock()
	remaining = len(s.openFiles)
	s.mu.Unlock()
	if remaining != 0 {
		t.Fatalf("expired descriptors retained: %d", remaining)
	}
}

func TestOpenFilesRotateAtomicSavesAndRejectStaleWrites(t *testing.T) {
	s, root := newTestService(t, nil)
	path := filepath.Join(root, "note.md")
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	identity := fileID(info)
	snapshot := registerTestOpenFile(t, s, "one", path, identity, "", []any{root})
	id := snapshot["tracking_id"].(string)
	for i := 0; i < 3; i++ {
		result, writeErr := s.Write(context.Background(), path, fmt.Sprintf("save %d", i), nil, identity)
		if writeErr != nil {
			t.Fatal(writeErr)
		}
		identity = result["file_id"].(string)
		next := filepath.Join(root, fmt.Sprintf("renamed-%d.md", i))
		if err := os.Rename(path, next); err != nil {
			t.Fatal(err)
		}
		s.reconcileOpenFiles()
		if _, writeErr := s.Write(context.Background(), path, "stale", nil, identity); writeErr == nil || writeErr.Status != 409 {
			t.Fatalf("stale write=%v", writeErr)
		}
		if _, err := os.Stat(path); !os.IsNotExist(err) {
			t.Fatalf("old path recreated: %v", err)
		}
		snapshot = registerTestOpenFile(t, s, "one", path, identity, id, []any{root})
		if snapshot["path"] != next {
			t.Fatalf("saved descriptor lost rename: %v", snapshot)
		}
		path = next
	}
	replacement := filepath.Join(root, "replacement")
	if err := os.WriteFile(replacement, []byte("external"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.Rename(replacement, path); err != nil {
		t.Fatal(err)
	}
	if _, err := s.Write(context.Background(), path, "overwrite", nil, identity); err == nil || err.Status != 409 {
		t.Fatalf("replaced write=%v", err)
	}
}

func TestOpenFilesAliasAndScope(t *testing.T) {
	s, root := newTestService(t, nil)
	original := filepath.Join(root, "note.md")
	alias := filepath.Join(root, "alias.md")
	if err := os.Link(original, alias); err != nil {
		t.Fatal(err)
	}
	info, err := os.Stat(original)
	if err != nil {
		t.Fatal(err)
	}
	one := registerTestOpenFile(t, s, "one", original, fileID(info), "", nil)
	two := registerTestOpenFile(t, s, "two", alias, fileID(info), "", nil)
	if one["tracking_id"] == two["tracking_id"] {
		t.Fatal("aliases merged")
	}
	// The other registered open-file parent scope is root, never its parent.
	outside := filepath.Join(t.TempDir(), "outside.md")
	if err := os.Rename(original, outside); err != nil {
		t.Fatal(err)
	}
	// Darwin's vnode path cache can now name the surviving hardlink instead.
	other, err := os.Open(alias)
	if err != nil {
		t.Fatal(err)
	}
	defer other.Close()
	s.reconcileOpenFiles()
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.openFiles[one["tracking_id"].(string)].path != original {
		t.Fatalf("followed outside open-parent scope: original=%s actual=%s outside=%s", original, s.openFiles[one["tracking_id"].(string)].path, outside)
	}
	if s.openFiles[two["tracking_id"].(string)].path != alias {
		t.Fatal("surviving alias followed another hardlink")
	}
}

func TestOpenFilesHardlinkAmbiguitySurvivesAliasRemoval(t *testing.T) {
	for _, registered := range []bool{false, true} {
		for _, late := range []bool{false, true} {
			for _, unlinkOriginal := range []bool{false, true} {
				t.Run(fmt.Sprintf("registered=%t/late=%t/unlink=%t", registered, late, unlinkOriginal), func(t *testing.T) {
					s, root := newTestService(t, nil)
					original := filepath.Join(root, "note.md")
					alias := filepath.Join(root, "alias.md")
					link := func() {
						if err := os.Link(original, alias); err != nil {
							t.Fatal(err)
						}
					}
					if !late {
						link()
					}
					info, err := os.Stat(original)
					if err != nil {
						t.Fatal(err)
					}
					row := registerTestOpenFile(t, s, "one", original, fileID(info), "", nil)
					if late {
						link()
					}
					if registered {
						registerTestOpenFile(t, s, "two", alias, fileID(info), "", nil)
					}
					// Observe multiple names before one is removed; confidence must not
					// return merely because the remaining link count becomes one.
					s.reconcileOpenFiles()
					if unlinkOriginal {
						if err := os.Remove(original); err != nil {
							t.Fatal(err)
						}
					} else if err := os.Rename(original, filepath.Join(t.TempDir(), "outside.md")); err != nil {
						t.Fatal(err)
					}
					other, err := os.Open(alias)
					if err != nil {
						t.Fatal(err)
					}
					defer other.Close()
					s.reconcileOpenFiles()
					s.mu.Lock()
					got := s.openFiles[row["tracking_id"].(string)].path
					s.mu.Unlock()
					if got != original {
						t.Fatalf("hardlink alias replaced document path: %s -> %s", original, got)
					}
				})
			}
		}
	}
}

func TestOpenFilesHardlinkRegistrationPropagatesAmbiguity(t *testing.T) {
	for _, registration := range []string{"alias", "same-path", "retained-id"} {
		t.Run(registration, func(t *testing.T) {
			s, root := newTestService(t, nil)
			original := filepath.Join(root, "note.md")
			alias := filepath.Join(root, "alias.md")
			info, err := os.Stat(original)
			if err != nil {
				t.Fatal(err)
			}
			row := registerTestOpenFile(t, s, "one", original, fileID(info), "", nil)
			if err := os.Link(original, alias); err != nil {
				t.Fatal(err)
			}
			path, id := original, ""
			if registration == "alias" {
				path = alias
			} else if registration == "retained-id" {
				id = row["tracking_id"].(string)
			}
			registerTestOpenFile(t, s, "two", path, fileID(info), id, nil)
			if err := os.Remove(original); err != nil {
				t.Fatal(err)
			}
			other, err := os.Open(alias)
			if err != nil {
				t.Fatal(err)
			}
			defer other.Close()
			s.reconcileOpenFiles()
			s.mu.Lock()
			got := s.openFiles[row["tracking_id"].(string)].path
			s.mu.Unlock()
			if got != original {
				t.Fatalf("registration lost observed hardlink ambiguity: %s -> %s", original, got)
			}
		})
	}
}

func TestOpenFilesAtomicSaveRestoresUnambiguousRename(t *testing.T) {
	s, root := newTestService(t, nil)
	original := filepath.Join(root, "note.md")
	alias := filepath.Join(root, "alias.md")
	if err := os.Link(original, alias); err != nil {
		t.Fatal(err)
	}
	info, err := os.Stat(original)
	if err != nil {
		t.Fatal(err)
	}
	row := registerTestOpenFile(t, s, "one", original, fileID(info), "", []any{root})
	other := registerTestOpenFile(t, s, "two", alias, fileID(info), "", []any{root})
	if _, writeErr := s.Write(context.Background(), original, "saved", nil, fileID(info)); writeErr != nil {
		t.Fatal(writeErr)
	}
	s.mu.Lock()
	unchanged := s.openFiles[other["tracking_id"].(string)].fileID == fileID(info)
	s.mu.Unlock()
	if !unchanged {
		t.Fatal("atomic save rotated a distinct hardlink")
	}
	next := filepath.Join(root, "saved.md")
	if err := os.Rename(original, next); err != nil {
		t.Fatal(err)
	}
	s.reconcileOpenFiles()
	s.mu.Lock()
	got := s.openFiles[row["tracking_id"].(string)].path
	s.mu.Unlock()
	if got != next {
		t.Fatalf("atomic save did not restore rename following: %s", got)
	}
}

func TestOpenFilesCapacityIsSharedAndDescriptorsClose(t *testing.T) {
	s, root := newTestService(t, nil)
	rows := make([]any, 0, maxActiveWatchFiles)
	for i := 0; i < maxActiveWatchFiles; i++ {
		path := filepath.Join(root, fmt.Sprintf("file-%d", i))
		if err := os.WriteFile(path, []byte("x"), 0600); err != nil {
			t.Fatal(err)
		}
		info, err := os.Stat(path)
		if err != nil {
			t.Fatal(err)
		}
		rows = append(rows, map[string]any{"path": path, "file_id": fileID(info)})
	}
	for _, client := range []string{"one", "two"} {
		if _, err := s.WatchRoots(context.Background(), map[string]any{"client_id": client, "open_files": rows}); err != nil {
			t.Fatal(err)
		}
	}
	s.mu.Lock()
	count := len(s.openFiles)
	var retained *os.File
	for _, entry := range s.openFiles {
		retained = entry.file
		break
	}
	s.mu.Unlock()
	if count != maxActiveWatchFiles {
		t.Fatalf("shared descriptor count %d", count)
	}
	path := filepath.Join(root, "note.md")
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := s.WatchRoots(context.Background(), map[string]any{"client_id": "three", "open_files": []any{map[string]any{"path": path, "file_id": fileID(info)}}}); err == nil || err.Status != 429 {
		t.Fatalf("capacity error %v", err)
	}
	for _, client := range []string{"one", "two"} {
		if _, err := s.WatchRoots(context.Background(), map[string]any{"client_id": client}); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := retained.Stat(); err == nil {
		t.Fatal("last close retained descriptor")
	}
}

func TestOpenFilesParentRenameAndReplacementDoNotReanchor(t *testing.T) {
	s, root := newTestService(t, nil)
	directory := filepath.Join(root, "src")
	path := filepath.Join(directory, "child.md")
	if err := os.WriteFile(path, []byte("old"), 0600); err != nil {
		t.Fatal(err)
	}
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	row := registerTestOpenFile(t, s, "one", path, fileID(info), "", []any{root})
	nextDirectory := filepath.Join(root, "renamed")
	if err := os.Rename(directory, nextDirectory); err != nil {
		t.Fatal(err)
	}
	s.reconcileOpenFiles()
	next := filepath.Join(nextDirectory, "child.md")
	s.mu.Lock()
	entry := s.openFiles[row["tracking_id"].(string)]
	got := entry.path
	s.mu.Unlock()
	if got != next {
		t.Fatalf("parent rename path %s", got)
	}
	replacement := filepath.Join(root, "replacement.md")
	if err := os.WriteFile(replacement, []byte("replacement"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.Rename(replacement, next); err != nil {
		t.Fatal(err)
	}
	s.reconcileOpenFiles()
	s.mu.Lock()
	defer s.mu.Unlock()
	if entry.fileID != fileID(info) || entry.revision != 2 {
		t.Fatal("external replacement reanchored document")
	}
	refs, registerErr := s.registerOpenFilesLocked([]any{map[string]any{"path": next, "file_id": fileID(info)}})
	if registerErr != nil || len(refs) != 1 {
		t.Fatalf("existing retained document registration %v %v", refs, registerErr)
	}
	// A new registration after a server restart has no retained inode to follow.
	other := &Service{openFiles: map[string]*openFileRecord{}, watches: map[string]watchRecord{}}
	refs, registerErr = other.registerOpenFilesLocked([]any{map[string]any{"path": next, "file_id": fileID(info)}})
	if registerErr != nil || len(refs) != 0 {
		t.Fatalf("mismatched identity reanchored %v %v", refs, registerErr)
	}
}

func TestOpenFilesParentRenameTickerWithStaleHotRegistrations(t *testing.T) {
	events := make(chan map[string]any, 4)
	s, root := newTestService(t, func(kind string, payload map[string]any) error {
		if kind == "file_relocated" {
			events <- payload
		}
		return nil
	})
	directory := filepath.Join(root, "src")
	path := filepath.Join(directory, "child.md")
	if err := os.WriteFile(path, []byte("old"), 0600); err != nil {
		t.Fatal(err)
	}
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	payloads := make([]map[string]any, 2)
	for i, client := range []string{"one", "two"} {
		payloads[i] = map[string]any{"client_id": client, "roots": []any{root}, "files": []any{path}, "open_files": []any{map[string]any{"path": path, "file_id": fileID(info)}}}
		if _, err := s.WatchRoots(context.Background(), payloads[i]); err != nil {
			t.Fatal(err)
		}
	}
	nextDirectory := filepath.Join(root, "renamed")
	if err := os.Rename(directory, nextDirectory); err != nil {
		t.Fatal(err)
	}
	registrationResults := make(chan *Error, 2)
	for _, payload := range payloads {
		go func(payload map[string]any) {
			_, err := s.WatchRoots(context.Background(), payload)
			registrationResults <- err
		}(payload)
	}
	var registrationErrors []*Error
	for i := 0; i < 2; i++ {
		if err := <-registrationResults; err != nil {
			registrationErrors = append(registrationErrors, err)
		}
	}
	select {
	case event := <-events:
		if event["new_path"] != filepath.Join(nextDirectory, "child.md") {
			t.Fatalf("event=%v", event)
		}
	case <-time.After(4 * time.Second):
		t.Fatal("actual service ticker emitted no relocation event")
	}
	if len(registrationErrors) > 0 {
		t.Fatalf("ticker relocated, but stale hot registrations rejected: %v", registrationErrors)
	}
	for _, payload := range payloads {
		response, registrationErr := s.WatchRoots(context.Background(), payload)
		if registrationErr != nil {
			t.Fatal(registrationErr)
		}
		rows := response["open_files"].([]map[string]any)
		if len(rows) != 1 || rows[0]["path"] != filepath.Join(nextDirectory, "child.md") {
			t.Fatalf("reconnect snapshot %v", rows)
		}
		if len(response["files"].([]string)) != 0 {
			t.Fatalf("missing hot path retained %v", response["files"])
		}
	}
	unknown := map[string]any{"client_id": "unknown", "roots": []any{root}, "files": []any{path}}
	if _, registrationErr := s.WatchRoots(context.Background(), unknown); registrationErr == nil || registrationErr.Status != 404 {
		t.Fatalf("unregistered missing path accepted: %v", registrationErr)
	}
}

func TestOpenFilesHiddenClientReconnectOmitsMissingParentRoot(t *testing.T) {
	s, root := newTestService(t, nil)
	parent := filepath.Join(root, "src")
	path := filepath.Join(parent, "child.md")
	if err := os.WriteFile(path, []byte("base"), 0600); err != nil {
		t.Fatal(err)
	}
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	row := registerTestOpenFile(t, s, "visible", path, fileID(info), "", []any{root})
	id := row["tracking_id"].(string)
	registerTestOpenFile(t, s, "hidden", path, fileID(info), id, []any{root, parent})
	// Hiding Finder drops every watched directory but keeps the open document.
	registerTestOpenFile(t, s, "hidden", path, fileID(info), id, []any{})
	s.mu.Lock()
	expires := s.watches["hidden"].ExpiresAt
	s.mu.Unlock()
	s.ReleaseWatchClient("hidden")
	s.mu.Lock()
	retained, exists := s.watches["hidden"]
	s.mu.Unlock()
	if !exists || len(retained.OpenFiles) != 1 || !retained.ExpiresAt.Equal(expires) {
		t.Fatalf("disconnected document lease lost or renewed: %v %v", exists, retained)
	}
	if len(retained.Roots) != 0 || len(retained.SearchRoots) != 0 || len(retained.Files) != 0 {
		t.Fatal("disconnected client retained hot demand")
	}

	nextParent := filepath.Join(root, "renamed")
	if err := os.Rename(parent, nextParent); err != nil {
		t.Fatal(err)
	}
	s.reconcileOpenFiles()
	payload := map[string]any{"client_id": "hidden", "roots": []any{root, parent}, "search_roots": []any{root, parent}, "files": []any{path}, "root_surfaces_version": 1, "root_surfaces": []any{map[string]any{"path": root, "surfaces": []any{"finder"}}, map[string]any{"path": parent, "surfaces": []any{"finder"}}}, "open_files": []any{map[string]any{"path": path, "file_id": fileID(info), "tracking_id": id}}}
	for i := 0; i < 2; i++ {
		response, registerErr := s.WatchRoots(context.Background(), payload)
		if registerErr != nil {
			t.Fatal(registerErr)
		}
		roots := response["roots"].([]string)
		if len(roots) != 1 || roots[0] != root {
			t.Fatalf("roots %v", roots)
		}
		surfaces := response["root_surfaces"].([]map[string]any)
		if len(surfaces) != 1 || surfaces[0]["path"] != root {
			t.Fatalf("surfaces %v", surfaces)
		}
		s.mu.Lock()
		searchRoots := append([]string(nil), s.watches["hidden"].SearchRoots...)
		s.mu.Unlock()
		if len(searchRoots) != 1 || searchRoots[0] != root {
			t.Fatalf("search roots %v", searchRoots)
		}
		rows := response["open_files"].([]map[string]any)
		if len(rows) != 1 || rows[0]["path"] != filepath.Join(nextParent, "child.md") {
			t.Fatalf("snapshot %v", rows)
		}
	}
	s.mu.Lock()
	descriptor := s.openFiles[id].file
	deadline := s.watches["hidden"].ExpiresAt
	s.mu.Unlock()
	s.ReleaseWatchClient("hidden")
	s.ReleaseWatchClient("visible")
	s.pruneExpiredWatches(deadline.Add(time.Second))
	if _, err := descriptor.Stat(); err == nil {
		t.Fatal("expired disconnected document retained its descriptor")
	}

	payload["client_id"] = "unknown"
	if _, registerErr := s.WatchRoots(context.Background(), payload); registerErr == nil || registerErr.Status != 404 {
		t.Fatalf("unknown client missing root accepted %v", registerErr)
	}
}

func TestOpenFilesRelocatedParentScopeSurvivesHiddenFinder(t *testing.T) {
	s, root := newTestService(t, nil)
	parent := filepath.Join(root, "src")
	path := filepath.Join(parent, "child.md")
	if err := os.WriteFile(path, []byte("base"), 0600); err != nil {
		t.Fatal(err)
	}
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	row := registerTestOpenFile(t, s, "one", path, fileID(info), "", []any{root})
	nextParent := filepath.Join(root, "renamed")
	if err := os.Rename(parent, nextParent); err != nil {
		t.Fatal(err)
	}
	s.reconcileOpenFiles()
	relocated := filepath.Join(nextParent, "child.md")
	id := row["tracking_id"].(string)
	registerTestOpenFile(t, s, "one", relocated, fileID(info), id, []any{})
	next := filepath.Join(nextParent, "again.md")
	if err := os.Rename(relocated, next); err != nil {
		t.Fatal(err)
	}
	s.reconcileOpenFiles()
	response := registerTestOpenFile(t, s, "one", relocated, fileID(info), id, []any{})
	if response["path"] != next || response["revision"] != uint64(3) {
		t.Fatalf("relocation after hidden Finder %v", response)
	}
}

func TestOpenFilesDisconnectReleasesOnlyProducerDemand(t *testing.T) {
	s, root := newTestService(t, nil)
	path := filepath.Join(root, "note.md")
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	_, registrationErr := s.WatchRoots(context.Background(), map[string]any{"client_id": "one", "roots": []any{root}, "search_roots": []any{root}, "files": []any{path}, "open_files": []any{map[string]any{"path": path, "file_id": fileID(info)}}})
	if registrationErr != nil {
		t.Fatal(registrationErr)
	}
	s.mu.Lock()
	before := s.watches["one"]
	s.mu.Unlock()
	s.ReleaseWatchClient("one")
	s.mu.Lock()
	after, exists := s.watches["one"]
	s.mu.Unlock()
	if !exists || len(after.OpenFiles) != 1 || !after.ExpiresAt.Equal(before.ExpiresAt) || after.Generation <= before.Generation {
		t.Fatalf("retained lease before=%v after=%v exists=%v", before, after, exists)
	}
	if len(after.Roots)+len(after.SearchRoots)+len(after.Files)+len(after.BackgroundFiles)+len(after.SignaturePaths)+len(after.RootSurfaces)+len(after.ContextItems)+len(after.SessionFiles) != 0 {
		t.Fatalf("producer demand survived disconnect %v", after)
	}
	if len(s.DemandRoots()) != 0 {
		t.Fatal("producer demand still active")
	}
	if _, registrationErr := s.WatchRoots(context.Background(), map[string]any{"client_id": "no-open-files", "roots": []any{root}}); registrationErr != nil {
		t.Fatal(registrationErr)
	}
	s.ReleaseWatchClient("no-open-files")
	s.mu.Lock()
	_, exists = s.watches["no-open-files"]
	s.mu.Unlock()
	if exists {
		t.Fatal("client without opened documents retained")
	}
}

func TestOpenFilesDirectoryAliasesPreserveEachDocumentNamespace(t *testing.T) {
	for _, saveBeforeMove := range []bool{false, true} {
		t.Run(fmt.Sprintf("atomic-save=%t", saveBeforeMove), func(t *testing.T) {
			s, root := newTestService(t, nil)
			resolved, err := filepath.EvalSymlinks(root)
			if err != nil {
				t.Fatal(err)
			}
			aliases := []string{filepath.Join(t.TempDir(), "first"), filepath.Join(t.TempDir(), "second")}
			for _, alias := range aliases {
				if err := os.Symlink(resolved, alias); err != nil {
					t.Fatal(err)
				}
			}
			if err := os.Mkdir(filepath.Join(root, "target"), 0700); err != nil {
				t.Fatal(err)
			}
			info, err := os.Stat(filepath.Join(root, "note.md"))
			if err != nil {
				t.Fatal(err)
			}
			ids := make([]string, len(aliases))
			for i, alias := range aliases {
				row := registerTestOpenFile(t, s, fmt.Sprint(i), filepath.Join(alias, "note.md"), fileID(info), "", []any{alias})
				ids[i] = row["tracking_id"].(string)
			}
			if ids[0] == ids[1] {
				t.Fatal("directory aliases merged document identities")
			}
			if saveBeforeMove {
				if _, writeErr := s.Write(context.Background(), filepath.Join(aliases[0], "note.md"), "saved", nil, fileID(info)); writeErr != nil {
					t.Fatal(writeErr)
				}
			}
			if err := os.Rename(filepath.Join(root, "note.md"), filepath.Join(root, "target", "moved.md")); err != nil {
				t.Fatal(err)
			}
			s.reconcileOpenFiles()
			for i, alias := range aliases {
				row := registerTestOpenFile(t, s, fmt.Sprint(i), filepath.Join(alias, "note.md"), fileID(info), ids[i], []any{alias})
				if row["path"] != filepath.Join(alias, "target", "moved.md") || row["revision"] != uint64(2) {
					t.Fatalf("alias namespace changed: %v", row)
				}
			}
		})
	}
}

func TestOpenFilesRetargetedAliasCannotReanchorDocument(t *testing.T) {
	s, root := newTestService(t, nil)
	alias := filepath.Join(t.TempDir(), "alias")
	if err := os.Symlink(root, alias); err != nil {
		t.Fatal(err)
	}
	old := filepath.Join(alias, "note.md")
	info, err := os.Stat(old)
	if err != nil {
		t.Fatal(err)
	}
	row := registerTestOpenFile(t, s, "one", old, fileID(info), "", []any{alias})
	if err := os.Rename(filepath.Join(root, "note.md"), filepath.Join(root, "src", "moved.md")); err != nil {
		t.Fatal(err)
	}
	other := t.TempDir()
	if err := os.Mkdir(filepath.Join(other, "src"), 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(other, "src", "moved.md"), []byte("different document"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.Remove(alias); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(other, alias); err != nil {
		t.Fatal(err)
	}
	s.reconcileOpenFiles()
	s.mu.Lock()
	entry := s.openFiles[row["tracking_id"].(string)]
	got, revision := entry.path, entry.revision
	s.mu.Unlock()
	if got != old || revision != 1 {
		t.Fatalf("retargeted alias followed: path=%s revision=%d", got, revision)
	}
}

func TestOpenFilesShutdownClosesSharedDescriptors(t *testing.T) {
	s, root := newTestService(t, nil)
	path := filepath.Join(root, "note.md")
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	first := registerTestOpenFile(t, s, "one", path, fileID(info), "", []any{root})
	registerTestOpenFile(t, s, "two", path, fileID(info), "", []any{root})
	s.mu.Lock()
	file := s.openFiles[first["tracking_id"].(string)].file
	s.mu.Unlock()
	s.Close()
	if _, err := file.Stat(); !errors.Is(err, os.ErrClosed) {
		t.Fatalf("shutdown descriptor error=%v", err)
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if len(s.openFiles) != 0 {
		t.Fatalf("shutdown retained %d registrations", len(s.openFiles))
	}
}

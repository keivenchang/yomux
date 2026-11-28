// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package finder

import (
	"context"
	"os"
	"path/filepath"
	"testing"
)

func TestGrantedFilePinsNamespaceAndOwnWrites(t *testing.T) {
	for _, replacement := range []string{"file", "symlink", "hardlink", "parent"} {
		t.Run(replacement, func(t *testing.T) {
			root := t.TempDir()
			parent := filepath.Join(root, "folder")
			if err := os.Mkdir(parent, 0700); err != nil {
				t.Fatal(err)
			}
			path := filepath.Join(parent, "source.md")
			if err := os.WriteFile(path, []byte("original"), 0600); err != nil {
				t.Fatal(err)
			}
			service, err := New(root, nil)
			if err != nil {
				t.Fatal(err)
			}
			defer service.Close()
			grant, failure := service.GrantFile(context.Background(), path)
			if failure != nil {
				t.Fatal(failure)
			}
			data, info, failure := grant.Read(context.Background(), 1024)
			if failure != nil || string(data) != "original" {
				t.Fatalf("initial read %q %v", data, failure)
			}
			if failure := grant.ReserveWrites(context.Background()); failure != nil {
				t.Fatal(failure)
			}
			if _, failure := service.Write(context.Background(), filepath.Join(parent, ".", "source.md"), "ordinary", nil); failure == nil || failure.Code != "collaboration_owns_save" {
				t.Fatalf("ordinary writer bypassed reservation: %v", failure)
			}
			result, failure := grant.Write(context.Background(), "own save", info)
			if failure != nil {
				t.Fatal(failure)
			}
			data, info, failure = grant.Read(context.Background(), 1024)
			if failure != nil || string(data) != "own save" {
				t.Fatalf("own generation %q %v %v", data, result, failure)
			}
			switch replacement {
			case "file":
				if err := os.Rename(path, path+".old"); err != nil {
					t.Fatal(err)
				}
				if err := os.WriteFile(path, []byte("external"), 0600); err != nil {
					t.Fatal(err)
				}
				if err := os.Chtimes(path, info.ModTime(), info.ModTime()); err != nil {
					t.Fatal(err)
				}
			case "hardlink":
				if err := os.Rename(path, path+".old"); err != nil {
					t.Fatal(err)
				}
				if err := os.WriteFile(path+".private", []byte("private"), 0600); err != nil {
					t.Fatal(err)
				}
				if err := os.Link(path+".private", path); err != nil {
					t.Fatal(err)
				}
			case "symlink":
				if err := os.Rename(path, path+".old"); err != nil {
					t.Fatal(err)
				}
				if err := os.WriteFile(path+".secret", []byte("secret"), 0600); err != nil {
					t.Fatal(err)
				}
				if err := os.Symlink(path+".secret", path); err != nil {
					t.Fatal(err)
				}
			case "parent":
				if err := os.Rename(parent, parent+".old"); err != nil {
					t.Fatal(err)
				}
				if err := os.Mkdir(parent, 0700); err != nil {
					t.Fatal(err)
				}
				if err := os.WriteFile(path, []byte("external"), 0600); err != nil {
					t.Fatal(err)
				}
			}
			if data, _, failure := grant.Read(context.Background(), 1024); failure == nil {
				t.Fatalf("read replacement %q", data)
			}
			if _, failure := grant.Write(context.Background(), "overwrite", info); failure == nil {
				t.Fatal("wrote replacement")
			}
			data, _, failure = grant.ReadCurrent(context.Background(), 1024)
			if replacement == "file" {
				if failure != nil || string(data) != "external" {
					t.Fatalf("read current leaf: %q %v", data, failure)
				}
				if _, failure := grant.Write(context.Background(), "stale snapshot", info); failure == nil {
					t.Fatal("wrote replacement with old identity and matching mtime")
				}
			} else if failure == nil {
				t.Fatalf("read unsafe replacement: %q", data)
			}
			if failure := grant.ReleaseWrites(context.Background()); failure != nil {
				t.Fatal(failure)
			}
		})
	}
}

func TestGrantedFileRejectsInitialSymlinkAndExternalContent(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "file.md")
	if err := os.WriteFile(path, []byte("old"), 0600); err != nil {
		t.Fatal(err)
	}
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	if err := os.Symlink(path, path+".link"); err != nil {
		t.Fatal(err)
	}
	if _, failure := service.GrantFile(context.Background(), path+".link"); failure == nil {
		t.Fatal("granted symlink")
	}
	grant, failure := service.GrantFile(context.Background(), path)
	if failure != nil {
		t.Fatal(failure)
	}
	_, info, failure := grant.Read(context.Background(), 1024)
	if failure != nil {
		t.Fatal(failure)
	}
	if err := os.WriteFile(path, []byte("external change"), 0600); err != nil {
		t.Fatal(err)
	}
	if _, failure := grant.Write(context.Background(), "draft", info); failure == nil {
		t.Fatal("overwrote external edit")
	}
}

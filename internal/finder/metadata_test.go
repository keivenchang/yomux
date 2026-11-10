// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package finder

import (
	"context"
	"os"
	"path/filepath"
	"testing"
)

func TestPermissionFormatting(t *testing.T) {
	for _, example := range []struct {
		mode  os.FileMode
		text  string
		octal uint32
	}{
		{0o644, "-rw-r--r--", 0o644}, {os.ModeDir | 0o755, "drwxr-xr-x", 0o755},
		{os.ModeSymlink | 0o777, "lrwxrwxrwx", 0o777},
		{os.ModeSetuid | os.ModeSetgid | os.ModeSticky | 0o640, "-rwSr-S--T", 0o7640},
		{os.ModeDir | os.ModeSetuid | os.ModeSetgid | os.ModeSticky | 0o777, "drwsrwsrwt", 0o7777},
	} {
		if got := permissionString(example.mode); got != example.text {
			t.Fatalf("%v: %s", example.mode, got)
		}
		if got := numericMode(example.mode); got != example.octal {
			t.Fatal(got)
		}
	}
}

func TestPermissionMetadataAndIdentity(t *testing.T) {
	root := t.TempDir()
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	path := filepath.Join(root, "file")
	if err := os.WriteFile(path, []byte("abc"), 0o640); err != nil {
		t.Fatal(err)
	}
	info, failure := service.Info(context.Background(), path)
	if failure != nil {
		t.Fatal(failure)
	}
	if info["permissions"] != "-rw-r-----" || info["mode"] != uint32(0o640) || info["owner_id"] == nil {
		t.Fatal(info)
	}
	requested := uint32(0o600)
	original := uint32(0o640)
	request := ChmodRequest{Path: path, Identity: info["metadata_identity"].(string), OriginalMode: &original, Mode: &requested}
	result, failure := service.Chmod(context.Background(), request, nil)
	if failure != nil || result["changed"] != 1 {
		t.Fatal(result, failure)
	}
	if result["metadata"].(map[string]any)["mode"] != requested {
		t.Fatal(result)
	}
	if _, failure = service.Chmod(context.Background(), request, nil); failure == nil || failure.Code != "changed_on_disk" {
		t.Fatal(failure)
	}
	original = requested
	if err := os.Rename(path, path+".old"); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(path+".old", path); err != nil {
		t.Fatal(err)
	}
	if _, failure = service.Chmod(context.Background(), request, nil); failure == nil {
		t.Fatal("symlink replacement admitted")
	}
	link, failure := service.Info(context.Background(), path)
	if failure != nil || link["permissions"] != "lrwxrwxrwx" || link["permission_edit_reason"] == "" {
		t.Fatal(link, failure)
	}
	old, err := os.Stat(path + ".old")
	if err != nil || old.Mode().Perm() != 0o600 {
		t.Fatal(old, err)
	}
}

func TestPermissionRecursivePostOrderAndCancellation(t *testing.T) {
	root := t.TempDir()
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	dir := filepath.Join(root, "dir")
	nested := filepath.Join(dir, "nested")
	if err := os.MkdirAll(nested, 0o700); err != nil {
		t.Fatal(err)
	}
	file := filepath.Join(nested, "file")
	if err := os.WriteFile(file, []byte("abc"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(root, filepath.Join(nested, "link")); err != nil {
		t.Fatal(err)
	}
	info, failure := service.Info(context.Background(), dir)
	if failure != nil {
		t.Fatal(failure)
	}
	original := info["mode"].(uint32)
	mode := uint32(0o400)
	request := ChmodRequest{Path: dir, Identity: info["metadata_identity"].(string), OriginalMode: &original, Mode: &mode, Recursive: true, Confirmed: true}
	defer os.Chmod(dir, 0o700)
	defer os.Chmod(nested, 0o700)
	result, failure := service.Chmod(context.Background(), request, nil)
	if failure != nil || result["changed"] != 3 || result["skipped"] != 1 {
		t.Fatal(result, failure)
	}
	if err := os.Chmod(dir, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(nested, 0o700); err != nil {
		t.Fatal(err)
	}
	after, err := os.Stat(file)
	if err != nil || after.Mode().Perm() != 0o400 {
		t.Fatal(after, err)
	}
	original = 0o700
	mode = 0o600
	ctx, cancel := context.WithCancel(context.Background())
	result, failure = service.Chmod(ctx, request, func(map[string]any) error { cancel(); return nil })
	if failure != nil || result["cancelled"] != true || result["changed"].(int) > 1 {
		t.Fatal(result, failure)
	}
}

func TestPermissionDirectorySize(t *testing.T) {
	root := t.TempDir()
	service, err := New(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	dir := filepath.Join(root, "contents")
	if err := os.MkdirAll(filepath.Join(dir, ".hidden"), 0o700); err != nil {
		t.Fatal(err)
	}
	file := filepath.Join(dir, ".hidden", "file")
	if err := os.WriteFile(file, []byte("abc"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.Link(file, filepath.Join(dir, "hardlink")); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(".hidden", filepath.Join(dir, "link")); err != nil {
		t.Fatal(err)
	}
	sparse, err := os.Create(filepath.Join(dir, "sparse"))
	if err != nil {
		t.Fatal(err)
	}
	if err := sparse.Truncate(1 << 24); err != nil {
		t.Fatal(err)
	}
	sparse.Close()
	for i := 0; i < 270; i++ {
		file, err := os.CreateTemp(dir, "stream-")
		if err != nil {
			t.Fatal(err)
		}
		file.Close()
	}
	total, failure := service.DirectorySize(context.Background(), dir, nil)
	if failure != nil || total["complete"] != true || total["files"] != 274 || total["folders"] != 1 || total["bytes"] != int64((1<<24)+13) {
		t.Fatal(total, failure)
	}
	if total["allocated_bytes"].(int64) >= total["bytes"].(int64) {
		t.Fatal(total)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	total, failure = service.DirectorySize(ctx, dir, nil)
	if failure != nil || total["partial"] != true || total["complete"] != false {
		t.Fatal(total, failure)
	}
}

func TestWriteRejectsReadOnlyFileAndPermissionRevocation(t *testing.T) {
	for _, revoke := range []bool{false, true} {
		t.Run(map[bool]string{false: "already_read_only", true: "revoked_before_publish"}[revoke], func(t *testing.T) {
			service, root := newTestService(t, nil)
			path := filepath.Join(root, "readonly-file")
			if err := os.WriteFile(path, []byte("original"), 0o640); err != nil {
				t.Fatal(err)
			}
			before, err := os.Lstat(path)
			if err != nil {
				t.Fatal(err)
			}
			if revoke {
				service.beforeWriteConflictRecheck = func() {
					if err := os.Chmod(path, 0o444); err != nil {
						t.Fatal(err)
					}
				}
			} else if err := os.Chmod(path, 0o444); err != nil {
				t.Fatal(err)
			}
			_, failure := service.Write(context.Background(), path, "must not replace", nil)
			if failure == nil || failure.Status != 403 {
				t.Fatalf("read-only replacement was not refused: %v", failure)
			}
			data, err := os.ReadFile(path)
			if err != nil || string(data) != "original" {
				t.Fatalf("original changed: %q %v", data, err)
			}
			after, err := os.Lstat(path)
			if err != nil || fileID(before) != fileID(after) || after.Mode().Perm() != 0o444 {
				t.Fatalf("entry changed: %v %v", after, err)
			}
			read, failure := service.Read(context.Background(), path)
			if failure != nil || read["writable"] != false || read["write_reason"] == "" {
				t.Fatalf("read-only metadata: %v %v", read, failure)
			}
		})
	}
}

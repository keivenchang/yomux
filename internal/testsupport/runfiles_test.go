package testsupport

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/bazelbuild/rules_go/go/runfiles"
)

func TestResolveRunfileFromDirectory(t *testing.T) {
	root := t.TempDir()
	want := filepath.Join(root, "yomux", "testsupport_runfiles")
	if err := os.MkdirAll(want, 0o700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("RUNFILES_DIR", root)
	t.Setenv("RUNFILES_MANIFEST_FILE", "")
	got, err := resolveRunfile("yomux/testsupport_runfiles")
	if err != nil {
		t.Fatal(err)
	}
	if got != want {
		t.Fatalf("resolveRunfile() = %q, want %q", got, want)
	}
}

func TestMaterializeRuntimeTreeCopiesRunfileLeaves(t *testing.T) {
	source, artifacts, destination := t.TempDir(), t.TempDir(), filepath.Join(t.TempDir(), "runtime")
	for _, mode := range []os.FileMode{0o444, 0o555} {
		name := mode.String()
		artifact := filepath.Join(artifacts, name)
		if err := os.WriteFile(artifact, []byte(name), mode); err != nil {
			t.Fatal(err)
		}
		if err := os.MkdirAll(filepath.Join(source, "nested"), 0o700); err != nil {
			t.Fatal(err)
		}
		if err := os.Symlink(artifact, filepath.Join(source, "nested", name)); err != nil {
			t.Fatal(err)
		}
	}
	if err := materializeRuntimeTree(source, destination); err != nil {
		t.Fatal(err)
	}
	for _, mode := range []os.FileMode{0o444, 0o555} {
		name := mode.String()
		path := filepath.Join(destination, "nested", name)
		info, err := os.Lstat(path)
		if err != nil {
			t.Fatal(err)
		}
		if !info.Mode().IsRegular() || info.Mode().Perm() != 0o600|mode&0o111 {
			t.Fatalf("materialized input mode=%v for source mode=%v", info.Mode(), mode)
		}
		content, err := os.ReadFile(path)
		if err != nil || string(content) != name {
			t.Fatalf("materialized input=%q error=%v, want %q", content, err, name)
		}
		if err := os.WriteFile(path, []byte("fixture mutation"), 0o600); err != nil {
			t.Fatal(err)
		}
		original, err := os.ReadFile(filepath.Join(artifacts, name))
		if err != nil || string(original) != name {
			t.Fatalf("fixture mutated declared artifact: input=%q error=%v", original, err)
		}
	}
}

func TestMaterializeRuntimeTreeRejectsNonFileAliases(t *testing.T) {
	for _, directory := range []bool{false, true} {
		t.Run(map[bool]string{false: "missing", true: "directory"}[directory], func(t *testing.T) {
			source, target := t.TempDir(), filepath.Join(t.TempDir(), "target")
			if directory {
				if err := os.Mkdir(target, 0o700); err != nil {
					t.Fatal(err)
				}
			}
			if err := os.Symlink(target, filepath.Join(source, "alias")); err != nil {
				t.Fatal(err)
			}
			if err := materializeRuntimeTree(source, filepath.Join(t.TempDir(), "runtime")); err == nil {
				t.Fatal("materialized an alias without a regular leaf artifact")
			}
		})
	}
}

func TestMaterializeRepoRootContainsOnlySelectedInputs(t *testing.T) {
	relative := "assets/runtime/model_pricing.json"
	root := MaterializeRepoRoot(t, relative)
	var files []string
	if err := filepath.WalkDir(root, func(path string, entry os.DirEntry, err error) error {
		if err != nil || entry.IsDir() {
			return err
		}
		if !entry.Type().IsRegular() {
			t.Errorf("materialized repository contains nonregular input %q: %v", path, entry.Type())
		}
		name, err := filepath.Rel(root, path)
		files = append(files, filepath.ToSlash(name))
		return err
	}); err != nil {
		t.Fatal(err)
	}
	if len(files) != 1 || files[0] != relative {
		t.Fatalf("materialized repository inputs=%v, want only %q", files, relative)
	}
}

func TestResolveRunfileFromManifestWithMappedRepositoryAndSpaces(t *testing.T) {
	root := t.TempDir()
	target := filepath.Join(root, "runfiles with spaces", "mapped repo", "required input")
	if err := os.MkdirAll(filepath.Dir(target), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(target, []byte("declared"), 0o600); err != nil {
		t.Fatal(err)
	}
	manifest := filepath.Join(root, "MANIFEST")
	line := "yomux/testsupport_runfiles " + target + "\n"
	if err := os.WriteFile(manifest, []byte(line), 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("RUNFILES_MANIFEST_FILE", manifest)
	t.Setenv("RUNFILES_DIR", "")
	got, err := resolveRunfile("yomux/testsupport_runfiles")
	if err != nil {
		t.Fatal(err)
	}
	if got != target {
		t.Fatalf("resolveRunfile() = %q, want %q", got, target)
	}
}

func TestResolveRunfileFailsForUndeclaredInput(t *testing.T) {
	manifest := filepath.Join(t.TempDir(), "MANIFEST")
	if err := os.WriteFile(manifest, nil, 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("RUNFILES_MANIFEST_FILE", manifest)
	t.Setenv("RUNFILES_DIR", "")
	if _, err := resolveRunfile("yomux/missing"); err == nil {
		t.Fatal("resolveRunfile accepted an undeclared input")
	}
}

func TestRepositoryTreeContainsPricingInputs(t *testing.T) {
	root := RepoRoot(t)
	want := []string{
		"yomux_lib/__init__.py",
		"assets/runtime/model_pricing.json",
		"assets/runtime/internal_model_pricing.json",
	}
	for _, relative := range want {
		if _, err := os.Stat(filepath.Join(root, relative)); err != nil {
			t.Fatalf("required repository input %q is missing from %q: %v", relative, root, err)
		}
	}

	// The exact inventory describes the declared runtime tree, not the checkout.
	if !inBazel() {
		return
	}

	actual := make(map[string]bool, len(want))
	var totalBytes int64
	if err := filepath.WalkDir(root, func(path string, entry os.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if entry.IsDir() {
			return nil
		}
		relative, err := filepath.Rel(root, path)
		if err != nil {
			return err
		}
		actual[filepath.ToSlash(relative)] = true
		info, err := entry.Info()
		if err != nil {
			return err
		}
		totalBytes += info.Size()
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	if len(actual) != len(want) {
		t.Fatalf("testsupport runfiles contain %d files, want only %v; got %v", len(actual), want, actual)
	}
	for _, relative := range want {
		if !actual[relative] {
			t.Errorf("testsupport runfiles unexpectedly omit %q", relative)
		}
	}
	t.Logf("testsupport runfiles: %d files, %d bytes", len(actual), totalBytes)
}

func TestRequiredRunfileFailsWhenDeclaredFileIsMissing(t *testing.T) {
	root := t.TempDir()
	manifest := filepath.Join(root, "MANIFEST")
	if err := os.WriteFile(manifest, []byte("yomux/missing "+filepath.Join(root, "absent")+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("RUNFILES_MANIFEST_FILE", manifest)
	t.Setenv("RUNFILES_DIR", "")
	if _, err := resolveRequiredRunfile("yomux/missing"); err == nil {
		t.Fatal("resolveRequiredRunfile accepted a missing declared input")
	}
}

func TestRulesGoRunfilesAppliesRepositoryMapping(t *testing.T) {
	root := t.TempDir()
	target := filepath.Join(root, "canonical", "required input")
	if err := os.MkdirAll(filepath.Dir(target), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(target, []byte("declared"), 0o600); err != nil {
		t.Fatal(err)
	}
	mapping := filepath.Join(root, "repo-mapping")
	if err := os.WriteFile(mapping, []byte("caller,apparent,canonical\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	manifest := filepath.Join(root, "MANIFEST")
	contents := "_repo_mapping " + mapping + "\ncanonical/required " + target + "\n"
	if err := os.WriteFile(manifest, []byte(contents), 0o600); err != nil {
		t.Fatal(err)
	}
	files, err := runfiles.New(runfiles.ManifestFile(manifest), runfiles.SourceRepo("caller"))
	if err != nil {
		t.Fatal(err)
	}
	got, err := files.Rlocation("apparent/required")
	if err != nil {
		t.Fatal(err)
	}
	if got != target {
		t.Fatalf("Rlocation() = %q, want %q", got, target)
	}
}

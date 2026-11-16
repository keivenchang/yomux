package testsupport

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/bazelbuild/rules_go/go/runfiles"
	"github.com/keivenchang/yomux/internal/productenv"
)

func inBazel() bool {
	return os.Getenv("TEST_SRCDIR") != "" || os.Getenv("RUNFILES_DIR") != "" || os.Getenv("RUNFILES_MANIFEST_FILE") != ""
}

func requiredRunfile(t testing.TB, name string) string {
	t.Helper()
	path, err := resolveRequiredRunfile(name)
	if err != nil {
		t.Fatalf("resolve required runfile %q: %v", name, err)
	}
	return path
}

func resolveRequiredRunfile(name string) (string, error) {
	path, err := resolveRunfile(name)
	if err != nil {
		return "", err
	}
	if _, err := os.Stat(path); err != nil {
		return "", err
	}
	return path, nil
}

func resolveRunfile(name string) (string, error) {
	files, err := runfiles.New()
	if err != nil {
		return "", err
	}
	return files.Rlocation(name)
}

// RepoRoot resolves the consumer's declared tree. Bazel may expose its files as
// symlinks, even when the declared output artifact contains regular files.
func RepoRoot(t testing.TB) string {
	t.Helper()
	if inBazel() {
		name := productenv.Value("REPO_ROOT_RUNFILE")
		if name == "" {
			t.Fatal("Bazel target omitted YOMUX_REPO_ROOT_RUNFILE")
		}
		return requiredRunfile(t, name)
	}
	if root, ok := rootFromCheckout(); ok {
		return root
	}
	t.Fatal("could not locate the YOmux repository")
	return ""
}

// RepoPath resolves individual data independently, including manifest-only layouts.
func RepoPath(t testing.TB, relative string) string {
	t.Helper()
	clean := filepath.Clean(relative)
	if clean == "." || filepath.IsAbs(relative) || clean == ".." || strings.HasPrefix(clean, ".."+string(filepath.Separator)) {
		t.Fatalf("repository data path must be relative and below the repository: %q", relative)
	}
	if inBazel() && strings.HasPrefix(filepath.ToSlash(clean), "internal/") {
		return requiredRunfile(t, "yomux/"+filepath.ToSlash(clean))
	}
	path := filepath.Join(RepoRoot(t), clean)
	if _, err := os.Stat(path); err != nil {
		t.Fatalf("repository data path %q is unavailable: %v", relative, err)
	}
	return path
}

// MaterializeRepoRoot gives strict runtime readers private regular files for the
// selected declared inputs. Selecting subtrees also avoids copying the checkout
// when the same fixture runs outside Bazel.
func MaterializeRepoRoot(t testing.TB, relatives ...string) string {
	t.Helper()
	if len(relatives) == 0 {
		t.Fatal("materialized repository fixture requires explicit inputs")
	}
	root := t.TempDir()
	for _, relative := range relatives {
		source := RepoPath(t, relative)
		if err := materializeRuntimeTree(source, filepath.Join(root, relative)); err != nil {
			t.Fatalf("materialize declared repository input %q: %v", relative, err)
		}
	}
	return root
}

func materializeRuntimeTree(source, destination string) error {
	return filepath.WalkDir(source, func(path string, entry os.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		relative, err := filepath.Rel(source, path)
		if err != nil {
			return err
		}
		target := filepath.Join(destination, relative)
		if entry.IsDir() {
			return os.MkdirAll(target, 0o700)
		}
		// Runfile aliases name declared leaf artifacts. Do not follow directory
		// aliases into undeclared trees or copy special files that could block.
		info, err := os.Stat(path)
		if err != nil {
			return err
		}
		if !info.Mode().IsRegular() {
			return fmt.Errorf("runtime input %q is not a regular file", path)
		}
		content, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		if err := os.MkdirAll(filepath.Dir(target), 0o700); err != nil {
			return err
		}
		return os.WriteFile(target, content, 0o600|info.Mode().Perm()&0o111)
	})
}

// ExternalBinary is mandatory for the dedicated parity targets. A missing input
// is a graph defect, never a reason to report a skipped parity case.
func ExternalBinary(t testing.TB) string {
	t.Helper()
	name := productenv.Value("GO_BINARY_RUNFILE")
	path := productenv.Value("GO_BINARY")
	if inBazel() {
		if name == "" {
			t.Fatal("integration target omitted YOMUX_GO_BINARY_RUNFILE")
		}
		path = requiredRunfile(t, name)
	}
	info, err := os.Stat(path)
	if err != nil || info.IsDir() || info.Mode()&0111 == 0 {
		t.Fatalf("required external Go executable %q is unavailable: %v", path, err)
	}
	return path
}

func rootFromCheckout() (string, bool) {
	workingDirectory, err := os.Getwd()
	if err != nil {
		return "", false
	}
	for directory := filepath.Clean(workingDirectory); ; directory = filepath.Dir(directory) {
		if _, err := os.Stat(filepath.Join(directory, "yomux_lib", "__init__.py")); err == nil {
			return directory, true
		}
		if filepath.Dir(directory) == directory {
			return "", false
		}
	}
}

package testsupport

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/bazelbuild/rules_go/go/runfiles"
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

// RepoRoot resolves a declared physical tree. Python imports require a directory;
// Bazel materializes precisely that consumer's inputs instead of inferring siblings
// from the physical location of an arbitrary runfile marker.
func RepoRoot(t testing.TB) string {
	t.Helper()
	if inBazel() {
		name := os.Getenv("YOMUX_REPO_ROOT_RUNFILE")
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

// ExternalBinary is mandatory for the dedicated parity targets. A missing input
// is a graph defect, never a reason to report a skipped parity case.
func ExternalBinary(t testing.TB) string {
	t.Helper()
	name := os.Getenv("YOMUX_GO_BINARY_RUNFILE")
	path := os.Getenv("YOMUX_GO_BINARY")
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

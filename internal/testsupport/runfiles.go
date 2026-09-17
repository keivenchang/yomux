package testsupport

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// RepoRoot returns the source/runfiles root for the current test. Bazel tests must
// resolve declared data through runfiles; the checkout walk keeps direct `go test`
// useful without making tests depend on the caller's working directory.
func RepoRoot(t testing.TB) string {
	t.Helper()
	if root, ok := rootFromRunfiles(); ok {
		return root
	}
	if root, ok := rootFromCheckout(); ok {
		return root
	}
	t.Fatalf("could not locate the YOLOmux repository in Bazel runfiles or the checkout")
	return ""
}

// RepoPath returns a declared repository file or directory and fails with the
// requested relative path when the test data graph is incomplete.
func RepoPath(t testing.TB, relative string) string {
	t.Helper()
	clean := filepath.Clean(relative)
	if clean == "." || filepath.IsAbs(relative) || clean == ".." || strings.HasPrefix(clean, ".."+string(filepath.Separator)) {
		t.Fatalf("repository data path must be relative and below the repository: %q", relative)
	}
	path := filepath.Join(RepoRoot(t), clean)
	if _, err := os.Stat(path); err != nil {
		t.Fatalf("repository data path %q is unavailable: %v", relative, err)
	}
	return path
}

func rootFromRunfiles() (string, bool) {
	const marker = "yolomux_lib/__init__.py"
	baseDirectories := make([]string, 0, 4)
	for _, name := range []string{"RUNFILES_DIR", "TEST_SRCDIR"} {
		if value := strings.TrimSpace(os.Getenv(name)); value != "" {
			baseDirectories = append(baseDirectories, value)
		}
	}
	relativeNames := []string{marker, "_main/" + marker, "yolomux/" + marker}
	if workspace := os.Getenv("TEST_WORKSPACE"); workspace != "" {
		relativeNames = append([]string{workspace + "/" + marker}, relativeNames...)
	}
	for _, base := range baseDirectories {
		for _, name := range relativeNames {
			path := filepath.Join(base, filepath.FromSlash(name))
			if _, err := os.Stat(path); err == nil {
				return filepath.Dir(filepath.Dir(path)), true
			}
		}
	}
	// A manifest-only launcher may omit RUNFILES_DIR. Resolve just the marker
	// entry; the remaining declared files stay under the same runfiles root.
	manifestPath := strings.TrimSpace(os.Getenv("RUNFILES_MANIFEST_FILE"))
	if manifestPath != "" {
		data, err := os.ReadFile(manifestPath)
		if err == nil {
			for _, line := range strings.Split(string(data), "\n") {
				fields := strings.Fields(line)
				if len(fields) == 2 && fields[0] == marker {
					if _, statErr := os.Stat(fields[1]); statErr == nil {
						return filepath.Dir(filepath.Dir(fields[1])), true
					}
				}
			}
		}
	}
	return "", false
}

func rootFromCheckout() (string, bool) {
	workingDirectory, err := os.Getwd()
	if err != nil {
		return "", false
	}
	for directory := filepath.Clean(workingDirectory); ; directory = filepath.Dir(directory) {
		if _, err := os.Stat(filepath.Join(directory, "yolomux_lib", "__init__.py")); err == nil {
			return directory, true
		}
		parent := filepath.Dir(directory)
		if parent == directory {
			return "", false
		}
	}
}

//go:build !linux && !darwin

package stats

import (
	"fmt"
	"os"
)

func readCodexDirectoryPage(_ *os.File, _ int64) ([]string, int64, bool, error) {
	return nil, 0, false, fmt.Errorf("Codex repair directory continuation is unsupported on this platform")
}

func codexDirectoryChangeTimeNS(os.FileInfo) int64 {
	return 0
}

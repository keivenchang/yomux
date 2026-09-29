//go:build !linux

package stats

import (
	"errors"
	"fmt"
	"os"
)

var (
	errCodexForkRepairNotDirectory = errors.New("Codex fork repair entry is not a directory")
	errCodexForkRepairSymlink      = errors.New("Codex fork repair entry is a symlink")
)

func openCodexForkRepairPathNoSymlinks(root, path string, directory bool) (*os.File, os.FileInfo, error) {
	if !codexForkRepairPathWithinRoot(root, path, directory) {
		return nil, nil, fmt.Errorf("Codex fork repair path is outside its catalog root")
	}
	return nil, nil, fmt.Errorf("Codex fork repair no-follow opens are unsupported on this platform")
}

package stats

import (
	"errors"
	"os"
	"path/filepath"
	"strings"

	"github.com/keivenchang/yomux/internal/productenv"
)

var errSyntheticTmuxSocketUnset = errors.New("synthetic tmux socket is not configured")

// TmuxNoServerError reports the normal result of querying a socket whose tmux
// server has not been started. That is an empty machine roster, not a broken
// collector; callers can keep the watcher healthy and return no sessions.
func TmuxNoServerError(output []byte) bool {
	lower := strings.ToLower(string(output))
	return strings.Contains(lower, "no server running") ||
		(strings.Contains(lower, "error connecting to") && strings.Contains(lower, "no such file or directory"))
}

// tmuxCommandArgs gives every stats tmux adapter the same server visibility as
// the operator runtime. An unset socket deliberately keeps tmux's default
// shared server; test sockets must be supplied through YOLOMUX_TMUX_SOCKET so a
// test-only environment variable can never redirect an operator scan.
func tmuxCommandArgs(args ...string) []string {
	command := []string{"tmux"}
	socket := strings.TrimSpace(productenv.Value("YOLOMUX_TMUX_SOCKET"))
	if socket != "" {
		command = append(command, "-S", socket)
	}
	return append(command, args...)
}

// syntheticTmuxSocketConfigured is a separate admission check from
// tmuxCommandArgs. Production adapters intentionally use the machine-wide
// default server when no socket is configured; synthetic adapters must fail
// closed instead of accidentally reading that server.
func syntheticTmuxSocketConfigured() bool {
	socket := strings.TrimSpace(productenv.Value("YOLOMUX_TMUX_SOCKET"))
	if socket == "" {
		return false
	}
	if productenv.Value("YOLOMUX_STATS_SYNTHETIC") != "1" {
		return true
	}
	root := strings.TrimSpace(productenv.Value("YOLOMUX_STATS_SYNTHETIC_ROOT"))
	if root == "" {
		return false
	}
	root, err := filepath.Abs(root)
	if err != nil {
		return false
	}
	socket, err = filepath.Abs(socket)
	if err != nil || filepath.Clean(socket) != filepath.Join(filepath.Clean(root), "tmux.sock") {
		return false
	}
	if info, err := os.Lstat(socket); err == nil && info.Mode()&os.ModeSocket == 0 {
		return false
	} else if err != nil && !os.IsNotExist(err) {
		return false
	}
	return true
}

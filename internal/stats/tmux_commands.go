package stats

import (
	"os"
	"strings"
)

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
	socket := strings.TrimSpace(os.Getenv("YOLOMUX_TMUX_SOCKET"))
	if socket != "" {
		command = append(command, "-S", socket)
	}
	return append(command, args...)
}

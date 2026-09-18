package stats

import (
	"os"
	"strings"
)

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

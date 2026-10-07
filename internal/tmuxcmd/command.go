// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package tmuxcmd

import (
	"context"
	"os"
	"os/exec"
	"strings"

	"github.com/keivenchang/yomux/internal/productenv"
)

func Args(args ...string) []string {
	command := []string{"tmux"}
	if socket := strings.TrimSpace(productenv.Value("TMUX_SOCKET")); socket != "" {
		command = append(command, "-S", socket)
	}
	return append(command, args...)
}

func Command(ctx context.Context, args ...string) *exec.Cmd {
	socket := strings.TrimSpace(productenv.Value("TMUX_SOCKET"))
	command := exec.CommandContext(ctx, "tmux", args...)
	// A direct Go entrypoint can be started from inside a nested/private tmux client,
	// where tmux would otherwise ignore the machine-wide default server and follow the
	// inherited TMUX socket. Operator launches use the global server unless an explicit
	// fixture socket was supplied; enforce that boundary at every command call so the
	// Tabber roster cannot silently come from the launcher's supervisor server.
	environment := make([]string, 0, len(os.Environ())+1)
	for _, value := range os.Environ() {
		if socket == "" && (strings.HasPrefix(value, "TMUX=") || strings.HasPrefix(value, "TMUX_PANE=")) {
			continue
		}
		// tmux renders whitespace format separators as underscores under the empty
		// locale used by Bazel's hermetic tests. Keep the wire format stable without
		// changing an operator's configured locale when one is already present.
		if os.Getenv("LC_ALL") == "" && os.Getenv("LC_CTYPE") == "" && os.Getenv("LANG") == "" && (strings.HasPrefix(value, "LC_ALL=") || strings.HasPrefix(value, "LC_CTYPE=") || strings.HasPrefix(value, "LANG=")) {
			continue
		}
		environment = append(environment, value)
	}
	if os.Getenv("LC_ALL") == "" && os.Getenv("LC_CTYPE") == "" && os.Getenv("LANG") == "" {
		environment = append(environment, "LC_ALL=C.UTF-8")
	}
	command.Env = environment
	return command
}

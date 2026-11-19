// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"context"
	"fmt"
	"os"
	"sort"
	"strconv"
	"strings"
	"sync/atomic"

	"golang.org/x/sys/unix"
)

var terminalResizeClaimSequence atomic.Uint64

// All servers sharing tmux use the kernel's boot clock, rather than a process
// origin or adjustable wall clock. Fixed-width strings compare exactly in tmux.
func newTerminalResizeClaim() (string, error) {
	var now unix.Timespec
	if err := unix.ClockGettime(unix.CLOCK_MONOTONIC, &now); err != nil {
		return "", err
	}
	return fmt.Sprintf("%020d-%010d-%020d", now.Nano(), os.Getpid(), terminalResizeClaimSequence.Add(1)), nil
}

func validTerminalResizeClaim(claim string) bool {
	if len(claim) != 52 || claim[20] != '-' || claim[31] != '-' {
		return false
	}
	for index, value := range claim {
		if index != 20 && index != 31 && (value < '0' || value > '9') {
			return false
		}
	}
	return true
}

type terminalAppearance struct {
	StatusForeground string `json:"status_fg"`
	StatusBackground string `json:"status_bg"`
	Cursor           string `json:"cursor"`
	Window           string `json:"window_id,omitempty"`
	styles           []terminalStatusStyle
}

func (a *terminalAppearance) valid() bool {
	if a == nil {
		return false
	}
	for _, color := range []string{a.StatusForeground, a.StatusBackground, a.Cursor} {
		if len(color) != 7 || color[0] != '#' {
			return false
		}
		for _, c := range color[1:] {
			if !(c >= '0' && c <= '9' || c >= 'a' && c <= 'f' || c >= 'A' && c <= 'F') {
				return false
			}
		}
	}
	return true
}

type terminalStatusStyle struct {
	target, session, option, base, revision string
	window, global, renew, remove           bool
}

var terminalStatusOptions = []string{"status-style", "status-left-style", "status-right-style", "window-status-style", "window-status-current-style"}

func (style terminalStatusStyle) scope() string {
	scope := "s"
	if style.window {
		scope = "w"
	}
	if style.global {
		scope += "g"
	}
	return scope
}

func (style terminalStatusStyle) key(kind string) string {
	return "@yomux_status_" + style.scope() + "_" + kind + "_" + style.option
}

func (style terminalStatusStyle) args(command string) []string {
	args := []string{command}
	if style.window {
		args = append(args, "-w")
	}
	if style.global {
		args = append(args, "-g")
	} else {
		args = append(args, "-t", style.target)
	}
	return args
}

func (style terminalStatusStyle) stamp() string {
	return "@yomux_status_" + style.scope() + strings.TrimLeft(style.target, "$@") + "_" + style.option + "_epoch"
}

func (style terminalStatusStyle) colorFormat() string {
	return "#{W:#{?#{window_active},#{?#{==:#{" + style.stamp() + "}," + style.revision + "},#{@yomux_status_style},},}}"
}

func (style terminalStatusStyle) expression() string {
	selected := style.colorFormat()
	if style.option != "status-style" {
		// A native general-style write releases every still-owned section style.
		general := "#{?#{==:#{status-style},#{@yomux_status_s_wrapper}},#{E:@yomux_status_s_color},#{?#{==:#{status-style},#{@yomux_status_sg_wrapper}},#{E:@yomux_status_sg_color},}}"
		owned := "#{W:#{?#{window_active},#{==:#{" + style.stamp() + "}," + style.revision + "},}}"
		selected = "#{?" + owned + "," + general + ",}"
	}
	base := "#{" + style.key("base") + "}"
	return "#{?" + selected + "," + base + "#," + selected + "," + base + "}"
}

func terminalLegacyStatusExpression(option string) string {
	selected := "#{W:#{?#{window_active},#{@yomux_status_style},}}"
	base := "#{@yomux_base_" + option + "}"
	return "#{?" + selected + "," + base + "#," + selected + "," + base + "}"
}

func (s *server) terminalAppearanceStyles(ctx context.Context, window string) ([]terminalStatusStyle, error) {
	output, err := s.tmuxCommandContext(ctx, "list-windows", "-a", "-F", "#{session_id}\t#{window_id}").Output()
	if err != nil {
		return nil, err
	}
	var windows [][2]string
	sessions := map[string]bool{}
	for _, line := range strings.Split(strings.TrimSuffix(string(output), "\n"), "\n") {
		fields := strings.Split(line, "\t")
		if len(fields) != 2 || !strings.HasPrefix(fields[0], "$") || !strings.HasPrefix(fields[1], "@") {
			return nil, fmt.Errorf("invalid terminal status inventory")
		}
		windows = append(windows, [2]string{fields[0], fields[1]})
		if fields[1] == window {
			sessions[fields[0]] = true
		}
	}
	var sessionIDs, windowIDs []string
	seenWindows := map[string]bool{}
	windowSessions := map[string]string{}
	for session := range sessions {
		sessionIDs = append(sessionIDs, session)
	}
	for _, pair := range windows {
		if sessions[pair[0]] && !seenWindows[pair[1]] {
			seenWindows[pair[1]] = true
			windowSessions[pair[1]] = pair[0]
			windowIDs = append(windowIDs, pair[1])
		}
	}
	sort.Strings(sessionIDs)
	sort.Strings(windowIDs)
	var candidates []terminalStatusStyle
	for _, option := range terminalStatusOptions {
		candidates = append(candidates, terminalStatusStyle{option: option, global: true, window: strings.HasPrefix(option, "window-")})
	}
	for _, targets := range [][]string{sessionIDs, windowIDs} {
		for _, target := range targets {
			for _, option := range terminalStatusOptions {
				isWindow := strings.HasPrefix(option, "window-")
				if isWindow == strings.HasPrefix(target, "@") {
					candidates = append(candidates, terminalStatusStyle{target: target, session: windowSessions[target], option: option, window: isWindow})
				}
			}
		}
	}
	// Batch scalar reads in one tmux command queue. Without -A, an unset local
	// style emits nothing: wrapping only its original owner preserves inheritance.
	const separator = "__YOMUX_STATUS_RECORD__\n"
	var args []string
	for _, style := range candidates {
		for _, option := range []string{style.option, style.key("base"), style.key("revision"), "@yomux_base_" + style.option} {
			if len(args) > 0 {
				args = append(args, ";")
			}
			args = append(args, append(style.args("show-options"), "-qv", option)...)
			args = append(args, ";", "display-message", "-p", strings.TrimSuffix(separator, "\n"))
		}
	}
	output, err = s.tmuxCommandContext(ctx, args...).Output()
	if err != nil {
		return nil, err
	}
	values := strings.Split(string(output), separator)
	if len(values) != len(candidates)*4+1 {
		return nil, fmt.Errorf("invalid terminal style ownership inventory")
	}
	selected := map[int]bool{}
	var retired []terminalStatusStyle
	for index := range candidates {
		style := &candidates[index]
		raw := strings.TrimSuffix(values[index*4], "\n")
		style.revision = strings.TrimSuffix(values[index*4+2], "\n")
		if style.revision != "" && !validTerminalResizeClaim(style.revision) {
			return nil, fmt.Errorf("invalid terminal style revision")
		}
		style.base = raw
		if raw == style.expression() {
			style.base = strings.TrimSuffix(values[index*4+1], "\n")
		} else {
			// A native write replaced our wrapper. Its next focus starts a new
			// epoch so rewrapping a global owner cannot recolor older windows.
			style.renew = true
		}
		if !style.global && raw == terminalLegacyStatusExpression(style.option) {
			style.base = strings.TrimSuffix(values[index*4+3], "\n")
			globalIndex := 0
			for candidates[globalIndex].option != style.option {
				globalIndex++
			}
			if style.base == candidates[globalIndex].base {
				removed := *style
				removed.remove = true
				retired = append(retired, removed)
				raw = ""
			}
		}
		if style.global {
			continue
		}
		if raw == "" {
			for globalIndex := range terminalStatusOptions {
				if candidates[globalIndex].option == style.option {
					selected[globalIndex] = true
				}
			}
		} else {
			selected[index] = true
		}
	}
	result := retired
	for index, style := range candidates {
		if selected[index] {
			result = append(result, style)
		}
	}
	return result, nil
}

type terminalAuthorityClaim struct {
	token      string
	appearance *terminalAppearance
	window     string
	result     *terminalAuthorityResult
	continuing bool
	prepare    bool
}

type terminalAuthorityResult struct{ window string }

func (claim *terminalAuthorityClaim) acknowledge(result terminalAuthorityResult) {
	if result.window == "" {
		return
	}
	claim.window, claim.appearance, claim.continuing = result.window, nil, true
}

type terminalResizeReceipt struct {
	Token  string `json:"token"`
	Window string `json:"window_id"`
}

func (r *terminalResizeReceipt) valid() bool {
	if r == nil || !validTerminalResizeClaim(r.Token) || len(r.Window) < 2 || r.Window[0] != '@' {
		return false
	}
	_, err := strconv.ParseUint(r.Window[1:], 10, 63)
	return err == nil
}

type terminalControlOptions struct {
	authority      bool
	appearance     *terminalAppearance
	passive        bool
	resume         *terminalResizeReceipt
	publishReceipt bool
}

type terminalResizeClient struct {
	tty, session, window, pid, created string
	cols, rows                         int
}

func terminalResizeClients(output []byte) []terminalResizeClient {
	var clients []terminalResizeClient
	for _, line := range strings.Split(strings.TrimSpace(string(output)), "\n") {
		fields := strings.Split(line, "\t")
		if len(fields) != 7 || !strings.HasPrefix(fields[0], "/dev/") || !strings.HasPrefix(fields[4], "@") {
			continue
		}
		// These kernel/tmux identities are embedded into formats, not shell code.
		if strings.IndexFunc(fields[0], func(r rune) bool {
			return !(r >= 'a' && r <= 'z' || r >= 'A' && r <= 'Z' || r >= '0' && r <= '9' || strings.ContainsRune("/_.-", r))
		}) >= 0 {
			continue
		}
		pid, err := strconv.ParseUint(fields[5], 10, 63)
		valid := err == nil && pid > 0
		for _, value := range []string{fields[2], fields[3], strings.TrimPrefix(fields[4], "@"), fields[6]} {
			if _, err := strconv.ParseUint(value, 10, 63); err != nil {
				valid = false
			}
		}
		if !valid {
			continue
		}
		cols, _ := strconv.Atoi(fields[2])
		rows, _ := strconv.Atoi(fields[3])
		clients = append(clients, terminalResizeClient{fields[0], fields[1], fields[4], fields[5], fields[6], cols, rows})
	}
	return clients
}

func (client terminalResizeClient) currentFormat() string {
	// A client loop includes connections before tty identification. Match the
	// numeric identity first through tmux's lazy conditional; && expands both sides.
	return fmt.Sprintf("#{L:#{?#{&&:#{==:#{client_pid},%s},#{==:#{client_created},%s}},#{?#{&&:#{==:#{client_tty},%s},#{==:#{window_id},%s}},1,},}}", client.pid, client.created, client.tty, client.window)
}

func tmuxResizeCommand(arguments ...string) string {
	quoted := make([]string, len(arguments))
	for index, argument := range arguments {
		quoted[index] = strconv.Quote(argument)
	}
	return strings.Join(quoted, " ")
}

const terminalResizeClientFormat = "#{client_tty}\t#{client_session}\t#{client_width}\t#{client_height}\t#{window_id}\t#{client_pid}\t#{client_created}"

func terminalResizeClaimCondition(active terminalResizeClient, claim string, continuing bool) string {
	comparison := ">="
	if continuing {
		comparison = "=="
	}
	return "#{&&:" + active.currentFormat() + ",#{" + comparison + ":" + claim + ",#{@yomux_resize_authority}}}"
}

func terminalResizePrepareArgs(active terminalResizeClient, claim string, continuing bool) []string {
	commands := tmuxResizeCommand("refresh-client", "-t", active.tty, "-f", "ignore-size") + " ; " + tmuxResizeCommand("display-message", "-p", claim)
	return []string{"if-shell", "-F", "-t", active.window, terminalResizeClaimCondition(active, claim, continuing), commands}
}

func terminalResizeClaimArgs(clients []terminalResizeClient, active terminalResizeClient, cols, rows int, claim string, continuing bool, appearance ...*terminalAppearance) []string {
	const owner = "@yomux_resize_authority"
	current := "#{" + owner + "}"
	condition := terminalResizeClaimCondition(active, claim, continuing)
	commands := []string{tmuxResizeCommand("set-option", "-w", "-t", active.window, owner, claim)}
	for _, client := range clients {
		if client.window != active.window {
			continue
		}
		flag := "ignore-size"
		if client.tty == active.tty {
			flag = "!ignore-size"
		}
		// Guard inside tmux as well as at publication: a canceled Go process cannot
		// retract a command already queued in the shared external server.
		guard := "#{&&:" + active.currentFormat() + ",#{&&:#{==:" + current + "," + claim + "}," + client.currentFormat() + "}}"
		commands = append(commands, tmuxResizeCommand("if-shell", "-F", "-t", active.window, guard,
			tmuxResizeCommand("refresh-client", "-t", client.tty, "-f", flag)))
	}
	if len(appearance) > 0 && appearance[0].valid() {
		colors := appearance[0]
		style := "fg=" + colors.StatusForeground + ",bg=" + colors.StatusBackground
		colorCommands := []string{
			tmuxResizeCommand("set-option", "-w", "-t", active.window, "@yomux_status_style", style),
			tmuxResizeCommand("set-option", "-w", "-t", active.window, "@yomux_appearance_claim", claim),
			tmuxResizeCommand("set-option", "-w", "-t", active.window, "cursor-colour", colors.Cursor),
		}
		for _, entry := range colors.styles {
			if entry.remove {
				guard := "#{==:#{" + entry.option + "},#{l:" + terminalLegacyStatusExpression(entry.option) + "}}"
				colorCommands = append(colorCommands, tmuxResizeCommand("if-shell", "-F", "-t", entry.target, guard,
					tmuxResizeCommand(append(entry.args("set-option"), "-u", entry.option)...)))
				continue
			}
			contextTarget := entry.target
			if entry.global {
				contextTarget = active.window
			}
			previousRevision := entry.revision
			if entry.renew {
				entry.revision = claim
			}
			commands := []string{
				tmuxResizeCommand(append(entry.args("set-option"), entry.key("base"), entry.base)...),
				tmuxResizeCommand(append(entry.args("set-option"), entry.key("revision"), entry.revision)...),
				tmuxResizeCommand(append(entry.args("set-option"), entry.option, entry.expression())...),
			}
			if entry.option == "status-style" {
				commands = append(commands,
					tmuxResizeCommand(append(entry.args("set-option"), "@yomux_status_"+entry.scope()+"_wrapper", entry.expression())...),
					tmuxResizeCommand(append(entry.args("set-option"), "@yomux_status_"+entry.scope()+"_color", entry.colorFormat())...))
			}
			// Concurrent first claims may share an inherited owner. Only one
			// snapshots its base and starts an epoch; the others reuse that epoch.
			guard := "#{==:#{" + entry.key("revision") + "}," + previousRevision + "}"
			colorCommands = append(colorCommands, tmuxResizeCommand("if-shell", "-F", "-t", contextTarget, guard, strings.Join(commands, " ; ")))
			value := "#{" + entry.key("revision") + "}"
			if !entry.global {
				session := entry.target
				if entry.window {
					session = entry.session
					value = "#{W:#{?#{==:#{window_id}," + entry.target + "}," + value + ",}}"
				}
				value = "#{S:#{?#{==:#{session_id}," + session + "}," + value + ",}}"
			}
			colorCommands = append(colorCommands, tmuxResizeCommand("set-option", "-wF", "-t", active.window, entry.stamp(), value))
		}
		colorGuard := "#{&&:" + active.currentFormat() + ",#{&&:#{==:" + current + "," + claim + "},#{>:" + claim + ",#{@yomux_appearance_claim}}}}"
		commands = append(commands, tmuxResizeCommand("if-shell", "-F", "-t", active.window, colorGuard, strings.Join(colorCommands, " ; ")))
	}
	commands = append(commands, tmuxResizeCommand("if-shell", "-F", "-t", active.window,
		"#{&&:"+active.currentFormat()+",#{==:"+current+","+claim+"}}", tmuxResizeCommand("display-message", "-p", claim)))
	return []string{"if-shell", "-F", "-t", active.window, condition, strings.Join(commands, " ; ")}
}

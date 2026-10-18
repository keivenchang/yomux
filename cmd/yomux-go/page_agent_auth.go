// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/keivenchang/yomux/internal/mutablefs"
	"github.com/keivenchang/yomux/internal/productenv"
	"github.com/keivenchang/yomux/internal/yoagent"
)

type pageAgentAuth struct {
	Installed  bool   `json:"installed"`
	LoggedIn   *bool  `json:"logged_in"`
	Available  bool   `json:"available"`
	Reason     string `json:"unavailable_reason,omitempty"`
	ProbeError string `json:"probe_error,omitempty"`
}

type nativePageState struct {
	pageAdmission       chan struct{}
	pageWork            sync.WaitGroup
	pageMu              sync.Mutex
	pageClosed          bool
	pageContext         context.Context
	pageCancel          context.CancelFunc
	agentAuth           map[string]pageAgentAuth
	agentAuthExpires    time.Time
	agentAuthStaleUntil time.Time
	agentAuthDone       chan struct{}
}

var pageAgentLoginCommands = map[string]string{"claude": "claude auth login", "codex": "codex login"}

func defaultPageAgentAuth() map[string]pageAgentAuth {
	rows := make(map[string]pageAgentAuth)
	for _, agent := range yoagent.ManagedBackends() {
		_, err := nativeCommandPath(agent)
		row := pageAgentAuth{Installed: err == nil, Available: true, Reason: "auth-unknown"}
		if err != nil {
			loggedIn := false
			row.LoggedIn = &loggedIn
			row.Reason = "not-on-path"
		}
		rows[agent] = row
	}
	return rows
}

func nativeCodexEnvironment() ([]string, error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return nil, err
	}
	codexHome := filepath.Join(home, ".codex")
	configuredRoot := strings.TrimSpace(productenv.Value("ROOT"))
	if override := strings.TrimSpace(productenv.Value("CODEX_HOME")); override != "" {
		if strings.HasPrefix(override, "~/") {
			override = filepath.Join(home, strings.TrimPrefix(override, "~/"))
		}
		codexHome, err = mutablefs.Canonical(override)
		if err != nil {
			return nil, err
		}
		if configuredRoot != "" {
			root, err := mutablefs.Canonical(configuredRoot)
			if err != nil {
				return nil, err
			}
			relative, err := filepath.Rel(root, codexHome)
			if err != nil || relative == ".." || strings.HasPrefix(relative, ".."+string(filepath.Separator)) {
				return nil, fmt.Errorf("configured Codex home escapes product root")
			}
		}
	} else if configuredRoot == "" && strings.TrimSpace(os.Getenv("CODEX_HOME")) != "" {
		codexHome = os.Getenv("CODEX_HOME")
	}
	if codexHome == "~" {
		codexHome = home
	} else if strings.HasPrefix(codexHome, "~/") {
		codexHome = filepath.Join(home, codexHome[2:])
	}
	codexHome, err = mutablefs.Canonical(codexHome)
	if err != nil {
		return nil, err
	}
	canonicalHome, err := mutablefs.Canonical(home)
	if err != nil {
		return nil, err
	}
	if codexHome == canonicalHome {
		return nil, fmt.Errorf("Codex home cannot be the login home directory")
	}
	return nativeRuntimeEnvironment(map[string]string{"CODEX_HOME": codexHome, "TERM": "xterm-256color", "NO_COLOR": "1"})
}

func probePageAgentAuth(parent context.Context, agent string, row pageAgentAuth) pageAgentAuth {
	if !row.Installed {
		return row
	}
	ctx, cancel := context.WithTimeout(parent, 8*time.Second)
	defer cancel()
	args := []string{"auth", "status"}
	if agent == "codex" {
		args = []string{"login", "status"}
	}
	executable, pathErr := nativeCommandPath(agent)
	if pathErr != nil {
		row.ProbeError = pathErr.Error()
		return row
	}
	command := exec.CommandContext(ctx, executable, args...)
	environment, envErr := nativeRuntimeEnvironment(nil)
	if envErr != nil {
		row.ProbeError = envErr.Error()
		return row
	}
	command.Env = environment
	if agent == "codex" {
		environment, err := nativeCodexEnvironment()
		if err != nil {
			row.ProbeError = err.Error()
			return row
		}
		command.Env = environment
	}
	// Auth probes print diagnostics on stderr; JSON authority is stdout only.
	stdout, stderr, err := nativeCommandOutput(command, 64*1024)
	if ctx.Err() != nil {
		row.ProbeError = ctx.Err().Error()
		return row
	}
	if errors.Is(err, errNativeCommandOutputLimit) || errors.Is(err, exec.ErrWaitDelay) {
		row.ProbeError = err.Error()
		return row
	}
	var authority map[string]json.RawMessage
	decodeErr := json.Unmarshal(stdout, &authority)
	declared, present := authority["loggedIn"]
	if decodeErr == nil && present {
		// A declared status is authoritative only for the literal JSON true value.
		// Missing fields use the text fallback; null and other types are logged out.
		loggedIn := strings.TrimSpace(string(declared)) == "true"
		row.LoggedIn = &loggedIn
	} else {
		combined := strings.ToLower(string(stdout) + "\n" + string(stderr))
		loggedOut := false
		for _, marker := range []string{"not logged in", "not authenticated", "logged out", "please log in", "please login", "no credentials", "no api key"} {
			loggedOut = loggedOut || strings.Contains(combined, marker)
		}
		if err != nil && !loggedOut {
			row.ProbeError = err.Error()
			return row
		}
		loggedIn := !loggedOut
		row.LoggedIn = &loggedIn
	}
	row.Available = *row.LoggedIn
	row.Reason = ""
	return row
}

func (s *server) pageAgentStatus(ctx context.Context, block bool) (map[string]pageAgentAuth, error) {
	s.pageMu.Lock()
	if s.pageClosed {
		s.pageMu.Unlock()
		return nil, fmt.Errorf("page runtime closed")
	}
	now := time.Now()
	cached := s.agentAuth
	if !now.Before(s.agentAuthStaleUntil) {
		cached = nil
	}
	if s.pageContext == nil {
		s.pageContext, s.pageCancel = context.WithCancel(context.Background())
	}
	if s.agentAuthDone == nil && (cached == nil || time.Until(s.agentAuthExpires) <= 60*time.Second) {
		done := make(chan struct{})
		s.agentAuthDone = done
		go s.refreshPageAgentAuth(s.pageContext, done)
	}
	done := s.agentAuthDone
	fresh := now.Before(s.agentAuthExpires)
	s.pageMu.Unlock()
	if block && !fresh && done != nil {
		select {
		case <-ctx.Done():
			return nil, ctx.Err()
		case <-done:
		}
		s.pageMu.Lock()
		cached = s.agentAuth
		s.pageMu.Unlock()
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	if cached == nil {
		cached = defaultPageAgentAuth()
	}
	return cached, nil
}

func (s *server) refreshPageAgentAuth(ctx context.Context, done chan struct{}) {
	rows := defaultPageAgentAuth()
	type result struct {
		agent string
		row   pageAgentAuth
	}
	results := make(chan result, len(rows))
	for agent, row := range rows {
		go func(agent string, row pageAgentAuth) { results <- result{agent, probePageAgentAuth(ctx, agent, row)} }(agent, row)
	}
	for range len(rows) {
		item := <-results
		rows[item.agent] = item.row
	}
	s.pageMu.Lock()
	defer s.pageMu.Unlock()
	defer close(done)
	s.agentAuthDone = nil
	if s.pageClosed {
		return
	}
	unknown := false
	for agent, row := range rows {
		if row.Installed && row.LoggedIn == nil {
			unknown = true
			if previous, ok := s.agentAuth[agent]; ok && previous.LoggedIn != nil {
				row.LoggedIn = previous.LoggedIn
				row.Available = previous.Available
				row.Reason = ""
				rows[agent] = row
			}
		}
	}
	ttl := 300 * time.Second
	if unknown {
		ttl = 15 * time.Second
	}
	s.agentAuth = rows
	s.agentAuthExpires = time.Now().Add(ttl)
	s.agentAuthStaleUntil = time.Now().Add(time.Hour)
}

func (s *server) beginNativePage(parent context.Context) (context.Context, func(), error) {
	s.pageMu.Lock()
	defer s.pageMu.Unlock()
	if s.pageClosed {
		return nil, nil, fmt.Errorf("page runtime closed")
	}
	if s.pageContext == nil {
		s.pageContext, s.pageCancel = context.WithCancel(context.Background())
	}
	if s.pageAdmission == nil {
		s.pageAdmission = make(chan struct{}, 1)
	}
	select {
	case s.pageAdmission <- struct{}{}:
	default:
		return nil, nil, fmt.Errorf("page renderer busy")
	}
	s.pageWork.Add(1)
	ctx, cancel := context.WithTimeout(parent, 10*time.Second)
	stop := context.AfterFunc(s.pageContext, cancel)
	return ctx, func() { stop(); cancel(); <-s.pageAdmission; s.pageWork.Done() }, nil
}

func (s *server) closeNativePages() {
	s.pageMu.Lock()
	s.pageClosed = true
	if s.pageCancel != nil {
		s.pageCancel()
	}
	done := s.agentAuthDone
	s.pageMu.Unlock()
	if done != nil {
		<-done
	}
	s.pageWork.Wait()
}

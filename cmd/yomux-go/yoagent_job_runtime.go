// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"strings"
	"sync"
	"time"

	"github.com/keivenchang/yomux/internal/yoagent"
)

type nativeYOAgentJobRuntime struct {
	server     *server
	mu         sync.RWMutex
	known      map[string]bool
	status     map[string]string
	prepareErr error
}

func (runtime *nativeYOAgentJobRuntime) BeginCycle(ctx context.Context, jobs []map[string]any) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	if runtime.server == nil {
		return errors.New("YO!agent job server is unavailable")
	}
	queued := make([]map[string]any, 0, len(jobs))
	for _, job := range jobs {
		if nativeYOAgentJobText(job["status"]) == "queued" {
			queued = append(queued, job)
		}
	}
	if len(queued) == 0 {
		runtime.mu.Lock()
		runtime.known, runtime.status, runtime.prepareErr = nil, nil, nil
		runtime.mu.Unlock()
		return nil
	}
	roster, err := runtime.server.tmuxSessionRosterSnapshot(true)
	if err != nil {
		runtime.mu.Lock()
		runtime.prepareErr = fmt.Errorf("refresh tmux session roster: %w", err)
		runtime.known, runtime.status = nil, nil
		runtime.mu.Unlock()
		return runtime.prepareErr
	}
	known := make(map[string]bool, len(roster.sessions))
	for _, session := range roster.sessions {
		known[session] = true
	}
	requested := make(map[string]bool)
	for _, job := range queued {
		target := settingsObject(job["target"])
		for _, session := range nativeYOAgentJobSessions(nativeYOAgentJobText(job["type"]), target) {
			if known[session] {
				requested[session] = true
			}
		}
	}
	sessions := make([]string, 0, len(requested))
	for session := range requested {
		sessions = append(sessions, session)
	}
	status := make(map[string]string, len(sessions))
	if len(sessions) > 0 {
		if runtime.server.agentStatusClassifier == nil {
			err = errors.New("YO!agent job status classifier is unavailable")
		} else {
			metadata, metadataErr := runtime.server.liveSessionMetadataForSessionsWithError(sessions)
			if metadataErr != nil {
				err = fmt.Errorf("refresh tmux panes for YO!agent job: %w", metadataErr)
			} else {
				for _, session := range sessions {
					status[session] = nativeYOAgentSessionState(metadata[session])
				}
			}
		}
	}
	runtime.mu.Lock()
	runtime.known, runtime.status, runtime.prepareErr = known, status, err
	runtime.mu.Unlock()
	return err
}

func (runtime *nativeYOAgentJobRuntime) Observe(ctx context.Context, job map[string]any) (yoagent.JobObservation, error) {
	if runtime.server == nil {
		return yoagent.JobObservation{}, errors.New("YO!agent job server is unavailable")
	}
	select {
	case <-ctx.Done():
		return yoagent.JobObservation{}, ctx.Err()
	default:
	}
	runtime.mu.RLock()
	prepareErr := runtime.prepareErr
	known := runtime.known
	statusSnapshot := runtime.status
	runtime.mu.RUnlock()
	if prepareErr != nil {
		return yoagent.JobObservation{}, prepareErr
	}
	jobType := nativeYOAgentJobText(job["type"])
	predicate := settingsObject(job["predicate"])
	target := settingsObject(job["target"])
	sessions := nativeYOAgentJobSessions(jobType, target)
	if len(sessions) == 0 {
		return yoagent.JobObservation{State: "missing"}, nil
	}
	for _, session := range sessions {
		if !known[session] {
			return yoagent.JobObservation{State: "missing", Fields: map[string]any{"session": session}}, nil
		}
	}
	states := make(map[string]string, len(sessions))
	for _, session := range sessions {
		state, observed := statusSnapshot[session]
		if !observed {
			return yoagent.JobObservation{}, fmt.Errorf("YO!agent job session %q was not included in the cycle observation", session)
		}
		states[session] = state
	}
	state := "unavailable"
	ready := false
	predicateType := nativeYOAgentJobText(predicate["type"])
	switch predicateType {
	case "session_idle", "session_needs_input", "session_blocked", "session_done_after_working":
		state = states[sessions[0]]
		switch predicateType {
		case "session_idle", "session_done_after_working":
			ready = state == "idle"
		case "session_needs_input":
			ready = state == "needs-input"
		case "session_blocked":
			ready = state == "blocked"
		}
	case "all_idle", "all_calm":
		state = predicateType
		ready = true
		for _, observed := range states {
			if observed != "idle" {
				state = "waiting"
				ready = false
				break
			}
		}
	case "interval":
		state, ready = "idle", true
	default:
		return yoagent.JobObservation{}, fmt.Errorf("unsupported YO!agent predicate %q", predicateType)
	}
	return yoagent.JobObservation{State: state, Ready: ready, Fields: map[string]any{"states": states, "observed_at": time.Now().UTC().Format(time.RFC3339Nano)}}, nil
}

func nativeYOAgentJobSessions(jobType string, target map[string]any) []string {
	if jobType == "notify_all_idle" || jobType == "wait_roster_then_send" {
		if roster, ok := target["roster"].([]any); ok {
			result := make([]string, 0, len(roster))
			for _, item := range roster {
				if session := strings.TrimSpace(nativeYOAgentJobText(item)); session != "" {
					result = append(result, session)
				}
			}
			return result
		}
		if roster, ok := target["roster"].([]string); ok {
			return append([]string(nil), roster...)
		}
		return nil
	}
	if session := strings.TrimSpace(nativeYOAgentJobText(target["session"])); session != "" {
		return []string{session}
	}
	return nil
}

func nativeYOAgentSessionState(metadata map[string]any) string {
	agents, _ := metadata["agents"].([]map[string]any)
	if len(agents) == 0 {
		return "unavailable"
	}
	state := "idle"
	for _, agent := range agents {
		switch nativeYOAgentJobText(agent["status"]) {
		case "working":
			return "working"
		case "needs-input":
			switch nativeYOAgentJobText(agent["attention_kind"]) {
			case "question":
				state = "needs-input"
			case "approval":
				state = "blocked"
			default:
				state = "unavailable"
			}
		case "blocked":
			if state != "needs-input" {
				state = "blocked"
			}
		case "idle":
		case "cooldown", "unavailable", "":
			return "unavailable"
		default:
			return "unavailable"
		}
	}
	return state
}

func (runtime *nativeYOAgentJobRuntime) Execute(ctx context.Context, job map[string]any) (yoagent.JobExecution, error) {
	action := settingsObject(job["action"])
	actionType := nativeYOAgentJobText(action["type"])
	if actionType == "send_prompt" {
		if runtime.server == nil || runtime.server.agentActions == nil {
			return yoagent.JobExecution{}, errors.New("verified YO!agent send owner is unavailable")
		}
		session := strings.TrimSpace(nativeYOAgentJobText(action["session"]))
		prompt := strings.TrimSpace(nativeYOAgentJobText(action["text"]))
		if session == "" || prompt == "" {
			return yoagent.JobExecution{Attempted: true, Succeeded: false, Result: map[string]any{"reason": "scheduled send has no target session or prompt"}}, nil
		}
		submit := action["submit"] != false
		preview, status := runtime.server.agentActions.previewWithLocale(ctx, session, prompt, submit, action["return_result"] == true, action["requires_confirmation"] == true, nativeYOAgentJobText(job["locale"]))
		if status != http.StatusOK {
			return yoagent.JobExecution{Attempted: true, Succeeded: false, Result: map[string]any{"reason": nativeAgentStringSetting(preview, "message", "target is not ready to accept a prompt"), "preview": preview}}, nil
		}
		previewID := nativeAgentStringSetting(preview, "id", "")
		result, status := runtime.server.agentActions.execute(ctx, previewID)
		if status != http.StatusOK {
			return yoagent.JobExecution{Attempted: true, Succeeded: false, Result: map[string]any{"reason": nativeAgentStringSetting(result, "error", "verified send failed"), "send": result}}, nil
		}
		watch := settingsObject(result["result_watch"])
		return yoagent.JobExecution{Attempted: true, Succeeded: true, Result: map[string]any{
			"send": result, "result_watch": watch,
			"return_result_supported": action["return_result"] != true || watch["started"] == true,
		}}, nil
	}
	if actionType != "notify_user" {
		return yoagent.JobExecution{Attempted: true, Succeeded: false, Result: map[string]any{"reason": "unsupported native YO!agent job action"}}, nil
	}
	if runtime.server == nil || runtime.server.events == nil {
		return yoagent.JobExecution{}, errors.New("YO!agent notification event owner is unavailable")
	}
	jobID := nativeYOAgentJobText(job["id"])
	message := strings.TrimSpace(nativeYOAgentJobText(action["message"]))
	if message == "" {
		return yoagent.JobExecution{Attempted: true, Succeeded: false}, errors.New("YO!agent notification has no message")
	}
	session := nativeYOAgentJobText(settingsObject(job["target"])["session"])
	notification := map[string]any{
		"title_key": "brand.tab.agent", "title": "YO!agent",
		"body_key": nativeYOAgentJobText(action["message_key"]), "body": message,
		"body_params": settingsObject(action["message_params"]), "session": session,
	}
	publicJob, err := yoagent.PublicJob(job)
	if err != nil {
		return yoagent.JobExecution{}, fmt.Errorf("redact YO!agent job notification: %w", err)
	}
	_, err = runtime.server.events.publish("yoagent_jobs_changed", map[string]any{
		"reason": "yoagent_job_fired", "job": publicJob, "notification": notification,
	})
	if err != nil {
		return yoagent.JobExecution{Attempted: true, Succeeded: false}, fmt.Errorf("publish YO!agent notification: %w", err)
	}
	return yoagent.JobExecution{Attempted: true, Succeeded: true, Result: map[string]any{"notification": notification, "message": message, "job_id": jobID}}, nil
}

func (s *server) runNativeYOAgentJobs(ctx context.Context) {
	if s.yoagentState == nil || s.yoagent == nil {
		return
	}
	engine, err := yoagent.NewJobEngine(s.yoagentState, &nativeYOAgentJobRuntime{server: s}, s.publishNativeYOAgentJobChange)
	if err != nil {
		slog.Error("native YO!agent job engine unavailable", "error", err)
		return
	}
	ticker := time.NewTicker(time.Second)
	defer ticker.Stop()
	for {
		if err := engine.RunOnce(ctx); err != nil && !errors.Is(err, context.Canceled) {
			slog.Error("native YO!agent job cycle failed", "error", err)
		}
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
	}
}

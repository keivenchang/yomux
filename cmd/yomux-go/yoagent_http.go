// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/keivenchang/yomux/internal/chat"
	"github.com/keivenchang/yomux/internal/yoagent"
)

func (s *server) agentAuthStatus(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		w.Header().Set("Allow", http.MethodGet)
		jsonResponse(w, http.StatusMethodNotAllowed, map[string]any{"error": "method_not_allowed"})
		return
	}
	if queryTrue(r.URL.Query().Get("force")) {
		s.pageMu.Lock()
		pending := s.agentAuthDone
		s.pageMu.Unlock()
		if pending != nil {
			select {
			case <-r.Context().Done():
				jsonResponse(w, http.StatusRequestTimeout, map[string]any{"error": "agent_auth_refresh_cancelled"})
				return
			case <-pending:
			}
		}
		s.pageMu.Lock()
		s.agentAuthExpires = time.Time{}
		s.pageMu.Unlock()
	}
	status, err := s.pageAgentStatus(r.Context(), true)
	if err != nil {
		slog.Error("YO!agent authentication status unavailable", "error", err)
		jsonResponse(w, http.StatusServiceUnavailable, map[string]any{"error": "agent_auth_unavailable"})
		return
	}
	available := make([]string, 0, 4)
	for _, agent := range []string{"claude", "codex", "opencode"} {
		if pageCommandAvailable(agent) {
			available = append(available, agent)
		}
	}
	available = append(available, "term")
	jsonResponse(w, http.StatusOK, map[string]any{"agentAuth": status, "availableAgents": available})
}

func (s *server) nativeYOAgentSettings(ctx context.Context) (map[string]any, error) {
	payload, err := s.nativeSettingsPayload(ctx, nil, false)
	if err != nil {
		return nil, err
	}
	if message, ok := payload["error"].(string); ok && message != "" {
		return nil, fmt.Errorf("settings are malformed: %s", message)
	}
	settings := settingsObject(payload["settings"])
	config := settingsObject(settings["yoagent"])
	if len(config) == 0 {
		return nil, errors.New("YO!agent settings are missing")
	}
	return config, nil
}

func (s *server) yoagentChat(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		w.Header().Set("Allow", http.MethodPost)
		jsonResponse(w, http.StatusMethodNotAllowed, map[string]any{"error": "method_not_allowed"})
		return
	}
	if s.yoagent == nil {
		jsonResponse(w, http.StatusServiceUnavailable, map[string]any{"error": "agent_unavailable"})
		return
	}
	var request struct {
		Message   string `json:"message"`
		Question  string `json:"question"`
		Locale    string `json:"locale"`
		RequestID string `json:"request_id"`
		StreamID  string `json:"stream_id"`
	}
	if err := decodeJSONBody(r.Body, 64*1024, &request); err != nil {
		writeChatError(w, chatRequestError("invalid", "invalid YO!agent request"))
		return
	}
	if request.Message == "" {
		request.Message = request.Question
	}
	localeState, err := s.pageLocales(request.Locale, r.Header.Get("Accept-Language"))
	if err != nil {
		jsonResponse(w, http.StatusServiceUnavailable, map[string]any{"error": "locale_unavailable"})
		return
	}
	locale := localeState.resolve(request.Locale, r.Header.Get("Accept-Language"))
	request.RequestID = nativeAgentRequestID(request.RequestID)
	request.StreamID = nativeAgentRequestID(request.StreamID)
	user, _ := r.Context().Value(authContextKey{}).(authUser)
	started := time.Now()
	sessions, rosterErr := s.tmuxSessionRoster()
	if rosterErr != nil {
		slog.Warn("YO!agent model-intent session inventory unavailable", "error", rosterErr)
		sessions = nil
	}
	answer, backend, err := s.yoagent.AnswerWithSessions(r.Context(), request.Message, locale, request.RequestID, request.StreamID, user.role, sessions)
	if err != nil {
		var protocol *chat.Error
		if errors.As(err, &protocol) {
			writeChatError(w, err)
		} else if errors.Is(err, context.Canceled) {
			jsonResponse(w, http.StatusOK, map[string]any{"ok": true, "cancelled": true, "request_id": request.RequestID, "stream_id": request.StreamID})
		} else {
			slog.Error("YO!agent request failed", "error", err)
			jsonResponse(w, http.StatusBadGateway, map[string]any{"error": "agent_backend_failed", "message": "The configured agent backend could not complete the request."})
		}
		return
	}
	conversation, err := s.yoagentConversationPayload(r)
	if err != nil {
		slog.Error("YO!agent conversation read after answer failed", "error", err)
		jsonResponse(w, http.StatusServiceUnavailable, map[string]any{"error": "conversation_unavailable"})
		return
	}
	jsonResponse(w, http.StatusOK, map[string]any{
		"ok": true, "answer": answer, "actions": []any{}, "backend": backend["backend"], "backend_used": backend["backend"],
		"fallback": false, "details": "", "detail_rows": []any{}, "cli": backend,
		"answered_at": time.Now().UTC().Format(time.RFC3339Nano), "request_id": nativeAgentRequestID(request.RequestID),
		"stream_id": nativeAgentRequestID(request.StreamID), "timing": map[string]any{"ttfr_ms": float64(time.Since(started).Microseconds()) / 1000},
		"conversation": conversation, "job": backend["job"],
	})
}

func (s *server) yoagentChatCancel(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		w.Header().Set("Allow", http.MethodPost)
		jsonResponse(w, http.StatusMethodNotAllowed, map[string]any{"error": "method_not_allowed"})
		return
	}
	if s.yoagent == nil {
		jsonResponse(w, http.StatusServiceUnavailable, map[string]any{"error": "agent_unavailable"})
		return
	}
	user, _ := r.Context().Value(authContextKey{}).(authUser)
	if user.role != "admin" {
		jsonResponse(w, http.StatusForbidden, map[string]any{"error": "forbidden"})
		return
	}
	pathID := strings.TrimSuffix(strings.TrimPrefix(r.URL.Path, "/api/yoagent/chat/"), "/cancel")
	var body map[string]json.RawMessage
	if err := decodeJSONBody(r.Body, 16*1024, &body); err != nil {
		jsonResponse(w, http.StatusBadRequest, map[string]any{"error": "invalid_request"})
		return
	}
	requestID, err := chatBodyString(body, "request_id")
	if err != nil {
		jsonResponse(w, http.StatusBadRequest, map[string]any{"error": "invalid_request"})
		return
	}
	if requestID == "" {
		requestID = pathID
	}
	if !s.yoagent.Cancel(requestID) {
		jsonResponse(w, http.StatusNotFound, map[string]any{"error": "request_not_active", "request_id": requestID})
		return
	}
	jsonResponse(w, http.StatusOK, map[string]any{"ok": true, "cancelled": true, "request_id": requestID})
}

func (s *server) yoagentConversation(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		w.Header().Set("Allow", http.MethodGet)
		jsonResponse(w, http.StatusMethodNotAllowed, map[string]any{"error": "method_not_allowed"})
		return
	}
	payload, err := s.yoagentConversationPayload(r)
	if err != nil {
		slog.Error("YO!agent conversation load failed", "error", err)
		jsonResponse(w, http.StatusServiceUnavailable, map[string]any{"error": "conversation_unavailable"})
		return
	}
	jsonResponse(w, http.StatusOK, payload)
}

func (s *server) yoagentJobs(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		w.Header().Set("Allow", http.MethodGet)
		jsonResponse(w, http.StatusMethodNotAllowed, map[string]any{"error": "method_not_allowed"})
		return
	}
	if s.yoagentState == nil {
		jsonResponse(w, http.StatusServiceUnavailable, map[string]any{"error": "jobs_unavailable"})
		return
	}
	jobs, issues, err := s.yoagentState.LoadJobs(r.Context())
	if err != nil {
		slog.Error("YO!agent jobs load failed", "error", err)
		jsonResponse(w, http.StatusServiceUnavailable, map[string]any{"error": "jobs_unavailable"})
		return
	}
	public := make([]map[string]any, 0, len(jobs))
	for _, job := range jobs {
		clean, err := yoagent.PublicJob(job)
		if err != nil {
			slog.Error("YO!agent job response copy failed", "error", err)
			jsonResponse(w, http.StatusServiceUnavailable, map[string]any{"error": "jobs_unavailable"})
			return
		}
		public = append(public, clean)
	}
	for _, issue := range issues {
		slog.Warn("YO!agent state record skipped during recovery", "path", issue.Path, "line", issue.Line, "reason", issue.Reason)
	}
	jsonResponse(w, http.StatusOK, map[string]any{"ok": true, "jobs": public, "recovery_issues": issues})
}

func (s *server) yoagentJobMutation(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		w.Header().Set("Allow", http.MethodPost)
		jsonResponse(w, http.StatusMethodNotAllowed, map[string]any{"error": "method_not_allowed"})
		return
	}
	if s.yoagentState == nil {
		jsonResponse(w, http.StatusServiceUnavailable, map[string]any{"error": "jobs_unavailable"})
		return
	}
	if r.URL.Path == "/api/yoagent/jobs/cancel-session" {
		s.yoagentJobsCancelSession(w, r)
		return
	}
	if r.URL.Path == "/api/yoagent/jobs" {
		s.yoagentJobCreate(w, r)
		return
	}
	suffix := ""
	switch {
	case strings.HasSuffix(r.URL.Path, "/confirm"):
		suffix = "/confirm"
	case strings.HasSuffix(r.URL.Path, "/cancel"):
		suffix = "/cancel"
	default:
		http.NotFound(w, r)
		return
	}
	id := strings.TrimSpace(strings.TrimSuffix(strings.TrimPrefix(r.URL.Path, "/api/yoagent/jobs/"), suffix))
	if id == "" || strings.Contains(id, "/") {
		jsonResponse(w, http.StatusBadRequest, map[string]any{"error": "invalid_job_id"})
		return
	}
	var body map[string]json.RawMessage
	if err := decodeJSONBody(r.Body, 16*1024, &body); err != nil {
		jsonResponse(w, http.StatusBadRequest, map[string]any{"error": "invalid_request"})
		return
	}
	if bodyID, err := chatBodyString(body, "id"); err != nil {
		jsonResponse(w, http.StatusBadRequest, map[string]any{"error": "invalid_request"})
		return
	} else if bodyID != "" {
		id = bodyID
	}
	s.yoagentJobTransition(w, r, id, suffix == "/confirm")
}

func (s *server) yoagentJobCreate(w http.ResponseWriter, r *http.Request) {
	var payload map[string]any
	if err := decodeJSONBody(r.Body, 64*1024, &payload); err != nil {
		jsonResponse(w, http.StatusBadRequest, map[string]any{"error": "invalid_request"})
		return
	}
	if payload == nil {
		jsonResponse(w, http.StatusBadRequest, map[string]any{"error": "invalid_request"})
		return
	}
	sessions, err := s.tmuxSessionRoster()
	if err != nil {
		slog.Error("YO!agent job session inventory failed", "error", err)
		jsonResponse(w, http.StatusServiceUnavailable, map[string]any{"error": "session_roster_unavailable"})
		return
	}
	user, _ := r.Context().Value(authContextKey{}).(authUser)
	job, err := yoagent.BuildJob(payload, sessions, user.username, time.Now())
	if err != nil {
		status := http.StatusBadRequest
		if strings.HasPrefix(err.Error(), "unknown YO!agent target session") {
			status = http.StatusNotFound
		}
		jsonResponse(w, status, map[string]any{"ok": false, "error": "invalid_job", "message": err.Error()})
		return
	}
	s.yoagentJobMu.Lock()
	created, inserted, err := s.yoagentState.CreateJob(r.Context(), job)
	s.yoagentJobMu.Unlock()
	if err != nil {
		slog.Error("YO!agent job persistence failed", "error", err)
		jsonResponse(w, http.StatusServiceUnavailable, map[string]any{"error": "job_persistence_failed"})
		return
	}
	public, err := yoagent.PublicJob(created)
	if err != nil {
		slog.Error("YO!agent job response failed", "error", err)
		jsonResponse(w, http.StatusInternalServerError, map[string]any{"error": "job_response_failed"})
		return
	}
	if !inserted {
		jsonResponse(w, http.StatusConflict, map[string]any{"ok": false, "duplicate": true, "job": public})
		return
	}
	if err := s.publishNativeYOAgentJobChange("yoagent_job_created", created); err != nil {
		slog.Error("YO!agent job event publication failed after persistence", "job_id", created["id"], "error", err)
		jsonResponse(w, http.StatusServiceUnavailable, map[string]any{"error": "job_event_failed", "job": public})
		return
	}
	jsonResponse(w, http.StatusOK, map[string]any{"ok": true, "job": public})
}

func (s *server) yoagentJobTransition(w http.ResponseWriter, r *http.Request, id string, confirm bool) {
	s.yoagentJobMu.Lock()
	defer s.yoagentJobMu.Unlock()
	job, err := s.yoagentState.LoadJob(r.Context(), id)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			jsonResponse(w, http.StatusNotFound, map[string]any{"ok": false, "error": "job_not_found"})
			return
		}
		slog.Error("YO!agent job load failed", "job_id", id, "error", err)
		jsonResponse(w, http.StatusServiceUnavailable, map[string]any{"error": "jobs_unavailable"})
		return
	}
	status, _ := job["status"].(string)
	now := time.Now().UTC()
	var expected []string
	var eventType string
	if confirm {
		if status != "pending_confirmation" {
			public, publicErr := yoagent.PublicJob(job)
			if publicErr != nil {
				jsonResponse(w, http.StatusInternalServerError, map[string]any{"error": "job_response_failed"})
				return
			}
			jsonResponse(w, http.StatusOK, map[string]any{"ok": true, "job": public})
			return
		}
		expected, eventType = []string{"pending_confirmation"}, "yoagent_job_confirmed"
		job["status"], job["confirmed_at"] = "queued", now.Format(time.RFC3339Nano)
	} else {
		if status != "queued" && status != "pending_confirmation" {
			public, publicErr := yoagent.PublicJob(job)
			if publicErr != nil {
				jsonResponse(w, http.StatusInternalServerError, map[string]any{"error": "job_response_failed"})
				return
			}
			jsonResponse(w, http.StatusOK, map[string]any{"ok": true, "job": public})
			return
		}
		expected, eventType = []string{"queued", "pending_confirmation"}, "yoagent_job_cancelled"
		job["status"], job["cancelled_at"] = "cancelled", now.Format(time.RFC3339Nano)
	}
	changed, err := s.yoagentState.TransitionJob(r.Context(), id, expected, job)
	if err != nil {
		slog.Error("YO!agent job transition failed", "job_id", id, "error", err)
		jsonResponse(w, http.StatusServiceUnavailable, map[string]any{"error": "job_transition_failed"})
		return
	}
	if !changed {
		jsonResponse(w, http.StatusConflict, map[string]any{"error": "job_state_changed"})
		return
	}
	public, err := yoagent.PublicJob(job)
	if err != nil {
		jsonResponse(w, http.StatusInternalServerError, map[string]any{"error": "job_response_failed"})
		return
	}
	if err := s.publishNativeYOAgentJobChange(eventType, job); err != nil {
		slog.Error("YO!agent job event publication failed after transition", "job_id", id, "error", err)
		jsonResponse(w, http.StatusServiceUnavailable, map[string]any{"error": "job_event_failed", "job": public})
		return
	}
	jsonResponse(w, http.StatusOK, map[string]any{"ok": true, "job": public})
}

func (s *server) yoagentJobsCancelSession(w http.ResponseWriter, r *http.Request) {
	var body map[string]json.RawMessage
	if err := decodeJSONBody(r.Body, 16*1024, &body); err != nil {
		jsonResponse(w, http.StatusBadRequest, map[string]any{"error": "invalid_request"})
		return
	}
	session, err := chatBodyString(body, "session")
	if err != nil || session == "" {
		jsonResponse(w, http.StatusBadRequest, map[string]any{"ok": false, "error": "target_session_required"})
		return
	}
	sessions, err := s.tmuxSessionRoster()
	if err != nil {
		jsonResponse(w, http.StatusServiceUnavailable, map[string]any{"error": "session_roster_unavailable"})
		return
	}
	known := false
	for _, candidate := range sessions {
		known = known || candidate == session
	}
	if !known {
		jsonResponse(w, http.StatusNotFound, map[string]any{"ok": false, "error": "unknown_session", "session": session})
		return
	}
	s.yoagentJobMu.Lock()
	jobs, _, err := s.yoagentState.LoadJobs(r.Context())
	if err != nil {
		s.yoagentJobMu.Unlock()
		jsonResponse(w, http.StatusServiceUnavailable, map[string]any{"error": "jobs_unavailable"})
		return
	}
	now := time.Now().UTC().Format(time.RFC3339Nano)
	cancelled := make([]map[string]any, 0)
	for _, job := range jobs {
		status, _ := job["status"].(string)
		if status != "queued" && status != "pending_confirmation" {
			continue
		}
		if !nativeYOAgentJobTargetsSession(job, session) {
			continue
		}
		job["status"], job["cancelled_at"] = "cancelled", now
		changed, transitionErr := s.yoagentState.TransitionJob(r.Context(), nativeYOAgentJobText(job["id"]), []string{"queued", "pending_confirmation"}, job)
		if transitionErr != nil {
			err = errors.Join(err, transitionErr)
			break
		}
		if changed {
			cancelled = append(cancelled, job)
		}
	}
	s.yoagentJobMu.Unlock()
	if err != nil {
		slog.Error("YO!agent session jobs cancellation failed", "session", session, "error", err)
		jsonResponse(w, http.StatusServiceUnavailable, map[string]any{"error": "jobs_unavailable"})
		return
	}
	public := make([]map[string]any, 0, len(cancelled))
	for _, job := range cancelled {
		copyJob, copyErr := yoagent.PublicJob(job)
		if copyErr != nil {
			jsonResponse(w, http.StatusInternalServerError, map[string]any{"error": "job_response_failed"})
			return
		}
		public = append(public, copyJob)
	}
	if len(public) > 0 {
		if err := s.publishNativeYOAgentJobChange("yoagent_jobs_cancelled_for_session", map[string]any{"session": session, "jobs": public}); err != nil {
			slog.Error("YO!agent cancellation event publication failed after persistence", "session", session, "error", err)
			jsonResponse(w, http.StatusServiceUnavailable, map[string]any{"error": "job_event_failed", "jobs": public})
			return
		}
	}
	jsonResponse(w, http.StatusOK, map[string]any{"ok": true, "session": session, "count": len(public), "jobs": public})
}

func nativeYOAgentJobTargetsSession(job map[string]any, session string) bool {
	target := settingsObject(job["target"])
	action := settingsObject(job["action"])
	if target["session"] == session || action["session"] == session {
		return true
	}
	for _, candidate := range yoagentValues(target["roster"]) {
		if nativeYOAgentJobText(candidate) == session {
			return true
		}
	}
	return false
}

func nativeYOAgentJobText(value any) string {
	text, _ := value.(string)
	return text
}

func yoagentValues(value any) []any {
	switch values := value.(type) {
	case []any:
		return values
	case []string:
		result := make([]any, len(values))
		for index, item := range values {
			result[index] = item
		}
		return result
	default:
		return nil
	}
}

func (s *server) publishNativeYOAgentJobChange(kind string, job map[string]any) error {
	if s.events == nil {
		return errors.New("YO!agent event owner is unavailable")
	}
	payload := map[string]any{"reason": kind}
	if _, exists := job["id"]; exists {
		public, err := yoagent.PublicJob(job)
		if err != nil {
			return fmt.Errorf("redact YO!agent job event: %w", err)
		}
		payload["job"] = public
	} else {
		jobs, _ := job["jobs"].([]map[string]any)
		payload["session"], payload["jobs"], payload["count"] = job["session"], job["jobs"], len(jobs)
	}
	_, err := s.events.publish("yoagent_jobs_changed", payload)
	return err
}

func (s *server) yoagentSkills(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		w.Header().Set("Allow", http.MethodGet)
		jsonResponse(w, http.StatusMethodNotAllowed, map[string]any{"error": "method_not_allowed"})
		return
	}
	payload, err := yoagent.LoadSkillSnapshot(s.root, s.config)
	if err != nil {
		slog.Error("YO!agent skills load failed", "error", err)
		jsonResponse(w, http.StatusServiceUnavailable, map[string]any{"error": "skills_unavailable"})
		return
	}
	jsonResponse(w, http.StatusOK, payload)
}

func (s *server) yoagentSkillFiles(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		w.Header().Set("Allow", http.MethodGet)
		jsonResponse(w, http.StatusMethodNotAllowed, map[string]any{"error": "method_not_allowed"})
		return
	}
	name := strings.TrimSpace(r.URL.Query().Get("name"))
	if name == "" {
		jsonResponse(w, http.StatusOK, yoagent.ListUserSkillFiles(s.config))
		return
	}
	payload, err := yoagent.ReadUserSkillFile(s.config, r.URL.Query().Get("kind"), name)
	if err != nil {
		status := http.StatusBadRequest
		if errors.Is(err, os.ErrNotExist) {
			status = http.StatusNotFound
		}
		slog.Warn("YO!agent skill file read failed", "name", name, "error", err)
		jsonResponse(w, status, map[string]any{"error": "skill_file_unavailable", "kind": r.URL.Query().Get("kind"), "name": name})
		return
	}
	jsonResponse(w, http.StatusOK, map[string]any{"ok": true, "file": payload})
}

func (s *server) yoagentSkillFileUpsert(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		w.Header().Set("Allow", http.MethodPost)
		jsonResponse(w, http.StatusMethodNotAllowed, map[string]any{"error": "method_not_allowed"})
		return
	}
	var body map[string]json.RawMessage
	if err := decodeJSONBody(r.Body, 72*1024, &body); err != nil {
		jsonResponse(w, http.StatusBadRequest, map[string]any{"error": "invalid_request"})
		return
	}
	kind, err := chatBodyString(body, "kind")
	if err != nil {
		jsonResponse(w, http.StatusBadRequest, map[string]any{"error": "invalid_request"})
		return
	}
	name, err := chatBodyString(body, "name")
	if err != nil {
		jsonResponse(w, http.StatusBadRequest, map[string]any{"error": "invalid_request"})
		return
	}
	text, err := chatBodyString(body, "text")
	if err != nil {
		jsonResponse(w, http.StatusBadRequest, map[string]any{"error": "invalid_request"})
		return
	}
	file, err := yoagent.WriteUserSkillFile(r.Context(), s.config, kind, name, text)
	if err != nil {
		status := http.StatusBadRequest
		var validation yoagent.SkillValidationError
		if !errors.As(err, &validation) {
			status = http.StatusInternalServerError
		}
		jsonResponse(w, status, map[string]any{"error": "skill_file_write_failed", "message": err.Error()})
		return
	}
	if err := s.publishNativeYOAgentSkillChange(r.Context(), file, false); err != nil {
		slog.Error("YO!agent skill update event failed", "error", err)
		jsonResponse(w, http.StatusServiceUnavailable, map[string]any{"error": "skill_file_event_failed"})
		return
	}
	skills, err := yoagent.LoadSkillSnapshot(s.root, s.config)
	if err != nil {
		jsonResponse(w, http.StatusServiceUnavailable, map[string]any{"error": "skills_unavailable"})
		return
	}
	jsonResponse(w, http.StatusOK, map[string]any{"ok": true, "file": file, "skills": skills})
}

func (s *server) yoagentSkillFileDelete(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		w.Header().Set("Allow", http.MethodPost)
		jsonResponse(w, http.StatusMethodNotAllowed, map[string]any{"error": "method_not_allowed"})
		return
	}
	var body map[string]json.RawMessage
	if err := decodeJSONBody(r.Body, 16*1024, &body); err != nil {
		jsonResponse(w, http.StatusBadRequest, map[string]any{"error": "invalid_request"})
		return
	}
	kind, err := chatBodyString(body, "kind")
	if err != nil {
		jsonResponse(w, http.StatusBadRequest, map[string]any{"error": "invalid_request"})
		return
	}
	name, err := chatBodyString(body, "name")
	if err != nil {
		jsonResponse(w, http.StatusBadRequest, map[string]any{"error": "invalid_request"})
		return
	}
	file, err := yoagent.DeleteUserSkillFile(r.Context(), s.config, kind, name)
	if err != nil {
		status := http.StatusInternalServerError
		var validation yoagent.SkillValidationError
		if errors.As(err, &validation) {
			status = http.StatusBadRequest
		}
		if errors.Is(err, os.ErrNotExist) {
			status = http.StatusNotFound
		}
		jsonResponse(w, status, map[string]any{"error": "skill_file_delete_failed", "message": err.Error()})
		return
	}
	if err := s.publishNativeYOAgentSkillChange(r.Context(), file, true); err != nil {
		slog.Error("YO!agent skill deletion event failed", "error", err)
		jsonResponse(w, http.StatusServiceUnavailable, map[string]any{"error": "skill_file_event_failed"})
		return
	}
	skills, err := yoagent.LoadSkillSnapshot(s.root, s.config)
	if err != nil {
		jsonResponse(w, http.StatusServiceUnavailable, map[string]any{"error": "skills_unavailable"})
		return
	}
	jsonResponse(w, http.StatusOK, map[string]any{"ok": true, "file": file, "skills": skills})
}

func (s *server) publishNativeYOAgentSkillChange(ctx context.Context, file map[string]any, deleted bool) error {
	payload := map[string]any{"kind": file["kind"], "name": file["name"], "path": file["path"], "deleted": deleted}
	_, err := s.events.publish("yoagent_skills_changed", payload)
	return err
}

func (s *server) yoagentReset(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		w.Header().Set("Allow", http.MethodPost)
		jsonResponse(w, http.StatusMethodNotAllowed, map[string]any{"error": "method_not_allowed"})
		return
	}
	if s.yoagentState == nil {
		jsonResponse(w, http.StatusServiceUnavailable, map[string]any{"error": "conversation_unavailable"})
		return
	}
	var backups []string
	var err error
	if s.yoagent != nil {
		backups, err = s.yoagent.Reset(r.Context())
	} else {
		backups, err = s.yoagentState.Reset(r.Context())
	}
	if err != nil {
		slog.Error("YO!agent conversation reset failed", "error", err)
		jsonResponse(w, http.StatusServiceUnavailable, map[string]any{"error": "conversation_reset_failed"})
		return
	}
	if len(backups) > 0 {
		slog.Info("YO!agent reset preserved recovery backups", "count", len(backups))
	}
	payload, err := s.yoagentConversationPayload(r)
	if err != nil {
		slog.Error("YO!agent conversation read after reset failed", "error", err)
		jsonResponse(w, http.StatusServiceUnavailable, map[string]any{"error": "conversation_unavailable"})
		return
	}
	jsonResponse(w, http.StatusOK, map[string]any{"ok": true, "conversation": payload})
}

func (s *server) yoagentConversationPayload(r *http.Request) (map[string]any, error) {
	return s.yoagentConversationPayloadContext(r.Context())
}

func (s *server) yoagentConversationPayloadContext(ctx context.Context) (map[string]any, error) {
	if s.yoagentState == nil {
		return nil, errors.New("YO!agent state is unavailable")
	}
	messages, issues, err := s.yoagentState.LoadMessages(ctx, yoagent.ConversationLimit)
	if err != nil {
		return nil, err
	}
	for _, issue := range issues {
		slog.Warn("YO!agent conversation record skipped during recovery", "path", issue.Path, "line", issue.Line, "reason", issue.Reason)
	}
	path := s.yoagentState.ConversationPath()
	displayPath := path
	if home, err := os.UserHomeDir(); err == nil {
		if relative, relErr := filepath.Rel(home, path); relErr == nil && relative != ".." && !strings.HasPrefix(relative, ".."+string(filepath.Separator)) {
			displayPath = filepath.Join("~", relative)
		}
	}
	pendingWaits := []map[string]any{}
	if s.agentActions != nil {
		pendingWaits = s.agentActions.pendingWaits()
	}
	return map[string]any{
		"ok":                      true,
		"messages":                messages,
		"transcript_path":         path,
		"transcript_display_path": displayPath,
		"resume_backends":         []string{},
		"pending_waits":           pendingWaits,
		"recovery_issues":         issues,
	}, nil
}

func (s *server) publishNativeYOAgentConversationChanged(reason string) error {
	if s.events == nil {
		return errors.New("YO!agent conversation event owner is unavailable")
	}
	_, err := s.events.publish("yoagent_conversation_changed", map[string]any{"reason": reason})
	return err
}

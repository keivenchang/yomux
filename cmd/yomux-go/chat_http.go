// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"encoding/json"
	"errors"
	"log/slog"
	"math/big"
	"net"
	"net/http"
	"strconv"
	"strings"

	"github.com/keivenchang/yomux/internal/chat"
)

// Chat routes share authentication and capability admission; YO!agent replies
// remain independently unavailable until their responder is installed.
func (s *server) registerChatRoutes(mux *http.ServeMux) {
	for _, path := range []string{"bootstrap", "page", "delta", "context", "search", "send", "yoagent", "typing", "read"} {
		mux.Handle("/api/chat/"+path, s.protected(http.HandlerFunc(s.chatHTTP)))
	}
}

func chatRequestError(code, message string) error {
	return &chat.Error{Code: code, Status: http.StatusBadRequest, Message: message}
}

func writeChatError(w http.ResponseWriter, err error) {
	status, code, message := http.StatusInternalServerError, "server_error", "chat request failed"
	var protocol *chat.Error
	var validation *chat.ValidationError
	switch {
	case errors.As(err, &protocol):
		status, code, message = protocol.Status, protocol.Code, protocol.Message
	case errors.As(err, &validation):
		status, code, message = http.StatusBadRequest, "invalid", validation.Reason
	default:
		slog.Error("chat request failed", "error", err)
	}
	payload := typedError(message, "common.requestFailed", false)
	payload["status"], payload["code"] = status, code
	jsonResponse(w, status, payload)
}

// Python's page/delta store accepts an invalid limit as the default, while
// context/search reject it. Clamp arbitrary-size integers before conversion.
func chatQueryInteger(r *http.Request, name string, fallback, minimum, maximum int, permissive bool) (*int, error) {
	raw := strings.TrimSpace(r.URL.Query().Get(name))
	if raw == "" {
		return nil, nil
	}
	number, ok := new(big.Int).SetString(raw, 10)
	if !ok {
		if permissive {
			return nil, nil
		}
		return nil, chatRequestError("invalid", "invalid chat "+name)
	}
	result := fallback
	switch {
	case number.Cmp(big.NewInt(int64(minimum))) < 0:
		result = minimum
	case number.Cmp(big.NewInt(int64(maximum))) > 0:
		result = maximum
	default:
		result = int(number.Int64())
	}
	return &result, nil
}

func chatMessageID(raw any) (int64, error) {
	// HTTP IDs come from a query string or JSON; preserve numeric JSON IDs and
	// numeric strings without accepting a fractional or overflowing identity.
	switch value := raw.(type) {
	case string:
		id, err := strconv.ParseInt(strings.TrimSpace(value), 10, 64)
		if err == nil {
			return id, nil
		}
	case json.RawMessage:
		var number json.Number
		if json.Unmarshal(value, &number) == nil {
			if id, err := number.Int64(); err == nil {
				return id, nil
			}
		}
	}
	return 0, chatRequestError("invalid", "invalid chat message ID")
}

func chatBodyString(body map[string]json.RawMessage, name string) (string, error) {
	value := body[name]
	if value == nil {
		return "", nil
	}
	var text string
	if err := json.Unmarshal(value, &text); err != nil {
		return "", chatRequestError("invalid", "invalid chat "+name)
	}
	return text, nil
}

func chatPeerIP(r *http.Request) string {
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err == nil {
		return host
	}
	// Never trust proxy headers: this is the transport peer, as in Python.
	return r.RemoteAddr
}

func (s *server) chatHTTP(w http.ResponseWriter, r *http.Request) {
	action := strings.TrimPrefix(r.URL.Path, "/api/chat/")
	write := action == "send" || action == "yoagent" || action == "typing" || action == "read"
	method := http.MethodGet
	if write {
		method = http.MethodPost
	}
	if r.Method != method {
		w.Header().Set("Allow", method)
		jsonResponse(w, http.StatusMethodNotAllowed, typedError("method not allowed", "common.requestFailed", false))
		return
	}
	if s.chat == nil {
		jsonResponse(w, http.StatusNotImplemented, map[string]any{"error": "feature_unavailable", "capability": "pane.chat.v1"})
		return
	}
	user, ok := r.Context().Value(authContextKey{}).(authUser)
	if !ok || user.username == "" {
		s.rejectUnauthorized(w, r)
		return
	}
	// Reading settings before dispatch prevents mutations with a malformed file.
	if err := s.chat.settingsReady(r.Context()); err != nil {
		writeChatError(w, err)
		return
	}
	var body map[string]json.RawMessage
	if write {
		limit := int64(4096)
		if action == "send" {
			limit = 12 * 1024
		}
		if err := decodeJSONBody(r.Body, limit, &body); err != nil || body == nil {
			writeChatError(w, chatRequestError("invalid", "invalid chat request"))
			return
		}
	}
	payload, err := s.chatRequest(r, user, action, body)
	if err != nil {
		writeChatError(w, err)
		return
	}
	status := http.StatusOK
	switch sent := payload.(type) {
	case chat.SendPayload:
		if sent.Created {
			status = http.StatusCreated
		}
	case chat.AgentReplyPayload:
		if sent.Created {
			status = http.StatusCreated
		}
	}
	jsonResponse(w, status, payload)
}

func (s *server) chatRequest(r *http.Request, user authUser, action string, body map[string]json.RawMessage) (any, error) {
	ctx, service := r.Context(), s.chat.service
	query := r.URL.Query()
	switch action {
	case "bootstrap":
		payload, err := service.Bootstrap(ctx, user.username, query.Get("browser_instance_id"))
		return struct {
			chat.BootstrapPayload
			ClientIP string `json:"client_ip"`
		}{payload, chatPeerIP(r)}, err
	case "page", "delta", "search":
		fallback, maximum := 50, 200
		if action == "delta" {
			fallback = 200
		}
		if action == "search" {
			fallback, maximum = 20, 100
		}
		limit, err := chatQueryInteger(r, "limit", fallback, 1, maximum, action != "search")
		if err != nil {
			return nil, err
		}
		switch action {
		case "page":
			return service.Page(ctx, user.username, query.Get("before"), limit)
		case "delta":
			return service.Delta(ctx, user.username, query.Get("after"), limit)
		default:
			return service.Search(ctx, user.username, query.Get("query"), query.Get("cursor"), limit)
		}
	case "context":
		id, err := chatMessageID(query.Get("message_id"))
		if err != nil {
			return nil, err
		}
		before, err := chatQueryInteger(r, "before", 3, 0, 25, false)
		if err != nil {
			return nil, err
		}
		after, err := chatQueryInteger(r, "after", 3, 0, 25, false)
		if err != nil {
			return nil, err
		}
		return service.Context(ctx, user.username, id, before, after)
	case "send":
		browser, err := chatBodyString(body, "browser_instance_id")
		if err != nil {
			return nil, err
		}
		uuid, err := chatBodyString(body, "client_message_uuid")
		if err != nil {
			return nil, err
		}
		text, err := chatBodyString(body, "body")
		if err != nil {
			return nil, err
		}
		preference, err := s.requestLocalePreference(r)
		if err != nil {
			return nil, err
		}
		payload, err := service.Send(ctx, user.username, chat.SendRequest{BrowserInstance: browser, ClientUUID: uuid, Body: text}, preference, chatPeerIP(r))
		if err != nil {
			return nil, err
		}
		// A retry also repairs a committed send whose publication failed.
		return payload, s.chat.events.Flush(ctx)
	case "yoagent":
		if s.chat.replies == nil {
			return nil, &chat.Error{Code: "feature_unavailable", Status: 501, Message: "chat agent responder unavailable"}
		}
		browser, err := chatBodyString(body, "browser_instance_id")
		if err != nil {
			return nil, err
		}
		id, err := chatMessageID(body["message_id"])
		if err != nil {
			return nil, err
		}
		preference, err := s.requestLocalePreference(r)
		if err != nil {
			return nil, err
		}
		return s.chat.replies.Reply(ctx, user.username, browser, id, preference, user.role)
	case "typing":
		browser, err := chatBodyString(body, "browser_instance_id")
		if err != nil {
			return nil, err
		}
		typing := string(body["typing"]) == "true"
		payload, err := service.Typing(ctx, user.username, browser, typing)
		if err != nil {
			return nil, err
		}
		return payload, s.chat.events.Flush(ctx)
	case "read":
		id, err := chatMessageID(body["message_id"])
		if err != nil {
			return nil, err
		}
		return service.Read(ctx, user.username, id)
	default:
		return nil, &chat.Error{Code: "not_found", Status: 404, Message: "chat route not found"}
	}
}

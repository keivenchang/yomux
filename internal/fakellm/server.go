// Package fakellm provides a deterministic, localhost-friendly LLM HTTP stub.
package fakellm

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"strconv"
	"strings"
	"time"
)

const (
	MaxRequestBody  = 1 << 20
	MaxResponseBody = 1 << 20
	MaxDelay        = 30 * time.Second
	MaxHold         = 60 * time.Second
	openAIModel     = "gpt-4o-mini"
	anthropicModel  = "claude-3-5-sonnet-20241022"
)

type Config struct {
	Scenario string
	Delay    time.Duration
	Hold     time.Duration
}

func ConfigFromEnv(getenv func(string) string) Config {
	return Config{
		Scenario: getenv("FAKE_LLM_SCENARIO"),
		Delay:    boundedDuration(getenv("FAKE_LLM_DELAY"), MaxDelay),
		Hold:     boundedDuration(getenv("FAKE_LLM_HOLD"), MaxHold),
	}
}

func NewHandler(config Config) http.Handler {
	if config.Scenario == "" {
		config.Scenario = "normal"
	}
	config.Delay = clamp(config.Delay, MaxDelay)
	config.Hold = clamp(config.Hold, MaxHold)
	return &handler{config: config}
}

type handler struct{ config Config }

func (h *handler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost || !providerPath(r.URL.Path) {
		writeJSON(w, http.StatusNotFound, map[string]any{"error": map[string]any{"type": "not_found", "message": "unsupported endpoint"}})
		return
	}
	if r.ContentLength > MaxRequestBody {
		writeJSON(w, http.StatusRequestEntityTooLarge, map[string]any{"error": map[string]any{"type": "request_too_large"}})
		return
	}
	r.Body = http.MaxBytesReader(w, r.Body, MaxRequestBody)
	body, err := io.ReadAll(r.Body)
	if err != nil {
		status := http.StatusBadRequest
		if errors.Is(err, io.ErrUnexpectedEOF) || strings.Contains(err.Error(), "request body too large") {
			status = http.StatusRequestEntityTooLarge
		}
		writeJSON(w, status, map[string]any{"error": map[string]any{"type": "invalid_request", "message": "request body unavailable"}})
		return
	}
	var request map[string]any
	if err := json.Unmarshal(body, &request); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": map[string]any{"type": "invalid_request", "message": "request must be JSON"}})
		return
	}
	scenario := scenarioFor(r, h.config.Scenario)
	if scenario == "disconnect" {
		if hijacker, ok := w.(http.Hijacker); ok {
			connection, _, err := hijacker.Hijack()
			if err == nil {
				_ = connection.Close()
			}
		}
		return
	}
	delay := h.config.Delay
	hold := h.config.Hold
	if scenario == "delay" && delay == 0 {
		delay = 100 * time.Millisecond
	}
	if scenario == "hold" && hold == 0 {
		hold = 5 * time.Second
	}
	if !wait(r.Context(), delay+hold) {
		return
	}
	if scenario == "error" {
		writeJSON(w, http.StatusBadGateway, map[string]any{"error": map[string]any{"type": "fake_llm_error", "message": "deterministic fake provider error"}})
		return
	}
	stream, _ := request["stream"].(bool)
	anthropic := strings.Contains(r.URL.Path, "messages")
	response := responseFor(scenario, anthropic)
	if stream {
		writeStream(w, response, anthropic)
		return
	}
	writeJSON(w, http.StatusOK, response)
}

func providerPath(path string) bool {
	return path == "/v1/chat/completions" || path == "/chat/completions" || path == "/v1/messages" || path == "/messages" || strings.HasPrefix(path, "/scenario/")
}

func scenarioFor(r *http.Request, fallback string) string {
	parts := strings.Split(strings.Trim(r.URL.Path, "/"), "/")
	if len(parts) >= 2 && parts[0] == "scenario" && parts[1] != "" {
		return parts[1]
	}
	if value := r.URL.Query().Get("scenario"); value != "" {
		return value
	}
	if fallback != "" {
		return fallback
	}
	return "normal"
}

func responseFor(scenario string, anthropic bool) map[string]any {
	if anthropic {
		return anthropicResponse(scenario)
	}
	return openAIResponse(scenario)
}

func openAIResponse(scenario string) map[string]any {
	message := map[string]any{"role": "assistant", "content": "fake completion"}
	if scenario == "tool" || scenario == "approval" {
		message["content"] = nil
		message["tool_calls"] = []any{map[string]any{
			"id": "call_fake_1", "type": "function",
			"function": map[string]any{"name": "request_approval", "arguments": `{"action":"continue"}`},
		}}
	}
	return map[string]any{"id": "chatcmpl_fake_1", "object": "chat.completion", "created": 1700000000, "model": openAIModel, "choices": []any{map[string]any{"index": 0, "message": message, "finish_reason": finishReason(scenario)}}, "usage": map[string]any{"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20}}
}

func anthropicResponse(scenario string) map[string]any {
	content := []any{map[string]any{"type": "text", "text": "fake completion"}}
	stop := "end_turn"
	if scenario == "tool" || scenario == "approval" {
		content = []any{map[string]any{"type": "tool_use", "id": "toolu_fake_1", "name": "request_approval", "input": map[string]any{"action": "continue"}}}
		stop = "tool_use"
	}
	return map[string]any{"id": "msg_fake_1", "type": "message", "role": "assistant", "model": anthropicModel, "content": content, "stop_reason": stop, "stop_sequence": nil, "usage": map[string]any{"input_tokens": 12, "output_tokens": 8}}
}

func finishReason(scenario string) any {
	if scenario == "tool" || scenario == "approval" {
		return "tool_calls"
	}
	return "stop"
}

func writeStream(w http.ResponseWriter, response map[string]any, anthropic bool) {
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.WriteHeader(http.StatusOK)
	flusher, ok := w.(http.Flusher)
	if !ok {
		return
	}
	events := []map[string]any{response}
	if anthropic {
		messageStart := map[string]any{"type": "message_start", "message": response}
		messageStart["message"].(map[string]any)["usage"] = map[string]any{"input_tokens": 12, "output_tokens": 0}
		events = []map[string]any{
			messageStart,
			{"type": "content_block_start", "index": 0, "content_block": map[string]any{"type": "text", "text": ""}},
			{"type": "content_block_delta", "index": 0, "delta": map[string]any{"type": "text_delta", "text": "fake completion"}},
			{"type": "content_block_stop", "index": 0},
			{"type": "message_delta", "delta": map[string]any{"stop_reason": response["stop_reason"], "stop_sequence": nil}, "usage": map[string]any{"output_tokens": 8}},
			{"type": "message_stop"},
		}
	} else {
		// OpenAI only sends usage in the terminal chunk when requested with
		// stream_options.include_usage. This chunk is accepted by clients that
		// request usage and harmless for clients that do not.
		events = []map[string]any{
			{"id": "chatcmpl_fake_1", "object": "chat.completion.chunk", "created": 1700000000, "model": openAIModel, "choices": []any{map[string]any{"index": 0, "delta": map[string]any{"role": "assistant", "content": "fake completion"}, "finish_reason": nil}}},
			{"id": "chatcmpl_fake_1", "object": "chat.completion.chunk", "created": 1700000000, "model": openAIModel, "choices": []any{map[string]any{"index": 0, "delta": map[string]any{}, "finish_reason": "stop"}}, "usage": map[string]any{"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20}},
		}
	}
	for _, event := range events {
		payload, _ := json.Marshal(event)
		if anthropic {
			_, _ = fmt.Fprintf(w, "event: %s\ndata: %s\n\n", eventType(event), payload)
		} else {
			_, _ = fmt.Fprintf(w, "data: %s\n\n", payload)
		}
		flusher.Flush()
	}
	if !anthropic {
		_, _ = io.WriteString(w, "data: [DONE]\n\n")
		flusher.Flush()
	}
}

func eventType(event map[string]any) string {
	if value, ok := event["type"].(string); ok {
		return value
	}
	return "message"
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	payload, err := json.Marshal(value)
	if err != nil || len(payload) > MaxResponseBody {
		status = http.StatusInternalServerError
		payload = []byte(`{"error":{"type":"response_too_large"}}`)
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_, _ = w.Write(payload)
}

func wait(ctx context.Context, duration time.Duration) bool {
	if duration <= 0 {
		return true
	}
	timer := time.NewTimer(duration)
	defer timer.Stop()
	select {
	case <-timer.C:
		return true
	case <-ctx.Done():
		return false
	}
}

func boundedDuration(value string, maximum time.Duration) time.Duration {
	if value == "" {
		return 0
	}
	if duration, err := time.ParseDuration(value); err == nil {
		return clamp(duration, maximum)
	}
	if milliseconds, err := strconv.Atoi(value); err == nil {
		return clamp(time.Duration(milliseconds)*time.Millisecond, maximum)
	}
	return 0
}

func clamp(value, maximum time.Duration) time.Duration {
	if value < 0 {
		return 0
	}
	if value > maximum {
		return maximum
	}
	return value
}

// NewServer creates a bounded localhost test server. Its ErrorLog is silent so
// request bodies, authorization headers, and provider credentials are not logged.
func NewServer(config Config) *http.Server {
	return &http.Server{Handler: NewHandler(config), ReadHeaderTimeout: 2 * time.Second, ReadTimeout: MaxHold + MaxDelay + 5*time.Second, WriteTimeout: MaxHold + MaxDelay + 5*time.Second, MaxHeaderBytes: 16 << 10, ErrorLog: log.New(io.Discard, "", 0)}
}

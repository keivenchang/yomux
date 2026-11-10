package fakellm

import (
	"bufio"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestOpenAINormalAndToolResponses(t *testing.T) {
	server := httptest.NewServer(NewHandler(Config{Scenario: "normal"}))
	defer server.Close()

	response := postJSON(t, server.URL+"/v1/chat/completions", map[string]any{"model": "fake", "messages": []any{}, "stream": false})
	if response.StatusCode != http.StatusOK || response.Body["object"] != "chat.completion" {
		t.Fatalf("normal response = %#v", response)
	}
	if response.Body["model"] != openAIModel || response.Body["usage"].(map[string]any)["total_tokens"] != float64(20) {
		t.Fatalf("normal metadata = %#v", response.Body)
	}
	tool := postJSON(t, server.URL+"/scenario/tool", map[string]any{"messages": []any{}})
	choices := tool.Body["choices"].([]any)
	message := choices[0].(map[string]any)["message"].(map[string]any)
	if message["tool_calls"] == nil || message["content"] != nil {
		t.Fatalf("tool response = %#v", tool.Body)
	}
	if choices[0].(map[string]any)["finish_reason"] != "tool_calls" {
		t.Fatalf("tool finish reason = %#v", choices[0])
	}
}

func TestAnthropicStreamingResponse(t *testing.T) {
	server := httptest.NewServer(NewHandler(Config{Scenario: "normal"}))
	defer server.Close()

	request, err := http.NewRequest(http.MethodPost, server.URL+"/v1/messages", strings.NewReader(`{"model":"fake","messages":[],"stream":true}`))
	if err != nil {
		t.Fatal(err)
	}
	response, err := http.DefaultClient.Do(request)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK || response.Header.Get("Content-Type") != "text/event-stream" {
		t.Fatalf("stream headers = %d %q", response.StatusCode, response.Header.Get("Content-Type"))
	}
	scanner := bufio.NewScanner(response.Body)
	seenStart, seenDelta, seenUsage, seenStop := false, false, false, false
	for scanner.Scan() {
		line := scanner.Text()
		seenStart = seenStart || line == "event: message_start"
		seenDelta = seenDelta || line == "event: content_block_delta"
		seenUsage = seenUsage || line == "event: message_delta"
		seenStop = seenStop || line == "event: message_stop"
	}
	if err := scanner.Err(); err != nil {
		t.Fatal(err)
	}
	if !seenStart || !seenDelta || !seenUsage || !seenStop {
		t.Fatalf("missing Anthropic events: start=%v delta=%v usage=%v stop=%v", seenStart, seenDelta, seenUsage, seenStop)
	}
}

func TestOpenAIStreamingIncludesTerminalUsage(t *testing.T) {
	server := httptest.NewServer(NewHandler(Config{Scenario: "normal"}))
	defer server.Close()
	request, err := http.NewRequest(http.MethodPost, server.URL+"/v1/chat/completions", strings.NewReader(`{"stream":true}`))
	if err != nil {
		t.Fatal(err)
	}
	response, err := http.DefaultClient.Do(request)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	body, err := io.ReadAll(response.Body)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(body), `"total_tokens":20`) || !strings.Contains(string(body), "data: [DONE]") {
		t.Fatalf("stream body = %s", body)
	}
}

func TestErrorDisconnectDelayAndBounds(t *testing.T) {
	server := httptest.NewServer(NewHandler(Config{Scenario: "error"}))
	defer server.Close()
	response := postJSON(t, server.URL+"/v1/chat/completions", map[string]any{})
	if response.StatusCode != http.StatusBadGateway {
		t.Fatalf("error status = %d", response.StatusCode)
	}

	disconnect := httptest.NewServer(NewHandler(Config{Scenario: "disconnect"}))
	defer disconnect.Close()
	request, _ := http.NewRequest(http.MethodPost, disconnect.URL+"/v1/chat/completions", strings.NewReader(`{}`))
	client := &http.Client{Timeout: time.Second}
	if response, err := client.Do(request); err == nil {
		response.Body.Close()
		t.Fatal("disconnect returned an HTTP response")
	}

	limited := httptest.NewServer(NewHandler(Config{Scenario: "normal"}))
	defer limited.Close()
	large := strings.Repeat("x", MaxRequestBody+1)
	request, _ = http.NewRequest(http.MethodPost, limited.URL+"/v1/chat/completions", strings.NewReader(large))
	largeResponse, err := http.DefaultClient.Do(request)
	if err != nil {
		t.Fatal(err)
	}
	largeResponse.Body.Close()
	if largeResponse.StatusCode != http.StatusRequestEntityTooLarge {
		t.Fatalf("large body status = %d", largeResponse.StatusCode)
	}

	started := time.Now()
	delayed := httptest.NewServer(NewHandler(Config{Scenario: "delay", Delay: 15 * time.Millisecond}))
	defer delayed.Close()
	postJSON(t, delayed.URL+"/v1/chat/completions", map[string]any{})
	if elapsed := time.Since(started); elapsed < 10*time.Millisecond {
		t.Fatalf("delay elapsed = %s", elapsed)
	}
}

func TestConfigFromEnvIsBounded(t *testing.T) {
	values := map[string]string{"FAKE_LLM_SCENARIO": "tool", "FAKE_LLM_DELAY": "999h", "FAKE_LLM_HOLD": "-1s"}
	config := ConfigFromEnv(func(name string) string { return values[name] })
	if config.Scenario != "tool" || config.Delay != MaxDelay || config.Hold != 0 {
		t.Fatalf("config = %#v", config)
	}
}

type jsonResponse struct {
	StatusCode int
	Body       map[string]any
}

func postJSON(t *testing.T, url string, value any) jsonResponse {
	t.Helper()
	payload, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	request, err := http.NewRequestWithContext(context.Background(), http.MethodPost, url, strings.NewReader(string(payload)))
	if err != nil {
		t.Fatal(err)
	}
	response, err := http.DefaultClient.Do(request)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	body, err := io.ReadAll(response.Body)
	if err != nil {
		t.Fatal(err)
	}
	var decoded map[string]any
	if err := json.Unmarshal(body, &decoded); err != nil {
		t.Fatalf("response body %q: %v", body, err)
	}
	return jsonResponse{StatusCode: response.StatusCode, Body: decoded}
}

func TestStreamingPreservesToolScenario(t *testing.T) {
	for _, path := range []string{"/v1/chat/completions", "/v1/messages"} {
		for _, scenario := range []string{"tool", "approval"} {
			t.Run(path+"/"+scenario, func(t *testing.T) {
				response := httptest.NewRecorder()
				NewHandler(Config{Scenario: scenario}).ServeHTTP(response, httptest.NewRequest(http.MethodPost, path, strings.NewReader(`{"stream":true}`)))
				body := response.Body.String()
				if !strings.Contains(body, `request_approval`) || !strings.Contains(body, `continue`) || strings.Contains(body, `fake completion`) {
					t.Fatalf("tool scenario stream lost tool payload: %s", body)
				}
				if path == "/v1/chat/completions" && !strings.Contains(body, `"finish_reason":"tool_calls"`) {
					t.Fatalf("missing streamed tool finish reason: %s", body)
				}
				if path == "/v1/messages" && (!strings.Contains(body, `"type":"input_json_delta"`) || !strings.Contains(body, `"stop_reason":"tool_use"`)) {
					t.Fatalf("missing streamed tool input or stop reason: %s", body)
				}
			})
		}
	}
}

func TestMillisecondDurationBoundsBeforeMultiplication(t *testing.T) {
	if got := boundedDuration("9223372036855", MaxDelay); got != MaxDelay {
		t.Fatalf("overflowed duration = %v, want %v", got, MaxDelay)
	}
}

func TestStreamDoesNotMutateResponse(t *testing.T) {
	for _, anthropic := range []bool{false, true} {
		response := responseFor("tool", anthropic)
		before, err := json.Marshal(response)
		if err != nil {
			t.Fatal(err)
		}
		writeStream(httptest.NewRecorder(), response, anthropic)
		after, err := json.Marshal(response)
		if err != nil || string(before) != string(after) {
			t.Fatalf("stream mutated canonical provider response; anthropic=%v err=%v", anthropic, err)
		}
	}
}

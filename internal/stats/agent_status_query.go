package stats

import (
	"context"
	"fmt"
	"sort"
	"time"
)

const AgentStatusTokenRateWindow = 60 * time.Second

// AgentStatusActiveCadence is the status collector's watched cadence. A
// committed sample superseded by a later unavailable span is not current
// status, even if the old row is still present in the retained database.
const AgentStatusActiveCadence = 10 * time.Second

type AgentStatusQuery struct {
	Now        time.Time
	RateWindow time.Duration
}

type AgentStatusFreshness struct {
	ObservedAt float64 `json:"observed_at"`
	AgeSeconds float64 `json:"age_seconds"`
}

type AgentStatusQueryUnavailable struct {
	Reason string `json:"reason"`
}

type AgentTokenRates struct {
	WindowSeconds   float64                  `json:"window_seconds"`
	InputPerSecond  float64                  `json:"input_per_second"`
	OutputPerSecond float64                  `json:"output_per_second"`
	CachePerSecond  float64                  `json:"cache_per_second"`
	TotalPerSecond  float64                  `json:"total_per_second"`
	ProviderModels  []AgentProviderModelRate `json:"provider_models,omitempty"`
}

type AgentProviderModelRate struct {
	Provider        string  `json:"provider"`
	Model           string  `json:"model"`
	InputPerSecond  float64 `json:"input_per_second"`
	OutputPerSecond float64 `json:"output_per_second"`
	CachePerSecond  float64 `json:"cache_per_second"`
	TotalPerSecond  float64 `json:"total_per_second"`
}

type AgentStatusSnapshot struct {
	Available        bool                          `json:"available"`
	Freshness        *AgentStatusFreshness         `json:"freshness"`
	SourceGeneration int64                         `json:"source_generation"`
	Payload          map[string]any                `json:"payload,omitempty"`
	Counts           map[string]any                `json:"counts,omitempty"`
	Agents           []any                         `json:"agents,omitempty"`
	TokenRates       AgentTokenRates               `json:"token_rates"`
	TokenCollection  *AgentTokenCollectionSnapshot `json:"token_collection,omitempty"`
	Unavailable      *AgentStatusQueryUnavailable  `json:"unavailable,omitempty"`
}

type AgentStatusLifecycleState struct {
	ID                   string
	ProcessInstanceID    string
	ObservedAt           time.Time
	WorkingStoppedAt     time.Time
	TransitionID         string
	CooldownAttentionKey string
	CompletionSettled    bool
	AttentionSignature   string
	AttentionKey         string
	AttentionSettled     bool
}

// ReadAgentStatus is the stats-owner read boundary for the public status
// projection. HTTP callers should receive this through StatsService rather
// than opening the SQLite database in a request handler.
func ReadAgentStatus(ctx context.Context, path string, query AgentStatusQuery) (AgentStatusSnapshot, error) {
	database, err := OpenReadOnly(ctx, path)
	if err != nil {
		return AgentStatusSnapshot{}, err
	}
	defer database.Close()
	return database.QueryAgentStatus(ctx, query)
}

// ReadRecentAgentStatusLifecycles restores active completion identity and
// settled-completion suppression from the last committed status row. The
// public status projection may already be stale while a yellow completion is
// still retained. Startup recovery validates it against the live process
// inventory; transient unavailable spans must not erase settled identities.
// This does not weaken the HTTP freshness contract.
func ReadRecentAgentStatusLifecycles(ctx context.Context, path string, now time.Time) ([]AgentStatusLifecycleState, error) {
	database, err := OpenReadOnly(ctx, path)
	if err != nil {
		return nil, err
	}
	defer database.Close()
	rows, err := database.reader.Rows(ctx, `SELECT observed_at, payload_json FROM observations
WHERE family = 'agent_status' ORDER BY observed_at DESC, event_id DESC LIMIT 1`)
	if err != nil {
		return nil, fmt.Errorf("read recent agent status lifecycles: %w", err)
	}
	if len(rows) == 0 {
		return nil, nil
	}
	if len(rows[0]) != 2 {
		return nil, fmt.Errorf("recent agent status row has %d columns", len(rows[0]))
	}
	observedAtSeconds, err := number(rows[0][0])
	if err != nil {
		return nil, fmt.Errorf("recent agent status timestamp: %w", err)
	}
	observedAt := time.Unix(int64(observedAtSeconds), int64((observedAtSeconds-float64(int64(observedAtSeconds)))*1e9))
	payload, err := decodePayload(rows[0][1])
	if err != nil {
		return nil, fmt.Errorf("recent agent status payload: %w", err)
	}
	agents, _ := payload["agents"].([]any)
	lifecycles := make([]AgentStatusLifecycleState, 0, len(agents))
	for _, value := range agents {
		row, ok := value.(map[string]any)
		if !ok {
			continue
		}
		id := rowString(row["id"])
		if id == "" {
			continue
		}
		completionSettled, _ := row["completion_settled"].(bool)
		processInstanceID := rowString(row["process_instance_id"])
		lifecycle := AgentStatusLifecycleState{ID: id, ProcessInstanceID: processInstanceID, ObservedAt: observedAt, CompletionSettled: completionSettled,
			AttentionSignature: rowString(row["attention_signature"]), AttentionKey: rowString(row["attention_key"])}
		lifecycle.AttentionSettled, _ = row["attention_acknowledged"].(bool)
		if stoppedSeconds, stoppedErr := number(row["working_stopped_ts"]); stoppedErr == nil && stoppedSeconds > 0 {
			stoppedAt := time.Unix(int64(stoppedSeconds), int64((stoppedSeconds-float64(int64(stoppedSeconds)))*1e9))
			if !now.Before(stoppedAt) {
				lifecycle.WorkingStoppedAt = stoppedAt
				lifecycle.TransitionID = rowString(row["transition_id"])
				lifecycle.CooldownAttentionKey = rowString(row["cooldown_attention_key"])
			}
		}
		if lifecycle.AttentionKey != "" || lifecycle.CompletionSettled || (!lifecycle.WorkingStoppedAt.IsZero() && lifecycle.CooldownAttentionKey != "") {
			lifecycles = append(lifecycles, lifecycle)
		}

	}
	return lifecycles, nil
}

// QueryAgentStatus returns the latest committed agent_status payload and a
// bounded token rate derived from the same SQLite read snapshot.
func (d *Database) QueryAgentStatus(ctx context.Context, query AgentStatusQuery) (AgentStatusSnapshot, error) {
	now := query.Now
	if now.IsZero() {
		now = time.Now()
	}
	window := query.RateWindow
	if window <= 0 {
		window = AgentStatusTokenRateWindow
	}
	if window > 24*time.Hour {
		return AgentStatusSnapshot{}, fmt.Errorf("token rate window exceeds 24 hours")
	}
	result := AgentStatusSnapshot{SourceGeneration: d.Header.SourceGeneration, TokenRates: AgentTokenRates{WindowSeconds: window.Seconds()}}
	nowSeconds := float64(now.UnixNano()) / 1e9
	var err error
	result.TokenRates, err = d.agentTokenRates(ctx, nowSeconds-window.Seconds(), nowSeconds, window)
	if err != nil {
		return AgentStatusSnapshot{}, err
	}
	rows, err := d.reader.Rows(ctx, `SELECT observed_at, source_id, epoch_id, payload_json
FROM observations WHERE family = 'agent_status'
ORDER BY observed_at DESC, event_id DESC LIMIT 1`)
	if err != nil {
		return AgentStatusSnapshot{}, fmt.Errorf("read latest agent status: %w", err)
	}
	if len(rows) == 0 {
		reason, reasonErr := d.latestUnavailableReason(ctx, "agent_status", nowSeconds)
		if reasonErr != nil {
			return AgentStatusSnapshot{}, reasonErr
		}
		if reason == "" {
			reason = "no-agent-status-observation"
		}
		result.Unavailable = &AgentStatusQueryUnavailable{Reason: reason}
		return result, nil
	}
	if len(rows[0]) != 4 {
		return AgentStatusSnapshot{}, fmt.Errorf("latest agent status row has %d columns", len(rows[0]))
	}
	observedAt, err := number(rows[0][0])
	if err != nil {
		return AgentStatusSnapshot{}, fmt.Errorf("latest agent status timestamp: %w", err)
	}
	payload, err := decodePayload(rows[0][3])
	if err != nil {
		return AgentStatusSnapshot{}, fmt.Errorf("latest agent status payload: %w", err)
	}
	unavailableStart, unavailableReason, unavailableErr := d.latestUnavailableSpan(ctx, "agent_status", nowSeconds)
	if unavailableErr != nil {
		return AgentStatusSnapshot{}, unavailableErr
	}
	if unavailableStart > observedAt {
		if unavailableReason == "" {
			unavailableReason = "agent-status-unavailable"
		}
		result.Unavailable = &AgentStatusQueryUnavailable{Reason: unavailableReason}
		return result, nil
	}
	result.Available = true
	result.Freshness = &AgentStatusFreshness{ObservedAt: observedAt, AgeSeconds: now.Sub(time.Unix(int64(observedAt), int64((observedAt-float64(int64(observedAt)))*1e9))).Seconds()}
	if result.Freshness.AgeSeconds < 0 {
		result.Freshness.AgeSeconds = 0
	}
	freshnessCadence, cadenceErr := d.agentStatusCadence(ctx, rowString(rows[0][1]), rowString(rows[0][2]), observedAt)
	if cadenceErr != nil {
		return AgentStatusSnapshot{}, cadenceErr
	}
	if result.Freshness.AgeSeconds > freshnessCadence {
		result.Available = false
		result.Unavailable = &AgentStatusQueryUnavailable{Reason: "agent-status-stale"}
		return result, nil
	}
	result.Payload = payload
	if value, ok := payload["counts"].(map[string]any); ok {
		result.Counts = value
	}
	if value, ok := payload["agents"].([]any); ok {
		result.Agents = value
	}
	return result, nil
}

func (d *Database) agentStatusCadence(ctx context.Context, sourceID, epochID string, observedAt float64) (float64, error) {
	rows, err := d.reader.Rows(ctx, `SELECT native_cadence_seconds FROM coverage_epochs
WHERE family = 'agent_status' AND source_id = ? AND epoch_id = ?
  AND started_at <= ? AND (ended_at IS NULL OR ended_at >= ?)
ORDER BY started_at DESC LIMIT 1`, sourceID, epochID, observedAt, observedAt)
	if err != nil {
		return 0, fmt.Errorf("read agent status cadence: %w", err)
	}
	if len(rows) == 0 {
		return AgentStatusActiveCadence.Seconds(), nil
	}
	if len(rows[0]) != 1 {
		return 0, fmt.Errorf("agent status cadence row has %d columns", len(rows[0]))
	}
	cadence, err := number(rows[0][0])
	if err != nil || cadence <= 0 {
		return 0, fmt.Errorf("agent status cadence is invalid")
	}
	return cadence, nil
}

func (d *Database) latestUnavailableReason(ctx context.Context, family string, at float64) (string, error) {
	rows, err := d.reader.Rows(ctx, `SELECT reason FROM unavailable_spans
WHERE family = ? AND started_at <= ? ORDER BY ended_at DESC, started_at DESC LIMIT 1`, family, at)
	if err != nil {
		return "", fmt.Errorf("read unavailable %s status: %w", family, err)
	}
	if len(rows) == 0 || len(rows[0]) != 1 {
		return "", nil
	}
	return rowString(rows[0][0]), nil
}

func (d *Database) latestUnavailableSpan(ctx context.Context, family string, at float64) (float64, string, error) {
	rows, err := d.reader.Rows(ctx, `SELECT started_at, reason FROM unavailable_spans
WHERE family = ? AND started_at <= ? ORDER BY started_at DESC, ended_at DESC LIMIT 1`, family, at)
	if err != nil {
		return 0, "", fmt.Errorf("read unavailable %s span: %w", family, err)
	}
	if len(rows) == 0 || len(rows[0]) != 2 {
		return 0, "", nil
	}
	startedAt, err := number(rows[0][0])
	if err != nil {
		return 0, "", fmt.Errorf("read unavailable %s timestamp: %w", family, err)
	}
	return startedAt, rowString(rows[0][1]), nil
}

func (d *Database) agentTokenRates(ctx context.Context, start, end float64, window time.Duration) (AgentTokenRates, error) {
	rates := AgentTokenRates{WindowSeconds: window.Seconds()}
	byProviderModel := map[string]*AgentProviderModelRate{}
	rows, err := d.reader.Rows(ctx, `SELECT direction, cache_role, payload_json
FROM usage_atoms WHERE unit = 'tokens' AND observed_at >= ? AND observed_at < ?`, start, end)
	if err != nil {
		return AgentTokenRates{}, fmt.Errorf("read agent token rates: %w", err)
	}
	for _, row := range rows {
		if len(row) != 3 {
			return AgentTokenRates{}, fmt.Errorf("agent token rate row has %d columns", len(row))
		}
		payload, err := decodePayload(row[2])
		if err != nil {
			return AgentTokenRates{}, fmt.Errorf("agent token rate payload: %w", err)
		}
		quantity, err := number(payload["quantity"])
		if err != nil || quantity < 0 {
			return AgentTokenRates{}, fmt.Errorf("agent token rate quantity is invalid")
		}
		rates.TotalPerSecond += quantity / window.Seconds()
		provider, _ := payload["provider"].(string)
		model, _ := payload["model"].(string)
		key := provider + "\x00" + model
		item := byProviderModel[key]
		if item == nil {
			item = &AgentProviderModelRate{Provider: provider, Model: model}
			byProviderModel[key] = item
		}
		item.TotalPerSecond += quantity / window.Seconds()
		if rowString(row[1]) != "none" {
			rates.CachePerSecond += quantity / window.Seconds()
			item.CachePerSecond += quantity / window.Seconds()
		} else if rowString(row[0]) == "input" {
			rates.InputPerSecond += quantity / window.Seconds()
			item.InputPerSecond += quantity / window.Seconds()
		} else if rowString(row[0]) == "output" {
			rates.OutputPerSecond += quantity / window.Seconds()
			item.OutputPerSecond += quantity / window.Seconds()
		}
	}
	for _, item := range byProviderModel {
		rates.ProviderModels = append(rates.ProviderModels, *item)
	}
	sort.Slice(rates.ProviderModels, func(i, j int) bool {
		if rates.ProviderModels[i].Provider != rates.ProviderModels[j].Provider {
			return rates.ProviderModels[i].Provider < rates.ProviderModels[j].Provider
		}
		return rates.ProviderModels[i].Model < rates.ProviderModels[j].Model
	})
	return rates, nil
}

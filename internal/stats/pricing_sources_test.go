package stats

import (
	"context"
	"errors"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

type pricingRoundTripper func(*http.Request) (*http.Response, error)

func (roundTrip pricingRoundTripper) RoundTrip(request *http.Request) (*http.Response, error) {
	return roundTrip(request)
}

func TestFetchPricingSourceSendsConditionalValidatorsWithoutNetwork(t *testing.T) {
	var sawETag, sawModified string
	client := &http.Client{Transport: pricingRoundTripper(func(request *http.Request) (*http.Response, error) {
		sawETag = request.Header.Get("If-None-Match")
		sawModified = request.Header.Get("If-Modified-Since")
		return &http.Response{StatusCode: http.StatusNotModified, Header: http.Header{"Etag": []string{`"fixture-v2"`}}, Body: io.NopCloser(strings.NewReader("")), Request: request}, nil
	})}
	result, err := fetchPricingSourceWithClient(context.Background(), pricingOpenAIURL, map[string]string{"If-None-Match": `"fixture-v1"`, "If-Modified-Since": "Wed, 23 Sep 2026 12:00:00 GMT"}, client)
	if err != nil {
		t.Fatal(err)
	}
	if result.Status != http.StatusNotModified || result.ETag != `"fixture-v2"` || sawETag != `"fixture-v1"` || sawModified != "Wed, 23 Sep 2026 12:00:00 GMT" {
		t.Fatalf("conditional request/result: status=%d etag=%q request etag=%q modified=%q", result.Status, result.ETag, sawETag, sawModified)
	}
}

func TestParseProviderPricingTableUsesSyntheticFixture(t *testing.T) {
	body := []byte(`<html><script>const fake = "<tr><td>wrong</td></tr>";</script><table><tr><th>Model</th><th>Input</th><th>Cached input</th><th>Output</th><th>5m cache writes</th><th>1h cache writes</th><th>Profile</th><th>Service tier</th></tr><tr><td>claude &amp; sonnet</td><td>$3.00 / MTok</td><td>$0.30</td><td>$15</td><td>$3.75</td><td>-</td><td>long</td><td>priority</td></tr></table></html>`)
	models, err := parseProviderPricingTable(body, "anthropic", pricingAnthropicURL)
	if err != nil {
		t.Fatal(err)
	}
	if len(models) != 1 {
		t.Fatalf("models = %d, want one parsed fixture model", len(models))
	}
	model := models[0]
	if model.Model != "claude & sonnet" || model.Aliases[1] != "claude-sonnet" || model.Source.Kind != "official" {
		t.Fatalf("model identity/source = %+v", model)
	}
	if len(model.Rates) != 4 || model.Rates[0].USD != "3" || model.Rates[1].USD != "15" || model.Rates[2].CacheRole != "read" || model.Rates[3].CacheRole != "write_5m" || model.Rates[3].Profile != "long" || model.Rates[3].ServiceTier != "priority" {
		t.Fatalf("normalized rates = %+v", model.Rates)
	}
}

func TestPricingRefreshUsesSyntheticSourcesAndRestoresDurableCatalog(t *testing.T) {
	dir := t.TempDir()
	seedPath := filepath.Join(dir, "seed.json")
	seed := `{"schema_version":1,"catalog_revision":4,"models":[{"provider":"openai","model":"fixture-seed","aliases":["fixture-seed"],"rates":[{"direction":"input","modality":"text","cache_role":"none","unit":"tokens","scale":1000000,"usd":"1.00","effective_from":"1970-01-01T00:00:00Z"}]}]}`
	if err := os.WriteFile(seedPath, []byte(seed), 0600); err != nil {
		t.Fatal(err)
	}
	catalog, err := LoadPricingCatalog(seedPath)
	if err != nil {
		t.Fatal(err)
	}
	cachePath := filepath.Join(dir, "state", "pricing-refresh.json")
	catalog.SetRefreshCachePath(cachePath)
	adapters := []pricingSourceAdapter{{name: "fixture-official", url: pricingOpenAIURL, kind: "official", parserVersion: "fixture-1", parse: func([]byte) ([]PricingModel, error) {
		return []PricingModel{{Provider: "openai", Model: "fixture-live", Aliases: []string{"fixture-live"}, Source: PricingSource{URL: pricingOpenAIURL, Kind: "official"}, Rates: []PricingRate{pricingTableRate("input", "none", "2.50", "default", "default")}}}, nil
	}}}
	fetch := func(_ context.Context, sourceURL string, headers map[string]string) (pricingFetchResponse, error) {
		if sourceURL != pricingOpenAIURL {
			t.Fatalf("unexpected source URL %q", sourceURL)
		}
		if len(headers) != 0 {
			t.Fatalf("unexpected initial conditional headers: %+v", headers)
		}
		return pricingFetchResponse{Status: http.StatusOK, ETag: `"fixture-v1"`, LastModified: "Wed, 23 Sep 2026 12:00:00 GMT", Body: []byte("synthetic source response")}, nil
	}
	if err := catalog.refreshFromSources(context.Background(), adapters, fetch); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(cachePath); err != nil {
		t.Fatalf("refresh did not persist the private catalog cache: %v", err)
	}
	if got := catalog.Resolve("openai", "fixture-live", "input", "text", "none", "tokens", "", "", "9999-12-31T23:59:59Z"); got == nil || got.Rate.USD != "2.50" || got.CatalogRevision != 5 {
		t.Fatalf("refreshed rate = %+v", got)
	}
	reopened, err := LoadPricingCatalog(seedPath)
	if err != nil {
		t.Fatal(err)
	}
	reopened.SetRefreshCachePath(cachePath)
	if err := reopened.RestoreRefreshCache(); err != nil {
		t.Fatal(err)
	}
	if check := reopened.sourceChecks[pricingOpenAIURL]; check.ETag != `"fixture-v1"` || check.LastModified != "Wed, 23 Sep 2026 12:00:00 GMT" || check.Digest == "" {
		t.Fatalf("restored source validators = %+v", check)
	}
	if got := reopened.Resolve("openai", "fixture-live", "input", "text", "none", "tokens", "", "", "9999-12-31T23:59:59Z"); got == nil || got.Rate.USD != "2.50" {
		t.Fatalf("restored rate = %+v", got)
	}
	info, err := os.Stat(cachePath)
	if err != nil {
		t.Fatal(err)
	}
	if info.Mode().Perm() != 0600 {
		t.Fatalf("cache permissions = %o, want 600", info.Mode().Perm())
	}
	checks := catalog.sourceChecks
	if checks[pricingOpenAIURL].ETag != `"fixture-v1"` || checks[pricingOpenAIURL].ParserVersion != "fixture-1" || checks[pricingOpenAIURL].Digest == "" {
		t.Fatalf("source check evidence = %+v", checks[pricingOpenAIURL])
	}
	unchangedFetch := func(_ context.Context, sourceURL string, headers map[string]string) (pricingFetchResponse, error) {
		if sourceURL != pricingOpenAIURL || headers["If-None-Match"] != `"fixture-v1"` || headers["If-Modified-Since"] != "Wed, 23 Sep 2026 12:00:00 GMT" {
			t.Fatalf("304 request = source %q headers %+v", sourceURL, headers)
		}
		return pricingFetchResponse{Status: http.StatusNotModified}, nil
	}
	if err := reopened.refreshFromSources(context.Background(), adapters, unchangedFetch); err != nil {
		t.Fatal(err)
	}
	status, warnings := reopened.RefreshResult()
	if status != "unchanged" || len(warnings) != 0 || reopened.CatalogRevision != 5 {
		t.Fatalf("304 refresh status=%q warnings=%v revision=%d", status, warnings, reopened.CatalogRevision)
	}
}

func TestPricingRefreshFailureSurvivesRestoreWithoutReplacingAcceptedCatalog(t *testing.T) {
	dir := t.TempDir()
	seedPath := filepath.Join(dir, "seed.json")
	seed := `{"schema_version":1,"catalog_revision":4,"models":[{"provider":"openai","model":"fixture-seed","aliases":["fixture-seed"],"rates":[{"direction":"input","modality":"text","cache_role":"none","unit":"tokens","scale":1000000,"usd":"1.00","effective_from":"1970-01-01T00:00:00Z"}]}]}`
	if err := os.WriteFile(seedPath, []byte(seed), 0600); err != nil {
		t.Fatal(err)
	}
	cachePath := filepath.Join(dir, "state", "pricing-refresh.json")
	newCatalog := func() *PricingCatalog {
		catalog, err := LoadPricingCatalog(seedPath)
		if err != nil {
			t.Fatal(err)
		}
		catalog.SetRefreshCachePath(cachePath)
		return catalog
	}
	adapter := pricingSourceAdapter{
		name: "official-fixture", url: pricingOpenAIURL, kind: "official", parserVersion: "fixture-1",
		parse: func([]byte) ([]PricingModel, error) {
			return []PricingModel{{
				Provider: "openai", Model: "accepted-model", Aliases: []string{"accepted-model"},
				Source: PricingSource{URL: pricingOpenAIURL, Kind: "official"},
				Rates:  []PricingRate{pricingTableRate("input", "none", "2.50", "default", "default")},
			}}, nil
		},
	}
	acceptedFetch := func(context.Context, string, map[string]string) (pricingFetchResponse, error) {
		return pricingFetchResponse{Status: http.StatusOK, ETag: `"accepted-v1"`, Body: []byte("synthetic accepted response")}, nil
	}
	catalog := newCatalog()
	if err := catalog.refreshFromSources(context.Background(), []pricingSourceAdapter{adapter}, acceptedFetch); err != nil {
		t.Fatal(err)
	}
	acceptedRevision := catalog.CatalogRevision
	failingFetch := func(context.Context, string, map[string]string) (pricingFetchResponse, error) {
		return pricingFetchResponse{}, errors.New("synthetic source outage")
	}
	if err := catalog.refreshFromSources(context.Background(), []pricingSourceAdapter{adapter}, failingFetch); err == nil {
		t.Fatal("failed synthetic refresh unexpectedly succeeded")
	}
	if catalog.CatalogRevision != acceptedRevision || catalog.Resolve("openai", "accepted-model", "input", "text", "none", "tokens", "", "", "9999-12-31T23:59:59Z") == nil {
		t.Fatal("failed refresh changed the accepted in-memory catalog")
	}
	reopened := newCatalog()
	if err := reopened.RestoreRefreshCache(); err != nil {
		t.Fatal(err)
	}
	status := reopened.PublicPayload()["status"].(map[string]any)
	if status["state"] != "review-needed" || reopened.CatalogRevision != acceptedRevision || reopened.Resolve("openai", "accepted-model", "input", "text", "none", "tokens", "", "", "9999-12-31T23:59:59Z") == nil {
		t.Fatalf("failed-refresh restart state=%#v revision=%d", status, reopened.CatalogRevision)
	}
	if check := reopened.sourceChecks[pricingOpenAIURL]; check.ETag != `"accepted-v1"` {
		t.Fatalf("failure replaced accepted source checks: %+v", check)
	}
	if err := reopened.AcknowledgePricingInvalidation(); err != nil {
		t.Fatal(err)
	}
	afterAcknowledgement := newCatalog()
	if err := afterAcknowledgement.RestoreRefreshCache(); err != nil {
		t.Fatal(err)
	}
	refreshStatus, warnings := afterAcknowledgement.RefreshResult()
	if refreshStatus != "failed" || len(warnings) == 0 || afterAcknowledgement.PricingInvalidationPending() {
		t.Fatalf("acknowledgement lost provider failure or retained pending work: %s %v", refreshStatus, warnings)
	}
	unchangedFetch := func(context.Context, string, map[string]string) (pricingFetchResponse, error) {
		return pricingFetchResponse{Status: http.StatusNotModified}, nil
	}
	if err := reopened.refreshFromSources(context.Background(), []pricingSourceAdapter{adapter}, unchangedFetch); err != nil {
		t.Fatal(err)
	}
	recovered := newCatalog()
	if err := recovered.RestoreRefreshCache(); err != nil {
		t.Fatal(err)
	}
	status = recovered.PublicPayload()["status"].(map[string]any)
	if status["state"] != "fresh" {
		t.Fatalf("successful unchanged refresh did not clear durable failure: %#v", status)
	}
}

func TestPricingRefreshFailureKeepsSeedOnlyStatus(t *testing.T) {
	dir := t.TempDir()
	seedPath := filepath.Join(dir, "seed.json")
	seed := `{"schema_version":1,"catalog_revision":4,"models":[{"provider":"openai","model":"fixture-seed","aliases":["fixture-seed"],"rates":[{"direction":"input","modality":"text","cache_role":"none","unit":"tokens","scale":1000000,"usd":"1.00","effective_from":"1970-01-01T00:00:00Z"}]}]}`
	if err := os.WriteFile(seedPath, []byte(seed), 0600); err != nil {
		t.Fatal(err)
	}
	cachePath := filepath.Join(dir, "state", "pricing-refresh.json")
	catalog, err := LoadPricingCatalog(seedPath)
	if err != nil {
		t.Fatal(err)
	}
	catalog.SetRefreshCachePath(cachePath)
	adapter := pricingSourceAdapter{name: "official-fixture", url: pricingOpenAIURL, kind: "official", parse: func([]byte) ([]PricingModel, error) { return nil, nil }}
	fetch := func(context.Context, string, map[string]string) (pricingFetchResponse, error) {
		return pricingFetchResponse{}, errors.New("synthetic source outage")
	}
	if err := catalog.refreshFromSources(context.Background(), []pricingSourceAdapter{adapter}, fetch); err == nil {
		t.Fatal("failed synthetic refresh unexpectedly succeeded")
	}
	reopened, err := LoadPricingCatalog(seedPath)
	if err != nil {
		t.Fatal(err)
	}
	reopened.SetRefreshCachePath(cachePath)
	if err := reopened.RestoreRefreshCache(); err != nil {
		t.Fatal(err)
	}
	status := reopened.PublicPayload()["status"].(map[string]any)
	if status["state"] != "seed-only" || !reopened.refreshFailed || reopened.CatalogRevision != 4 {
		t.Fatalf("seed-only failure status=%#v failed=%t revision=%d", status, reopened.refreshFailed, reopened.CatalogRevision)
	}
}

func TestPricingFailureMarkerPersistenceErrorPreservesActiveCatalog(t *testing.T) {
	catalog := &PricingCatalog{SchemaVersion: 1, CatalogRevision: 2, Models: []PricingModel{{
		Provider: "openai", Model: "accepted-model", Aliases: []string{"accepted-model"},
		Source: PricingSource{Kind: "official", URL: pricingOpenAIURL},
		Rates:  []PricingRate{pricingTableRate("input", "none", "2.50", "default", "default")},
	}}}
	cachePath := t.TempDir()
	catalog.SetRefreshCachePath(cachePath)
	adapter := pricingSourceAdapter{name: "official-fixture", url: pricingOpenAIURL, kind: "official", parse: func([]byte) ([]PricingModel, error) { return nil, nil }}
	fetch := func(context.Context, string, map[string]string) (pricingFetchResponse, error) {
		return pricingFetchResponse{}, errors.New("synthetic source outage")
	}
	err := catalog.refreshFromSources(context.Background(), []pricingSourceAdapter{adapter}, fetch)
	if err == nil || !strings.Contains(err.Error(), "failure state persistence") {
		t.Fatalf("refresh error = %v; want surfaced failure-marker persistence error", err)
	}
	if catalog.CatalogRevision != 2 || len(catalog.Models) != 1 || catalog.Models[0].Rates[0].USD != "2.50" {
		t.Fatalf("failure marker persistence mutated active catalog: revision=%d models=%+v", catalog.CatalogRevision, catalog.Models)
	}
}

func TestPricingRefreshCacheOlderThanCurrentSeedIsIgnored(t *testing.T) {
	dir := t.TempDir()
	seedPath := filepath.Join(dir, "seed.json")
	cachePath := filepath.Join(dir, "cache.json")
	seed := `{"schema_version":1,"catalog_revision":8,"models":[{"provider":"openai","model":"new-seed","aliases":["new-seed"],"rates":[{"direction":"input","modality":"text","cache_role":"none","unit":"tokens","scale":1000000,"usd":"1","effective_from":"1970-01-01T00:00:00Z"}]}]}`
	oldCache := `{"schema_version":1,"catalog_revision":7,"models":[{"provider":"openai","model":"old-refresh","aliases":["old-refresh"],"rates":[{"direction":"input","modality":"text","cache_role":"none","unit":"tokens","scale":1000000,"usd":"2","effective_from":"1970-01-01T00:00:00Z"}]}]}`
	if err := os.WriteFile(seedPath, []byte(seed), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(cachePath, []byte(oldCache), 0600); err != nil {
		t.Fatal(err)
	}
	catalog, err := LoadPricingCatalog(seedPath)
	if err != nil {
		t.Fatal(err)
	}
	catalog.SetRefreshCachePath(cachePath)
	if err := catalog.RestoreRefreshCache(); err != nil {
		t.Fatal(err)
	}
	if catalog.Resolve("openai", "new-seed", "input", "text", "none", "tokens", "", "", "9999-12-31T23:59:59Z") == nil || catalog.Resolve("openai", "old-refresh", "input", "text", "none", "tokens", "", "", "9999-12-31T23:59:59Z") != nil {
		t.Fatalf("stale refresh cache replaced newer seed: %+v", catalog.PublicPayload())
	}
}

func TestPricingRefreshDueUsesOnlyCheckedNonSeedSources(t *testing.T) {
	now := time.Date(2026, 9, 23, 12, 0, 0, 0, time.UTC)
	seed := &PricingCatalog{Models: []PricingModel{{Source: PricingSource{Kind: "seed", CheckedAt: now.Format(time.RFC3339)}}}}
	if !seed.RefreshDue(now) {
		t.Fatal("seed-only catalog should be due for remote refresh")
	}
	fresh := &PricingCatalog{Models: []PricingModel{{Source: PricingSource{Kind: "official", CheckedAt: now.Add(-time.Hour).Format(time.RFC3339)}}}}
	if fresh.RefreshDue(now) {
		t.Fatal("recently checked official catalog should not be due")
	}
	stale := &PricingCatalog{Models: []PricingModel{{Source: PricingSource{Kind: "official", CheckedAt: now.Add(-31 * 24 * time.Hour).Format(time.RFC3339)}}}}
	if !stale.RefreshDue(now) {
		t.Fatal("stale official catalog should be due")
	}
}

func TestPricingRefreshRejectsCorroborationConflictWithoutReplacingCatalog(t *testing.T) {
	seed := &PricingCatalog{SchemaVersion: 1, CatalogRevision: 2, Models: []PricingModel{{Provider: "openai", Model: "fixture-live", Aliases: []string{"fixture-live"}, Source: PricingSource{Kind: "official"}, Rates: []PricingRate{pricingTableRate("input", "none", "1.00", "default", "default")}}}}
	adapters := []pricingSourceAdapter{
		{name: "official", url: pricingOpenAIURL, kind: "official", parse: func([]byte) ([]PricingModel, error) { return clonePricingModels(seed.Models), nil }},
		{name: "corroboration", url: pricingOpenRouterURL, kind: "corroboration", parse: func([]byte) ([]PricingModel, error) {
			model := clonePricingModels(seed.Models)
			model[0].Rates[0].USD = "2.00"
			return model, nil
		}},
	}
	fetch := func(context.Context, string, map[string]string) (pricingFetchResponse, error) {
		return pricingFetchResponse{Status: http.StatusOK, Body: []byte("fixture")}, nil
	}
	if err := seed.refreshFromSources(context.Background(), adapters, fetch); err == nil || !strings.Contains(err.Error(), "disagreement") {
		t.Fatalf("conflicting corroboration error = %v", err)
	}
	if seed.CatalogRevision != 2 || seed.Models[0].Rates[0].USD != "1.00" || !seed.refreshFailed {
		t.Fatalf("conflict mutated active pricing: revision=%d models=%+v failed=%t", seed.CatalogRevision, seed.Models, seed.refreshFailed)
	}
}

func TestPricingRefreshReportsPartialSourceFailuresWhenAuthoritativeDataWasAccepted(t *testing.T) {
	catalog := &PricingCatalog{SchemaVersion: 1, CatalogRevision: 1, Models: []PricingModel{{Provider: "openai", Model: "seed-model", Aliases: []string{"seed-model"}, Source: PricingSource{Kind: "seed"}, Rates: []PricingRate{pricingTableRate("input", "none", "1", "default", "default")}}}}
	adapters := []pricingSourceAdapter{
		{name: "openai", url: pricingOpenAIURL, kind: "official", parserVersion: "1", parse: func([]byte) ([]PricingModel, error) {
			return []PricingModel{{Provider: "openai", Model: "live-model", Aliases: []string{"live-model"}, Source: PricingSource{URL: pricingOpenAIURL, Kind: "official"}, Rates: []PricingRate{pricingTableRate("input", "none", "2", "default", "default")}}}, nil
		}},
		{name: "anthropic", url: pricingAnthropicURL, kind: "official", parserVersion: "1", parse: func([]byte) ([]PricingModel, error) { return nil, nil }},
	}
	fetch := func(_ context.Context, sourceURL string, _ map[string]string) (pricingFetchResponse, error) {
		if sourceURL == pricingAnthropicURL {
			return pricingFetchResponse{}, os.ErrPermission
		}
		return pricingFetchResponse{Status: http.StatusOK, Body: []byte("synthetic")}, nil
	}
	if err := catalog.refreshFromSources(context.Background(), adapters, fetch); err != nil {
		t.Fatal(err)
	}
	status, warnings := catalog.RefreshResult()
	if status != "updated" || len(warnings) != 1 || !strings.Contains(warnings[0], "anthropic") {
		t.Fatalf("partial refresh status=%q warnings=%v", status, warnings)
	}
	if catalog.Resolve("openai", "live-model", "input", "text", "none", "tokens", "", "", "9999-12-31T23:59:59Z") == nil {
		t.Fatal("accepted authoritative source was not activated")
	}
}

func TestPricingRefreshPublishesMachineRatesAsOfficialWithMachineCheckProvenance(t *testing.T) {
	catalog := &PricingCatalog{SchemaVersion: 1, CatalogRevision: 2, Models: []PricingModel{{
		Provider: "openai", Model: "seed-model", Aliases: []string{"seed-model"},
		Source: PricingSource{Kind: "seed"},
		Rates:  []PricingRate{pricingTableRate("input", "none", "1", "default", "default")},
	}}}
	adapter := pricingSourceAdapter{
		name: "litellm-fixture", url: pricingLiteLLMURL, kind: "machine", parserVersion: "machine-fixture-v1",
		parse: func([]byte) ([]PricingModel, error) {
			return []PricingModel{{
				Provider: "openai", Model: "machine-only-model", Aliases: []string{"machine-only-model"},
				Source: PricingSource{URL: pricingLiteLLMURL, Kind: "machine"},
				Rates:  []PricingRate{pricingTableRate("input", "read", "0.25", "default", "default")},
			}}, nil
		},
	}
	fetch := func(context.Context, string, map[string]string) (pricingFetchResponse, error) {
		return pricingFetchResponse{Status: http.StatusOK, Body: []byte("synthetic machine source")}, nil
	}
	if err := catalog.refreshFromSources(context.Background(), []pricingSourceAdapter{adapter}, fetch); err != nil {
		t.Fatal(err)
	}
	rate := catalog.Resolve("openai", "machine-only-model", "input", "text", "read", "tokens", "", "", "9999-12-31T23:59:59Z")
	if rate == nil || rate.Source.Kind != "official" || rate.Rate.USD != "0.25" {
		t.Fatalf("machine-source rate evidence = %+v; want official public evidence", rate)
	}
	if got := catalog.sourceChecks[pricingLiteLLMURL].ParserVersion; got != "machine-fixture-v1" {
		t.Fatalf("adapter provenance parser version = %q", got)
	}
	sources := catalog.PublicPayload()["sources"].([]map[string]any)
	if len(sources) != 1 || sources[0]["kind"] != "official" || sources[0]["url"] != pricingLiteLLMURL {
		t.Fatalf("public source records = %#v; want official catalog source", sources)
	}
}

func TestPricingSourceParsersRejectBadFixturesAndNormalizeStructuredSources(t *testing.T) {
	if _, err := parseProviderPricingTable([]byte(`<table><tr><th>Name</th><th>Prompt</th></tr></table>`), "openai", pricingOpenAIURL); err == nil {
		t.Fatal("unreviewed provider table layout was accepted")
	}
	litellm, err := parseLiteLLMPricing([]byte(`{"gpt-fixture":{"litellm_provider":"openai","input_cost_per_token":0.000003,"output_cost_per_token":"0.000012"}}`))
	if err != nil {
		t.Fatal(err)
	}
	if len(litellm) != 1 || litellm[0].Provider != "openai" || litellm[0].Rates[0].USD != "3" || litellm[0].Rates[1].USD != "12" {
		t.Fatalf("LiteLLM fixture result = %+v", litellm)
	}
	openrouter, err := parseOpenRouterPricing([]byte(`{"data":[{"id":"provider/model-fixture","pricing":{"prompt":"0.000002","completion":"0.000009","input_cache_read":"0.000001"}}]}`))
	if err != nil {
		t.Fatal(err)
	}
	if len(openrouter) != 1 || openrouter[0].Source.Kind != "corroboration" || openrouter[0].Rates[0].USD != "2" || openrouter[0].Rates[2].CacheRole != "read" {
		t.Fatalf("OpenRouter fixture result = %+v", openrouter)
	}
	if _, err := parseLiteLLMPricing([]byte(`not-json`)); err == nil || !strings.Contains(err.Error(), "LiteLLM") {
		t.Fatalf("malformed LiteLLM source error = %v", err)
	}
}

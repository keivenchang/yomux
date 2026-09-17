package stats

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"testing"

	"github.com/keivenchang/yolomux/internal/testsupport"
)

func TestPricingCatalogLoadsPythonFixtureAndResolvesCacheDimensions(t *testing.T) {
	catalog, err := LoadRepositoryPricingCatalog(testsupport.RepoRoot(t), "")
	if err != nil {
		t.Fatal(err)
	}
	rate := catalog.Resolve("openai", "gpt-4.1", "input", "text", "read", "tokens", "", "", "9999-12-31T23:59:59Z")
	if rate == nil || rate.Rate.USD != "0.50" {
		t.Fatalf("cache rate = %+v", rate)
	}
	if got, err := PricingMicroUSD(1234, rate); err != nil || got != 617 {
		t.Fatalf("micro usd = %d, err=%v", got, err)
	}
	if catalog.Resolve("openai", "unknown-fixture", "input", "text", "none", "tokens", "", "", "9999-12-31T23:59:59Z") != nil {
		t.Fatal("unknown model was priced")
	}
}

func TestPricingCatalogNormalizesRepeatedRoutedOpenAIPrefix(t *testing.T) {
	root := filepath.Join("..", "..")
	catalog, err := LoadRepositoryPricingCatalog(root, "")
	if err != nil {
		t.Fatal(err)
	}
	rate := catalog.Resolve("inferencehub", "openai/openai/gpt-5.6-luna", "output", "text", "none", "tokens", "", "", "9999-12-31T23:59:59Z")
	if rate == nil || rate.Provider != "openai" || rate.Model != "gpt-5.6-luna" {
		t.Fatalf("repeated routed OpenAI identity = %+v", rate)
	}
}

func TestPricingResolverMatchesPythonRoutedAndSubscriptionCases(t *testing.T) {
	catalog, err := LoadRepositoryPricingCatalog(filepath.Join("..", ".."), "")
	if err != nil {
		t.Fatal(err)
	}
	resolver := PricingResolver(catalog)
	cases := []struct {
		name, provider, model, profile  string
		quantity, wantMarginal, wantAPI int64
	}{
		{name: "switchyard", provider: "switchyard", model: "gpt-5.6-sol", quantity: 1_000_000, wantMarginal: 30_000_000, wantAPI: 30_000_000},
		{name: "switchyard routed", provider: "switchyard", model: "openai/gpt-5.6-luna", quantity: 480_000, wantMarginal: 422_784, wantAPI: 422_784},
		{name: "inferencehub routed", provider: "inferencehub", model: "switchyard/openai/gpt-5.6-luna", quantity: 480_000, wantMarginal: 422_784, wantAPI: 422_784},
		{name: "repeated prefix", provider: "inferencehub", model: "openai/openai/gpt-5.6-luna", quantity: 480_000, wantMarginal: 422_784, wantAPI: 422_784},
		{name: "subscription", provider: "openai", model: "gpt-5.6-sol", profile: "subscription", quantity: 1_000_000, wantMarginal: 0, wantAPI: 30_000_000},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			atom := UsageAtom{EventID: test.name, Direction: "output", Modality: "text", CacheRole: "none", Unit: "tokens", ObservedAt: 1783728000, Payload: map[string]any{
				"quantity": float64(test.quantity), "provider": test.provider, "model": test.model, "pricing_profile": test.profile,
			}}
			marginal, api, evidence, ok, err := resolver(atom)
			if err != nil || !ok || evidence == nil || marginal != test.wantMarginal || api != test.wantAPI {
				t.Fatalf("resolved pricing = marginal=%d api=%d evidence=%+v ok=%t err=%v", marginal, api, evidence, ok, err)
			}
		})
	}
}

func TestPricingResolverProjectionMatchesPythonOnOneSharedFixture(t *testing.T) {
	root := t.TempDir()
	fixturePath := filepath.Join(root, "pricing-fixture.json")
	scriptPath := filepath.Join(root, "pricing-oracle.py")
	fixture := map[string]any{
		"event_id":    "pricing-oracle",
		"direction":   "output",
		"modality":    "text",
		"cache_role":  "none",
		"unit":        "tokens",
		"observed_at": float64(1783728000),
		"payload": map[string]any{
			"quantity": float64(480000), "provider": "inferencehub",
			"model": "openai/openai/gpt-5.6-luna", "agent_id": "agent-a",
			"pricing_profile": "default", "telemetry_complete": true,
		},
	}
	data, err := json.Marshal(fixture)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(fixturePath, data, 0600); err != nil {
		t.Fatal(err)
	}
	const oracle = `import json, sys
from pathlib import Path
from yolomux_lib.observability.pricing_catalog import PricingCatalog
from yolomux_lib.stats_current import pricing, storage, usage
item = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
atom = storage.UsageAtom(item["event_id"], item["direction"], item["modality"], item["cache_role"], item["unit"], item["observed_at"], item["payload"])
projected = pricing.UsagePriceProjector(PricingCatalog(Path(sys.argv[2])))(usage.normalize_usage_atom(atom))
print(json.dumps({"micro_usd": projected.micro_usd, "api_list_micro_usd": projected.api_list_micro_usd, "priced": projected.priced, "catalog_model": projected.evidence.catalog_model if projected.evidence else None, "catalog_revision": projected.evidence.catalog_revision if projected.evidence else None}, sort_keys=True, separators=(",", ":")))
`
	if err := os.WriteFile(scriptPath, []byte(oracle), 0600); err != nil {
		t.Fatal(err)
	}
	expectedRaw := runPython(t, pythonExecutable(t), scriptPath, fixturePath, filepath.Join(root, "python-pricing-cache"))
	var expected map[string]any
	if err := json.Unmarshal([]byte(expectedRaw), &expected); err != nil {
		t.Fatalf("decode Python pricing oracle: %v\n%s", err, expectedRaw)
	}
	catalog, err := LoadRepositoryPricingCatalog(filepath.Join("..", ".."), "")
	if err != nil {
		t.Fatal(err)
	}
	item := fixture
	atom := UsageAtom{EventID: item["event_id"].(string), Direction: item["direction"].(string), Modality: item["modality"].(string), CacheRole: item["cache_role"].(string), Unit: item["unit"].(string), ObservedAt: item["observed_at"].(float64), Payload: item["payload"].(map[string]any)}
	micro, api, evidence, priced, err := PricingResolver(catalog)(atom)
	if err != nil {
		t.Fatal(err)
	}
	got := map[string]any{"micro_usd": micro, "api_list_micro_usd": api, "priced": priced, "catalog_model": evidence.CatalogModel, "catalog_revision": evidence.CatalogRevision}
	gotRaw, _ := json.Marshal(got)
	var gotJSON any
	if err := json.Unmarshal(gotRaw, &gotJSON); err != nil {
		t.Fatal(err)
	}
	var expectedJSON any
	if err := json.Unmarshal([]byte(expectedRaw), &expectedJSON); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(gotJSON, expectedJSON) {
		t.Fatalf("Go/Python pricing projection mismatch\nGo: %s\nPython: %s", gotRaw, expectedRaw)
	}
}

func TestPricingCatalogRefreshIsBoundedAndTransactional(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "catalog.json")
	fixture := `{"schema_version":1,"catalog_revision":1,"models":[{"provider":"p","model":"m","aliases":["m"],"rates":[{"direction":"input","modality":"text","cache_role":"none","unit":"tokens","scale":1000000,"usd":"1.00","effective_from":"1970-01-01T00:00:00Z"}]}]}`
	if err := os.WriteFile(path, []byte(fixture), 0600); err != nil {
		t.Fatal(err)
	}
	catalog, err := LoadPricingCatalog(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(`not-json`), 0600); err != nil {
		t.Fatal(err)
	}
	if err := catalog.Refresh(); err == nil {
		t.Fatal("invalid refresh accepted")
	}
	if catalog.Resolve("p", "m", "input", "text", "none", "tokens", "", "", "9999-12-31T23:59:59Z") == nil {
		t.Fatal("refresh corrupted active catalog")
	}
}

func TestLoadRepositoryPricingCatalogIgnoresMissingFallbackAfterConfiguredCatalog(t *testing.T) {
	dir := t.TempDir()
	configured := filepath.Join(dir, "configured.json")
	fixture := `{"schema_version":1,"catalog_revision":7,"models":[{"provider":"p","model":"m","aliases":["m"],"rates":[{"direction":"input","modality":"text","cache_role":"none","unit":"tokens","scale":1000000,"usd":"1.00","effective_from":"1970-01-01T00:00:00Z"}]}]}`
	if err := os.WriteFile(configured, []byte(fixture), 0600); err != nil {
		t.Fatal(err)
	}
	catalog, err := LoadRepositoryPricingCatalog(dir, configured)
	if err != nil {
		t.Fatalf("configured catalog with absent repository fallbacks: %v", err)
	}
	if catalog.CatalogRevision != 7 || catalog.Resolve("p", "m", "input", "text", "none", "tokens", "", "", "9999-12-31T23:59:59Z") == nil {
		t.Fatalf("loaded catalog = %+v", catalog)
	}
}

func TestLoadRepositoryPricingCatalogPreservesConfiguredModelOverInternalFallback(t *testing.T) {
	dir := t.TempDir()
	if err := os.MkdirAll(filepath.Join(dir, "yolomux_lib", "data"), 0700); err != nil {
		t.Fatal(err)
	}
	configured := filepath.Join(dir, "configured.json")
	configuredFixture := `{"schema_version":1,"catalog_revision":7,"models":[{"provider":"openai","model":"gpt-5.6-luna","aliases":["gpt-5.6-luna"],"rates":[{"direction":"output","modality":"text","cache_role":"none","unit":"tokens","scale":1000000,"usd":"1.00","effective_from":"1970-01-01T00:00:00Z"}]}]}`
	internalFixture := `{"schema_version":1,"catalog_revision":8,"models":[{"provider":"openai","model":"gpt-5.6-luna","aliases":["gpt-5.6-luna"],"rates":[{"direction":"output","modality":"text","cache_role":"none","unit":"tokens","scale":1000000,"usd":"9.00","effective_from":"1970-01-01T00:00:00Z"}]}]}`
	if err := os.WriteFile(configured, []byte(configuredFixture), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "yolomux_lib", "data", "internal_model_pricing.json"), []byte(internalFixture), 0600); err != nil {
		t.Fatal(err)
	}
	catalog, err := LoadRepositoryPricingCatalog(dir, configured)
	if err != nil {
		t.Fatal(err)
	}
	rate := catalog.Resolve("openai", "gpt-5.6-luna", "output", "text", "none", "tokens", "", "", "9999-12-31T23:59:59Z")
	if rate == nil || rate.Rate.USD != "1.00" || catalog.CatalogRevision != 8 {
		t.Fatalf("configured model was overwritten: rate=%+v revision=%d", rate, catalog.CatalogRevision)
	}
}

func TestLoadRepositoryPricingCatalogMergesInternalLunaForPartialConfiguredCatalog(t *testing.T) {
	dir := t.TempDir()
	if err := os.MkdirAll(filepath.Join(dir, "yolomux_lib", "data"), 0700); err != nil {
		t.Fatal(err)
	}
	configured := filepath.Join(dir, "configured.json")
	configuredFixture := `{"schema_version":1,"catalog_revision":7,"models":[{"provider":"p","model":"m","aliases":["m"],"rates":[{"direction":"input","modality":"text","cache_role":"none","unit":"tokens","scale":1000000,"usd":"1.00","effective_from":"1970-01-01T00:00:00Z"}]}]}`
	internalFixture := `{"schema_version":1,"catalog_revision":8,"models":[{"provider":"openai","model":"gpt-5.6-luna","aliases":["gpt-5.6-luna"],"rates":[{"direction":"output","modality":"text","cache_role":"none","unit":"tokens","scale":1000000,"usd":"0.88","effective_from":"1970-01-01T00:00:00Z"}]}]}`
	if err := os.WriteFile(configured, []byte(configuredFixture), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "yolomux_lib", "data", "internal_model_pricing.json"), []byte(internalFixture), 0600); err != nil {
		t.Fatal(err)
	}
	catalog, err := LoadRepositoryPricingCatalog(dir, configured)
	if err != nil {
		t.Fatal(err)
	}
	rate := catalog.Resolve("openai", "gpt-5.6-luna", "output", "text", "none", "tokens", "", "", "9999-12-31T23:59:59Z")
	if rate == nil || rate.Rate.USD != "0.88" || catalog.CatalogRevision != 8 {
		t.Fatalf("partial configured catalog suppressed internal Luna fallback: rate=%+v revision=%d", rate, catalog.CatalogRevision)
	}
}

func TestPricingCatalogRefreshPreservesRepositoryFallbackMerge(t *testing.T) {
	dir := t.TempDir()
	dataDir := filepath.Join(dir, "yolomux_lib", "data")
	if err := os.MkdirAll(dataDir, 0700); err != nil {
		t.Fatal(err)
	}
	configured := filepath.Join(dir, "configured.json")
	configuredFixture := `{"schema_version":1,"catalog_revision":7,"models":[{"provider":"p","model":"m","aliases":["m"],"rates":[{"direction":"input","modality":"text","cache_role":"none","unit":"tokens","scale":1000000,"usd":"1.00","effective_from":"1970-01-01T00:00:00Z"}]}]}`
	publicFixture := `{"schema_version":1,"catalog_revision":8,"models":[{"provider":"openai","model":"gpt-5.6-luna","aliases":["gpt-5.6-luna"],"rates":[{"direction":"output","modality":"text","cache_role":"none","unit":"tokens","scale":1000000,"usd":"6.00","effective_from":"1970-01-01T00:00:00Z"}]}]}`
	internalPath := filepath.Join(dataDir, "internal_model_pricing.json")
	internalFixture := func(usd string) []byte {
		return []byte(fmt.Sprintf(`{"schema_version":1,"catalog_revision":9,"models":[{"provider":"openai","model":"gpt-5.6-luna","aliases":["gpt-5.6-luna"],"rates":[{"direction":"output","modality":"text","cache_role":"none","unit":"tokens","scale":1000000,"usd":"%s","effective_from":"1970-01-01T00:00:00Z"}]}]}`, usd))
	}
	if err := os.WriteFile(configured, []byte(configuredFixture), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dataDir, "model_pricing.json"), []byte(publicFixture), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(internalPath, internalFixture("0.88"), 0600); err != nil {
		t.Fatal(err)
	}
	catalog, err := LoadRepositoryPricingCatalog(dir, configured)
	if err != nil {
		t.Fatal(err)
	}
	resolve := func() string {
		rate := catalog.Resolve("openai", "gpt-5.6-luna", "output", "text", "none", "tokens", "", "", "9999-12-31T23:59:59Z")
		if rate == nil {
			return ""
		}
		return rate.Rate.USD
	}
	if got := resolve(); got != "0.88" {
		t.Fatalf("initial merged Luna rate = %q", got)
	}
	if err := os.WriteFile(internalPath, internalFixture("0.77"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := catalog.Refresh(); err != nil {
		t.Fatal(err)
	}
	if got := resolve(); got != "0.77" {
		t.Fatalf("refresh dropped internal Luna fallback: %q", got)
	}
}

func TestLoadRepositoryPricingCatalogTreatsConfiguredLunaAliasAsOwnership(t *testing.T) {
	dir := t.TempDir()
	if err := os.MkdirAll(filepath.Join(dir, "yolomux_lib", "data"), 0700); err != nil {
		t.Fatal(err)
	}
	configured := filepath.Join(dir, "configured.json")
	configuredFixture := `{"schema_version":1,"catalog_revision":7,"models":[{"provider":"openai","model":"company-luna","aliases":["company-luna","gpt-5.6-luna"],"rates":[{"direction":"output","modality":"text","cache_role":"none","unit":"tokens","scale":1000000,"usd":"1.23","effective_from":"1970-01-01T00:00:00Z"}]}]}`
	internalFixture := `{"schema_version":1,"catalog_revision":8,"models":[{"provider":"openai","model":"gpt-5.6-luna","aliases":["gpt-5.6-luna"],"rates":[{"direction":"output","modality":"text","cache_role":"none","unit":"tokens","scale":1000000,"usd":"9.00","effective_from":"1970-01-01T00:00:00Z"}]}]}`
	if err := os.WriteFile(configured, []byte(configuredFixture), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "yolomux_lib", "data", "internal_model_pricing.json"), []byte(internalFixture), 0600); err != nil {
		t.Fatal(err)
	}
	catalog, err := LoadRepositoryPricingCatalog(dir, configured)
	if err != nil {
		t.Fatal(err)
	}
	rate := catalog.Resolve("openai", "gpt-5.6-luna", "output", "text", "none", "tokens", "", "", "9999-12-31T23:59:59Z")
	if rate == nil || rate.Rate.USD != "1.23" {
		t.Fatalf("configured Luna alias did not own precedence: rate=%+v", rate)
	}
}

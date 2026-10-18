package stats

import (
	"context"

	"errors"
	"fmt"
	"math/big"
	"os"
	"path/filepath"

	"testing"
	"time"

	"github.com/keivenchang/yomux/internal/testsupport"
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

func TestPricingMicroUSDEnforcesPythonJSONSafeIntegerBoundary(t *testing.T) {
	const maxSafe = maxSafeJSONInteger

	within := &ResolvedPricingRate{Rate: PricingRate{Scale: 1, USD: "9007199254.740991"}}
	if got, err := PricingMicroUSD(1, within); err != nil || got != maxSafe {
		t.Fatalf("maximum safe projected cost = %d, err=%v; want %d", got, err, maxSafe)
	}

	outside := &ResolvedPricingRate{Rate: PricingRate{Scale: 1, USD: "9007199254.740992"}}
	if got, err := PricingMicroUSD(1, outside); err == nil || got != 0 {
		t.Fatalf("cost above Python's JSON-safe limit = %d, err=%v; want a rejected projection", got, err)
	}
}

func TestPricingCatalogPublicPayloadReportsJSONSourceLifecycle(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "catalog.json")
	checkedAt := time.Now().UTC().Truncate(time.Second)
	fixture := func(source string) []byte {
		return []byte(fmt.Sprintf(`{"schema_version":1,"catalog_revision":7,"models":[{"provider":"fixture","model":"model-a","aliases":["model-a"],"source":{"kind":"official","url":"https://example.invalid/pricing","checked_at":"%s"},"rates":[{"direction":"input","modality":"text","cache_role":"none","unit":"tokens","scale":1000000,"usd":"1.00","effective_from":"1970-01-01T00:00:00Z"}]}]}`, source))
	}
	if err := os.WriteFile(path, fixture(checkedAt.Format(time.RFC3339)), 0600); err != nil {
		t.Fatal(err)
	}
	catalog, err := LoadPricingCatalog(path)
	if err != nil {
		t.Fatal(err)
	}
	status := catalog.PublicPayload()["status"].(map[string]any)
	if status["state"] != "fresh" || status["catalog_revision"] != int64(7) || status["seed_revision"] != int64(0) {
		t.Fatalf("fresh status = %#v", status)
	}
	if got := status["last_checked_at"].(float64); got != float64(checkedAt.Unix()) {
		t.Fatalf("last_checked_at = %v, want %d", got, checkedAt.Unix())
	}
	if status["database"] != "" {
		t.Fatalf("Go JSON catalog fabricated a database identity: %#v", status["database"])
	}

	old := checkedAt.Add(-31 * 24 * time.Hour).Format(time.RFC3339)
	if err := os.WriteFile(path, fixture(old), 0600); err != nil {
		t.Fatal(err)
	}
	if err := catalog.Refresh(); err != nil {
		t.Fatal(err)
	}
	status = catalog.PublicPayload()["status"].(map[string]any)
	if status["state"] != "stale" {
		t.Fatalf("stale status = %#v", status)
	}

	if err := os.WriteFile(path, []byte("not-json"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := catalog.Refresh(); err == nil {
		t.Fatal("invalid pricing refresh unexpectedly succeeded")
	}
	status = catalog.PublicPayload()["status"].(map[string]any)
	if status["state"] != "review-needed" {
		t.Fatalf("failed-refresh status = %#v", status)
	}
}

func TestPricingCatalogSeedPayloadDoesNotUseEmbeddedCheckedAtAsRefreshEvidence(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "catalog.json")
	fixture := `{"schema_version":1,"catalog_revision":11,"models":[{"provider":"fixture","model":"model-a","aliases":["model-a"],"source":{"kind":"seed","url":"https://example.invalid/pricing","checked_at":"2026-09-17T00:00:00Z"},"rates":[{"direction":"input","modality":"text","cache_role":"none","unit":"tokens","scale":1000000,"usd":"1.00","effective_from":"1970-01-01T00:00:00Z"}]}]}`
	if err := os.WriteFile(path, []byte(fixture), 0600); err != nil {
		t.Fatal(err)
	}
	catalog, err := LoadPricingCatalog(path)
	if err != nil {
		t.Fatal(err)
	}
	status := catalog.PublicPayload()["status"].(map[string]any)
	if status["state"] != "seed-only" || status["seed_revision"] != int64(11) || status["last_checked_at"] != float64(0) {
		t.Fatalf("seed status = %#v", status)
	}
}

func TestPricingCatalogRejectsPythonInvalidRateAndSourceFields(t *testing.T) {
	cases := []struct {
		name string
		raw  string
	}{
		{name: "negative usd", raw: `{"schema_version":1,"catalog_revision":1,"models":[{"provider":"p","model":"m","aliases":["m"],"rates":[{"direction":"input","modality":"text","cache_role":"none","unit":"tokens","scale":1000000,"usd":"-1.00","effective_from":"1970-01-01T00:00:00Z"}]}]}`},
		{name: "blank alias", raw: `{"schema_version":1,"catalog_revision":1,"models":[{"provider":"p","model":"m","aliases":[""],"rates":[{"direction":"input","modality":"text","cache_role":"none","unit":"tokens","scale":1000000,"usd":"1.00","effective_from":"1970-01-01T00:00:00Z"}]}]}`},
		{name: "blank rate dimension", raw: `{"schema_version":1,"catalog_revision":1,"models":[{"provider":"p","model":"m","aliases":["m"],"rates":[{"direction":"","modality":"text","cache_role":"none","unit":"tokens","scale":1000000,"usd":"1.00","effective_from":"1970-01-01T00:00:00Z"}]}]}`},
		{name: "non-https source", raw: `{"schema_version":1,"catalog_revision":1,"models":[{"provider":"p","model":"m","aliases":["m"],"source":{"url":"http://example.invalid/pricing"},"rates":[{"direction":"input","modality":"text","cache_role":"none","unit":"tokens","scale":1000000,"usd":"1.00","effective_from":"1970-01-01T00:00:00Z"}]}]}`},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "catalog.json")
			if err := os.WriteFile(path, []byte(test.raw), 0600); err != nil {
				t.Fatal(err)
			}
			if _, err := LoadPricingCatalog(path); err == nil {
				t.Fatalf("invalid Python catalog fixture was accepted: %s", test.raw)
			}
		})
	}
}

func TestPricingCatalogNormalizesRepeatedRoutedOpenAIPrefix(t *testing.T) {
	root := testsupport.RepoRoot(t)
	catalog, err := LoadRepositoryPricingCatalog(root, "")
	if err != nil {
		t.Fatal(err)
	}
	rate := catalog.Resolve("inferencehub", "openai/openai/gpt-5.6-luna", "output", "text", "none", "tokens", "", "", "9999-12-31T23:59:59Z")
	if rate == nil || rate.Provider != "openai" || rate.Model != "gpt-5.6-luna" {
		t.Fatalf("repeated routed OpenAI identity = %+v", rate)
	}
}

func TestPricingCatalogInfersInferenceHubSwitchyardEquivalentRates(t *testing.T) {
	equivalent := "claude-fixture"
	model := "switchyard/anthropic/" + equivalent
	catalog := &PricingCatalog{SchemaVersion: 1, CatalogRevision: 7, Models: []PricingModel{
		{
			Provider: "openai", Model: equivalent, Aliases: []string{equivalent},
			Source: PricingSource{Kind: "official", URL: pricingOpenAIURL, CheckedAt: "2026-01-01T00:00:00Z"},
			Rates:  []PricingRate{pricingTableRate("output", "none", "1", "default", "default")},
		},
		{
			Provider: "anthropic", Model: "claude/" + equivalent, Aliases: []string{"claude/" + equivalent},
			Source: PricingSource{Kind: "official", URL: pricingAnthropicURL},
			Rates:  []PricingRate{pricingTableRate("output", "none", "3", "default", "default")},
		},
		{
			// Python's fallback averages one newest matching row per
			// (provider, model), not every duplicate catalog representation.
			Provider: "openai", Model: equivalent, Aliases: []string{"legacy/" + equivalent},
			Source: PricingSource{Kind: "official", URL: pricingOpenAIURL, CheckedAt: "2026-02-01T00:00:00Z"},
			Rates:  []PricingRate{pricingTableRate("output", "none", "5", "default", "default")},
		},
	}}
	catalog.Models[2].Rates[0].EffectiveFrom = "1970-01-01T00:00:00Z"
	catalog.Models = append(catalog.Models, PricingModel{
		Provider: "openai", Model: equivalent, Aliases: []string{"older/" + equivalent},
		Source: PricingSource{Kind: "official", URL: pricingOpenAIURL, CheckedAt: "2026-03-01T00:00:00Z"},
		Rates:  []PricingRate{pricingTableRate("output", "none", "99", "default", "default")},
	})
	catalog.Models[3].Rates[0].EffectiveFrom = "1969-01-01T00:00:00Z"
	rate := catalog.Resolve("inferencehub", model, "output", "text", "none", "tokens", "", "", "9999-12-31T23:59:59Z")
	if rate == nil || rate.Provider != "inferencehub" || rate.Model != equivalent || rate.Alias != model || rate.Rate.USD != "4" || rate.Source.Kind != "inferred" || rate.Source.CheckedAt != "2026-02-01T00:00:00Z" {
		t.Fatalf("inferencehub equivalent rate = %+v; want latest inferred mean price", rate)
	}
	if got, err := PricingMicroUSD(1_000_000, rate); err != nil || got != 4_000_000 {
		t.Fatalf("inferred equivalent cost = %d, err=%v; want 4,000,000 micro-USD", got, err)
	}
	caseVariant := &PricingCatalog{SchemaVersion: 1, CatalogRevision: 7, Models: []PricingModel{{
		Provider: "synthetic", Model: "tenant/CLAUDE-FIXTURE", Aliases: []string{"tenant/CLAUDE-FIXTURE"},
		Source: PricingSource{Kind: "official", URL: pricingAnthropicURL},
		Rates:  []PricingRate{pricingTableRate("output", "none", "2", "default", "default")},
	}}}
	caseInsensitive := caseVariant.Resolve("inferencehub", model, "output", "text", "none", "tokens", "", "", "9999-12-31T23:59:59Z")
	if caseInsensitive == nil || caseInsensitive.Rate.USD != "2" || caseInsensitive.Source.Kind != "inferred" {
		t.Fatalf("case-insensitive Switchyard suffix rate = %+v; want inferred 2 USD rate", caseInsensitive)
	}
	caseVariant.Models[0].Model = "tenant/CLÁUDE-FIXTURE"
	if unexpected := caseVariant.Resolve("inferencehub", model, "output", "text", "none", "tokens", "", "", "9999-12-31T23:59:59Z"); unexpected != nil {
		t.Fatalf("non-ASCII Switchyard suffix unexpectedly matched SQLite LIKE semantics: %+v", unexpected)
	}
	caseVariant.Models[0].Model = "CLAUDE-FIXTURE"
	if unexpected := caseVariant.Resolve("inferencehub", model, "output", "text", "none", "tokens", "", "", "9999-12-31T23:59:59Z"); unexpected != nil {
		t.Fatalf("case-variant exact Switchyard model unexpectedly matched SQLite equality: %+v", unexpected)
	}

	seed := &PricingCatalog{SchemaVersion: 1, CatalogRevision: 7, Models: clonePricingModels(catalog.Models)}
	seed.Models = append(seed.Models, PricingModel{
		Provider: "inferencehub", Model: model, Aliases: []string{model},
		Source: PricingSource{Kind: "seed"},
		Rates:  []PricingRate{pricingTableRate("output", "none", "9", "default", "default")},
	})
	if got := seed.Resolve("inferencehub", model, "output", "text", "none", "tokens", "", "", "9999-12-31T23:59:59Z"); got == nil || got.Rate.USD != "5.666666666666666666666666667" || got.Source.Kind != "inferred" {
		t.Fatalf("equivalent fallback did not include the active seed rate before exact seed resolution: %+v", got)
	}

	direct := &PricingCatalog{SchemaVersion: 1, CatalogRevision: 7, Models: clonePricingModels(catalog.Models)}
	direct.Models = append(direct.Models, PricingModel{
		Provider: "inferencehub", Model: model, Aliases: []string{model},
		Source: PricingSource{Kind: "official", URL: pricingAnthropicURL},
		Rates:  []PricingRate{pricingTableRate("output", "none", "9", "default", "default")},
	})
	if got := direct.Resolve("inferencehub", model, "output", "text", "none", "tokens", "", "", "9999-12-31T23:59:59Z"); got == nil || got.Rate.USD != "9" || got.Source.Kind != "official" {
		t.Fatalf("explicit inferencehub rate did not take precedence over fallback: %+v", got)
	}
	override := &PricingCatalog{SchemaVersion: 1, CatalogRevision: 7, Models: clonePricingModels(catalog.Models)}
	override.Models = append(override.Models, PricingModel{
		Provider: "inferencehub", Model: model, Aliases: []string{model},
		Source: PricingSource{Kind: "override"},
		Rates:  []PricingRate{pricingTableRate("output", "none", "11", "default", "default")},
	})
	if got := override.Resolve("inferencehub", model, "output", "text", "none", "tokens", "", "", "9999-12-31T23:59:59Z"); got == nil || got.Rate.USD != "11" || got.Source.Kind != "override" {
		t.Fatalf("explicit inferencehub override did not take precedence over fallback: %+v", got)
	}
}

func TestPricingCatalogSwitchyardInferenceIncludesActiveSeedRates(t *testing.T) {
	equivalent := "claude-fixture"
	catalog := &PricingCatalog{SchemaVersion: 1, CatalogRevision: 7, Models: []PricingModel{
		{
			Provider: "anthropic", Model: "anthropic/" + equivalent, Aliases: []string{"anthropic/" + equivalent},
			Source: PricingSource{Kind: "official", URL: pricingAnthropicURL},
			Rates:  []PricingRate{pricingTableRate("output", "none", "1", "default", "default")},
		},
		{
			Provider: "fixture-seed", Model: "switchyard/anthropic/" + equivalent, Aliases: []string{"switchyard/anthropic/" + equivalent},
			Source: PricingSource{Kind: "seed"},
			Rates:  []PricingRate{pricingTableRate("output", "none", "9", "default", "default")},
		},
	}}
	rate := catalog.Resolve("inferencehub", "switchyard/anthropic/"+equivalent, "output", "text", "none", "tokens", "", "", "9999-12-31T23:59:59Z")
	if rate == nil || rate.Rate.USD != "5" || rate.Source.Kind != "inferred" {
		t.Fatalf("Switchyard inferred rate = %+v; want inferred mean including active seed rate: 5 USD", rate)
	}
}

func TestPricingCatalogOfficialFamilyInferencePrecedesExactSeed(t *testing.T) {
	catalog := &PricingCatalog{SchemaVersion: 1, CatalogRevision: 7, Models: []PricingModel{
		{
			Provider: "openai", Model: "gpt-5.6", Aliases: []string{"gpt-5.6"},
			Source: PricingSource{Kind: "official", URL: pricingOpenAIURL},
			Rates:  []PricingRate{pricingTableRate("input", "none", "4", "default", "default")},
		},
		{
			Provider: "openai", Model: "gpt-5.6-sol", Aliases: []string{"gpt-5.6-sol"},
			Source: PricingSource{Kind: "seed"},
			Rates:  []PricingRate{pricingTableRate("input", "none", "9", "default", "default")},
		},
	}}
	rate := catalog.Resolve("openai", "gpt-5.6-sol", "input", "text", "none", "tokens", "", "", "9999-12-31T23:59:59Z")
	if rate == nil || rate.Rate.USD != "4" || rate.Source.Kind != "inferred" {
		t.Fatalf("official family rate = %+v; want inferred 4 USD before exact seed", rate)
	}
}

func TestPricingRatDecimalStringMatchesPythonDecimalPrecision(t *testing.T) {
	cases := []struct {
		name      string
		value     string
		precision int
		want      string
	}{
		{name: "repeating fraction", value: "17/3", precision: 28, want: "5.666666666666666666666666667"},
		{name: "small magnitude", value: "1/2000000000", precision: 2, want: "0.0000000005"},
		{name: "half even down", value: "5/4", precision: 2, want: "1.2"},
		{name: "half even up", value: "27/20", precision: 2, want: "1.4"},
		{name: "large magnitude", value: "12345", precision: 3, want: "12300"},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			value, ok := new(big.Rat).SetString(test.value)
			if !ok {
				t.Fatalf("invalid rational fixture %q", test.value)
			}
			if got := pricingRatDecimalString(value, test.precision); got != test.want {
				t.Fatalf("pricingRatDecimalString(%s, %d) = %q, want %q", test.value, test.precision, got, test.want)
			}
		})
	}
}

func TestPricingCatalogInfersOnlyPythonReviewedModelSuffixes(t *testing.T) {
	base := PricingModel{
		Provider: "openai", Model: "gpt-fixture", Aliases: []string{"gpt-fixture"},
		Source: PricingSource{Kind: "official", URL: pricingOpenAIURL},
		Rates:  []PricingRate{pricingTableRate("input", "none", "1.25", "default", "default")},
	}
	catalog := &PricingCatalog{SchemaVersion: 1, CatalogRevision: 3, Models: []PricingModel{base}}
	cases := []string{
		"gpt-fixture-2026-01-02", "gpt-fixture-20260102", "gpt-fixture-latest", "gpt-fixture-preview",
		"gpt-fixture-sol", "gpt-fixture-terra", "gpt-fixture-luna",
		"gpt-fixture-sol-latest", "gpt-fixture-terra-preview", "gpt-fixture-luna-2026-01-02",
		"gpt-fixture-SOL", "gpt-fixture-TERRA-LATEST", "gpt-fixture-PREVIEW",
	}
	for _, model := range cases {
		t.Run(model, func(t *testing.T) {
			resolved := catalog.Resolve("openai", model, "input", "text", "none", "tokens", "", "", "9999-12-31T23:59:59Z")
			if resolved == nil || resolved.Rate.USD != "1.25" || resolved.Source.Kind != "inferred" {
				t.Fatalf("resolved rate = %+v; want inferred gpt-fixture rate", resolved)
			}
		})
	}
	for _, model := range []string{"gpt-fixture-sol-extra", "gpt-fixture-2026", "gpt-fixture-preview-x"} {
		if resolved := catalog.Resolve("openai", model, "input", "text", "none", "tokens", "", "", "9999-12-31T23:59:59Z"); resolved != nil {
			t.Fatalf("unsupported suffix %q was inferred: %+v", model, resolved)
		}
	}
}

func TestPricingResolverMatchesPythonRoutedAndSubscriptionCases(t *testing.T) {
	catalog, err := LoadRepositoryPricingCatalog(testsupport.RepoRoot(t), "")
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

func TestPricingCatalogRefreshKeepsRevisionMonotonicForLowerSourceRevision(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "catalog.json")
	fixture := func(revision int, usd string) []byte {
		return []byte(fmt.Sprintf(`{"schema_version":1,"catalog_revision":%d,"models":[{"provider":"p","model":"m","aliases":["m"],"source":{"kind":"fixture","url":"https://example.invalid/pricing"},"rates":[{"direction":"input","modality":"text","cache_role":"none","unit":"tokens","scale":1000000,"usd":"%s","effective_from":"1970-01-01T00:00:00Z"}]}]}`, revision, usd))
	}
	if err := os.WriteFile(path, fixture(7, "1.00"), 0600); err != nil {
		t.Fatal(err)
	}
	catalog, err := LoadPricingCatalog(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, fixture(3, "2.00"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := catalog.Refresh(); err != nil {
		t.Fatal(err)
	}
	if catalog.CatalogRevision != 8 {
		t.Fatalf("catalog revision = %d, want monotonic revision 8", catalog.CatalogRevision)
	}
	rate := catalog.Resolve("p", "m", "input", "text", "none", "tokens", "", "", "9999-12-31T23:59:59Z")
	if rate == nil || rate.Rate.USD != "2.00" || rate.CatalogRevision != 8 {
		t.Fatalf("refreshed rate = %+v, want source rate at revision 8", rate)
	}
}

func TestPricingCatalogRefreshInvalidatesPublishedUsageCost(t *testing.T) {
	ctx := context.Background()
	dir := t.TempDir()
	path := filepath.Join(dir, "catalog.json")
	fixture := func(revision int, usd string) []byte {
		return []byte(fmt.Sprintf(`{"schema_version":1,"catalog_revision":%d,"models":[{"provider":"p","model":"m","aliases":["m"],"source":{"kind":"fixture","url":"https://example.invalid/pricing"},"rates":[{"direction":"input","modality":"text","cache_role":"none","unit":"tokens","scale":1000000,"usd":"%s","effective_from":"1970-01-01T00:00:00Z"}]}]}`, revision, usd))
	}
	if err := os.WriteFile(path, fixture(1, "1.00"), 0600); err != nil {
		t.Fatal(err)
	}
	catalog, err := LoadPricingCatalog(path)
	if err != nil {
		t.Fatal(err)
	}
	statsPath := filepath.Join(dir, "stats.sqlite3")
	writer, err := OpenWriter(ctx, statsPath)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	now := time.Unix(1000, 0)
	resolver := PricingResolver(catalog)
	if _, err := writer.AppendBatch(ctx, AppendBatch{UsageAtoms: []UsageAtom{{
		EventID: "pricing-refresh-atom", Direction: "input", Modality: "text", CacheRole: "none", Unit: "tokens", ObservedAt: 990,
		Payload: map[string]any{"quantity": 100.0, "provider": "p", "model": "m", "agent_id": "fixture-agent", "thread_id": "fixture-thread", "execution_source": "codex"},
	}}}); err != nil {
		t.Fatal(err)
	}
	lifecycle := NewLifecycle(writer, LifecycleConfig{WindowSeconds: 100, MaxCells: 64, Price: resolver, Now: func() time.Time { return now }})
	t.Cleanup(lifecycle.Stop)
	lifecycle.DemandProduct(100, 10)
	service := NewStatsService(writer, StatsServiceConfig{Products: []ProductKey{{RangeSeconds: 100, ResolutionSeconds: 10}}, Price: resolver, Now: func() time.Time { return now }})
	refresh := func() StatsSnapshot {
		for attempt := 0; attempt < 64; attempt++ {
			if err := lifecycle.RunOnce(ctx); err != nil {
				t.Fatal(err)
			}
			if err := service.Refresh(ctx); err != nil && !errors.Is(err, ErrProductPending) {
				t.Fatal(err)
			}
			if service.State().Ready {
				break
			}
		}
		if !service.State().Ready {
			t.Fatalf("pricing product did not become ready: %+v", service.State())
		}
		snapshot, err := service.Snapshot(ctx, SnapshotRequest{Start: 910, End: 1010, Resolution: 10})
		if err != nil {
			t.Fatal(err)
		}
		return snapshot
	}
	initial := refresh()
	initialCost := int64(0)
	for _, bucket := range initial.Materialized.Buckets {
		initialCost += bucket.Cost.MicroUSD
	}
	if initialCost != 100 {
		t.Fatalf("initial materialized cost = %d, snapshot=%+v", initialCost, initial)
	}
	if err := os.WriteFile(path, fixture(2, "2.00"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := catalog.Refresh(); err != nil {
		t.Fatal(err)
	}
	generation, err := writer.InvalidatePricingCatalog(ctx, float64(now.Unix()))
	if err != nil {
		t.Fatal(err)
	}
	if generation != 2 || writer.PendingCount() <= 0 {
		t.Fatalf("pricing invalidation generation=%d pending=%d", generation, writer.PendingCount())
	}
	updated := refresh()
	updatedCost := int64(0)
	evidenceRevision := int64(0)
	for _, bucket := range updated.Materialized.Buckets {
		updatedCost += bucket.Cost.MicroUSD
		for _, evidence := range bucket.Cost.Evidence {
			evidenceRevision = evidence.CatalogRevision
		}
	}
	if updatedCost != 200 || evidenceRevision != 2 {
		t.Fatalf("refreshed materialized cost=%d evidence revision=%d, snapshot=%+v", updatedCost, evidenceRevision, updated)
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
	if err := os.MkdirAll(filepath.Join(dir, "assets", "runtime"), 0700); err != nil {
		t.Fatal(err)
	}
	configured := filepath.Join(dir, "configured.json")
	configuredFixture := `{"schema_version":1,"catalog_revision":7,"models":[{"provider":"openai","model":"gpt-5.6-luna","aliases":["gpt-5.6-luna"],"rates":[{"direction":"output","modality":"text","cache_role":"none","unit":"tokens","scale":1000000,"usd":"1.00","effective_from":"1970-01-01T00:00:00Z"}]}]}`
	internalFixture := `{"schema_version":1,"catalog_revision":8,"models":[{"provider":"openai","model":"gpt-5.6-luna","aliases":["gpt-5.6-luna"],"rates":[{"direction":"output","modality":"text","cache_role":"none","unit":"tokens","scale":1000000,"usd":"9.00","effective_from":"1970-01-01T00:00:00Z"}]}]}`
	if err := os.WriteFile(configured, []byte(configuredFixture), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "assets", "runtime", "internal_model_pricing.json"), []byte(internalFixture), 0600); err != nil {
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
	if err := os.MkdirAll(filepath.Join(dir, "assets", "runtime"), 0700); err != nil {
		t.Fatal(err)
	}
	configured := filepath.Join(dir, "configured.json")
	configuredFixture := `{"schema_version":1,"catalog_revision":7,"models":[{"provider":"p","model":"m","aliases":["m"],"rates":[{"direction":"input","modality":"text","cache_role":"none","unit":"tokens","scale":1000000,"usd":"1.00","effective_from":"1970-01-01T00:00:00Z"}]}]}`
	internalFixture := `{"schema_version":1,"catalog_revision":8,"models":[{"provider":"openai","model":"gpt-5.6-luna","aliases":["gpt-5.6-luna"],"rates":[{"direction":"output","modality":"text","cache_role":"none","unit":"tokens","scale":1000000,"usd":"0.88","effective_from":"1970-01-01T00:00:00Z"}]}]}`
	if err := os.WriteFile(configured, []byte(configuredFixture), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "assets", "runtime", "internal_model_pricing.json"), []byte(internalFixture), 0600); err != nil {
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
	dataDir := filepath.Join(dir, "assets", "runtime")
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
	if err := os.MkdirAll(filepath.Join(dir, "assets", "runtime"), 0700); err != nil {
		t.Fatal(err)
	}
	configured := filepath.Join(dir, "configured.json")
	configuredFixture := `{"schema_version":1,"catalog_revision":7,"models":[{"provider":"openai","model":"company-luna","aliases":["company-luna","gpt-5.6-luna"],"rates":[{"direction":"output","modality":"text","cache_role":"none","unit":"tokens","scale":1000000,"usd":"1.23","effective_from":"1970-01-01T00:00:00Z"}]}]}`
	internalFixture := `{"schema_version":1,"catalog_revision":8,"models":[{"provider":"openai","model":"gpt-5.6-luna","aliases":["gpt-5.6-luna"],"rates":[{"direction":"output","modality":"text","cache_role":"none","unit":"tokens","scale":1000000,"usd":"9.00","effective_from":"1970-01-01T00:00:00Z"}]}]}`
	if err := os.WriteFile(configured, []byte(configuredFixture), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "assets", "runtime", "internal_model_pricing.json"), []byte(internalFixture), 0600); err != nil {
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

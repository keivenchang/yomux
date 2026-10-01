package stats

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"html"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"
)

const (
	maxPricingSourceBytes = 1_000_000
	pricingFetchTimeout   = 10 * time.Second
	pricingOpenAIURL      = "https://developers.openai.com/api/docs/pricing"
	pricingAnthropicURL   = "https://platform.claude.com/docs/en/about-claude/pricing"
	pricingGoogleURL      = "https://ai.google.dev/gemini-api/docs/pricing"
	pricingLiteLLMURL     = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
	pricingOpenRouterURL  = "https://openrouter.ai/api/v1/models"
)

type pricingSourceCheck struct {
	ETag          string `json:"etag,omitempty"`
	LastModified  string `json:"last_modified,omitempty"`
	Digest        string `json:"digest,omitempty"`
	CheckedAt     string `json:"checked_at,omitempty"`
	ParserVersion string `json:"parser_version,omitempty"`
	Status        string `json:"status,omitempty"`
}

type pricingCacheEnvelope struct {
	Catalog             *PricingCatalog               `json:"catalog"`
	SourceChecks        map[string]pricingSourceCheck `json:"source_checks,omitempty"`
	Failure             *pricingRefreshFailure        `json:"refresh_failure,omitempty"`
	InvalidationPending bool                          `json:"pricing_invalidation_pending,omitempty"`
}

type pricingRefreshFailure struct {
	Warnings []string `json:"warnings"`
}

const (
	maxPricingFailureWarnings = 32
	maxPricingFailureBytes    = 500
)

type pricingFetchResponse struct {
	Status             int
	ETag, LastModified string
	Body               []byte
}

type pricingSourceFetcher func(context.Context, string, map[string]string) (pricingFetchResponse, error)

func boundedPricingFailure(source, detail string) string {
	message := source + ": " + detail
	if len(message) > 500 {
		message = message[:500]
	}
	return message
}

func pricingPublicSourceKind(adapterKind string) string {
	if adapterKind == "machine" {
		return "official"
	}
	return adapterKind
}

func (c *PricingCatalog) RefreshFromSources(ctx context.Context) error {
	return c.refreshFromSources(ctx, defaultPricingSourceAdapters(), fetchPricingSource)
}

func (c *PricingCatalog) refreshFromSources(ctx context.Context, adapters []pricingSourceAdapter, fetch pricingSourceFetcher) (err error) {
	c.refreshMu.Lock()
	defer c.refreshMu.Unlock()
	c.mu.RLock()
	cachePath := c.refreshCache
	invalidationPending := c.pricingInvalidationPending
	c.mu.RUnlock()
	defer func() {
		c.mu.Lock()
		c.refreshFailed = err != nil
		if err != nil {
			c.refreshStatus = "failed"
			c.refreshWarnings = boundedPricingWarnings([]string{err.Error()})
		}
		models := clonePricingModels(c.Models)
		revision, generatedAt, schemaVersion := c.CatalogRevision, c.GeneratedAt, c.SchemaVersion
		checks := clonePricingSourceChecks(c.sourceChecks)
		warnings := append([]string(nil), c.refreshWarnings...)
		c.mu.Unlock()
		if err != nil && cachePath != "" {
			preserved := &PricingCatalog{SchemaVersion: schemaVersion, CatalogRevision: revision, GeneratedAt: generatedAt, Models: models}
			failure := &pricingRefreshFailure{Warnings: warnings}
			if persistErr := writePricingCache(cachePath, preserved, checks, failure, invalidationPending); persistErr != nil {
				persistMessage := boundedPricingFailure("pricing failure state persistence", persistErr.Error())
				c.mu.Lock()
				c.refreshWarnings = boundedPricingWarnings(append(c.refreshWarnings, persistMessage))
				c.mu.Unlock()
				err = fmt.Errorf("%w; %s", err, persistMessage)
			}
		}
	}()
	if fetch == nil {
		return fmt.Errorf("pricing source fetcher is required")
	}
	now := time.Now().UTC()
	checkedAt := now.Format(time.RFC3339)
	c.mu.RLock()
	currentRevision := c.CatalogRevision
	currentModels := clonePricingModels(c.Models)
	cachePath = c.refreshCache
	invalidationPending = c.pricingInvalidationPending
	currentChecks := clonePricingSourceChecks(c.sourceChecks)
	c.mu.RUnlock()
	if currentChecks == nil {
		currentChecks = map[string]pricingSourceCheck{}
	}
	var authoritative, corroboration []PricingModel
	var failures []string
	anyUpdated, authoritativeUpdated := false, false
	for _, adapter := range adapters {
		if safePricingSourceURL(adapter.url) == "" {
			failures = append(failures, boundedPricingFailure(adapter.name, "source URL is not allowlisted"))
			continue
		}
		previous := currentChecks[adapter.url]
		requestHeaders := map[string]string{}
		if previous.ETag != "" {
			requestHeaders["If-None-Match"] = previous.ETag
		}
		if previous.LastModified != "" {
			requestHeaders["If-Modified-Since"] = previous.LastModified
		}
		response, fetchErr := fetch(ctx, adapter.url, requestHeaders)
		if fetchErr != nil {
			failures = append(failures, boundedPricingFailure(adapter.name, fetchErr.Error()))
			continue
		}
		if response.Status == http.StatusNotModified {
			cached := pricingModelsForSource(currentModels, adapter.url, checkedAt)
			if len(cached) == 0 && adapter.kind != "corroboration" {
				failures = append(failures, boundedPricingFailure(adapter.name, "source returned 304 without cached catalog data"))
				continue
			}
			if adapter.kind == "corroboration" {
				corroboration = append(corroboration, cached...)
			} else {
				authoritative = append(authoritative, cached...)
			}
			previous.CheckedAt = checkedAt
			previous.Status = "accepted"
			previous.ParserVersion = adapter.parserVersion
			if response.ETag != "" {
				previous.ETag = response.ETag
			}
			if response.LastModified != "" {
				previous.LastModified = response.LastModified
			}
			currentChecks[adapter.url] = previous
			continue
		}
		if response.Status != http.StatusOK {
			failures = append(failures, boundedPricingFailure(adapter.name, fmt.Sprintf("pricing source HTTP %d", response.Status)))
			continue
		}
		models, parseErr := adapter.parse(response.Body)
		if parseErr != nil {
			failures = append(failures, boundedPricingFailure(adapter.name, parseErr.Error()))
			continue
		}
		anyUpdated = true
		for i := range models {
			models[i].Source.CheckedAt = checkedAt
			// Machine feeds participate in the accepted public catalog like
			// provider pages. Adapter identity/version remains in sourceChecks.
			models[i].Source.Kind = pricingPublicSourceKind(adapter.kind)
		}
		digest := sha256.Sum256(response.Body)
		currentChecks[adapter.url] = pricingSourceCheck{ETag: response.ETag, LastModified: response.LastModified, Digest: hex.EncodeToString(digest[:]), CheckedAt: checkedAt, ParserVersion: adapter.parserVersion, Status: "accepted"}
		if adapter.kind == "corroboration" {
			corroboration = append(corroboration, models...)
		} else if adapter.kind == "official" || adapter.kind == "machine" {
			authoritative = append(authoritative, models...)
			authoritativeUpdated = true
		} else {
			failures = append(failures, boundedPricingFailure(adapter.name, "unknown source kind"))
		}
	}
	if !anyUpdated && len(failures) > 0 {
		return fmt.Errorf("pricing refresh failed: %s", failures[0])
	}
	if anyUpdated && !authoritativeUpdated {
		return fmt.Errorf("no accepted authoritative pricing catalog")
	}
	if len(authoritative) == 0 {
		if len(failures) > 0 {
			return fmt.Errorf("no accepted authoritative pricing catalog: %s", failures[0])
		}
		return fmt.Errorf("no accepted authoritative pricing catalog")
	}
	if pricingModelsDisagree(authoritative, corroboration) {
		return fmt.Errorf("pricing source disagreement requires review")
	}
	var models []PricingModel
	revision := currentRevision
	generatedAt := ""
	if anyUpdated {
		models = mergePricingModels(authoritative, currentModels)
		revision++
		generatedAt = now.Format(time.RFC3339)
		invalidationPending = true
	} else {
		models = currentModels
		for i := range models {
			if _, checked := currentChecks[models[i].Source.URL]; checked {
				models[i].Source.CheckedAt = currentChecks[models[i].Source.URL].CheckedAt
			}
		}
		c.mu.RLock()
		generatedAt = c.GeneratedAt
		c.mu.RUnlock()
	}
	merged := &PricingCatalog{SchemaVersion: pricingSchemaVersion, CatalogRevision: revision, GeneratedAt: generatedAt, Models: models}
	if err := merged.validate(); err != nil {
		return fmt.Errorf("validate refreshed pricing catalog: %w", err)
	}
	if cachePath != "" {
		if err := writePricingCache(cachePath, merged, currentChecks, nil, invalidationPending); err != nil {
			return err
		}
	}
	if anyUpdated {
		c.installRefreshedCatalog(merged)
	}
	c.mu.Lock()
	if !anyUpdated {
		c.Models = models
		c.GeneratedAt = generatedAt
	}
	c.sourceChecks = currentChecks
	c.pricingInvalidationPending = invalidationPending
	c.refreshStatus = "updated"
	if !anyUpdated {
		c.refreshStatus = "unchanged"
	}
	c.refreshWarnings = append([]string(nil), failures...)
	c.mu.Unlock()
	return nil
}

func fetchPricingSource(ctx context.Context, sourceURL string, requestHeaders map[string]string) (pricingFetchResponse, error) {
	if safePricingSourceURL(sourceURL) == "" {
		return pricingFetchResponse{}, fmt.Errorf("pricing source URL is not allowlisted")
	}
	client := &http.Client{Timeout: pricingFetchTimeout, CheckRedirect: func(request *http.Request, via []*http.Request) error {
		if len(via) >= 5 || request.URL.Scheme != "https" || safePricingSourceURL(request.URL.String()) == "" {
			return fmt.Errorf("pricing source redirect is not allowlisted")
		}
		return nil
	}}
	return fetchPricingSourceWithClient(ctx, sourceURL, requestHeaders, client)
}

func fetchPricingSourceWithClient(ctx context.Context, sourceURL string, requestHeaders map[string]string, client *http.Client) (pricingFetchResponse, error) {
	if safePricingSourceURL(sourceURL) == "" {
		return pricingFetchResponse{}, fmt.Errorf("pricing source URL is not allowlisted")
	}
	if client == nil {
		return pricingFetchResponse{}, fmt.Errorf("pricing source HTTP client is required")
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, sourceURL, nil)
	if err != nil {
		return pricingFetchResponse{}, err
	}
	request.Header.Set("User-Agent", "YOmux pricing refresh")
	request.Header.Set("Accept", "application/json,text/html;q=0.9")
	for name, value := range requestHeaders {
		if name == "If-None-Match" || name == "If-Modified-Since" {
			request.Header.Set(name, value)
		}
	}
	response, err := client.Do(request)
	if err != nil {
		return pricingFetchResponse{}, fmt.Errorf("pricing source unavailable: %w", err)
	}
	defer response.Body.Close()
	result := pricingFetchResponse{Status: response.StatusCode, ETag: response.Header.Get("ETag"), LastModified: response.Header.Get("Last-Modified")}
	if response.StatusCode == http.StatusNotModified {
		return result, nil
	}
	if response.StatusCode != http.StatusOK {
		return pricingFetchResponse{}, fmt.Errorf("pricing source HTTP %d", response.StatusCode)
	}
	body, err := io.ReadAll(io.LimitReader(response.Body, maxPricingSourceBytes+1))
	if err != nil {
		return pricingFetchResponse{}, err
	}
	if len(body) > maxPricingSourceBytes {
		return pricingFetchResponse{}, fmt.Errorf("pricing source body exceeds limit")
	}
	result.Body = body
	return result, nil
}

func mergePricingModels(incoming, existing []PricingModel) []PricingModel {
	var models []PricingModel
	claimed := map[string]struct{}{}
	appendUnique := func(model PricingModel) {
		for _, alias := range model.Aliases {
			if _, exists := claimed[model.Provider+"\x00"+alias]; exists {
				return
			}
		}
		models = append(models, model)
		for _, alias := range model.Aliases {
			claimed[model.Provider+"\x00"+alias] = struct{}{}
		}
	}
	for _, model := range incoming {
		appendUnique(model)
	}
	for _, model := range existing {
		if pricingSourceKind(model.Source) == "official" || pricingSourceKind(model.Source) == "machine" || pricingSourceKind(model.Source) == "corroboration" {
			// Keep prior authoritative evidence for aliases omitted by a partial
			// upstream response, matching Python's carry-forward behavior.
			if pricingSourceKind(model.Source) != "official" {
				continue
			}
		}
		appendUnique(model)
	}
	return models
}

func clonePricingModels(models []PricingModel) []PricingModel {
	cloned := make([]PricingModel, len(models))
	for i, model := range models {
		cloned[i] = model
		cloned[i].Aliases = append([]string(nil), model.Aliases...)
		cloned[i].Rates = append([]PricingRate(nil), model.Rates...)
	}
	return cloned
}

func pricingModelsDisagree(authoritative, corroboration []PricingModel) bool {
	type rateKey struct{ provider, model, direction, modality, cache, unit, profile, tier string }
	known := map[rateKey]string{}
	for _, model := range authoritative {
		for _, rate := range model.Rates {
			known[rateKey{model.Provider, model.Model, rate.Direction, rate.Modality, rate.CacheRole, rate.Unit, rate.Profile, rate.ServiceTier}] = rate.USD
		}
	}
	for _, model := range corroboration {
		for _, rate := range model.Rates {
			key := rateKey{model.Provider, model.Model, rate.Direction, rate.Modality, rate.CacheRole, rate.Unit, rate.Profile, rate.ServiceTier}
			if value, ok := known[key]; ok && value != rate.USD {
				return true
			}
		}
	}
	return false
}

func readPricingCache(path string) (*PricingCatalog, map[string]pricingSourceCheck, *pricingRefreshFailure, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, nil, nil, err
	}
	var envelope pricingCacheEnvelope
	if err := json.Unmarshal(data, &envelope); err != nil {
		return nil, nil, nil, fmt.Errorf("decode pricing refresh cache: %w", err)
	}
	if envelope.Catalog == nil {
		var legacy PricingCatalog
		if err := json.Unmarshal(data, &legacy); err != nil {
			return nil, nil, nil, fmt.Errorf("decode pricing refresh cache: %w", err)
		}
		envelope.Catalog = &legacy
	}
	if err := envelope.Catalog.validate(); err != nil {
		return nil, nil, nil, err
	}
	envelope.Catalog.path = path
	envelope.Catalog.pricingInvalidationPending = envelope.InvalidationPending
	envelope.Catalog.seedRevision = pricingCatalogSeedRevision(envelope.Catalog.Models, envelope.Catalog.CatalogRevision)
	if envelope.SourceChecks == nil {
		envelope.SourceChecks = map[string]pricingSourceCheck{}
	}
	if err := validatePricingSourceChecks(envelope.SourceChecks); err != nil {
		return nil, nil, nil, err
	}
	if envelope.Failure != nil {
		envelope.Failure.Warnings = boundedPricingWarnings(envelope.Failure.Warnings)
		if len(envelope.Failure.Warnings) == 0 {
			return nil, nil, nil, fmt.Errorf("pricing cache contains an empty refresh failure")
		}
	}
	return envelope.Catalog, envelope.SourceChecks, envelope.Failure, nil
}

func boundedPricingWarnings(warnings []string) []string {
	if len(warnings) > maxPricingFailureWarnings {
		warnings = warnings[:maxPricingFailureWarnings]
	}
	bounded := make([]string, 0, len(warnings))
	for _, warning := range warnings {
		warning = strings.TrimSpace(warning)
		if len(warning) > maxPricingFailureBytes {
			warning = warning[:maxPricingFailureBytes]
		}
		if warning != "" {
			bounded = append(bounded, warning)
		}
	}
	return bounded
}

func validatePricingSourceChecks(checks map[string]pricingSourceCheck) error {
	for sourceURL, check := range checks {
		if safePricingSourceURL(sourceURL) == "" {
			return fmt.Errorf("pricing source cache contains a non-allowlisted URL")
		}
		if len(check.ETag) > 1024 || strings.ContainsAny(check.ETag, "\r\n") || len(check.LastModified) > 128 || strings.ContainsAny(check.LastModified, "\r\n") {
			return fmt.Errorf("pricing source cache contains invalid validators")
		}
		if len(check.Digest) != 0 && len(check.Digest) != sha256.Size*2 {
			return fmt.Errorf("pricing source cache contains an invalid digest")
		}
		if check.Digest != "" {
			if _, err := hex.DecodeString(check.Digest); err != nil {
				return fmt.Errorf("pricing source cache contains an invalid digest")
			}
		}
		if check.CheckedAt != "" {
			if _, err := time.Parse(time.RFC3339, check.CheckedAt); err != nil {
				return fmt.Errorf("pricing source cache contains an invalid check timestamp")
			}
		}
		if len(check.ParserVersion) > 64 || (check.Status != "" && check.Status != "accepted") {
			return fmt.Errorf("pricing source cache contains invalid source status")
		}
	}
	return nil
}

func writePricingCache(path string, catalog *PricingCatalog, checks map[string]pricingSourceCheck, failure *pricingRefreshFailure, invalidationPending bool) error {
	if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		return fmt.Errorf("create pricing cache directory: %w", err)
	}
	if failure != nil {
		failure = &pricingRefreshFailure{Warnings: boundedPricingWarnings(failure.Warnings)}
		if len(failure.Warnings) == 0 {
			return fmt.Errorf("pricing refresh failure state must contain a warning")
		}
	}
	data, err := json.Marshal(pricingCacheEnvelope{Catalog: catalog, SourceChecks: checks, Failure: failure, InvalidationPending: invalidationPending})
	if err != nil {
		return err
	}
	file, err := os.CreateTemp(filepath.Dir(path), ".pricing-cache-*.tmp")
	if err != nil {
		return fmt.Errorf("create pricing cache: %w", err)
	}
	tempPath := file.Name()
	defer os.Remove(tempPath)
	if err := file.Chmod(0600); err != nil {
		file.Close()
		return err
	}
	if _, err := file.Write(data); err != nil {
		file.Close()
		return err
	}
	if err := file.Sync(); err != nil {
		file.Close()
		return err
	}
	if err := file.Close(); err != nil {
		return err
	}
	if err := os.Rename(tempPath, path); err != nil {
		return fmt.Errorf("install pricing cache: %w", err)
	}
	directory, err := os.Open(filepath.Dir(path))
	if err != nil {
		return err
	}
	defer directory.Close()
	return directory.Sync()
}

func clonePricingSourceChecks(checks map[string]pricingSourceCheck) map[string]pricingSourceCheck {
	if checks == nil {
		return nil
	}
	cloned := make(map[string]pricingSourceCheck, len(checks))
	for source, check := range checks {
		cloned[source] = check
	}
	return cloned
}

func pricingModelsForSource(models []PricingModel, sourceURL, checkedAt string) []PricingModel {
	var matches []PricingModel
	for _, model := range models {
		if model.Source.URL != sourceURL {
			continue
		}
		copy := clonePricingModels([]PricingModel{model})[0]
		copy.Source.CheckedAt = checkedAt
		matches = append(matches, copy)
	}
	return matches
}

type pricingSourceAdapter struct {
	name, url, kind string
	parserVersion   string
	parse           func([]byte) ([]PricingModel, error)
}

func defaultPricingSourceAdapters() []pricingSourceAdapter {
	return []pricingSourceAdapter{
		{name: "openai", url: pricingOpenAIURL, kind: "official", parserVersion: "1", parse: func(body []byte) ([]PricingModel, error) {
			return parseProviderPricingTable(body, "openai", pricingOpenAIURL)
		}},
		{name: "anthropic", url: pricingAnthropicURL, kind: "official", parserVersion: "1", parse: func(body []byte) ([]PricingModel, error) {
			return parseProviderPricingTable(body, "anthropic", pricingAnthropicURL)
		}},
		{name: "google", url: pricingGoogleURL, kind: "official", parserVersion: "1", parse: func(body []byte) ([]PricingModel, error) {
			return parseProviderPricingTable(body, "google", pricingGoogleURL)
		}},
		{name: "litellm", url: pricingLiteLLMURL, kind: "machine", parserVersion: "1", parse: parseLiteLLMPricing},
		{name: "openrouter", url: pricingOpenRouterURL, kind: "corroboration", parserVersion: "1", parse: parseOpenRouterPricing},
	}
}

var pricingTagPattern = regexp.MustCompile(`(?s)<(/?)([a-zA-Z0-9]+)(?:\s[^<>]*?)?>`)
var pricingDecimalPattern = regexp.MustCompile(`^([+-]?)([0-9]*)(?:\.([0-9]*))?(?:[eE]([+-]?[0-9]+))?$`)

// parsePricingRows extracts only table rows and skips script/style contents;
// unknown layouts fail closed in parseProviderPricingTable.
func parsePricingRows(body []byte) ([][]string, error) {
	if !bytes.Equal(bytes.ToValidUTF8(body, nil), body) {
		return nil, fmt.Errorf("pricing page is not UTF-8")
	}
	text := string(body)
	var rows [][]string
	var row []string
	var cell strings.Builder
	cellOpen := false
	insideScript := ""
	previousTagEnd := 0
	for _, loc := range pricingTagPattern.FindAllStringSubmatchIndex(text, -1) {
		if insideScript == "" && cellOpen {
			cell.WriteString(html.UnescapeString(text[previousTagEnd:loc[0]]))
		}
		tag := strings.ToLower(text[loc[4]:loc[5]])
		closing := text[loc[2]:loc[3]] == "/"
		if insideScript != "" {
			if closing && tag == insideScript {
				insideScript = ""
			}
			previousTagEnd = loc[1]
			continue
		}
		if !closing && (tag == "script" || tag == "style") {
			insideScript = tag
			previousTagEnd = loc[1]
			continue
		}
		if tag == "tr" {
			if closing {
				if len(row) > 0 {
					rows = append(rows, row)
				}
				row = nil
			} else {
				row = []string{}
			}
		} else if (tag == "td" || tag == "th") && row != nil {
			if closing && cellOpen {
				row = append(row, strings.Join(strings.Fields(cell.String()), " "))
				cell.Reset()
				cellOpen = false
			} else if !closing {
				cell.Reset()
				cellOpen = true
			}
		}
		previousTagEnd = loc[1]
	}
	return rows, nil
}

func parseProviderPricingTable(body []byte, provider, sourceURL string) ([]PricingModel, error) {
	rows, err := parsePricingRows(body)
	if err != nil {
		return nil, err
	}
	headerIndex := -1
	for i, row := range rows {
		if len(row) > 0 && strings.ToLower(strings.TrimSpace(row[0])) == "model" {
			headerIndex = i
			break
		}
	}
	if headerIndex < 0 {
		return nil, fmt.Errorf("reviewed pricing table header missing")
	}
	headers := make([]string, len(rows[headerIndex]))
	for i, header := range rows[headerIndex] {
		headers[i] = strings.ToLower(strings.TrimSpace(header))
	}
	column := func(names ...string) int {
		for _, name := range names {
			for i, header := range headers {
				if header == name {
					return i
				}
			}
		}
		return -1
	}
	input, output := column("input", "base input tokens"), column("output", "output tokens")
	if input < 0 || output < 0 {
		return nil, fmt.Errorf("reviewed pricing table columns changed")
	}
	cached, write5m, write1h := column("cached input", "cache hits & refreshes"), column("cache writes", "5m cache writes"), column("1h cache writes")
	profile, tier := column("pricing profile", "profile"), column("service tier")
	var models []PricingModel
	checkedAt := ""
	for _, row := range rows[headerIndex+1:] {
		if len(row) > 0 && strings.ToLower(strings.TrimSpace(row[0])) == "model" {
			break
		}
		if len(row) != len(headers) || strings.TrimSpace(row[0]) == "" {
			continue
		}
		get := func(index int) string {
			if index < 0 {
				return ""
			}
			return strings.TrimSpace(row[index])
		}
		inputUSD, inputOK := optionalUSDPerMillion(get(input))
		outputUSD, outputOK := optionalUSDPerMillion(get(output))
		if !inputOK || !outputOK {
			continue
		}
		model := strings.TrimSpace(row[0])
		profileName, tierName := strings.ToLower(get(profile)), strings.ToLower(get(tier))
		if profileName == "" {
			profileName = "default"
		}
		if tierName == "" {
			tierName = "default"
		}
		rates := []PricingRate{pricingTableRate("input", "none", inputUSD, profileName, tierName), pricingTableRate("output", "none", outputUSD, profileName, tierName)}
		for _, item := range []struct {
			index int
			role  string
		}{{cached, "read"}, {write5m, "write_5m"}, {write1h, "write_1h"}} {
			if usd, ok := optionalUSDPerMillion(get(item.index)); ok {
				rates = append(rates, pricingTableRate("input", item.role, usd, profileName, tierName))
			}
		}
		aliases := []string{model}
		if provider == "anthropic" {
			base := strings.ToLower(strings.SplitN(model, "(", 2)[0])
			words := regexp.MustCompile(`[^a-z0-9]+`).Split(base, -1)
			var parts []string
			for _, word := range words {
				if word != "" {
					parts = append(parts, word)
				}
			}
			slug := strings.Join(parts, "-")
			if slug != "" && slug != model {
				aliases = append(aliases, slug)
			}
		}
		models = append(models, PricingModel{Provider: provider, Model: model, Aliases: aliases, Source: PricingSource{URL: sourceURL, Kind: "official", CheckedAt: checkedAt}, Rates: rates})
	}
	if len(models) == 0 {
		return nil, fmt.Errorf("reviewed pricing table has no models")
	}
	return models, nil
}

func pricingTableRate(direction, cacheRole, usd, profile, tier string) PricingRate {
	return PricingRate{Direction: direction, Modality: "text", CacheRole: cacheRole, Unit: "tokens", Profile: profile, ServiceTier: tier, Scale: 1_000_000, USD: usd, EffectiveFrom: "1970-01-01T00:00:00Z"}
}

func optionalUSDPerMillion(value string) (string, bool) {
	cleaned := strings.TrimSpace(strings.ToLower(value))
	if cleaned == "" || cleaned == "-" || cleaned == "—" || cleaned == "n/a" || cleaned == "not available" || strings.Contains(cleaned, "free") || strings.Contains(cleaned, "contact") {
		return "", false
	}
	cleaned = strings.TrimSpace(strings.ReplaceAll(strings.ReplaceAll(value, "$", ""), ",", ""))
	lower := strings.ToLower(cleaned)
	for _, suffix := range []string{"/ mtok", "per mtok"} {
		if strings.HasSuffix(lower, suffix) {
			cleaned = strings.TrimSpace(cleaned[:len(cleaned)-len(suffix)])
			break
		}
	}
	return normalizePricingDecimal(cleaned)
}

func multiplyUSDByMillion(value string) (string, bool) {
	cleaned, ok := normalizePricingDecimal(value)
	if !ok {
		return "", false
	}
	parts := strings.SplitN(cleaned, ".", 2)
	whole, fraction := parts[0], ""
	if len(parts) == 2 {
		fraction = parts[1]
	}
	digits := whole + fraction
	point := len(whole) + 6
	if point >= len(digits) {
		digits += strings.Repeat("0", point-len(digits))
		return normalizePricingDecimal(digits)
	}
	return normalizePricingDecimal(digits[:point] + "." + digits[point:])
}

func normalizePricingDecimal(value string) (string, bool) {
	match := pricingDecimalPattern.FindStringSubmatch(strings.TrimSpace(value))
	if match == nil || (match[2] == "" && match[3] == "") || match[1] == "-" {
		return "", false
	}
	exponent := 0
	if match[4] != "" {
		parsed, err := strconv.Atoi(match[4])
		if err != nil || parsed < -1000 || parsed > 1000 {
			return "", false
		}
		exponent = parsed
	}
	whole, fraction := match[2], match[3]
	digits := strings.TrimLeft(whole+fraction, "0")
	if digits == "" {
		return "0", true
	}
	decimalPlaces := len(fraction) - exponent
	if decimalPlaces <= 0 {
		digits += strings.Repeat("0", -decimalPlaces)
		return digits, true
	}
	if decimalPlaces >= len(digits) {
		digits = strings.Repeat("0", decimalPlaces-len(digits)+1) + digits
	}
	point := len(digits) - decimalPlaces
	whole, fraction = digits[:point], strings.TrimRight(digits[point:], "0")
	whole = strings.TrimLeft(whole, "0")
	if whole == "" {
		whole = "0"
	}
	if fraction == "" {
		return whole, true
	}
	return whole + "." + fraction, true
}

func parsePricingJSON(body []byte, target any) error {
	decoder := json.NewDecoder(bytes.NewReader(body))
	decoder.UseNumber()
	if err := decoder.Decode(target); err != nil {
		return err
	}
	var trailing any
	if err := decoder.Decode(&trailing); err != io.EOF {
		if err == nil {
			return fmt.Errorf("unexpected trailing JSON")
		}
		return err
	}
	return nil
}

func parseOpenRouterPricing(body []byte) ([]PricingModel, error) {
	var payload struct {
		Data []struct {
			ID      string         `json:"id"`
			Pricing map[string]any `json:"pricing"`
		} `json:"data"`
	}
	if err := parsePricingJSON(body, &payload); err != nil {
		return nil, fmt.Errorf("invalid OpenRouter price JSON: %w", err)
	}
	if payload.Data == nil {
		return nil, fmt.Errorf("OpenRouter data array missing")
	}
	var models []PricingModel
	for _, entry := range payload.Data {
		if strings.TrimSpace(entry.ID) == "" {
			continue
		}
		var rates []PricingRate
		for _, field := range []struct{ key, direction, cache string }{{"prompt", "input", "none"}, {"completion", "output", "none"}, {"input_cache_read", "input", "read"}, {"input_cache_write", "input", "write"}} {
			usd, ok := pricingJSONNumber(entry.Pricing[field.key])
			if !ok {
				continue
			}
			millionUSD, valid := multiplyUSDByMillion(usd)
			if !valid {
				continue
			}
			rates = append(rates, pricingTableRate(field.direction, field.cache, millionUSD, "default", "default"))
		}
		if len(rates) > 0 {
			models = append(models, PricingModel{Provider: "openrouter", Model: entry.ID, Aliases: []string{entry.ID}, Source: PricingSource{URL: pricingOpenRouterURL, Kind: "corroboration"}, Rates: rates})
		}
	}
	return models, nil
}

func parseLiteLLMPricing(body []byte) ([]PricingModel, error) {
	var payload map[string]map[string]any
	if err := parsePricingJSON(body, &payload); err != nil {
		return nil, fmt.Errorf("invalid LiteLLM price map: %w", err)
	}
	if payload == nil {
		return nil, fmt.Errorf("LiteLLM price map missing")
	}
	var models []PricingModel
	for model, details := range payload {
		if strings.TrimSpace(model) == "" {
			continue
		}
		var rates []PricingRate
		for _, field := range []struct{ key, direction, cache string }{{"input_cost_per_token", "input", "none"}, {"output_cost_per_token", "output", "none"}, {"cache_read_input_token_cost", "input", "read"}, {"cache_creation_input_token_cost", "input", "write"}} {
			usd, ok := pricingJSONNumber(details[field.key])
			if !ok {
				continue
			}
			millionUSD, valid := multiplyUSDByMillion(usd)
			if !valid {
				continue
			}
			rates = append(rates, pricingTableRate(field.direction, field.cache, millionUSD, "default", "default"))
		}
		if len(rates) == 0 {
			continue
		}
		provider, _ := details["litellm_provider"].(string)
		if strings.TrimSpace(provider) == "" {
			provider = "litellm"
		}
		models = append(models, PricingModel{Provider: provider, Model: model, Aliases: []string{model}, Source: PricingSource{URL: pricingLiteLLMURL, Kind: "machine"}, Rates: rates})
	}
	if len(models) == 0 {
		return nil, fmt.Errorf("LiteLLM price map has no usable models")
	}
	return models, nil
}

func pricingJSONNumber(value any) (string, bool) {
	switch typed := value.(type) {
	case json.Number:
		return typed.String(), true
	case string:
		return typed, typed != ""
	default:
		return "", false
	}
}

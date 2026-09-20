package stats

import (
	"encoding/json"
	"errors"
	"fmt"
	"math/big"
	"net/url"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"
)

const pricingSchemaVersion = 1

const pricingStaleSeconds = 30 * 24 * 60 * 60

type PricingCatalog struct {
	SchemaVersion   int            `json:"schema_version"`
	CatalogRevision int64          `json:"catalog_revision"`
	GeneratedAt     string         `json:"generated_at,omitempty"`
	Models          []PricingModel `json:"models"`
	path            string
	root            string
	configured      string
	seedRevision    int64
	refreshFailed   bool
	mu              sync.RWMutex
}

type PricingModel struct {
	Provider string        `json:"provider"`
	Model    string        `json:"model"`
	Aliases  []string      `json:"aliases"`
	Source   PricingSource `json:"source,omitempty"`
	Rates    []PricingRate `json:"rates"`
}

type PricingSource struct {
	URL       string `json:"url,omitempty"`
	Kind      string `json:"kind,omitempty"`
	CheckedAt string `json:"checked_at,omitempty"`
}

type PricingRate struct {
	Direction     string `json:"direction"`
	Modality      string `json:"modality"`
	CacheRole     string `json:"cache_role"`
	Unit          string `json:"unit"`
	Profile       string `json:"profile,omitempty"`
	ServiceTier   string `json:"service_tier,omitempty"`
	Scale         int64  `json:"scale"`
	USD           string `json:"usd"`
	EffectiveFrom string `json:"effective_from"`
}

type ResolvedPricingRate struct {
	Provider, Model, Alias string
	Rate                   PricingRate
	Source                 PricingSource
	CatalogRevision        int64
}

func LoadPricingCatalog(path string) (*PricingCatalog, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read pricing catalog: %w", err)
	}
	var catalog PricingCatalog
	if err := json.Unmarshal(data, &catalog); err != nil {
		return nil, fmt.Errorf("decode pricing catalog: %w", err)
	}
	if err := catalog.validate(); err != nil {
		return nil, err
	}
	catalog.path = path
	catalog.seedRevision = pricingCatalogSeedRevision(catalog.Models, catalog.CatalogRevision)
	return &catalog, nil
}

func pricingSourceKind(source PricingSource) string {
	kind := strings.TrimSpace(source.Kind)
	if kind == "" {
		return "seed"
	}
	return kind
}

func pricingCatalogSeedRevision(models []PricingModel, catalogRevision int64) int64 {
	seedRevision := int64(0)
	for _, model := range models {
		if pricingSourceKind(model.Source) == "seed" && catalogRevision > seedRevision {
			seedRevision = catalogRevision
		}
	}
	return seedRevision
}

func LoadRepositoryPricingCatalog(root, configured string) (*PricingCatalog, error) {
	paths := []string{}
	if configured != "" {
		paths = append(paths, configured)
	}
	if root != "" {
		paths = append(paths, filepath.Join(root, "yolomux_lib", "data", "model_pricing.json"))
		paths = append(paths, filepath.Join(root, "yolomux_lib", "data", "internal_model_pricing.json"))
	}
	var loaded []*PricingCatalog
	configuredOwnsLuna := false
	for _, path := range paths {
		catalog, err := LoadPricingCatalog(path)
		if err == nil {
			// A repository's bundled catalogs are the offline seed, even though
			// their embedded source metadata identifies the upstream authority.
			// Keep that provenance distinct from an explicitly configured live
			// catalog so Go emits the same evidence as Python for the default
			// repository startup path.
			if configured == "" && root != "" {
				bundledPath := filepath.Join(root, "yolomux_lib", "data")
				if path == filepath.Join(bundledPath, "model_pricing.json") || path == filepath.Join(bundledPath, "internal_model_pricing.json") {
					for index := range catalog.Models {
						catalog.Models[index].Source.Kind = "seed"
					}
				}
			}
			catalog.seedRevision = pricingCatalogSeedRevision(catalog.Models, catalog.CatalogRevision)
			loaded = append(loaded, catalog)
			if configured != "" && path == configured {
				for _, model := range catalog.Models {
					if model.Provider == "openai" && (model.Model == "gpt-5.6-luna" || containsPricingAlias(model.Aliases, "gpt-5.6-luna")) {
						configuredOwnsLuna = true
						break
					}
				}
			}
			continue
		}
		// LoadPricingCatalog wraps the underlying PathError with context. Use
		// errors.Is so an absent optional fallback remains ignorable while real
		// catalog failures still fail startup.
		if !errors.Is(err, os.ErrNotExist) {
			return nil, err
		}
	}
	if len(loaded) > 0 {
		result := loaded[0]
		for _, extra := range loaded[1:] {
			if extra.CatalogRevision > result.CatalogRevision {
				result.CatalogRevision = extra.CatalogRevision
			}
			// The packaged Python seed deliberately replaces the public Luna
			// model with the newer internal rate set before deduplicating the
			// remaining aliases. Keep that same precedence here; otherwise the
			// older public $6 rate silently wins over the internal $0.8808 rate.
			if filepath.Base(extra.path) == "internal_model_pricing.json" && !configuredOwnsLuna {
				for _, replacement := range extra.Models {
					if replacement.Provider != "openai" || replacement.Model != "gpt-5.6-luna" {
						continue
					}
					filtered := result.Models[:0]
					for _, existing := range result.Models {
						if existing.Provider == replacement.Provider && existing.Model == replacement.Model {
							continue
						}
						filtered = append(filtered, existing)
					}
					result.Models = append(filtered, replacement)
				}
			}
			seen := map[string]bool{}
			for _, model := range result.Models {
				for _, alias := range model.Aliases {
					seen[model.Provider+"\x00"+alias] = true
				}
			}
			for _, model := range extra.Models {
				if filepath.Base(extra.path) == "internal_model_pricing.json" && model.Provider == "openai" && model.Model == "gpt-5.6-luna" {
					continue
				}
				duplicate := false
				for _, alias := range model.Aliases {
					if seen[model.Provider+"\x00"+alias] {
						duplicate = true
						break
					}
				}
				if duplicate {
					continue
				}
				result.Models = append(result.Models, model)
				for _, alias := range model.Aliases {
					seen[model.Provider+"\x00"+alias] = true
				}
			}
		}
		if err := result.validate(); err != nil {
			return nil, err
		}
		result.root = root
		result.configured = configured
		result.seedRevision = 0
		for _, catalog := range loaded {
			if catalog.seedRevision > result.seedRevision {
				result.seedRevision = catalog.seedRevision
			}
		}
		return result, nil
	}
	return nil, fmt.Errorf("pricing catalog not found")
}

func (c *PricingCatalog) validate() error {
	if c.SchemaVersion != pricingSchemaVersion || c.CatalogRevision < 1 {
		return fmt.Errorf("invalid pricing catalog schema or revision")
	}
	seen := map[string]bool{}
	for i := range c.Models {
		m := &c.Models[i]
		if strings.TrimSpace(m.Provider) == "" || strings.TrimSpace(m.Model) == "" || len(m.Aliases) == 0 || len(m.Rates) == 0 {
			return fmt.Errorf("invalid pricing model %d", i)
		}
		for _, alias := range m.Aliases {
			key := m.Provider + "\x00" + alias
			if seen[key] {
				return fmt.Errorf("duplicate pricing alias %s/%s", m.Provider, alias)
			}
			seen[key] = true
		}
		for _, rate := range m.Rates {
			if rate.Scale <= 0 || rate.USD == "" || rate.EffectiveFrom == "" {
				return fmt.Errorf("invalid pricing rate for %s", m.Model)
			}
			if _, ok := new(big.Rat).SetString(rate.USD); !ok {
				return fmt.Errorf("invalid pricing usd for %s", m.Model)
			}
			if rate.Profile == "" {
				rate.Profile = "default"
			}
			if rate.ServiceTier == "" {
				rate.ServiceTier = "default"
			}
		}
	}
	return nil
}

func (c *PricingCatalog) Refresh() (err error) {
	defer func() {
		c.mu.Lock()
		c.refreshFailed = err != nil
		c.mu.Unlock()
	}()
	c.mu.RLock()
	path := c.path
	root, configured := c.root, c.configured
	c.mu.RUnlock()
	if root != "" {
		next, err := LoadRepositoryPricingCatalog(root, configured)
		if err != nil {
			return err
		}
		c.mu.Lock()
		c.SchemaVersion, c.CatalogRevision, c.GeneratedAt, c.Models = next.SchemaVersion, next.CatalogRevision, next.GeneratedAt, next.Models
		c.seedRevision = next.seedRevision
		c.mu.Unlock()
		return nil
	}
	if path == "" {
		return fmt.Errorf("pricing catalog has no source path")
	}
	next, err := LoadPricingCatalog(path)
	if err != nil {
		return err
	}
	c.mu.Lock()
	c.SchemaVersion, c.CatalogRevision, c.GeneratedAt, c.Models = next.SchemaVersion, next.CatalogRevision, next.GeneratedAt, next.Models
	c.seedRevision = next.seedRevision
	c.mu.Unlock()
	return nil
}

func containsPricingAlias(aliases []string, wanted string) bool {
	for _, alias := range aliases {
		if alias == wanted {
			return true
		}
	}
	return false
}

func (c *PricingCatalog) Resolve(provider, model, direction, modality, cacheRole, unit, profile, tier, timestamp string) *ResolvedPricingRate {
	c.mu.RLock()
	defer c.mu.RUnlock()
	provider, model = catalogIdentity(provider, model)
	if profile == "" {
		profile = "default"
	}
	if tier == "" {
		tier = "default"
	}
	for _, item := range c.Models {
		if item.Provider != provider {
			continue
		}
		for _, alias := range item.Aliases {
			if alias != model {
				continue
			}
			var best *PricingRate
			for i := range item.Rates {
				rate := &item.Rates[i]
				rp, rt := rate.Profile, rate.ServiceTier
				if rp == "" {
					rp = "default"
				}
				if rt == "" {
					rt = "default"
				}
				if rate.Direction == direction && rate.Modality == modality && rate.CacheRole == cacheRole && rate.Unit == unit && rp == profile && rt == tier && rate.EffectiveFrom <= timestamp && (best == nil || rate.EffectiveFrom > best.EffectiveFrom) {
					best = rate
				}
			}
			if best != nil {
				return &ResolvedPricingRate{Provider: provider, Model: item.Model, Alias: alias, Rate: *best, Source: item.Source, CatalogRevision: c.CatalogRevision}
			}
		}
	}
	return nil
}

func catalogIdentity(provider, model string) (string, string) {
	provider = strings.ToLower(strings.TrimSpace(provider))
	model = strings.TrimSpace(model)
	lower := strings.ToLower(model)
	if strings.HasPrefix(lower, "switchyard/openai/") {
		return "openai", model[len("switchyard/openai/"):]
	}
	if provider == "inferencehub" && strings.HasPrefix(lower, "openai/") {
		for strings.HasPrefix(strings.ToLower(model), "openai/") {
			model = model[len("openai/"):]
		}
		return "openai", model
	}
	if provider == "switchyard" {
		if index := strings.IndexByte(model, '/'); index >= 0 {
			provider, model = model[:index], model[index+1:]
		} else {
			provider = "openai"
		}
	}
	if provider == "openai" {
		for strings.HasPrefix(strings.ToLower(model), "openai/") {
			model = model[len("openai/"):]
		}
	}
	return provider, model
}

func (c *PricingCatalog) PublicPayload() map[string]any {
	c.mu.RLock()
	defer c.mu.RUnlock()
	type sourceRecord struct {
		kind     string
		url      string
		revision int64
	}
	seen := map[string]struct{}{}
	sources := make([]sourceRecord, 0, len(c.Models))
	for _, model := range c.Models {
		sourceURL := safePricingSourceURL(model.Source.URL)
		if sourceURL == "" {
			continue
		}
		kind := model.Source.Kind
		if kind == "" {
			kind = "seed"
		}
		record := sourceRecord{kind: kind, url: sourceURL, revision: c.CatalogRevision}
		key := fmt.Sprintf("%s\x00%s\x00%d", record.kind, record.url, record.revision)
		if _, exists := seen[key]; exists {
			continue
		}
		seen[key] = struct{}{}
		sources = append(sources, record)
	}
	sort.Slice(sources, func(i, j int) bool {
		if sources[i].kind != sources[j].kind {
			return sources[i].kind < sources[j].kind
		}
		if sources[i].url != sources[j].url {
			return sources[i].url < sources[j].url
		}
		return sources[i].revision < sources[j].revision
	})
	publicSources := make([]map[string]any, 0, len(sources))
	for _, source := range sources {
		publicSources = append(publicSources, map[string]any{"kind": source.kind, "url": source.url, "revision": source.revision})
	}
	state := "seed-only"
	lastCheckedAt := float64(0)
	official := false
	for _, model := range c.Models {
		if pricingSourceKind(model.Source) == "seed" {
			continue
		}
		official = true
		checkedAt, err := time.Parse(time.RFC3339, strings.TrimSpace(model.Source.CheckedAt))
		if err == nil && float64(checkedAt.Unix()) > lastCheckedAt {
			lastCheckedAt = float64(checkedAt.Unix())
		}
	}
	if official {
		state = "fresh"
		if lastCheckedAt > 0 && time.Now().Unix()-int64(lastCheckedAt) > pricingStaleSeconds {
			state = "stale"
		}
	}
	if c.refreshFailed {
		state = "review-needed"
	}
	seedRevision := c.seedRevision
	if seedRevision == 0 && state == "seed-only" {
		seedRevision = c.CatalogRevision
	}
	status := map[string]any{
		"state": state, "seed_revision": seedRevision,
		"catalog_revision": c.CatalogRevision, "last_checked_at": lastCheckedAt,
		// Go loads the catalog from immutable JSON rather than the Python
		// refresh SQLite; keep the field explicit without exposing a misleading
		// source path as a database identity.
		"database": "",
	}
	return map[string]any{"status": status, "sources": publicSources}
}

func safePricingSourceURL(value string) string {
	parsed, err := url.Parse(strings.TrimSpace(value))
	if err != nil || parsed.Scheme != "https" || parsed.User != nil {
		return ""
	}
	if port := parsed.Port(); port != "" && port != "443" {
		return ""
	}
	host := strings.ToLower(parsed.Hostname())
	switch host {
	case "platform.openai.com", "developers.openai.com", "openai.com", "www.anthropic.com", "anthropic.com", "platform.claude.com", "ai.google.dev", "cloud.google.com", "inference-backend.internal", "openrouter.ai", "raw.githubusercontent.com", "github.com":
		return parsed.String()
	default:
		return ""
	}
}

func PricingMicroUSD(quantity float64, rate *ResolvedPricingRate) (int64, error) {
	if rate == nil || quantity < 0 || quantity != float64(int64(quantity)) {
		return 0, fmt.Errorf("invalid pricing quantity")
	}
	q := new(big.Rat).SetInt64(int64(quantity))
	usd, ok := new(big.Rat).SetString(rate.Rate.USD)
	if !ok {
		return 0, fmt.Errorf("invalid pricing rate")
	}
	amount := new(big.Rat).Mul(q, usd)
	amount.Mul(amount, big.NewRat(1000000, rate.Rate.Scale))
	n := new(big.Int)
	d := amount.Denom()
	rem := new(big.Int)
	n.QuoRem(amount.Num(), d, rem)
	if new(big.Int).Mul(rem, big.NewInt(2)).Cmp(d) >= 0 {
		n.Add(n, big.NewInt(1))
	}
	if !n.IsInt64() {
		return 0, fmt.Errorf("projected cost exceeds int64")
	}
	return n.Int64(), nil
}

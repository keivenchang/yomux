package stats

import (
	"encoding/json"
	"fmt"
	"math/big"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
)

const pricingSchemaVersion = 1

type PricingCatalog struct {
	SchemaVersion   int            `json:"schema_version"`
	CatalogRevision int64          `json:"catalog_revision"`
	GeneratedAt     string         `json:"generated_at,omitempty"`
	Models          []PricingModel `json:"models"`
	path            string
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
	URL  string `json:"url,omitempty"`
	Kind string `json:"kind,omitempty"`
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
	return &catalog, nil
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
	for _, path := range paths {
		catalog, err := LoadPricingCatalog(path)
		if err == nil {
			loaded = append(loaded, catalog)
			continue
		}
		if !os.IsNotExist(err) {
			return nil, err
		}
	}
	if len(loaded) > 0 {
		result := loaded[0]
		seen := map[string]bool{}
		for _, model := range result.Models {
			for _, alias := range model.Aliases {
				seen[model.Provider+"\x00"+alias] = true
			}
		}
		for _, extra := range loaded[1:] {
			if extra.CatalogRevision > result.CatalogRevision {
				result.CatalogRevision = extra.CatalogRevision
			}
			for _, model := range extra.Models {
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

func (c *PricingCatalog) Refresh() error {
	c.mu.RLock()
	path := c.path
	c.mu.RUnlock()
	if path == "" {
		return fmt.Errorf("pricing catalog has no source path")
	}
	next, err := LoadPricingCatalog(path)
	if err != nil {
		return err
	}
	c.mu.Lock()
	c.SchemaVersion, c.CatalogRevision, c.GeneratedAt, c.Models = next.SchemaVersion, next.CatalogRevision, next.GeneratedAt, next.Models
	c.mu.Unlock()
	return nil
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
		return "openai", model[len("openai/"):]
	}
	if provider == "switchyard" {
		if index := strings.IndexByte(model, '/'); index >= 0 {
			provider, model = model[:index], model[index+1:]
		} else {
			provider = "openai"
		}
	}
	if provider == "openai" {
		model = strings.TrimPrefix(strings.TrimPrefix(model, "openai/"), "openai/")
	}
	return provider, model
}

func (c *PricingCatalog) PublicPayload() map[string]any {
	c.mu.RLock()
	defer c.mu.RUnlock()
	models := append([]PricingModel(nil), c.Models...)
	sort.Slice(models, func(i, j int) bool {
		return models[i].Provider+"/"+models[i].Model < models[j].Provider+"/"+models[j].Model
	})
	return map[string]any{"schema_version": c.SchemaVersion, "catalog_revision": c.CatalogRevision, "generated_at": c.GeneratedAt, "models": models, "status": "loaded"}
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

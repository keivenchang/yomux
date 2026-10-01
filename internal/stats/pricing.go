package stats

import (
	"encoding/json"
	"errors"
	"fmt"
	"math/big"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"sync"
	"time"
)

const (
	// MaxSafeJSONInteger is the largest integer JavaScript can represent exactly.
	MaxSafeJSONInteger = int64(1<<53 - 1)
	maxSafeJSONInteger = MaxSafeJSONInteger
)

const pricingSchemaVersion = 1

const pricingStaleSeconds = 30 * 24 * 60 * 60

var pricingDatedModelSuffix = regexp.MustCompile(`(?i)-(?:20\d{2}[-.]?\d{2}[-.]?\d{2}|latest|preview)$`)
var pricingVariantModelSuffix = regexp.MustCompile(`(?i)-(?:sol|terra|luna)(?:-(?:latest|preview|20\d{2}[-.]?\d{2}[-.]?\d{2}))?$`)

type PricingCatalog struct {
	SchemaVersion              int            `json:"schema_version"`
	CatalogRevision            int64          `json:"catalog_revision"`
	GeneratedAt                string         `json:"generated_at,omitempty"`
	Models                     []PricingModel `json:"models"`
	path                       string
	root                       string
	configured                 string
	refreshCache               string
	seedRevision               int64
	refreshFailed              bool
	refreshStatus              string
	refreshWarnings            []string
	pricingInvalidationPending bool
	sourceChecks               map[string]pricingSourceCheck
	refreshMu                  sync.Mutex
	mu                         sync.RWMutex
}

func (c *PricingCatalog) SetRefreshCachePath(path string) {
	c.mu.Lock()
	c.refreshCache = strings.TrimSpace(path)
	c.mu.Unlock()
}

func (c *PricingCatalog) RefreshResult() (string, []string) {
	c.mu.RLock()
	defer c.mu.RUnlock()
	return c.refreshStatus, append([]string(nil), c.refreshWarnings...)
}

func (c *PricingCatalog) RefreshDue(now time.Time) bool {
	c.mu.RLock()
	defer c.mu.RUnlock()
	var latest time.Time
	for _, model := range c.Models {
		kind := pricingSourceKind(model.Source)
		if kind == "seed" || kind == "corroboration" {
			continue
		}
		checkedAt, err := time.Parse(time.RFC3339, strings.TrimSpace(model.Source.CheckedAt))
		if err == nil && checkedAt.After(latest) {
			latest = checkedAt
		}
	}
	return latest.IsZero() || now.Sub(latest) >= pricingStaleSeconds*time.Second
}

func (c *PricingCatalog) PricingInvalidationPending() bool {
	c.mu.RLock()
	defer c.mu.RUnlock()
	return c.pricingInvalidationPending
}

func (c *PricingCatalog) Revision() int64 {
	if c == nil {
		return 0
	}
	c.mu.RLock()
	defer c.mu.RUnlock()
	return c.CatalogRevision
}

func (c *PricingCatalog) AcknowledgePricingInvalidation() error {
	c.refreshMu.Lock()
	defer c.refreshMu.Unlock()
	c.mu.RLock()
	if !c.pricingInvalidationPending {
		c.mu.RUnlock()
		return nil
	}
	cachePath := c.refreshCache
	catalog := &PricingCatalog{
		SchemaVersion: c.SchemaVersion, CatalogRevision: c.CatalogRevision,
		GeneratedAt: c.GeneratedAt, Models: clonePricingModels(c.Models),
	}
	checks := clonePricingSourceChecks(c.sourceChecks)
	c.mu.RUnlock()
	if cachePath == "" {
		return fmt.Errorf("pricing invalidation state has no durable cache path")
	}
	if err := writePricingCache(cachePath, catalog, checks, nil, false); err != nil {
		return fmt.Errorf("persist pricing invalidation acknowledgement: %w", err)
	}
	c.mu.Lock()
	c.pricingInvalidationPending = false
	c.mu.Unlock()
	return nil
}

func (c *PricingCatalog) RestoreRefreshCache() error {
	c.mu.RLock()
	path := c.refreshCache
	c.mu.RUnlock()
	if path == "" {
		return nil
	}
	next, checks, failure, err := readPricingCache(path)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return fmt.Errorf("load refreshed pricing cache: %w", err)
	}
	c.mu.RLock()
	seedRevision, currentRevision := c.seedRevision, c.CatalogRevision
	c.mu.RUnlock()
	if next.CatalogRevision < currentRevision {
		if next.pricingInvalidationPending {
			c.mu.Lock()
			c.pricingInvalidationPending = true
			c.mu.Unlock()
		}
		return nil
	}
	for index := range next.Models {
		next.Models[index].Source.Kind = pricingPublicSourceKind(next.Models[index].Source.Kind)
	}
	next.seedRevision = seedRevision
	c.mu.Lock()
	c.SchemaVersion = next.SchemaVersion
	c.CatalogRevision = next.CatalogRevision
	c.GeneratedAt = next.GeneratedAt
	c.Models = next.Models
	c.seedRevision = next.seedRevision
	c.pricingInvalidationPending = next.pricingInvalidationPending
	c.sourceChecks = checks
	c.refreshFailed = failure != nil
	c.refreshStatus = ""
	c.refreshWarnings = nil
	if failure != nil {
		c.refreshStatus = "failed"
		c.refreshWarnings = append([]string(nil), failure.Warnings...)
	}
	c.mu.Unlock()
	return nil
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
		paths = append(paths, filepath.Join(root, "yomux_lib", "data", "model_pricing.json"))
		paths = append(paths, filepath.Join(root, "yomux_lib", "data", "internal_model_pricing.json"))
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
				bundledPath := filepath.Join(root, "yomux_lib", "data")
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
		m.Provider = strings.TrimSpace(m.Provider)
		m.Model = strings.TrimSpace(m.Model)
		if strings.TrimSpace(m.Provider) == "" || strings.TrimSpace(m.Model) == "" || len(m.Aliases) == 0 || len(m.Rates) == 0 {
			return fmt.Errorf("invalid pricing model %d", i)
		}
		for aliasIndex := range m.Aliases {
			m.Aliases[aliasIndex] = strings.TrimSpace(m.Aliases[aliasIndex])
			alias := m.Aliases[aliasIndex]
			if alias == "" {
				return fmt.Errorf("invalid pricing alias for %s", m.Model)
			}
			key := m.Provider + "\x00" + alias
			if seen[key] {
				return fmt.Errorf("duplicate pricing alias %s/%s", m.Provider, alias)
			}
			seen[key] = true
		}
		if sourceURL := strings.TrimSpace(m.Source.URL); sourceURL != "" {
			parsed, err := url.Parse(sourceURL)
			if err != nil || parsed.Scheme != "https" {
				return fmt.Errorf("invalid pricing source URL for %s", m.Model)
			}
			m.Source.URL = sourceURL
		}
		for rateIndex := range m.Rates {
			rate := &m.Rates[rateIndex]
			rate.Direction = strings.TrimSpace(rate.Direction)
			rate.Modality = strings.TrimSpace(rate.Modality)
			rate.CacheRole = strings.TrimSpace(rate.CacheRole)
			rate.Unit = strings.TrimSpace(rate.Unit)
			rate.USD = strings.TrimSpace(rate.USD)
			rate.EffectiveFrom = strings.TrimSpace(rate.EffectiveFrom)
			if rate.Direction == "" || rate.Modality == "" || rate.CacheRole == "" || rate.Unit == "" || rate.Scale <= 0 || rate.USD == "" || rate.EffectiveFrom == "" {
				return fmt.Errorf("invalid pricing rate for %s", m.Model)
			}
			amount, ok := new(big.Rat).SetString(rate.USD)
			if !ok || amount.Sign() < 0 {
				return fmt.Errorf("invalid pricing usd for %s", m.Model)
			}
			rate.Profile = strings.TrimSpace(rate.Profile)
			rate.ServiceTier = strings.TrimSpace(rate.ServiceTier)
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
	c.refreshMu.Lock()
	defer c.refreshMu.Unlock()
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
		c.installRefreshedCatalog(next)
		return nil
	}
	if path == "" {
		return fmt.Errorf("pricing catalog has no source path")
	}
	next, err := LoadPricingCatalog(path)
	if err != nil {
		return err
	}
	c.installRefreshedCatalog(next)
	return nil
}

func (c *PricingCatalog) installRefreshedCatalog(next *PricingCatalog) {
	c.mu.Lock()
	defer c.mu.Unlock()
	// A source revision is scoped to its producer. The active catalog revision
	// is local evidence and must remain monotonic across refreshes, even when a
	// source is replayed or temporarily serves an older revision.
	if next.CatalogRevision <= c.CatalogRevision {
		next.CatalogRevision = c.CatalogRevision + 1
	}
	if next.seedRevision < c.seedRevision {
		next.seedRevision = c.seedRevision
	}
	c.SchemaVersion, c.CatalogRevision, c.GeneratedAt, c.Models = next.SchemaVersion, next.CatalogRevision, next.GeneratedAt, next.Models
	c.seedRevision = next.seedRevision
	// Publish the new revision and its invalidation barrier together. Readers
	// must not observe prices from this catalog alongside ring products built
	// against the previous revision.
	c.pricingInvalidationPending = true
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
	equivalent, switchyardRouted := switchyardEquivalentModel(model)
	switchyardRouted = switchyardRouted && provider == "inferencehub"
	for _, item := range c.Models {
		if item.Provider != provider {
			continue
		}
		kind := pricingSourceKind(item.Source)
		if kind == "seed" {
			continue
		}
		for _, alias := range item.Aliases {
			if alias != model {
				continue
			}
			if best := matchingPricingRate(item.Rates, direction, modality, cacheRole, unit, profile, tier, timestamp); best != nil {
				return &ResolvedPricingRate{Provider: provider, Model: item.Model, Alias: alias, Rate: *best, Source: item.Source, CatalogRevision: c.CatalogRevision}
			}
		}
	}
	if switchyardRouted {
		if inferred := inferSwitchyardPricingRate(c.Models, equivalent, model, direction, modality, cacheRole, unit, profile, tier, timestamp, c.CatalogRevision); inferred != nil {
			return inferred
		}
	}
	for _, family := range inferredPricingFamilies(model) {
		for _, item := range c.Models {
			if item.Provider != provider || pricingSourceKind(item.Source) != "official" {
				continue
			}
			for _, alias := range item.Aliases {
				if alias != family {
					continue
				}
				if best := matchingPricingRate(item.Rates, direction, modality, cacheRole, unit, profile, tier, timestamp); best != nil {
					inferredSource := item.Source
					inferredSource.Kind = "inferred"
					return &ResolvedPricingRate{Provider: provider, Model: item.Model, Alias: model, Rate: *best, Source: inferredSource, CatalogRevision: c.CatalogRevision}
				}
			}
		}
	}
	for _, item := range c.Models {
		if item.Provider != provider || pricingSourceKind(item.Source) != "seed" {
			continue
		}
		for _, alias := range item.Aliases {
			if alias != model {
				continue
			}
			if best := matchingPricingRate(item.Rates, direction, modality, cacheRole, unit, profile, tier, timestamp); best != nil {
				return &ResolvedPricingRate{Provider: provider, Model: item.Model, Alias: alias, Rate: *best, Source: item.Source, CatalogRevision: c.CatalogRevision}
			}
		}
	}
	return nil
}

func switchyardEquivalentModel(model string) (string, bool) {
	if !strings.HasPrefix(model, "switchyard/") {
		return "", false
	}
	separator := strings.LastIndexByte(model, '/')
	if separator < 0 || separator == len(model)-1 {
		return "", false
	}
	return model[separator+1:], true
}

func equalPricingASCIIFold(left, right string) bool {
	if len(left) != len(right) {
		return false
	}
	for index := 0; index < len(left); index++ {
		leftByte, rightByte := left[index], right[index]
		if leftByte >= 'A' && leftByte <= 'Z' {
			leftByte += 'a' - 'A'
		}
		if rightByte >= 'A' && rightByte <= 'Z' {
			rightByte += 'a' - 'A'
		}
		if leftByte != rightByte {
			return false
		}
	}
	return true
}

// Python treats an inferencehub Switchyard alias as an inferred market rate:
// average the positive matching provider/model entries after exact route
// identities have had their chance to resolve.
func inferSwitchyardPricingRate(models []PricingModel, equivalent, alias, direction, modality, cacheRole, unit, profile, tier, timestamp string, revision int64) *ResolvedPricingRate {
	type candidate struct {
		provider string
		model    string
		rate     PricingRate
		source   PricingSource
		usd      *big.Rat
	}
	type modelIdentity struct {
		provider string
		model    string
	}
	latest := make(map[modelIdentity]candidate, len(models))
	suffix := "/" + equivalent
	for _, item := range models {
		separator := strings.LastIndexByte(item.Model, '/')
		matchesEquivalent := item.Model == equivalent ||
			separator >= 0 && equalPricingASCIIFold(item.Model[separator:], suffix)
		if !matchesEquivalent {
			continue
		}
		rate := matchingPricingRate(item.Rates, direction, modality, cacheRole, unit, profile, tier, timestamp)
		if rate == nil {
			continue
		}
		usd, ok := new(big.Rat).SetString(rate.USD)
		if !ok || usd.Sign() <= 0 {
			continue
		}
		row := candidate{provider: item.Provider, model: item.Model, rate: *rate, source: item.Source, usd: usd}
		key := modelIdentity{provider: item.Provider, model: item.Model}
		previous, exists := latest[key]
		if !exists || row.rate.EffectiveFrom > previous.rate.EffectiveFrom || row.rate.EffectiveFrom == previous.rate.EffectiveFrom && row.source.CheckedAt > previous.source.CheckedAt {
			latest[key] = row
		}
	}
	rows := make([]candidate, 0, len(latest))
	for _, row := range latest {
		rows = append(rows, row)
	}
	if len(rows) == 0 {
		return nil
	}
	sort.Slice(rows, func(i, j int) bool {
		if rows[i].rate.EffectiveFrom != rows[j].rate.EffectiveFrom {
			return rows[i].rate.EffectiveFrom > rows[j].rate.EffectiveFrom
		}
		if rows[i].source.CheckedAt != rows[j].source.CheckedAt {
			return rows[i].source.CheckedAt > rows[j].source.CheckedAt
		}
		if rows[i].provider != rows[j].provider {
			return rows[i].provider < rows[j].provider
		}
		return rows[i].model < rows[j].model
	})
	total := new(big.Rat)
	for _, row := range rows {
		total.Add(total, row.usd)
	}
	total.Quo(total, new(big.Rat).SetInt64(int64(len(rows))))
	resolved := rows[0]
	resolved.rate.USD = pricingRatDecimalString(total, 28)
	resolved.source.Kind = "inferred"
	return &ResolvedPricingRate{
		Provider: "inferencehub", Model: equivalent, Alias: alias,
		Rate: resolved.rate, Source: resolved.source, CatalogRevision: revision,
	}
}

func pricingRatDecimalString(value *big.Rat, precision int) string {
	if value.Sign() == 0 {
		return "0"
	}
	numerator := new(big.Int).Abs(value.Num())
	denominator := new(big.Int).Set(value.Denom())
	exponent := len(numerator.String()) - len(denominator.String())
	if exponent >= 0 {
		boundary := new(big.Int).Mul(denominator, new(big.Int).Exp(big.NewInt(10), big.NewInt(int64(exponent)), nil))
		if numerator.Cmp(boundary) < 0 {
			exponent--
		}
	} else {
		boundary := new(big.Int).Mul(numerator, new(big.Int).Exp(big.NewInt(10), big.NewInt(int64(-exponent)), nil))
		if boundary.Cmp(denominator) < 0 {
			exponent--
		}
	}
	places := precision - 1 - exponent
	scaledNumerator := new(big.Int).Set(numerator)
	scaledDenominator := new(big.Int).Set(denominator)
	if places >= 0 {
		scaledNumerator.Mul(scaledNumerator, new(big.Int).Exp(big.NewInt(10), big.NewInt(int64(places)), nil))
	} else {
		scaledDenominator.Mul(scaledDenominator, new(big.Int).Exp(big.NewInt(10), big.NewInt(int64(-places)), nil))
	}
	quotient, remainder := new(big.Int), new(big.Int)
	quotient.QuoRem(scaledNumerator, scaledDenominator, remainder)
	doubledRemainder := new(big.Int).Lsh(new(big.Int).Set(remainder), 1)
	comparison := doubledRemainder.Cmp(scaledDenominator)
	if comparison > 0 || comparison == 0 && quotient.Bit(0) == 1 {
		quotient.Add(quotient, big.NewInt(1))
	}
	digits := quotient.String()
	if places < 0 {
		digits += strings.Repeat("0", -places)
	} else if places > 0 {
		if len(digits) <= places {
			digits = strings.Repeat("0", places-len(digits)+1) + digits
		}
		point := len(digits) - places
		digits = digits[:point] + "." + digits[point:]
	}
	if strings.Contains(digits, ".") {
		digits = strings.TrimRight(strings.TrimRight(digits, "0"), ".")
	}
	if value.Sign() < 0 {
		return "-" + digits
	}
	return digits
}

func inferredPricingFamilies(model string) []string {
	var families []string
	if dated := pricingDatedModelSuffix.ReplaceAllString(model, ""); dated != model {
		families = append(families, dated)
	}
	if variant := pricingVariantModelSuffix.ReplaceAllString(model, ""); variant != model && (len(families) == 0 || families[0] != variant) {
		families = append(families, variant)
	}
	return families
}

func matchingPricingRate(rates []PricingRate, direction, modality, cacheRole, unit, profile, tier, timestamp string) *PricingRate {
	if profile == "" {
		profile = "default"
	}
	if tier == "" {
		tier = "default"
	}
	var best *PricingRate
	for i := range rates {
		rate := &rates[i]
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
	return best
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
	if c.refreshFailed && official {
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
	if n.Cmp(big.NewInt(maxSafeJSONInteger)) > 0 {
		return 0, fmt.Errorf("projected cost exceeds the JSON safe integer range")
	}
	return n.Int64(), nil
}

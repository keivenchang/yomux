package stats

import (
	"context"
	"database/sql"
	"fmt"
	"time"
)

// SnapshotRequest describes one bounded, read-only projection operation.
type SnapshotRequest struct {
	Start      float64
	End        float64
	Resolution float64
	// FactLimit bounds each fact family independently. Zero uses the package
	// limit; a larger value is rejected rather than causing an unbounded read.
	FactLimit int
}

type GeneratedMetadata struct {
	SchemaVersion         int64
	MinimumWriterProtocol int64
	MinimumWriterBuild    int64
}

type SnapshotAvailability struct {
	State  string
	Reason string
}

const (
	SnapshotAvailable   = "available"
	SnapshotNoData      = "no_data"
	SnapshotUnavailable = "unavailable"
)

// StatsSnapshot is the complete result of one bounded projection. It contains
// no wall-clock fields, so identical committed facts and requests produce
// identical metadata and bucket contents.
type StatsSnapshot struct {
	SourceGeneration int64
	CacheGeneration  int64
	GeneratedAt      float64
	Request          SnapshotRequest
	Generated        GeneratedMetadata
	Observations     ObservationProjection
	Tokens           TokenProjection
	Materialized     MaterializedLayer
	Unavailable      SnapshotAvailability
}

// BuildSnapshot reads only the requested range and refuses a family that is
// larger than the caller's bound. Database.Header is populated from the same
// validated SQLite read boundary as the fact readers, and therefore supplies
// the committed source generation for this snapshot.
func BuildSnapshot(ctx context.Context, database *Database, request SnapshotRequest) (StatsSnapshot, error) {
	return BuildSnapshotWithPricing(ctx, database, request, nil)
}

func BuildSnapshotWithPricing(ctx context.Context, database *Database, request SnapshotRequest, catalog *PricingCatalog) (StatsSnapshot, error) {
	if database == nil {
		return StatsSnapshot{}, fmt.Errorf("stats database is required")
	}
	limit := request.FactLimit
	if limit == 0 {
		limit = MaxFactReadLimit
	}
	if limit < 1 || limit > MaxFactReadLimit {
		return StatsSnapshot{}, fmt.Errorf("invalid snapshot fact limit %d", limit)
	}
	projectionRequest := ProjectionRequest{Start: request.Start, End: request.End, Resolution: request.Resolution}
	// Validate the range before touching SQLite and before allocating buckets.
	if _, err := projectionBounds(projectionRequest); err != nil {
		return StatsSnapshot{}, err
	}
	if err := ctx.Err(); err != nil {
		return StatsSnapshot{}, err
	}
	readDatabase := database
	if database.sqlDB != nil {
		transaction, err := database.sqlDB.BeginTx(ctx, &sql.TxOptions{ReadOnly: true})
		if err != nil {
			return StatsSnapshot{}, fmt.Errorf("begin snapshot read: %w", err)
		}
		defer transaction.Rollback()
		readDatabase = &Database{Header: database.Header, reader: transactionReader{tx: transaction}, readMetricsOwner: &database.reads}
	}

	generation, err := readDatabase.snapshotGeneration(ctx)
	if err != nil {
		return StatsSnapshot{}, err
	}
	counts, err := readDatabase.countFacts(ctx, request.Start, request.End, limit)
	if err != nil {
		return StatsSnapshot{}, err
	}
	if counts.observations > limit || counts.unavailable > limit || counts.usage > limit {
		return StatsSnapshot{}, fmt.Errorf("snapshot facts exceed bound %d", limit)
	}
	observations, err := readDatabase.ReadObservations(ctx, request.Start, request.End, limit)
	if err != nil {
		return StatsSnapshot{}, err
	}
	unavailable, err := readDatabase.ReadUnavailableSpans(ctx, request.Start, request.End, limit)
	if err != nil {
		return StatsSnapshot{}, err
	}
	atoms, err := readDatabase.ReadUsageAtoms(ctx, request.Start, request.End, limit)
	if err != nil {
		return StatsSnapshot{}, err
	}
	epochs, err := readDatabase.ReadCoverageEpochs(ctx, request.Start, request.End, limit)
	if err != nil {
		return StatsSnapshot{}, err
	}
	observationProjection, err := ProjectObservations(projectionRequest, observations, unavailable)
	if err != nil {
		return StatsSnapshot{}, err
	}
	tokenProjection, err := ProjectTokens(projectionRequest, atoms)
	if err != nil {
		return StatsSnapshot{}, err
	}
	materialized, err := (Materializer{Price: pricingResolver(catalog)}).Build(
		MaterializeRequest{Start: request.Start, End: request.End, Resolution: request.Resolution},
		observations, epochs, unavailable, atoms,
	)
	if err != nil {
		return StatsSnapshot{}, err
	}

	availability := SnapshotAvailability{State: SnapshotAvailable}
	allNoData := true
	for _, bucket := range observationProjection.Buckets {
		if len(bucket.Unavailable) > 0 {
			availability = SnapshotAvailability{State: SnapshotUnavailable, Reason: bucket.Unavailable[0].Reason}
			break
		}
		allNoData = allNoData && bucket.NoData
	}
	if availability.State == SnapshotAvailable && allNoData {
		availability.State = SnapshotNoData
	}
	return StatsSnapshot{
		SourceGeneration: generation, CacheGeneration: generation, GeneratedAt: request.End,
		Request: SnapshotRequest{Start: request.Start, End: request.End, Resolution: request.Resolution, FactLimit: limit},
		Generated: GeneratedMetadata{
			SchemaVersion: database.Header.SchemaVersion, MinimumWriterProtocol: database.Header.MinimumWriterProtocol,
			MinimumWriterBuild: database.Header.MinimumWriterBuild,
		},
		Observations: observationProjection,
		Tokens:       tokenProjection,
		Materialized: materialized,
		Unavailable:  availability,
	}, nil
}

// snapshotPriceResolver only accepts costs that were already persisted with
// the usage atom. Unknown pricing remains explicitly unpriced.
func pricingResolver(catalog *PricingCatalog) PriceResolver {
	if catalog == nil {
		return snapshotPriceResolver
	}
	return func(atom UsageAtom) (int64, int64, *PriceEvidence, bool, error) {
		quantity, err := tokenQuantity(atom)
		if err != nil {
			return 0, 0, nil, false, err
		}
		provider, _ := atom.Payload["provider"].(string)
		model, _ := atom.Payload["model"].(string)
		profile, _ := atom.Payload["pricing_profile"].(string)
		tier, _ := atom.Payload["service_tier"].(string)
		if profile == "" {
			profile = "default"
		}
		if tier == "" {
			tier = "default"
		}
		timestamp := time.Unix(int64(atom.ObservedAt), int64((atom.ObservedAt-float64(int64(atom.ObservedAt)))*1e9)).UTC().Format("2006-01-02T15:04:05.999999Z07:00")
		api := catalog.Resolve(provider, model, atom.Direction, atom.Modality, atom.CacheRole, atom.Unit, "default", tier, timestamp)
		if api == nil {
			return 0, 0, nil, false, nil
		}
		apiCost, err := PricingMicroUSD(quantity, api)
		if err != nil {
			return 0, 0, nil, false, err
		}
		marginal := apiCost
		if profile == "subscription" {
			marginal = 0
		} else if profile != "" && profile != "default" {
			resolved := catalog.Resolve(provider, model, atom.Direction, atom.Modality, atom.CacheRole, atom.Unit, profile, tier, timestamp)
			if resolved == nil {
				return 0, 0, nil, false, nil
			}
			marginal, err = PricingMicroUSD(quantity, resolved)
			if err != nil {
				return 0, 0, nil, false, err
			}
			api = resolved
		}
		return marginal, apiCost, &PriceEvidence{
			// Keep evidence identity tied to every rendered pricing fact. This is
			// the same length-delimited key used by Python, so HTTP cost reports
			// remain byte-comparable across implementations and catalog revisions.
			Key:      stableCostKey(provider, model, usageCostDimension(atom), atom.Direction, atom.Modality, atom.CacheRole, atom.Unit, profile, tier, api.Model, api.Rate.USD, fmt.Sprintf("%d", api.Rate.Scale), api.Rate.EffectiveFrom, api.Source.Kind, api.Source.URL, fmt.Sprintf("%d", api.CatalogRevision)),
			Provider: provider, Model: model, Dimension: usageCostDimension(atom), Direction: atom.Direction,
			Modality: atom.Modality, CacheRole: atom.CacheRole, Unit: atom.Unit,
			PricingProfile: profile, ServiceTier: tier, CatalogModel: api.Model,
			RateUSD: api.Rate.USD, RateScale: api.Rate.Scale, EffectiveFrom: api.Rate.EffectiveFrom,
			SourceKind: api.Source.Kind, SourceURL: api.Source.URL, CatalogRevision: api.CatalogRevision,
			Tokens: int64(quantity), MicroUSD: marginal, APIListMicroUSD: apiCost, PricedAtoms: 1,
		}, true, nil
	}
}

// PricingResolver exposes the same catalog resolution used by snapshots to
// lifecycle materialization. Keeping one resolver prevents ring products and
// cold projections from assigning different prices to the same atom.
func PricingResolver(catalog *PricingCatalog) PriceResolver {
	return pricingResolver(catalog)
}

func snapshotPriceResolver(atom UsageAtom) (int64, int64, *PriceEvidence, bool, error) {
	marginal, ok := integerPayload(atom.Payload, "micro_usd")
	if !ok {
		return 0, 0, nil, false, nil
	}
	apiList, ok := integerPayload(atom.Payload, "api_list_micro_usd")
	if !ok {
		return 0, 0, nil, false, nil
	}
	return marginal, apiList, nil, true, nil
}

func integerPayload(payload map[string]any, key string) (int64, bool) {
	value, ok := payload[key]
	if !ok {
		return 0, false
	}
	n, err := number(value)
	if err != nil || n < 0 || n > float64(^uint64(0)>>1) || n != float64(int64(n)) {
		return 0, false
	}
	return int64(n), true
}

type transactionReader struct{ tx *sql.Tx }

// beginReadSnapshot pins all fact pages to one SQLite read transaction. A
// lifecycle fold must not combine observations from one source generation
// with coverage or usage rows committed by a later generation.
func (d *Database) beginReadSnapshot(ctx context.Context) (*Database, func(), error) {
	if d == nil || d.sqlDB == nil {
		return d, func() {}, nil
	}
	tx, err := d.sqlDB.BeginTx(ctx, &sql.TxOptions{ReadOnly: true})
	if err != nil {
		return nil, nil, fmt.Errorf("begin stats read snapshot: %w", err)
	}
	snapshot := &Database{Header: d.Header, reader: transactionReader{tx: tx}, readMetricsOwner: &d.reads}
	closeSnapshot := func() { _ = tx.Rollback() }
	return snapshot, closeSnapshot, nil
}

func (r transactionReader) Scalar(ctx context.Context, query string, args ...any) (any, error) {
	var value any
	if err := r.tx.QueryRowContext(ctx, query, args...).Scan(&value); err != nil {
		return nil, err
	}
	return value, nil
}

func (r transactionReader) Rows(ctx context.Context, query string, args ...any) ([][]any, error) {
	rows, err := r.tx.QueryContext(ctx, query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	columns, err := rows.Columns()
	if err != nil {
		return nil, err
	}
	result := make([][]any, 0)
	for rows.Next() {
		values := make([]any, len(columns))
		pointers := make([]any, len(values))
		for i := range values {
			pointers[i] = &values[i]
		}
		if err := rows.Scan(pointers...); err != nil {
			return nil, err
		}
		result = append(result, values)
	}
	return result, rows.Err()
}

// QueryRows keeps lifecycle materialization on the typed database/sql path
// even while the read is pinned to a transaction. The surrounding Database
// records the elapsed query/row-stepping time and forwards it to the owner
// reader's metrics, so transactional reads remain visible to diagnostics.
func (r transactionReader) QueryRows(ctx context.Context, query string, visit func(*sql.Rows) error, args ...any) error {
	rows, err := r.tx.QueryContext(ctx, query, args...)
	if err != nil {
		return err
	}
	defer rows.Close()
	if err := visit(rows); err != nil {
		return err
	}
	return rows.Err()
}

func (transactionReader) QueryOnly(context.Context) error { return nil }
func (transactionReader) Close() error                    { return nil }

func (d *Database) snapshotGeneration(ctx context.Context) (int64, error) {
	rows, err := d.reader.Rows(ctx, "SELECT source_generation FROM schema_meta WHERE singleton = 1")
	if err != nil {
		return 0, fmt.Errorf("read snapshot generation: %w", err)
	}
	if len(rows) != 1 || len(rows[0]) != 1 {
		return 0, fmt.Errorf("read snapshot generation: malformed result")
	}
	generation, err := integer(rows[0][0])
	if err != nil || generation < 0 {
		return 0, fmt.Errorf("read snapshot generation: invalid generation")
	}
	return generation, nil
}

type factCounts struct{ observations, unavailable, usage int }

func (d *Database) countFacts(ctx context.Context, start, end float64, limit int) (factCounts, error) {
	count := func(table, timeColumn string) (int, error) {
		rows, err := d.reader.Rows(ctx, fmt.Sprintf("SELECT count(*) FROM (SELECT 1 FROM %s WHERE %s >= ? AND %s < ? LIMIT ?)", table, timeColumn, timeColumn), start, end, limit+1)
		if err != nil {
			return 0, fmt.Errorf("count %s: %w", table, err)
		}
		if len(rows) != 1 || len(rows[0]) != 1 {
			return 0, fmt.Errorf("count %s: malformed result", table)
		}
		value, err := number(rows[0][0])
		if err != nil {
			return 0, fmt.Errorf("count %s: %w", table, err)
		}
		return int(value), nil
	}
	observations, err := count("observations", "observed_at")
	if err != nil {
		return factCounts{}, err
	}
	unavailable, err := countUnavailable(ctx, d.reader, start, end, limit)
	if err != nil {
		return factCounts{}, err
	}
	usage, err := count("usage_atoms", "observed_at")
	if err != nil {
		return factCounts{}, err
	}
	return factCounts{observations: observations, unavailable: unavailable, usage: usage}, nil
}

func countUnavailable(ctx context.Context, reader ReadOnlyDB, start, end float64, limit int) (int, error) {
	rows, err := reader.Rows(ctx, "SELECT count(*) FROM (SELECT 1 FROM unavailable_spans WHERE ended_at > ? AND started_at < ? LIMIT ?)", start, end, limit+1)
	if err != nil {
		return 0, fmt.Errorf("count unavailable_spans: %w", err)
	}
	if len(rows) != 1 || len(rows[0]) != 1 {
		return 0, fmt.Errorf("count unavailable_spans: malformed result")
	}
	value, err := number(rows[0][0])
	if err != nil {
		return 0, fmt.Errorf("count unavailable_spans: %w", err)
	}
	return int(value), nil
}

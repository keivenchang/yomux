package stats

import (
	"context"
	"runtime/pprof"
)

// runWithStatsProfileLabels keeps sampled CPU attribution useful without
// changing collector ownership or exposing raw source data. Labels are only
// active while the supplied function runs, so they also follow the existing
// collector goroutine and cancellation boundaries.
func runWithStatsProfileLabels(ctx context.Context, component, family string, fn func(context.Context)) {
	runWithStatsProfilePhase(ctx, component, family, "", fn)
}

func runWithStatsProfilePhase(ctx context.Context, component, family, phase string, fn func(context.Context)) {
	labelValues := []string{"stats_component", component}
	if family != "" {
		labelValues = append(labelValues, "stats_family", family)
	}
	if phase != "" {
		labelValues = append(labelValues, "stats_phase", phase)
	}
	pprof.Do(ctx, pprof.Labels(labelValues...), fn)
}

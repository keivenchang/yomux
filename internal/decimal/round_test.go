// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package decimal

import (
	"errors"
	"math"
	"testing"
)

func TestRoundPreservesBinaryTiesAndFiniteRange(t *testing.T) {
	for _, example := range []struct{ input, want float64 }{{1e308, 1e308}, {2.6755, 2.675}, {0.1005, 0.101}, {12.34567, 12.346}, {-2.6755, -2.675}} {
		got, err := Round(example.input, 3)
		if err != nil || got != example.want {
			t.Fatalf("Round(%g)=%g err=%v want=%g", example.input, got, err, example.want)
		}
	}
	for _, value := range []float64{math.Inf(1), math.Inf(-1), math.NaN()} {
		if _, err := Round(value, 3); !errors.Is(err, ErrNonfinite) {
			t.Fatalf("non-finite error=%v", err)
		}
	}
	for _, places := range []int{-1, 19} {
		if _, err := Round(1, places); !errors.Is(err, ErrPrecision) {
			t.Fatalf("precision error=%v", err)
		}
	}
}

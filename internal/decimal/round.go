// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

// Package decimal owns fixed-decimal rounding of finite binary floats.
package decimal

import (
	"errors"
	"math"
	"strconv"
)

var ErrNonfinite = errors.New("cannot round a non-finite number")
var ErrPrecision = errors.New("decimal precision must be between zero and eighteen")

func Round(value float64, places int) (float64, error) {
	if math.IsNaN(value) || math.IsInf(value, 0) {
		return 0, ErrNonfinite
	}
	if places < 0 || places > 18 {
		return 0, ErrPrecision
	}
	// Formatting rounds the binary float itself. Multiplying first changes
	// ties such as 2.6755 and can overflow a finite value such as 1e308.
	return strconv.ParseFloat(strconv.FormatFloat(value, 'f', places, 64), 64)
}

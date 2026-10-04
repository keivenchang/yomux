// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package productenv

import (
	"os"
	"strings"
)

// Value resolves product aliases without mutating the environment. An explicitly
// empty canonical value must clear a legacy setting as it does at CLI startup.
func Value(legacyName string) string {
	if strings.HasPrefix(legacyName, "YOLOMUX_") {
		if value, present := os.LookupEnv("YOMUX_" + strings.TrimPrefix(legacyName, "YOLOMUX_")); present {
			return value
		}
	}
	return os.Getenv(legacyName)
}

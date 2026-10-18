// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import "testing"

func TestResolveHostPartitionIDMatchesPythonHostIdentityRules(t *testing.T) {
	for _, example := range []struct {
		name, platform, override, machineID, platformOutput string
		present                                             bool
		want                                                string
		wantError                                           bool
	}{
		{name: "explicit override is normalized", platform: "linux", override: "  HOST_ABC  ", present: true, want: "host_abc"},
		{name: "linux machine ID is used", platform: "linux", machineID: "ABCDEF-1234\n", want: "abcdef-1234"},
		{name: "macOS platform UUID is used", platform: "darwin", platformOutput: `  "IOPlatformUUID" = "ABCDEF-1234"`, want: "abcdef-1234"},
		{name: "present but invalid override fails closed", platform: "linux", override: "../other-host", present: true, wantError: true},
		{name: "missing linux machine ID fails closed", platform: "linux", wantError: true},
		{name: "missing macOS UUID fails closed", platform: "darwin", wantError: true},
	} {
		t.Run(example.name, func(t *testing.T) {
			got, err := resolveHostPartitionID(example.platform, example.override, example.present, example.machineID, example.platformOutput)
			if (err != nil) != example.wantError || got != example.want {
				t.Fatalf("resolveHostPartitionID = %q, %v; want %q, error=%t", got, err, example.want, example.wantError)
			}
		})
	}
}

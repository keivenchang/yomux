// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"os"

	"github.com/keivenchang/yomux/internal/productenv"
)

func nativeLaunchIdentityKeys() []string {
	return append(productenv.Names("ROOT"), productenv.Names("INSTANCE")...)
}

func nativeLaunchIdentityEnvironment() map[string]string {
	values := map[string]string{}
	for _, name := range nativeLaunchIdentityKeys() {
		values[name] = os.Getenv(name)
	}
	return values
}

// Identity must remain independent of the optional, host-wide metrics census.
func (s *server) nativeServerIdentity() map[string]any {
	return map[string]any{
		"backend": "go", "version": version, "bundle_revision": "go:" + version,
		"instance_id": s.instanceID, "root": s.root, "pid": os.Getpid(),
		"launch_environment": s.launchIdentity,
	}
}

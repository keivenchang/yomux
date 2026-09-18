// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package finder

import "context"

const maxNativeWatchRegistrations = 512
const maxNativeWatchEntries = 4096
const maxActiveWatchClients = 64
const maxActiveWatchRoots = 256
const maxActiveWatchFiles = 512

type nativeWatchEvent struct {
	path     string
	root     string
	overflow bool
}

type nativeWatchStatus struct {
	available     bool
	degraded      bool
	reason        string
	registrations int
}

type nativeWatchController interface {
	SetRoots(ctx context.Context, roots []string) nativeWatchStatus
	Events() <-chan nativeWatchEvent
	Close()
}

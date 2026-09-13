//go:build !linux

// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package finder

import "context"

type unsupportedNativeWatcher struct {
	events chan nativeWatchEvent
}

func newNativeWatcher() (nativeWatchController, nativeWatchStatus) {
	return &unsupportedNativeWatcher{events: make(chan nativeWatchEvent)}, nativeWatchStatus{degraded: true, reason: "native-watcher-unsupported"}
}

func (w *unsupportedNativeWatcher) SetRoots(_ context.Context, _ []string) nativeWatchStatus {
	return nativeWatchStatus{degraded: true, reason: "native-watcher-unsupported"}
}

func (w *unsupportedNativeWatcher) Events() <-chan nativeWatchEvent { return w.events }

func (w *unsupportedNativeWatcher) Close() { close(w.events) }

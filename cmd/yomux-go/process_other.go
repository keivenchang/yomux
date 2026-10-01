// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
//go:build !darwin

package main

import "errors"

func darwinProcessEntries() ([]procEntry, error) {
	return nil, errors.New("Darwin process inventory is unavailable")
}

func darwinDescendantProcessEntries(int) ([]procEntry, error) {
	return nil, errors.New("Darwin descendant process inventory is unavailable")
}

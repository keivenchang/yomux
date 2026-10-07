//go:build !linux && !darwin

// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package mutablefs

import "errors"

func filesystemType(string) (string, error) { return "", errors.New("filesystem probe unavailable") }

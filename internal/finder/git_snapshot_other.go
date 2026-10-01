// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
//go:build !darwin

package finder

import (
	"context"
	"os"
)

func (namespace *gitNamespace) prepareSnapshot(context.Context, *os.File) error { return nil }

func removeDarwinSnapshot(root string) { _ = os.RemoveAll(root) }

func (s *Service) openDarwinGitNamespace(ctx context.Context, directory *os.File, workingDirectory, requested string) (*gitNamespace, *Error) {
	return s.openGitNamespaceGenericContext(ctx, directory, workingDirectory, requested)
}

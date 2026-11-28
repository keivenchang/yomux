// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"context"
	"errors"
	"log/slog"
	"path/filepath"
	"strings"
	"time"
)

type sharePDFStatus struct {
	Revision uint64 `json:"revision"`
	Stale    bool   `json:"stale"`
}

func isShareTeX(path string) bool {
	extension := strings.ToLower(filepath.Ext(path))
	return extension == ".tex" || extension == ".ltx"
}

// errTeXPreempted marks a background compile that yielded to an interactive preview.
var errTeXPreempted = errors.New("interactive TeX preview took the compiler")

// The caller holds doc.mu. Queue only durable source changes, never presence updates.
// shared reports whether a live share grant includes the document, read under the registry
// lock. Owner tabs editing together have no guest to publish a PDF for, so they compile nothing.
func (s *server) scheduleSharedTeX(doc *shareDocument, shared bool) {
	doc.cancelTeX()
	if !shared || s.producerContext == nil || !isShareTeX(doc.Path) {
		return
	}
	ctx, cancel := context.WithCancel(s.producerContext)
	source := doc.Saved
	doc.texCancel = cancel
	doc.texGeneration++
	generation := doc.texGeneration
	if !s.startBackgroundWork(func() {
		defer func() {
			cancel()
			doc.mu.Lock()
			if doc.texGeneration == generation {
				doc.texCancel = nil
			}
			doc.mu.Unlock()
		}()
		idle := time.NewTimer(time.Second)
		defer idle.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-idle.C:
			}
			// A preempted compile waits out the idle delay again, which queues it behind
			// the interactive preview that took the compiler.
			if !s.compileSharedTeX(ctx, doc, source) {
				return
			}
			idle.Reset(time.Second)
		}
	}) {
		doc.cancelTeX()
	}
}

// beginInteractiveTeX makes shared compiles yield until endInteractiveTeX: running and queued
// ones are cancelled, and one that registers meanwhile cancels itself before taking the compiler.
func (s *server) beginInteractiveTeX() {
	s.texBackgroundMu.Lock()
	defer s.texBackgroundMu.Unlock()
	s.texInteractive++
	for _, preempt := range s.texBackground {
		preempt(errTeXPreempted)
	}
}

func (s *server) endInteractiveTeX() {
	s.texBackgroundMu.Lock()
	defer s.texBackgroundMu.Unlock()
	s.texInteractive--
}

// compileSharedTeX reports whether an interactive preview preempted it.
func (s *server) compileSharedTeX(parent context.Context, doc *shareDocument, source string) bool {
	ctx, cancel := context.WithTimeout(parent, 150*time.Second)
	defer cancel()
	compiler, kind := s.texCompiler()
	if compiler == "" {
		return false
	}
	attempt, preempt := context.WithCancelCause(ctx)
	defer preempt(nil)
	s.texBackgroundMu.Lock()
	if s.texBackground == nil {
		s.texBackground = map[uint64]context.CancelCauseFunc{}
	}
	s.texBackgroundSerial++
	ticket := s.texBackgroundSerial
	s.texBackground[ticket] = preempt
	if s.texInteractive > 0 {
		preempt(errTeXPreempted)
	}
	s.texBackgroundMu.Unlock()
	defer func() {
		s.texBackgroundMu.Lock()
		delete(s.texBackground, ticket)
		s.texBackgroundMu.Unlock()
	}()
	pdf, err := s.compileBackgroundTeX(attempt, compiler, kind, source)
	if errors.Is(context.Cause(attempt), errTeXPreempted) {
		return parent.Err() == nil
	}
	if err != nil {
		if parent.Err() == nil {
			slog.Warn("Shared TeX preview failed", "error", err)
		}
		return false
	}
	s.publishSharedTeX(ctx, doc, source, pdf)
	return false
}

// compileBackgroundTeX reuses a cached compile of the same source when there is one.
func (s *server) compileBackgroundTeX(ctx context.Context, compiler, kind, source string) ([]byte, error) {
	output, _, err := s.cachedTeXCompile(ctx, compiler, kind, "", source, func(ctx context.Context) error {
		return s.texCompileMu.LockContext(ctx, nil)
	}, nil)
	return output.pdf, err
}

// publishSharedTeX hands a finished compile to the guests while its source is still current.
// The document may have closed after the save that started the compile (a read-only share
// keeps no editors); then the file itself must still hold that source.
func (s *server) publishSharedTeX(ctx context.Context, doc *shareDocument, source string, pdf []byte) {
	data, _, failure := doc.file.ReadCurrent(ctx, shareTextLimit)
	onDisk := failure == nil && normalizeShareText(string(data)) == source
	s.shares.mu.Lock()
	defer s.shares.mu.Unlock()
	if ctx.Err() != nil || !s.shareGrantIncludes(doc.Path, false) {
		return
	}
	current := s.shares.documents[doc.Path]
	if current == nil {
		if !onDisk {
			return
		}
	} else {
		current.mu.Lock()
		defer current.mu.Unlock()
		if current.Saved != source {
			return
		}
	}
	if err := s.sharePublishPDFLocked(doc.Path, source, pdf, current); err != nil {
		slog.Warn("Shared TeX PDF publication failed", "error", err)
	}
}

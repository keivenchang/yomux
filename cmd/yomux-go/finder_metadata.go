// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"context"
	"fmt"
	"github.com/keivenchang/yomux/internal/finder"
	"net/http"
)

func (s *server) fsChmod(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	var body finder.ChmodRequest
	if err := decodeJSONBody(r.Body, 8192, &body); err != nil {
		http.Error(w, "invalid permission request", http.StatusBadRequest)
		return
	}
	original, _ := s.identityStatus(r)
	credentialRequest := r.Clone(context.Background())
	body.Authorize = func() error {
		user, status := s.identityStatus(credentialRequest)
		if status != 0 || user.role != "admin" || user.username != original.username {
			return fmt.Errorf("YOmux administrator authorization is no longer valid")
		}
		return nil
	}
	s.finderMetadataStream(w, r, func(ctx context.Context, progress func(map[string]any) error) (map[string]any, *finder.Error) {
		return s.finder.Chmod(ctx, body, progress)
	})
}

func (s *server) fsDirectorySize(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	path := r.URL.Query().Get("path")
	s.finderMetadataStream(w, r, func(ctx context.Context, progress func(map[string]any) error) (map[string]any, *finder.Error) {
		return s.finder.DirectorySize(ctx, path, progress)
	})
}

func (s *server) finderMetadataStream(w http.ResponseWriter, r *http.Request, task func(context.Context, func(map[string]any) error) (map[string]any, *finder.Error)) {
	stream, ok := newJSONSSEWriter(w, r.Context())
	if !ok {
		http.Error(w, "streaming unavailable", http.StatusInternalServerError)
		return
	}
	defer stream.close()
	product, finderErr := s.runFinderStreamProduct(r.Context(), func(ctx context.Context) (finderProduct, *finder.Error) {
		data, err := task(ctx, func(progress map[string]any) error { return stream.write("progress", progress) })
		return finderProduct{data: data}, err
	})
	if r.Context().Err() != nil {
		return
	}
	if finderErr != nil {
		_ = stream.write("error", finderErr.Payload())
		return
	}
	_ = stream.write("done", product.data)
}

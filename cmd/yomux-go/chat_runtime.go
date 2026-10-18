// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"context"
	"errors"
	"path/filepath"

	"github.com/keivenchang/yomux/internal/chat"
)

// nativeChat binds the protocol and event owners to the same database. Creation
// is explicit: registering unavailable routes must never migrate user data.
type nativeChat struct {
	store     *chat.Store
	service   *chat.Service
	events    *chat.Events
	replies   *chat.AgentReplies
	retention func(context.Context) (int, error)
}

func newNativeChat(ctx context.Context, root, database string, retention func(context.Context) (int, error), publish chat.EventPublisher) (*nativeChat, error) {
	if retention == nil || publish == nil {
		return nil, errors.New("chat requires settings and event owners")
	}
	classifier, err := chat.NewQuestionClassifier(root)
	if err != nil {
		return nil, err
	}
	store, err := chat.OpenStore(ctx, database, chat.StoreOptions{})
	if err != nil {
		return nil, err
	}
	service, err := chat.NewService(store, chat.NewCursorCodec(filepath.Join(filepath.Dir(database), "chat-cursor.key")), classifier, chat.ServiceOptions{RetentionDays: retention})
	if err != nil {
		return nil, errors.Join(err, store.Close())
	}
	events, err := chat.NewEvents(store, publish)
	if err != nil {
		return nil, errors.Join(err, store.Close())
	}
	return &nativeChat{store: store, service: service, events: events, retention: retention}, nil
}

func (runtime *nativeChat) settingsReady(ctx context.Context) error {
	days, err := runtime.retention(ctx)
	if err != nil {
		return err
	}
	if days < 1 || days > 365 {
		return errors.New("invalid chat retention configuration")
	}
	return nil
}

func (s *server) chatRetentionDays(ctx context.Context) (int, error) {
	payload, err := s.nativeSettingsPayload(ctx, nil, false)
	if err != nil {
		return 0, err
	}
	if message, ok := payload["error"].(string); ok && message != "" {
		return 0, &chat.Error{Code: "settings_file_malformed", Status: 503, Message: message}
	}
	value := settingsObject(settingsObject(payload["settings"])["chat"])["retention_days"]
	days, ok := settingsNumber(value)
	if !ok || days < 1 || days > 365 || days != float64(int(days)) {
		return 0, errors.New("invalid chat retention setting")
	}
	return int(days), nil
}

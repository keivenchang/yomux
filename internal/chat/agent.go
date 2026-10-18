// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package chat

import (
	"context"
	"crypto/rand"
	"errors"
	"fmt"
	"time"

	"github.com/keivenchang/yomux/internal/locales"
)

// AgentRequest preserves the controller's stable request identity and the
// authenticated caller's role. A responder must honor cancellation and enforce
// that role before taking any action on another session.
type AgentRequest struct {
	Message, Locale, RequestID, AccessRole string
}

type AgentResponder func(context.Context, AgentRequest) (string, error)

type AgentReplyPayload struct {
	SendPayload
	SourceID int64 `json:"source_message_id"`
}

type AgentReplies struct {
	service         *Service
	events          *Events
	respond         AgentResponder
	refreshInterval time.Duration
}

func NewAgentReplies(service *Service, events *Events, respond AgentResponder) (*AgentReplies, error) {
	if service == nil || events == nil || respond == nil || events.store != service.store {
		return nil, errors.New("chat agent replies require one service/store/event owner and responder")
	}
	return &AgentReplies{service: service, events: events, respond: respond, refreshInterval: time.Duration(TypingLeaseSeconds) * time.Second / 2}, nil
}

func (owner *AgentReplies) typing(ctx context.Context, instance string, active bool) error {
	if _, err := owner.service.Typing(ctx, AgentUsername, instance, active); err != nil {
		return err
	}
	return owner.events.Flush(ctx)
}

func (owner *AgentReplies) Reply(ctx context.Context, username, browser string, sourceID int64, locale, role string) (_ AgentReplyPayload, resultErr error) {
	source, question, err := owner.service.YOAgentSource(ctx, username, browser, sourceID)
	if err != nil {
		return AgentReplyPayload{}, err
	}
	// Concurrent requests (including another server) own separate leases. One
	// completion must not clear the typing indicator of a still-running request.
	instance := fmt.Sprintf("%s-%d-%s", AgentInstance, source.ID, rand.Text())
	defer func() {
		cleanupCtx, cancel := context.WithTimeout(context.WithoutCancel(ctx), 5*time.Second)
		defer cancel()
		resultErr = errors.Join(resultErr, owner.typing(cleanupCtx, instance, false))
	}()
	if err := owner.typing(ctx, instance, true); err != nil {
		return AgentReplyPayload{}, err
	}
	requestCtx, cancel := context.WithCancel(ctx)
	defer cancel()
	refreshCtx, stop := context.WithCancel(requestCtx)
	done := make(chan struct{})
	var refreshErr error
	go func() {
		defer close(done)
		ticker := time.NewTicker(owner.refreshInterval)
		defer ticker.Stop()
		for {
			select {
			case <-refreshCtx.Done():
				return
			case <-ticker.C:
				if err := owner.typing(refreshCtx, instance, true); err != nil {
					if errors.Is(err, context.Canceled) && refreshCtx.Err() != nil {
						return
					}
					// Propagate a failed unit to its request and interrupt its responder;
					// the server and other requests keep their independent ownership.
					cancel()
					refreshErr = err
					return
				}
			}
		}
	}()
	defer func() { stop(); <-done }()
	answer, replyErr := owner.respond(requestCtx, AgentRequest{Message: question, Locale: locale, RequestID: fmt.Sprintf("yochat-%d", source.ID), AccessRole: role})
	stop()
	<-done
	if err := errors.Join(replyErr, refreshErr, ctx.Err()); err != nil {
		return AgentReplyPayload{}, err
	}
	result, err := owner.service.RecordYOAgentReply(ctx, source, locales.TrimSpace(answer))
	if err != nil {
		return AgentReplyPayload{}, err
	}
	return AgentReplyPayload{SendPayload: result, SourceID: source.ID}, owner.events.Flush(ctx)
}

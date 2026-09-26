// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"context"
	"net/http"
	"sync"

	"github.com/keivenchang/yolomux/internal/finder"
)

const sessionFilesBatchWorkers = 4

type sessionFilesBatchResult struct {
	session string
	payload map[string]any
	status  int
}

func runSessionFilesBatch(ctx context.Context, sessions []string, item func(context.Context, string) (map[string]any, int)) (map[string]any, map[string]any, error) {
	payloads := make(map[string]any, len(sessions))
	statuses := make(map[string]any, len(sessions))
	if len(sessions) == 0 {
		return payloads, statuses, nil
	}
	workerCount := min(sessionFilesBatchWorkers, len(sessions))
	jobs := make(chan string)
	results := make(chan sessionFilesBatchResult, len(sessions))
	var workers sync.WaitGroup
	workers.Add(workerCount)
	for range workerCount {
		go func() {
			defer workers.Done()
			for {
				select {
				case <-ctx.Done():
					return
				case session, ok := <-jobs:
					if !ok {
						return
					}
					payload, status := item(ctx, session)
					results <- sessionFilesBatchResult{session: session, payload: payload, status: status}
				}
			}
		}()
	}
	go func() {
		defer close(jobs)
		for _, session := range sessions {
			select {
			case <-ctx.Done():
				return
			case jobs <- session:
			}
		}
	}()
	go func() {
		workers.Wait()
		close(results)
	}()
	for result := range results {
		payloads[result.session] = result.payload
		statuses[result.session] = result.status
	}
	if err := ctx.Err(); err != nil {
		return nil, nil, err
	}
	return payloads, statuses, nil
}

func sessionFilesBatchErrorPayload(session string, finderErr *finder.Error) map[string]any {
	messageKey := finderErr.MessageKey
	if finderErr.Code != "unknown_session" {
		messageKey = "diff.error.sessionUnavailable"
	}
	return map[string]any{
		"session": session,
		"errors":  []any{},
		"error":   finderErr.Message,
		"user_message": map[string]any{
			"key": messageKey, "params": map[string]any{"session": session}, "fallback": finderErr.Message,
		},
	}
}

// sessionFilesPlan owns the request identity and shared produce/cache transaction used by JSON,
// SSE, and batch routes; route-specific cache admission remains at each HTTP boundary.
type sessionFilesPlan struct {
	request    sessionFilesRequest
	descriptor string
	viewID     string
}

func newSessionFilesPlan(request sessionFilesRequest) sessionFilesPlan {
	descriptor := sessionFilesRequestDescriptor(request)
	return sessionFilesPlan{
		request:    request,
		descriptor: descriptor,
		viewID:     sessionFilesCacheViewID(descriptor),
	}
}

func (s *server) sessionFilesPlanInputs(plan sessionFilesPlan) ([]string, []finder.SessionFileAgent, *finder.Error) {
	roots, agents, finderErr := s.sessionFilesPaneInputs(plan.request.session)
	if finderErr != nil {
		return nil, nil, finderErr
	}
	return sessionFilesPlanRoots(plan, roots), agents, nil
}

func sessionFilesPlanRoots(plan sessionFilesPlan, roots []string) []string {
	return sessionFilesStreamRoots(plan.request.repoPaths, roots)
}

func (s *server) sessionFilesPlanProduceWithInputs(ctx context.Context, plan sessionFilesPlan, roots []string, agents []finder.SessionFileAgent, emit func(map[string]any) error) (map[string]any, *finder.Error) {
	request := plan.request
	return s.finder.SessionFilesWithAgentsStream(ctx, request.session, roots, agents, request.hours, request.fromRef, request.toRef, request.repoRefs, emit)
}

func (s *server) sessionFilesPlanProduceAndCacheWithInputs(ctx context.Context, plan sessionFilesPlan, roots []string, agents []finder.SessionFileAgent, emit func(map[string]any) error) (map[string]any, *finder.Error) {
	request := plan.request
	generation := s.beginSessionFilesCacheWrite(plan.viewID)
	payload, finderErr := s.sessionFilesPlanProduceWithInputs(ctx, plan, roots, agents, emit)
	if finderErr != nil {
		return payload, finderErr
	}
	if request.session != "" && !s.sessionFilesIdentityMatches(request.session, request.sessionIdentity) {
		return nil, sessionFilesIncarnationChangedError(request.session)
	}
	s.writeSessionFilesCache(request, plan.viewID, payload, http.StatusOK, generation)
	return payload, nil
}

func (s *server) sessionFilesPlanTaskWithInputs(plan sessionFilesPlan, roots []string, agents []finder.SessionFileAgent) func(context.Context) (map[string]any, *finder.Error) {
	return func(ctx context.Context) (map[string]any, *finder.Error) {
		return s.sessionFilesPlanProduceWithInputs(ctx, plan, roots, agents, nil)
	}
}

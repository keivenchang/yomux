// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"strconv"
	"time"

	"github.com/keivenchang/yomux/internal/finder"
	"github.com/keivenchang/yomux/internal/work"
)

const (
	finderJobWorkers        = 2
	finderJobQueue          = 32
	finderBackgroundWorkers = 1
	finderBackgroundQueue   = 8
	finderBulkJobQueue      = 8
	finderStreamWorkers     = 2
	finderStreamJobQueue    = 8
	finderJobDeadline       = 10 * time.Minute
	finderFastListBudget    = 50 * time.Millisecond
)

type finderProduct struct {
	body        []byte
	contentType string
	filename    string
	size        int64
	data        map[string]any
}

func newFinderJobScheduler() *work.Scheduler {
	return work.New(finderJobWorkers, finderJobQueue)
}

func newFinderBackgroundJobScheduler() *work.Scheduler {
	return work.New(finderBackgroundWorkers, finderBackgroundQueue)
}

func (s *server) finderBackgroundScheduler() *work.Scheduler {
	if s.finderBackgroundJobs != nil {
		return s.finderBackgroundJobs
	}
	return s.finderJobs
}

func newFinderBulkJobScheduler() *work.Scheduler {
	return work.New(1, finderBulkJobQueue)
}

func newFinderStreamScheduler() *work.Scheduler {
	return work.New(finderStreamWorkers, finderStreamJobQueue)
}

func finderOperationID() string {
	return fmt.Sprintf("finder-%d", time.Now().UnixNano())
}

func finderRequestID(id string) string {
	return "r-" + strconv.FormatInt(time.Now().UnixMilli(), 10) + "-" + id
}

func newFinderJobContext(parent context.Context, timeout time.Duration) (context.Context, context.CancelFunc) {
	if parent == nil {
		parent = context.Background()
	}
	return context.WithTimeout(parent, timeout)
}

func acceptedFinderJobParent(parent context.Context) context.Context {
	if parent == nil {
		return context.Background()
	}
	// The HTTP request context ends when an asynchronous 202 response is returned. Preserve its
	// values for the accepted operation, but do not let that normal handler lifecycle cancel work
	// that the operation receipt has already promised to run.
	return context.WithoutCancel(parent)
}

func finderReadyEnvelope(requestID string, payload map[string]any) map[string]any {
	envelope := map[string]any{
		"state":    "ready",
		"request":  map[string]any{"id": requestID},
		"data":     payload,
		"ok":       true,
		"terminal": true,
	}
	// Python's response writer preserves established top-level aliases alongside the canonical
	// data object.  apiFetchJson consumes data, while older callers still read entries/content/etc.
	for key, value := range payload {
		if _, exists := envelope[key]; !exists {
			envelope[key] = value
		}
	}
	return envelope
}

func finderFailedEnvelope(requestID string, finderErr *finder.Error) map[string]any {
	messageParams := map[string]any{"path": finderErr.Path}
	if finderErr.MessageParams != nil {
		messageParams = cloneMap(finderErr.MessageParams)
	}
	message := map[string]any{
		"key":      finderErr.MessageKey,
		"params":   messageParams,
		"fallback": finderErr.Message,
	}
	details := finderErr.Payload()
	if finderErr.Details != nil {
		details = cloneMap(finderErr.Details)
	}
	origin := finderErr.Origin
	if origin == "" {
		origin = "finder"
	}
	stack := []any{map[string]any{"component": "finder", "operation": "filesystem", "code": finderErr.Code}}
	if len(finderErr.Stack) > 0 {
		stack = make([]any, 0, len(finderErr.Stack))
		for _, frame := range finderErr.Stack {
			stack = append(stack, cloneMap(frame))
		}
	}
	return map[string]any{
		"state":   "failed",
		"request": map[string]any{"id": requestID},
		"error": map[string]any{
			"code": finderErr.Code, "origin": origin, "retryable": finderErr.Retryable,
			"message": message, "details": details, "stack": stack,
		},
		"user_message": cloneMap(message),
		"legacy_error": finderErr.Message,
		"error_code":   finderErr.Code,
		"retryable":    finderErr.Retryable,
		"status":       finderErr.Status,
		"ok":           false,
		"terminal":     true,
	}
}

func finderHTTPJSON(w http.ResponseWriter, requestID string, payload map[string]any, finderErr *finder.Error) {
	if finderErr != nil {
		jsonResponse(w, finderErr.Status, finderFailedEnvelope(requestID, finderErr))
		return
	}
	jsonResponse(w, http.StatusOK, finderReadyEnvelope(requestID, payload))
}

func finderRequestIDForHTTP() string {
	return finderRequestID("http")
}

func normalizeFinderSearchLimit(raw string) int {
	limit := 400
	if raw != "" {
		if parsed, err := strconv.Atoi(raw); err == nil {
			limit = parsed
		}
	}
	if limit < 1 {
		return 1
	}
	if limit > finder.MaxSearchResults {
		return finder.MaxSearchResults
	}
	return limit
}

func (s *server) enqueueFinderJSON(w http.ResponseWriter, ctx context.Context, route, operation, path string, task func(context.Context) (map[string]any, *finder.Error)) {
	s.enqueueFinderJSONWithScheduler(w, ctx, s.finderJobs, route, operation, path, 0, task)
}

func (s *server) enqueueFinderJSONWithBudget(w http.ResponseWriter, ctx context.Context, route, operation, path string, budget time.Duration, task func(context.Context) (map[string]any, *finder.Error)) {
	s.enqueueFinderJSONWithScheduler(w, ctx, s.finderJobs, route, operation, path, budget, task)
}

func (s *server) enqueueFinderJSONWithScheduler(w http.ResponseWriter, ctx context.Context, scheduler *work.Scheduler, route, operation, path string, budget time.Duration, task func(context.Context) (map[string]any, *finder.Error)) {
	if scheduler == nil || s.events == nil {
		payload, finderErr := task(ctx)
		finderHTTPJSON(w, finderRequestIDForHTTP(), payload, finderErr)
		return
	}
	id := finderOperationID()
	requestID := finderRequestID(id)
	if err := s.acceptOperationWithDetails(id, requestID, route, "filesystem_operation", time.Now().Add(finderJobDeadline).UnixMilli(), map[string]any{"phase": "accepted"}, nil, map[string]any{"operation": operation, "path": path}); err != nil {
		jsonResponse(w, http.StatusServiceUnavailable, map[string]any{"error": "filesystem operation could not be accepted", "error_code": "service_busy"})
		return
	}
	jobContext, cancel := newFinderJobContext(acceptedFinderJobParent(ctx), finderJobDeadline)
	result, err := scheduler.Submit(jobContext, id, func(ctx context.Context) (any, error) {
		payload, finderErr := task(ctx)
		if finderErr != nil {
			return finderErr, nil
		}
		return payload, nil
	})
	if err != nil {
		cancel()
		failure := finderJobFailure(err)
		_, _ = s.terminalizeOperation(id, failure, http.StatusServiceUnavailable)
		jsonResponse(w, http.StatusServiceUnavailable, failure)
		return
	}
	if budget > 0 {
		timer := time.NewTimer(budget)
		defer timer.Stop()
		select {
		case completed := <-result:
			cancel()
			terminal, status := finderJSONCompletion(requestID, completed)
			_, _ = s.terminalizeOperation(id, terminal, status)
			jsonResponse(w, status, terminal)
			return
		case <-timer.C:
		}
	}
	if !s.startFinderCompletion(w, id, cancel, func() { s.finishFinderJSONOperation(id, requestID, result, cancel) }) {
		return
	}
	record := s.operationRecord(id)
	if record == nil {
		jsonResponse(w, http.StatusServiceUnavailable, map[string]any{"error": "filesystem operation receipt was lost", "error_code": "service_busy"})
		return
	}
	jsonResponse(w, http.StatusAccepted, record.Receipt)
}

func (s *server) enqueueFinderDelete(w http.ResponseWriter, ctx context.Context, path string, recursive bool) {
	if s.finderJobs == nil || s.events == nil {
		payload, finderErr := s.finder.Delete(ctx, path, recursive)
		if finderErr == nil && !recursive && payload["pending"] == "subtree" {
			payload, finderErr = s.finder.Delete(ctx, path, true)
		}
		finderHTTPJSON(w, finderRequestIDForHTTP(), payload, finderErr)
		return
	}
	id := finderOperationID()
	requestID := finderRequestID(id)
	if err := s.acceptOperationWithDetails(id, requestID, "POST /api/fs/delete", "filesystem_operation", time.Now().Add(finderJobDeadline).UnixMilli(), map[string]any{"phase": "accepted"}, nil, map[string]any{"operation": "delete", "path": path, "recursive": recursive}); err != nil {
		jsonResponse(w, http.StatusServiceUnavailable, map[string]any{"error": "filesystem operation could not be accepted", "error_code": "service_busy"})
		return
	}
	jobContext, cancel := newFinderJobContext(acceptedFinderJobParent(ctx), finderJobDeadline)
	scheduler := s.finderJobs
	if recursive {
		scheduler = s.finderBulkJobs
	}
	if scheduler == nil {
		cancel()
		failure := finderJobFailure(work.ErrClosed)
		_, _ = s.terminalizeOperation(id, failure, http.StatusServiceUnavailable)
		jsonResponse(w, http.StatusServiceUnavailable, failure)
		return
	}
	result, err := scheduler.Submit(jobContext, id, func(ctx context.Context) (any, error) {
		payload, finderErr := s.finder.Delete(ctx, path, recursive)
		if finderErr != nil {
			return finderErr, nil
		}
		return payload, nil
	})
	if err != nil {
		cancel()
		failure := finderJobFailure(err)
		_, _ = s.terminalizeOperation(id, failure, http.StatusServiceUnavailable)
		jsonResponse(w, http.StatusServiceUnavailable, failure)
		return
	}
	if !s.startFinderCompletion(w, id, cancel, func() { s.finishFinderDeleteOperation(id, requestID, path, result, jobContext, cancel) }) {
		return
	}
	record := s.operationRecord(id)
	if record == nil {
		jsonResponse(w, http.StatusServiceUnavailable, map[string]any{"error": "filesystem operation receipt was lost", "error_code": "service_busy"})
		return
	}
	jsonResponse(w, http.StatusAccepted, record.Receipt)
}

func (s *server) finishFinderDeleteOperation(id, requestID, path string, result <-chan work.Result, ctx context.Context, cancel context.CancelFunc) {
	defer cancel()
	completed := <-result
	if completed.Err != nil {
		payload, status := finderJSONCompletion(requestID, completed)
		_, _ = s.terminalizeOperation(id, payload, status)
		return
	}
	if finderErr, ok := completed.Value.(*finder.Error); ok {
		payload, status := finderJSONCompletion(requestID, work.Result{Value: finderErr})
		_, _ = s.terminalizeOperation(id, payload, status)
		return
	}
	payload, ok := completed.Value.(map[string]any)
	if !ok {
		failure, status := finderJSONCompletion(requestID, work.Result{Err: errors.New("finder delete returned an invalid result")})
		_, _ = s.terminalizeOperation(id, failure, status)
		return
	}
	if payload["pending"] == "subtree" {
		if s.finderBulkJobs == nil {
			failure := finderJobFailure(work.ErrClosed)
			_, _ = s.terminalizeOperation(id, failure, http.StatusServiceUnavailable)
			return
		}
		bulkResult, err := s.finderBulkJobs.Submit(ctx, id, func(ctx context.Context) (any, error) {
			payload, finderErr := s.finder.Delete(ctx, path, true)
			if finderErr != nil {
				return finderErr, nil
			}
			return payload, nil
		})
		if err != nil {
			failure := finderJobFailure(err)
			_, _ = s.terminalizeOperation(id, failure, http.StatusServiceUnavailable)
			return
		}
		completed = <-bulkResult
	}
	terminal, status := finderJSONCompletion(requestID, completed)
	_, _ = s.terminalizeOperation(id, terminal, status)
}

func (s *server) enqueueFinderList(w http.ResponseWriter, ctx context.Context, path string, includeRepo bool, operation string) {
	if s.finderJobs == nil || s.events == nil {
		var payload map[string]any
		var finderErr *finder.Error
		if includeRepo {
			payload, finderErr = s.finder.List(ctx, path)
		} else {
			payload, finderErr = s.finder.FastList(ctx, path)
		}
		if finderErr != nil {
			finderHTTPJSON(w, finderRequestIDForHTTP(), nil, finderErr)
			return
		}
		finderHTTPJSON(w, finderRequestIDForHTTP(), payload, nil)
		return
	}
	id := finderOperationID()
	requestID := finderRequestID(id)
	route := "GET /api/fs/list"
	if !includeRepo {
		route = "GET /api/fs/fast/list"
	}
	if err := s.acceptOperationWithDetails(id, requestID, route, "filesystem_operation", time.Now().Add(finderJobDeadline).UnixMilli(), map[string]any{"phase": "accepted"}, nil, map[string]any{"operation": operation, "path": path}); err != nil {
		jsonResponse(w, http.StatusServiceUnavailable, map[string]any{"error": "filesystem operation could not be accepted", "error_code": "service_busy"})
		return
	}
	jobContext, cancel := newFinderJobContext(acceptedFinderJobParent(ctx), finderJobDeadline)
	result, err := s.finderJobs.Submit(jobContext, id, func(ctx context.Context) (any, error) {
		var payload map[string]any
		var finderErr *finder.Error
		if includeRepo {
			payload, finderErr = s.finder.List(ctx, path)
		} else {
			payload, finderErr = s.finder.FastList(ctx, path)
		}
		if finderErr != nil {
			return finderErr, nil
		}
		return payload, nil
	})
	if err != nil {
		cancel()
		failure := finderJobFailure(err)
		_, _ = s.terminalizeOperation(id, failure, http.StatusServiceUnavailable)
		jsonResponse(w, http.StatusServiceUnavailable, failure)
		return
	}
	timer := time.NewTimer(finderFastListBudget)
	defer timer.Stop()
	select {
	case completed := <-result:
		cancel()
		terminal, status := finderJSONCompletion(requestID, completed)
		if status == http.StatusOK && terminal["state"] == "ready" {
			_, _ = s.terminalizeOperation(id, terminal, status)
			jsonResponse(w, status, terminal)
			return
		}
		_, _ = s.terminalizeOperation(id, terminal, status)
	case <-timer.C:
		if !s.startFinderCompletion(w, id, cancel, func() { s.finishFinderJSONOperation(id, requestID, result, cancel) }) {
			return
		}
	}
	record := s.operationRecord(id)
	if record == nil {
		jsonResponse(w, http.StatusServiceUnavailable, map[string]any{"error": "filesystem operation receipt was lost", "error_code": "service_busy"})
		return
	}
	jsonResponse(w, http.StatusAccepted, record.Receipt)
}

// Receipt delivery remains owned after task workers exit and before journal leases are released.
func (s *server) startFinderCompletion(w http.ResponseWriter, id string, cancel context.CancelFunc, finish func()) bool {
	if s.startBackgroundWork(finish) {
		return true
	}
	cancel()
	failure := finderJobFailure(work.ErrClosed)
	_, _ = s.terminalizeOperation(id, failure, http.StatusServiceUnavailable)
	jsonResponse(w, http.StatusServiceUnavailable, failure)
	return false
}

func (s *server) finishFinderJSONOperation(id, requestID string, result <-chan work.Result, cancel context.CancelFunc) {
	defer cancel()
	completed := <-result
	payload, status := finderJSONCompletion(requestID, completed)
	_, _ = s.terminalizeOperation(id, payload, status)
}

func finderJSONCompletion(requestID string, completed work.Result) (map[string]any, int) {
	if completed.Err != nil {
		if errors.Is(completed.Err, context.Canceled) {
			return map[string]any{"state": "cancelled", "request": map[string]any{"id": requestID}}, http.StatusOK
		}
		if errors.Is(completed.Err, context.DeadlineExceeded) {
			failure := map[string]any{
				"state": "failed", "request": map[string]any{"id": requestID},
				"error": map[string]any{
					"code": "deadline_exceeded", "origin": "finder.work", "retryable": true,
					"message": map[string]any{"key": "request.error.deadlineExceeded", "params": map[string]any{}, "fallback": "filesystem operation deadline exceeded"},
					"details": map[string]any{"deadline_seconds": finderJobDeadline.Seconds()}, "stack": []any{map[string]any{"component": "finder.work", "operation": "bounded-job", "code": "deadline_exceeded"}},
				},
			}
			return failure, http.StatusGatewayTimeout
		}
		return finderJobFailure(completed.Err), http.StatusInternalServerError
	}
	if finderErr, ok := completed.Value.(*finder.Error); ok {
		return finderFailedEnvelope(requestID, finderErr), finderErr.Status
	}
	payload, ok := completed.Value.(map[string]any)
	if !ok {
		return finderJobFailure(errors.New("finder job returned an invalid result")), http.StatusInternalServerError
	}
	return finderReadyEnvelope(requestID, payload), http.StatusOK
}

func finderJobFailure(err error) map[string]any {
	message := "filesystem operation failed"
	if err != nil && err.Error() != "" {
		message = err.Error()
	}
	return map[string]any{
		"state": "failed", "error": map[string]any{
			"code": "service_busy", "origin": "finder.work", "retryable": true,
			"message": map[string]any{"key": "common.requestFailed", "params": map[string]any{}, "fallback": message},
			"details": map[string]any{"error": message}, "stack": []any{map[string]any{"component": "finder.work", "operation": "bounded-job", "code": "service_busy"}},
		}, "error_code": "service_busy", "status": http.StatusServiceUnavailable, "retryable": true,
	}
}

func (s *server) runFinderProduct(ctx context.Context, task func(context.Context) (finderProduct, *finder.Error)) (finderProduct, *finder.Error) {
	return s.runFinderProductWithScheduler(ctx, s.finderJobs, finderJobDeadline, task)
}

func (s *server) runFinderProductWithDeadline(ctx context.Context, deadline time.Duration, task func(context.Context) (finderProduct, *finder.Error)) (finderProduct, *finder.Error) {
	return s.runFinderProductWithScheduler(ctx, s.finderJobs, deadline, task)
}

func (s *server) runFinderStreamProduct(ctx context.Context, task func(context.Context) (finderProduct, *finder.Error)) (finderProduct, *finder.Error) {
	scheduler := s.finderStreamJobs
	if scheduler == nil {
		scheduler = s.finderJobs
	}
	return s.runFinderProductWithSchedulerAndCancelWait(ctx, scheduler, finderJobDeadline, task, true)
}

func (s *server) runFinderProductWithScheduler(ctx context.Context, scheduler *work.Scheduler, deadline time.Duration, task func(context.Context) (finderProduct, *finder.Error)) (finderProduct, *finder.Error) {
	return s.runFinderProductWithSchedulerAndCancelWait(ctx, scheduler, deadline, task, false)
}

func (s *server) runFinderProductWithSchedulerAndCancelWait(ctx context.Context, scheduler *work.Scheduler, deadline time.Duration, task func(context.Context) (finderProduct, *finder.Error), waitForCancellation bool) (finderProduct, *finder.Error) {
	jobContext, cancel := newFinderJobContext(ctx, deadline)
	defer cancel()
	if scheduler == nil {
		return task(jobContext)
	}
	result, err := scheduler.Submit(jobContext, "", func(jobCtx context.Context) (any, error) {
		product, finderErr := task(jobCtx)
		if finderErr != nil {
			return product, finderErr
		}
		return product, nil
	})
	if err != nil {
		return finderProduct{}, &finder.Error{Status: http.StatusServiceUnavailable, Code: "service_busy", MessageKey: "common.requestFailed", Retryable: true, Message: err.Error()}
	}
	var completed work.Result
	select {
	case completed = <-result:
	case <-jobContext.Done():
		if waitForCancellation {
			// Stream tasks may still be writing to the request's ResponseWriter. Do not
			// return from the handler until the cancelled task has stopped using it.
			<-result
		}
		return finderProduct{}, finderJobContextError(jobContext.Err())
	}
	if completed.Err != nil {
		if finderErr, ok := completed.Err.(*finder.Error); ok {
			product, _ := completed.Value.(finderProduct)
			return product, finderErr
		}
		return finderProduct{}, finderJobContextError(completed.Err)
	}
	product, ok := completed.Value.(finderProduct)
	if !ok {
		return finderProduct{}, &finder.Error{Status: http.StatusInternalServerError, Code: "filesystem_error", MessageKey: "fs.error.operationFailed", Retryable: true, Message: "finder product returned an invalid result"}
	}
	return product, nil
}

func finderJobContextError(err error) *finder.Error {
	if errors.Is(err, context.DeadlineExceeded) {
		return &finder.Error{Status: http.StatusGatewayTimeout, Code: "deadline_exceeded", MessageKey: "request.error.deadlineExceeded", Retryable: true, Message: "filesystem operation deadline exceeded"}
	}
	if errors.Is(err, context.Canceled) {
		return &finder.Error{Status: http.StatusRequestTimeout, Code: "cancelled", MessageKey: "request.error.cancelled", Retryable: true, Message: "filesystem operation cancelled"}
	}
	return &finder.Error{Status: http.StatusInternalServerError, Code: "filesystem_error", MessageKey: "fs.error.operationFailed", Retryable: true, Message: err.Error()}
}

// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

// Package work owns bounded, cancellable in-process jobs.  Product packages provide the work;
// this package owns admission, worker count, cancellation, and shutdown.
package work

import (
	"context"
	"errors"
	"fmt"
	"sync"
	"time"
)

var ErrClosed = errors.New("job scheduler is closed")
var ErrBusy = errors.New("job scheduler queue is full")
var ErrDuplicate = errors.New("job scheduler job ID already exists")

const defaultCloseTimeout = 5 * time.Second

type Result struct {
	Value any
	Err   error
}

type Task func(context.Context) (any, error)

type job struct {
	id     string
	ctx    context.Context
	cancel context.CancelFunc
	task   Task
	result chan Result
}

type Scheduler struct {
	ctx    context.Context
	cancel context.CancelFunc
	queue  chan job
	stop   chan struct{}
	wg     sync.WaitGroup
	mu     sync.Mutex
	jobs   map[string]context.CancelFunc
	closed bool
	close  sync.Once
	done   chan struct{}
}

func New(workers, queueSize int) *Scheduler {
	if workers < 1 {
		workers = 1
	}
	if queueSize < 1 {
		queueSize = workers
	}
	ctx, cancel := context.WithCancel(context.Background())
	scheduler := &Scheduler{ctx: ctx, cancel: cancel, queue: make(chan job, queueSize), stop: make(chan struct{}), jobs: make(map[string]context.CancelFunc), done: make(chan struct{})}
	scheduler.wg.Add(workers)
	for range workers {
		go scheduler.worker()
	}
	go func() {
		scheduler.wg.Wait()
		close(scheduler.done)
	}()
	return scheduler
}

func (s *Scheduler) Submit(parent context.Context, id string, task Task) (<-chan Result, error) {
	if parent == nil {
		parent = context.Background()
	}
	if err := parent.Err(); err != nil {
		return nil, err
	}
	ctx, cancelTask := context.WithCancel(parent)
	stop := context.AfterFunc(s.ctx, cancelTask)
	cancel := func() { stop(); cancelTask() }
	result := make(chan Result, 1)
	item := job{id: id, ctx: ctx, cancel: cancel, task: task, result: result}
	s.mu.Lock()
	if s.closed {
		s.mu.Unlock()
		cancel()
		return nil, ErrClosed
	}
	if err := ctx.Err(); err != nil {
		s.mu.Unlock()
		cancel()
		return nil, err
	}
	if id != "" {
		if _, exists := s.jobs[id]; exists {
			s.mu.Unlock()
			cancel()
			return nil, ErrDuplicate
		}
		s.jobs[id] = cancel
	}
	select {
	case s.queue <- item:
		s.mu.Unlock()
		return result, nil
	default:
		if id != "" {
			delete(s.jobs, id)
		}
		s.mu.Unlock()
		cancel()
		return nil, ErrBusy
	}
}

func (s *Scheduler) Cancel(id string) bool {
	if id == "" {
		return false
	}
	s.mu.Lock()
	cancel, ok := s.jobs[id]
	s.mu.Unlock()
	if ok {
		cancel()
	}
	return ok
}

func (s *Scheduler) Close() {
	_ = s.CloseWithTimeout(defaultCloseTimeout)
}

// CloseWithTimeout stops admission, cancels accepted jobs, and waits for workers to exit until
// timeout. A false result means a task did not return before the bound; the scheduler remains
// closed and a later call may observe workers finishing through the same completion signal.
func (s *Scheduler) CloseWithTimeout(timeout time.Duration) bool {
	s.close.Do(func() {
		s.mu.Lock()
		s.closed = true
		s.cancel()
		close(s.stop)
		for _, cancel := range s.jobs {
			cancel()
		}
		s.jobs = map[string]context.CancelFunc{}
		s.mu.Unlock()
	})
	if timeout < 0 {
		timeout = 0
	}
	timer := time.NewTimer(timeout)
	defer timer.Stop()
	select {
	case <-s.done:
		return true
	case <-timer.C:
		return false
	}
}

func (s *Scheduler) worker() {
	defer s.wg.Done()
	for {
		select {
		case <-s.stop:
			s.drainCancelled()
			return
		case item := <-s.queue:
			s.mu.Lock()
			closed := s.closed
			s.mu.Unlock()
			if closed {
				item.cancel()
				s.finish(item, Result{Err: context.Canceled})
				s.drainCancelled()
				return
			}
			s.finish(item, runTask(item))
		}
	}
}

func (s *Scheduler) drainCancelled() {
	for {
		select {
		case item := <-s.queue:
			s.finish(item, Result{Err: context.Canceled})
		default:
			return
		}
	}
}

func (s *Scheduler) remove(id string) {
	if id == "" {
		return
	}
	s.mu.Lock()
	delete(s.jobs, id)
	s.mu.Unlock()
}

// WaitStopped retains dependency ownership after a diagnostic shutdown timeout.
// Callers must stop admission with CloseWithTimeout before waiting.
func (s *Scheduler) WaitStopped() {
	<-s.done
}

func (s *Scheduler) finish(item job, result Result) {
	s.remove(item.id)
	item.cancel()
	item.result <- result
}

func runTask(item job) (result Result) {
	if err := item.ctx.Err(); err != nil {
		return Result{Err: err}
	}
	if item.task == nil {
		return Result{Err: errors.New("job task is nil")}
	}
	defer func() {
		if recovered := recover(); recovered != nil {
			result = Result{Err: fmt.Errorf("job task panicked: %v", recovered)}
		}
	}()
	result.Value, result.Err = item.task(item.ctx)
	return result
}

// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

// Package work owns bounded, cancellable in-process jobs.  Product packages provide the work;
// this package owns admission, worker count, cancellation, and shutdown.
package work

import (
	"context"
	"errors"
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
	scheduler := &Scheduler{queue: make(chan job, queueSize), stop: make(chan struct{}), jobs: make(map[string]context.CancelFunc), done: make(chan struct{})}
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
	ctx, cancel := context.WithCancel(parent)
	result := make(chan Result, 1)
	item := job{id: id, ctx: ctx, cancel: cancel, task: task, result: result}
	s.mu.Lock()
	if s.closed {
		s.mu.Unlock()
		cancel()
		return nil, ErrClosed
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
				item.result <- Result{Err: context.Canceled}
				s.remove(item.id)
				s.drainCancelled()
				return
			}
			if item.task == nil {
				item.result <- Result{Err: errors.New("job task is nil")}
				s.remove(item.id)
				item.cancel()
				continue
			}
			value, err := item.task(item.ctx)
			item.result <- Result{Value: value, Err: err}
			s.remove(item.id)
			item.cancel()
		}
	}
}

func (s *Scheduler) drainCancelled() {
	for {
		select {
		case item := <-s.queue:
			item.cancel()
			item.result <- Result{Err: context.Canceled}
			s.remove(item.id)
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

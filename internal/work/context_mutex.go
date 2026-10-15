// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package work

import (
	"context"
	"errors"
	"sync"
)

// ContextMutex serializes operations while allowing queued callers to cancel.
// Its zero value is ready for use; like sync.Mutex it must not be copied after use.
type ContextMutex struct {
	initOnce sync.Once
	token    chan struct{}
}

func (mutex *ContextMutex) Lock() {
	_ = mutex.LockContext(context.Background(), nil)
}

func (mutex *ContextMutex) LockContext(ctx context.Context, abort <-chan struct{}) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	mutex.initOnce.Do(func() { mutex.token = make(chan struct{}, 1) })
	select {
	case mutex.token <- struct{}{}:
		if err := ctx.Err(); err != nil {
			mutex.Unlock()
			return err
		}
		select {
		case <-abort:
			mutex.Unlock()
			return errors.New("operation is shutting down")
		default:
			return nil
		}
	case <-ctx.Done():
		return ctx.Err()
	case <-abort:
		return errors.New("operation is shutting down")
	}
}

func (mutex *ContextMutex) Unlock() {
	<-mutex.token
}

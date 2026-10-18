// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package atomicfile

import (
	"context"
	"errors"
	"os"
	"sync"
	"syscall"
	"time"
)

// Lock is a persistent-file advisory lease shared with Python's flock owner.
// The file is deliberately retained: unlinking it can create two lock owners.
type Lock struct {
	file *os.File
	once sync.Once
	err  error
}

func AcquireLock(ctx context.Context, path string) (*Lock, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	file, err := os.OpenFile(path, os.O_CREATE|os.O_RDWR|syscall.O_NOFOLLOW, 0600)
	if err != nil {
		return nil, err
	}
	info, err := file.Stat()
	if err != nil {
		return nil, errors.Join(err, file.Close())
	}
	if !info.Mode().IsRegular() {
		return nil, errors.Join(errors.New("lock must be a regular file"), file.Close())
	}
	finish := func() (*Lock, error) {
		lease := &Lock{file: file}
		if err := ctx.Err(); err != nil {
			return nil, errors.Join(err, lease.Close())
		}
		return lease, nil
	}
	try := func() error { return syscall.Flock(int(file.Fd()), syscall.LOCK_EX|syscall.LOCK_NB) }
	if err = try(); err == nil {
		return finish()
	}
	if !errors.Is(err, syscall.EWOULDBLOCK) && !errors.Is(err, syscall.EAGAIN) {
		return nil, errors.Join(err, file.Close())
	}
	ticker := time.NewTicker(10 * time.Millisecond)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return nil, errors.Join(ctx.Err(), file.Close())
		case <-ticker.C:
			if err := ctx.Err(); err != nil {
				return nil, errors.Join(err, file.Close())
			}
			err = try()
			if err == nil {
				return finish()
			}
			if !errors.Is(err, syscall.EWOULDBLOCK) && !errors.Is(err, syscall.EAGAIN) {
				return nil, errors.Join(err, file.Close())
			}
		}
	}
}

func (lock *Lock) Close() error {
	lock.once.Do(func() { lock.err = errors.Join(syscall.Flock(int(lock.file.Fd()), syscall.LOCK_UN), lock.file.Close()) })
	return lock.err
}

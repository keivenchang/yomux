// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

// Package atomicfile stages private state before a caller-controlled publication.
package atomicfile

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"time"
)

type Staged struct{ temporary, destination string }

type durableFile interface {
	io.Writer
	Sync() error
	Close() error
}

func prepare(file durableFile, data []byte) error {
	n, err := file.Write(data)
	if err == nil && n != len(data) {
		err = io.ErrShortWrite
	}
	if err == nil {
		err = file.Sync()
	}
	return errors.Join(err, file.Close())
}

func Stage(path string, data []byte) (*Staged, error) {
	file, err := os.CreateTemp(filepath.Dir(path), ".atomic-*")
	if err != nil {
		return nil, err
	}
	staged := &Staged{temporary: file.Name(), destination: path}
	if err = prepare(file, data); err != nil {
		staged.Cleanup()
		return nil, err
	}
	return staged, nil
}

func (s *Staged) Chmod(mode os.FileMode) error { return os.Chmod(s.temporary, mode) }

func (s *Staged) Cleanup()      { _ = os.Remove(s.temporary) }
func (s *Staged) Commit() error { return os.Rename(s.temporary, s.destination) }

func Write(path string, data []byte) error {
	staged, err := Stage(path, data)
	if err != nil {
		return err
	}
	defer staged.Cleanup()
	if err = staged.Commit(); err != nil {
		return err
	}
	directory, err := os.Open(filepath.Dir(path))
	if err != nil {
		return err
	}
	return errors.Join(directory.Sync(), directory.Close())
}

// LoadOrCreateSecret publishes a complete 32-byte key without replacing a
// concurrent creator's key. Hard-link publication prevents partial readers.
func LoadOrCreateSecret(path string) ([]byte, error) {
	return loadOrCreateSecret(context.Background(), path, false)
}

// LoadOrCreateRawSecret preserves the binary chat-key format. Existing keys
// may exceed 32 bytes; every stored byte is significant to the signature.
func LoadOrCreateRawSecret(ctx context.Context, path string) ([]byte, error) {
	return loadOrCreateSecret(ctx, path, true)
}

var errIncompleteSecret = errors.New("raw secret is shorter than 32 bytes")

func loadOrCreateSecret(ctx context.Context, path string, rawEncoding bool) ([]byte, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return nil, err
	}

	read := func() ([]byte, error) {
		info, err := os.Lstat(path)
		if err != nil {
			return nil, err
		}
		if !info.Mode().IsRegular() {
			return nil, fmt.Errorf("secret must be a regular file")
		}
		raw, err := os.ReadFile(path)
		if err != nil {
			return nil, err
		}
		if rawEncoding {
			if len(raw) < 32 {
				return nil, errIncompleteSecret
			}
			return raw, nil
		}
		key, err := hex.DecodeString(strings.TrimSpace(string(raw)))
		if err != nil || len(key) != 32 {
			return nil, fmt.Errorf("invalid secret")
		}
		return key, nil
	}
	readComplete := func() ([]byte, error) { return waitForCompleteSecret(ctx, read) }
	if key, err := readComplete(); err == nil {
		return key, nil
	} else if !os.IsNotExist(err) {
		return nil, err
	}
	key := make([]byte, 32)
	if _, err := rand.Read(key); err != nil {
		return nil, err
	}
	stored := key
	if !rawEncoding {
		stored = []byte(hex.EncodeToString(key) + "\n")
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	staged, err := Stage(path, stored)
	if err != nil {
		return nil, err
	}
	defer staged.Cleanup()
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	if err = os.Link(staged.temporary, path); err != nil {
		if os.IsExist(err) {
			return readComplete()
		}
		return nil, err
	}
	directory, err := os.Open(filepath.Dir(path))
	if err != nil {
		return nil, err
	}
	if err = errors.Join(directory.Sync(), directory.Close()); err != nil {
		return nil, err
	}
	return key, nil
}

func waitForCompleteSecret(ctx context.Context, read func() ([]byte, error)) ([]byte, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	value, err := read()
	if !errors.Is(err, errIncompleteSecret) {
		return value, err
	}
	// Go publishes complete files atomically. Retain a bounded compatibility
	// wait for an older Python creator between O_EXCL and its first fsync.
	deadline, cancel := context.WithTimeout(ctx, time.Second)
	defer cancel()
	ticker := time.NewTicker(7 * time.Millisecond)
	defer ticker.Stop()
	for {
		select {
		case <-deadline.Done():
			return nil, errors.Join(errIncompleteSecret, deadline.Err())
		case <-ticker.C:
			value, err = read()
			if !errors.Is(err, errIncompleteSecret) {
				return value, err
			}
		}
	}
}

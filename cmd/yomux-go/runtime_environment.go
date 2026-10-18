// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"bytes"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"strings"
	"time"

	"github.com/keivenchang/yomux/internal/productenv"
)

// Discovery and child processes use one PATH without changing the process-wide
// environment while concurrent requests are running.
func nativeRuntimePath() (string, error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	existing := []string{}
	for _, entry := range filepath.SplitList(os.Getenv("PATH")) {
		if entry != "" {
			existing = append(existing, entry)
		}
	}
	candidates := []string{}
	for _, entry := range filepath.SplitList(productenv.Value("EXTRA_PATH")) {
		if entry == "" {
			continue
		}
		if entry == "~" {
			entry = home
		} else if strings.HasPrefix(entry, "~/") {
			entry = filepath.Join(home, entry[2:])
		}
		candidates = append(candidates, entry)
	}
	local := filepath.Join(home, ".local", "bin")
	if info, err := os.Stat(local); err == nil && info.IsDir() {
		candidates = append(candidates, local)
	} else if err != nil && !os.IsNotExist(err) {
		return "", fmt.Errorf("inspect local command directory: %w", err)
	}
	additions := []string{}
	for _, entry := range candidates {
		if !slices.Contains(existing, entry) && !slices.Contains(additions, entry) {
			additions = append(additions, entry)
		}
	}
	return strings.Join(append(additions, existing...), string(os.PathListSeparator)), nil
}

func nativeCommandPath(name string) (string, error) {
	path, err := nativeRuntimePath()
	if err != nil {
		return "", err
	}
	for _, directory := range filepath.SplitList(path) {
		candidate, err := filepath.Abs(filepath.Join(directory, name))
		if err != nil {
			return "", err
		}
		executable, err := exec.LookPath(candidate)
		if err == nil {
			return executable, nil
		}
	}
	return "", &exec.Error{Name: name, Err: exec.ErrNotFound}
}

func nativeRuntimeEnvironment(overrides map[string]string) ([]string, error) {
	path, err := nativeRuntimePath()
	if err != nil {
		return nil, err
	}
	values := map[string]string{"PATH": path}
	for name, value := range overrides {
		values[name] = value
	}
	environment := make([]string, 0, len(os.Environ())+len(values))
	for _, entry := range os.Environ() {
		name, _, _ := strings.Cut(entry, "=")
		if _, replace := values[name]; !replace {
			environment = append(environment, entry)
		}
	}
	for name, value := range values {
		environment = append(environment, name+"="+value)
	}
	return environment, nil
}

func defaultStateDir() (string, error) {
	state := productenv.Value("STATE_DIR")
	if state == "" {
		if root := productenv.Value("ROOT"); root != "" {
			state = filepath.Join(root, "state")
		} else {
			home, err := os.UserHomeDir()
			if err != nil {
				return "", fmt.Errorf("resolve YOmux state directory: %w", err)
			}
			state = filepath.Join(home, ".local", "state", "yomux")
		}
	}
	resolved, err := filepath.Abs(state)
	if err != nil {
		return "", fmt.Errorf("resolve YOmux state directory %q: %w", state, err)
	}
	return resolved, nil
}

var errNativeCommandOutputLimit = errors.New("native command output exceeds limit")

type nativeCommandBuffer struct {
	data  bytes.Buffer
	limit int
	err   error
}

func (output *nativeCommandBuffer) Write(data []byte) (int, error) {
	if output.data.Len()+len(data) > output.limit {
		output.err = errNativeCommandOutputLimit
		return 0, output.err
	}
	return output.data.Write(data)
}

func nativeCommandOutput(command *exec.Cmd, limit int) ([]byte, []byte, error) {
	stdout, stderr := nativeCommandBuffer{limit: limit}, nativeCommandBuffer{limit: limit}
	command.Stdout = &stdout
	command.Stderr = &stderr
	command.WaitDelay = 250 * time.Millisecond
	err := command.Run()
	return stdout.data.Bytes(), stderr.data.Bytes(), errors.Join(err, stdout.err, stderr.err)
}

// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"github.com/keivenchang/yomux/internal/finder"
)

// Installed resources stay independent of the source checkout. Only updates
// follow this explicit provenance, which is revalidated before activation.
type nativeInstallSource struct {
	Schema     int    `json:"schema"`
	SourceRoot string `json:"source_root"`
	Prefix     string `json:"prefix"`
}

func nativeRepositoryPath(checkout, option string) ([]byte, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	command := exec.CommandContext(ctx, "git", "-C", checkout, "rev-parse", option)
	command.Env = finder.GitReadEnvironment()
	return command.CombinedOutput()
}

func verifyUpdateCheckout(root string) (string, error) {
	if !filepath.IsAbs(root) {
		return "", fmt.Errorf("update source must be an absolute checkout path")
	}
	canonical, err := filepath.EvalSymlinks(root)
	if err != nil {
		return "", fmt.Errorf("resolve update source: %w", err)
	}
	output, err := nativeRepositoryPath(canonical, "--show-toplevel")
	if err != nil {
		return "", fmt.Errorf("verify update source checkout: %w: %s", err, strings.TrimSpace(string(output)))
	}
	toplevel, err := filepath.EvalSymlinks(strings.TrimSpace(string(output)))
	if err != nil || toplevel != canonical {
		return "", fmt.Errorf("update source is not the recorded checkout root")
	}
	return canonical, nil
}

// Runtime admission validates the installation without following its update
// source: the installed server remains usable after that checkout moves.
func readNativeInstallation(root string) (*nativeInstallSource, error) {
	path := filepath.Join(root, "install-source.json")
	info, err := os.Lstat(path)
	if os.IsNotExist(err) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	if !info.Mode().IsRegular() {
		return nil, errors.New("installation provenance must be a regular file")
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var installation nativeInstallSource
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&installation); err != nil {
		return nil, fmt.Errorf("decode installation provenance: %w", err)
	}
	var trailing any
	if err := decoder.Decode(&trailing); !errors.Is(err, io.EOF) {
		return nil, errors.New("installation provenance contains trailing data")
	}
	if err := validateNativeInstallation(root, installation); err != nil {
		return nil, err
	}
	return &installation, nil
}

func nativeUpdateSource(root string) (string, *nativeInstallSource, error) {
	installation, err := readNativeInstallation(root)
	if err != nil {
		return "", nil, err
	}
	source := root
	if installation != nil {
		source = installation.SourceRoot
	}
	verified, err := verifyUpdateCheckout(source)
	return verified, installation, err
}

func validateNativeInstallation(root string, installation nativeInstallSource) error {
	if installation.Schema != 1 || !filepath.IsAbs(installation.SourceRoot) || !filepath.IsAbs(installation.Prefix) || filepath.Clean(installation.Prefix) == "/" {
		return fmt.Errorf("invalid installation provenance")
	}
	expected, err := filepath.EvalSymlinks(filepath.Join(installation.Prefix, "lib", "yomux"))
	if err != nil {
		return err
	}
	actual, err := filepath.EvalSymlinks(root)
	if err != nil || actual != expected {
		return fmt.Errorf("installation prefix does not own the running resource root")
	}
	return nil
}

func recordNativeInstallSource(root, source, prefix string) error {
	verified, err := verifyUpdateCheckout(source)
	if err != nil {
		return err
	}
	installation := nativeInstallSource{Schema: 1, SourceRoot: verified, Prefix: prefix}
	if err := validateNativeInstallation(root, installation); err != nil {
		return err
	}
	data, err := json.Marshal(installation)
	if err != nil {
		return err
	}
	return writeAtomicFile(filepath.Join(root, "install-source.json"), append(data, '\n'))
}

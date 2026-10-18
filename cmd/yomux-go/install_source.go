// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

// Installed resources stay independent of the source checkout. Only updates
// follow this explicit provenance, which is revalidated before activation.
type nativeInstallSource struct {
	Schema     int    `json:"schema"`
	SourceRoot string `json:"source_root"`
	Prefix     string `json:"prefix"`
}

func verifyUpdateCheckout(root string) (string, error) {
	if !filepath.IsAbs(root) {
		return "", fmt.Errorf("update source must be an absolute checkout path")
	}
	canonical, err := filepath.EvalSymlinks(root)
	if err != nil {
		return "", fmt.Errorf("resolve update source: %w", err)
	}
	output, err := exec.Command("git", "-C", canonical, "rev-parse", "--show-toplevel").CombinedOutput()
	if err != nil {
		return "", fmt.Errorf("verify update source checkout: %w: %s", err, strings.TrimSpace(string(output)))
	}
	toplevel, err := filepath.EvalSymlinks(strings.TrimSpace(string(output)))
	if err != nil || toplevel != canonical {
		return "", fmt.Errorf("update source is not the recorded checkout root")
	}
	return canonical, nil
}

func nativeUpdateSource(root string) (string, *nativeInstallSource, error) {
	data, err := os.ReadFile(filepath.Join(root, "install-source.json"))
	if os.IsNotExist(err) {
		source, err := verifyUpdateCheckout(root)
		return source, nil, err
	}
	if err != nil {
		return "", nil, err
	}
	var installation nativeInstallSource
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&installation); err != nil {
		return "", nil, fmt.Errorf("decode installation provenance: %w", err)
	}
	if err := validateNativeInstallation(root, installation); err != nil {
		return "", nil, err
	}
	source, err := verifyUpdateCheckout(installation.SourceRoot)
	return source, &installation, err
}

func validateNativeInstallation(root string, installation nativeInstallSource) error {
	if installation.Schema != 1 || !filepath.IsAbs(installation.Prefix) || filepath.Clean(installation.Prefix) == "/" {
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

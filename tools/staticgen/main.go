// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"flag"
	"fmt"
	"os"
	"path/filepath"

	"github.com/keivenchang/yomux/internal/mutablefs"
	"github.com/keivenchang/yomux/internal/staticassets"
)

// Build actions and self-update own private output trees. Development tooling
// publishes into the shared checkout under its separate writer lease.
func run(source, output string, check bool) error {
	return runWithPolicy(source, output, check, false)
}

func runWithPolicy(source, output string, check, bazelOutput bool) error {
	if bazelOutput && !isBazelOutputPath(output) {
		return fmt.Errorf("Bazel output publication must stay under bazel-out")
	}
	root, err := mutablefs.Canonical(source)
	if err != nil {
		return err
	}
	destination, err := mutablefs.Canonical(output)
	if err != nil {
		return err
	}
	if !check && !bazelOutput {
		for parent := destination; ; parent = filepath.Dir(parent) {
			if _, err := os.Lstat(filepath.Join(parent, ".git")); err == nil {
				return fmt.Errorf("output must be outside a Git worktree")
			} else if !os.IsNotExist(err) {
				return err
			}
			if parent == filepath.Dir(parent) {
				break
			}
		}
	}
	outputs, err := staticassets.Build(root)
	if err != nil {
		return err
	}
	changed, err := staticassets.Publish(destination, outputs, check)
	if err != nil {
		return err
	}
	if check && len(changed) > 0 {
		return fmt.Errorf("stale static assets: %v", changed)
	}
	return nil
}

func isBazelOutputPath(path string) bool {
	absolute, err := filepath.Abs(path)
	if err != nil {
		return false
	}
	for current := filepath.Clean(absolute); ; current = filepath.Dir(current) {
		if filepath.Base(current) == "bazel-out" {
			return true
		}
		parent := filepath.Dir(current)
		if parent == current {
			return false
		}
	}
}

func main() {
	source := flag.String("source-root", ".", "declared source tree")
	output := flag.String("output-directory", "", "private generated output directory (required)")
	check := flag.Bool("check", false, "fail if generated output differs")
	bazelOutput := flag.Bool("bazel-output", false, "allow publication into Bazel's declared output tree")
	flag.Parse()
	if *output == "" || flag.NArg() != 0 {
		fmt.Fprintln(os.Stderr, "--output-directory is required; positional arguments are unsupported")
		os.Exit(2)
	}
	if err := runWithPolicy(*source, *output, *check, *bazelOutput); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

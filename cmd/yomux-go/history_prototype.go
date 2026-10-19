// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"os"
	"os/signal"
	"path/filepath"
	"runtime"
	"syscall"

	"github.com/keivenchang/yomux/internal/finder"
)

func runHistoryPrototypeDiagnostic(args []string) error {
	flags := flag.NewFlagSet("--diagnose-history-prototype", flag.ContinueOnError)
	root := flags.String("root", "", "Finder authorization root (required)")
	path := flags.String("repo", "", "whole repository root (required)")
	limit := flags.Int("limit", 50, "commits per request (1..200)")
	offset := flags.Int("offset", 0, "history offset (0..1000000)")
	head := flags.String("head", "", "optional frozen HEAD; a changed HEAD fails")
	if err := flags.Parse(args); err != nil {
		if errors.Is(err, flag.ErrHelp) {
			return nil
		}
		return err
	}
	if *root == "" || *path == "" || flags.NArg() != 0 {
		return errors.New("--root and --repo are required")
	}
	if runtime.GOOS != "darwin" {
		return errors.New("this snapshot comparison diagnostic requires macOS")
	}
	canonicalRoot, err := filepath.Abs(*root)
	if err != nil {
		return err
	}
	service, err := finder.New(canonicalRoot, nil)
	if err != nil {
		return err
	}
	defer service.Close()
	executable, err := os.Executable()
	if err != nil {
		return err
	}
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	report := struct {
		Ordering         string                              `json:"ordering"`
		CacheMeasurement string                              `json:"cache_measurement"`
		Qualified        bool                                `json:"qualified"`
		Runs             []finder.HistoryPrototypeComparison `json:"runs"`
	}{Ordering: "native --topo-order versus go-git LogOrderCommitterTime", CacheMeasurement: "initial and three repeats; includes helper startup; OS cache state uncontrolled", Qualified: true}
	for i := 0; i < 4; i++ {
		comparison, err := service.CompareHistoryPrototype(ctx, *path, executable, finder.HistoryPrototypeRequest{Limit: *limit, Offset: *offset, Head: *head})
		if err != nil {
			return err
		}
		comparison.Iteration = i
		report.Runs = append(report.Runs, comparison)
		report.Qualified = report.Qualified && comparison.Qualified
		if ctx.Err() != nil {
			break
		}
	}
	encoder := json.NewEncoder(os.Stdout)
	encoder.SetIndent("", "  ")
	return encoder.Encode(report)
}

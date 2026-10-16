// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"fmt"
	"os"

	"github.com/keivenchang/yomux/internal/productversion"
)

func main() {
	if len(os.Args) != 3 {
		fmt.Fprintln(os.Stderr, "usage: versiongen <version.yaml> <output.go>")
		os.Exit(2)
	}
	source, err := os.Open(os.Args[1])
	if err != nil {
		fatal(err)
	}
	version, parseErr := productversion.Parse(source)
	closeErr := source.Close()
	if parseErr != nil {
		fatal(parseErr)
	}
	if closeErr != nil {
		fatal(closeErr)
	}
	content := fmt.Sprintf("// Code generated from version.yaml; DO NOT EDIT.\n\npackage main\n\nconst version = %q\n", version)
	if err := os.WriteFile(os.Args[2], []byte(content), 0o644); err != nil {
		fatal(err)
	}
}

func fatal(err error) {
	fmt.Fprintln(os.Stderr, err)
	os.Exit(1)
}

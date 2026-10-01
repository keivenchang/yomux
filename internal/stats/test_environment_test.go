// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package stats

import (
	"fmt"
	"os"
	"testing"

	"github.com/keivenchang/yomux/internal/testsupport"
)

func TestMain(main *testing.M) {
	cleanup, err := testsupport.NewPrivateTestEnvironment()
	if err != nil {
		fmt.Fprintln(os.Stderr, "create private test environment:", err)
		os.Exit(2)
	}
	code := main.Run()
	cleanup()
	os.Exit(code)
}

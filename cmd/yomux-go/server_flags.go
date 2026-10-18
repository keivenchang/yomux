// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"flag"

	"github.com/keivenchang/yomux/internal/productenv"
)

type nativeServerFlags struct {
	managedLaunch     *bool
	launchPlanJSON    *string
	forceLaunch       *bool
	port              *int
	listenFD          *int
	launchLeaseFDs    *string
	host              *string
	root              *string
	configDir         *string
	statsPath         *string
	httpMode          *bool
	certPath          *string
	keyPath           *string
	dangerouslyYolo   *bool
	searchIndexWorker *bool
	searchIndexDB     *string
	showVersion       *bool
}

// Startup and kernel argv verification must interpret every flag identically.
func registerServerFlags(flags *flag.FlagSet) nativeServerFlags {
	var options nativeServerFlags
	options.managedLaunch = flags.Bool("launch", false, "apply native managed instance planning")
	options.launchPlanJSON = flags.String("launch-plan-json", "", "captured managed launch plan")
	options.forceLaunch = flags.Bool("force", false, "replace this checkout's verified listener")
	flags.String("backend", "go", "Go backend selector (go or auto)")
	flags.Bool("golang", false, "alias for the Go backend")
	options.port = flags.Int("port", 7221, "HTTP port")
	options.listenFD = flags.Int("listen-fd", -1, "use an already-bound TCP listener file descriptor")
	options.launchLeaseFDs = flags.String("launch-lease-fds", "", "managed launcher lease descriptors")
	options.host = flags.String("host", "127.0.0.1", "bind address")
	options.root = flags.String("root", ".", "YOmux checkout root")
	options.configDir = flags.String("config", productenv.Value("CONFIG_DIR"), "YOmux config directory")
	options.statsPath = flags.String("stats", productenv.Value("STATS_DATABASE"), "YO!stats database path")
	options.httpMode = flags.Bool("http", false, "serve plain HTTP instead of the default HTTPS")
	options.certPath = flags.String("cert", "", "TLS certificate PEM path")
	options.keyPath = flags.String("key", "", "TLS private key PEM path")
	options.dangerouslyYolo = flags.Bool("dangerously-yolo", false, "allow new Claude/Codex sessions to use dangerous bypass flags")
	options.searchIndexWorker = flags.Bool("search-index-worker", false, "run the private Finder index worker")
	options.searchIndexDB = flags.String("index-db", "", "private Finder index database path")
	flags.BoolVar(options.dangerouslyYolo, "dang", false, "alias for --dangerously-yolo")
	options.showVersion = flags.Bool("version", false, "print version")
	return options
}

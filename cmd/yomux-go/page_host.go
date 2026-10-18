// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"bufio"
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"sort"
	"strconv"
	"strings"
	"time"
	_ "time/tzdata"

	"github.com/keivenchang/yomux/internal/stats"
)

type pageVersion struct {
	Status, SHA, TimePT, Error string
	Count                      int
	Commits                    []string
}

func (s *server) pageVersion(parent context.Context) pageVersion {
	result := pageVersion{Status: version, TimePT: "commit time unavailable", Commits: []string{}}
	location, err := time.LoadLocation("America/Los_Angeles")
	if err != nil {
		result.Error = err.Error()
		return result
	}
	git := func(args ...string) (string, error) {
		ctx, cancel := context.WithTimeout(parent, time.Second)
		defer cancel()
		output, _, err := nativeCommandOutput(exec.CommandContext(ctx, "git", append([]string{"-C", s.root}, args...)...), 1024*1024)
		if err != nil {
			return "", fmt.Errorf("version metadata git %s: %w", args[0], err)
		}
		return strings.TrimSpace(string(output)), nil
	}
	recordError := func(err error) {
		if err != nil {
			if result.Error != "" {
				result.Error += "; "
			}
			result.Error += err.Error()
		}
	}
	if output, err := git("log", "v"+version+"^{}..HEAD", "--format=%cI%x09%s"); err == nil {
		for _, line := range strings.Split(output, "\n") {
			if line == "" {
				continue
			}
			timestamp, subject, found := strings.Cut(line, "\t")
			at, parseErr := time.Parse(time.RFC3339, timestamp)
			if !found || parseErr != nil {
				recordError(fmt.Errorf("malformed version commit metadata"))
				result.Commits = []string{}
				break
			}
			result.Commits = append(result.Commits, at.In(location).Format("2006-01-02 15:04:05 PT")+" "+subject)
		}
	} else {
		recordError(err)
	}
	if len(result.Commits) > 0 {
		result.Status += fmt.Sprintf("(%d)", len(result.Commits))
	}
	if status, err := git("status", "--porcelain=v2", "--untracked-files=all"); err == nil {
		if pageVersionTrackedChanges(status) {
			result.Status += "*"
		}
	} else {
		recordError(err)
	}
	if sha, err := git("rev-parse", "--short=12", "HEAD"); err == nil {
		result.SHA = sha
	} else {
		recordError(err)
	}
	if info, err := git("show", "-s", "--format=%cI%x09%H", "HEAD"); err == nil {
		timestamp, _, _ := strings.Cut(info, "\t")
		at, err := time.Parse(time.RFC3339, timestamp)
		if err == nil {
			result.TimePT = at.In(location).Format("2006-01-02 15:04:05 PT")
		} else {
			recordError(err)
		}
	} else {
		recordError(err)
	}
	if count, err := git("rev-list", "--count", "HEAD"); err == nil {
		result.Count, err = strconv.Atoi(count)
		recordError(err)
	} else {
		recordError(err)
	}
	return result
}

func pageVersionTrackedChanges(status string) bool {
	for _, line := range strings.Split(status, "\n") {
		if line == "" || strings.HasPrefix(line, "? ") || strings.HasPrefix(line, "! ") {
			continue
		}
		if strings.HasPrefix(line, "1 ") {
			fields := strings.Fields(line)
			if len(fields) >= 9 && strings.Trim(fields[6], "0") == "" {
				continue
			}
		}
		return true
	}
	return false
}

func pageCommandAvailable(name string) bool { _, err := nativeCommandPath(name); return err == nil }

func pageTerminalCommands() []string {
	candidates := []string{filepath.Base(os.Getenv("SHELL")), "bash", "zsh", "fish", "sh", "dash", "ksh", "csh", "tcsh", "nu", "pwsh", "tsh"}
	if data, err := os.ReadFile("/etc/shells"); err == nil {
		for _, line := range strings.Split(string(data), "\n") {
			line = strings.TrimSpace(line)
			if line != "" && !strings.HasPrefix(line, "#") {
				candidates = append(candidates, filepath.Base(line))
			}
		}
	}
	names := map[string]bool{}
	for _, name := range candidates {
		if name != "tmux" && validSessionLaunchName(name) && pageCommandAvailable(name) {
			names[name] = true
		}
	}
	result := make([]string, 0, len(names))
	for name := range names {
		result = append(result, name)
	}
	sort.Slice(result, func(i, j int) bool { return strings.ToLower(result[i]) < strings.ToLower(result[j]) })
	return result
}

func pageCPUTopology() map[string]int {
	logical, physical := runtime.NumCPU(), 0
	if runtime.GOOS == "linux" {
		if data, err := os.ReadFile("/sys/devices/system/cpu/online"); err == nil {
			if count := stats.ParseLinuxOnlineCPUCount(data); count > 0 {
				logical = count
			}
		}
		if data, err := os.ReadFile("/proc/cpuinfo"); err == nil {
			cores := map[string]bool{}
			scanner := bufio.NewScanner(strings.NewReader(string(data)))
			socket, core := "", ""
			collect := func() {
				if socket != "" && core != "" {
					cores[socket+":"+core] = true
				}
				socket, core = "", ""
			}
			for scanner.Scan() {
				line := strings.TrimSpace(scanner.Text())
				if line == "" {
					collect()
					continue
				}
				key, value, found := strings.Cut(line, ":")
				if found {
					switch strings.TrimSpace(key) {
					case "physical id":
						socket = strings.TrimSpace(value)
					case "core id":
						core = strings.TrimSpace(value)
					}
				}
			}
			collect()
			if scanner.Err() == nil {
				physical = len(cores)
			}
		}
	} else if runtime.GOOS == "darwin" {
		ctx, cancel := context.WithTimeout(context.Background(), time.Second)
		defer cancel()
		if output, err := exec.CommandContext(ctx, "sysctl", "-n", "hw.logicalcpu", "hw.physicalcpu").Output(); err == nil {
			fields := strings.Fields(string(output))
			if len(fields) == 2 {
				if count, err := strconv.Atoi(fields[0]); err == nil && count > 0 {
					logical = count
				}
				if count, err := strconv.Atoi(fields[1]); err == nil && count > 0 {
					physical = count
				}
			}
		}
	}
	result := map[string]int{}
	if logical > 0 {
		result["logical_cpus"] = logical
	}
	if physical > 0 && physical <= logical {
		result["physical_cores"] = physical
	}
	return result
}

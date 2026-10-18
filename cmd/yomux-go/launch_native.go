// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"bytes"
	"crypto/hmac"
	"crypto/sha256"
	"crypto/tls"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"slices"
	"sort"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/keivenchang/yomux/internal/mutablefs"
	"github.com/keivenchang/yomux/internal/productenv"
	"github.com/keivenchang/yomux/internal/productversion"
	"github.com/keivenchang/yomux/internal/staticassets"
	"github.com/keivenchang/yomux/internal/stats"
)

// Launch plans contain only root assignments. Credentials never cross this boundary.
type nativeRowPlan struct {
	Unset  []string          `json:"unset"`
	Assign map[string]string `json:"assign"`
}

var launchRootSettings = []string{"ROOT", "CONFIG_DIR", "STATE_DIR", "CACHE_DIR", "RUNTIME_DIR", "CODEX_HOME", "HOST_ARTIFACT_DIR", "START_LOCK_DIR", "LOG_DIR", "CA_DIR", "TOOL_LOCK_PATH"}

func launchInstanceKeys() []string {
	keys := []string{"CODEX_HOME", "PYTHONPYCACHEPREFIX", "TMPDIR", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "XDG_RUNTIME_DIR"}
	for _, setting := range append(slices.Clone(launchRootSettings), "INSTANCE", "EARLY_INSTANCE_PORT", "MANAGED_INSTANCE_PORT", "GENERATED_PYTHONPYCACHEPREFIX", "ROW_PLAN_FILE") {
		keys = append(keys, productenv.Names(setting)...)
	}
	return keys
}

func validateNativeBackend(args []string) error {
	for _, name := range productenv.Names("BACKEND") {
		if value, exists := os.LookupEnv(name); exists && value != "" && value != "go" && value != "auto" {
			return fmt.Errorf("%s=%q is unsupported; the Python server is retired (use go or auto)", name, value)
		}
	}
	for index, arg := range args {
		if !strings.HasPrefix(arg, "-") {
			continue
		}
		option := strings.TrimPrefix(strings.TrimPrefix(arg, "-"), "-")
		value, selected := strings.CutPrefix(option, "backend=")
		if option == "deprecated-python" {
			return fmt.Errorf("--deprecated-python is retired; use the Go server")
		}
		if option == "backend" {
			if index+1 == len(args) {
				return fmt.Errorf("--backend requires go or auto")
			}
			value, selected = args[index+1], true
		}
		if selected && value != "go" && value != "auto" {
			return fmt.Errorf("unsupported backend %q; use go or auto", value)
		}
	}
	return nil
}

func nativeProductPath(root, name, value string, rejectHome bool) (string, error) {
	if !filepath.IsAbs(value) {
		return "", fmt.Errorf("%s requires an absolute path", name)
	}
	path, err := mutablefs.Canonical(value)
	if err != nil {
		return "", err
	}
	home, err := mutablefs.Canonical(os.Getenv("HOME"))
	if err != nil {
		return "", err
	}
	if path == string(filepath.Separator) || (rejectHome && path == home) {
		return "", fmt.Errorf("%s cannot use the filesystem root or HOME", name)
	}
	checkout, err := mutablefs.Canonical(root)
	if err != nil {
		return "", err
	}
	if path == checkout || strings.HasPrefix(path, checkout+string(filepath.Separator)) {
		return "", fmt.Errorf("%s cannot resolve inside the shared worktree", name)
	}
	return path, nil
}

func validateNativeRoots(checkout string) error {
	home := os.Getenv("HOME")
	if !filepath.IsAbs(home) || filepath.Clean(home) == "/" {
		return fmt.Errorf("HOME must be an absolute non-root path")
	}
	if _, err := nativeProductPath(checkout, "HOME", home, false); err != nil {
		return err
	}
	root := productenv.Value("ROOT")
	if root != "" {
		var err error
		root, err = nativeProductPath(checkout, "ROOT", root, true)
		if err != nil {
			return err
		}
	}
	for _, setting := range launchRootSettings {
		value := productenv.Value(setting)
		if value == "" {
			continue
		}
		path, err := nativeProductPath(checkout, setting, value, true)
		if err != nil {
			return err
		}
		if root != "" && slices.Contains([]string{"CONFIG_DIR", "STATE_DIR", "CACHE_DIR", "RUNTIME_DIR", "CODEX_HOME", "HOST_ARTIFACT_DIR"}, setting) && path != root && !strings.HasPrefix(path, root+string(filepath.Separator)) {
			return fmt.Errorf("%s resolves outside YOMUX_ROOT", setting)
		}
	}
	if root != "" {
		runtimeDir := productenv.Value("RUNTIME_DIR")
		if runtimeDir == "" {
			runtimeDir = filepath.Join(root, "runtime")
		}
		limit := 107
		if runtime.GOOS == "darwin" {
			limit = 96
		}
		if len(filepath.Join(runtimeDir, "control/yolomux-4194304-ffffffffffffffff.sock")) > limit {
			return fmt.Errorf("YOMUX_ROOT is too deep for product sockets")
		}
	}
	for _, name := range []string{"TMPDIR", "XDG_RUNTIME_DIR", "XDG_CACHE_HOME", "PYTHONPYCACHEPREFIX"} {
		if value := os.Getenv(name); value != "" {
			if _, err := nativeProductPath(checkout, name, value, false); err != nil {
				return err
			}
		}
	}
	return nil
}

func nativeLaunchPlan(checkout string, port int, row bool) (nativeRowPlan, error) {
	if port < 1 || port > 65535 {
		return nativeRowPlan{}, fmt.Errorf("invalid port")
	}
	plan := nativeRowPlan{Unset: launchInstanceKeys(), Assign: map[string]string{}}
	explicit := false
	for _, setting := range []string{"ROOT", "CONFIG_DIR", "STATE_DIR", "CACHE_DIR", "RUNTIME_DIR"} {
		explicit = explicit || productenv.Value(setting) != ""
	}
	defaultPort := 7110
	if runtime.GOOS == "darwin" {
		defaultPort = 8880
	}
	if !row && explicit {
		if err := validateNativeRoots(checkout); err != nil {
			return plan, err
		}
		for _, key := range plan.Unset {
			if value := os.Getenv(key); value != "" && key != "YOMUX_INSTANCE" && key != "YOLOMUX_INSTANCE" && !strings.Contains(key, "EARLY_INSTANCE_PORT") && !strings.Contains(key, "MANAGED_INSTANCE_PORT") && !strings.Contains(key, "ROW_PLAN_FILE") {
				plan.Assign[key] = value
			}
		}
	} else if port != defaultPort {
		base := "/tmp"
		if !row && os.Getenv("TMPDIR") != "" {
			var err error
			base, err = nativeProductPath(checkout, "TMPDIR", os.Getenv("TMPDIR"), false)
			if err != nil {
				return plan, err
			}
		}
		plan.Assign[productenv.Name("ROOT")] = filepath.Join(base, "y"+strconv.Itoa(os.Getuid()), "p"+strconv.Itoa(port))
		plan.Assign[productenv.Name("INSTANCE")] = strconv.Itoa(port) + ":managed"
	}
	return plan, nil
}

func decodeNativePlan(data []byte) (nativeRowPlan, error) {
	var plan nativeRowPlan
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&plan); err != nil {
		return plan, err
	}
	if plan.Unset == nil || plan.Assign == nil {
		return plan, fmt.Errorf("row plan requires unset and assign")
	}
	if err := decoder.Decode(new(any)); err != io.EOF {
		return plan, fmt.Errorf("trailing row plan data")
	}
	allowed := launchInstanceKeys()
	for _, key := range plan.Unset {
		if !slices.Contains(allowed, key) {
			return plan, fmt.Errorf("non-instance unset key %q", key)
		}
	}
	for key := range plan.Assign {
		if !slices.Contains(allowed, key) {
			return plan, fmt.Errorf("non-instance assignment %q", key)
		}
	}
	return plan, nil
}

func applyNativePlan(plan nativeRowPlan) error {
	for _, key := range plan.Unset {
		if err := os.Unsetenv(key); err != nil {
			return err
		}
	}
	for key, value := range plan.Assign {
		if err := os.Setenv(key, value); err != nil {
			return err
		}
	}
	normalizeYomuxEnvironment()
	return nil
}

// A census must account for every target listening inode before reporting owners.
func nativeListenerPIDs(port int) ([]int, error) {
	if port < 1 || port > 65535 {
		return nil, fmt.Errorf("invalid port")
	}
	if runtime.GOOS != "linux" {
		command := exec.Command("lsof", "-nP", "-a", "-iTCP:"+strconv.Itoa(port), "-sTCP:LISTEN", "-Fp")
		output, err := command.Output()
		if err != nil {
			var exit *exec.ExitError
			if !errors.As(err, &exit) || exit.ExitCode() != 1 {
				return nil, fmt.Errorf("listener census: %w", err)
			}
		}
		pids := []int{}
		for _, line := range strings.Fields(string(output)) {
			if strings.HasPrefix(line, "p") {
				pid, err := strconv.Atoi(line[1:])
				if err != nil {
					return nil, err
				}
				if !slices.Contains(pids, pid) {
					pids = append(pids, pid)
				}
			}
		}
		sort.Ints(pids)
		return pids, nil
	}
	inodes := map[string]bool{}
	for _, table := range []string{"/proc/net/tcp", "/proc/net/tcp6"} {
		data, err := os.ReadFile(table)
		if err != nil {
			return nil, err
		}
		for _, line := range strings.Split(string(data), "\n")[1:] {
			fields := strings.Fields(line)
			if len(fields) < 10 || fields[3] != "0A" {
				continue
			}
			_, raw, found := strings.Cut(fields[1], ":")
			number, err := strconv.ParseInt(raw, 16, 32)
			if found && err == nil && int(number) == port {
				inodes[fields[9]] = false
			}
		}
	}
	if len(inodes) == 0 {
		return []int{}, nil
	}
	entries, err := os.ReadDir("/proc")
	if err != nil {
		return nil, err
	}
	pids := []int{}
	for _, entry := range entries {
		pid, err := strconv.Atoi(entry.Name())
		if err != nil {
			continue
		}
		fds, err := os.ReadDir(filepath.Join("/proc", entry.Name(), "fd"))
		if err != nil {
			continue
		}
		owns := false
		for _, fd := range fds {
			target, err := os.Readlink(filepath.Join("/proc", entry.Name(), "fd", fd.Name()))
			if err != nil {
				continue
			}
			if strings.HasPrefix(target, "socket:[") && strings.HasSuffix(target, "]") {
				inode := strings.TrimSuffix(strings.TrimPrefix(target, "socket:["), "]")
				if _, found := inodes[inode]; found {
					inodes[inode] = true
					owns = true
				}
			}
		}
		if owns {
			pids = append(pids, pid)
		}
	}
	for _, accounted := range inodes {
		if !accounted {
			return nil, fmt.Errorf("listener census cannot account for every owner on port %d", port)
		}
	}
	sort.Ints(pids)
	return pids, nil
}

func nativeProbe(checkout, host string, port int, httpMode bool, path string) (map[string]any, error) {
	config := defaultConfigDir()
	scheme := "https"
	if httpMode {
		scheme = "http"
	}
	if host == "" || host == "0.0.0.0" || host == "::" {
		host = "localhost"
	}
	request, err := http.NewRequest(http.MethodGet, scheme+"://"+net.JoinHostPort(host, strconv.Itoa(port))+path, nil)
	if err != nil {
		return nil, err
	}
	// Read the existing secret; probes must never create authentication state.
	secretBytes, secretErr := os.ReadFile(filepath.Join(config, "auth-cookie-secret"))
	users := (&server{config: config}).users()
	if len(users) > 0 {
		if secretErr != nil {
			return nil, secretErr
		}
		secret, err := hex.DecodeString(strings.TrimSpace(string(secretBytes)))
		if err != nil || len(secret) != 32 {
			return nil, fmt.Errorf("invalid authentication secret")
		}
		user := users[0]
		for _, candidate := range users {
			if candidate.role == "admin" {
				user = candidate
				break
			}
		}
		digest := hmac.New(sha256.New, secret)
		_, _ = digest.Write([]byte(user.username + ":" + user.password))
		request.AddCookie(&http.Cookie{Name: "yomux_auth_" + strconv.Itoa(port), Value: hex.EncodeToString(digest.Sum(nil))})
	}
	transport := &http.Transport{TLSClientConfig: &tls.Config{InsecureSkipVerify: true}, Proxy: nil}
	defer transport.CloseIdleConnections()
	client := &http.Client{Transport: transport, Timeout: 3 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	response, err := client.Do(request)
	if err != nil {
		return nil, err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("probe %s returned HTTP %d", path, response.StatusCode)
	}
	var payload map[string]any
	err = json.NewDecoder(io.LimitReader(response.Body, 4<<20)).Decode(&payload)
	return payload, err
}

func nativeLaunchReady(checkout, host string, port int, httpMode bool, sessions []string) error {
	payload, err := nativeListenerProbe(checkout, host, port, httpMode)
	if err != nil {
		return err
	}
	if payload["version"] != version {
		return fmt.Errorf("unexpected executable version or backend")
	}
	pids, err := nativeListenerPIDs(port)
	if err != nil {
		return err
	}
	if len(pids) != 1 {
		return fmt.Errorf("expected exactly one listener, got %d", len(pids))
	}
	if _, err := verifyNativeListener(checkout, host, port, httpMode, pids[0]); err != nil {
		return err
	}
	if err := verifyNativeExecutable(pids[0]); err != nil {
		return err
	}
	for _, session := range sessions {
		payload, err := nativeProbe(checkout, host, port, httpMode, "/api/tmux-session-exists?session="+url.QueryEscape(session))
		if err != nil {
			return err
		}
		if payload["exists"] != true {
			return fmt.Errorf("session %q was not discovered", session)
		}
	}
	return nil
}

func nativeManagedPreflight(checkout, host string, port int, httpMode, force bool, planJSON string) error {
	return nativeManagedPreflightWithMode(checkout, host, port, httpMode, force, true, planJSON)
}

func nativeManagedPreflightWithMode(checkout, host string, port int, httpMode, force, stop bool, planJSON string) error {
	if err := nativePrepareLaunch(checkout, port, planJSON); err != nil {
		return err
	}
	pids, err := nativeListenerPIDs(port)
	if err != nil {
		return err
	}
	if len(pids) == 0 {
		return nativeRootsAvailable(checkout, 0)
	}
	if !force {
		return fmt.Errorf("port %d already has a listener; use --force to replace a verified YOmux process", port)
	}
	if len(pids) != 1 {
		return fmt.Errorf("port %d has ambiguous listener ownership", port)
	}
	pid := pids[0]
	identity, err := verifyNativeReplacementListener(checkout, host, port, httpMode, pid)
	if err != nil {
		return err
	}
	if err := nativeRootsAvailable(checkout, pid); err != nil {
		return err
	}
	// Revalidate both the kernel listener and generation at the signaling boundary.
	current, err := nativeListenerPIDs(port)
	currentIdentity, _, proofErr := nativeProcessProof(pid)
	if err != nil || proofErr != nil || !slices.Equal(current, pids) || currentIdentity != identity {
		return fmt.Errorf("listener generation changed before replacement")
	}
	if !stop {
		return nil
	}
	if err := syscall.Kill(pid, syscall.SIGTERM); err != nil {
		return err
	}
	return nativeWaitReplacementRelease(checkout, pid, identity, 15*time.Second, nativeProcessProof)
}

func nativeWaitReplacementRelease(checkout string, pid int, identity string, timeout time.Duration, processProof func(int) (string, string, error)) error {
	deadline := time.Now().Add(timeout)
	ticker := time.NewTicker(50 * time.Millisecond)
	defer ticker.Stop()
	var pending error
	for {
		currentIdentity, _, proofErr := processProof(pid)
		if proofErr != nil || currentIdentity != identity {
			// Executable visibility may disappear before the kernel closes the old
			// process's files. Storage ownership, not that visibility, admits reuse.
			pending = nativeRootsAvailable(checkout, 0)
			if pending == nil {
				return nil
			}
		} else {
			pending = fmt.Errorf("verified listener %d did not exit", pid)
		}
		if !time.Now().Before(deadline) {
			return fmt.Errorf("replacement resources did not become available; no other process was signaled: %w", pending)
		}
		<-ticker.C
	}
}

func runNativeOperator(args []string) error {
	if len(args) == 0 {
		return fmt.Errorf("usage: yomux-go --operator <plan|plan-direct|exec|validate-roots|resolve-product-path|resolve-state-dir|listeners|ready|sessions|version> [options]")
	}
	action := args[0]
	options := flag.NewFlagSet("operator "+action, flag.ContinueOnError)
	checkout := options.String("root", ".", "checkout root")
	sourceRoot := options.String("source-root", "", "verified source checkout for installed updates")
	installPrefix := options.String("prefix", "", "native installation prefix")
	port := options.Int("port", 7110, "instance port")
	host := options.String("host", "localhost", "probe host")
	httpMode := options.Bool("http", false, "plain HTTP")
	certificate := options.String("cert", "", "TLS certificate PEM path")
	key := options.String("key", "", "TLS private key PEM path")
	planFile := options.String("plan-file", "", "captured row plan")
	planJSON := options.String("plan-json", "", "captured row plan JSON")
	preflight := options.Bool("preflight", false, "validate without changing processes")
	printPlan := options.Bool("print-plan", false, "print the selected profile plan")
	printCommand := options.Bool("print-command", false, "print the selected profile launch")
	checkAssets := options.Bool("check-assets", false, "validate tracked browser assets")
	pid := options.Int("pid", 0, "listener process")
	sessions := options.String("sessions", "", "comma-separated session names")
	timeout := options.Duration("timeout", 60*time.Second, "readiness deadline")
	if err := options.Parse(args[1:]); err != nil {
		return err
	}
	if action == "verify-replacement" {
		if err := validateNativeTLS(*httpMode, *certificate, *key); err != nil {
			return err
		}
	}
	absolute, err := filepath.Abs(*checkout)
	if err != nil {
		return err
	}
	if *planFile != "" || *planJSON != "" {
		data := []byte(*planJSON)
		if *planFile != "" {
			data, err = os.ReadFile(*planFile)
			if err != nil {
				return err
			}
		}
		plan, err := decodeNativePlan(data)
		if err != nil {
			return err
		}
		if err = applyNativePlan(plan); err != nil {
			return err
		}
	}
	switch action {
	case "copy-file":
		paths := options.Args()
		if len(paths) != 2 {
			return fmt.Errorf("copy-file requires -- SOURCE DESTINATION")
		}
		info, err := os.Stat(paths[0])
		if err != nil {
			return err
		}
		if !info.Mode().IsRegular() {
			return fmt.Errorf("installation source is not a regular file")
		}
		return copyAtomic(paths[0], paths[1], info.Mode().Perm())
	case "update-source":
		source, _, err := nativeUpdateSource(absolute)
		if err == nil {
			fmt.Println(source)
		}
		return err
	case "record-install-source":
		return recordNativeInstallSource(absolute, *sourceRoot, *installPrefix)
	case "plan", "plan-direct":
		plan, err := nativeLaunchPlan(absolute, *port, action == "plan")
		if err != nil {
			return err
		}
		return json.NewEncoder(os.Stdout).Encode(plan)
	case "profile":
		return runNativeProfile(absolute, *sessions, *port, *preflight, *printPlan, *printCommand, *checkAssets)
	case "verify-environment":
		data, err := os.ReadFile(*planFile)
		if err != nil {
			return err
		}
		plan, err := decodeNativePlan(data)
		if err != nil {
			return err
		}
		return verifyNativeEnvironment(absolute, *host, *port, *httpMode, *pid, plan)
	case "root-available":
		plan, err := nativeLaunchPlan(absolute, *port, false)
		if err != nil {
			return err
		}
		if err := applyNativePlan(plan); err != nil {
			return err
		}
		if err := validateNativeRoots(absolute); err != nil {
			return err
		}
		return nativeRootsAvailable(absolute, 0)
	case "exec":
		remaining := options.Args()
		if len(remaining) == 0 {
			return fmt.Errorf("exec requires -- <command>")
		}
		if err := validateNativeRoots(absolute); err != nil {
			return err
		}
		binary, err := exec.LookPath(remaining[0])
		if err != nil {
			return err
		}
		return syscall.Exec(binary, remaining, os.Environ())
	case "validate-roots":
		return validateNativeRoots(absolute)
	case "resolve-state-dir":
		if err := validateNativeRoots(absolute); err != nil {
			return err
		}
		value, err := defaultStateDir()
		if err != nil {
			return err
		}
		fmt.Println(value)
		return nil
	case "resolve-product-path":
		values := options.Args()
		if len(values) != 2 {
			return fmt.Errorf("resolve-product-path requires KEY PATH")
		}
		value, err := nativeProductPath(absolute, values[0], values[1], values[0] != "TMPDIR")
		if err != nil {
			return err
		}
		fmt.Println(value)
		return nil
	case "listeners":
		pids, err := nativeListenerPIDs(*port)
		if err != nil {
			return err
		}
		for _, pid := range pids {
			fmt.Println(pid)
		}
		return nil
	case "verify-replacement":
		return nativeManagedPreflightWithMode(absolute, *host, *port, *httpMode, true, false, *planJSON)
	case "ready", "sessions":
		if *planFile == "" && *planJSON == "" {
			plan, err := nativeLaunchPlan(absolute, *port, false)
			if err != nil {
				return err
			}
			if err := applyNativePlan(plan); err != nil {
				return err
			}
		}
		if err := validateNativeRoots(absolute); err != nil {
			return err
		}
		deadline := time.Now().Add(*timeout)
		ticker := time.NewTicker(200 * time.Millisecond)
		defer ticker.Stop()
		var last error
		expected := []string{}
		if *sessions != "" {
			expected = strings.Split(*sessions, ",")
		}
		for {
			last = nativeLaunchReady(absolute, *host, *port, *httpMode, expected)
			if last == nil {
				return nil
			}
			if time.Now().After(deadline) {
				return last
			}
			<-ticker.C
		}
	case "tls-san":
		value, err := nativeTLSSAN()
		if err != nil {
			return err
		}
		fmt.Println(value)
		return nil
	case "build-assets":
		return nativePublishAssets(absolute)
	case "version":
		if err := verifyNativeVersion(absolute); err != nil {
			return err
		}
		fmt.Println(version)
		return nil
	default:
		return fmt.Errorf("unsupported native operator command %q", action)
	}
}

func nativeMutableRoots() []string {
	home := os.Getenv("HOME")
	root := productenv.Value("ROOT")
	roots := []string{}
	for _, item := range []struct{ setting, leaf, fallback string }{
		{"CONFIG_DIR", "config", filepath.Join(home, ".config", "yomux")},
		{"STATE_DIR", "state", filepath.Join(home, ".local", "state", "yomux")},
		{"CACHE_DIR", "cache", filepath.Join(home, ".cache", "yomux")},
		{"RUNTIME_DIR", "runtime", filepath.Join("/tmp", "yomux-server-"+strconv.Itoa(os.Getuid()), "shared")},
	} {
		value := productenv.Value(item.setting)
		if value == "" {
			value = item.fallback
			if root != "" {
				value = filepath.Join(root, item.leaf)
			}
		}
		if !slices.Contains(roots, value) {
			roots = append(roots, value)
		}
	}
	sort.Strings(roots)
	return roots
}

func nativeRootsAvailable(checkout string, allowPID int) error {
	roots := nativeMutableRoots()
	productRoot := productenv.Value("ROOT")
	if productRoot == "" {
		productRoot = checkout
	}
	roots = append(roots, productRoot)
	paths := []string{filepath.Join(defaultConfigDir(), "go-storage.lock")}
	for _, root := range roots {
		paths = append(paths, filepath.Join(root, "instance.lock"))
	}
	for _, path := range paths {
		file, err := os.OpenFile(path, os.O_RDWR|syscall.O_NOFOLLOW, 0)
		if os.IsNotExist(err) {
			continue
		}
		if err != nil {
			return err
		}
		err = syscall.Flock(int(file.Fd()), syscall.LOCK_EX|syscall.LOCK_NB)
		if err == nil {
			err = syscall.Flock(int(file.Fd()), syscall.LOCK_UN)
			closeErr := file.Close()
			if err != nil {
				return err
			}
			if closeErr != nil {
				return closeErr
			}
			continue
		}
		data, readErr := io.ReadAll(io.LimitReader(file, 16384))
		closeErr := file.Close()
		var record struct {
			PID int `json:"pid"`
		}
		if allowPID == 0 || readErr != nil || closeErr != nil || json.Unmarshal(data, &record) != nil || record.PID != allowPID {
			return fmt.Errorf("product storage is already owned or unavailable: %s", path)
		}
	}
	return nil
}

func nativeAcquireMutableLeases() ([]*os.File, error) {
	leases := []*os.File{}
	for _, root := range nativeMutableRoots() {
		if root == productenv.Value("ROOT") {
			continue
		}
		lease, err := acquireDirectoryLease(root, "instance.lock")
		if err != nil {
			for _, held := range leases {
				releaseInstanceRootLease(held)
			}
			return nil, err
		}
		leases = append(leases, lease)
	}
	return leases, nil
}

func verifyNativeVersion(checkout string) error {
	source, err := os.Open(filepath.Join(checkout, "version.yaml"))
	if err != nil {
		return err
	}
	expected, parseErr := productversion.Parse(source)
	closeErr := source.Close()
	if err := errors.Join(parseErr, closeErr); err != nil {
		return err
	}
	if expected != version {
		return fmt.Errorf("Go binary version %s does not match checkout %s; rebuild before restarting", version, expected)
	}
	return nil
}

// Publish under the same declaration slot as Python developer tools. A busy or
// malformed owner refuses publication; this path never reclaims another writer.
func nativePublishAssets(checkout string) error {
	output, err := exec.Command("git", "-C", checkout, "rev-parse", "--absolute-git-dir").Output()
	if err != nil {
		return fmt.Errorf("resolve asset writer slot: %w", err)
	}
	slot := filepath.Join(strings.TrimSpace(string(output)), "yolomux", "worktree-writer")
	if err := os.MkdirAll(filepath.Dir(slot), 0700); err != nil {
		return err
	}
	if err := os.Mkdir(slot, 0700); err != nil {
		return fmt.Errorf("checkout already has an asset/test writer: %w", err)
	}
	recordPath := filepath.Join(slot, "owner.json")
	token := fmt.Sprintf("native-assets-%d-%d", os.Getpid(), time.Now().UnixNano())
	defer func() {
		data, err := os.ReadFile(recordPath)
		if err == nil {
			var record map[string]any
			if json.Unmarshal(data, &record) == nil && record["token"] == token {
				if err := os.Remove(recordPath); err != nil {
					fmt.Fprintln(os.Stderr, err)
				}
				if err := os.Remove(slot); err != nil {
					fmt.Fprintln(os.Stderr, err)
				}
			}
		}
	}()
	hostID, err := productHostPartitionID()
	if err != nil {
		_ = os.Remove(slot)
		return err
	}
	hostname, err := os.Hostname()
	if err != nil {
		_ = os.Remove(slot)
		return err
	}
	now := float64(time.Now().UnixNano()) / 1e9
	record := map[string]any{"schema": 1, "token": token, "purpose": "native-static-build", "stable_host_id": hostID, "hostname": hostname, "boot_id": readIdentityValue("", "/proc/sys/kernel/random/boot_id", "unknown-boot"), "pid": os.Getpid(), "process_start_identity": processStartIdentity(os.Getpid()), "declared_at": now, "heartbeat_at": now}
	payload, err := json.Marshal(record)
	if err != nil {
		_ = os.Remove(slot)
		return err
	}
	if err := os.WriteFile(recordPath, payload, 0600); err != nil {
		_ = os.Remove(slot)
		return err
	}
	outputs, err := staticassets.Build(checkout)
	if err != nil {
		return err
	}
	// Re-read immediately before publication so losing the declaration cannot authorize a write.
	data, err := os.ReadFile(recordPath)
	if err != nil {
		return err
	}
	var current map[string]any
	if err := json.Unmarshal(data, &current); err != nil {
		return err
	}
	if current["token"] != token {
		return fmt.Errorf("asset writer lease was replaced")
	}
	if time.Since(time.Unix(0, int64(now*1e9))) > 30*time.Second {
		return fmt.Errorf("asset writer lease expired before publication")
	}
	_, err = staticassets.Publish(filepath.Join(checkout, "static"), outputs, false)
	return err
}

func nativeTLSSAN() (string, error) {
	hostname, err := os.Hostname()
	if err != nil {
		return "", err
	}
	names := []string{"DNS:localhost", "IP:127.0.0.1", "IP:::1"}
	if hostname != "localhost" && !strings.ContainsAny(hostname, " ,/\n\r\t") {
		names = append(names, "DNS:"+hostname)
	}
	interfaces, err := net.Interfaces()
	if err != nil {
		return "", err
	}
	for _, network := range interfaces {
		if strings.HasPrefix(network.Name, "docker") || strings.HasPrefix(network.Name, "br-") || strings.HasPrefix(network.Name, "veth") {
			continue
		}
		addresses, err := network.Addrs()
		if err != nil {
			return "", err
		}
		for _, address := range addresses {
			raw, _, _ := strings.Cut(address.String(), "/")
			raw, _, _ = strings.Cut(raw, "%")
			ip := net.ParseIP(raw)
			if ip == nil || ip.IsLoopback() || ip.IsUnspecified() {
				continue
			}
			value := "IP:" + ip.String()
			if !slices.Contains(names, value) {
				names = append(names, value)
			}
		}
	}
	return strings.Join(names, ","), nil
}

func nativeProcessProof(pid int) (string, string, error) {
	if runtime.GOOS == "darwin" {
		row, err := stats.DarwinProcessForPID(pid)
		if err != nil {
			return "", "", err
		}
		return row.Identity, row.Executable, nil
	}
	identity := processStartIdentity(pid)
	executable, err := os.Readlink(filepath.Join("/proc", strconv.Itoa(pid), "exe"))
	if err != nil {
		return "", "", err
	}
	if identity == "" {
		return "", "", fmt.Errorf("process generation unavailable")
	}
	return identity, strings.TrimSuffix(executable, " (deleted)"), nil
}

// Authentication and launch checks share a cheap service proof. The detailed
// status endpoint performs host-wide metrics work and cannot admit a restart.
func nativeListenerProbe(checkout, host string, port int, httpMode bool) (map[string]any, error) {
	path := "/api/ping"
	if len((&server{config: defaultConfigDir()}).users()) == 0 {
		path = "/healthz"
	}
	payload, err := nativeProbe(checkout, host, port, httpMode, path)
	if err != nil {
		return nil, err
	}
	if payload["backend"] != "go" {
		return nil, fmt.Errorf("listener is not the Go service")
	}
	return payload, nil
}

func nativeVerifyCommandRoot(checkout, directory string, arguments []string) error {
	if len(arguments) == 0 {
		return fmt.Errorf("listener process arguments are unavailable")
	}
	flags := flag.NewFlagSet("listener identity", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	options := registerServerFlags(flags)
	if err := flags.Parse(arguments[1:]); err != nil {
		return fmt.Errorf("parse listener process arguments: %w", err)
	}
	root := *options.root
	if root == "" || directory == "" || !filepath.IsAbs(directory) {
		return fmt.Errorf("listener checkout root is unverified")
	}
	if !filepath.IsAbs(root) {
		root = filepath.Join(directory, root)
	}
	canonical, err := mutablefs.Canonical(root)
	if err != nil {
		return fmt.Errorf("resolve listener checkout root: %w", err)
	}
	expected, err := mutablefs.Canonical(checkout)
	if err != nil {
		return fmt.Errorf("resolve expected checkout root: %w", err)
	}
	if canonical != expected {
		return fmt.Errorf("listener checkout root is unverified")
	}
	return nil
}

func nativeVerifyListenerRoot(checkout string, pid int) error {
	arguments, err := nativeProcessArguments(pid)
	if err != nil {
		return err
	}
	directory, err := os.Readlink(filepath.Join("/proc", strconv.Itoa(pid), "cwd"))
	if err != nil {
		return err
	}
	return nativeVerifyCommandRoot(checkout, directory, arguments)
}

func nativeValidateAPIIdentity(payload map[string]any, checkout string, pid int) error {
	root, rootOK := payload["root"].(string)
	observedPID, pidOK := payload["pid"].(float64)
	if payload["backend"] != "go" || !rootOK || !filepath.IsAbs(root) || !pidOK || observedPID != float64(pid) {
		return fmt.Errorf("listener API root/pid identity is missing or does not match process %d", pid)
	}
	actual, err := mutablefs.Canonical(root)
	if err != nil {
		return fmt.Errorf("resolve listener API root: %w", err)
	}
	expected, err := mutablefs.Canonical(checkout)
	if err != nil {
		return err
	}
	if actual != expected {
		return fmt.Errorf("listener API checkout root does not match %s", expected)
	}
	return nil
}

func nativeListenerAPIIdentity(checkout, host string, port int, httpMode bool, pid int, allowLegacy bool) (map[string]any, error) {
	payload, err := nativeListenerProbe(checkout, host, port, httpMode)
	if err != nil {
		return nil, err
	}
	_, rootPresent := payload["root"]
	_, pidPresent := payload["pid"]
	authenticated := len((&server{config: defaultConfigDir()}).users()) > 0
	if !rootPresent && !pidPresent && authenticated && allowLegacy {
		legacy, err := nativeProbe(checkout, host, port, httpMode, "/api/system-status")
		if err != nil {
			return nil, fmt.Errorf("legacy listener identity proof failed: %w", err)
		}
		var ok bool
		payload, ok = legacy["server"].(map[string]any)
		if !ok {
			return nil, fmt.Errorf("legacy listener root/pid identity is unavailable")
		}
	}
	if !authenticated {
		// Setup has no credential proof. Only the selected trusted executable may
		// supply identity; older setup processes safely refuse replacement.
		if err := verifyNativeExecutable(pid); err != nil {
			return nil, fmt.Errorf("unauthenticated listener executable proof failed: %w", err)
		}
	}
	if err := nativeValidateAPIIdentity(payload, checkout, pid); err != nil {
		return nil, err
	}
	return payload, nil
}

func verifyNativeListener(checkout, host string, port int, httpMode bool, pid int) (string, error) {
	identity, executable, err := nativeProcessProof(pid)
	if err != nil {
		return "", err
	}
	if filepath.Base(executable) != "yomux-go" {
		return "", fmt.Errorf("listener executable is not yomux-go")
	}
	if runtime.GOOS == "darwin" {
		if _, err := nativeListenerAPIIdentity(checkout, host, port, httpMode, pid, true); err != nil {
			return "", err
		}
	} else {
		if err := nativeVerifyListenerRoot(checkout, pid); err != nil {
			return "", err
		}
		if _, err := nativeListenerProbe(checkout, host, port, httpMode); err != nil {
			return "", err
		}
	}
	current, _, err := nativeProcessProof(pid)
	if err != nil || current != identity {
		return "", fmt.Errorf("listener generation changed during proof")
	}
	return identity, nil
}

func verifyNativeReplacementListener(checkout, host string, port int, desiredHTTP bool, pid int) (string, error) {
	identity, _, err := nativeProcessProof(pid)
	if err != nil {
		return "", err
	}
	var failures []error
	// Replacement may change transport. Both candidates retain the complete
	// ownership/authentication proof; readiness still requires the requested one.
	for _, currentHTTP := range []bool{desiredHTTP, !desiredHTTP} {
		verified, err := verifyNativeListener(checkout, host, port, currentHTTP, pid)
		if err == nil {
			if verified != identity {
				return "", fmt.Errorf("listener generation changed during transport verification")
			}
			return verified, nil
		}
		failures = append(failures, err)
	}
	return "", errors.Join(failures...)
}

func decodeNativeEnvironment(data []byte) map[string]string {
	values := map[string]string{}
	for _, entry := range bytes.Split(data, []byte{0}) {
		key, value, ok := strings.Cut(string(entry), "=")
		if ok {
			values[key] = value
		}
	}
	return values
}

func verifyNativeEnvironment(checkout, host string, port int, httpMode bool, pid int, plan nativeRowPlan) error {
	identity, _, err := nativeProcessProof(pid)
	if err != nil {
		return err
	}
	var actual map[string]string
	if runtime.GOOS == "darwin" {
		pids, err := nativeListenerPIDs(port)
		if err != nil {
			return fmt.Errorf("listener environment port ownership: %w", err)
		}
		if !slices.Equal(pids, []int{pid}) {
			return fmt.Errorf("listener environment has ambiguous port ownership")
		}
		if err := verifyNativeExecutable(pid); err != nil {
			return fmt.Errorf("listener environment executable proof failed: %w", err)
		}
		payload, err := nativeListenerAPIIdentity(checkout, host, port, httpMode, pid, false)
		if err != nil {
			return err
		}
		actual, err = nativeAPIEnvironment(payload)
		if err != nil {
			return err
		}
	} else {
		actual, err = nativeProcessEnvironment(pid)
		if err != nil {
			return err
		}
	}
	if err := nativeVerifyEnvironmentValues(actual, plan); err != nil {
		return err
	}
	current, _, err := nativeProcessProof(pid)
	if err != nil || current != identity {
		return fmt.Errorf("listener generation changed during environment verification")
	}
	return nil
}

func nativeAPIEnvironment(payload map[string]any) (map[string]string, error) {
	values, ok := payload["launch_environment"].(map[string]any)
	if !ok {
		return nil, fmt.Errorf("listener launch environment identity is unavailable")
	}
	keys := nativeLaunchIdentityKeys()
	actual := map[string]string{}
	for key, value := range values {
		text, valid := value.(string)
		if !slices.Contains(keys, key) || !valid {
			return nil, fmt.Errorf("listener launch environment contains an unverified key %q", key)
		}
		actual[key] = text
	}
	for _, key := range keys {
		if _, present := actual[key]; !present {
			return nil, fmt.Errorf("listener launch environment is missing %s", key)
		}
	}
	for _, setting := range []string{"ROOT", "INSTANCE"} {
		names := productenv.Names(setting)
		if actual[names[0]] != actual[names[1]] {
			return nil, fmt.Errorf("listener launch environment has conflicting %s aliases", setting)
		}
	}
	return actual, nil
}

func nativeVerifyEnvironmentValues(actual map[string]string, plan nativeRowPlan) error {
	var err error
	for _, setting := range []string{"ROOT", "INSTANCE"} {
		expected := ""
		present := false
		for _, key := range productenv.Names(setting) {
			if value, ok := plan.Assign[key]; ok {
				expected = value
				present = true
				break
			}
		}
		if !present {
			continue
		}
		value := ""
		for _, key := range productenv.Names(setting) {
			if found, ok := actual[key]; ok {
				value = found
				break
			}
		}
		if setting == "ROOT" && value != "" {
			value, err = mutablefs.Canonical(value)
			if err != nil {
				return err
			}
			expected, err = mutablefs.Canonical(expected)
			if err != nil {
				return err
			}
		}
		if value != expected {
			return fmt.Errorf("listener %s differs from its captured row plan", setting)
		}
	}
	return nil
}

func runNativeProfile(checkout, session string, port int, preflight, printPlan, printCommand, checkAssets bool) error {
	if session == "" || strings.ContainsAny(session, "/\\\x00") {
		return fmt.Errorf("profile requires a configured session name")
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return err
	}
	launcher := filepath.Join(home, "dev/ai-config/agents/skills/yomux-dev-start/yomux-dev-start.sh")
	table, err := exec.Command(launcher, "--print-plan", "--only", session).Output()
	if err != nil {
		return fmt.Errorf("resolve profile row: %w", err)
	}
	found := false
	for _, line := range strings.Split(string(table), "\n") {
		fields := strings.Split(line, "|")
		if len(fields) != 5 || fields[0] != session {
			continue
		}
		if found {
			return fmt.Errorf("ambiguous profile row")
		}
		found = true
		rowPort, err := strconv.Atoi(fields[3])
		if err != nil {
			return err
		}
		rowRoot, err := mutablefs.Canonical(fields[1])
		if err != nil {
			return err
		}
		expectedRoot, err := mutablefs.Canonical(checkout)
		if err != nil {
			return err
		}
		if port != rowPort || expectedRoot != rowRoot {
			return fmt.Errorf("profile roots and port must match the selected session table row")
		}
	}
	if !found {
		return fmt.Errorf("profile has no selected row")
	}
	path := filepath.Join(home, ".config/yomux-operations", session+".json")
	data, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	var profile struct {
		Session                string            `json:"session"`
		Environment            map[string]string `json:"environment"`
		Certificate            string            `json:"certificate"`
		Key                    string            `json:"key"`
		StaticFallbackManifest string            `json:"static_fallback_manifest"`
	}
	if err := json.Unmarshal(data, &profile); err != nil {
		return err
	}
	if profile.Session != session {
		return fmt.Errorf("profile does not match selected session")
	}
	if profile.StaticFallbackManifest != "" {
		data, err := os.ReadFile(profile.StaticFallbackManifest)
		if err != nil {
			return err
		}
		var state struct {
			State string `json:"state"`
		}
		if err := json.Unmarshal(data, &state); err != nil {
			return err
		}
		if state.State != "canonical" {
			return fmt.Errorf("retired Python cutover profile must be reconciled before native restart; no processes changed")
		}
	}
	if _, err := tls.LoadX509KeyPair(profile.Certificate, profile.Key); err != nil {
		return fmt.Errorf("profile TLS identity: %w", err)
	}
	for _, entry := range os.Environ() {
		key, _, _ := strings.Cut(entry, "=")
		if _, product := productenv.Suffix(key); product || strings.HasPrefix(key, "XDG_") {
			if err := os.Unsetenv(key); err != nil {
				return err
			}
		}
	}
	for key, value := range profile.Environment {
		if key == "PYTHON" {
			continue
		}
		if _, product := productenv.Suffix(key); !product {
			return fmt.Errorf("unsupported profile environment name %q", key)
		}
		if err := os.Setenv(key, value); err != nil {
			return err
		}
	}
	if err := validateNativeBackend(nil); err != nil {
		return err
	}
	if err := validateNativeRoots(checkout); err != nil {
		return err
	}
	if err := verifyNativeVersion(checkout); err != nil {
		return err
	}
	command := []string{filepath.Join(checkout, "boot.sh"), "--force", "--no-dev", "--port", strconv.Itoa(port), "--cert", profile.Certificate, "--key", profile.Key}
	if printPlan {
		plan, err := nativeLaunchPlan(checkout, port, false)
		if err != nil {
			return err
		}
		return json.NewEncoder(os.Stdout).Encode(map[string]any{"checkout": checkout, "port": port, "profile": path, "row_plan": plan, "command": command})
	}
	if preflight {
		probe := exec.Command(command[0], append(command[1:], "--print-command")...)
		probe.Env = os.Environ()
		probe.Stderr = os.Stderr
		return probe.Run()
	}
	if printCommand {
		command = append(command, "--print-command")
	}
	if checkAssets {
		command = append(command, "--check-assets")
	}
	if !printCommand && !checkAssets {
		exists := exec.Command("tmux", "has-session", "-t", "="+session)
		if err := exists.Run(); err != nil {
			var exit *exec.ExitError
			if !errors.As(err, &exit) || exit.ExitCode() != 1 {
				return fmt.Errorf("inspect profile session: %w", err)
			}
			create := exec.Command(launcher, "--session", session)
			create.Stdout = os.Stdout
			create.Stderr = os.Stderr
			if err := create.Run(); err != nil {
				return err
			}
		}
	}
	return syscall.Exec(command[0], command, os.Environ())
}

// Managed commands share boot's environment defaults; direct server invocations
// retain their existing flags and conservative network and agent defaults.
func nativeManagedDefaults(explicit map[string]bool, port *int, host *string, dangerouslyYolo *bool) error {
	if !explicit["port"] {
		value, present := productenv.Lookup("PORT")
		if !present {
			value = productenv.Value("DEFAULT_PORT")
		}
		selected, err := strconv.Atoi(value)
		if err != nil || selected < 1 || selected > 65535 {
			return fmt.Errorf("no valid port selected; set YOMUX_DEFAULT_PORT or pass --port")
		}
		*port = selected
	}
	if !explicit["host"] {
		value, present := productenv.Lookup("HOST")
		if !present {
			value = "0.0.0.0"
		}
		*host = value
	}
	if !explicit["dang"] && !explicit["dangerously-yolo"] {
		*dangerouslyYolo = true
	}
	return nil
}

func validateNativeTLS(httpMode bool, certificate, key string) error {
	if (certificate == "") != (key == "") || (httpMode && certificate != "") {
		return fmt.Errorf("invalid TLS certificate arguments")
	}
	if certificate != "" {
		if _, err := tls.LoadX509KeyPair(certificate, key); err != nil {
			return fmt.Errorf("invalid TLS certificate: %w", err)
		}
	}
	return nil
}

// A release version alone cannot distinguish a rebuilt executable. Linux lets
// us hash the running image; Darwin also needs proof that its on-disk image has
// not been replaced since this process started.
func verifyNativeExecutable(pid int) error {
	if runtime.GOOS == "linux" {
		return verifyNativeExecutableFiles("/proc/self/exe", filepath.Join("/proc", strconv.Itoa(pid), "exe"), time.Time{})
	}
	if runtime.GOOS != "darwin" {
		return fmt.Errorf("native executable verification is unsupported on %s", runtime.GOOS)
	}
	selected, err := os.Executable()
	if err != nil {
		return err
	}
	before, err := stats.DarwinProcessForPID(pid)
	if err != nil {
		return err
	}
	if before.StartedAt <= 0 || before.Identity == "" {
		return fmt.Errorf("listener start identity is unavailable")
	}
	started := time.Unix(0, int64(before.StartedAt*1e9))
	if err := verifyNativeExecutableFiles(selected, before.Executable, started); err != nil {
		return err
	}
	after, err := stats.DarwinProcessForPID(pid)
	if err != nil || before.Identity != after.Identity || before.Executable != after.Executable || before.StartedAt != after.StartedAt {
		return fmt.Errorf("listener generation changed during executable verification")
	}
	return nil
}

func verifyNativeExecutableFiles(selected, running string, started time.Time) error {
	var digests [2][32]byte
	for index, path := range []string{selected, running} {
		file, err := os.Open(path)
		if err != nil {
			return err
		}
		before, statErr := file.Stat()
		if statErr != nil || !before.Mode().IsRegular() {
			return errors.Join(statErr, file.Close(), fmt.Errorf("executable path is not a regular file"))
		}
		if index == 1 && !started.IsZero() && before.ModTime().After(started) {
			return errors.Join(file.Close(), fmt.Errorf("listener executable was replaced or touched after process startup"))
		}
		hash := sha256.New()
		_, copyErr := io.Copy(hash, file)
		if err := errors.Join(copyErr, file.Close()); err != nil {
			return err
		}
		after, err := os.Stat(path)
		if err != nil {
			return err
		}
		if !os.SameFile(before, after) || before.Size() != after.Size() || !before.ModTime().Equal(after.ModTime()) {
			return fmt.Errorf("executable changed while its digest was read")
		}
		copy(digests[index][:], hash.Sum(nil))
	}
	if digests[0] != digests[1] {
		return fmt.Errorf("listener executable does not match the selected Go binary; restart verification refused")
	}
	return nil
}

func nativePrepareLaunch(checkout string, port int, planJSON string) error {
	if err := verifyNativeVersion(checkout); err != nil {
		return err
	}
	plan, err := nativeLaunchPlan(checkout, port, false)
	if err != nil {
		return err
	}
	if value := productenv.Value("ROW_PLAN_FILE"); value != "" {
		data, err := os.ReadFile(value)
		if err != nil {
			return err
		}
		plan, err = decodeNativePlan(data)
		if err != nil {
			return err
		}
	}
	if planJSON != "" {
		plan, err = decodeNativePlan([]byte(planJSON))
		if err != nil {
			return err
		}
	}
	if err := applyNativePlan(plan); err != nil {
		return err
	}
	if err := validateNativeRoots(checkout); err != nil {
		return err
	}
	return nil
}

func reexecNativeManagedLaunch(listenerFD int) error {
	// Kernel process-environment readers observe exec-time values. Publish the
	// accepted native plan before opening mutable roots or constructing workers.
	plan := nativeRowPlan{Unset: launchInstanceKeys(), Assign: map[string]string{}}
	for _, key := range plan.Unset {
		if value, present := os.LookupEnv(key); present {
			plan.Assign[key] = value
		}
	}
	data, err := json.Marshal(plan)
	if err != nil {
		return err
	}
	args := []string{os.Args[0], "--launch-plan-json", string(data)}
	for index := 1; index < len(os.Args); index++ {
		if os.Args[index] == "--launch-plan-json" {
			index++
			continue
		}
		if strings.HasPrefix(os.Args[index], "--launch-plan-json=") {
			continue
		}
		args = append(args, os.Args[index])
	}
	executable, err := os.Executable()
	if err != nil {
		return err
	}
	return withInheritedLaunchDescriptors(listenerFD, func() error {
		return syscall.Exec(executable, args, os.Environ())
	})
}

func nativeInheritedLaunchPreflight(checkout, host string, port, fd int, planJSON string) error {
	// Duplicate before constructing an os.File: this probe never owns the original.
	duplicate, err := syscall.Dup(fd)
	if err != nil {
		return err
	}
	file := os.NewFile(uintptr(duplicate), "inherited-listener-probe")
	defer file.Close()
	listener, err := net.FileListener(file)
	if err != nil {
		return err
	}
	defer listener.Close()
	address, ok := listener.Addr().(*net.TCPAddr)
	if !ok || address.Port != port {
		return fmt.Errorf("inherited listener does not match the selected port")
	}
	selectedIP := net.ParseIP(host)
	if host == "localhost" {
		if !address.IP.IsLoopback() {
			return fmt.Errorf("inherited listener is not loopback")
		}
	} else if host != "" && (selectedIP == nil || !selectedIP.Equal(address.IP)) {
		return fmt.Errorf("inherited listener does not match the selected bind address")
	}
	if err := nativePrepareLaunch(checkout, port, planJSON); err != nil {
		return err
	}
	return nativeRootsAvailable(checkout, os.Getpid())
}

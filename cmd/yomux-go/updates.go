// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/keivenchang/yomux/internal/productversion"
	"github.com/keivenchang/yomux/internal/staticassets"
	"gopkg.in/yaml.v3"
)

type updateSnapshot struct {
	BuildState string `json:"build_state"`
	BuildError string `json:"build_error,omitempty"`
	prepared   *preparedSelfUpdate

	Available bool      `json:"available"`
	Current   string    `json:"current_version"`
	Target    string    `json:"target_version,omitempty"`
	TargetSHA string    `json:"target_sha,omitempty"`
	Branch    string    `json:"branch"`
	Level     string    `json:"version_change_level"`
	CheckedAt time.Time `json:"last_checked_at,omitempty"`
	Error     string    `json:"error,omitempty"`
}

type updateChecker struct {
	mu           sync.Mutex
	root         string
	current      string
	branch       string
	readPref     func() string
	prepare      func(context.Context, selfUpdateJob) (*preparedSelfUpdate, error)
	publish      func(updateSnapshot)
	snapshot     updateSnapshot
	running      bool
	manualQueued bool
	lastManual   time.Time
	wait         chan struct{}
}

func newUpdateChecker(root, current string, readPref func() string) *updateChecker {
	return &updateChecker{root: root, current: current, branch: "main", readPref: readPref, snapshot: updateSnapshot{Current: current, Branch: "main", Level: "none"}}
}

func (c *updateChecker) Snapshot() updateSnapshot {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.snapshot
}

func (c *updateChecker) Check(ctx context.Context) updateSnapshot {
	c.mu.Lock()
	if c.running {
		wait := c.wait
		c.mu.Unlock()
		select {
		case <-wait:
			return c.Snapshot()
		case <-ctx.Done():
			return c.Snapshot()
		}
	}
	previous := c.snapshot
	c.running = true
	c.wait = make(chan struct{})
	wait := c.wait
	c.mu.Unlock()
	fetchCtx, fetchCancel := context.WithTimeout(ctx, time.Minute)
	result := c.check(fetchCtx)
	fetchCancel()
	if result.Available && c.prepare != nil {
		result.BuildState = "checking"
		c.mu.Lock()
		c.snapshot = result
		c.mu.Unlock()
		if c.publish != nil {
			c.publish(result)
		}
		buildCtx, cancel := context.WithTimeout(ctx, 15*time.Minute)
		job := selfUpdateJob{ID: fmt.Sprintf("preflight-%d-%d", os.Getpid(), time.Now().UnixNano()), Target: result.Target, TargetSHA: result.TargetSHA}
		prepared := previous.prepared
		var err error
		if prepared == nil || prepared.target != result.TargetSHA || validatePreparedUpdate(buildCtx, prepared) != nil {
			prepared, err = c.prepare(buildCtx, job)
		}
		cancel()
		result.BuildState = "ready"
		if err != nil {
			result.BuildState, result.BuildError = "failed", err.Error()
		} else {
			result.prepared = prepared
		}
	}
	c.mu.Lock()
	c.snapshot = result
	c.mu.Unlock()
	if c.publish != nil {
		c.publish(result)
	}
	c.mu.Lock()
	c.running = false
	close(wait)
	c.mu.Unlock()
	return result
}

// QueueManual reserves admission before starting a goroutine, so bursts coalesce.
func (c *updateChecker) QueueManual(ctx context.Context, start func(func()) bool, now time.Time) (bool, bool) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.running || c.manualQueued {
		return false, false
	}
	if !c.lastManual.IsZero() && now.Sub(c.lastManual) < 30*time.Second {
		return false, true
	}
	c.manualQueued = true
	previous := c.snapshot
	if c.snapshot.Available {
		c.snapshot.BuildState = "checking"
		c.snapshot.BuildError = ""
		c.snapshot.prepared = nil
	}
	if !start(func() {
		defer func() { c.mu.Lock(); c.manualQueued = false; c.mu.Unlock() }()
		c.Check(ctx)
	}) {
		c.manualQueued = false
		c.snapshot = previous
		return false, false
	}
	c.lastManual = now
	return true, false
}

func (c *updateChecker) check(ctx context.Context) updateSnapshot {
	result := updateSnapshot{Current: c.current, Branch: c.branch, Level: "none", CheckedAt: time.Now().UTC()}
	preference := "patch"
	if c.readPref != nil {
		preference = c.readPref()
	}
	if preference == "none" {
		return result
	}
	sourceRoot, _, sourceErr := nativeUpdateSource(c.root)
	if sourceErr != nil {
		result.Error = sourceErr.Error()
		return result
	}
	command := func(args ...string) ([]byte, error) {
		cmd := exec.CommandContext(ctx, "git", append([]string{"-C", sourceRoot}, args...)...)
		output, err := cmd.CombinedOutput()
		if err != nil {
			return nil, fmt.Errorf("git %s: %w: %s", args[0], err, strings.TrimSpace(string(output)))
		}
		return output, nil
	}
	if _, err := command("fetch", "--quiet", "origin", c.branch); err != nil {
		result.Error = err.Error()
		return result
	}
	shaBytes, err := command("rev-parse", "origin/"+c.branch)
	if err != nil {
		result.Error = err.Error()
		return result
	}
	result.TargetSHA = strings.TrimSpace(string(shaBytes))
	manifest, err := command("show", result.TargetSHA+":version.yaml")
	if err != nil {
		result.Error = err.Error()
		return result
	}
	target, err := productversion.Parse(strings.NewReader(string(manifest)))
	if err != nil {
		result.Error = "upstream version manifest: " + err.Error()
		return result
	}
	result.Target = target
	result.Level = versionChangeLevel(c.current, target)
	result.Available = result.Level != "none" && updateNotifyLevelAllows(result.Level, preference)
	return result
}

func (s *server) updateNotifyPreference() string {
	data, err := os.ReadFile(filepath.Join(s.config, "settings.yaml"))
	if err != nil {
		return "patch"
	}
	var settings map[string]any
	if yaml.Unmarshal(data, &settings) != nil {
		return "patch"
	}
	updates, ok := settings["updates"].(map[string]any)
	if !ok {
		return "patch"
	}
	level, ok := updates["notify_level"].(string)
	if !ok {
		return "patch"
	}
	switch level {
	case "none", "patch", "minor", "major":
		return level
	}
	return "patch"
}

func (s *server) runUpdateChecker(ctx context.Context) {
	check := func() { s.updates.Check(ctx) }
	check()
	ticker := time.NewTicker(time.Hour)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			check()
		}
	}
}

func updateStatusPayload(result updateSnapshot) map[string]any {
	return map[string]any{
		"available": result.Available, "notify": result.Available, "current": result.Current, "current_version": result.Current,
		"target": result.Target, "target_version": result.Target, "target_sha": result.TargetSHA, "branch": result.Branch,
		"version_change_level": result.Level, "last_checked_at": result.CheckedAt, "error": result.Error,
		"build_state": result.BuildState, "build_error": result.BuildError,
	}
}

func (s *server) publishUpdateStatus(result updateSnapshot) {
	payload := updateStatusPayload(result)
	payload["instance_id"], payload["bundle_revision"] = s.instanceID, "go:"+version
	if _, err := s.events.publish("update_available", payload); err != nil {
		fmt.Fprintf(os.Stderr, "publish update readiness: %v\n", err)
	}
}

func (s *server) updateStatus(w http.ResponseWriter, r *http.Request) {
	if r.Method == http.MethodPost {
		if s.producerContext == nil {
			jsonResponse(w, http.StatusServiceUnavailable, map[string]any{"error": "Update checker is unavailable"})
			return
		}
		job := s.loadUpdateJob()
		if job.State == "preparing" || job.State == "activating" || job.State == "restarting" {
			jsonResponse(w, http.StatusConflict, map[string]any{"error": "An update is being installed"})
			return
		}
		accepted, limited := s.updates.QueueManual(s.producerContext, s.startBackgroundWork, time.Now())
		if !accepted && !limited && s.backgroundWorkAdmissionClosed() {
			jsonResponse(w, http.StatusServiceUnavailable, map[string]any{"error": "Update checker is shutting down"})
			return
		}
		if limited {
			w.Header().Set("Retry-After", "30")
			jsonResponse(w, http.StatusTooManyRequests, map[string]any{"error": "Wait 30 seconds before retrying preparation"})
			return
		}
		jsonResponse(w, http.StatusAccepted, map[string]any{"accepted": accepted, "status": updateStatusPayload(s.updates.Snapshot())})
		return
	}
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	result := s.updates.Snapshot()
	if s.producerContext != nil && (result.CheckedAt.IsZero() || time.Since(result.CheckedAt) > time.Hour) {
		s.startBackgroundWork(func() { s.updates.Check(s.producerContext) })
	}
	jsonResponse(w, http.StatusOK, updateStatusPayload(result))
}

type selfUpdateJob struct {
	prepared *preparedSelfUpdate

	ID          string    `json:"id"`
	State       string    `json:"state"`
	Progress    string    `json:"progress"`
	Target      string    `json:"target_version"`
	TargetSHA   string    `json:"target_sha"`
	StartedAt   time.Time `json:"started_at"`
	UpdatedAt   time.Time `json:"updated_at"`
	LauncherLog string    `json:"launcher_log,omitempty"`
	Error       string    `json:"error,omitempty"`
}

type selfUpdateSupervisorSpec struct {
	JobPath        string   `json:"job_path"`
	JobID          string   `json:"job_id"`
	Root           string   `json:"root"`
	PreparedBinary string   `json:"prepared_binary"`
	BootArgs       []string `json:"boot_args"`
	LogPath        string   `json:"log_path"`
}

func (s *server) loadUpdateJob() selfUpdateJob {
	return loadSelfUpdateJob(s.updateJobPath)
}

func loadSelfUpdateJob(path string) selfUpdateJob {
	data, err := os.ReadFile(path)
	if err != nil {
		return selfUpdateJob{State: "idle"}
	}
	var job selfUpdateJob
	if json.Unmarshal(data, &job) != nil {
		return selfUpdateJob{State: "failed", Error: "update job status is unreadable"}
	}
	return job
}

func (s *server) saveUpdateJob(job selfUpdateJob) error {
	return saveSelfUpdateJob(s.updateJobPath, job)
}

func saveSelfUpdateJob(path string, job selfUpdateJob) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return err
	}
	return withFileLock(path, func() error {
		return saveSelfUpdateJobUnlocked(path, job)
	})
}

func saveSelfUpdateJobUnlocked(path string, job selfUpdateJob) error {
	job.UpdatedAt = time.Now().UTC()
	data, err := json.Marshal(job)
	if err != nil {
		return err
	}
	return writeAtomicFile(path, data)
}

func mutateSelfUpdateJob(path, jobID string, allowedStates []string, update func(*selfUpdateJob)) (bool, error) {
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return false, err
	}
	updated := false
	err := withFileLock(path, func() error {
		job := loadSelfUpdateJob(path)
		if job.ID != jobID {
			return nil
		}
		allowed := false
		for _, state := range allowedStates {
			if job.State == state {
				allowed = true
				break
			}
		}
		if !allowed {
			return nil
		}
		update(&job)
		if err := saveSelfUpdateJobUnlocked(path, job); err != nil {
			return err
		}
		updated = true
		return nil
	})
	return updated, err
}

func (s *server) selfUpdateStatus(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	jsonResponse(w, http.StatusOK, map[string]any{"job": s.loadUpdateJob()})
}

func (s *server) recoverSelfUpdateJob() {
	job := s.loadUpdateJob()
	if job.State == "preparing" || job.State == "activating" {
		if _, err := mutateSelfUpdateJob(s.updateJobPath, job.ID, []string{"preparing", "activating"}, func(current *selfUpdateJob) {
			current.State, current.Progress = "failed", "Previous update was interrupted before restart"
			current.Error = "The previous update was interrupted. Check the new build result before retrying."
		}); err != nil {
			fmt.Fprintf(os.Stderr, "recover interrupted update: %v\n", err)
		}
		return
	}
	if job.State != "restarting" {
		return
	}
	sourceRoot, _, err := nativeUpdateSource(s.root)
	var output []byte
	if err == nil {
		output, err = exec.Command("git", "-C", sourceRoot, "rev-parse", "HEAD").Output()
	}
	if err != nil {
		_, _ = mutateSelfUpdateJob(s.updateJobPath, job.ID, []string{"restarting"}, func(current *selfUpdateJob) {
			current.State, current.Progress = "failed", "Managed restart did not verify the pinned target"
			current.Error = fmt.Sprintf("could not verify checkout after restart: %v", err)
		})
		return
	}
	actualSHA := strings.TrimSpace(string(output))
	if job.Target != version || actualSHA != job.TargetSHA {
		_, _ = mutateSelfUpdateJob(s.updateJobPath, job.ID, []string{"restarting"}, func(current *selfUpdateJob) {
			current.State, current.Progress = "failed", "Managed restart did not start the pinned target"
			current.Error = fmt.Sprintf("expected version %s at %s, running version is %s at %s", job.Target, job.TargetSHA, version, actualSHA)
		})
		return
	}
	_, _ = mutateSelfUpdateJob(s.updateJobPath, job.ID, []string{"restarting"}, func(current *selfUpdateJob) {
		current.Progress = "New instance started; awaiting managed readiness"
		current.Error = ""
	})
}

func (s *server) selfUpdate(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	if r.URL.Query().Get("dryrun") == "1" {
		// A dry run performs the same preparation as the automatic eligibility check.
		update := s.updates.Check(r.Context())
		status := http.StatusOK
		if update.BuildState != "ready" {
			status = http.StatusConflict
		}
		jsonResponse(w, status, map[string]any{"ok": status == http.StatusOK, "dry_run": true, "restarting": false, "target": update.Target, "build_state": update.BuildState, "error": update.BuildError, "user_message": map[string]any{"fallback": func() string {
			if status == http.StatusOK {
				return "Build verified. No update was installed."
			}
			if update.BuildError != "" {
				return update.BuildError
			}
			return "No verified update build is ready."
		}()}})
		return
	}

	if !s.updateJobMu.TryLock() {
		jsonResponse(w, http.StatusConflict, map[string]any{"ok": false, "error": "The update build is still being verified. Wait for the update notice."})
		return
	}
	job := s.loadUpdateJob()
	if job.State == "preparing" || job.State == "activating" || job.State == "restarting" {
		s.updateJobMu.Unlock()
		jsonResponse(w, http.StatusOK, map[string]any{"ok": true, "restarting": true, "version": job.Target, "target": job.Target, "instance_id": s.instanceID, "job": job})
		return
	}
	update := s.updates.Snapshot()
	if !update.Available || update.TargetSHA == "" {
		s.updateJobMu.Unlock()
		jsonResponse(w, http.StatusConflict, map[string]any{"ok": false, "error": "No eligible update is currently available."})
		return
	}
	if update.BuildState != "ready" || update.prepared == nil {
		s.updateJobMu.Unlock()
		jsonResponse(w, http.StatusConflict, map[string]any{"ok": false, "error": "The update build is not ready. " + update.BuildError, "build_state": update.BuildState})
		return
	}
	var request struct {
		TargetSHA string `json:"target_sha"`
	}
	if r.Body != nil && r.ContentLength != 0 {
		if err := json.NewDecoder(io.LimitReader(r.Body, 4096)).Decode(&request); err != nil {
			s.updateJobMu.Unlock()
			jsonResponse(w, http.StatusBadRequest, map[string]any{"ok": false, "error": "Invalid update request"})
			return
		}
	}
	if request.TargetSHA != "" && request.TargetSHA != update.TargetSHA {
		s.updateJobMu.Unlock()
		jsonResponse(w, http.StatusConflict, map[string]any{"ok": false, "error": "The available version changed. Review the new update notice before upgrading."})
		return
	}
	job = selfUpdateJob{ID: fmt.Sprintf("%d-%d", os.Getpid(), time.Now().UnixNano()), prepared: update.prepared, State: "preparing", Progress: "Activating the verified update build", Target: update.Target, TargetSHA: update.TargetSHA, StartedAt: time.Now().UTC()}
	if err := s.saveUpdateJob(job); err != nil {
		s.updateJobMu.Unlock()
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	s.updateJobMu.Unlock()
	go s.runSelfUpdate(job)
	jsonResponse(w, http.StatusOK, map[string]any{"ok": true, "restarting": true, "version": job.Target, "target": job.Target, "instance_id": s.instanceID, "job": job})
}

type preparedSelfUpdate struct {
	runtimeRoot, root, head, stage, binary, target string
	installation                                   *nativeInstallSource
	hashes                                         map[string][32]byte
}

func (s *server) prepareSelfUpdate(ctx context.Context, job selfUpdateJob) (result *preparedSelfUpdate, resultErr error) {
	s.updateJobMu.Lock()
	defer s.updateJobMu.Unlock()
	active := s.loadUpdateJob()
	if active.ID != job.ID && (active.State == "preparing" || active.State == "activating" || active.State == "restarting") {
		return nil, errors.New("an update is already being installed")
	}
	command := func(dir string, args ...string) ([]byte, error) {
		cmd := exec.CommandContext(ctx, "git", append([]string{"-C", dir}, args...)...)
		output, err := cmd.CombinedOutput()
		if err != nil {
			return nil, fmt.Errorf("git %s: %w: %s", args[0], err, strings.TrimSpace(string(output)))
		}
		return output, nil
	}
	runtimeRoot, err := filepath.EvalSymlinks(s.root)
	if err != nil {
		return nil, err
	}
	runtimeRoot, err = filepath.Abs(runtimeRoot)
	if err != nil {
		return nil, err
	}
	if runtimeRoot != s.root {
		return nil, errors.New("checkout identity changed")
	}
	root, installation, err := nativeUpdateSource(runtimeRoot)
	if err != nil {
		return nil, err
	}
	headBytes, err := command(root, "rev-parse", "HEAD")
	if err != nil {
		return nil, err
	}
	head := strings.TrimSpace(string(headBytes))
	status, err := command(root, "status", "--porcelain", "--untracked-files=all")
	if err != nil {
		return nil, err
	}
	resolved, err := command(root, "rev-parse", job.TargetSHA)
	if err != nil || strings.TrimSpace(string(resolved)) != job.TargetSHA {
		return nil, errors.New("pinned target commit changed or is unavailable")
	}
	ancestorErr := exec.CommandContext(ctx, "git", "-C", root, "merge-base", "--is-ancestor", head, job.TargetSHA).Run()
	notAncestor := false
	if ancestorErr != nil {
		var exitErr *exec.ExitError
		if !errors.As(ancestorErr, &exitErr) || exitErr.ExitCode() != 1 {
			return nil, fmt.Errorf("could not verify update ancestry: %w", ancestorErr)
		}
		notAncestor = true
	}
	hasLocalChanges := strings.TrimSpace(string(status)) != ""
	if notAncestor {
		if hasLocalChanges {
			return nil, fmt.Errorf("Auto-update can't use this checkout because it has local work. Save that work, then run this instance from a clean %s checkout.", job.Target)
		} else {
			return nil, fmt.Errorf("Auto-update can't use this branch because it has a local commit. Run this instance from a clean %s checkout to update.", job.Target)
		}
	}
	if hasLocalChanges {
		return nil, errors.New("Auto-update needs a clean checkout. Commit or stash the local edits, then retry.")
	}
	if err := os.MkdirAll(s.config, 0700); err != nil {
		return nil, err
	}
	configRoot, err := filepath.Abs(s.config)
	if err != nil {
		return nil, err
	}
	configRoot, err = filepath.EvalSymlinks(configRoot)
	if err != nil {
		return nil, err
	}
	stage := filepath.Join(configRoot, "self-update", job.TargetSHA, job.ID)
	reusable := strings.HasPrefix(job.ID, "preflight-")
	if reusable {
		stage = filepath.Join(configRoot, "self-update", "preflight")
	}
	if err := prepareUpdateStage(configRoot, stage); err != nil {
		return nil, err
	}
	// Retire only the candidate workspace's Bazel server; never another checkout's server.
	defer func() {
		if _, err := os.Stat(filepath.Join(stage, "MODULE.bazel")); err == nil {
			shutdownCtx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
			defer cancel()
			cmd := exec.CommandContext(shutdownCtx, "bazel", "--bazelrc=/dev/null", "shutdown")
			cmd.Dir, cmd.WaitDelay = stage, 5*time.Second
			output, err := cmd.CombinedOutput()
			if err != nil {
				result, resultErr = nil, errors.Join(resultErr, fmt.Errorf("retire update build worker: %w: %s", err, strings.TrimSpace(string(output))))
			}
		}
	}()
	archive := exec.CommandContext(ctx, "git", "-C", root, "archive", "--format=tar", job.TargetSHA)
	tar := exec.CommandContext(ctx, "tar", "-xf", "-", "-C", stage)
	pipe, err := archive.StdoutPipe()
	if err != nil {
		return nil, err
	}
	tar.Stdin = pipe
	var archiveErr strings.Builder
	archive.Stderr = &archiveErr
	if err := tar.Start(); err != nil {
		return nil, err
	}
	if err := archive.Start(); err != nil {
		_ = tar.Process.Kill()
		_ = tar.Wait()
		return nil, err
	}
	archiveWait := archive.Wait()
	tarWait := tar.Wait()
	if archiveWait != nil || tarWait != nil {
		return nil, fmt.Errorf("stage archive failed: %s %v %v", archiveErr.String(), archiveWait, tarWait)
	}
	for step, args := range [][]string{{"tools/build_go.sh"}, {"bazel-bin/tools/staticgen/staticgen_/staticgen", "--source-root", stage, "--output-directory", filepath.Join(stage, "static")}} {
		cmd := exec.CommandContext(ctx, args[0], args[1:]...)
		cmd.Dir = stage
		cmd.Env = os.Environ()
		cmd.WaitDelay = 5 * time.Second
		cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
		cmd.Cancel = func() error { return syscall.Kill(-cmd.Process.Pid, syscall.SIGTERM) }
		logPath := filepath.Join(stage, "build.log")
		logFile, err := os.OpenFile(logPath, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o600)
		if err != nil {
			return nil, err
		}
		cmd.Stdout, cmd.Stderr = logFile, logFile
		runErr := cmd.Run()
		if ctx.Err() != nil && cmd.Process != nil {
			// WaitDelay bounds TERM; retire build children that outlived their parent.
			if err := syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL); err != nil && !errors.Is(err, syscall.ESRCH) {
				runErr = errors.Join(runErr, err)
			}
		}
		closeErr := logFile.Close()
		if runErr != nil {
			phase := "compilation or browser asset setup"
			if step == 1 {
				phase = "static generation"
			}
			// build_go.sh writes the current phase for concise failures; the full output stays private.
			if data, err := os.ReadFile(filepath.Join(stage, ".update-build-phase")); err == nil && step == 0 {
				phase = strings.TrimSpace(string(data))
			}
			return nil, updatePreparationFailure(stage, phase, runErr)
		}
		if closeErr != nil {
			return nil, closeErr
		}

	}
	binary := filepath.Join(stage, "bazel-bin", "cmd", "yomux-go", "yomux-go_", "yomux-go")
	if info, err := os.Stat(binary); err != nil || !info.Mode().IsRegular() {
		return nil, errors.New("verification failed: prepared Go executable is missing")
	}
	// Verification shares the private output log while preserving stdout's protocol.
	verify := func(label string, args ...string) ([]byte, []byte, error) {
		logFile, err := os.OpenFile(filepath.Join(stage, "build.log"), os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0600)
		if err != nil {
			return nil, nil, err
		}
		if _, err := fmt.Fprintln(logFile, "Verification:", label); err != nil {
			return nil, nil, errors.Join(err, logFile.Close())
		}
		var stdout, stderr bytes.Buffer
		cmd := exec.CommandContext(ctx, args[0], args[1:]...)
		cmd.Stdout = io.MultiWriter(logFile, &stdout)
		cmd.Stderr = io.MultiWriter(logFile, &stderr)
		cmd.WaitDelay = 5 * time.Second
		runErr := cmd.Run()
		return stdout.Bytes(), stderr.Bytes(), errors.Join(runErr, logFile.Close())
	}
	versionOutput, _, versionErr := verify("executable version", binary, "--version")
	if versionErr != nil || strings.TrimSpace(string(versionOutput)) != "yomux-go "+job.Target {
		return nil, updatePreparationFailure(stage, "verification: prepared Go executable reported the wrong version", versionErr)
	}
	operatorOutput, operatorStderr, operatorErr := verify("native launch support", binary, "--operator", "version", "--root", stage)
	if operatorErr != nil || strings.TrimSpace(string(append(operatorOutput, operatorStderr...))) != job.Target {
		return nil, updatePreparationFailure(stage, "verification: prepared checkout lacks compatible native launch support", operatorErr)
	}
	if installation != nil {
		output, stderr, err := verify("installed-update provenance", "bash", filepath.Join(stage, "tools", "install.sh"), "--help")
		if err != nil || !strings.Contains(string(append(output, stderr...)), "--source-root") {
			return nil, updatePreparationFailure(stage, "verification: prepared installer lacks installed-update provenance support", err)
		}
	}
	preparedBinary := filepath.Join(stage, "yomux-go")
	if err := copyAtomic(binary, preparedBinary, 0o700); err != nil {
		return nil, fmt.Errorf("verification failed: %w", err)
	}
	for _, asset := range []string{"yomux.js", "yomux.css"} {
		if info, err := os.Stat(filepath.Join(stage, "static", asset)); err != nil || !info.Mode().IsRegular() || info.Size() == 0 {
			return nil, fmt.Errorf("verification failed: prepared browser asset %s is missing", asset)
		}
	}
	hashes, err := preparedUpdateHashes(stage, preparedBinary)
	if err != nil {
		return nil, fmt.Errorf("verification failed: %w", err)
	}
	prepared := &preparedSelfUpdate{runtimeRoot: runtimeRoot, root: root, head: head, installation: installation, stage: stage, binary: preparedBinary, target: job.TargetSHA, hashes: hashes}
	if err := validatePreparedUpdate(ctx, prepared); err != nil {
		return nil, fmt.Errorf("verification failed: %w", err)
	}
	return prepared, nil
}

// Keep one bounded user-facing reason; command output remains in the private log.
func updatePreparationFailure(stage, phase string, cause error) error {
	if cause == nil {
		cause = errors.New("unexpected verification response")
	}
	logPath := filepath.Join(stage, "build.log")
	failure := func(err error) error {
		return fmt.Errorf("%s failed: %w (full log: %s)", phase, errors.Join(cause, err), logPath)
	}
	reader, err := os.Open(logPath)
	if err != nil {
		return failure(err)
	}
	info, err := reader.Stat()
	if err != nil {
		return failure(errors.Join(err, reader.Close()))
	}
	if info.Size() > 512 {
		if _, err := reader.Seek(-512, io.SeekEnd); err != nil {
			return failure(errors.Join(err, reader.Close()))
		}
	}
	data, readErr := io.ReadAll(io.LimitReader(reader, 512))
	if err := errors.Join(readErr, reader.Close()); err != nil {
		return failure(err)
	}
	lines := strings.Split(strings.TrimSpace(string(data)), "\n")
	for index := len(lines) - 1; index >= 0; index-- {
		reason := strings.TrimSpace(lines[index])
		if reason != "" && !strings.HasPrefix(reason, "Verification:") {
			return fmt.Errorf("%s failed: %w: %s (full log: %s)", phase, cause, reason, logPath)
		}
	}
	return failure(nil)
}

// Browser dependencies and generated catalogs are part of the prepared runtime,
// just like the executable. Activation must neither acquire nor trust new bytes.
func preparedUpdateHashes(stage, binary string) (map[string][32]byte, error) {
	paths := []string{binary}
	if err := filepath.WalkDir(filepath.Join(stage, "static"), func(path string, entry fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if entry.IsDir() {
			return nil
		}
		if !entry.Type().IsRegular() {
			return fmt.Errorf("prepared runtime contains a non-regular asset: %s", path)
		}
		paths = append(paths, path)
		return nil
	}); err != nil {
		return nil, err
	}
	hashes := make(map[string][32]byte, len(paths))
	for _, path := range paths {
		input, err := os.Open(path)
		if err != nil {
			return nil, err
		}
		digest := sha256.New()
		_, readErr := io.Copy(digest, input)
		if err := errors.Join(readErr, input.Close()); err != nil {
			return nil, err
		}
		hashes[path] = [32]byte(digest.Sum(nil))
	}
	return hashes, nil
}

func validatePreparedUpdate(ctx context.Context, prepared *preparedSelfUpdate) error {
	currentHead, err := exec.CommandContext(ctx, "git", "-C", prepared.root, "rev-parse", "HEAD").CombinedOutput()
	if err != nil || strings.TrimSpace(string(currentHead)) != prepared.head {
		return errors.New("checkout changed during update preparation")
	}
	status, err := exec.CommandContext(ctx, "git", "-C", prepared.root, "status", "--porcelain", "--untracked-files=all").CombinedOutput()
	if err != nil || strings.TrimSpace(string(status)) != "" {
		return errors.New("checkout changed during update preparation")
	}
	currentSource, currentInstallation, err := nativeUpdateSource(prepared.runtimeRoot)
	if err != nil || currentSource != prepared.root || (prepared.installation == nil) != (currentInstallation == nil) ||
		(prepared.installation != nil && *prepared.installation != *currentInstallation) {
		return errors.New("installation provenance changed during update preparation")
	}
	hashes, err := preparedUpdateHashes(prepared.stage, prepared.binary)
	if err != nil || len(hashes) != len(prepared.hashes) {
		return errors.Join(errors.New("prepared update artifacts changed; check for updates again"), err)
	}
	for path, expected := range prepared.hashes {
		if actual, exists := hashes[path]; !exists || actual != expected {
			return errors.New("prepared update artifacts changed; check for updates again")
		}
	}
	return nil
}

func (s *server) runSelfUpdate(job selfUpdateJob) {
	set := func(state, progress string, err error) {
		job.State, job.Progress, job.Error = state, progress, ""
		if err != nil {
			job.Error = err.Error()
		}
		if err := s.saveUpdateJob(job); err != nil {
			fmt.Fprintf(os.Stderr, "self-update status publication: %v\n", err)
		}
	}
	moved := false
	prepared := job.prepared
	fail := func(err error) {
		progress := "Update stopped before checkout activation"
		if moved {
			progress = "Update stopped after checkout activation"
			err = fmt.Errorf("%w; recover with: %s", err, prepared.recoveryCommand(s.selfUpdateBootArgs()))
		}
		set("failed", progress, err)
	}
	if prepared == nil {
		ctx, cancel := context.WithTimeout(context.Background(), 15*time.Minute)
		defer cancel()
		var err error
		prepared, err = s.prepareSelfUpdate(ctx, job)
		if err != nil {
			fail(err)
			return
		}
	}
	if prepared.target != job.TargetSHA {
		fail(errors.New("prepared update target changed"))
		return
	}

	runtimeRoot, root, installation, stage, preparedBinary := prepared.runtimeRoot, prepared.root, prepared.installation, prepared.stage, prepared.binary
	command := func(dir string, args ...string) ([]byte, error) {
		cmd := exec.Command("git", append([]string{"-C", dir}, args...)...)
		output, err := cmd.CombinedOutput()
		if err != nil {
			return nil, fmt.Errorf("git %s: %w: %s", args[0], err, strings.TrimSpace(string(output)))
		}
		return output, nil
	}
	set("activating", "Validating checkout and activating the pinned fast-forward", nil)
	if err := validatePreparedUpdate(context.Background(), prepared); err != nil {
		fail(err)
		return
	}
	if _, err := command(root, "merge", "--ff-only", job.TargetSHA); err != nil {
		fail(err)
		return
	}
	moved = true
	if installation != nil {
		installer := exec.Command("bash", filepath.Join(stage, "tools", "install.sh"), "--prefix", installation.Prefix, "--source-root", root)
		installer.Dir = stage
		installer.Env = environmentWithValue(os.Environ(), "YOMUX_GO_BINARY", preparedBinary)
		installer.Env = environmentWithValue(installer.Env, "YOMUX_INSTALL_PREPARED", "1")
		if output, err := installer.CombinedOutput(); err != nil {
			fail(fmt.Errorf("activated commit but native installation failed: %w: %s", err, strings.TrimSpace(string(output))))
			return
		}
		preparedBinary = filepath.Join(runtimeRoot, "yomux-go")
	} else {
		outputs := make(map[string][]byte, len(prepared.hashes)-1)
		for path, expected := range prepared.hashes {
			if path == prepared.binary {
				continue // The executable is activated through the managed launcher below.
			}
			relative, err := filepath.Rel(filepath.Join(stage, "static"), path)
			if err != nil || !filepath.IsLocal(relative) {
				fail(errors.New("prepared browser asset escapes its runtime tree"))
				return
			}
			data, err := os.ReadFile(path)
			if err != nil || sha256.Sum256(data) != expected {
				fail(errors.Join(errors.New("prepared browser asset changed during activation"), err))
				return
			}
			outputs[relative] = data
		}
		if _, err := staticassets.Publish(filepath.Join(root, "static"), outputs, false); err != nil {
			fail(fmt.Errorf("activated commit but browser asset install failed: %w", err))
			return
		}
	}
	set("restarting", "Starting the selected instance through boot.sh", nil)
	args := s.selfUpdateBootArgs()
	jobPath, err := filepath.Abs(s.updateJobPath)
	if err != nil {
		fail(err)
		return
	}
	launcherLog := filepath.Join(stage, "managed-launcher.log")
	supervisorSpecPath := filepath.Join(stage, "self-update-supervisor.json")
	spec := selfUpdateSupervisorSpec{
		JobPath: jobPath, JobID: job.ID, Root: runtimeRoot, PreparedBinary: preparedBinary,
		BootArgs: args, LogPath: launcherLog,
	}
	if err := writeSelfUpdateSupervisorSpec(supervisorSpecPath, spec); err != nil {
		fail(fmt.Errorf("could not prepare managed-launch supervisor: %w", err))
		return
	}
	if err := launchManagedSelfUpdateSupervisor(s, job, preparedBinary, supervisorSpecPath, launcherLog); err != nil {
		fail(err)
		return
	}
}

func launchManagedSelfUpdateSupervisor(s *server, job selfUpdateJob, executable, specPath, logPath string) error {
	job.LauncherLog = logPath
	job.State, job.Progress, job.Error = "restarting", "Managed launcher started; waiting for the new instance", ""
	if err := s.saveUpdateJob(job); err != nil {
		return err
	}
	if err := startDetachedSelfUpdateSupervisor(executable, specPath, logPath); err != nil {
		failure := fmt.Errorf("could not start managed-launch supervisor: %w", err)
		_, recordErr := mutateSelfUpdateJob(s.updateJobPath, job.ID, []string{"restarting"}, func(current *selfUpdateJob) {
			current.State, current.Progress = "failed", "Managed restart could not start its supervisor"
			current.Error = failure.Error()
			current.LauncherLog = logPath
		})
		return errors.Join(failure, recordErr)
	}
	return nil
}

func writeSelfUpdateSupervisorSpec(path string, spec selfUpdateSupervisorSpec) error {
	if spec.JobPath == "" || spec.JobID == "" || spec.Root == "" || spec.PreparedBinary == "" || spec.LogPath == "" {
		return errors.New("managed-launch supervisor specification is incomplete")
	}
	data, err := json.Marshal(spec)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return err
	}
	return writeAtomicFile(path, data)
}

func startDetachedSelfUpdateSupervisor(executable, specPath, logPath string) error {
	return startDetachedCommand(executable, []string{"--internal-self-update-supervisor", specPath}, os.Environ(), logPath)
}

func startDetachedCommand(executable string, args, environment []string, logPath string) error {
	if err := os.MkdirAll(filepath.Dir(logPath), 0o700); err != nil {
		return err
	}
	logFile, err := os.OpenFile(logPath, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o600)
	if err != nil {
		return err
	}
	if err := logFile.Chmod(0o600); err != nil {
		logFile.Close()
		return err
	}
	cmd := exec.Command(executable, args...)
	cmd.Env = environment
	cmd.Stdin = nil
	cmd.Stdout, cmd.Stderr = logFile, logFile
	cmd.SysProcAttr = &syscall.SysProcAttr{Setsid: true}
	startErr := cmd.Start()
	_ = logFile.Close()
	if startErr != nil {
		return startErr
	}
	go func() { _ = cmd.Wait() }()
	return nil
}

func runSelfUpdateSupervisor(specPath string) error {
	data, err := os.ReadFile(specPath)
	if err != nil {
		return fmt.Errorf("read managed-launch supervisor specification: %w", err)
	}
	var spec selfUpdateSupervisorSpec
	if err := json.Unmarshal(data, &spec); err != nil {
		return fmt.Errorf("decode managed-launch supervisor specification: %w", err)
	}
	if spec.JobPath == "" || spec.JobID == "" || spec.Root == "" || spec.PreparedBinary == "" || spec.LogPath == "" {
		return errors.New("managed-launch supervisor specification is incomplete")
	}
	logFile, err := os.OpenFile(spec.LogPath, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o600)
	if err != nil {
		_ = recordSelfUpdateLauncherFailure(spec, fmt.Errorf("could not open launcher log: %w", err))
		return fmt.Errorf("open managed-launch log: %w", err)
	}
	if err := logFile.Chmod(0o600); err != nil {
		logFile.Close()
		_ = recordSelfUpdateLauncherFailure(spec, fmt.Errorf("could not secure launcher log: %w", err))
		return fmt.Errorf("secure managed-launch log: %w", err)
	}
	defer logFile.Close()
	commandArgs := append([]string{filepath.Join(spec.Root, "boot.sh")}, spec.BootArgs...)
	cmd := exec.Command("bash", commandArgs...)
	cmd.Dir = spec.Root
	cmd.Env = environmentWithValue(os.Environ(), "YOMUX_GO_BINARY", spec.PreparedBinary)
	cmd.Stdin = nil
	cmd.Stdout, cmd.Stderr = logFile, logFile
	if err := cmd.Start(); err != nil {
		_ = recordSelfUpdateLauncherFailure(spec, fmt.Errorf("could not start boot.sh: %w", err))
		return fmt.Errorf("start managed launcher: %w", err)
	}
	if err := cmd.Wait(); err != nil {
		failure := fmt.Errorf("managed launcher exited unsuccessfully: %w; see launcher log %s", err, spec.LogPath)
		_ = recordSelfUpdateLauncherFailure(spec, failure)
		return failure
	}
	if err := recordSelfUpdateLauncherSuccess(spec); err != nil {
		return fmt.Errorf("record managed launcher success: %w", err)
	}
	return nil
}

func environmentWithValue(environment []string, key, value string) []string {
	prefix := key + "="
	result := make([]string, 0, len(environment)+1)
	for _, entry := range environment {
		if !strings.HasPrefix(entry, prefix) {
			result = append(result, entry)
		}
	}
	return append(result, prefix+value)
}

func recordSelfUpdateLauncherFailure(spec selfUpdateSupervisorSpec, failure error) error {
	_, err := mutateSelfUpdateJob(spec.JobPath, spec.JobID, []string{"restarting"}, func(job *selfUpdateJob) {
		job.State = "failed"
		job.Progress = "Managed restart failed; the launcher did not verify the replacement"
		job.Error = failure.Error() + "; recover with: " + selfUpdateRecoveryCommand(spec.Root, spec.PreparedBinary, spec.BootArgs)
		job.LauncherLog = spec.LogPath
	})
	return err
}

func recordSelfUpdateLauncherSuccess(spec selfUpdateSupervisorSpec) error {
	_, err := mutateSelfUpdateJob(spec.JobPath, spec.JobID, []string{"restarting"}, func(job *selfUpdateJob) {
		job.State = "complete"
		job.Progress = "Update installed and the new Go instance passed managed readiness"
		job.Error = ""
	})
	return err
}

func copyAtomic(source, target string, mode os.FileMode) error {
	in, err := os.Open(source)
	if err != nil {
		return err
	}
	defer in.Close()
	if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
		return err
	}
	tmp, err := os.CreateTemp(filepath.Dir(target), ".update-*.tmp")
	if err != nil {
		return err
	}
	tmpName := tmp.Name()
	defer os.Remove(tmpName)
	if err := tmp.Chmod(mode); err != nil {
		tmp.Close()
		return err
	}
	if _, err := io.Copy(tmp, in); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Sync(); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	return os.Rename(tmpName, target)
}

var semverPattern = regexp.MustCompile(`^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$`)

func versionChangeLevel(current, target string) string {
	left, right := semverPattern.FindStringSubmatch(current), semverPattern.FindStringSubmatch(target)
	if left == nil || right == nil {
		return "none"
	}
	var old, next [3]uint64
	for index := 0; index < 3; index++ {
		var err error
		old[index], err = strconv.ParseUint(left[index+1], 10, 64)
		if err != nil {
			return "none"
		}
		next[index], err = strconv.ParseUint(right[index+1], 10, 64)
		if err != nil {
			return "none"
		}
	}
	if next == old || next[0] < old[0] || next[0] == old[0] && next[1] < old[1] || next[0] == old[0] && next[1] == old[1] && next[2] <= old[2] {
		return "none"
	}
	if next[0] != old[0] {
		return "major"
	}
	if next[1] != old[1] {
		return "minor"
	}
	return "patch"
}

func updateNotifyLevelAllows(level, threshold string) bool {
	ranks := map[string]int{"none": 0, "patch": 1, "minor": 2, "major": 3}
	if threshold == "none" {
		return false
	}
	if threshold == "" {
		threshold = "patch"
	}
	return ranks[level] > 0 && ranks[level] >= ranks[threshold]
}

// Restart from the effective startup state. Re-reading argv loses defaults and
// equals-form flags, and can widen a loopback listener during an update.
func (s *server) selfUpdateBootArgs() []string {
	args := []string{"--force", "--backend", "go", "--port", strconv.Itoa(s.port), "--host", s.host}
	if s.config != "" {
		args = append(args, "--config", s.config)
	}
	if s.statsPath != "" {
		args = append(args, "--stats", s.statsPath)
	}
	if s.httpMode {
		args = append(args, "--http")
	} else if s.tlsCertificate != "" {
		args = append(args, "--cert", s.tlsCertificate, "--key", s.tlsKey)
	}
	return args
}

func selfUpdateRecoveryCommand(root, binary string, args []string) string {
	parts := []string{"env", shellQuote("YOMUX_GO_BINARY=" + binary), "bash", shellQuote(filepath.Join(root, "boot.sh"))}
	for _, arg := range args {
		parts = append(parts, shellQuote(arg))
	}
	return strings.Join(parts, " ")
}

func (prepared *preparedSelfUpdate) recoveryCommand(args []string) string {
	if prepared.installation == nil {
		return selfUpdateRecoveryCommand(prepared.runtimeRoot, prepared.binary, args)
	}
	// Repair a prefix that may contain a new executable beside an old manifest or assets.
	installer := []string{"env", shellQuote("YOMUX_GO_BINARY=" + prepared.binary), "YOMUX_INSTALL_PREPARED=1", "bash", shellQuote(filepath.Join(prepared.stage, "tools", "install.sh")), "--prefix", shellQuote(prepared.installation.Prefix), "--source-root", shellQuote(prepared.root)}
	repair := "(cd " + shellQuote(prepared.stage) + " && " + strings.Join(installer, " ") + ")"
	return repair + " && " + selfUpdateRecoveryCommand(prepared.runtimeRoot, filepath.Join(prepared.runtimeRoot, "yomux-go"), args)
}

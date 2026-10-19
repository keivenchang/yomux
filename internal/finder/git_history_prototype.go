// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package finder

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"reflect"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/go-git/gcfg"
	git "github.com/go-git/go-git/v5"
	"github.com/go-git/go-git/v5/plumbing"
	"github.com/go-git/go-git/v5/plumbing/cache"
	"github.com/go-git/go-git/v5/storage/filesystem"
)

const HistoryPrototypeHelperFlag = "--internal-history-prototype"

type HistoryPrototypeRequest struct {
	Limit  int    `json:"limit"`
	Offset int    `json:"offset"`
	Head   string `json:"head,omitempty"`
}
type HistoryPrototypeCommit struct {
	SHA         string   `json:"sha"`
	Parents     []string `json:"parents"`
	Author      string   `json:"author"`
	AuthoredAt  int64    `json:"authored_at"`
	Subject     string   `json:"subject"`
	Decorations []string `json:"decorations"`
}
type HistoryPrototypePage struct {
	Head    string                   `json:"head"`
	Refs    map[string]string        `json:"refs"`
	Commits []HistoryPrototypeCommit `json:"commits"`
	HasMore bool                     `json:"has_more"`
}
type HistoryPrototypeError struct {
	Code    string `json:"code"`
	Message string `json:"message"`
}
type HistoryPrototypeResult struct {
	Page         *HistoryPrototypePage  `json:"page,omitempty"`
	Error        *HistoryPrototypeError `json:"error,omitempty"`
	Milliseconds float64                `json:"milliseconds"`
}
type HistoryPrototypeComparison struct {
	Iteration   int                    `json:"iteration"`
	Snapshot    HistoryPrototypeResult `json:"snapshot"`
	Descriptor  HistoryPrototypeResult `json:"descriptor"`
	Differences []string               `json:"differences"`
	Qualified   bool                   `json:"qualified"`
}

func validateHistoryPrototypeRequest(r HistoryPrototypeRequest) error {
	if r.Limit < 1 || r.Limit > gitHistoryMaxLimit || r.Offset < 0 || r.Offset > gitHistoryMaxOffset {
		return errors.New("history prototype pagination exceeds existing limits")
	}
	if r.Head != "" && (len(r.Head) != 40 || !gitObjectIDPattern.MatchString(r.Head)) {
		return errors.New("invalid prototype HEAD")
	}
	return nil
}

// Config is parsed as data only. Includes and executable settings never reach
// go-git; its storage sees an empty, in-memory allowlist for metadata-only reads.
func validateDescriptorGitConfig(fs *descriptorGitFS) error {
	for _, name := range []string{"config", "config.worktree"} {
		data, err := descriptorGitRead(fs, name, gitConfigMaxBytes)
		if errors.Is(err, os.ErrNotExist) {
			continue
		}
		if err != nil {
			return err
		}
		extensions := map[string]string{}
		err = gcfg.ReadWithCallback(bytes.NewReader(data), func(section, subsection, key, value string, boolean bool) error {
			if key == "" {
				return nil
			}
			section, key = strings.ToLower(section), strings.ToLower(key)
			if subsection != "" {
				if section == "extensions" {
					return errors.New("unsupported Git repository extension subsection")
				}
				return nil
			}
			if section == "extensions" {
				normalized, err := normalizeGitRepositoryExtension(key, value, !boolean)
				if err != nil {
					return err
				}
				if previous, exists := extensions[key]; exists && previous != normalized {
					return errors.New("ambiguous Git extension")
				}
				extensions[key] = normalized
				if key == "objectformat" && normalized != "sha1" || key == "refstorage" && normalized != "files" {
					return errors.New("unsupported prototype repository format")
				}
			}
			if section == "core" && key == "repositoryformatversion" && value != "0" && value != "1" {
				return errors.New("unsupported repository format version")
			}
			if section == "log" && key == "excludedecoration" {
				return errors.New("prototype does not support decoration exclusions")
			}
			return nil
		})
		if err != nil {
			return err
		}
	}
	fs.config = []byte{}
	return nil
}

func readDescriptorGitHistory(fs *descriptorGitFS, request HistoryPrototypeRequest) (*HistoryPrototypePage, error) {
	if err := validateHistoryPrototypeRequest(request); err != nil {
		return nil, err
	}
	if err := validateDescriptorGitConfig(fs); err != nil {
		return nil, err
	}
	for _, name := range []string{"objects/info/alternates", "objects/info/http-alternates", "shallow", "info/grafts"} {
		data, err := descriptorGitRead(fs, name, gitConfigMaxBytes)
		if errors.Is(err, os.ErrNotExist) {
			continue
		}
		if err != nil {
			return nil, err
		}
		if len(bytes.TrimSpace(data)) != 0 {
			// go-git's Log does not honor shallow boundaries or grafts. Report
			// this incompatibility rather than silently walking different history.
			return nil, fmt.Errorf("unsupported prototype metadata: %s", name)
		}
	}
	storage := filesystem.NewStorage(fs, cache.NewObjectLRUDefault())
	defer storage.Close()
	repo, err := git.Open(storage, nil)
	if err != nil {
		return nil, err
	}
	page := &HistoryPrototypePage{Refs: map[string]string{}, Commits: []HistoryPrototypeCommit{}}
	refs, err := repo.References()
	if err != nil {
		return nil, err
	}
	defer refs.Close()
	decorations := map[string][]string{}
	err = refs.ForEach(func(ref *plumbing.Reference) error {
		if !strings.HasPrefix(ref.Name().String(), "refs/") {
			return nil
		}
		if len(page.Refs) >= gitMetadataMaxEntries {
			return errGitOutputLimit
		}
		name := ref.Name().String()
		resolved, resolveErr := repo.Reference(ref.Name(), true)
		if resolveErr != nil {
			return resolveErr
		}
		hash := resolved.Hash()
		if strings.HasPrefix(name, "refs/replace/") {
			return errors.New("prototype does not support replacement refs")
		}
		page.Refs[name] = hash.String()
		if !ref.Name().IsBranch() && !ref.Name().IsRemote() && !ref.Name().IsTag() && name != "refs/stash" {
			return nil
		}
		label := strings.TrimPrefix(strings.TrimPrefix(strings.TrimPrefix(name, "refs/heads/"), "refs/remotes/"), "refs/tags/")
		if ref.Name().IsTag() {
			label = "tag: " + label
			// Peel annotated tags with a bounded chain; no signatures are checked.
			for depth := 0; ; depth++ {
				tag, tagErr := repo.TagObject(hash)
				if errors.Is(tagErr, plumbing.ErrObjectNotFound) {
					break
				}
				if tagErr != nil {
					return tagErr
				}
				if depth >= 32 {
					return errors.New("prototype tag nesting exceeds bound")
				}
				hash = tag.Target
				if tag.TargetType != plumbing.TagObject {
					break
				}
			}
		}
		decorations[hash.String()] = append(decorations[hash.String()], label)
		return nil
	})
	if err != nil {
		return nil, err
	}
	head, err := repo.Head()
	if errors.Is(err, plumbing.ErrReferenceNotFound) {
		raw, refErr := storage.Reference(plumbing.HEAD)
		if refErr == nil && raw.Type() == plumbing.SymbolicReference {
			if _, found := page.Refs[raw.Target().String()]; !found {
				if request.Head != "" {
					return nil, errors.New("prototype history HEAD changed")
				}
				return page, nil
			}
		}
	}
	if err != nil {
		return nil, err
	}
	page.Head = head.Hash().String()
	if request.Head != "" && request.Head != page.Head {
		return nil, errors.New("prototype history HEAD changed")
	}
	rawHead, err := storage.Reference(plumbing.HEAD)
	if err != nil {
		return nil, err
	}
	if rawHead.Type() == plumbing.SymbolicReference {
		branch := strings.TrimPrefix(rawHead.Target().String(), "refs/heads/")
		labels := decorations[page.Head]
		for i, label := range labels {
			if label == branch {
				labels[i] = "HEAD -> " + branch
			}
		}
		decorations[page.Head] = labels
	} else {
		decorations[page.Head] = append(decorations[page.Head], "HEAD")
	}
	iter, err := repo.Log(&git.LogOptions{From: head.Hash(), Order: git.LogOrderCommitterTime})
	if err != nil {
		return nil, err
	}
	defer iter.Close()
	for index := 0; index <= request.Offset+request.Limit; index++ {
		commit, err := iter.Next()
		if err == io.EOF {
			break
		}
		if err != nil {
			return nil, err
		}
		if index < request.Offset {
			continue
		}
		if len(page.Commits) == request.Limit {
			page.HasMore = true
			break
		}
		parents := make([]string, len(commit.ParentHashes))
		for i, hash := range commit.ParentHashes {
			parents[i] = hash.String()
		}
		author, _ := boundedGitHistoryText(commit.Author.Name)
		subject, _ := boundedGitHistoryText(strings.Join(strings.Split(strings.TrimSpace(strings.SplitN(strings.TrimSpace(commit.Message), "\n\n", 2)[0]), "\n"), " "))
		labels := decorations[commit.Hash.String()]
		if labels == nil {
			labels = []string{}
		}
		sort.Strings(labels)
		page.Commits = append(page.Commits, HistoryPrototypeCommit{commit.Hash.String(), parents, author, commit.Author.When.Unix(), subject, labels})
	}
	encoded, err := json.Marshal(page)
	if err != nil {
		return nil, err
	}
	if len(encoded) > gitHistoryMaxPayload {
		return nil, errGitOutputLimit
	}
	return page, nil
}

func prototypeResult(page *HistoryPrototypePage, err error, start time.Time) HistoryPrototypeResult {
	if err == nil && page != nil {
		encoded, encodeErr := json.Marshal(page)
		if encodeErr != nil {
			err = encodeErr
		} else if len(encoded) > gitHistoryMaxPayload {
			err = errGitOutputLimit
		}
	}
	result := HistoryPrototypeResult{Page: page, Milliseconds: float64(time.Since(start).Microseconds()) / 1000}
	if err != nil {
		code := "prototype_read_failed"
		if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
			code = "cancelled"
		}
		result.Error = &HistoryPrototypeError{code, err.Error()}
		result.Page = nil
	}
	return result
}

// RunHistoryPrototypeHelper is dispatched before server initialization. Only
// descriptors 3..5 and bounded JSON are consumed; no repository paths are accepted.
func RunHistoryPrototypeHelper(input io.Reader, output io.Writer) error {
	data, err := io.ReadAll(io.LimitReader(input, 4097))
	if err != nil {
		return err
	}
	var request HistoryPrototypeRequest
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	err = decoder.Decode(&request)
	if len(data) > 4096 {
		err = errGitOutputLimit
	}
	if err == nil && decoder.Decode(new(any)) != io.EOF {
		err = errors.New("trailing helper request data")
	}
	files := []*os.File{os.NewFile(3, "git"), os.NewFile(4, "common"), os.NewFile(5, "objects")}
	defer func() {
		for _, f := range files {
			if f != nil {
				_ = f.Close()
			}
		}
	}()
	for _, f := range files {
		if err != nil {
			break
		}
		if f == nil {
			err = errors.New("missing helper directory")
			break
		}
		info, statErr := f.Stat()
		if statErr != nil || !info.IsDir() {
			err = errors.New("invalid helper directory")
		}
	}
	var page *HistoryPrototypePage
	if err == nil {
		page, err = readDescriptorGitHistory(&descriptorGitFS{git: files[0], common: files[1], objects: files[2]}, request)
	}
	result := prototypeResult(page, err, time.Now())
	encoded, err := json.Marshal(result)
	if err != nil {
		return err
	}
	if len(encoded) > gitHistoryMaxOutput {
		return errGitOutputLimit
	}
	_, err = output.Write(append(encoded, '\n'))
	return err
}

func runHistoryPrototypeProcess(ctx context.Context, executable string, namespace *gitNamespace, request HistoryPrototypeRequest) (*HistoryPrototypePage, error) {
	ctx, cancel := gitViewContext(ctx)
	defer cancel()
	command, err := newHistoryPrototypeCommand(ctx, executable, namespace, request)
	if err != nil {
		return nil, err
	}
	stdout, stderr := &boundedBuffer{limit: gitHistoryMaxOutput}, &boundedBuffer{limit: 64 * 1024}
	command.Stdout, command.Stderr = stdout, stderr
	err = command.Run() // CommandContext kills, and Run always waits/reaps.
	if ctx.Err() != nil {
		return nil, ctx.Err()
	}
	if stdout.over || stderr.over {
		return nil, errGitOutputLimit
	}
	if err != nil {
		return nil, fmt.Errorf("history helper failed: %w", err)
	}
	var result HistoryPrototypeResult
	if err := json.Unmarshal(stdout.data, &result); err != nil {
		return nil, err
	}
	if result.Error != nil {
		return nil, fmt.Errorf("%s: %s", result.Error.Code, result.Error.Message)
	}
	if result.Page == nil {
		return nil, errors.New("helper omitted history page")
	}
	return result.Page, nil
}

// Command construction is shared with the live-process cancellation test so
// inherited-handle and process ownership are tested at the actual exec boundary.
func newHistoryPrototypeCommand(ctx context.Context, executable string, namespace *gitNamespace, request HistoryPrototypeRequest) (*exec.Cmd, error) {
	if err := validateHistoryPrototypeRequest(request); err != nil {
		return nil, err
	}
	data, err := json.Marshal(request)
	if err != nil {
		return nil, err
	}
	command := exec.CommandContext(ctx, executable, HistoryPrototypeHelperFlag)
	command.ExtraFiles = []*os.File{namespace.gitDir, namespace.commonDir, namespace.objects}
	command.Stdin = bytes.NewReader(data)
	command.Env = historyPrototypeEnvironment()
	return command, nil
}

// Reuse Git's safe settings without forwarding unrelated operator credentials
// to the reader process or its test fixtures.
func historyPrototypeEnvironment() []string {
	var environment []string
	for _, entry := range gitCommandEnvironment() {
		key, _, _ := strings.Cut(entry, "=")
		if key == "PATH" || key == "LANG" || key == "LC_ALL" || strings.HasPrefix(key, "GIT_") {
			environment = append(environment, entry)
		}
	}
	return environment
}

func readSnapshotPrototypeHistory(ctx context.Context, s *Service, scope *gitHistoryScope, request HistoryPrototypeRequest) (*HistoryPrototypePage, error) {
	if err := s.prepareGitHistoryNamespace(ctx, scope); err != nil {
		return nil, errors.New(err.Message)
	}
	head, finderErr := gitVerifiedHistoryHead(ctx, scope, "fs.error.gitHistoryFailed")
	if finderErr != nil {
		return nil, errors.New(finderErr.Message)
	}
	if request.Head != "" && head != request.Head {
		return nil, errors.New("snapshot history HEAD changed")
	}
	page := &HistoryPrototypePage{Head: head, Refs: map[string]string{}, Commits: []HistoryPrototypeCommit{}}
	output, _, err := runGitMetadataBounded(ctx, scope.directory, nil, []string{"for-each-ref", "--format=%(refname) %(objectname)"}, gitHistoryMaxOutput, scope.namespace)
	if err != nil {
		return nil, err
	}
	for _, line := range strings.Split(strings.TrimSpace(string(output)), "\n") {
		if line == "" {
			continue
		}
		name, hash, ok := strings.Cut(line, " ")
		if !ok {
			return nil, errors.New("malformed native ref output")
		}
		page.Refs[name] = hash
	}
	if head == "" {
		return page, nil
	}
	args := []string{"log", "--topo-order", "--no-patch", "--decorate=short", "-z", "--max-count=" + strconv.Itoa(request.Limit+1), "--skip=" + strconv.Itoa(request.Offset), "--format=" + strings.ReplaceAll(gitHistoryFormat, "%G?", ""), head, "--"}
	output, _, err = runGitMetadataBounded(ctx, scope.directory, nil, args, gitHistoryMaxOutput, scope.namespace)
	if err != nil {
		return nil, err
	}
	rows, _, err := parseGitHistory(output, false)
	if err != nil {
		return nil, err
	}
	page.HasMore = len(rows) > request.Limit
	if page.HasMore {
		rows = rows[:request.Limit]
	}
	for _, row := range rows {
		labels := []string{}
		if row["decorations"].(string) != "" {
			labels = strings.Split(row["decorations"].(string), ", ")
		}
		sort.Strings(labels)
		page.Commits = append(page.Commits, HistoryPrototypeCommit{row["sha"].(string), row["parents"].([]string), row["author"].(string), row["authored_at"].(int64), row["subject"].(string), labels})
	}
	return page, nil
}

// CompareHistoryPrototype is opt-in and never called by an HTTP history route.
// Each reader gets its own 30-second budget and fresh Finder authorization.
func (s *Service) CompareHistoryPrototype(ctx context.Context, path, executable string, request HistoryPrototypeRequest) (HistoryPrototypeComparison, error) {
	comparison := HistoryPrototypeComparison{Differences: []string{}}
	if err := validateHistoryPrototypeRequest(request); err != nil {
		return comparison, err
	}
	for _, descriptor := range []bool{false, true} {
		start := time.Now()
		readCtx, cancel := gitViewContext(ctx)
		scope, finderErr := s.openGitHistoryScopeWithOptions(readCtx, path, gitHistoryScopeOptions{})
		var page *HistoryPrototypePage
		var err error
		if finderErr != nil {
			err = errors.New(finderErr.Message)
		} else {
			if scope.relative != "" {
				err = errors.New("prototype requires the whole repository root")
			} else if descriptor {
				page, err = runHistoryPrototypeProcess(readCtx, executable, scope.namespace, request)
			} else {
				page, err = readSnapshotPrototypeHistory(readCtx, s, scope, request)
			}
			scope.close()
		}
		cancel()
		result := prototypeResult(page, err, start)
		if descriptor {
			comparison.Descriptor = result
		} else {
			comparison.Snapshot = result
		}
	}
	a, b := comparison.Snapshot.Page, comparison.Descriptor.Page
	if a == nil || b == nil {
		comparison.Differences = append(comparison.Differences, "reader error")
	} else {
		if a.Head != b.Head {
			comparison.Differences = append(comparison.Differences, "HEAD")
		}
		if !reflect.DeepEqual(a.Refs, b.Refs) {
			comparison.Differences = append(comparison.Differences, "refs")
		}
		if a.HasMore != b.HasMore {
			comparison.Differences = append(comparison.Differences, "pagination")
		}
		if !reflect.DeepEqual(a.Commits, b.Commits) {
			comparison.Differences = append(comparison.Differences, "commit metadata or ordering (native --topo-order versus go-git committer-time)")
		}
	}
	comparison.Qualified = len(comparison.Differences) == 0
	return comparison, nil
}

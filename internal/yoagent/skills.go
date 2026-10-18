// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package yoagent

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"sort"
	"strings"
	"unicode/utf8"

	"github.com/keivenchang/yomux/internal/atomicfile"
	"gopkg.in/yaml.v3"
)

const (
	SkillTextLimit      = 1200
	ContextTextLimit    = 2400
	UserSkillTextLimit  = 64 * 1024
	SkillTimeoutMaxMins = 1440
)

var (
	skillNamePattern  = regexp.MustCompile(`^[a-z][a-z0-9-]{1,63}$`)
	managedSkillTools = []string{
		"read_activity", "read_settings_catalog", "read_product_capabilities", "recommend_next_work",
		"watch_session", "watch_all_sessions", "notify_user", "read_skill_files", "write_skill_file",
		"delete_skill_file", "write_settings_patch", "preview_send_prompt", "execute_confirmed_send", "summarize_sessions",
	}
)

type SkillValidationError struct{ Reason string }

func (err SkillValidationError) Error() string { return err.Reason }

func invalidSkill(reason string) error { return SkillValidationError{Reason: reason} }

type Skill struct {
	Name                  string         `json:"name"`
	Kind                  string         `json:"kind"`
	Description           string         `json:"description"`
	Tools                 []string       `json:"tools"`
	Triggers              []string       `json:"triggers"`
	Enabled               bool           `json:"enabled"`
	Builtin               bool           `json:"builtin"`
	Source                string         `json:"source"`
	Confirmation          string         `json:"confirmation"`
	DefaultTimeoutMinutes *int           `json:"default_timeout_minutes,omitempty"`
	ModelIntent           map[string]any `json:"model_intent,omitempty"`
}

type SkillContext struct {
	Name    string `json:"name"`
	Source  string `json:"source"`
	Builtin bool   `json:"builtin"`
	Text    string `json:"text"`
}

type SkillSnapshot struct {
	OK           bool              `json:"ok"`
	BuiltinDirs  map[string]string `json:"builtin_dirs"`
	UserDirs     map[string]string `json:"user_dirs"`
	AllowedTools []string          `json:"allowed_tools"`
	Skills       []Skill           `json:"skills"`
	Contexts     []SkillContext    `json:"contexts"`
	ContextLines []string          `json:"context_lines"`
	Errors       []string          `json:"errors"`
}

func ManagedSkillTools() []string { return slices.Clone(managedSkillTools) }

func skillString(value any, limit int) string {
	text := strings.TrimSpace(fmt.Sprint(value))
	if value == nil {
		return ""
	}
	runes := []rune(text)
	if len(runes) > limit {
		text = string(runes[:limit])
	}
	return text
}

func skillStrings(value any, field string) ([]string, error) {
	if value == nil {
		return []string{}, nil
	}
	values, ok := value.([]any)
	if !ok {
		return nil, invalidSkill(fmt.Sprintf("%s must be a list of strings", field))
	}
	result := make([]string, 0, len(values))
	seen := map[string]bool{}
	for _, item := range values {
		text, ok := item.(string)
		if !ok {
			return nil, invalidSkill(fmt.Sprintf("%s must contain only strings", field))
		}
		text = skillString(text, 160)
		if text != "" && !seen[text] {
			seen[text] = true
			result = append(result, text)
		}
	}
	return result, nil
}

func parseSkill(data []byte, source, fallbackName string, builtin bool) (Skill, error) {
	if !utf8.Valid(data) {
		return Skill{}, errors.New("skill file is not UTF-8")
	}
	var raw map[string]any
	if err := yaml.Unmarshal(data, &raw); err != nil {
		return Skill{}, invalidSkill(fmt.Sprintf("invalid skill YAML: %v", err))
	}
	if raw == nil {
		return Skill{}, invalidSkill("skill file must contain a YAML mapping")
	}
	name := skillString(raw["name"], 80)
	if name == "" {
		name = fallbackName
	}
	if !skillNamePattern.MatchString(name) {
		return Skill{}, invalidSkill("skill name must match [a-z][a-z0-9-]{1,63]")
	}
	enabled := true
	if value, exists := raw["enabled"]; exists {
		var ok bool
		enabled, ok = value.(bool)
		if !ok {
			return Skill{}, invalidSkill("enabled must be true or false")
		}
	}
	kind := skillString(raw["kind"], 80)
	if kind == "" {
		kind = "workflow"
	}
	if !skillNamePattern.MatchString(kind) {
		return Skill{}, invalidSkill("skill kind must match [a-z][a-z0-9-]{1,63]")
	}
	description := skillString(raw["description"], SkillTextLimit)
	if enabled && description == "" {
		return Skill{}, invalidSkill("enabled skills require a description")
	}
	tools, err := skillStrings(raw["tools"], "tools")
	if err != nil {
		return Skill{}, err
	}
	for _, tool := range tools {
		if !slices.Contains(managedSkillTools, tool) {
			return Skill{}, invalidSkill(fmt.Sprintf("unknown YO!agent tool %q", tool))
		}
	}
	triggers, err := skillStrings(raw["triggers"], "triggers")
	if err != nil {
		return Skill{}, err
	}
	confirmation := skillString(raw["confirmation"], 80)
	if confirmation == "" {
		confirmation = "none"
	}
	if !slices.Contains([]string{"none", "required", "template"}, confirmation) {
		return Skill{}, invalidSkill("confirmation must be none, required, or template")
	}
	skill := Skill{Name: name, Kind: kind, Description: description, Tools: tools, Triggers: triggers, Enabled: enabled, Builtin: builtin, Source: source, Confirmation: confirmation}
	if value, exists := raw["default_timeout_minutes"]; exists && value != nil {
		minutes, ok := value.(int)
		if !ok {
			if number, isNumber := value.(uint64); isNumber && number <= SkillTimeoutMaxMins {
				minutes, ok = int(number), true
			}
		}
		if !ok || minutes < 1 || minutes > SkillTimeoutMaxMins {
			return Skill{}, invalidSkill(fmt.Sprintf("default_timeout_minutes must be an integer between 1 and %d", SkillTimeoutMaxMins))
		}
		skill.DefaultTimeoutMinutes = &minutes
	}
	if value, exists := raw["model_intent"]; exists && value != nil {
		intent, ok := value.(map[string]any)
		if !ok || !builtin || !enabled || !validModelIntent(name, intent) {
			return Skill{}, invalidSkill("model_intent must be a valid enabled built-in intent definition")
		}
		skill.ModelIntent = intent
	}
	return skill, nil
}

func validModelIntent(name string, intent map[string]any) bool {
	handler, _ := intent["handler"].(string)
	confirmation, _ := intent["confirmation"].(string)
	candidate := object(intent["candidate"])
	output := object(intent["output"])
	defaults := object(intent["defaults"])
	if candidate == nil || output == nil || defaults == nil || !slices.Contains([]string{"wait-roster-then-send", "wait-then-send", "loop-send"}, handler) || !skillNamePattern.MatchString(name) || !skillNamePattern.MatchString(handler) || confirmation != "required" && confirmation != "none" {
		return false
	}
	if !modelIntentStringList(candidate["any_of"], true) || !modelIntentStringList(candidateValue(candidate, "require_any", []any{}), false) || !modelIntentStringList(candidateValue(candidate, "collective_terms", []any{}), false) {
		return false
	}
	minimum, ok := modelIntentInteger(candidateValue(candidate, "min_known_sessions", 0))
	if !ok || minimum < 0 {
		return false
	}
	priority, ok := modelIntentInteger(candidateValue(candidate, "priority", 0))
	if !ok || priority < 0 || priority > 100 {
		return false
	}
	scope, _ := candidateValue(candidate, "session_scope", "condition").(string)
	if scope != "condition" && scope != "request" {
		return false
	}
	tag, _ := output["tag"].(string)
	if !skillNamePattern.MatchString(tag) {
		return false
	}
	schema := output["schema"]
	if schemaName, ok := schema.(string); ok {
		if schemaName != "delegated-prompt" && schemaName != "loop-send" {
			return false
		}
	} else if object(schema) == nil {
		return false
	}
	for _, value := range defaults {
		switch value.(type) {
		case nil, map[string]any, []any:
			return false
		}
	}
	return true
}

func candidateValue(candidate map[string]any, key string, fallback any) any {
	value, exists := candidate[key]
	if !exists {
		return fallback
	}
	return value
}

func modelIntentStringList(value any, required bool) bool {
	values, ok := value.([]any)
	if !ok || required && len(values) == 0 {
		return false
	}
	for _, value := range values {
		text, ok := value.(string)
		if !ok || strings.TrimSpace(text) == "" {
			return false
		}
	}
	return true
}

func modelIntentInteger(value any) (int, bool) {
	switch number := value.(type) {
	case int:
		return number, true
	case int64:
		return int(number), true
	case uint64:
		if number <= uint64(^uint(0)>>1) {
			return int(number), true
		}
	case float64:
		if number == float64(int(number)) {
			return int(number), true
		}
	}
	return 0, false
}

func regularFiles(directory string, suffixes ...string) ([]string, error) {
	entries, err := os.ReadDir(directory)
	if errors.Is(err, os.ErrNotExist) {
		return []string{}, nil
	}
	if err != nil {
		return nil, err
	}
	paths := make([]string, 0, len(entries))
	for _, entry := range entries {
		if entry.Type()&os.ModeSymlink != 0 || entry.IsDir() {
			continue
		}
		for _, suffix := range suffixes {
			if strings.EqualFold(filepath.Ext(entry.Name()), suffix) {
				paths = append(paths, filepath.Join(directory, entry.Name()))
				break
			}
		}
	}
	sort.Strings(paths)
	return paths, nil
}

func readSkillFile(path string, limit int64) ([]byte, error) {
	info, err := os.Lstat(path)
	if err != nil {
		return nil, err
	}
	if !info.Mode().IsRegular() || info.Size() > limit {
		return nil, errors.New("skill file is not a bounded regular file")
	}
	return os.ReadFile(path)
}

func skillContextLine(skill Skill) string {
	return fmt.Sprintf("YO!agent skill `%s` (%s): %s; tools: %s", skill.Name, skill.Kind, skill.Description, strings.Join(skill.Tools, ", "))
}

func LoadSkillSnapshot(root, configDir string) (SkillSnapshot, error) {
	if strings.TrimSpace(root) == "" || strings.TrimSpace(configDir) == "" {
		return SkillSnapshot{}, errors.New("YO!agent skill paths are required")
	}
	builtinSkillDir := filepath.Join(root, "yomux_lib", "yoagent", "builtin_skills")
	builtinContextDir := filepath.Join(root, "yomux_lib", "yoagent", "builtin_context")
	userSkillsDir := filepath.Join(configDir, "skills.d")
	userContextDir := filepath.Join(configDir, "context.d")
	snapshot := SkillSnapshot{
		OK:           true,
		BuiltinDirs:  map[string]string{"skills": "yomux_lib/yoagent/builtin_skills", "context": "yomux_lib/yoagent/builtin_context"},
		UserDirs:     map[string]string{"root": configDir, "skills": userSkillsDir, "context": userContextDir},
		AllowedTools: ManagedSkillTools(), Skills: []Skill{}, Contexts: []SkillContext{}, ContextLines: []string{}, Errors: []string{},
	}
	allSkills := []Skill{}
	for _, group := range []struct {
		directory string
		builtin   bool
	}{{builtinSkillDir, true}, {userSkillsDir, false}} {
		paths, err := regularFiles(group.directory, ".yaml", ".yml")
		if err != nil {
			snapshot.Errors = append(snapshot.Errors, fmt.Sprintf("read skill directory %s: %v", group.directory, err))
			continue
		}
		for _, path := range paths {
			data, err := readSkillFile(path, UserSkillTextLimit)
			if err != nil {
				snapshot.Errors = append(snapshot.Errors, fmt.Sprintf("read skill %s: %v", path, err))
				continue
			}
			skill, err := parseSkill(data, path, strings.TrimSuffix(filepath.Base(path), filepath.Ext(path)), group.builtin)
			if err != nil {
				snapshot.Errors = append(snapshot.Errors, fmt.Sprintf("invalid skill %s: %v", path, err))
				continue
			}
			allSkills = append(allSkills, skill)
		}
	}
	index := map[string]int{}
	for _, skill := range allSkills {
		if previous, exists := index[skill.Name]; exists {
			snapshot.Skills[previous] = skill
			continue
		}
		index[skill.Name] = len(snapshot.Skills)
		snapshot.Skills = append(snapshot.Skills, skill)
	}
	for _, skill := range snapshot.Skills {
		if skill.Enabled {
			snapshot.ContextLines = append(snapshot.ContextLines, skillContextLine(skill))
		}
	}
	for _, group := range []struct {
		directory string
		builtin   bool
	}{{builtinContextDir, true}, {userContextDir, false}} {
		paths, err := regularFiles(group.directory, ".md")
		if err != nil {
			snapshot.Errors = append(snapshot.Errors, fmt.Sprintf("read context directory %s: %v", group.directory, err))
			continue
		}
		for _, path := range paths {
			data, err := readSkillFile(path, UserSkillTextLimit)
			if err != nil {
				snapshot.Errors = append(snapshot.Errors, fmt.Sprintf("read context %s: %v", path, err))
				continue
			}
			text := strings.TrimSpace(string(data))
			if !utf8.Valid(data) {
				snapshot.Errors = append(snapshot.Errors, fmt.Sprintf("context %s is not UTF-8", path))
				continue
			}
			if len([]rune(text)) > ContextTextLimit {
				text = string([]rune(text)[:ContextTextLimit])
			}
			if text == "" {
				continue
			}
			item := SkillContext{Name: strings.TrimSuffix(filepath.Base(path), filepath.Ext(path)), Source: path, Builtin: group.builtin, Text: text}
			snapshot.Contexts = append(snapshot.Contexts, item)
			snapshot.ContextLines = append(snapshot.ContextLines, fmt.Sprintf("YO!agent context `%s`: %s", item.Name, item.Text))
		}
	}
	snapshot.OK = len(snapshot.Errors) == 0
	return snapshot, nil
}

func skillFilePath(configDir, kind, name string) (string, string, error) {
	kind = strings.ToLower(strings.TrimSpace(kind))
	if slices.Contains([]string{"skills", "yaml", "yml"}, kind) {
		kind = "skill"
	}
	if slices.Contains([]string{"contexts", "md", "markdown"}, kind) {
		kind = "context"
	}
	if kind != "skill" && kind != "context" {
		return "", "", invalidSkill("file type must be skill or context")
	}
	name = strings.TrimSpace(name)
	suffix, directory := ".yaml", "skills.d"
	if kind == "context" {
		suffix, directory = ".md", "context.d"
	}
	name = strings.TrimSuffix(name, suffix)
	if name == "" || filepath.Base(name) != name || strings.ContainsAny(name, `/\\`) || !skillNamePattern.MatchString(name) {
		return "", "", invalidSkill("file name must match [a-z][a-z0-9-]{1,63} and contain no path")
	}
	return kind, filepath.Join(configDir, directory, name+suffix), nil
}

func ReadUserSkillFile(configDir, kind, name string) (map[string]any, error) {
	kind, path, err := skillFilePath(configDir, kind, name)
	if err != nil {
		return nil, err
	}
	data, err := readSkillFile(path, UserSkillTextLimit)
	if err != nil {
		return nil, err
	}
	payload := map[string]any{"kind": kind, "name": strings.TrimSuffix(filepath.Base(path), filepath.Ext(path)), "path": path, "text": string(data), "exists": true}
	if kind == "skill" {
		skill, parseErr := parseSkill(data, path, payload["name"].(string), false)
		payload["valid"] = parseErr == nil
		if parseErr == nil {
			payload["skill"] = skill
		} else {
			payload["errors"] = []string{parseErr.Error()}
		}
	}
	return payload, nil
}

func ListUserSkillFiles(configDir string) map[string]any {
	files, issues := []map[string]any{}, []map[string]any{}
	for _, group := range []struct {
		kind      string
		directory string
		suffixes  []string
	}{{"skill", filepath.Join(configDir, "skills.d"), []string{".yaml", ".yml"}}, {"context", filepath.Join(configDir, "context.d"), []string{".md"}}} {
		paths, err := regularFiles(group.directory, group.suffixes...)
		if err != nil {
			issues = append(issues, map[string]any{"source": group.directory, "diagnostic": err.Error()})
			continue
		}
		for _, path := range paths {
			name := strings.TrimSuffix(filepath.Base(path), filepath.Ext(path))
			payload, err := ReadUserSkillFile(configDir, group.kind, name)
			if err != nil {
				issues = append(issues, map[string]any{"source": path, "diagnostic": err.Error()})
				continue
			}
			files = append(files, payload)
		}
	}
	return map[string]any{
		"ok":        len(issues) == 0,
		"user_dirs": map[string]string{"root": configDir, "skills": filepath.Join(configDir, "skills.d"), "context": filepath.Join(configDir, "context.d")},
		"files":     files,
		"errors":    issues,
	}
}

func WriteUserSkillFile(ctx context.Context, configDir, kind, name, text string) (result map[string]any, resultErr error) {
	kind, path, err := skillFilePath(configDir, kind, name)
	if err != nil {
		return nil, err
	}
	text = strings.TrimSpace(strings.ReplaceAll(strings.ReplaceAll(text, "\r\n", "\n"), "\r", "\n"))
	if len([]rune(text)) > UserSkillTextLimit {
		return nil, invalidSkill(fmt.Sprintf("file text exceeds %d characters", UserSkillTextLimit))
	}
	if kind == "skill" {
		name := strings.TrimSuffix(filepath.Base(path), filepath.Ext(path))
		skill, err := parseSkill([]byte(text), path, name, false)
		if err != nil {
			return nil, invalidSkill(err.Error())
		}
		if skill.Name != name {
			return nil, invalidSkill("skill name must match the file name")
		}
	} else if text == "" {
		return nil, invalidSkill("context text is required")
	}
	if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		return nil, err
	}
	if err := os.Chmod(filepath.Dir(path), 0700); err != nil {
		return nil, err
	}
	lock, err := atomicfile.AcquireLock(ctx, path+".lock")
	if err != nil {
		return nil, err
	}
	defer func() { resultErr = errors.Join(resultErr, lock.Close()) }()
	if info, statErr := os.Lstat(path); statErr == nil && !info.Mode().IsRegular() {
		return nil, errors.New("skill destination must be a regular file")
	} else if statErr != nil && !errors.Is(statErr, os.ErrNotExist) {
		return nil, statErr
	}
	if err := atomicfile.Write(path, []byte(strings.TrimRight(text, "\n")+"\n")); err != nil {
		return nil, err
	}
	if err := os.Chmod(path, 0600); err != nil {
		return nil, err
	}
	return ReadUserSkillFile(configDir, kind, name)
}

func DeleteUserSkillFile(ctx context.Context, configDir, kind, name string) (result map[string]any, resultErr error) {
	kind, path, err := skillFilePath(configDir, kind, name)
	if err != nil {
		return nil, err
	}
	lock, err := atomicfile.AcquireLock(ctx, path+".lock")
	if err != nil {
		return nil, err
	}
	defer func() { resultErr = errors.Join(resultErr, lock.Close()) }()
	info, err := os.Lstat(path)
	if err != nil {
		return nil, err
	}
	if !info.Mode().IsRegular() {
		return nil, errors.New("skill file must be a regular file")
	}
	if err := os.Remove(path); err != nil {
		return nil, err
	}
	return map[string]any{"ok": true, "kind": kind, "name": strings.TrimSuffix(filepath.Base(path), filepath.Ext(path)), "path": path, "deleted": true}, nil
}

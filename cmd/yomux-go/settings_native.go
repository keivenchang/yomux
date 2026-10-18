// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"sort"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/keivenchang/yomux/internal/atomicfile"
	"github.com/keivenchang/yomux/internal/decimal"
	"github.com/keivenchang/yomux/internal/productenv"
	"github.com/keivenchang/yomux/internal/work"
	"gopkg.in/yaml.v3"
)

// Settings metadata is shared with retained Python tooling; neither reader owns
// a second list of defaults, accepted values, or migration boundaries.
type nativeSettingsSchema map[string]any

func settingsObject(value any) map[string]any {
	object, _ := value.(map[string]any)
	return object
}

func settingsList(value any) []any {
	list, _ := value.([]any)
	return list
}

func (schema nativeSettingsSchema) entry(name, path string) any {
	return settingsObject(schema[name])[path]
}

func cloneSettingsValue(value any) any {
	switch value := value.(type) {
	case map[string]any:
		copy := make(map[string]any, len(value))
		for key, item := range value {
			copy[key] = cloneSettingsValue(item)
		}
		return copy
	case []any:
		copy := make([]any, len(value))
		for index, item := range value {
			copy[index] = cloneSettingsValue(item)
		}
		return copy
	default:
		return value
	}
}

func (s *server) nativeSettingsSchema() (nativeSettingsSchema, error) {
	data, err := os.ReadFile(filepath.Join(s.root, "assets", "runtime", "settings-schema.json"))
	if err != nil {
		return nil, fmt.Errorf("read settings schema: %w", err)
	}
	var schema nativeSettingsSchema
	if err := json.Unmarshal(data, &schema); err != nil {
		return nil, fmt.Errorf("decode settings schema: %w", err)
	}
	if err := s.applyLocaleSettingsSchema(schema); err != nil {
		return nil, err
	}
	defaults := settingsObject(schema["DEFAULT_SETTINGS"])
	if len(defaults) == 0 {
		return nil, errors.New("settings schema has no defaults")
	}
	for _, field := range []struct{ env, key string }{{"SUMMARY_MODEL", "codex_model"}, {"SUMMARY_EFFORT", "codex_effort"}, {"SUMMARY_SERVICE_TIER", "codex_service_tier"}} {
		value := strings.TrimSpace(productenv.Value(field.env))
		if slices.Contains(settingsList(schema.entry("SETTING_CHOICES", "summary."+field.key)), any(value)) {
			settingsObject(defaults["summary"])[field.key] = value
		}
	}
	for _, name := range []string{"FILE_TRANSFER_MAX_BYTES", "UPLOAD_MAX_BYTES"} {
		if value, present := productenv.Lookup(name); present {
			number, err := strconv.ParseInt(strings.TrimSpace(value), 10, 64)
			if err != nil || number <= 0 {
				return nil, fmt.Errorf("%s must be a positive integer", productenv.Name(name))
			}
			settingsObject(defaults["uploads"])["max_bytes"] = float64(number)
			break
		}
	}
	return schema, nil
}

func settingsNumber(value any) (float64, bool) {
	switch value := value.(type) {
	case float64:
		return value, true
	case int:
		return float64(value), true
	case int64:
		return float64(value), true
	case uint64:
		return float64(value), true
	case bool:
		if value {
			return 1, true
		}
		return 0, true
	case string:
		number, err := strconv.ParseFloat(strings.TrimSpace(value), 64)
		return number, err == nil || errors.Is(err, strconv.ErrRange)
	default:
		return 0, false
	}
}

func equivalentSettingsValue(left, right any) bool {
	if reflect.DeepEqual(left, right) {
		return true
	}
	// Python treats booleans as integers when reporting numeric coercions.
	switch left.(type) {
	case bool, float64, int:
	default:
		return false
	}
	switch right.(type) {
	case bool, float64, int:
	default:
		return false
	}
	a, validA := settingsNumber(left)
	b, validB := settingsNumber(right)
	return validA && validB && a == b
}

func normalizeImageAction(value string) string {
	return strings.Join(strings.Fields(strings.ToLower(strings.TrimRight(strings.TrimSpace(strings.TrimPrefix(strings.TrimSpace(value), ";")), ".:"))), " ")
}

func (schema nativeSettingsSchema) imageAction(value string) string {
	normalized := normalizeImageAction(value)
	if normalized == "" {
		return ""
	}
	if slices.Contains([]string{"insert-path", "insert path", "path", "server-ocr", "server ocr", "server ocr image", "ocr result", "shell-file", "show file type", "file", "file type"}, normalized) {
		return ""
	}
	for _, raw := range settingsList(schema["IMAGE_DROP_ACTION_ORDER_SPECS"]) {
		spec := settingsObject(raw)
		prompt, _ := spec["prompt"].(string)
		canonical := spec["canonical"].(string)
		aliases := []any{spec["id"], spec["label"], canonical, prompt, "; " + prompt}
		labels := append([]any{spec["label"]}, settingsList(spec["aliases"])...)
		aliases = append(aliases, labels...)
		for _, label := range labels {
			aliases = append(aliases, fmt.Sprint(label)+": "+prompt, fmt.Sprint(label)+": ; "+prompt)
		}
		for _, alias := range aliases {
			if text, ok := alias.(string); ok && normalizeImageAction(text) == normalized {
				return canonical
			}
		}
		if _, suffix, found := strings.Cut(value, ":"); found && prompt != "" && normalizeImageAction(suffix) == normalizeImageAction(prompt) {
			return canonical
		}
	}
	return strings.TrimSpace(value)
}

type settingsProfileEvent struct {
	at      float64
	profile string
}

func settingsProfileHistory(value any) []settingsProfileEvent {
	byTime := map[float64]string{0: "default"}
	for _, item := range settingsList(value) {
		text, ok := item.(string)
		if !ok {
			continue
		}
		at, profile, found := strings.Cut(text, "|")
		number, err := strconv.ParseFloat(at, 64)
		profile = strings.ToLower(strings.TrimSpace(profile))
		if found && err == nil && !math.IsNaN(number) && !math.IsInf(number, 0) && number >= 0 && (profile == "default" || profile == "subscription") {
			byTime[number] = profile
		}
	}
	ordered := make([]settingsProfileEvent, 0, len(byTime))
	for at, profile := range byTime {
		ordered = append(ordered, settingsProfileEvent{at, profile})
	}
	sort.Slice(ordered, func(i, j int) bool { return ordered[i].at < ordered[j].at })
	result := make([]settingsProfileEvent, 0, len(ordered))
	for _, event := range ordered {
		if len(result) == 0 || result[len(result)-1].profile != event.profile {
			result = append(result, event)
		}
	}
	return result
}

func settingsHistoryPayload(events []settingsProfileEvent) []any {
	result := make([]any, 0, len(events))
	for _, event := range events {
		result = append(result, strconv.FormatFloat(event.at, 'g', 17, 64)+"|"+event.profile)
	}
	return result
}

func settingsHistoryKey(key string) bool {
	return key == "_openai_pricing_profile_history" || key == "_anthropic_pricing_profile_history"
}

func recordSettingsProfiles(previous, current map[string]any, effectiveAt float64) {
	before, after := settingsObject(previous["cost"]), settingsObject(current["cost"])
	for _, provider := range []string{"openai", "anthropic"} {
		key := "_" + provider + "_pricing_profile_history"
		history := settingsProfileHistory(before[key])
		if before[provider+"_pricing_profile"] != after[provider+"_pricing_profile"] {
			history = append(history, settingsProfileEvent{effectiveAt, after[provider+"_pricing_profile"].(string)})
		}
		after[key] = settingsHistoryPayload(settingsProfileHistory(settingsHistoryPayload(history)))
	}
}

func (schema nativeSettingsSchema) sanitize(raw any, coerced *[]string) map[string]any {
	defaults := settingsObject(schema["DEFAULT_SETTINGS"])
	source := settingsObject(raw)
	result := settingsObject(cloneSettingsValue(defaults))
	for section, rawDefaults := range defaults {
		incoming := settingsObject(cloneSettingsValue(settingsObject(source[section])))
		if incoming == nil {
			incoming = map[string]any{}
		}
		if section == "general" {
			if _, exists := incoming["startup_tips"]; !exists {
				if value, exists := incoming["startup_helpers"]; exists {
					incoming["startup_tips"] = value
				}
			}
		}
		if section == "performance" {
			if _, exists := incoming["workflow_transition_glow_seconds"]; !exists {
				if value, exists := incoming["agent_window_cooldown_seconds"]; exists {
					if number, ok := settingsNumber(value); ok && number == 0 {
						value = settingsObject(rawDefaults)["workflow_transition_glow_seconds"]
					}
					incoming["workflow_transition_glow_seconds"] = value
				} else if value := settingsObject(source["appearance"])["metadata_badge_pulse_seconds"]; value != nil {
					if number, ok := settingsNumber(value); ok && number == 20 {
						value = settingsObject(rawDefaults)["workflow_transition_glow_seconds"]
					}
					incoming["workflow_transition_glow_seconds"] = value
				}
			}
		}
		for key, fallback := range settingsObject(rawDefaults) {
			path := section + "." + key
			value, present := incoming[key]
			if !present {
				value = cloneSettingsValue(fallback)
			}
			if stale := schema.entry("STALE_DEFAULT_MIGRATIONS", path); present && stale != nil {
				candidates := settingsList(stale)
				if candidates == nil {
					candidates = []any{stale}
				}
				if number, ok := settingsNumber(value); ok {
					for _, candidate := range candidates {
						if old, valid := settingsNumber(candidate); valid && old == number {
							value = fallback
							break
						}
					}
				}
			}
			if text, ok := value.(string); ok {
				if alias, found := settingsObject(schema.entry("SETTING_VALUE_ALIASES", path))[strings.ToLower(strings.TrimSpace(text))]; found {
					value = alias
				}
				if section == "yoagent" {
					if text == schema.entry("LEGACY_YOAGENT_DEFAULTS", key) {
						value = fallback
					}
					for _, marker := range settingsList(schema.entry("LEGACY_YOAGENT_PROMPT_MARKERS", key)) {
						if strings.Contains(text, marker.(string)) {
							value = fallback
						}
					}
				}
			}
			normalized := cloneSettingsValue(fallback)
			switch fallback := fallback.(type) {
			case bool:
				if boolean, ok := value.(bool); ok {
					normalized = boolean
				} else if text, ok := value.(string); ok {
					switch strings.ToLower(strings.TrimSpace(text)) {
					case "1", "true", "yes", "on":
						normalized = true
					case "0", "false", "no", "off":
						normalized = false
					}
				}
			case float64:
				if number, ok := settingsNumber(value); ok {
					lower, upper := -1e9, 1e9
					if limits := settingsList(schema.entry("SETTING_LIMITS", path)); len(limits) == 2 {
						lower, upper = limits[0].(float64), limits[1].(float64)
					}
					if math.IsNaN(number) {
						number = upper
					}
					number = math.Max(lower, math.Min(upper, number))
					if math.Trunc(fallback) == fallback {
						number = math.RoundToEven(number)
					} else {
						// Fixed-decimal formatting rounds the binary float itself.
						// Multiplying before rounding loses ties such as 0.1005.
						rounded, err := decimal.Round(number, 3)
						if err != nil {
							panic(fmt.Sprintf("finite settings number failed fixed-decimal round trip: %v", err))
						}
						number = rounded
					}
					normalized = number
				}
			case []any:
				items := settingsList(value)
				if items == nil {
					items = fallback
				}
				clean := []any{}
				seen := map[string]bool{}
				for _, item := range items {
					text, ok := item.(string)
					if !ok {
						continue
					}
					text = strings.TrimSpace(text)
					if path == "uploads.image_action_order" {
						text = schema.imageAction(text)
						identity := normalizeImageAction(text)
						if seen[identity] {
							continue
						}
						seen[identity] = true
					}
					if text == "" {
						continue
					}
					if path == "notifications.notify_transitions" && !slices.Contains(settingsList(schema["NOTIFY_TRANSITION_KEYS"]), any(text)) {
						continue
					}
					clean = append(clean, text)
				}
				if section == "cost" && settingsHistoryKey(key) {
					clean = settingsHistoryPayload(settingsProfileHistory(value))
				}
				if path == "uploads.image_action_order" && len(clean) == 0 {
					clean = settingsList(cloneSettingsValue(fallback))
				}
				if limit, ok := schema.entry("SETTING_LIST_LIMITS", path).(float64); ok && limit > 0 && len(clean) > int(limit) {
					clean = clean[:int(limit)]
				}
				normalized = clean
			case string:
				if choices := settingsList(schema.entry("SETTING_CHOICES", path)); choices != nil {
					if text, ok := value.(string); ok && slices.Contains(choices, any(text)) {
						normalized = text
					}
				} else if text, ok := value.(string); ok && (strings.TrimSpace(text) != "" || slices.Contains(settingsList(schema["STRING_ALLOW_EMPTY"]), any(path))) {
					normalized = text
				}
			}
			settingsObject(result[section])[key] = normalized
			if coerced != nil && present && !equivalentSettingsValue(normalized, value) {
				*coerced = append(*coerced, path)
			}
		}
	}
	for _, retired := range settingsList(schema["RETIRED_HOVER_TIMING_KEYS"]) {
		if _, exists := settingsObject(source["performance"])[retired.(string)]; exists {
			for _, key := range []string{"popover_show_delay_ms", "popover_hide_delay_ms"} {
				settingsObject(result["performance"])[key] = settingsObject(defaults["performance"])[key]
			}
			break
		}
	}
	return result
}

func (schema nativeSettingsSchema) merge(current map[string]any, patch any, coerced *[]string) map[string]any {
	merged := settingsObject(cloneSettingsValue(current))
	for section, raw := range settingsObject(patch) {
		values := settingsObject(merged[section])
		for key, value := range settingsObject(raw) {
			if section == "general" && key == "startup_helpers" {
				key = "startup_tips"
			}
			if section == "cost" && settingsHistoryKey(key) {
				continue
			}
			if _, exists := values[key]; exists {
				values[key] = value
			}
		}
	}
	merged = schema.sanitize(merged, coerced)
	appearance := settingsObject(merged["appearance"])
	patchAppearance := settingsObject(settingsObject(patch)["appearance"])
	_, syncPresent := patchAppearance["sync_font_sizes"]
	_, sizePresent := patchAppearance["global_font_size"]
	if appearance["sync_font_sizes"] == true && (syncPresent || sizePresent) {
		size := appearance["global_font_size"].(float64)
		for _, key := range []string{"ui_font_size", "terminal_font_size", "editor_font_size", "preview_font_size"} {
			appearance[key] = size
		}
		appearance["file_explorer_font_size"] = math.Max(1, size-math.Max(1, math.RoundToEven(size*0.12)))
	}
	return merged
}

func settingUnits(key string) string {
	switch {
	case key == "max_bytes":
		return "bytes"
	case strings.HasSuffix(key, "_ms"):
		return "milliseconds"
	case strings.HasSuffix(key, "_seconds"):
		return "seconds"
	case strings.HasSuffix(key, "_days"):
		return "days"
	case strings.HasSuffix(key, "_font_size") || slices.Contains([]string{"tab_width", "pane_spacing", "image_preview_max_px"}, key):
		return "pixels"
	case strings.HasSuffix(key, "_opacity"):
		return "percent"
	case key == "max_tabs_per_pane":
		return "tabs"
	case key == "scrollback":
		return "lines"
	default:
		return ""
	}
}

func (schema nativeSettingsSchema) catalog(current map[string]any) map[string]any {
	catalog := map[string]any{}
	for section, raw := range settingsObject(schema["DEFAULT_SETTINGS"]) {
		for key, fallback := range settingsObject(raw) {
			if section == "cost" && settingsHistoryKey(key) {
				continue
			}
			path := section + "." + key
			choices := settingsList(schema.entry("SETTING_PAYLOAD_CHOICE_ORDER", path))
			if choices == nil {
				choices = settingsList(schema.entry("SETTING_CHOICES", path))
			}
			if choices == nil {
				choices = []any{}
			}
			hidden := settingsList(schema.entry("SETTING_HIDDEN_CHOICES", path))
			if hidden == nil {
				hidden = []any{}
			}
			visible := []any{}
			for _, choice := range choices {
				if !slices.Contains(hidden, choice) {
					visible = append(visible, choice)
				}
			}
			kind := "string"
			switch value := fallback.(type) {
			case bool:
				kind = "boolean"
			case []any:
				kind = "list"
			case float64:
				if math.Trunc(value) == value {
					kind = "integer"
				} else {
					kind = "number"
				}
			}
			var limits any
			if bounds := settingsList(schema.entry("SETTING_LIMITS", path)); len(bounds) == 2 {
				limits = map[string]any{"min": bounds[0], "max": bounds[1]}
			}
			localeKeys := map[string]any{"description": "pref." + path + ".help", "label": "pref." + path + ".label"}
			for name, value := range settingsObject(schema.entry("SETTING_LOCALE_KEY_OVERRIDES", path)) {
				localeKeys[name] = value
			}
			label := strings.ReplaceAll(key, "_", " ")
			label = strings.ToUpper(label[:1]) + label[1:]
			if path == "uploads.max_bytes" {
				label = "File transfer size cap"
			}
			gui, _ := schema.entry("SETTING_GUI_SECTIONS", path).(string)
			guiLocale, _ := schema.entry("SETTING_GUI_SECTION_LOCALE_KEYS", gui).(string)
			description, _ := schema.entry("SETTING_COMMENTS", path).(string)
			sensitivity, _ := schema.entry("SETTING_SENSITIVITY", path).(string)
			if sensitivity == "" {
				sensitivity = "normal"
			}
			live := "live"
			if section == "yoagent" && slices.Contains([]string{"system_prompt", "intro", "format"}, key) {
				live = "next-use"
			}
			aliases := settingsObject(schema.entry("SETTING_VALUE_ALIASES", path))
			if aliases == nil {
				aliases = map[string]any{}
			}
			labels, metadata := map[string]any{}, map[string]any{}
			if key == "codex_model" && (section == "yoagent" || section == "summary") {
				for id, rawSpec := range settingsObject(schema["YOAGENT_CODEX_MODEL_CATALOG"]) {
					spec := settingsObject(rawSpec)
					labels[id] = spec["display_name"]
					metadata[id] = spec
				}
			}
			if key == "claude_model" && section == "yoagent" {
				for id, rawSpec := range settingsObject(schema["YOAGENT_CLAUDE_MODEL_CATALOG"]) {
					spec := settingsObject(rawSpec)
					labels[id] = spec["display_name"]
				}
			}
			catalog[path] = map[string]any{"path": path, "section": section, "key": key, "label": label, "locale_keys": localeKeys, "current": settingsObject(current[section])[key], "default": fallback, "type": kind, "choices": visible, "accepted_choices": choices, "hidden_choices": hidden, "choice_labels": labels, "choice_metadata": metadata, "limits": limits, "units": settingUnits(key), "empty_allowed": slices.Contains(settingsList(schema["STRING_ALLOW_EMPTY"]), any(path)), "list_limit": schema.entry("SETTING_LIST_LIMITS", path), "aliases": aliases, "description": description, "sensitivity": sensitivity, "requires_confirmation": slices.Contains(settingsList(schema["SETTING_WRITE_CONFIRMATION"]), any(path)), "read_role": "readonly", "write_role": "admin", "live_apply": live, "gui": map[string]any{"section": gui, "section_locale_key": guiLocale, "visible": gui != ""}}
		}
	}
	return catalog
}

// POSIX record locks interoperate with Python's lockf, unlike flock. A separate
// process-local lock is necessary because record locks are owned by the process.
var nativeSettingsLocks sync.Map

func lockNativeSettings(ctx context.Context, path string) (func() error, error) {
	canonical, err := filepath.Abs(path)
	if err != nil {
		return nil, err
	}
	if err := os.MkdirAll(filepath.Dir(canonical), 0700); err != nil {
		return nil, err
	}
	parent, err := filepath.EvalSymlinks(filepath.Dir(canonical))
	if err != nil {
		return nil, err
	}
	canonical = filepath.Join(parent, filepath.Base(canonical))
	value, _ := nativeSettingsLocks.LoadOrStore(canonical, &work.ContextMutex{})
	mutex := value.(*work.ContextMutex)
	if err := mutex.LockContext(ctx, nil); err != nil {
		return nil, err
	}
	file, err := os.OpenFile(filepath.Join(parent, "."+filepath.Base(path)+".shared-config.lock"), os.O_CREATE|os.O_RDWR, 0600)
	if err != nil {
		mutex.Unlock()
		return nil, err
	}
	lock := syscall.Flock_t{Type: syscall.F_WRLCK, Whence: io.SeekStart}
	ticker := time.NewTicker(10 * time.Millisecond)
	defer ticker.Stop()
	for {
		err = syscall.FcntlFlock(file.Fd(), syscall.F_SETLK, &lock)
		if err == nil {
			if contextErr := ctx.Err(); contextErr != nil {
				lock.Type = syscall.F_UNLCK
				unlockErr := syscall.FcntlFlock(file.Fd(), syscall.F_SETLK, &lock)
				closeErr := file.Close()
				mutex.Unlock()
				return nil, errors.Join(contextErr, unlockErr, closeErr)
			}
			break
		}
		if !errors.Is(err, syscall.EAGAIN) && !errors.Is(err, syscall.EACCES) {
			closeErr := file.Close()
			mutex.Unlock()
			return nil, errors.Join(err, closeErr)
		}
		select {
		case <-ctx.Done():
			closeErr := file.Close()
			mutex.Unlock()
			return nil, errors.Join(ctx.Err(), closeErr)
		case <-ticker.C:
		}
	}
	return func() error {
		lock.Type = syscall.F_UNLCK
		err := syscall.FcntlFlock(file.Fd(), syscall.F_SETLK, &lock)
		closeErr := file.Close()
		mutex.Unlock()
		return errors.Join(err, closeErr)
	}, nil
}

func settingsYAMLValue(value any) any {
	switch value := value.(type) {
	case float64:
		// Existing companions read integer settings with ParseInt. JSON decoding
		// must not turn those scalars into exponential YAML notation.
		if math.Trunc(value) == value && value >= math.MinInt64 && value < math.MaxInt64 {
			return int64(value)
		}
	case string:
		if strings.ContainsAny(value, "\r\n") {
			return &yaml.Node{Kind: yaml.ScalarNode, Tag: "!!str", Style: yaml.DoubleQuotedStyle, Value: value}
		}
	case []any:
		items := make([]any, len(value))
		for index, item := range value {
			items[index] = settingsYAMLValue(item)
		}
		return items
	}
	return value
}

func (schema nativeSettingsSchema) write(path string, current map[string]any) error {
	var output strings.Builder
	output.WriteString("# YOmux user preferences.\n# Hand edits are picked up by running servers. UI saves rewrite this file from this template.\n# Path: ~/.config/yomux/settings.yaml\n\n")
	sections := make([]string, 0, len(current))
	for section := range current {
		sections = append(sections, section)
	}
	sort.Strings(sections)
	for _, section := range sections {
		output.WriteString(section + ":\n")
		values := settingsObject(current[section])
		keys := make([]string, 0, len(values))
		for key := range values {
			keys = append(keys, key)
		}
		sort.Strings(keys)
		for _, key := range keys {
			if comment, ok := schema.entry("SETTING_COMMENTS", section+"."+key).(string); ok {
				output.WriteString("  # " + comment + "\n")
			}
			encoded, err := yaml.Marshal(map[string]any{key: settingsYAMLValue(values[key])})
			if err != nil {
				return err
			}
			for _, line := range strings.Split(strings.TrimSuffix(string(encoded), "\n"), "\n") {
				output.WriteString("  " + line + "\n")
			}
		}
		output.WriteByte('\n')
	}
	return atomicfile.Write(path, []byte(output.String()))
}

func (schema nativeSettingsSchema) read(path string) (map[string]any, map[string]any, error) {
	file, err := os.Open(path)
	if errors.Is(err, os.ErrNotExist) {
		if err := schema.write(path, schema.sanitize(nil, nil)); err != nil {
			return nil, nil, err
		}
		file, err = os.Open(path)
	}
	if err != nil {
		return nil, nil, err
	}
	data, readErr := io.ReadAll(io.LimitReader(file, 1<<20+1))
	info, statErr := file.Stat()
	closeErr := file.Close()
	if err := errors.Join(readErr, statErr, closeErr); err != nil {
		return nil, nil, err
	}
	if len(data) > 1<<20 {
		return nil, nil, errors.New("settings YAML exceeds 1 MiB")
	}
	var decoded any
	if err := yaml.Unmarshal(data, &decoded); err != nil {
		return nil, nil, fmt.Errorf("parse settings YAML: %w", err)
	}
	// Normalize numeric types without JSON encoding: YAML permits non-finite
	// numbers, which validation clamps, and unknown keys must remain ignorable.
	raw := settingsObject(normalizeSettingsYAML(decoded))
	current := schema.sanitize(raw, nil)
	previous := settingsObject(cloneSettingsValue(current))
	for _, provider := range []string{"openai", "anthropic"} {
		history := settingsProfileHistory(settingsObject(previous["cost"])["_"+provider+"_pricing_profile_history"])
		settingsObject(previous["cost"])[provider+"_pricing_profile"] = history[len(history)-1].profile
	}
	before := cloneSettingsValue(current["cost"])
	recordSettingsProfiles(previous, current, float64(info.ModTime().UnixNano())/1e9)
	if !reflect.DeepEqual(before, current["cost"]) {
		if err := schema.write(path, current); err != nil {
			return nil, nil, err
		}
	}
	return current, raw, nil
}

func normalizeSettingsYAML(value any) any {
	switch value := value.(type) {
	case int:
		return float64(value)
	case int64:
		return float64(value)
	case uint64:
		return float64(value)
	case map[string]any:
		for key, item := range value {
			value[key] = normalizeSettingsYAML(item)
		}
		return value
	case map[any]any:
		mapping := map[string]any{}
		for key, item := range value {
			if text, ok := key.(string); ok {
				mapping[text] = normalizeSettingsYAML(item)
			}
		}
		return mapping
	case []any:
		for index, item := range value {
			value[index] = normalizeSettingsYAML(item)
		}
		return value
	default:
		return value
	}
}

func (s *server) nativeSettingsPayload(ctx context.Context, patch any, save bool) (payload map[string]any, resultErr error) {
	schema, err := s.nativeSettingsSchema()
	if err != nil {
		return nil, err
	}
	path := filepath.Join(s.config, "settings.yaml")
	release, err := lockNativeSettings(ctx, path)
	if err != nil {
		return nil, err
	}
	defer func() { resultErr = errors.Join(resultErr, release()) }()
	current, raw, err := schema.read(path)
	coerced := []string{}
	if err == nil && save {
		next := schema.merge(current, patch, &coerced)
		recordSettingsProfiles(current, next, float64(time.Now().UnixNano())/1e9)
		if err = schema.write(path, next); err == nil {
			current, raw, err = schema.read(path)
		}
	}
	mtime := float64(0)
	if info, statErr := os.Stat(path); statErr == nil {
		mtime = float64(info.ModTime().UnixNano())
	} else if err == nil {
		return nil, statErr
	}
	choices := settingsObject(schema["SETTING_PAYLOAD_CHOICE_ORDER"])
	defaults := settingsObject(cloneSettingsValue(schema["DEFAULT_SETTINGS"]))
	if current == nil {
		current = defaults
	}
	payload = map[string]any{"settings": current, "defaults": defaults, "choices": choices, "catalog": schema.catalog(current), "path": path, "display_path": "~/.config/yomux/settings.yaml", "mtime_ns": mtime, "error": ""}
	if err != nil {
		payload["settings"] = nil
		payload["error"] = err.Error()
		return payload, nil
	}
	_, explicit := settingsObject(raw["general"])["default_tabber_mode"]
	payload["tabber_mode_explicit"] = explicit
	if save {
		sort.Strings(coerced)
		payload["coerced"] = coerced
	}
	return payload, nil
}

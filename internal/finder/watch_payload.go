// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package finder

import (
	"encoding/json"
	"fmt"
	"math"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
)

const (
	watchRootSurfacesVersion  = 1
	maxWatchDescriptorItems   = 99
	maxCompactTranscriptItems = 200
	maxSessionFilesHours      = 24 * 14
)

type normalizedWatchPayload struct {
	BackgroundFiles     []string
	RootSurfacesVersion int
	RootSurfaces        []map[string]any
	ContextItems        []map[string]any
	SessionFiles        []map[string]any
	ActivitySummary     map[string]any
}

func (s *Service) normalizeWatchPayload(payload map[string]any, roots, files []string) (normalizedWatchPayload, *Error) {
	background := make([]string, 0)
	activeFiles := make(map[string]bool, len(files))
	for _, path := range files {
		activeFiles[path] = true
	}
	for _, path := range normalizeWatchBackgroundFiles(payload["background_files"]) {
		if activeFiles[path] {
			continue
		}
		requested, _, authErr := s.authorize(path, true)
		if authErr == nil {
			background = append(background, requested)
		}
	}
	sort.Strings(background)
	accepted := make(map[string]bool, len(roots))
	for _, root := range roots {
		accepted[root] = true
	}
	rootSurfacesVersion, rootSurfaces, err := normalizeWatchRootSurfaces(payload, accepted)
	if err != nil {
		return normalizedWatchPayload{}, err
	}
	return normalizedWatchPayload{
		BackgroundFiles:     background,
		RootSurfacesVersion: rootSurfacesVersion,
		RootSurfaces:        rootSurfaces,
		ContextItems:        normalizeWatchContextItems(payload["context_items"]),
		SessionFiles:        normalizeWatchSessionFiles(payload["session_files"]),
		// The Go backend has no activity-summary producer in Phase 4/5. Returning an empty object
		// matches Python's disabled-feature boundary and prevents a false subscription claim.
		ActivitySummary: map[string]any{},
	}, nil
}

func normalizeWatchBackgroundFiles(value any) []string {
	items, ok := watchAnySlice(value)
	if !ok {
		return []string{}
	}
	seen := map[string]bool{}
	result := make([]string, 0, minFinderInt(len(items), MaxWatchFiles))
	for _, item := range items {
		path, ok := item.(string)
		if !ok {
			continue
		}
		path = strings.TrimSpace(path)
		if path == "" || !filepath.IsAbs(path) {
			continue
		}
		path = filepath.Clean(path)
		if !seen[path] {
			seen[path] = true
			result = append(result, path)
		}
	}
	sort.Strings(result)
	if len(result) > MaxWatchFiles {
		result = result[:MaxWatchFiles]
	}
	return result
}

func normalizeWatchRootSurfaces(payload map[string]any, accepted map[string]bool) (int, []map[string]any, *Error) {
	rawVersion, hasVersion := payload["root_surfaces_version"]
	rawRows, hasRows := payload["root_surfaces"]
	if !hasVersion && !hasRows {
		return 0, []map[string]any{}, nil
	}
	if !hasVersion || !hasRows {
		return 0, nil, invalid("", "root surface version and rows must be provided together")
	}
	version, ok := watchInt(rawVersion)
	if !ok || version != watchRootSurfacesVersion {
		return 0, nil, invalid("", "unsupported root surface protocol version")
	}
	rows, ok := watchAnySlice(rawRows)
	if !ok || len(rows) > MaxWatchRoots {
		return 0, nil, invalid("", "root surfaces must be a bounded list")
	}
	seen := make(map[string]bool, len(rows))
	result := make([]map[string]any, 0, len(rows))
	for _, raw := range rows {
		row, ok := raw.(map[string]any)
		if !ok {
			return 0, nil, invalid("", "each root surface row must be an object")
		}
		path, ok := row["path"].(string)
		path = strings.TrimSpace(path)
		if !ok || !filepath.IsAbs(path) {
			return 0, nil, invalid(path, "root surface paths must be absolute")
		}
		path = filepath.Clean(path)
		if seen[path] {
			return 0, nil, invalid(path, "root surface paths must be unique")
		}
		seen[path] = true
		rawSurfaces, ok := watchAnySlice(row["surfaces"])
		if !ok || len(rawSurfaces) == 0 || len(rawSurfaces) > 3 {
			return 0, nil, invalid(path, "root surfaces must be a bounded non-empty list")
		}
		surfacesSeen := map[string]bool{}
		surfaces := make([]string, 0, len(rawSurfaces))
		for _, rawSurface := range rawSurfaces {
			text := strings.TrimSpace(fmt.Sprint(rawSurface))
			if rawSurface == nil {
				text = ""
			}
			if !watchRootSurfaceNames[text] {
				return 0, nil, invalid(path, "root surfaces contain an unknown surface")
			}
			if !surfacesSeen[text] {
				surfacesSeen[text] = true
				surfaces = append(surfaces, text)
			}
		}
		if len(surfaces) == 0 {
			return 0, nil, invalid(path, "root surfaces must be a bounded non-empty list")
		}
		sort.Strings(surfaces)
		result = append(result, map[string]any{"path": path, "surfaces": surfaces})
	}
	if len(seen) != len(accepted) {
		return 0, nil, invalid("", "root surfaces must exactly cover accepted roots")
	}
	for path := range accepted {
		if !seen[path] {
			return 0, nil, invalid(path, "root surfaces must exactly cover accepted roots")
		}
	}
	sort.Slice(result, func(left, right int) bool {
		return result[left]["path"].(string) < result[right]["path"].(string)
	})
	return version, result, nil
}

var watchRootSurfaceNames = map[string]bool{
	"finder":                    true,
	"modified-files-parent":     true,
	"modified-files-repository": true,
}

func normalizeWatchContextItems(value any) []map[string]any {
	items, ok := watchAnySlice(value)
	if !ok {
		return []map[string]any{}
	}
	seen := map[string]bool{}
	result := make([]map[string]any, 0, minFinderInt(len(items), maxWatchDescriptorItems))
	for _, raw := range items {
		item, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		session := strings.TrimSpace(fmt.Sprint(item["session"]))
		if item["session"] == nil || session == "" {
			continue
		}
		messages := watchBoundedInt(item["messages"], 200, 1, maxCompactTranscriptItems)
		key := session + "\x00" + strconv.Itoa(messages)
		if seen[key] {
			continue
		}
		seen[key] = true
		result = append(result, map[string]any{"session": session, "messages": messages})
		if len(result) == maxWatchDescriptorItems {
			break
		}
	}
	return result
}

func normalizeWatchSessionFiles(value any) []map[string]any {
	items, ok := watchAnySlice(value)
	if !ok {
		return []map[string]any{}
	}
	seen := map[string]bool{}
	result := make([]map[string]any, 0, minFinderInt(len(items), maxWatchDescriptorItems))
	for _, raw := range items {
		item, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		var session any
		if text := strings.TrimSpace(fmt.Sprint(item["session"])); item["session"] != nil && text != "" {
			session = text
		}
		request := map[string]any{
			"session":   session,
			"hours":     watchBoundedFloat(item["hours"], 24, 0.25, maxSessionFilesHours),
			"from_ref":  watchOptionalString(item["from_ref"], item["from"]),
			"to_ref":    watchOptionalString(item["to_ref"], item["to"]),
			"repo_refs": nil,
		}
		if refs, ok := item["repo_refs"].(map[string]any); ok {
			request["repo_refs"] = refs
		}
		encoded, err := json.Marshal(request)
		key := string(encoded)
		if err != nil {
			key = fmt.Sprintf("%#v", request)
		}
		if seen[key] {
			continue
		}
		seen[key] = true
		result = append(result, request)
		if len(result) == maxWatchDescriptorItems {
			break
		}
	}
	return result
}

func watchOptionalString(primary, fallback any) any {
	value := strings.TrimSpace(fmt.Sprint(primary))
	if primary == nil || value == "" {
		value = strings.TrimSpace(fmt.Sprint(fallback))
	}
	if primary == nil && fallback == nil || value == "" {
		return nil
	}
	return value
}

func watchAnySlice(value any) ([]any, bool) {
	switch values := value.(type) {
	case []any:
		return values, true
	case []map[string]any:
		result := make([]any, len(values))
		for index, item := range values {
			result[index] = item
		}
		return result, true
	default:
		return nil, false
	}
}

func watchInt(value any) (int, bool) {
	switch number := value.(type) {
	case int:
		return number, true
	case int64:
		return int(number), int64(int(number)) == number
	case float64:
		return int(number), !math.IsNaN(number) && !math.IsInf(number, 0) && number == float64(int(number))
	case json.Number:
		parsed, err := strconv.Atoi(string(number))
		return parsed, err == nil
	default:
		return 0, false
	}
}

func watchBoundedInt(value any, fallback, minimum, maximum int) int {
	parsed, ok := watchFloat(value)
	if !ok {
		parsed = float64(fallback)
	}
	if parsed < float64(minimum) {
		parsed = float64(minimum)
	}
	if parsed > float64(maximum) {
		parsed = float64(maximum)
	}
	return int(parsed)
}

func watchBoundedFloat(value any, fallback, minimum, maximum float64) float64 {
	parsed, ok := watchFloat(value)
	if !ok {
		parsed = fallback
	}
	if parsed < minimum {
		return minimum
	}
	if parsed > maximum {
		return maximum
	}
	return parsed
}

func watchFloat(value any) (float64, bool) {
	switch number := value.(type) {
	case float64:
		return number, !math.IsNaN(number) && !math.IsInf(number, 0)
	case float32:
		return float64(number), !math.IsNaN(float64(number)) && !math.IsInf(float64(number), 0)
	case int:
		return float64(number), true
	case int64:
		return float64(number), true
	case json.Number:
		parsed, err := strconv.ParseFloat(string(number), 64)
		return parsed, err == nil && !math.IsNaN(parsed) && !math.IsInf(parsed, 0)
	case string:
		parsed, err := strconv.ParseFloat(strings.TrimSpace(number), 64)
		return parsed, err == nil && !math.IsNaN(parsed) && !math.IsInf(parsed, 0)
	default:
		return 0, false
	}
}

func minFinderInt(left, right int) int {
	if left < right {
		return left
	}
	return right
}

// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

// Package locales owns native admission of the shipped locale registry.
package locales

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"unicode"
	"unicode/utf8"
)

type Spec struct {
	Code             string   `json:"code"`
	Endonym          string   `json:"endonym"`
	PluralRule       string   `json:"plural_rule"`
	Aliases          []string `json:"aliases"`
	PluralCategories []string `json:"plural_categories"`
	Interrogatives   []string `json:"interrogatives"`
}

var localeCodePattern = regexp.MustCompile(`^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$`)

func Load(root string) ([]Spec, error) {
	raw, err := os.ReadFile(filepath.Join(root, "assets", "runtime", "locale-registry.json"))
	if err != nil {
		return nil, fmt.Errorf("read locale registry: %w", err)
	}
	if !utf8.Valid(raw) {
		return nil, fmt.Errorf("locale registry is not UTF-8")
	}
	var specs []Spec
	if err := json.Unmarshal(raw, &specs); err != nil {
		return nil, fmt.Errorf("decode locale registry: %w", err)
	}
	if len(specs) == 0 || specs[0].Code != "en" {
		return nil, fmt.Errorf("locale registry has no English fallback")
	}
	seen := make(map[string]bool, len(specs))
	for _, spec := range specs {
		folded := strings.ToLower(spec.Code)
		if !localeCodePattern.MatchString(spec.Code) || strings.EqualFold(spec.Code, "en-XA") || seen[folded] {
			return nil, fmt.Errorf("invalid or duplicate locale code %q", spec.Code)
		}
		seen[folded] = true
	}
	return specs, nil
}

// TrimSpace retains Python's four additional C0 whitespace separators.
func TrimSpace(value string) string {
	return strings.TrimFunc(value, func(r rune) bool { return unicode.IsSpace(r) || (r >= 0x1c && r <= 0x1f) })
}

func Normalize(specs []Spec, value, fallback string, allowSystem bool) string {
	value = TrimSpace(value)
	if allowSystem && strings.EqualFold(value, "system") {
		return "system"
	}
	if strings.EqualFold(value, "en-XA") {
		return "en-XA"
	}
	for _, spec := range specs {
		if strings.EqualFold(value, spec.Code) {
			return spec.Code
		}
	}
	return fallback
}

// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"encoding/json"
	"fmt"
	"math"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"

	localeregistry "github.com/keivenchang/yomux/internal/locales"
)

type pageLocaleSpec = localeregistry.Spec

type pageLocales struct {
	specs      []pageLocaleSpec
	catalogs   map[string]map[string]string
	locale     string
	preference string
}

func (s *server) nativeLocaleSpecs() ([]pageLocaleSpec, error) { return localeregistry.Load(s.root) }

func (s *server) pageLocales(preference, acceptLanguage string) (*pageLocales, error) {
	specs, err := s.nativeLocaleSpecs()
	if err != nil {
		return nil, err
	}
	p := &pageLocales{specs: specs, catalogs: make(map[string]map[string]string)}
	p.preference = p.normalize(preference, "system", true)
	p.locale = p.resolve(p.preference, acceptLanguage)
	for _, code := range []string{"en", p.locale} {
		if _, exists := p.catalogs[code]; exists {
			continue
		}
		path := filepath.Join(s.root, "static", "locales", code+".json")
		catalog, err := os.ReadFile(path)
		if os.IsNotExist(err) && code == "en-XA" {
			continue
		}
		if err != nil {
			return nil, fmt.Errorf("read %s locale: %w", code, err)
		}
		var values map[string]string
		if err := json.Unmarshal(catalog, &values); err != nil {
			return nil, fmt.Errorf("decode %s locale: %w", code, err)
		}
		p.catalogs[code] = values
	}
	return p, nil
}

func (p *pageLocales) normalize(value, fallback string, allowSystem bool) string {
	return localeregistry.Normalize(p.specs, value, fallback, allowSystem)
}

func (p *pageLocales) languageTag(tag string) string {
	tag = strings.ToLower(strings.ReplaceAll(strings.TrimSpace(tag), "_", "-"))
	if strings.HasPrefix(tag, "zh") {
		for _, part := range strings.Split(tag, "-") {
			if part == "hant" || part == "tw" || part == "hk" || part == "mo" {
				return "zh-Hant"
			}
		}
		return "zh-Hans"
	}
	if exact := p.normalize(tag, "", false); exact != "" && exact != "en-XA" {
		return exact
	}
	base, _, _ := strings.Cut(tag, "-")
	for _, spec := range p.specs {
		specBase, _, _ := strings.Cut(strings.ToLower(spec.Code), "-")
		if base == specBase {
			return spec.Code
		}
	}
	return ""
}

func (p *pageLocales) resolve(value, header string) string {
	if preference := p.normalize(value, "system", true); preference != "system" {
		return preference
	}
	type language struct {
		tag     string
		quality float64
	}
	weighted := []language{}
	for _, item := range strings.Split(header, ",") {
		parts := strings.Split(item, ";")
		tag, quality := strings.TrimSpace(parts[0]), 1.0
		if tag == "" || tag == "*" {
			continue
		}
		for _, part := range parts[1:] {
			part = strings.TrimSpace(part)
			if !strings.HasPrefix(strings.ToLower(part), "q=") {
				continue
			}
			number, err := strconv.ParseFloat(part[2:], 64)
			if err != nil {
				quality = 0
			} else {
				quality = number
			}
		}
		if quality > 0 && !math.IsNaN(quality) {
			weighted = append(weighted, language{tag, quality})
		}
	}
	sort.SliceStable(weighted, func(i, j int) bool { return weighted[i].quality > weighted[j].quality })
	for _, item := range weighted {
		if code := p.languageTag(item.tag); code != "" {
			return code
		}
	}
	return "en"
}

func localeDirection(code string) string {
	base, _, _ := strings.Cut(strings.ToLower(code), "-")
	switch base {
	case "ar", "fa", "he", "ur":
		return "rtl"
	default:
		return "ltr"
	}
}

func (p *pageLocales) text(key string, params ...string) string {
	value := p.catalogs[p.locale][key]
	if value == "" {
		value = p.catalogs["en"][key]
	}
	if value == "" {
		value = key
	}
	for index := 0; index+1 < len(params); index += 2 {
		value = strings.ReplaceAll(value, "{"+params[index]+"}", params[index+1])
	}
	return value
}

func (p *pageLocales) registry(header string) map[string]any {
	locales := make([]map[string]string, 0, len(p.specs))
	for _, spec := range p.specs {
		locales = append(locales, map[string]string{"id": spec.Code, "endonym": spec.Endonym, "direction": localeDirection(spec.Code)})
	}
	return map[string]any{"fallback": "en", "pseudo": "en-XA", "systemPreference": "system", "systemLocale": p.resolve("system", header), "locales": locales}
}

func (s *server) applyLocaleSettingsSchema(schema nativeSettingsSchema) error {
	specs, err := s.nativeLocaleSpecs()
	if err != nil {
		return err
	}
	choices := []any{"system", "en-XA"}
	codes := []string{}
	aliases := map[string]any{"pseudo": "en-XA", "system language": "system"}
	for _, spec := range specs {
		choices = append(choices, spec.Code)
		codes = append(codes, spec.Code)
		for _, alias := range spec.Aliases {
			aliases[alias] = spec.Code
		}
	}
	sort.Slice(choices, func(i, j int) bool { return choices[i].(string) < choices[j].(string) })
	settingsObject(schema["SETTING_CHOICES"])["general.language"] = choices
	settingsObject(schema["SETTING_VALUE_ALIASES"])["general.language"] = aliases
	settingsObject(schema["SETTING_COMMENTS"])["general.language"] = "UI language. system matches the browser/OS; otherwise a locale code with a shipped catalog (" + strings.Join(append(codes, "en-XA pseudo"), ", ") + ")."
	return nil
}

func savedLanguagePreference(payload map[string]any) string {
	language, ok := settingsObject(settingsObject(payload["settings"])["general"])["language"].(string)
	if !ok {
		return "system"
	}
	return language
}

// Login/setup cookies carry an exact shipped preference, never an arbitrary
// locale or the development pseudo-locale. Missing/invalid cookies use the
// saved language; malformed settings still allow the pre-auth login shell.
func (s *server) requestLocalePreference(r *http.Request) (string, error) {
	specs, err := s.nativeLocaleSpecs()
	if err != nil {
		return "", err
	}
	for _, cookie := range r.Cookies() {
		if cookie.Name != "yomux_locale" && cookie.Name != "yolomux_locale" {
			continue
		}
		candidate, err := url.PathUnescape(strings.TrimSpace(cookie.Value))
		if err != nil {
			continue
		}
		if candidate == "system" {
			return candidate, nil
		}
		for _, spec := range specs {
			if candidate == spec.Code {
				return candidate, nil
			}
		}
	}
	payload, err := s.nativeSettingsPayload(r.Context(), nil, false)
	if err != nil {
		return "", err
	}
	return localeregistry.Normalize(specs, savedLanguagePreference(payload), "system", true), nil
}

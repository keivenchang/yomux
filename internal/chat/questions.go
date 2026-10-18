// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package chat

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strings"
	"unicode"
	"unicode/utf8"

	"github.com/keivenchang/yomux/internal/locales"
	"golang.org/x/text/cases"
	"golang.org/x/text/unicode/norm"
)

type questionRules struct {
	Punctuation          string   `json:"punctuation"`
	TrailingClosers      string   `json:"trailing_closers"`
	LeadingExclusions    string   `json:"leading_exclusions"`
	URLOrCodePattern     string   `json:"url_or_code_pattern"`
	CompactPrefixLocales []string `json:"compact_prefix_locales"`
}

type QuestionClassifier struct {
	specs     []locales.Spec
	rules     questionRules
	urlOrCode *regexp.Regexp
}

func NewQuestionClassifier(root string) (*QuestionClassifier, error) {
	specs, err := locales.Load(root)
	if err != nil {
		return nil, err
	}
	raw, err := os.ReadFile(filepath.Join(root, "assets", "runtime", "chat-question-rules.json"))
	if err != nil {
		return nil, err
	}
	if !utf8.Valid(raw) {
		return nil, errors.New("chat question rules are not UTF-8")
	}
	var rules questionRules
	if err := json.Unmarshal(raw, &rules); err != nil {
		return nil, err
	}
	if rules.Punctuation == "" || rules.URLOrCodePattern == "" || rules.TrailingClosers == "" || rules.LeadingExclusions == "" {
		return nil, errors.New("incomplete chat question rules")
	}
	expression, err := regexp.Compile("(?i)" + rules.URLOrCodePattern)
	if err != nil {
		return nil, err
	}
	for _, spec := range specs {
		if len(spec.Interrogatives) == 0 {
			return nil, errors.New("missing locale question prefixes")
		}
		for _, prefix := range spec.Interrogatives {
			if locales.TrimSpace(prefix) == "" {
				return nil, errors.New("empty locale question prefix")
			}
		}
	}
	for _, code := range rules.CompactPrefixLocales {
		if !slices.ContainsFunc(specs, func(spec locales.Spec) bool { return spec.Code == code }) {
			return nil, errors.New("unknown compact-prefix locale")
		}
	}
	return &QuestionClassifier{specs: specs, rules: rules, urlOrCode: expression}, nil
}

func (c *QuestionClassifier) tail(value string) string {
	runes := []rune(value)
	for len(runes) > 0 && strings.ContainsRune(c.rules.TrailingClosers, runes[len(runes)-1]) {
		runes = runes[:len(runes)-1]
		for len(runes) > 0 && (unicode.IsSpace(runes[len(runes)-1]) || (runes[len(runes)-1] >= 0x1c && runes[len(runes)-1] <= 0x1f)) {
			runes = runes[:len(runes)-1]
		}
	}
	return string(runes)
}

func (c *QuestionClassifier) IsQuestion(body, locale string) bool {
	raw := locales.TrimSpace(body)
	text := norm.NFKC.String(raw)
	match := c.urlOrCode.FindStringIndex(text)
	if text == "" || (match != nil && match[0] == 0 && match[1] == len(text)) {
		return false
	}
	runes := []rune(text)
	if strings.ContainsRune(c.rules.LeadingExclusions, runes[0]) {
		return false
	}
	tail, rawTail := []rune(c.tail(text)), []rune(c.tail(raw))
	punctuated := len(tail) > 0 && strings.ContainsRune(c.rules.Punctuation, tail[len(tail)-1])
	punctuated = punctuated || (len(rawTail) > 0 && strings.ContainsRune(c.rules.Punctuation, rawTail[len(rawTail)-1]))
	if punctuated && !c.urlOrCode.MatchString(string(tail[max(0, len(tail)-256):])) {
		return true
	}
	code := locales.Normalize(c.specs, locale, "en", false)
	prefixes := c.specs[0].Interrogatives
	for _, spec := range c.specs {
		if spec.Code == code {
			prefixes = spec.Interrogatives
			break
		}
	}
	fold := cases.Fold()
	lowered := fold.String(text)
	for _, prefix := range prefixes {
		candidate := fold.String(norm.NFKC.String(prefix))
		if lowered == candidate || strings.HasPrefix(lowered, candidate+" ") {
			return true
		}
		if slices.Contains(c.rules.CompactPrefixLocales, code) && strings.HasPrefix(lowered, candidate) {
			return true
		}
	}
	return false
}

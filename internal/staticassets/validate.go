// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package staticassets

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"regexp"
	"slices"
	"strings"
	"unicode"
	"unicode/utf8"
)

func validCategory(category string) bool {
	switch category {
	case "zero", "one", "two", "few", "many", "other":
		return true
	}
	return false
}

func readCatalog(path string, catalog *map[string]string) error {
	data, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	if !utf8.Valid(data) {
		return fmt.Errorf("invalid UTF-8 catalog %s", path)
	}
	var raw map[string]json.RawMessage
	if err := json.Unmarshal(data, &raw); err != nil {
		return err
	}
	if raw == nil {
		return fmt.Errorf("catalog must be an object: %s", path)
	}
	result := make(map[string]string, len(raw))
	for key, value := range raw {
		if len(value) == 0 || value[0] != '"' {
			return fmt.Errorf("%s: non-string value at %s", path, key)
		}
		var text string
		if err := json.Unmarshal(value, &text); err != nil {
			return err
		}
		result[key] = text
	}
	*catalog = result
	return nil
}

// Reject a truncated partial before it can consume a subsequent partial's rules.
func validateCSS(text string) error {
	depth := 0
	for i := 0; i < len(text); i++ {
		if i+1 < len(text) && text[i:i+2] == "/*" {
			end := strings.Index(text[i+2:], "*/")
			if end < 0 {
				return errors.New("unterminated CSS comment")
			}
			i += end + 3
			continue
		}
		if text[i] == '\'' || text[i] == '"' {
			quote := text[i]
			closed := false
			for i++; i < len(text); i++ {
				if text[i] == '\\' {
					i++
					continue
				}
				if text[i] == quote {
					closed = true
					break
				}
			}
			if !closed {
				return errors.New("unterminated CSS string")
			}
			continue
		}
		if text[i] == '{' {
			depth++
		}
		if text[i] == '}' {
			depth--
			if depth < 0 {
				return errors.New("unbalanced CSS braces")
			}
		}
	}
	if depth != 0 {
		return errors.New("unbalanced CSS braces")
	}
	return nil
}

// Python uses Unicode word boundaries; RE2's ASCII boundaries need the same
// adjacent-letter check around keyboard shortcuts in otherwise Unicode prose.
func tokenSpans(pattern *regexp.Regexp, value string) [][]int {
	result := make([][]int, 0)
	for _, pair := range pattern.FindAllStringIndex(value, -1) {
		token := value[pair[0]:pair[1]]
		if strings.HasPrefix(token, "Ctrl") || strings.HasPrefix(token, "Cmd") {
			word := func(r rune) bool { return unicode.IsLetter(r) || unicode.IsNumber(r) || r == '_' }
			if pair[0] > 0 {
				r, _ := utf8.DecodeLastRuneInString(value[:pair[0]])
				if word(r) {
					continue
				}
			}
			// RE2 cannot express Python's Unicode word boundary. Match the keyboard
			// branch greedily, then backtrack to its last valid Unicode boundary.
			accepted := false
			for end := pair[1]; end > pair[0]; end-- {
				previous, _ := utf8.DecodeLastRuneInString(value[:end])
				nextWord := false
				if end < len(value) {
					next, _ := utf8.DecodeRuneInString(value[end:])
					nextWord = word(next)
				}
				if word(previous) == nextWord {
					continue
				}
				candidate := pattern.FindStringIndex(value[pair[0]:end])
				if candidate != nil && candidate[0] == 0 && candidate[1] == end-pair[0] {
					pair[1] = end
					accepted = true
					break
				}
			}
			if !accepted {
				continue
			}
		}
		result = append(result, pair)
	}
	return result
}

func compileTokenGrammar(pattern string) (*regexp.Regexp, error) {
	// The shared grammar uses word boundaries only around keyboard shortcuts;
	// tokenSpans owns those boundaries with Unicode-aware adjacent characters.
	return regexp.Compile(strings.ReplaceAll(pattern, `\b`, ""))
}

func tokenCounts(pattern *regexp.Regexp, value string, ignoreCode bool) map[string]int {
	result := make(map[string]int)
	for _, span := range tokenSpans(pattern, value) {
		token := value[span[0]:span[1]]
		if ignoreCode && strings.HasPrefix(token, "`") {
			continue
		}
		result[strings.TrimRight(token, ".,;:")]++
	}
	return result
}

func sameCounts(a, b map[string]int) bool {
	if len(a) != len(b) {
		return false
	}
	for k, v := range a {
		if b[k] != v {
			return false
		}
	}
	return true
}

func (p *pseudoLocalizer) validateTranslation(locale, key, source, value string) error {
	if strings.TrimSpace(source) != "" && strings.TrimSpace(value) == "" && !slices.Contains(p.spec.OptionalBlankTranslations[key], locale) {
		return fmt.Errorf("%s blank translation at %s", locale, key)
	}
	if !sameCounts(tokenCounts(p.placeholders, source, false), tokenCounts(p.placeholders, value, false)) {
		return fmt.Errorf("%s placeholder drift at %s", locale, key)
	}
	ignoreCode := slices.Contains(p.spec.TranslatableCodeKeys, key)
	if !sameCounts(tokenCounts(p.protected, source, ignoreCode), tokenCounts(p.protected, value, ignoreCode)) {
		return fmt.Errorf("%s protected-token drift at %s", locale, key)
	}
	return nil
}

// JSON escapes for actual separators may be removed; escaped literal text
// such as "\\u2028" must remain a pair of backslashes in the encoded document.
func literalSeparators(data []byte) []byte {
	var out bytes.Buffer
	for i := 0; i < len(data); i++ {
		if data[i] == '\\' && i+1 < len(data) {
			if bytes.HasPrefix(data[i:], []byte(`\u2028`)) {
				out.WriteRune('\u2028')
				i += 5
				continue
			}
			if bytes.HasPrefix(data[i:], []byte(`\u2029`)) {
				out.WriteRune('\u2029')
				i += 5
				continue
			}
			out.Write(data[i : i+2])
			i++
			continue
		}
		out.WriteByte(data[i])
	}
	return out.Bytes()
}

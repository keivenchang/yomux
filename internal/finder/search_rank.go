// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package finder

import (
	_ "embed"
	"encoding/json"
	"strings"
	"unicode"
	"unicode/utf16"

	"golang.org/x/text/cases"
	"golang.org/x/text/language"
)

// The browser bundle reads this same authored table at build time.
//
//go:embed search_rank_weights.json
var searchRankWeightsJSON []byte

type filenameSearchWeights struct {
	PerChar, Contiguous, ContiguousSubstring, WordStart                  float64
	GapPenalty, HaystackLengthPenalty, AnchorPrimary                     float64
	FileNamePrefix, FileNameExact, FileNameContains, FileNameSubsequence float64
}

var filenameRankWeights = func() filenameSearchWeights {
	var weights filenameSearchWeights
	if err := json.Unmarshal(searchRankWeightsJSON, &weights); err != nil {
		panic(err)
	}
	return weights
}()

// ECMAScript whitespace excludes NEL and includes BOM. Filename queries use
// this policy; established index tokenization deliberately remains unchanged.
func searchWhitespace(char rune) bool {
	return char == '\ufeff' || char != '\u0085' && unicode.IsSpace(char)
}

func searchLowerText(text string) string {
	// Most filenames are ASCII; contextual Unicode casing is needed only when
	// a non-ASCII byte occurs, and constructing a transformer per name is costly.
	for index := 0; index < len(text); index++ {
		if text[index] >= 0x80 {
			return cases.Lower(language.Und).String(text)
		}
	}
	return strings.ToLower(text)
}

type preparedFilenameSearchToken struct {
	text      string
	units     []uint16
	canonical string
}

func prepareFilenameSearchTokens(query string) []preparedFilenameSearchToken {
	tokens := strings.FieldsFunc(query, searchWhitespace)
	prepared := make([]preparedFilenameSearchToken, 0, len(tokens))
	for _, token := range tokens {
		text := searchLowerText(token)
		prepared = append(prepared, preparedFilenameSearchToken{
			text: text, units: utf16.Encode([]rune(text)), canonical: alnumSearchText(text),
		})
	}
	return prepared
}

func searchWordStart(text []uint16, index int) bool {
	if index == 0 {
		return true
	}
	previous := rune(text[index-1])
	return searchWhitespace(previous) || strings.ContainsRune("/_:.-", previous)
}

func searchUnitsIndex(text, needle []uint16, start int) int {
	for index := start; index+len(needle) <= len(text); index++ {
		matched := true
		for offset, unit := range needle {
			if text[index+offset] != unit {
				matched = false
				break
			}
		}
		if matched {
			return index
		}
	}
	return -1
}

// Match the browser's fuzzySubsequenceMatch, including UTF-16 offsets, first
// contiguous occurrence, word starts, gaps and length penalties. The shortlist
// excludes browser-local history bonuses; those remain in its final ranker.
func fuzzyFilenameScore(token preparedFilenameSearchToken, text []uint16) (float64, bool) {
	w := filenameRankWeights
	needle := token.units
	if len(needle) == 0 {
		return 0, true
	}
	penalty := float64(max(0, len(text)-len(needle))) * w.HaystackLengthPenalty
	if start := searchUnitsIndex(text, needle, 0); start >= 0 {
		score := float64(len(needle))*w.PerChar + float64(len(needle)-1)*w.Contiguous + w.ContiguousSubstring
		if searchWordStart(text, start) {
			score += w.WordStart
		}
		return score - penalty, true
	}
	position, previous := 0, -1
	score := 0.0
	for _, char := range token.text {
		units := utf16.Encode([]rune{char})
		index := searchUnitsIndex(text, units, position)
		if index < 0 {
			return 0, false
		}
		score += w.PerChar
		if previous >= 0 && index == previous+1 {
			score += w.Contiguous
		}
		if searchWordStart(text, index) {
			score += w.WordStart
		}
		score -= float64(max(0, index-position)) * w.GapPenalty
		previous, position = index, index+1
	}
	return score - penalty, true
}

func searchFilenameSortKey(name string, tokens []preparedFilenameSearchToken) (searchSortKey, bool) {
	key := searchSortKey{length: len(name), lowerBase: strings.ToLower(name), lowerName: name}
	lowerName := searchLowerText(name)
	text, canonicalName := utf16.Encode([]rune(lowerName)), alnumSearchText(lowerName)
	w := filenameRankWeights
	for _, token := range tokens {
		score, ok := fuzzyFilenameScore(token, text)
		if !ok {
			return searchSortKey{}, false
		}
		canonicalToken := token.canonical
		if canonicalToken != "" {
			switch {
			case canonicalName == canonicalToken:
				score += w.FileNameExact
			case strings.HasPrefix(canonicalName, canonicalToken):
				score += w.FileNamePrefix
			case strings.Contains(canonicalName, canonicalToken):
				score += w.FileNameContains
			default:
				score += w.FileNameSubsequence
			}
			if strings.HasPrefix(canonicalName, canonicalToken) {
				score += w.AnchorPrimary
			}
		}
		key.score += score
	}
	return key, true
}

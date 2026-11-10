// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

// Package tailfile reads a bounded suffix of line-oriented files.
package tailfile

import (
	"io"
	"os"
)

func Read(path string, lines int, byteLimit int64) (string, error) {
	file, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer file.Close()
	return ReadFile(file, lines, byteLimit)
}

func ReadFile(file *os.File, lines int, byteLimit int64) (string, error) {
	if lines < 1 {
		return "", nil
	}
	info, err := file.Stat()
	if err != nil {
		return "", err
	}
	return readTail(file, info.Size(), lines, byteLimit)
}

func readTail(reader io.ReaderAt, size int64, lines int, byteLimit int64) (string, error) {
	if lines < 1 {
		return "", nil
	}
	maxBytes := int64(lines) * 64 * 1024
	if maxBytes < 64*1024 {
		maxBytes = 64 * 1024
	}
	if byteLimit > 0 && maxBytes > byteLimit {
		maxBytes = byteLimit
	}
	start := max(int64(0), size-maxBytes)
	data, err := io.ReadAll(io.NewSectionReader(reader, start, size-start))
	if err != nil {
		return "", err
	}
	text := string(data)
	if start > 0 {
		for index := 0; index < len(text); index++ {
			if text[index] == '\n' {
				text = text[index+1:]
				break
			}
		}
	}
	parts := make([]string, 0, lines)
	startIndex := 0
	for index := 0; index < len(text); index++ {
		if text[index] == '\n' {
			parts = append(parts, text[startIndex:index+1])
			startIndex = index + 1
		}
	}
	if startIndex < len(text) {
		parts = append(parts, text[startIndex:])
	}
	if len(parts) > lines {
		parts = parts[len(parts)-lines:]
	}
	result := make([]byte, 0, len(text))
	for _, part := range parts {
		result = append(result, part...)
	}
	return string(result), nil
}

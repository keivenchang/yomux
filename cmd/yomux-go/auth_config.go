// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import "strings"

// These scalar rules mirror yomux_lib.auth's deliberately small YAML contract.
func stripYAMLComment(line string) string {
	single, double, escaped := false, false, false
	for i, c := range line {
		if escaped {
			escaped = false
			continue
		}
		if c == '\\' && double {
			escaped = true
			continue
		}
		if c == '"' && !single {
			double = !double
			continue
		}
		if c == '\'' && !double {
			single = !single
			continue
		}
		if c == '#' && !single && !double {
			return line[:i]
		}
	}
	return line
}

func yamlValue(value string) string {
	value = strings.TrimSpace(value)
	if len(value) >= 2 {
		if value[0] == '"' && value[len(value)-1] == '"' {
			return strings.ReplaceAll(strings.ReplaceAll(value[1:len(value)-1], `\"`, `"`), `\\`, `\`)
		}
		if value[0] == '\'' && value[len(value)-1] == '\'' {
			return strings.ReplaceAll(value[1:len(value)-1], "''", "'")
		}
	}
	return value
}

func parseAuthYAML(text string) []authUser {
	var users []authUser
	var current map[string]string
	inUsers := false
	publish := func() {
		username := strings.TrimSpace(current["username"])
		password := current["password_hash"]
		if password == "" {
			password = current["password"]
		}
		role, exists := current["role"]
		if !exists {
			role = current["access"]
		}
		role = strings.ToLower(strings.TrimSpace(role))
		if role != "admin" {
			role = "readonly"
		}
		if username != "" && password != "" {
			users = append(users, authUser{username: username, password: password, role: role})
		}
	}
	for _, raw := range strings.Split(text, "\n") {
		line := strings.TrimSpace(stripYAMLComment(raw))
		if line == "" {
			continue
		}
		if line == "users:" {
			publish()
			current = nil
			inUsers = true
			continue
		}
		if !inUsers {
			continue
		}
		if strings.HasPrefix(line, "- ") {
			publish()
			current = map[string]string{}
			line = strings.TrimSpace(line[2:])
		} else if current == nil && strings.HasSuffix(line, ":") {
			current = map[string]string{"username": yamlValue(strings.TrimSuffix(line, ":"))}
			continue
		}
		if current == nil {
			continue
		}
		key, value, ok := strings.Cut(line, ":")
		if ok && strings.TrimSpace(key) != "" {
			current[strings.TrimSpace(key)] = yamlValue(value)
		}
	}
	publish()
	return users
}

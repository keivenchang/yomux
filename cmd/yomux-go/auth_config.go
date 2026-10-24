// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"crypto/rand"
	"encoding/base64"
	"errors"
	"fmt"
	"os"
	"os/user"
	"path/filepath"
	"strings"
)

const starterAuthConfigHeader = "# YOmux authentication is disabled until an account entry is uncommented.\n"

func initializeStarterAuthConfig(path string) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return err
	}
	username := "admin"
	if current, err := user.Current(); err == nil && strings.TrimSpace(current.Username) != "" {
		username = current.Username
	} else if value := strings.TrimSpace(os.Getenv("USER")); value != "" {
		username = value
	} else if value := strings.TrimSpace(os.Getenv("LOGNAME")); value != "" {
		username = value
	}
	passwordBytes := make([]byte, 18)
	if _, err := rand.Read(passwordBytes); err != nil {
		return fmt.Errorf("generate starter admin password: %w", err)
	}
	password := base64.RawURLEncoding.EncodeToString(passwordBytes)
	content := starterAuthConfigHeader + fmt.Sprintf("# Uncomment the admin account below, save the file, then refresh the browser.\n# Keep admin credentials private. Add readonly accounts explicitly only when needed.\n#\nusers:\n#  - username: %q\n#    password: %q\n#    role: \"admin\"\n", username, password)
	file, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if errors.Is(err, os.ErrExist) {
		return nil
	}
	if err != nil {
		return err
	}
	if _, err := file.WriteString(content); err != nil {
		_ = file.Close()
		_ = os.Remove(path)
		return err
	}
	if err := file.Sync(); err != nil {
		_ = file.Close()
		_ = os.Remove(path)
		return err
	}
	if err := file.Close(); err != nil {
		_ = os.Remove(path)
		return err
	}
	directory, err := os.Open(filepath.Dir(path))
	if err != nil {
		return err
	}
	return errors.Join(directory.Sync(), directory.Close())
}

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

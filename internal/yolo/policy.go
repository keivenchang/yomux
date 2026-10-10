// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

// Package yolo loads and evaluates command rules without worker or terminal
// dependencies.
package yolo

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"

	"gopkg.in/yaml.v3"
)

type Action string

const (
	Approve Action = "approve"
	Decline Action = "decline"
	Block   Action = "block"
	Ask     Action = "ask"
	Notify  Action = "notify"
	Off     Action = "off"
)

type MatchType string

const (
	MatchCommand  MatchType = "command"
	MatchRegex    MatchType = "regex"
	MatchGlob     MatchType = "glob"
	MatchContains MatchType = "contains"
)

type Rule struct {
	Name     string
	Type     MatchType
	Patterns []string
	Action   Action
	Risk     string
	compiled []*regexp.Regexp
}

type Policy struct {
	Default Action
	Rules   []Rule
}

type Decision struct {
	Action     Action
	RuleName   string
	Risk       string
	Source     string
	ReasonCode string
	Error      string
}

type invocation struct {
	name string
	args []string
}

var (
	validActions   = map[Action]bool{Approve: true, Decline: true, Block: true, Ask: true, Notify: true, Off: true}
	validMatchers  = map[MatchType]bool{MatchCommand: true, MatchRegex: true, MatchGlob: true, MatchContains: true}
	separators     = map[string]bool{";": true, "&&": true, "||": true, "|": true, "&": true}
	assignRE       = regexp.MustCompile("^[A-Za-z_][A-Za-z0-9_]*=")
	variableRE     = regexp.MustCompile("\\$(?:[A-Za-z_{])")
	blockDeviceRE  = regexp.MustCompile("/dev/(?:sd[a-z]\\d*|nvme\\d+n\\d+(?:p\\d+)?|vd[a-z]\\d*)")
	redirectRE     = regexp.MustCompile("(?:^|[\\s;&|])(?:\\d?>|>>?)\\s*/dev/(?:sd[a-z]|nvme\\d|vd[a-z])")
	forkRE         = regexp.MustCompile("([A-Za-z_:][A-Za-z0-9_:.-]*)\\(\\)\\{([^}]*)\\};?([A-Za-z_:][A-Za-z0-9_:.-]*)")
	forkFunctionRE = regexp.MustCompile("function([A-Za-z_][A-Za-z0-9_.-]*)\\{([^}]*)\\};?([A-Za-z_][A-Za-z0-9_.-]*)")
	rootDirs       = map[string]bool{"/": true, "/bin": true, "/boot": true, "/dev": true, "/etc": true, "/home": true, "/lib": true, "/lib64": true, "/opt": true, "/proc": true, "/root": true, "/sbin": true, "/sys": true, "/tmp": true, "/usr": true, "/var": true}
	shells         = map[string]bool{"sh": true, "bash": true, "zsh": true, "dash": true, "fish": true}
	wrappers       = map[string]bool{"sudo": true, "doas": true, "command": true, "builtin": true, "time": true, "nohup": true, "nice": true}
)

// Parse validates a YAML policy and compiles regex patterns. An omitted default
// action resolves to ask, matching the Python rules-file contract.
func Parse(data []byte) (*Policy, error) {
	var raw any
	if err := yaml.Unmarshal(data, &raw); err != nil {
		return nil, fmt.Errorf("decode YO rules YAML: %w", err)
	}
	root, ok := raw.(map[string]any)
	if !ok {
		return nil, errors.New("rules file must contain a YAML mapping")
	}
	policy := &Policy{Default: Ask}
	if value, exists := root["default"]; exists {
		text, ok := value.(string)
		if !ok || !validActions[Action(text)] {
			return nil, errors.New("default must be approve, decline, block, ask, notify, or off")
		}
		policy.Default = Action(text)
	}
	rawRules, exists := root["rules"]
	if !exists || rawRules == nil {
		return policy, nil
	}
	rules, ok := rawRules.([]any)
	if !ok {
		return nil, errors.New("rules must be a list")
	}
	for index, value := range rules {
		fields, ok := value.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("rules[%d] must be a mapping", index)
		}
		name := fmt.Sprintf("rule %d", index+1)
		if value, exists := fields["name"]; exists {
			name, ok = value.(string)
			if !ok || strings.TrimSpace(name) == "" {
				return nil, fmt.Errorf("rules[%d].name must be a non-empty string", index)
			}
			name = strings.TrimSpace(name)
		}
		typeText, ok := fields["type"].(string)
		if !ok || !validMatchers[MatchType(typeText)] {
			return nil, fmt.Errorf("rules[%d].type must be command, regex, glob, or contains", index)
		}
		actionText, ok := fields["action"].(string)
		if !ok || !validActions[Action(actionText)] {
			return nil, fmt.Errorf("rules[%d].action must be approve, decline, block, ask, notify, or off", index)
		}
		risk := ""
		if value, exists := fields["risk"]; exists && value != nil {
			risk, ok = value.(string)
			if !ok {
				return nil, fmt.Errorf("rules[%d].risk must be a string", index)
			}
			risk = strings.TrimSpace(risk)
		}
		patterns, err := parsePatterns(fields["match"], index)
		if err != nil {
			return nil, err
		}
		rule := Rule{Name: name, Type: MatchType(typeText), Patterns: patterns, Action: Action(actionText), Risk: risk}
		if rule.Type == MatchRegex {
			for _, pattern := range patterns {
				compiled, err := regexp.Compile(pattern)
				if err != nil {
					return nil, fmt.Errorf("rules[%d].match regex error for %q: %w", index, pattern, err)
				}
				rule.compiled = append(rule.compiled, compiled)
			}
		}
		policy.Rules = append(policy.Rules, rule)
	}
	return policy, nil
}

func parsePatterns(value any, index int) ([]string, error) {
	var items []any
	switch typed := value.(type) {
	case string:
		items = []any{typed}
	case []any:
		items = typed
	default:
		return nil, fmt.Errorf("rules[%d].match must be a string or list of strings", index)
	}
	patterns := make([]string, 0, len(items))
	for _, item := range items {
		pattern, ok := item.(string)
		if !ok || pattern == "" {
			return nil, fmt.Errorf("rules[%d].match must contain non-empty strings", index)
		}
		patterns = append(patterns, pattern)
	}
	if len(patterns) == 0 {
		return nil, fmt.Errorf("rules[%d].match must contain non-empty strings", index)
	}
	return patterns, nil
}

// Load reads and validates a policy file. Callers should fail closed on errors.
func Load(path string) (*Policy, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read YO rules %q: %w", path, err)
	}
	policy, err := Parse(data)
	if err != nil {
		return nil, fmt.Errorf("load YO rules %q: %w", path, err)
	}
	return policy, nil
}

// Evaluate checks the hard floor before configured rules. A nil policy or
// ambiguous shell command returns ask. dangerouslyYolo bypasses soft rules only.
func Evaluate(policy *Policy, command string, dangerouslyYolo bool) Decision {
	if result := hardFloor(command); result.Action != "" {
		return result
	}
	if strings.TrimSpace(command) == "" {
		return Decision{Action: Ask, RuleName: "command unavailable", Source: "error", ReasonCode: "command_missing", Error: "command text is unavailable"}
	}
	if policy == nil {
		return Decision{Action: Ask, RuleName: "ruleset error", Source: "error", ReasonCode: "rules_file_unavailable", Error: "YO ruleset is unavailable"}
	}
	calls, parseErr := invocations(command, 0)
	if parseErr != nil && containsCommandRule(policy) {
		return Decision{Action: Ask, RuleName: "command parse error", Source: "error", ReasonCode: "command_parse_failed", Error: parseErr.Error()}
	}
	if dangerouslyYolo {
		return Decision{Action: Approve, RuleName: "dangerously-yolo bypass", Risk: "unknown", Source: "bypass"}
	}
	for _, rule := range policy.Rules {
		matched, err := matches(rule, command, calls)
		if err != nil {
			return Decision{Action: Ask, RuleName: "ruleset error", Risk: "unknown", Source: "error", ReasonCode: "rules_evaluator_failed", Error: err.Error()}
		}
		if matched {
			risk := normalizeRisk(rule.Risk)
			return Decision{Action: rule.Action, RuleName: rule.Name, Risk: risk, Source: "file"}
		}
	}
	return Decision{Action: policy.Default, RuleName: "default", Risk: "unknown", Source: "file"}
}

func normalizeRisk(value string) string {
	risk := strings.ToLower(strings.TrimSpace(value))
	if risk == "" {
		return "unknown"
	}
	switch risk {
	case "device", "format", "wipe":
		return "delete"
	default:
		return risk
	}
}

// LoadAndEvaluate preserves hard-floor blocks when a soft policy cannot load.
func LoadAndEvaluate(path, command string, dangerouslyYolo bool) Decision {
	if result := hardFloor(command); result.Action != "" {
		return result
	}
	policy, err := Load(path)
	if err != nil {
		return Decision{Action: Ask, RuleName: "ruleset error", Risk: "unknown", Source: "error", ReasonCode: "rules_file_unavailable", Error: err.Error()}
	}
	return Evaluate(policy, command, dangerouslyYolo)
}

func containsCommandRule(policy *Policy) bool {
	for _, rule := range policy.Rules {
		if rule.Type == MatchCommand {
			return true
		}
	}
	return false
}

func matches(rule Rule, command string, calls []invocation) (bool, error) {
	switch rule.Type {
	case MatchCommand:
		for _, call := range calls {
			for _, pattern := range rule.Patterns {
				name := commandName(pattern)
				if name != "" && (call.name == name || strings.HasPrefix(call.name, name+".")) {
					return true, nil
				}
			}
		}
	case MatchRegex:
		for _, pattern := range rule.compiled {
			if pattern.MatchString(command) {
				return true, nil
			}
		}
	case MatchGlob:
		for _, pattern := range rule.Patterns {
			ok, err := fnmatch(pattern, command)
			if err != nil {
				return false, err
			}
			if ok {
				return true, nil
			}
		}
	case MatchContains:
		for _, pattern := range rule.Patterns {
			if strings.Contains(command, pattern) {
				return true, nil
			}
		}
	}
	return false, nil
}

// fnmatch follows Python fnmatch's rule that * and ? match slash characters.
func fnmatch(pattern, value string) (bool, error) {
	var out strings.Builder
	out.WriteString("^")
	runes := []rune(pattern)
	for i := 0; i < len(runes); {
		switch runes[i] {
		case '*':
			out.WriteString(".*")
			i++
		case '?':
			out.WriteByte('.')
			i++
		case '[':
			end := -1
			for index := i + 1; index < len(runes); index++ {
				if runes[index] == ']' {
					end = index
					break
				}
			}
			if end < 0 {
				out.WriteString("\\[")
				i++
				continue
			}
			class := string(runes[i+1 : end])
			if class == "" {
				out.WriteString("\\[\\]")
				i = end + 1
				continue
			}
			negated := class[0] == '!'
			if negated {
				class = class[1:]
			}
			class = strings.ReplaceAll(class, "\\", "\\\\")
			if negated {
				out.WriteString("[^" + class + "]")
			} else {
				out.WriteString("[" + class + "]")
			}
			i = end + 1
		default:
			out.WriteString(regexp.QuoteMeta(string(runes[i])))
			i++
		}
	}
	out.WriteByte('$')
	compiled, err := regexp.Compile(out.String())
	if err != nil {
		return false, fmt.Errorf("invalid glob pattern %q: %w", pattern, err)
	}
	return compiled.MatchString(value), nil
}

func hardFloor(command string) Decision {
	if strings.TrimSpace(command) == "" {
		return Decision{}
	}
	compact := strings.Join(strings.Fields(command), "")
	if forkBombMatch(forkRE, compact) || forkBombMatch(forkFunctionRE, compact) {
		return Decision{Action: Block, RuleName: "built-in hard floor: fork bomb", Risk: "process", Source: "hard-floor"}
	}
	if redirectRE.MatchString(command) {
		return Decision{Action: Block, RuleName: "built-in hard floor: block-device redirect", Risk: "device", Source: "hard-floor"}
	}
	calls, err := invocations(command, 0)
	if err != nil {
		return Decision{Action: Ask, RuleName: "command parse error", Risk: "unknown", Source: "error", ReasonCode: "command_parse_failed", Error: err.Error()}
	}
	for _, call := range calls {
		if call.name == "mkfs" || strings.HasPrefix(call.name, "mkfs.") {
			return Decision{Action: Block, RuleName: "built-in hard floor: mkfs", Risk: "format", Source: "hard-floor"}
		}
		if call.name == "dd" {
			for _, arg := range call.args {
				parts := strings.SplitN(arg, "=", 2)
				if blockDeviceRE.MatchString(arg) || len(parts) == 2 && blockDeviceRE.MatchString(parts[1]) {
					return Decision{Action: Block, RuleName: "built-in hard floor: dd block device", Risk: "device", Source: "hard-floor"}
				}
			}
		}
		if call.name == "rm" && rmTargetsRoot(call.args) {
			return Decision{Action: Block, RuleName: "built-in hard floor: rm root", Risk: "delete", Source: "hard-floor"}
		}
		if call.name == "rm" && hasBraceExpansion(call.args) {
			return Decision{Action: Ask, RuleName: "ambiguous rm brace expansion", Risk: "delete", Source: "error", ReasonCode: "command_parse_failed", Error: "rm brace expansion cannot be safely evaluated by the YO policy parser"}
		}
		if call.name == "rmdir" {
			for _, arg := range call.args {
				if arg == "/" {
					return Decision{Action: Block, RuleName: "built-in hard floor: rmdir root", Risk: "delete", Source: "hard-floor"}
				}
			}
		}
	}
	return Decision{}
}

func hasBraceExpansion(args []string) bool {
	// shellTokens separates whitespace before this safety check, while Bash
	// still accepts whitespace inside a brace expansion. Join the rm arguments
	// and remove whitespace so a split `/{, etc}` cannot reach an approve rule.
	compact := strings.Join(strings.Fields(strings.Join(args, "")), "")
	return strings.Contains(compact, "{") && strings.Contains(compact, "}") && strings.Contains(compact, ",")
}

func forkBombMatch(pattern *regexp.Regexp, command string) bool {
	for _, match := range pattern.FindAllStringSubmatch(command, -1) {
		if len(match) == 4 && match[1] == match[3] && strings.Contains(match[2], match[1]+"|"+match[1]+"&") {
			return true
		}
	}
	return false
}

func rmTargetsRoot(args []string) bool {
	var flags, targets []string
	afterOptions := false
	for _, arg := range args {
		if !afterOptions && arg == "--" {
			afterOptions = true
			continue
		}
		if !afterOptions && strings.HasPrefix(arg, "-") {
			flags = append(flags, arg)
			continue
		}
		targets = append(targets, arg)
	}
	short := ""
	long := map[string]bool{}
	for _, flag := range flags {
		if strings.HasPrefix(flag, "--") {
			long[flag] = true
		} else if strings.HasPrefix(flag, "-") {
			short += flag[1:]
		}
	}
	if !(strings.ContainsAny(strings.ToLower(short), "rf") || strings.Contains(short, "R") || long["--recursive"] || long["--force"]) {
		return false
	}
	for _, target := range targets {
		if rootLike(target) {
			return true
		}
	}
	return false
}

func rootLike(target string) bool {
	text := strings.Trim(strings.TrimSpace(target), "\"'")
	if text == "" {
		return false
	}
	if text == "~" || text == "~/" || text == "$HOME" || text == "$"+"{HOME}" {
		return true
	}
	if regexp.MustCompile("^/+\\*+$").MatchString(text) || regexp.MustCompile("^/+(?:\\./?)*$").MatchString(text) {
		return true
	}
	text = filepath.Clean(text)
	normalized := strings.TrimRight(regexp.MustCompile("/+").ReplaceAllString(text, "/"), "/")
	if strings.HasPrefix(normalized, "/*") || strings.HasPrefix(normalized, "/?") || strings.HasPrefix(normalized, "/[") {
		return true
	}
	if rootDirs[normalized] {
		return true
	}
	for root := range rootDirs {
		if normalized == root+"/*" {
			return true
		}
	}
	return false
}

func invocations(command string, depth int) ([]invocation, error) {
	if depth > 4 {
		return nil, errors.New("shell command nesting exceeds the safe parser limit")
	}
	tokens, err := shellTokens(command)
	if err != nil {
		return nil, err
	}
	var calls []invocation
	var segment []string
	consume := func() error {
		if len(segment) == 0 {
			return nil
		}
		if ambiguousWrapperTokens(segment) {
			return errors.New("command wrapper options are unsupported by the safety parser")
		}
		call, ok := invocationFrom(segment)
		segment = nil
		if !ok {
			return nil
		}
		calls = append(calls, call)
		if strings.ContainsAny(call.name, "*?[") {
			return errors.New("shell command name contains an unresolved glob")
		}
		for _, payload := range nestedPayloads(call) {
			nested, err := invocations(payload, depth+1)
			if err != nil {
				return err
			}
			calls = append(calls, nested...)
		}
		return nil
	}
	for _, token := range tokens {
		if separators[token] {
			if err := consume(); err != nil {
				return nil, err
			}
			continue
		}
		segment = append(segment, token)
	}
	if err := consume(); err != nil {
		return nil, err
	}
	for _, payload := range substitutionPayloads(command) {
		nested, err := invocations(payload, depth+1)
		if err != nil {
			return nil, err
		}
		calls = append(calls, nested...)
	}
	if malformedSubstitution(command) {
		return nil, errors.New("shell command has an incomplete command substitution")
	}
	if hasUnsafeVariableExpansion(command) {
		return nil, errors.New("shell command contains variable expansion the policy parser cannot safely resolve")
	}
	return calls, nil
}

func hasUnsafeVariableExpansion(command string) bool {
	for _, token := range strings.Fields(command) {
		unquoted := strings.Trim(token, "\"'")
		if unquoted == "$HOME" || unquoted == "$"+"{HOME}" {
			continue
		}
		if variableRE.MatchString(token) {
			return true
		}
	}
	return false
}

func nestedPayloads(call invocation) []string {
	command, args := call.name, call.args
	var payloads []string
	if shells[command] {
		for index, arg := range args {
			if (arg == "-c" || arg == "-lc" || strings.HasPrefix(arg, "-") && strings.Contains(arg[1:], "c")) && index+1 < len(args) {
				payloads = append(payloads, args[index+1])
				break
			}
		}
	}
	if (command == "docker" || command == "podman") && len(args) > 0 && (args[0] == "exec" || args[0] == "run") {
		index := 1
		entrypoint := ""
		for index < len(args) && strings.HasPrefix(args[index], "-") {
			option := args[index]
			if strings.HasPrefix(option, "--entrypoint=") {
				entrypoint = strings.TrimPrefix(option, "--entrypoint=")
			}
			index++
			if !strings.Contains(option, "=") && dockerOptionTakesValue(option) && index < len(args) {
				if option == "--entrypoint" {
					entrypoint = args[index]
				}
				index++
			}
		}
		if index < len(args) {
			index++
		}
		if index < len(args) {
			if args[0] == "run" && entrypoint != "" {
				payloads = append(payloads, shellJoin(append([]string{entrypoint}, args[index:]...)))
			} else {
				payloads = append(payloads, shellJoin(args[index:]))
			}
		}
	}
	if command == "kubectl" {
		if index := kubectlExecIndex(args); index >= 0 {
			index++
			for index < len(args) {
				next := nextKubectlArg(args, index)
				if next == index {
					break
				}
				index = next
			}
			if index < len(args) && args[index] != "--" {
				index++
			}
			for argIndex, arg := range args {
				if arg == "--" && argIndex > 0 {
					index = argIndex + 1
					break
				}
			}
			if index < len(args) {
				payloads = append(payloads, shellJoin(args[index:]))
			}
		}
	}
	if command == "ssh" {
		index := 0
		for index < len(args) {
			arg := args[index]
			if arg == "--" {
				index++
				break
			}
			if strings.HasPrefix(arg, "-") {
				if (arg == "-i" || arg == "-p" || arg == "-l" || arg == "-o") && index+1 < len(args) {
					index += 2
				} else {
					index++
				}
				continue
			}
			index++
			break
		}
		if index < len(args) {
			payloads = append(payloads, shellJoin(args[index:]))
		}
	}
	if command == "xargs" {
		index := 0
		for index < len(args) && strings.HasPrefix(args[index], "-") {
			index++
		}
		if index < len(args) {
			payloads = append(payloads, shellJoin(args[index:]))
		}
	}
	if command == "find" {
		for index, arg := range args {
			if arg == "-exec" && index+1 < len(args) {
				end := len(args)
				for offset, candidate := range args[index+1:] {
					if candidate == ";" {
						end = index + 1 + offset
						break
					}
				}
				if index+1 < end {
					payloads = append(payloads, shellJoin(args[index+1:end]))
				}
				break
			}
		}
	}
	return payloads
}

func ambiguousWrapperTokens(tokens []string) bool {
	for index := 0; index < len(tokens); {
		token := tokens[index]
		name := commandName(token)
		if assignRE.MatchString(token) || token == "$" || token == "(" || token == ")" {
			index++
			continue
		}
		if wrappers[name] {
			index++
			for index < len(tokens) && strings.HasPrefix(tokens[index], "-") {
				option := tokens[index]
				index++
				if wrapperOptionTakesValue(name, option) && index < len(tokens) {
					index++
				}
			}
			continue
		}
		switch name {
		case "xargs":
			if index+1 < len(tokens) && strings.HasPrefix(tokens[index+1], "-") {
				return true
			}
			return false
		case "env":
			index++
			for index < len(tokens) {
				option := tokens[index]
				if assignRE.MatchString(option) {
					index++
					continue
				}
				if option == "-S" || strings.HasPrefix(option, "-S") || option == "--split-string" || strings.HasPrefix(option, "--split-string=") {
					return true
				}
				if option == "-u" || option == "--unset" || option == "-C" || option == "--chdir" {
					index += 2
					continue
				}
				if option == "--" {
					index++
					break
				}
				if !strings.HasPrefix(option, "-") {
					break
				}
				index++
			}
			continue
		case "docker", "podman":
			if index+1 >= len(tokens) || tokens[index+1] != "run" {
				return false
			}
			entrypoint := ""
			for optionIndex := index + 2; optionIndex < len(tokens) && strings.HasPrefix(tokens[optionIndex], "-"); optionIndex++ {
				option := tokens[optionIndex]
				if strings.HasPrefix(option, "--entrypoint=") {
					entrypoint = strings.TrimPrefix(option, "--entrypoint=")
				} else if option == "--entrypoint" && optionIndex+1 < len(tokens) {
					entrypoint = tokens[optionIndex+1]
					optionIndex++
				}
			}
			return entrypoint != "" && !shells[filepath.Base(entrypoint)] || dockerRunMountsRoot(tokens[index+2:])
		default:
			return false
		}
	}
	return false
}

func dockerRunMountsRoot(options []string) bool {
	for index := 0; index < len(options) && strings.HasPrefix(options[index], "-"); {
		option := options[index]
		value := ""
		if strings.HasPrefix(option, "--mount=") {
			value = strings.TrimPrefix(option, "--mount=")
		} else if strings.HasPrefix(option, "--volume=") {
			value = strings.TrimPrefix(option, "--volume=")
		} else if strings.HasPrefix(option, "-v=") {
			value = strings.TrimPrefix(option, "-v=")
		} else {
			index++
			if dockerOptionTakesValue(option) && index < len(options) {
				value = options[index]
				index++
			}
		}
		if option == "-v" || option == "--volume" || strings.HasPrefix(option, "--volume=") || strings.HasPrefix(option, "-v=") {
			if filepath.Clean(strings.SplitN(value, ":", 2)[0]) == "/" {
				return true
			}
		}
		if option == "--mount" || strings.HasPrefix(option, "--mount=") {
			for _, field := range strings.Split(value, ",") {
				key, source, found := strings.Cut(field, "=")
				if found && (key == "source" || key == "src") && filepath.Clean(source) == "/" {
					return true
				}
			}
		}
	}
	return false
}

func dockerOptionTakesValue(option string) bool {
	switch option {
	case "-u", "--user", "-e", "--env", "-w", "--workdir", "--name", "--network", "--entrypoint", "--env-file", "-v", "--volume", "-p", "--publish", "--mount":
		return true
	default:
		return false
	}
}

func kubectlExecIndex(args []string) int {
	for index := 0; index < len(args); {
		if args[index] == "exec" {
			return index
		}
		next := nextKubectlArg(args, index)
		if next == index {
			return -1
		}
		index = next
	}
	return -1
}

func nextKubectlArg(args []string, index int) int {
	token := args[index]
	if token == "--" || !strings.HasPrefix(token, "-") {
		return index
	}
	if strings.Contains(token, "=") {
		return index + 1
	}
	switch token {
	case "-c", "-n", "--as", "--as-group", "--as-uid", "--cache-dir", "--cluster", "--container", "--context", "--field-manager", "--kubeconfig", "--namespace", "--pod-running-timeout", "--profile", "--profile-output", "--request-timeout", "--server", "--token", "--user":
		if index+1 < len(args) {
			return index + 2
		}
	}
	return index + 1
}

func shellJoin(tokens []string) string {
	var parts []string
	safe := regexp.MustCompile("^[A-Za-z0-9_@%+=:,./-]+$")
	for _, token := range tokens {
		if token == "$" || token == "(" || token == ")" {
			continue
		}
		if token == "" {
			parts = append(parts, "''")
		} else if safe.MatchString(token) {
			parts = append(parts, token)
		} else {
			parts = append(parts, "'"+strings.ReplaceAll(token, "'", "'\"'\"'")+"'")
		}
	}
	return strings.Join(parts, " ")
}

func invocationFrom(tokens []string) (invocation, bool) {
	i := 0
	for i < len(tokens) {
		token := tokens[i]
		name := commandName(token)
		if token == "$" || token == "(" || token == ")" || assignRE.MatchString(token) {
			i++
			continue
		}
		if wrappers[name] {
			i++
			for i < len(tokens) && strings.HasPrefix(tokens[i], "-") {
				option := tokens[i]
				i++
				if wrapperOptionTakesValue(name, option) {
					if i >= len(tokens) {
						return invocation{}, false
					}
					i++
				}
			}
			continue
		}
		if name == "env" {
			i++
			for i < len(tokens) {
				if assignRE.MatchString(tokens[i]) {
					i++
					continue
				}
				if !strings.HasPrefix(tokens[i], "-") {
					break
				}
				option := tokens[i]
				i++
				if (option == "-u" || option == "--unset" || option == "-C" || option == "--chdir") && i < len(tokens) {
					i++
				}
			}
			continue
		}
		break
	}
	if i >= len(tokens) {
		return invocation{}, false
	}
	name := commandName(tokens[i])
	if name == "" {
		return invocation{}, false
	}
	return invocation{name: name, args: append([]string(nil), tokens[i+1:]...)}, true
}

func wrapperOptionTakesValue(wrapper, option string) bool {
	if strings.Contains(option, "=") {
		return false
	}
	switch wrapper {
	case "sudo", "doas":
		return option == "-u" || option == "-g" || option == "-h" || option == "-p" || option == "-C" || option == "-T" ||
			option == "--user" || option == "--group" || option == "--host" || option == "--prompt" || option == "--close-from" || option == "--command-timeout"
	case "nice":
		return option == "-n"
	case "time":
		return option == "-f" || option == "-o"
	default:
		return false
	}
}

func commandName(value string) string {
	value = strings.Trim(value, string(rune(96))+"'\"()")
	value = strings.TrimPrefix(value, "\\")
	if value == "" {
		return ""
	}
	return filepath.Base(value)
}

func shellTokens(input string) ([]string, error) {
	var tokens []string
	var current strings.Builder
	var quote rune
	escaped, inToken := false, false
	runes := []rune(input)
	flush := func() {
		if inToken {
			tokens = append(tokens, current.String())
			current.Reset()
			inToken = false
		}
	}
	for index := 0; index < len(runes); index++ {
		char := runes[index]
		if escaped {
			current.WriteRune(char)
			inToken = true
			escaped = false
			continue
		}
		if quote == '\'' {
			if char == '\'' {
				quote = 0
			} else {
				current.WriteRune(char)
			}
			continue
		}
		if quote == '"' {
			if char == '"' {
				quote = 0
			} else if char == '\\' && index+1 < len(runes) && strings.ContainsRune("\\\"$", runes[index+1]) {
				escaped = true
			} else {
				current.WriteRune(char)
			}
			inToken = true
			continue
		}
		switch char {
		case '\'', '"':
			quote = char
			inToken = true
		case '\\':
			escaped = true
			inToken = true
		case ' ', '\t', '\r', '\n':
			flush()
		case '(', ')', ';', '<', '>', '|', '&':
			flush()
			punctuation := string(char)
			for index+1 < len(runes) && runes[index+1] == char && strings.ContainsRune("|&<>;", char) {
				punctuation += string(char)
				index++
			}
			tokens = append(tokens, punctuation)
		default:
			current.WriteRune(char)
			inToken = true
		}
	}
	if escaped || quote != 0 {
		return nil, errors.New("shell command has an incomplete escape or quote")
	}
	flush()
	depth := 0
	for _, token := range tokens {
		if token == "(" {
			depth++
		} else if token == ")" {
			depth--
			if depth < 0 {
				return nil, errors.New("shell command has unmatched parentheses")
			}
		}
	}
	if depth != 0 {
		return nil, errors.New("shell command has unmatched parentheses")
	}
	return tokens, nil
}

func substitutionPayloads(command string) []string {
	var payloads []string
	for index := 0; index+1 < len(command); index++ {
		if command[index:index+2] == "$(" {
			start, depth, quote := index+2, 1, byte(0)
			escaped := false
			for end := start; end < len(command); end++ {
				char := command[end]
				if escaped {
					escaped = false
					continue
				}
				if char == '\\' && quote != '\'' {
					escaped = true
					continue
				}
				if quote != 0 {
					if char == quote {
						quote = 0
					}
					continue
				}
				if char == '\'' || char == '"' {
					quote = char
					continue
				}
				if char == '$' && end+1 < len(command) && command[end+1] == '(' {
					depth++
					end++
					continue
				}
				if char == '(' {
					depth++
				} else if char == ')' {
					depth--
					if depth == 0 {
						payloads = append(payloads, command[start:end])
						index = end
						break
					}
				}
			}
			if depth != 0 {
				return append(payloads, "")
			}
		}
	}
	for index := 0; index < len(command); index++ {
		if command[index] == 96 {
			start := index + 1
			escaped := false
			found := false
			for end := start; end < len(command); end++ {
				if escaped {
					escaped = false
					continue
				}
				if command[end] == '\\' {
					escaped = true
				} else if command[end] == 96 {
					payloads = append(payloads, command[start:end])
					index = end
					found = true
					break
				}
			}
			if !found {
				return append(payloads, "")
			}
		}
	}
	return payloads
}

func malformedSubstitution(command string) bool {
	if strings.Contains(command, "$(") || strings.ContainsRune(command, 96) {
		for _, payload := range substitutionPayloads(command) {
			if payload == "" {
				return true
			}
		}
	}
	return false
}

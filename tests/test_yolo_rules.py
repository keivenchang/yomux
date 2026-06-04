import os
from pathlib import Path

os.environ.setdefault("YOLOMUX_CONFIG_DIR", "/tmp/yolomux-test-config")
os.environ.setdefault("YOLOMUX_STATE_DIR", "/tmp/yolomux-test-state")

import auto_approve_tmux
from yolomux_lib import yolo_rules


def ruleset(data):
    return yolo_rules.validate_rules(data, path=Path("/tmp/yolo-rules-test.yaml"), source="test")


def action_for(cmd, rules):
    return yolo_rules.evaluate_ruleset(cmd, rules)["action"]


def test_command_match_is_argv_aware():
    rules = ruleset({
        "default": "approve",
        "rules": [
            {"name": "delete commands", "type": "command", "match": ["rm"], "action": "block", "risk": "delete"},
        ],
    })

    assert action_for('echo "rm"', rules) == "approve"
    assert action_for("rm file.txt", rules) == "block"
    assert action_for("ls /tmp && rm -rf build", rules) == "block"
    assert action_for("bash -c 'rm -rf build'", rules) == "block"
    assert action_for('docker exec abc bash -c "rm -rf /workspace"', rules) == "block"
    assert action_for("kubectl exec pod -- rm -rf /workspace", rules) == "block"
    assert action_for("kubectl exec -it -n test pod -- rm -rf /workspace", rules) == "block"
    assert action_for("kubectl --namespace test exec pod -- rm -rf /workspace", rules) == "block"
    assert action_for("kubectl exec --container app pod rm -rf /workspace", rules) == "block"


def test_malformed_shell_quote_does_not_abort_rule_evaluation():
    rules = ruleset({
        "default": "approve",
        "rules": [
            {"name": "delete commands", "type": "command", "match": ["rm"], "action": "block", "risk": "delete"},
        ],
    })

    assert action_for('python3 -c "from yolomux_lib.settings import settings_payload;', rules) == "approve"
    assert action_for('rm -rf "/tmp/unclosed', rules) == "block"


def test_match_types_and_first_match_precedence():
    rules = ruleset({
        "default": "ask",
        "rules": [
            {"name": "first wins", "type": "contains", "match": "rm file.txt", "action": "approve", "risk": "test"},
            {"name": "later block", "type": "command", "match": ["rm"], "action": "block", "risk": "delete"},
            {"name": "glob build", "type": "glob", "match": "make *", "action": "approve", "risk": "build"},
            {"name": "regex git read", "type": "regex", "match": r"^git (status|log)\b", "action": "approve", "risk": "read"},
        ],
    })

    first = yolo_rules.evaluate_ruleset("rm file.txt", rules)
    assert first["action"] == "approve"
    assert first["rule_name"] == "first wins"
    assert action_for("make test", rules) == "approve"
    assert action_for("git status --short", rules) == "approve"
    assert action_for("python3 script.py", rules) == "ask"


def test_hard_floor_blocks_unrelaxable_cases():
    assert yolo_rules.hard_floor_decision("rm -rf /")["action"] == "block"
    assert yolo_rules.hard_floor_decision("rm -rf /*")["action"] == "block"
    assert yolo_rules.hard_floor_decision("rm -rf //")["action"] == "block"
    assert yolo_rules.hard_floor_decision("rm -rf ~/")["action"] == "block"
    assert yolo_rules.hard_floor_decision("rm -rf /home")["action"] == "block"
    assert yolo_rules.hard_floor_decision("rm --recursive --force /usr")["action"] == "block"
    assert yolo_rules.hard_floor_decision("kubectl exec -it -n test pod -- rm -rf /")["action"] == "block"
    assert yolo_rules.hard_floor_decision("dd if=/dev/zero of=/dev/sda")["action"] == "block"
    assert yolo_rules.hard_floor_decision("mkfs.ext4 /dev/sda1")["action"] == "block"
    assert yolo_rules.hard_floor_decision("echo foo > /dev/sda")["action"] == "block"
    assert yolo_rules.hard_floor_decision(":(){ :|:& };:")["action"] == "block"
    assert yolo_rules.hard_floor_decision("bomb(){ bomb | bomb & }; bomb")["action"] == "block"

    permissive = ruleset({
        "default": "approve",
        "rules": [
            {"name": "approve everything", "type": "regex", "match": ".*", "action": "approve", "risk": "test"},
        ],
    })
    assert yolo_rules.evaluate_ruleset("mkfs.ext4 /dev/sda1", permissive)["action"] == "approve"
    assert yolo_rules.hard_floor_decision("mkfs.ext4 /dev/sda1")["action"] == "block"


def test_dangerously_yolo_still_hits_the_hard_floor():
    # DOIT.6 #62: --dangerously-yolo opts out of the soft ruleset / ask-default, NOT the catastrophic
    # floor — rm -rf /, mkfs, dd to a block device, and fork bombs are blocked even under the flag.
    for cmd in ["rm -rf /", "mkfs.ext4 /dev/sda1", "dd if=/dev/zero of=/dev/sda", ":(){ :|:& };:"]:
        decision = yolo_rules.evaluate(cmd, "bash", dangerously_yolo=True)
        assert decision["action"] == "block", cmd
        assert decision["source"] == "hard-floor", cmd


def test_schema_validation_reports_errors():
    bad = {
        "default": "approve",
        "rules": [
            {"name": "bad regex", "type": "regex", "match": "(", "action": "block"},
        ],
    }

    try:
        yolo_rules.validate_rules(bad)
    except ValueError as exc:
        assert "regex error" in str(exc)
    else:
        raise AssertionError("invalid regex should fail validation")


def test_builtin_default_reproduces_current_block_and_allow_sets(monkeypatch):
    rules = yolo_rules.validate_rules(yolo_rules.default_rule_data("approve"), source="built-in")
    # DOIT.6 #68: is_dangerous now reads the ACTIVE cached ruleset; pin it to the built-in default here
    # so this test still validates the built-in block/allow classification deterministically.
    monkeypatch.setattr(yolo_rules, "cached_rules", lambda: (rules, ""))
    dangerous_cmds = [
        "rm -rf /tmp/foo",
        "rm file.txt",
        "sudo rm -rf /",
        "rmdir /some/dir",
        "dd if=/dev/zero of=/dev/sda",
        "mkfs.ext4 /dev/sda1",
        "shred /dev/sda",
        "fdisk /dev/sda",
        "parted /dev/sda print",
        "wipefs -a /dev/sda",
        "format C:",
        "echo foo > /dev/sda",
        "bash -c 'rm -rf /tmp/foo'",
        'docker exec abc bash -c "rm -rf /workspace"',
        "docker exec abc rm -rf /workspace",
        "ssh user@host 'rm -rf /tmp/stuff'",
        "echo $(rm -rf /tmp)",
        "find /tmp -name '*.log' -delete",
        'find /tmp -name "*.log" | xargs rm -f',
        'find /tmp -name "*.tmp" -exec rm {} \\;',
        "kubectl exec pod -- rm -rf /workspace",
        "docker run ubuntu rm -rf /",
    ]
    safe_cmds = [
        "mv file1 file2",
        "cp file1 file2",
        "chmod +x script.sh",
        "ls -la",
        "cat /etc/passwd",
        "find . -name '*.py'",
        "curl https://example.com",
        'docker exec foo bash -c "ls"',
        "docker images repo --format '{{.Tag}} {{.Size}}'",
        "docker pull repo/image:tag",
        "docker rmi repo/image:old-tag",
        'echo "rm -rf /tmp/foo"',
        'grep "dd if=/dev/zero of=/dev/sda" notes.txt',
        'printf "find /tmp -delete\\n"',
        "git push origin main",
        "python3 -m pytest tests/",
        "cargo fmt",
        "",
        "   ",
    ]

    for cmd in dangerous_cmds:
        assert auto_approve_tmux.is_dangerous(cmd) is True, cmd
        assert action_for(cmd, rules) == "block", cmd
    for cmd in safe_cmds:
        assert auto_approve_tmux.is_dangerous(cmd) is False, cmd
        assert action_for(cmd, rules) == "approve", cmd


def test_rule_file_default_is_authoritative(tmp_path):
    path = tmp_path / "yolo-rules.yaml"
    path.write_text(
        """
default: notify
rules:
  - name: allow git status
    type: regex
    match: "^git status"
    action: approve
""",
        encoding="utf-8",
    )

    rules = yolo_rules.load_rules_file(path)

    assert rules.default_action == "notify"
    assert action_for("python3 script.py", rules) == "notify"


def test_missing_rule_default_falls_back_to_ask(tmp_path):
    path = tmp_path / "yolo-rules.yaml"
    path.write_text(
        """
rules:
  - name: allow git status
    type: regex
    match: "^git status"
    action: approve
""",
        encoding="utf-8",
    )

    rules = yolo_rules.load_rules_file(path)

    assert rules.default_action == "ask"


def test_non_bash_prompts_use_rules_and_hard_floor(monkeypatch):
    rules = ruleset({
        "default": "ask",
        "rules": [
            {"name": "tool delete text", "type": "contains", "match": "delete everything", "action": "block", "risk": "delete"},
        ],
    })
    monkeypatch.setattr(yolo_rules, "cached_rules", lambda: (rules, None))

    decision = yolo_rules.evaluate("delete everything?", prompt_type="tool")

    assert decision["action"] == "block"
    assert decision["rule_name"] == "tool delete text"
    assert yolo_rules.evaluate("rm -rf /", prompt_type="file")["source"] == "hard-floor"


def test_dry_run_downgrades_active_rule_actions(monkeypatch):
    rules = ruleset({
        "default": "ask",
        "rules": [
            {"name": "allow git status", "type": "regex", "match": "^git status", "action": "approve", "risk": "read"},
        ],
    })
    monkeypatch.setattr(yolo_rules, "cached_rules", lambda: (rules, ""))
    monkeypatch.setattr(yolo_rules, "yolo_settings", lambda: {"dry_run": True, "rule_file_path": "/tmp/yolo-rules-test.yaml"})

    decision = yolo_rules.evaluate("git status --short", agent="codex", session="6")

    assert decision["action"] == "ask"
    assert decision["would_action"] == "approve"
    assert decision["dry_run"] is True
    assert decision["agent"] == "codex"
    assert decision["session"] == "6"


def test_is_dangerous_uses_active_ruleset_and_flags_non_approve(monkeypatch):
    # DOIT.6 #68: the UI danger badge reflects the USER's active ruleset (what the worker acts on) and
    # treats ANY non-approve outcome (block / decline / ask) as dangerous — only a clean approve is safe.
    rules = ruleset({
        "default": "ask",
        "rules": [
            {"name": "ok ls", "type": "command", "match": ["ls"], "action": "approve", "risk": "test"},
            {"name": "decline curl", "type": "command", "match": ["curl"], "action": "decline", "risk": "net"},
        ],
    })
    monkeypatch.setattr(yolo_rules, "cached_rules", lambda: (rules, ""))
    assert auto_approve_tmux.is_dangerous("ls -la") is False        # approve -> safe
    assert auto_approve_tmux.is_dangerous("curl http://example") is True   # decline -> dangerous
    assert auto_approve_tmux.is_dangerous("make build") is True     # default ask -> dangerous
    assert auto_approve_tmux.is_dangerous("rm -rf /") is True       # hard floor -> dangerous


def test_dangerously_yolo_keeps_the_hard_floor(monkeypatch):
    # DOIT.6 #62: --dangerously-yolo only opts out of the soft ruleset / ask-default — the catastrophic
    # hard floor ALWAYS applies. rm -rf / is blocked under the flag; a non-catastrophic command the
    # ruleset approves is still approved (so the flag's broad relaxation is preserved).
    rules = ruleset({
        "default": "approve",
        "rules": [
            {"name": "approve all", "type": "regex", "match": ".*", "action": "approve", "risk": "test"},
        ],
    })
    monkeypatch.setattr(yolo_rules, "cached_rules", lambda: (rules, ""))
    monkeypatch.setattr(yolo_rules, "yolo_settings", lambda: {"dry_run": False, "rule_file_path": "/tmp/yolo-rules-test.yaml"})

    for flag in (False, True):
        catastrophic = yolo_rules.evaluate("rm -rf /", dangerously_yolo=flag)
        assert catastrophic["action"] == "block", flag
        assert catastrophic["source"] == "hard-floor", flag

    safe = yolo_rules.evaluate("ls -la", dangerously_yolo=True)
    assert safe["action"] == "approve"
    assert safe["rule_name"] == "approve all"
    assert safe["source"] == "test"


def test_rule_file_text_validation_reports_yaml_errors():
    try:
        yolo_rules.validate_rule_file_text("default: approve\nrules:\n  - type: regex\n    match: '('\n    action: block\n")
    except ValueError as exc:
        assert "regex error" in str(exc)
    else:
        raise AssertionError("invalid YOLO rules should fail validation")

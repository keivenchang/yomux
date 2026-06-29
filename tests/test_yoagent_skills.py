from yolomux_lib.yoagent.skills import load_yoagent_skills
from yolomux_lib.yoagent.skills import list_user_skill_files
from yolomux_lib.yoagent.skills import parse_skill_text
from yolomux_lib.yoagent.skills import read_user_skill_file
from yolomux_lib.yoagent.skills import write_user_skill_file
from yolomux_lib.yoagent.skills import delete_user_skill_file


def test_builtin_yoagent_skills_load_with_context(tmp_path):
    payload = load_yoagent_skills(
        user_skills_dir=tmp_path / "missing-skills",
        user_context_dir=tmp_path / "missing-context",
        config_dir=tmp_path,
    )

    names = {item["name"] for item in payload["skills"]}
    assert payload["ok"] is True
    assert {"work-next", "notify-when-idle", "wait-then-run", "ask-for-status", "all-idle-summary", "session-handoff", "handoff-after-done", "sequential-dependent-asks", "manage-skills"} <= names
    assert payload["user_dirs"]["skills"] == str(tmp_path / "skills.d")
    assert payload["user_dirs"]["context"] == str(tmp_path / "context.d")
    assert any("YO!agent skill `work-next`" in line for line in payload["context_lines"])
    assert any(
        "YO!agent skill `session-handoff`" in line
        and "Preserve perspectives" in line
        and "Do not ask one target session" in line
        and "do not reveal target-session identities" in line
        and "Direct agent-to-agent relay/chaining is rare" in line
        and "concrete instructions for how the agent should relay" in line
        for line in payload["context_lines"]
    )
    assert any(
        "YO!agent skill `handoff-after-done`" in line
        and "source-neutral pickup prompt" in line
        for line in payload["context_lines"]
    )
    assert any(
        "YO!agent skill `wait-then-run`" in line
        and "ask agent 1 to <do ...>" in line
        and "sends only \"<do ...>\"" in line
        for line in payload["context_lines"]
    )
    assert any(
        "YO!agent skill `sequential-dependent-asks`" in line
        and "even when every step targets the same tmux session" in line
        and "never paste free text into a menu" in line
        for line in payload["context_lines"]
    )
    assert any("YO!agent skill `manage-skills`" in line and "~/.config/yolomux/skills.d/" in line for line in payload["context_lines"])
    assert any("YO!agent context `recommendation-rubric`" in line for line in payload["context_lines"])


def test_user_skills_override_disable_and_extend_builtins(tmp_path):
    skills_dir = tmp_path / "skills.d"
    skills_dir.mkdir()
    (skills_dir / "work-next.yaml").write_text("name: work-next\nenabled: false\n", encoding="utf-8")
    (skills_dir / "local-checks.yaml").write_text(
        "\n".join([
            "name: local-checks",
            "kind: workflow",
            "description: Ask an idle agent to run the local focused check command after review.",
            "tools:",
            "  - read_activity",
            "  - preview_send_prompt",
            "confirmation: required",
        ]),
        encoding="utf-8",
    )

    payload = load_yoagent_skills(user_skills_dir=skills_dir, user_context_dir=tmp_path / "missing-context")
    by_name = {item["name"]: item for item in payload["skills"]}

    assert payload["ok"] is True
    assert by_name["work-next"]["enabled"] is False
    assert by_name["work-next"]["builtin"] is False
    assert by_name["local-checks"]["enabled"] is True
    assert by_name["local-checks"]["confirmation"] == "required"
    assert not any("YO!agent skill `work-next`" in line for line in payload["context_lines"])
    assert any("YO!agent skill `local-checks`" in line for line in payload["context_lines"])


def test_invalid_user_skill_reports_errors_without_loading(tmp_path):
    skills_dir = tmp_path / "skills.d"
    skills_dir.mkdir()
    (skills_dir / "bad.yaml").write_text(
        "name: bad skill\ndescription: no\nkind: workflow\ntools:\n  - not_a_tool\n",
        encoding="utf-8",
    )

    payload = load_yoagent_skills(user_skills_dir=skills_dir, user_context_dir=tmp_path / "missing-context")

    assert payload["ok"] is False
    assert not any(item["name"] == "bad skill" for item in payload["skills"])
    errors = " ".join(item["error"] for item in payload["errors"])
    assert "name must match" in errors
    assert "unknown tools: not_a_tool" in errors


def test_parse_skill_text_validates_enabled_and_timeout():
    skill, errors = parse_skill_text(
        "name: sample\nkind: workflow\ndescription: Sample skill.\nenabled: maybe\ndefault_timeout_minutes: 0\n",
        "sample.yaml",
        False,
        "sample",
    )

    assert skill is None
    assert [item["error"] for item in errors] == [
        "enabled must be true or false",
        "default_timeout_minutes must be between 1 and 1440",
    ]


def test_user_skill_files_can_be_written_listed_read_and_deleted(tmp_path):
    skill_text = "\n".join([
        "name: local-checks",
        "kind: workflow",
        "description: Ask an idle agent to run the focused local check command.",
        "tools:",
        "  - read_activity",
        "  - write_skill_file",
        "confirmation: none",
    ])

    skill = write_user_skill_file("skill", "local-checks", skill_text, config_dir=tmp_path)
    context = write_user_skill_file("context", "local-notes", "Prefer the narrow focused test before the full gate.", config_dir=tmp_path)
    listed = list_user_skill_files(config_dir=tmp_path)
    read_back = read_user_skill_file("skill", "local-checks", config_dir=tmp_path)
    deleted = delete_user_skill_file("skill", "local-checks", config_dir=tmp_path)

    assert skill["path"] == str(tmp_path / "skills.d" / "local-checks.yaml")
    assert skill["valid"] is True
    assert context["path"] == str(tmp_path / "context.d" / "local-notes.md")
    assert {item["name"] for item in listed["files"]} == {"local-checks", "local-notes"}
    assert read_back["text"].startswith("name: local-checks")
    assert deleted["deleted"] is True
    assert not (tmp_path / "skills.d" / "local-checks.yaml").exists()


def test_user_skill_file_writes_reject_bad_paths_and_invalid_yaml(tmp_path):
    bad_skill = "\n".join([
        "name: local checks",
        "description: invalid name",
        "tools:",
        "  - unknown_tool",
    ])

    try:
        write_user_skill_file("skill", "../bad", bad_skill, config_dir=tmp_path)
    except ValueError as exc:
        assert "file name" in str(exc)
    else:
        raise AssertionError("path traversal must be rejected")

    try:
        write_user_skill_file("skill", "local-checks", bad_skill, config_dir=tmp_path)
    except ValueError as exc:
        message = str(exc)
        assert "name must match" in message
        assert "unknown tools" in message
    else:
        raise AssertionError("invalid skill YAML must be rejected")

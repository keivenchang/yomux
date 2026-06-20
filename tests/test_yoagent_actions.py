from yolomux_lib.yoagent.actions import parse_yoagent_action_intent
from yolomux_lib.yoagent.actions import parse_yoagent_job_intent
from yolomux_lib.yoagent.actions import parse_yoagent_skill_file_intent


def test_parse_wait_then_send_to_numeric_session():
    intent = parse_yoagent_action_intent(
        "wait for session '6' to finish, then tell it to run date",
        [],
        ["6"],
    )

    assert intent == {"type": "wait_then_send", "session": "6", "text": "date", "submit": True, "return_result": True}


def test_parse_direct_tell_session_to_run():
    intent = parse_yoagent_action_intent("tell session 6 to run python3 tools/check.py", [], ["6"])

    assert intent == {
        "type": "send_prompt",
        "session": "6",
        "text": "python3 tools/check.py",
        "submit": True,
        "return_result": True,
    }


def test_parse_direct_agent_alias_strips_routing_perspective():
    intent = parse_yoagent_action_intent("ask agent 1 to list changed files", [], ["1"])

    assert intent == {
        "type": "send_prompt",
        "session": "1",
        "text": "list changed files",
        "submit": True,
        "return_result": True,
    }


def test_parse_notify_job_intents():
    assert parse_yoagent_job_intent("notify me when agent 1 is idling", ["1"]) == {
        "type": "notify_session_idle",
        "session": "1",
    }
    assert parse_yoagent_job_intent("tell me when all sessions are idle", ["1", "2"]) == {
        "type": "notify_all_idle",
    }
    assert parse_yoagent_job_intent("notify me when agent 9 is idling", ["1"]) is None


def test_parse_direct_tell_bare_session_name():
    intent = parse_yoagent_action_intent(
        "tell 8002 and ask what it has done today, and tell me what it says.",
        [],
        ["8002"],
    )

    assert intent == {
        "type": "send_prompt",
        "session": "8002",
        "text": "what have you done today?",
        "submit": True,
        "return_result": True,
    }


def test_parse_target_agent_prompts_use_second_person_point_of_view():
    cases = [
        ("ask session 1 what has it done today", "what have you done today?"),
        ("ask session 1 what it is doing", "what are you doing?"),
        ("ask session 1 what it has been working on this morning", "what have you been working on this morning?"),
        ("ask session 1 for its status", "what is your status?"),
        ("ask session 1 what the time is", "what time is it?"),
    ]
    for question, expected in cases:
        intent = parse_yoagent_action_intent(question, [], ["1"])
        assert intent == {"type": "send_prompt", "session": "1", "text": expected, "submit": True, "return_result": True}


def test_parse_target_agent_prompt_does_not_rewrite_object_it():
    intent = parse_yoagent_action_intent("ask session 1 to review it for regressions", [], ["1"])

    assert intent == {"type": "send_prompt", "session": "1", "text": "review it for regressions", "submit": True, "return_result": True}


def test_parse_direct_send_text_to_session():
    intent = parse_yoagent_action_intent("send `date` to tmux session 6", [], ["6"])

    assert intent == {"type": "send_prompt", "session": "6", "text": "date", "submit": True, "return_result": True}


def test_parse_natural_date_command_as_agent_prompt():
    intent = parse_yoagent_action_intent("send a date command session 6", [], ["6"])

    assert intent == {"type": "send_prompt", "session": "6", "text": "tell me the date", "submit": True, "return_result": True}


def test_parse_run_date_keeps_shell_command():
    intent = parse_yoagent_action_intent("tell session 6 to run date", [], ["6"])

    assert intent == {"type": "send_prompt", "session": "6", "text": "date", "submit": True, "return_result": True}


def test_parse_direct_uses_recent_session_from_history():
    intent = parse_yoagent_action_intent(
        "tell it to run git status",
        [{"role": "user", "content": "what is tmux session 6 doing?"}],
        ["6"],
    )

    assert intent == {"type": "send_prompt", "session": "6", "text": "git status", "submit": True, "return_result": True}


def test_parse_direct_send_can_request_confirmation():
    intent = parse_yoagent_action_intent("preview before sending `date` to tmux session 6", [], ["6"])

    assert intent == {
        "type": "send_prompt",
        "session": "6",
        "text": "date",
        "submit": True,
        "requires_confirmation": True,
        "return_result": True,
    }


def test_parse_direct_send_can_request_result_here():
    intent = parse_yoagent_action_intent("tell session 6 to run date and show the result here", [], ["6"])

    assert intent == {
        "type": "send_prompt",
        "session": "6",
        "text": "date",
        "submit": True,
        "return_result": True,
    }


def test_parse_direct_send_can_opt_out_of_result_wait():
    assert parse_yoagent_action_intent("send `date` to tmux session 6 but do not wait for the result", [], ["6"]) == {
        "type": "send_prompt",
        "session": "6",
        "text": "date",
        "submit": True,
    }
    assert parse_yoagent_action_intent("tell session 6 to run date and just send it", [], ["6"]) == {
        "type": "send_prompt",
        "session": "6",
        "text": "date",
        "submit": True,
    }


def test_parse_direct_send_result_request_variants():
    assert parse_yoagent_action_intent("send `date` to tmux session 6 and print it here", [], ["6"]) == {
        "type": "send_prompt",
        "session": "6",
        "text": "date",
        "submit": True,
        "return_result": True,
    }
    assert parse_yoagent_action_intent("tell session 6 to run date and tell me the result", [], ["6"]) == {
        "type": "send_prompt",
        "session": "6",
        "text": "date",
        "submit": True,
        "return_result": True,
    }


def test_parse_session_handoff_keeps_next_session_out_of_first_prompt():
    intent = parse_yoagent_action_intent(
        "ask session 1 what the time is, and then take that result, add 35 minutes, and ask session 2 if that is correct.",
        [],
        ["1", "2"],
    )

    assert intent == {
        "type": "session_handoff",
        "session": "1",
        "text": "what time is it?",
        "submit": True,
        "return_result": True,
        "handoff": {
            "source_session": "1",
            "session": "2",
            "instruction": "take that result, add 35 minutes, and ask session 2 if that is correct",
        },
    }


def test_parse_skill_file_create_with_description():
    intent = parse_yoagent_skill_file_intent("create skill local-checks description: Ask idle agents to run focused tests.")

    assert intent == {
        "type": "skill_file",
        "operation": "upsert",
        "kind": "skill",
        "name": "local-checks",
        "text": "name: local-checks\nkind: workflow\ndescription: Ask idle agents to run focused tests.\nconfirmation: none",
    }


def test_parse_skill_file_update_with_fenced_yaml():
    intent = parse_yoagent_skill_file_intent(
        "update skill local-checks\n```yaml\nname: local-checks\nenabled: false\n```"
    )

    assert intent == {
        "type": "skill_file",
        "operation": "upsert",
        "kind": "skill",
        "name": "local-checks",
        "text": "name: local-checks\nenabled: false",
    }


def test_parse_skill_file_read_delete_and_list():
    assert parse_yoagent_skill_file_intent("list my YO!skills") == {
        "type": "skill_file",
        "operation": "list",
        "kind": "skill",
    }
    assert parse_yoagent_skill_file_intent("read skill local-checks") == {
        "type": "skill_file",
        "operation": "read",
        "kind": "skill",
        "name": "local-checks",
    }
    assert parse_yoagent_skill_file_intent("delete skill local-checks") == {
        "type": "skill_file",
        "operation": "delete",
        "kind": "skill",
        "name": "local-checks",
    }

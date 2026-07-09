# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Offline contracts for declarative model-assisted YO!agent intents."""

from yolomux_lib.yoagent.model_intents import model_intent_job_payload
from yolomux_lib.yoagent.model_intents import model_intent_prompt
from yolomux_lib.yoagent.model_intents import model_intent_provider_schema
from yolomux_lib.yoagent.model_intents import parse_model_intent_plan
from yolomux_lib.yoagent.model_intents import select_model_intent
from yolomux_lib.yoagent.skills import load_yoagent_skills


def roster_skill_payload():
    return load_yoagent_skills()["skills"]


def roster_definition(question: str):
    definition = select_model_intent({"skills": roster_skill_payload()}, question, ["1", "2", "3", "4"])
    assert definition is not None
    return definition


def test_registry_selects_one_builtin_intent_and_builds_isolated_prompt():
    definition = roster_definition("Keep an eye on 1, 2, 3 and 4, then have session 1 run /dyn-tps-report 1 2 3 4 EOD.")

    provider_schema = model_intent_provider_schema(definition, ["1", "2", "3", "4"])
    prompt = model_intent_prompt(definition, "Keep an eye on 1, 2, 3 and 4, then have session 1 run /dyn-tps-report 1 2 3 4 EOD.", ["1", "2", "3", "4"], schema=provider_schema)

    assert definition.name == "wait-roster-then-send"
    assert definition.handler == "wait-roster-then-send"
    assert definition.confirmation_required is True
    assert "return exactly the JSON object" in prompt
    assert "<yoagent-job-plan>" not in prompt
    assert "Known tmux sessions: [\"1\", \"2\", \"3\", \"4\"]" in prompt
    assert "Do not use tools" in prompt
    assert "known-session" not in prompt
    assert provider_schema["properties"]["roster"]["items"]["enum"] == ["1", "2", "3", "4"]
    assert select_model_intent({"skills": roster_skill_payload()}, "wait for session 1 to finish, then tell it to run date", ["1"]) is None
    assert select_model_intent({"skills": roster_skill_payload()}, "monitor session 1 and then send /dyn-tps-report 1 2 3 4 EOD to session 1", ["1", "2", "3", "4"]) is None


def test_generic_schema_parser_rejects_untrusted_or_malformed_model_plans():
    definition = roster_definition("Watch 1 2 3 4 and when they are all done send the report to session 1.")
    valid = '{"type":"wait_roster_then_send","roster":["1","2","3","4"],"destination":"1","command":"/dyn-tps-report 1 2 3 4 EOD","return_result":false}'

    plan = parse_model_intent_plan(valid, definition, ["1", "2", "3", "4"])

    assert plan is not None
    assert model_intent_job_payload(definition, plan) == {
        "type": "wait_roster_then_send",
        "roster": ["1", "2", "3", "4"],
        "quiet_seconds": 10.0,
        "return_result": False,
        "requires_confirmation": True,
        "action": {
            "session": "1",
            "text": "/dyn-tps-report 1 2 3 4 EOD",
            "submit": True,
            "return_result": False,
            "requires_confirmation": True,
        },
    }
    assert parse_model_intent_plan(valid.replace('"1","2","3","4"', '"missing"'), definition, ["1", "2", "3", "4"]) is None
    assert parse_model_intent_plan(valid.replace('"return_result":false', '"return_result":"false"'), definition, ["1", "2", "3", "4"]) is None
    assert parse_model_intent_plan(valid.replace('}', ',"unexpected":true}', 1), definition, ["1", "2", "3", "4"]) is None
    assert parse_model_intent_plan("I can do that: " + valid, definition, ["1", "2", "3", "4"]) is None
    assert parse_model_intent_plan(f"<yoagent-job-plan>{valid}</yoagent-job-plan>", definition, ["1", "2", "3", "4"]) is not None


def test_user_override_cannot_register_a_model_intent():
    skills = roster_skill_payload()
    overridden = [
        {**skill, "builtin": False}
        if skill["name"] == "wait-roster-then-send"
        else skill
        for skill in skills
    ]

    assert select_model_intent({"skills": overridden}, "Monitor 1 2 3 4 and send a report to session 1", ["1", "2", "3", "4"]) is None

# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Declarative, model-assisted YO!agent intent planning.

Models may propose one typed plan, but this module owns selection, parsing,
schema validation, and dispatch. The model never receives a transport or an
execution capability.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


MODEL_INTENT_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,63}$")


@dataclass(frozen=True)
class ModelIntentDefinition:
    name: str
    handler: str
    tag: str
    schema: dict[str, Any]
    candidate_any_of: tuple[str, ...]
    candidate_require_any: tuple[str, ...]
    candidate_collective_terms: tuple[str, ...]
    min_known_sessions: int
    confirmation_required: bool
    defaults: dict[str, Any]


def _string_list(value: Any) -> tuple[str, ...] | None:
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        return None
    return tuple(dict.fromkeys(item.strip().lower() for item in value))


def model_intent_definition_from_payload(skill: dict[str, Any]) -> ModelIntentDefinition | None:
    """Accept only a complete enabled built-in declarative model-intent skill."""
    if not skill.get("builtin") or not skill.get("enabled"):
        return None
    raw = skill.get("model_intent")
    if not isinstance(raw, dict):
        return None
    name = str(skill.get("name") or "").strip()
    handler = str(raw.get("handler") or "").strip()
    confirmation = str(raw.get("confirmation") or "").strip()
    candidate = raw.get("candidate")
    output = raw.get("output")
    defaults = raw.get("defaults")
    if not MODEL_INTENT_NAME_RE.fullmatch(name) or not MODEL_INTENT_NAME_RE.fullmatch(handler) or confirmation != "required":
        return None
    if not isinstance(candidate, dict) or not isinstance(output, dict) or not isinstance(defaults, dict):
        return None
    any_of = _string_list(candidate.get("any_of"))
    require_any = _string_list(candidate.get("require_any"))
    collective_terms = _string_list(candidate.get("collective_terms"))
    minimum = candidate.get("min_known_sessions")
    tag = str(output.get("tag") or "").strip()
    schema = output.get("schema")
    if not any_of or not require_any or not collective_terms or isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 1:
        return None
    if not MODEL_INTENT_NAME_RE.fullmatch(tag) or not isinstance(schema, dict):
        return None
    quiet_seconds = defaults.get("quiet_seconds")
    if isinstance(quiet_seconds, bool) or not isinstance(quiet_seconds, (int, float)) or not 1 <= quiet_seconds <= 300:
        return None
    return ModelIntentDefinition(
        name=name,
        handler=handler,
        tag=tag,
        schema=schema,
        candidate_any_of=any_of,
        candidate_require_any=require_any,
        candidate_collective_terms=collective_terms,
        min_known_sessions=minimum,
        confirmation_required=True,
        defaults={"quiet_seconds": float(quiet_seconds)},
    )


def known_session_mentions(question: str, known_sessions: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    text = str(question or "")
    mentioned = []
    for session in dict.fromkeys(str(item).strip() for item in known_sessions if str(item).strip()):
        pattern = rf"(?<![A-Za-z0-9_.-])[`'\"]?{re.escape(session)}[`'\"]?(?![A-Za-z0-9_.-])"
        if re.search(pattern, text, flags=re.IGNORECASE):
            mentioned.append(session)
    return tuple(mentioned)


def model_intent_matches(definition: ModelIntentDefinition, question: str, known_sessions: list[str] | tuple[str, ...]) -> bool:
    text = str(question or "").lower()
    if not any(term in text for term in definition.candidate_any_of) or not any(term in text for term in definition.candidate_require_any):
        return False
    command_start = min(position for term in definition.candidate_require_any if (position := text.find(term)) >= 0)
    condition_text = str(question or "")[:command_start]
    mentions = known_session_mentions(condition_text, known_sessions)
    return len(mentions) >= definition.min_known_sessions or any(term in condition_text.lower() for term in definition.candidate_collective_terms)


def select_model_intent(skills_payload: dict[str, Any], question: str, known_sessions: list[str] | tuple[str, ...]) -> ModelIntentDefinition | None:
    skills = skills_payload.get("skills") if isinstance(skills_payload, dict) else None
    if not isinstance(skills, list):
        return None
    matches = [definition for skill in skills if isinstance(skill, dict) for definition in [model_intent_definition_from_payload(skill)] if definition and model_intent_matches(definition, question, known_sessions)]
    return matches[0] if len(matches) == 1 else None


def model_intent_provider_schema(definition: ModelIntentDefinition, known_sessions: list[str] | tuple[str, ...]) -> dict[str, Any]:
    """Lower YOLOmux-only schema formats into the provider's portable JSON-Schema subset."""
    sessions = list(dict.fromkeys(str(item).strip() for item in known_sessions if str(item).strip()))

    def lower(value: Any) -> Any:
        if isinstance(value, list):
            return [lower(item) for item in value]
        if not isinstance(value, dict):
            return value
        lowered = {str(key): lower(item) for key, item in value.items()}
        if lowered.get("format") == "known-session":
            lowered.pop("format", None)
            lowered["enum"] = sessions
        return lowered

    lowered = lower(definition.schema)
    return lowered if isinstance(lowered, dict) else {}


def model_intent_prompt(definition: ModelIntentDefinition, question: str, known_sessions: list[str] | tuple[str, ...], *, schema: dict[str, Any] | None = None, native_structured_output: bool = True) -> str:
    sessions = list(dict.fromkeys(str(item).strip() for item in known_sessions if str(item).strip()))
    encoded_schema = json.dumps(schema if schema is not None else definition.schema, ensure_ascii=False, separators=(",", ":"))
    output_instruction = "return exactly the JSON object matching this schema with no surrounding prose" if native_structured_output else f"return exactly `<{definition.tag}>` followed by JSON matching this schema and `</{definition.tag}>`, with no surrounding prose"
    return (
        "You are a read-only YO!agent intent planner. Do not use tools, do not run commands, and do not contact or send anything to tmux. "
        f"Invoke preset `{definition.name}` by interpreting the user request against only this contract. Known tmux sessions: {json.dumps(sessions, ensure_ascii=False)}. "
        f"If and only if the request unambiguously matches the preset, {output_instruction}: {encoded_schema}. "
        "Use only known sessions. If any required field is ambiguous, return exactly `<no-plan/>`.\n\n"
        f"User request: {question}"
    )


def _validate_schema(value: Any, schema: dict[str, Any], known_sessions: set[str]) -> bool:
    value_type = schema.get("type")
    if value_type == "object":
        if not isinstance(value, dict):
            return False
        properties = schema.get("properties")
        required = schema.get("required")
        if not isinstance(properties, dict) or not isinstance(required, list) or not all(isinstance(item, str) for item in required):
            return False
        if any(item not in value for item in required):
            return False
        if schema.get("additionalProperties") is False and any(key not in properties for key in value):
            return False
        return all(isinstance(properties.get(key), dict) and _validate_schema(item, properties[key], known_sessions) for key, item in value.items())
    if value_type == "array":
        if not isinstance(value, list):
            return False
        minimum = schema.get("minItems")
        maximum = schema.get("maxItems")
        if isinstance(minimum, int) and len(value) < minimum:
            return False
        if isinstance(maximum, int) and len(value) > maximum:
            return False
        items = schema.get("items")
        return isinstance(items, dict) and all(_validate_schema(item, items, known_sessions) for item in value)
    if value_type == "string":
        if not isinstance(value, str) or not value.strip():
            return False
        choices = schema.get("enum")
        if choices is not None and (not isinstance(choices, list) or value not in choices):
            return False
        if schema.get("format") == "known-session" and value not in known_sessions:
            return False
        limit = schema.get("maxLength")
        return not isinstance(limit, int) or len(value) <= limit
    if value_type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        return (not isinstance(minimum, (int, float)) or value >= minimum) and (not isinstance(maximum, (int, float)) or value <= maximum)
    if value_type == "boolean":
        return isinstance(value, bool)
    return False


def parse_model_intent_plan(answer: str, definition: ModelIntentDefinition, known_sessions: list[str] | tuple[str, ...]) -> dict[str, Any] | None:
    text = str(answer or "").strip()
    match = re.fullmatch(rf"<{re.escape(definition.tag)}>\s*(?P<json>\{{[\s\S]*?\}})\s*</{re.escape(definition.tag)}>", text, flags=re.IGNORECASE)
    encoded = match.group("json") if match else text
    try:
        plan = json.loads(encoded)
    except json.JSONDecodeError:
        return None
    known = {str(item).strip() for item in known_sessions if str(item).strip()}
    return plan if _validate_schema(plan, definition.schema, known) else None


def _wait_roster_then_send_job_payload(definition: ModelIntentDefinition, plan: dict[str, Any]) -> dict[str, Any]:
    roster = list(dict.fromkeys(str(item).strip() for item in plan["roster"]))
    return {
        "type": "wait_roster_then_send",
        "roster": roster,
        "quiet_seconds": float(definition.defaults["quiet_seconds"]),
        "return_result": bool(plan.get("return_result", False)),
        "requires_confirmation": definition.confirmation_required,
        "action": {
            "session": str(plan["destination"]).strip(),
            "text": str(plan["command"]).strip(),
            "submit": True,
            "return_result": bool(plan.get("return_result", False)),
            "requires_confirmation": definition.confirmation_required,
        },
    }


MODEL_INTENT_JOB_HANDLERS = {
    "wait-roster-then-send": _wait_roster_then_send_job_payload,
}


def model_intent_job_payload(definition: ModelIntentDefinition, plan: dict[str, Any]) -> dict[str, Any] | None:
    """Map a validated plan to the existing job schema through the named handler registry."""
    handler = MODEL_INTENT_JOB_HANDLERS.get(definition.handler)
    return handler(definition, plan) if handler is not None else None

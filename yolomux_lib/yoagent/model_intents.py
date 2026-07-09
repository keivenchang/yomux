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
MODEL_INTENT_SCHEMA_TEMPLATES: dict[str, dict[str, Any]] = {
    "delegated-prompt": {
        "type": "object",
        "additionalProperties": False,
        "required": ["session", "prompt", "return_result"],
        "properties": {
            "session": {"type": "string", "format": "known-session"},
            "prompt": {"type": "string", "maxLength": 4000},
            "return_result": {"type": "boolean"},
        },
    },
    "loop-send": {
        "type": "object",
        "additionalProperties": False,
        "required": ["session", "prompt", "interval_seconds", "max_runs"],
        "properties": {
            "session": {"type": "string", "format": "known-session"},
            "prompt": {"type": "string", "maxLength": 4000},
            "interval_seconds": {"type": "number", "minimum": 5, "maximum": 3600},
            "max_runs": {"type": "integer", "minimum": 1, "maximum": 100},
        },
    },
}


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
    session_scope: str
    priority: int
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
    if not MODEL_INTENT_NAME_RE.fullmatch(name) or not MODEL_INTENT_NAME_RE.fullmatch(handler) or confirmation not in {"required", "none"}:
        return None
    if not isinstance(candidate, dict) or not isinstance(output, dict) or not isinstance(defaults, dict):
        return None
    any_of = _string_list(candidate.get("any_of"))
    require_any = _string_list(candidate.get("require_any", []))
    collective_terms = _string_list(candidate.get("collective_terms", []))
    minimum = candidate.get("min_known_sessions", 0)
    priority = candidate.get("priority", 0)
    session_scope = str(candidate.get("session_scope") or "condition").strip()
    tag = str(output.get("tag") or "").strip()
    raw_schema = output.get("schema")
    schema = MODEL_INTENT_SCHEMA_TEMPLATES.get(raw_schema) if isinstance(raw_schema, str) else raw_schema
    if not any_of or require_any is None or collective_terms is None or isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 0 or isinstance(priority, bool) or not isinstance(priority, int) or not 0 <= priority <= 100 or session_scope not in {"condition", "request"}:
        return None
    if not MODEL_INTENT_NAME_RE.fullmatch(tag) or not isinstance(schema, dict):
        return None
    if any(isinstance(value, (dict, list)) or value is None for value in defaults.values()):
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
        session_scope=session_scope,
        priority=priority,
        confirmation_required=confirmation == "required",
        defaults=dict(defaults),
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
    if not any(term in text for term in definition.candidate_any_of):
        return False
    if definition.candidate_require_any and not any(term in text for term in definition.candidate_require_any):
        return False
    command_start = min((position for term in definition.candidate_require_any if (position := text.find(term)) >= 0), default=len(text))
    condition_text = str(question or "")[:command_start]
    session_text = str(question or "") if definition.session_scope == "request" else condition_text
    mentions = known_session_mentions(session_text, known_sessions)
    if definition.candidate_collective_terms:
        return len(mentions) >= definition.min_known_sessions or any(term in condition_text.lower() for term in definition.candidate_collective_terms)
    return len(mentions) >= definition.min_known_sessions


def select_model_intent(skills_payload: dict[str, Any], question: str, known_sessions: list[str] | tuple[str, ...]) -> ModelIntentDefinition | None:
    skills = skills_payload.get("skills") if isinstance(skills_payload, dict) else None
    if not isinstance(skills, list):
        return None
    matches = [definition for skill in skills if isinstance(skill, dict) for definition in [model_intent_definition_from_payload(skill)] if definition and model_intent_matches(definition, question, known_sessions)]
    if not matches:
        return None
    highest = max(definition.priority for definition in matches)
    selected = [definition for definition in matches if definition.priority == highest]
    return selected[0] if len(selected) == 1 else None


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
    if value_type == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            return False
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        return (not isinstance(minimum, int) or value >= minimum) and (not isinstance(maximum, int) or value <= maximum)
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


def _wait_then_send_job_payload(definition: ModelIntentDefinition, plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "wait_then_send",
        "session": str(plan["session"]).strip(),
        "quiet_seconds": float(definition.defaults.get("quiet_seconds", 3)),
        "return_result": bool(plan.get("return_result", True)),
        "requires_confirmation": definition.confirmation_required,
        "action": {
            "session": str(plan["session"]).strip(),
            "text": str(plan["prompt"]).strip(),
            "submit": True,
            "return_result": bool(plan.get("return_result", True)),
            "requires_confirmation": definition.confirmation_required,
        },
    }


def _loop_send_job_payload(definition: ModelIntentDefinition, plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "loop_send",
        "session": str(plan["session"]).strip(),
        "interval_seconds": float(plan.get("interval_seconds") or definition.defaults.get("interval_seconds", 30)),
        "max_runs": int(plan.get("max_runs") or definition.defaults.get("max_runs", 10)),
        "return_result": False,
        "requires_confirmation": definition.confirmation_required,
        "action": {
            "session": str(plan["session"]).strip(),
            "text": str(plan["prompt"]).strip(),
            "submit": True,
            "return_result": False,
            "requires_confirmation": definition.confirmation_required,
        },
    }


MODEL_INTENT_JOB_HANDLERS = {
    "wait-roster-then-send": _wait_roster_then_send_job_payload,
    "wait-then-send": _wait_then_send_job_payload,
    "loop-send": _loop_send_job_payload,
}


def model_intent_job_payload(definition: ModelIntentDefinition, plan: dict[str, Any]) -> dict[str, Any] | None:
    """Map a validated plan to the existing job schema through the named handler registry."""
    handler = MODEL_INTENT_JOB_HANDLERS.get(definition.handler)
    return handler(definition, plan) if handler is not None else None

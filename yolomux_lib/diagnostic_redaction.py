from __future__ import annotations

import re
from typing import Any


DIAGNOSTIC_SECRET_NAME_PATTERN = (
    r"(?:token|secret|password|passwd|(?:proxy[_-]?)?authorization|(?:set[_-]?)?cookie|bearer|"
    r"(?:x[_-]?)?api[_-]?key|client[_-]?secret|(?:access|refresh|share)[_-]?token|x[_-]?share[_-]?token)"
)
DIAGNOSTIC_SECRET_KEY_RE = re.compile(rf"^{DIAGNOSTIC_SECRET_NAME_PATTERN}$", re.I)
DIAGNOSTIC_SHARE_URL_RE = re.compile(r"(?:https?://[^\"'\s<>]+)?/share/[A-Za-z0-9_-]+(?:#[^\"'\s<>]*)?")
DIAGNOSTIC_SECRET_ASSIGNMENT_RE = re.compile(
    rf"\b(?P<prefix>{DIAGNOSTIC_SECRET_NAME_PATTERN}\b[\"']?[ \t]*(?:=|:)[ \t]*)"
    r"(?:(?P<quote>[\"'])(?P<quoted_value>(?:\\[^\r\n]|(?!(?P=quote))[^\\\r\n])*)(?P=quote)|"
    r"(?P<unterminated_quote>[\"'])(?P<unterminated_value>[^\r\n]*)|"
    r"(?P<value>[^&#\s,;\"'<>}]+))",
    re.I,
)
DIAGNOSTIC_BEARER_VALUE_RE = re.compile(r"\b(Bearer)([ \t]+)([^\s,;:=\"'<>]+)", re.I)
DIAGNOSTIC_AUTHORIZATION_NAME_PATTERN = r"(?:proxy[-_]?)?authorization"
DIAGNOSTIC_AUTHORIZATION_SEPARATOR_PATTERN = r"[ \t]*(?::|=)[ \t]*"
DIAGNOSTIC_AUTHORIZATION_HEADER_RE = re.compile(
    rf"\b(?P<name>{DIAGNOSTIC_AUTHORIZATION_NAME_PATTERN})"
    rf"(?P<separator>{DIAGNOSTIC_AUTHORIZATION_SEPARATOR_PATTERN})"
    r"(?:Basic|Bearer)[ \t]+[^\s,;\"'<>}]+"
    r"(?![^\r\n]*=)"
    r"(?=[ \t]+(?:failed\b|after\b|at[ \t]+/|Cookie[ \t]*:)|[;\r\n]|$)",
    re.I,
)
DIAGNOSTIC_MALFORMED_AUTHORIZATION_HEADER_RE = re.compile(
    rf"(?!\b{DIAGNOSTIC_AUTHORIZATION_NAME_PATTERN}{DIAGNOSTIC_AUTHORIZATION_SEPARATOR_PATTERN}"
    r"\[redacted-secret\])"
    rf"(?!\b{DIAGNOSTIC_AUTHORIZATION_NAME_PATTERN}{DIAGNOSTIC_AUTHORIZATION_SEPARATOR_PATTERN}"
    r"(?:\r?\n|$))"
    rf"(?!\b{DIAGNOSTIC_AUTHORIZATION_NAME_PATTERN}{DIAGNOSTIC_AUTHORIZATION_SEPARATOR_PATTERN}[\"'])"
    rf"\b(?P<name>{DIAGNOSTIC_AUTHORIZATION_NAME_PATTERN})"
    rf"(?P<separator>{DIAGNOSTIC_AUTHORIZATION_SEPARATOR_PATTERN})[^\r\n]+",
    re.I,
)
DIAGNOSTIC_COOKIE_PAIR_PATTERN = (
    r"[^\s=;,\"'<>}]+[ \t]*=[ \t]*(?:"
    r'"(?:\\[^\r\n]|[^"\\\r\n])*"|'
    r"'(?:\\[^\r\n]|[^'\\\r\n])*'|"
    r"[^\s;,\"'<>}]+)(?=\s|;|\r?$)"
)
DIAGNOSTIC_COOKIE_HEADER_RE = re.compile(
    rf"\b(?P<name>(?:Set-)?Cookie)(?P<separator>[ \t]*:[ \t]*)"
    rf"{DIAGNOSTIC_COOKIE_PAIR_PATTERN}(?:[ \t]*;[ \t]*{DIAGNOSTIC_COOKIE_PAIR_PATTERN})*"
    r"(?![ \t]*;)(?![^\r\n]*=)",
    re.I,
)
DIAGNOSTIC_MALFORMED_COOKIE_HEADER_RE = re.compile(
    r"(?!\b(?:Set-)?Cookie[ \t]*:[ \t]*\[redacted-secret\])"
    r"(?!\b(?:Set-)?Cookie[ \t]*:[ \t]*(?:\r?\n|$))"
    r"\b(?P<name>(?:Set-)?Cookie)(?P<separator>[ \t]*:[ \t]*)[^\r\n]+",
    re.I,
)


def _redact_secret_assignment(match: re.Match[str]) -> str:
    quote = match.group("quote") or ""
    if match.group("unterminated_quote"):
        return f"{match.group('prefix')}[redacted-secret]"
    value = match.group("quoted_value") if quote else match.group("value")
    assert value is not None
    if value.startswith("[redacted-"):
        return match.group(0)
    return f"{match.group('prefix')}{quote}[redacted-secret]{quote}"


def _redact_secret_header(match: re.Match[str]) -> str:
    return f"{match.group('name')}{match.group('separator')}[redacted-secret]"


def redact_diagnostic_value(value: Any, key: str = "", depth: int = 0) -> Any:
    """Remove share credentials from bounded diagnostic values before retention."""

    if depth > 12:
        return "[truncated-depth]"
    if DIAGNOSTIC_SECRET_KEY_RE.search(str(key or "")):
        return "[redacted-share-token]"
    if isinstance(value, dict):
        return {
            str(name)[:120]: redact_diagnostic_value(item, str(name), depth + 1)
            for name, item in value.items()
        }
    if isinstance(value, list):
        return [redact_diagnostic_value(item, key, depth + 1) for item in value[:256]]
    if isinstance(value, str):
        text = DIAGNOSTIC_SHARE_URL_RE.sub("[redacted-share-url]", value)
        text = re.sub(
            r"([?#&](?:t|token|share|shareToken|share_token)=)[^&#\s\"']+",
            r"\1[redacted-share-token]",
            text,
            flags=re.I,
        )
        text = DIAGNOSTIC_AUTHORIZATION_HEADER_RE.sub(_redact_secret_header, text)
        text = DIAGNOSTIC_MALFORMED_AUTHORIZATION_HEADER_RE.sub(_redact_secret_header, text)
        text = DIAGNOSTIC_COOKIE_HEADER_RE.sub(_redact_secret_header, text)
        text = DIAGNOSTIC_MALFORMED_COOKIE_HEADER_RE.sub(_redact_secret_header, text)
        text = DIAGNOSTIC_SECRET_ASSIGNMENT_RE.sub(_redact_secret_assignment, text)
        text = DIAGNOSTIC_BEARER_VALUE_RE.sub(r"\1\2[redacted-secret]", text)
        return text[:4000] + ("[truncated]" if len(text) > 4000 else "")
    return value

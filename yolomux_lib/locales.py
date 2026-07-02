# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Canonical shipped-locale registry and normalization."""

from __future__ import annotations

from collections.abc import Iterable
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from decimal import InvalidOperation
from decimal import ROUND_HALF_UP
from decimal import localcontext
from typing import Any


@dataclass(frozen=True)
class LocaleSpec:
    code: str
    endonym: str
    plural_rule: str


_PLURAL_CATEGORIES_BY_RULE: dict[str, frozenset[str]] = {
    "other": frozenset({"other"}),
    "one": frozenset({"one", "other"}),
    "romance-million": frozenset({"one", "many", "other"}),
    "french-million": frozenset({"one", "many", "other"}),
    "polish": frozenset({"one", "few", "many", "other"}),
    "hebrew": frozenset({"one", "two", "other"}),
    "arabic": frozenset({"zero", "one", "two", "few", "many", "other"}),
    "russian": frozenset({"one", "few", "many", "other"}),
    "hindi": frozenset({"one", "other"}),
}

LOCALE_SPECS: tuple[LocaleSpec, ...] = (
    LocaleSpec("en", "English", "one"),
    LocaleSpec("zh-Hant", "繁體中文", "other"),
    LocaleSpec("zh-Hans", "简体中文", "other"),
    LocaleSpec("ja", "日本語", "other"),
    LocaleSpec("ko", "한국어", "other"),
    LocaleSpec("es", "Español", "romance-million"),
    LocaleSpec("de", "Deutsch", "one"),
    LocaleSpec("fr", "Français", "french-million"),
    LocaleSpec("it", "Italiano", "romance-million"),
    LocaleSpec("pt-BR", "Português (BR)", "french-million"),
    LocaleSpec("pl", "Polski", "polish"),
    LocaleSpec("nl", "Nederlands", "one"),
    LocaleSpec("he", "עברית", "hebrew"),
    LocaleSpec("ar", "العربية", "arabic"),
    LocaleSpec("ru", "Русский", "russian"),
    LocaleSpec("hi", "हिन्दी", "hindi"),
    LocaleSpec("vi", "Tiếng Việt", "other"),
    LocaleSpec("th", "ไทย", "other"),
    LocaleSpec("tr", "Türkçe", "one"),
)
LOCALE_ENDONYMS = tuple((spec.code, spec.endonym) for spec in LOCALE_SPECS)
SHIPPED_LOCALES = tuple(locale for locale, _label in LOCALE_ENDONYMS)
FALLBACK_LOCALE = "en"
PSEUDO_LOCALE = "en-XA"
SYSTEM_LOCALE_PREFERENCE = "system"
LANGUAGE_PREFERENCES = frozenset((SYSTEM_LOCALE_PREFERENCE, *SHIPPED_LOCALES, PSEUDO_LOCALE))
RTL_LANGUAGE_BASES = frozenset({"ar", "fa", "he", "ur"})
_LOCALE_BY_CASEFOLD = {locale.casefold(): locale for locale in (*SHIPPED_LOCALES, PSEUDO_LOCALE)}
_LOCALE_SPEC_BY_CODE = {spec.code: spec for spec in LOCALE_SPECS}
PLURAL_CATEGORIES_BY_LOCALE = {
    spec.code: _PLURAL_CATEGORIES_BY_RULE[spec.plural_rule]
    for spec in LOCALE_SPECS
}


def _plural_operands(value: object) -> tuple[Decimal, int, int] | None:
    """Return CLDR's absolute n, integer i, and visible-decimal count v for a JS Number value."""
    try:
        number = abs(Decimal(str(value or 0)))
    except (InvalidOperation, ValueError):
        number = Decimal(0)
    if number.is_nan():
        number = Decimal(0)
    elif not number.is_finite():
        return None
    # Intl.PluralRules defaults to at most three fraction digits with halfExpand rounding. Apply the
    # same rounding before computing CLDR operands, then drop insignificant zeroes like JavaScript Number.
    if number.as_tuple().exponent < -3:
        with localcontext() as context:
            context.prec = max(28, len(number.as_tuple().digits) + abs(number.as_tuple().exponent) + 4)
            number = number.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    normalized = number.normalize()
    exponent = normalized.as_tuple().exponent
    return number, int(number), max(0, -exponent)


def plural_category(locale: object, count: object) -> str:
    """Select the shipped locale's CLDR cardinal category, matching browser Intl.PluralRules."""
    code = normalize_locale(locale)
    spec = _LOCALE_SPEC_BY_CODE[code]
    operands = _plural_operands(count)
    if operands is None:
        return "other"
    number, integer, visible_decimals = operands
    rule = spec.plural_rule
    if rule == "other":
        return "other"
    if rule == "one":
        return "one" if number == 1 else "other"
    if rule == "romance-million":
        if visible_decimals == 0 and integer and integer % 1_000_000 == 0:
            return "many"
        return "one" if number == 1 else "other"
    if rule == "french-million":
        if visible_decimals == 0 and integer and integer % 1_000_000 == 0:
            return "many"
        return "one" if integer in {0, 1} else "other"
    if rule == "polish":
        if visible_decimals == 0 and integer == 1:
            return "one"
        if visible_decimals == 0 and integer % 10 in {2, 3, 4} and integer % 100 not in {12, 13, 14}:
            return "few"
        if visible_decimals == 0:
            return "many"
        return "other"
    if rule == "hebrew":
        if (integer == 1 and visible_decimals == 0) or (integer == 0 and visible_decimals > 0):
            return "one"
        if integer == 2 and visible_decimals == 0:
            return "two"
        return "other"
    if rule == "arabic":
        if visible_decimals:
            return "other"
        if integer == 0:
            return "zero"
        if integer == 1:
            return "one"
        if integer == 2:
            return "two"
        remainder = integer % 100
        if 3 <= remainder <= 10:
            return "few"
        if 11 <= remainder <= 99:
            return "many"
        return "other"
    if rule == "russian":
        if visible_decimals:
            return "other"
        if integer % 10 == 1 and integer % 100 != 11:
            return "one"
        if integer % 10 in {2, 3, 4} and integer % 100 not in {12, 13, 14}:
            return "few"
        return "many"
    if rule == "hindi":
        return "one" if integer == 0 or number == 1 else "other"
    raise ValueError(f"unknown plural rule: {rule}")


def message_descriptor(key: str, fallback: object, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return the shared localizable-message shape with its raw diagnostic fallback."""
    raw_fallback = str(fallback or "")
    return {
        "key": str(key or ""),
        "params": {str(name): value for name, value in (params or {}).items()},
        "fallback": raw_fallback,
    }


def message_fields(field: str, key: str, fallback: object, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Flatten one message descriptor for payloads that retain a legacy raw text field."""
    descriptor = message_descriptor(key, fallback, params)
    return {
        field: descriptor["fallback"],
        f"{field}_key": descriptor["key"],
        f"{field}_params": descriptor["params"],
    }


def user_message_payload(key: str, fallback: object, **params: Any) -> dict[str, Any]:
    """Return a localizable user-message descriptor plus the unchanged diagnostic fallback."""
    descriptor = message_descriptor(key, fallback, params)
    return {
        "error": descriptor["fallback"],
        "user_message": descriptor,
    }


def normalize_locale(value: object, default: str = FALLBACK_LOCALE, *, allow_system: bool = False) -> str:
    """Return a canonical shipped locale, never an unchecked path/cache key."""
    text = str(value or "").strip()
    if allow_system and text.casefold() == SYSTEM_LOCALE_PREFERENCE:
        return SYSTEM_LOCALE_PREFERENCE
    return _LOCALE_BY_CASEFOLD.get(text.casefold(), default)


def locale_direction(locale: object) -> str:
    base = normalize_locale(locale).casefold().split("-", 1)[0]
    return "rtl" if base in RTL_LANGUAGE_BASES else "ltr"


def accept_language_locales(header: object) -> Iterable[str]:
    """Yield browser language tags by descending q-value, preserving header order for ties."""
    weighted: list[tuple[float, int, str]] = []
    for index, item in enumerate(str(header or "").split(",")):
        tag, *params = (part.strip() for part in item.split(";"))
        if not tag or tag == "*":
            continue
        quality = 1.0
        for param in params:
            if not param.lower().startswith("q="):
                continue
            try:
                quality = float(param[2:])
            except ValueError:
                quality = 0.0
        if quality > 0:
            weighted.append((-quality, index, tag))
    for _quality, _index, tag in sorted(weighted):
        yield tag


def locale_for_language_tag(tag: object, default: str = "") -> str:
    text = str(tag or "").strip().replace("_", "-")
    lowered = text.casefold()
    if lowered.startswith("zh"):
        return "zh-Hant" if any(part in {"hant", "tw", "hk", "mo"} for part in lowered.split("-")) else "zh-Hans"
    exact = _LOCALE_BY_CASEFOLD.get(lowered)
    if exact and exact != PSEUDO_LOCALE:
        return exact
    base = lowered.split("-", 1)[0]
    return next((locale for locale in SHIPPED_LOCALES if locale.casefold().split("-", 1)[0] == base), default)


def resolve_locale_preference(value: object, accept_language: object = "", default: str = FALLBACK_LOCALE) -> str:
    preference = normalize_locale(value, default=SYSTEM_LOCALE_PREFERENCE, allow_system=True)
    if preference != SYSTEM_LOCALE_PREFERENCE:
        return preference
    for tag in accept_language_locales(accept_language):
        locale = locale_for_language_tag(tag)
        if locale:
            return locale
    return default


def locale_registry_payload(accept_language: object = "") -> dict[str, Any]:
    """Return the one ordered locale registry consumed by browser boot and runtime switches."""
    return {
        "fallback": FALLBACK_LOCALE,
        "pseudo": PSEUDO_LOCALE,
        "systemPreference": SYSTEM_LOCALE_PREFERENCE,
        "systemLocale": resolve_locale_preference(SYSTEM_LOCALE_PREFERENCE, accept_language),
        "locales": [
            {
                "id": spec.code,
                "endonym": spec.endonym,
                "direction": locale_direction(spec.code),
            }
            for spec in LOCALE_SPECS
        ],
    }

# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Product environment ownership, usable before importing the application."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Mapping, MutableMapping

NAMESPACE_PATH = Path(__file__).with_name("product_namespace.json")
NAMESPACE = json.loads(NAMESPACE_PATH.read_text())
CANONICAL_PREFIX: str = NAMESPACE["canonical"]
LEGACY_PREFIXES: tuple[str, ...] = tuple(NAMESPACE["legacy"])
PRODUCT_PREFIXES = (CANONICAL_PREFIX, *LEGACY_PREFIXES)
_METADATA = ("ENVIRONMENT_NORMALIZED", "ENVIRONMENT_ALIASES", "ENVIRONMENT_LEGACY_INPUT")


def product_env_suffix(name: str) -> str | None:
    """Identify an external environment name; ordinary variables stay ordinary."""
    for prefix in PRODUCT_PREFIXES:
        if name.startswith(prefix):
            return name[len(prefix):]
    return None


def _setting(setting: str) -> str:
    # Compatibility wrappers still accept complete names from older callers.
    suffix = product_env_suffix(setting)
    setting = setting if suffix is None else suffix
    if not re.fullmatch(r"[A-Z][A-Z_0-9]*", setting):
        raise ValueError("product environment setting must be an uppercase suffix")
    return setting


def product_env_name(setting: str) -> str:
    return CANONICAL_PREFIX + _setting(setting)


def product_env_names(setting: str) -> tuple[str, ...]:
    suffix = _setting(setting)
    return tuple(prefix + suffix for prefix in PRODUCT_PREFIXES)


def product_env(setting: str, default: str | None = None, *, environ: Mapping[str, str] | None = None) -> str | None:
    """Read without normalization, mutation, warnings, or truthiness fallback."""
    values = os.environ if environ is None else environ
    for name in product_env_names(setting):
        if name in values:
            return values[name]
    return default


def product_env_present(setting: str, *, environ: Mapping[str, str] | None = None) -> bool:
    values = os.environ if environ is None else environ
    return any(name in values for name in product_env_names(setting))


def require_product_env(setting: str, *, environ: Mapping[str, str] | None = None) -> str:
    value = product_env(setting, environ=environ)
    if value is None:
        raise KeyError(product_env_name(setting))
    return value


def environment_value(values: Mapping[str, str], name: str, default: str | None = None) -> str | None:
    """Resolve mixed external-key tables through the same product owner."""
    suffix = product_env_suffix(name)
    return values.get(name, default) if suffix is None else product_env(suffix, default, environ=values)


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _environment_alias_provenance(values: Mapping[str, str]) -> str:
    aliases = {key: _fingerprint(value) for key, value in values.items()
               if key.startswith(LEGACY_PREFIXES) and product_env_suffix(key) not in _METADATA}
    return "," + ",".join(f"{key}={value}" for key, value in sorted(aliases.items())) + ","


def normalize_product_environment(values: Mapping[str, str], *, warn: bool = False) -> dict[str, str]:
    environment = dict(values)
    for setting in _METADATA:
        for name in product_env_names(setting)[1:]:
            environment.pop(name, None)
    aliases = dict(item.split("=", 1) for item in product_env("ENVIRONMENT_ALIASES", "", environ=environment).split(",") if "=" in item)
    legacy_keys = {key for key in environment if key.startswith(LEGACY_PREFIXES)}
    pending = set(filter(None, product_env("ENVIRONMENT_LEGACY_INPUT", "", environ=environment).split(","))) | {
        key for key in legacy_keys if aliases.get(key) != _fingerprint(environment[key])
    }
    settings = {product_env_suffix(key) for key in environment if key.startswith(PRODUCT_PREFIXES)} - set(_METADATA)
    for setting in settings:
        value = require_product_env(setting, environ=environment)
        environment.update(dict.fromkeys(product_env_names(setting), value))
    if warn and pending:
        print(f"WARNING: {', '.join(prefix + '*' for prefix in LEGACY_PREFIXES)} environment names are deprecated; use {CANONICAL_PREFIX}*.", file=sys.stderr)
        pending.clear()
    environment[product_env_name("ENVIRONMENT_NORMALIZED")] = "1"
    environment[product_env_name("ENVIRONMENT_ALIASES")] = _environment_alias_provenance(environment)
    environment[product_env_name("ENVIRONMENT_LEGACY_INPUT")] = ",".join(sorted(pending))
    return environment


def apply_product_environment_assignments(target: MutableMapping[str, str], assignments: Mapping[str, str]) -> None:
    """Publish suffix-keyed settings while retaining legacy-input provenance."""
    normalized = normalize_product_environment(target)
    authored_keys = set()
    for setting, value in assignments.items():
        names = product_env_names(setting)
        normalized.update(dict.fromkeys(names, value))
        authored_keys.update(names)
    # Only publish aliases of authored settings; unrelated injected roots stay live.
    normalized[product_env_name("ENVIRONMENT_ALIASES")] = _environment_alias_provenance(normalized)
    authored_keys.update(product_env_name(setting) for setting in _METADATA)
    target.update({key: normalized[key] for key in authored_keys})
    target[product_env_name("ENVIRONMENT_ALIASES")] = _environment_alias_provenance(target)


def clear_product_environment_assignments(target: MutableMapping[str, str], *settings: str) -> None:
    for setting in settings:
        for name in product_env_names(setting):
            target.pop(name, None)
    target[product_env_name("ENVIRONMENT_ALIASES")] = _environment_alias_provenance(target)


def expose_yomux_legacy_aliases(values: MutableMapping[str, str]) -> None:
    values.update(normalize_product_environment(values, warn=True))


# Supported pre-refactor imports have no independent implementation.
normalize_yomux_environment = normalize_product_environment


def apply_yomux_environment_assignments(target: MutableMapping[str, str], assignments: Mapping[str, str]) -> None:
    """Compatibility boundary for older mixed external-key assignment tables."""
    product = {}
    for name, value in assignments.items():
        suffix = product_env_suffix(name)
        if suffix is None:
            target[name] = value
        elif name == product_env_name(suffix) or product_env_name(suffix) not in assignments:
            product[suffix] = value
    apply_product_environment_assignments(target, product)


clear_yomux_environment_assignments = clear_product_environment_assignments

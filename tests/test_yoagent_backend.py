# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Backend-availability diagnostic for YO!agent's own chat.

Before this diagnostic existed the UI showed the single generic det.noBackend string regardless of
cause. These tests pin that the backend now reports a STRUCTURED which-precondition-failed reason
(module-missing vs no-provider vs no-credentials), not a bare boolean/fallback, and that each reason
maps to a specific actionable locale string in static_src/locales/en.json.
"""

import json
from pathlib import Path

from yolomux_lib.yoagent.preferences import backend_no_backend_notice
from yolomux_lib.yoagent.transports import BACKEND_REASON_AVAILABLE
from yolomux_lib.yoagent.transports import BACKEND_REASON_MODULE_MISSING
from yolomux_lib.yoagent.transports import BACKEND_REASON_NO_CREDENTIALS
from yolomux_lib.yoagent.transports import BACKEND_REASON_NO_PROVIDER
from yolomux_lib.yoagent.transports import BackendAvailability
from yolomux_lib.yoagent.transports import backend_availability


EN_LOCALE_PATH = Path(__file__).resolve().parents[1] / "static_src" / "locales" / "en.json"


def _no_module(_name):
    return False


def _module(name):
    def check(candidate):
        return candidate == name

    return check


def test_deterministic_backend_reports_no_provider_selected():
    result = backend_availability("deterministic", {}, module_available=_no_module)

    assert isinstance(result, BackendAvailability)
    assert result.available is False
    # Not a bare boolean/fallback: a specific machine-readable reason code.
    assert result.reason == BACKEND_REASON_NO_PROVIDER


def test_selected_provider_without_cli_or_sdk_reports_module_missing():
    result = backend_availability(
        "codex",
        {"codex": {"installed": False, "logged_in": False}},
        module_available=_no_module,
    )

    assert result.available is False
    assert result.reason == BACKEND_REASON_MODULE_MISSING
    # Names the exact managed SDK package the operator can install.
    assert result.sdk_module == "openai_codex"


def test_selected_provider_installed_but_logged_out_reports_no_credentials():
    result = backend_availability(
        "claude",
        {"claude": {"installed": True, "logged_in": False}},
        module_available=_no_module,
    )

    assert result.available is False
    assert result.reason == BACKEND_REASON_NO_CREDENTIALS
    # Surfaces the exact login command so the UI can deep-link the fix.
    assert result.login_command == "claude auth login"


def test_three_preconditions_produce_three_distinct_reasons():
    # The whole point of the diagnostic: distinguish the three causes instead of one generic fallback.
    no_provider = backend_availability("deterministic", {}, module_available=_no_module)
    module_missing = backend_availability(
        "codex", {"codex": {"installed": False, "logged_in": False}}, module_available=_no_module
    )
    no_credentials = backend_availability(
        "codex", {"codex": {"installed": True, "logged_in": False}}, module_available=_no_module
    )

    reasons = {no_provider.reason, module_missing.reason, no_credentials.reason}
    assert reasons == {
        BACKEND_REASON_NO_PROVIDER,
        BACKEND_REASON_MODULE_MISSING,
        BACKEND_REASON_NO_CREDENTIALS,
    }


def test_logged_in_cli_or_installed_sdk_is_available():
    via_cli = backend_availability(
        "codex", {"codex": {"installed": True, "logged_in": True}}, module_available=_no_module
    )
    via_sdk = backend_availability(
        "claude", {"claude": {"installed": False, "logged_in": False}}, module_available=_module("claude_code_sdk")
    )

    assert via_cli.available is True
    assert via_cli.reason == BACKEND_REASON_AVAILABLE
    assert via_sdk.available is True
    assert via_sdk.sdk_module == "claude_code_sdk"


def test_auto_backend_prefers_available_then_most_actionable_blocker():
    # auto resolves codex then claude; surface the first provider that can answer.
    available = backend_availability(
        "auto",
        {"codex": {"installed": True, "logged_in": True}, "claude": {"installed": False}},
        module_available=_no_module,
    )
    assert available.available is True
    assert available.backend == "codex"

    # When none can answer, a present-but-logged-out CLI is more actionable than a wholly missing one.
    blocked = backend_availability(
        "auto",
        {"codex": {"installed": False, "logged_in": False}, "claude": {"installed": True, "logged_in": False}},
        module_available=_no_module,
    )
    assert blocked.available is False
    assert blocked.reason == BACKEND_REASON_NO_CREDENTIALS
    assert blocked.backend == "claude"


def test_notice_maps_each_reason_to_a_specific_locale_key_with_params():
    no_provider = backend_no_backend_notice(
        backend_availability("deterministic", {}, module_available=_no_module)
    )
    module_missing = backend_no_backend_notice(
        backend_availability("codex", {"codex": {"installed": False, "logged_in": False}}, module_available=_no_module)
    )
    no_credentials = backend_no_backend_notice(
        backend_availability("codex", {"codex": {"installed": True, "logged_in": False}}, module_available=_no_module)
    )

    assert no_provider["locale_key"] == "det.noBackend.noProvider"
    assert no_provider["params"] == {}

    assert module_missing["locale_key"] == "det.noBackend.moduleMissing"
    assert module_missing["params"] == {"provider": "codex", "module": "openai_codex"}

    assert no_credentials["locale_key"] == "det.noBackend.noCredentials"
    assert no_credentials["params"] == {"provider": "codex", "command": "codex login"}


def test_actionable_locale_keys_exist_in_en_json():
    catalog = json.loads(EN_LOCALE_PATH.read_text(encoding="utf-8"))
    for key in ("det.noBackend.noProvider", "det.noBackend.moduleMissing", "det.noBackend.noCredentials"):
        assert key in catalog, f"missing actionable locale string {key} in en.json"
    # The provider/module/command placeholders the notice supplies must exist in the strings.
    assert "{provider}" in catalog["det.noBackend.moduleMissing"]
    assert "{module}" in catalog["det.noBackend.moduleMissing"]
    assert "{provider}" in catalog["det.noBackend.noCredentials"]
    assert "{command}" in catalog["det.noBackend.noCredentials"]

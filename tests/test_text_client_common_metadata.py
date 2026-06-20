# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from text_client_common import (  # noqa: E402
    CLAUDE_CONFIG_KEYS,
    CLAUDE_OUTPUT_TERMS,
    CLIENT_SHARED_CONFIG_KEYS,
    CLIENT_INTENT_ROWS,
    CLIENT_PERMISSION_DEFAULTS,
    CODEX_CONFIG_KEYS,
    CODEX_OUTPUT_TERMS,
    METRICS_OUTPUT_PREFIX,
    TOOL_OUTPUT_PREFIX,
    client_intent_markdown_rows,
    prefixed_output_labels,
)


def test_output_terminology_uses_cross_product_names():
    assert CODEX_OUTPUT_TERMS.title_label == "Reasoning (aka Thinking)"
    assert CODEX_OUTPUT_TERMS.lower_label == "reasoning (aka thinking)"
    assert CODEX_OUTPUT_TERMS.prefix == "reasoning"
    assert CLAUDE_OUTPUT_TERMS.title_label == "Thinking (aka Reasoning)"
    assert CLAUDE_OUTPUT_TERMS.lower_label == "thinking (aka reasoning)"
    assert CLAUDE_OUTPUT_TERMS.prefix == "thinking"


def test_permissive_defaults_are_single_source():
    assert CLIENT_PERMISSION_DEFAULTS.claude_permission_mode == "bypassPermissions"
    assert CLIENT_PERMISSION_DEFAULTS.codex_sandbox == "danger-full-access"
    assert CLIENT_PERMISSION_DEFAULTS.codex_approval_policy == "never"
    assert CLIENT_PERMISSION_DEFAULTS.codex_bypass_hook_trust is True
    assert CLIENT_PERMISSION_DEFAULTS.codex_text_client_approval_mode == "accept"
    assert CLIENT_PERMISSION_DEFAULTS.claude_skip_permissions_flag == "--dangerously-skip-permissions"
    assert CLIENT_PERMISSION_DEFAULTS.codex_bypass_approvals_flag == "--dangerously-bypass-approvals-and-sandbox"
    assert CLIENT_PERMISSION_DEFAULTS.codex_bypass_hook_trust_flag == "--dangerously-bypass-hook-trust"
    assert CLIENT_PERMISSION_DEFAULTS.codex_is_permissive("danger-full-access", "never")


def test_shared_config_keys_map_same_intent_names():
    assert CLIENT_SHARED_CONFIG_KEYS.model == "model"
    assert CODEX_CONFIG_KEYS.model == CLIENT_SHARED_CONFIG_KEYS.model
    assert CLAUDE_CONFIG_KEYS.model == CLIENT_SHARED_CONFIG_KEYS.model
    assert CODEX_CONFIG_KEYS.tool_output == CLIENT_SHARED_CONFIG_KEYS.tool_output
    assert CLAUDE_CONFIG_KEYS.tool_output == CLIENT_SHARED_CONFIG_KEYS.tool_output
    assert CODEX_CONFIG_KEYS.metrics == CLIENT_SHARED_CONFIG_KEYS.metrics
    assert CLAUDE_CONFIG_KEYS.metrics == CLIENT_SHARED_CONFIG_KEYS.metrics
    assert CODEX_CONFIG_KEYS.effort == "model_reasoning_effort"
    assert CLAUDE_CONFIG_KEYS.effort == "effort"
    assert CODEX_CONFIG_KEYS.hidden_work_raw == "text_client.show_raw_reasoning"
    assert CLAUDE_CONFIG_KEYS.hidden_work_visibility == "text_client.show_thinking"


def test_prefixed_output_labels_share_common_tool_and_metrics_labels():
    assert prefixed_output_labels(CODEX_OUTPUT_TERMS) == ("reasoning", TOOL_OUTPUT_PREFIX, METRICS_OUTPUT_PREFIX)
    assert prefixed_output_labels(CLAUDE_OUTPUT_TERMS) == ("thinking", TOOL_OUTPUT_PREFIX, METRICS_OUTPUT_PREFIX)


def test_terminology_markdown_rows_are_valid_table_rows():
    rows = client_intent_markdown_rows()
    assert len(rows) == len(CLIENT_INTENT_ROWS) + 2
    assert all(row.count("|") == 6 for row in rows)
    assert any("reasoning&#124; ..." in row for row in rows)
    assert any("thinking&#124; ..." in row for row in rows)


def test_clients_doc_uses_shared_terminology_table():
    docs = (ROOT / "tools" / "CLIENTS.md").read_text()
    assert "\n".join(client_intent_markdown_rows()) in docs

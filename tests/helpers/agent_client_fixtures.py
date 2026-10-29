# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Private real-client provider configuration shared by E2E fixtures."""

import json
from pathlib import Path


TEST_MODELS = {
    "codex": "gpt-4o-mini",
    "claude": "claude-3-5-haiku-20241022",
    "opencode": "yolomux-fake/gpt-4o-mini",
}


def write_client_config(client: str, config_root: Path, port: int, fake_path: str) -> Path:
    endpoint = f"http://127.0.0.1:{port}{fake_path}"
    config_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if client == "codex":
        path = config_root / "codex-home" / "config.toml"
        path.parent.mkdir(mode=0o700, exist_ok=True)
        workspace = config_root / "workspace"
        workspace.mkdir(mode=0o700, exist_ok=True)
        path.write_text(
            f'model = "{TEST_MODELS[client]}"\nmodel_provider = "yolomux-fake"\n\n'
            f'[model_providers.yolomux-fake]\nname = "YOmux fake"\nwire_api = "responses"\nbase_url = "{endpoint}/v1"\nenv_key = "OPENAI_API_KEY"\n'
            '\n[features]\ndaemon_auto_start = false\n',
            encoding="utf-8",
        )
        with path.open("a", encoding="utf-8") as config_file:
            for directory in (workspace, config_root):
                config_file.write(f'\n[projects."{directory}"]\ntrust_level = "trusted"\n')
    elif client == "claude":
        path = config_root / "claude-settings" / "settings.json"
        path.parent.mkdir(mode=0o700, exist_ok=True)
        path.write_text(json.dumps({"model": TEST_MODELS[client], "apiKeyHelper": "printf yomux-test-only", "env": {"ANTHROPIC_BASE_URL": endpoint}}) + "\n", encoding="utf-8")
    else:
        path = config_root / "opencode" / "opencode.json"
        path.parent.mkdir(mode=0o700, exist_ok=True)
        path.write_text(json.dumps({
            "$schema": "https://opencode.ai/config.json",
            "model": TEST_MODELS[client],
            "provider": {"yolomux-fake": {"npm": "@ai-sdk/openai-compatible", "name": "YOmux fake", "options": {"baseURL": f"{endpoint}/v1", "apiKey": "yomux-test-only"}, "models": {"gpt-4o-mini": {"name": "gpt-4o-mini"}}}},
        }, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path

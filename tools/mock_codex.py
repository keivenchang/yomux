#!/usr/bin/env python3
"""Codex style mock terminal for YOLOmux UI testing."""

import sys

from mock_agent_common import configure, main


configure(
    agent_name="codex",
    agent_display_name="Codex",
    agent_product_name="OpenAI Codex",
    history_file="~/.cache/yolomux/mock_codex_history",
    version="0.141.0",
    model="gpt-5.5",
    effort="xhigh",
    model_line="gpt-5.5 xhigh · API Usage Billing",
    prompt_glyph="›",
    selector_glyph="›",
    permission_style="codex",
    startup_style="codex",
    codex_bypass_hook_trust="--dangerously-bypass-hook-trust" in sys.argv[1:],
    codex_danger_full_access="--dangerously-bypass-approvals-and-sandbox" in sys.argv[1:],
)


if __name__ == "__main__":
    main()

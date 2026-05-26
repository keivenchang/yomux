#!/usr/bin/env python3
"""Codex style mock terminal for YOLOMux UI testing."""

from mock_agent_common import configure, main


configure(
    agent_name="codex",
    agent_display_name="Codex",
    agent_product_name="Codex CLI",
    history_file="~/.cache/yolomux/mock_codex_history",
    version="9.9.999",
    model="gpt-5.5",
    effort="high",
    model_line="gpt-5.5 high · API Usage Billing",
    prompt_glyph="›",
    selector_glyph="›",
    permission_style="codex",
)


if __name__ == "__main__":
    main()

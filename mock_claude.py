#!/usr/bin/env python3
"""Claude Code style mock terminal for YOLOMux UI testing."""

from mock_agent_common import configure, main


configure(
    agent_name="claude",
    agent_display_name="Claude",
    agent_product_name="Claude Code",
    history_file="~/.cache/yolomux/mock_claude_history",
    version=".9.9.999",
    model="Opus 4.7 (1M context)",
    effort="low",
    model_line="Opus 4.7 (1M context) with low effort · API Usage Billing",
    prompt_glyph="❯",
    selector_glyph="❯",
    permission_style="claude",
)


if __name__ == "__main__":
    main()

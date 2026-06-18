# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""tests/ui_pins.json is the single place the node + Selenium suites pin shared UI color literals.

This guards it against drift: every pin must still equal its real origin (a token value in
00_tokens_base.css, or a literal in the shipping JS), so the pin file can't quietly disagree with what
the app actually renders.
"""

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PINS = json.loads((REPO_ROOT / "tests" / "ui_pins.json").read_text(encoding="utf-8"))
TOKENS_CSS = (REPO_ROOT / "static_src" / "css" / "yolomux" / "00_tokens_base.css").read_text(encoding="utf-8")
CM_JS = (REPO_ROOT / "static_src" / "js" / "yolomux" / "95_codemirror_editor.js").read_text(encoding="utf-8")


def test_ui_pins_match_sources():
    # token-sourced pins: value must equal the named token's value in 00_tokens_base.css
    token_pins = {
        "paneRingAttention": "--pane-ring-attention",
        "paneMetaPathLight": "--pane-meta-path",      # light-theme override value
        "textSelectionBg": "--text-selection-bg",
        "paneTabTextLight": "--pane-tab-text",          # light-theme override value
        "activeAccentBright": "--active-accent-bright",
    }
    for pin, token in token_pins.items():
        assert f"{token}: {PINS[pin]};" in TOKENS_CSS, f"{pin} ({PINS[pin]}) no longer matches {token} in 00_tokens_base.css"
    scoped_token_pins = {
        "activeAccentBrightLight": ("body.theme-light", "--active-accent-bright"),
    }
    for pin, (scope, token) in scoped_token_pins.items():
        pattern = rf"{re.escape(scope)}\s*\{{[\s\S]*?{re.escape(token)}:\s*{re.escape(PINS[pin])};"
        assert re.search(pattern, TOKENS_CSS), f"{pin} ({PINS[pin]}) no longer matches {token} in {scope}"
    # JS-literal pins: the diff overview band colors are literals in the shipping editor source
    assert f"'{PINS['diffOverviewAdd']}'" in CM_JS, "diffOverviewAdd no longer matches the JS diff-overview band color"
    assert f"'{PINS['diffOverviewDelete']}'" in CM_JS, "diffOverviewDelete no longer matches the JS diff-overview band color"

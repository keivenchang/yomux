# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Validate the shared UI color and locale-backed label registry.

Every color pin must still equal its real origin (a token value in 00_tokens_base.css or a literal in
the shipping JS). Label pins point to English locale keys instead of copying user-visible strings.
"""

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PINS = json.loads((REPO_ROOT / "tests" / "fixtures" / "ui_pins.json").read_text(encoding="utf-8"))
TOKENS_CSS = (REPO_ROOT / "static_src" / "css" / "yolomux" / "00_tokens_base.css").read_text(encoding="utf-8")
CM_JS = (REPO_ROOT / "static_src" / "js" / "yolomux" / "92_codemirror_editor.js").read_text(encoding="utf-8")
EN_CATALOG = json.loads((REPO_ROOT / "static_src" / "locales" / "en.json").read_text(encoding="utf-8"))
def ui_pin_color_literals(pins: dict | None = None) -> frozenset[str]:
    """Return registry colors that test code must reference by pin name, not retype."""
    return frozenset(
        value.lower()
        for value in (pins or PINS).values()
        if isinstance(value, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", value)
    )


def ui_pin_rehardcode_findings(root: Path, pins: dict | None = None) -> list[str]:
    """Find registered pin colors copied into tests instead of read from ui_pins.json.

    This scans tests only: product source owns literals and generated bundles are intentionally
    outside the test registry contract.  One finding per copied literal keeps migration reviews
    short and makes a new fork fail at the first relevant line.
    """
    findings: list[str] = []
    colors = ui_pin_color_literals(pins)
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.suffix in {".js", ".py"}):
        if path.name == "ui_pins.json":
            continue
        relative = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), start=1):
            for literal in sorted(colors):
                if literal in line.lower():
                    findings.append(f"{relative}:{line_number}: registered UI pin literal {literal} must use ui_pins.json")
    return findings


def test_ui_pins_match_sources():
    # token-sourced pins: value must equal the named token's value in 00_tokens_base.css
    token_pins = {
        "agentStatusCooldown": "--agent-status-cooldown",
        "statusGood": "--good",
        "neutralTextCool": "--neutral-text-cool",
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


def test_ui_label_pins_resolve_from_english_catalog():
    labels = PINS["labels"]

    assert labels, "the shared label registry must not be empty"
    for pin, locale_key in labels.items():
        assert locale_key in EN_CATALOG, f"{pin} refers to missing English locale key {locale_key}"
        assert isinstance(EN_CATALOG[locale_key], str) and EN_CATALOG[locale_key].strip(), (
            f"{pin} refers to blank/non-string English locale value {locale_key}"
        )


def test_ui_class_pins_are_stable_css_identifiers():
    classes = PINS["classes"]

    assert classes, "the shared CSS-class registry must not be empty"
    for pin, class_name in classes.items():
        assert re.fullmatch(r"[a-z][a-z0-9-]*", class_name), f"{pin} must be a CSS class identifier"


def test_ui_pin_rehardcode_scanner_rejects_registered_color_copies(tmp_path):
    source = tmp_path / "copied_pin.test.js"
    copied_color = "#123456"
    source.write_text(f"const copied = '{copied_color}';\n", encoding="utf-8")

    assert ui_pin_rehardcode_findings(tmp_path, {"green": copied_color}) == [
        "copied_pin.test.js:1: registered UI pin literal #123456 must use ui_pins.json",
    ]


def test_registered_ui_pin_colors_are_not_rehardcoded_in_tests():
    assert ui_pin_rehardcode_findings(REPO_ROOT / "tests") == []

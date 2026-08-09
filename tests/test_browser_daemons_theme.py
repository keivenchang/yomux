# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Theme coverage for the Daemons roster alert.

The compact alert above the roster is the surface that announces a dead daemon, and its ONLY
container affordance is `border: 1px solid var(--warning-border-strong)`. That token had no
`body.theme-light` value, so the same near-white yellow painted in both themes and the box had no
visible outline on the light panel (measured 1.2:1, WCAG's non-text minimum is 3:1). No test in the
repo toggled a theme and measured the roster, which is why only the text re-themed.

The ratios here are computed from RESOLVED computed styles in each theme, never from an expected
hex: a test that pinned the colour would pass on any wrong colour typed in later.
"""

import pytest

from tests.browser_helpers.browser_layout import *  # noqa: F401,F403

pytestmark = [pytest.mark.browser, pytest.mark.socket]

# WCAG 2.1 SC 1.4.11 non-text contrast: a control/graphical boundary needs 3:1 against its adjacent
# background. The alert border IS the box, so it is the boundary that must clear it.
NON_TEXT_CONTRAST_MINIMUM = 3.0

# The real Daemons ancestry, so the background the border sits on resolves through the real cascade
# (`.panel` -> --panel-inactive) rather than a background this fixture invents.
DAEMONS_ALERT_FIXTURE = """
<div class="panel js-debug-panel">
  <div class="js-debug-subview js-debug-system-view" data-js-debug-system>
    <div class="js-debug-system-region" data-js-debug-system-region="alerts">
      <div class="js-debug-system-alert" id="daemons-alert" role="alert">
        <p data-system-alert="health">Backend health observer stopped: no daemon has reported for 14 minutes.</p>
      </div>
    </div>
  </div>
</div>
"""

# Walk outwards for the first painted background, exactly as a reader's eye does: the alert and its
# region wrappers are transparent, so the adjacent colour is whichever ancestor actually paints.
MEASURE_ALERT_SCRIPT = """
const alert = document.getElementById('daemons-alert');
const painted = value => {
  const parts = String(value || '').match(/[\\d.]+/g) || [];
  return Boolean(parts.length) && !(parts.length > 3 && Number(parts[3]) === 0) && value !== 'transparent';
};
let background = getComputedStyle(document.documentElement).backgroundColor;
let backgroundOwner = 'html';
for (let node = alert; node; node = node.parentElement) {
  const value = getComputedStyle(node).backgroundColor;
  if (painted(value)) {
    background = value;
    backgroundOwner = node.id || node.className || node.tagName;
    break;
  }
}
const style = getComputedStyle(alert);
return {
  theme: document.body.className,
  border: style.borderTopColor,
  borderWidth: style.borderTopWidth,
  color: style.color,
  background,
  backgroundOwner,
};
"""


def _measure_daemons_alert(browser, tmp_path, body_class):
    page = tmp_path / "daemons-alert-theme.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        f"""<!doctype html><html><head><meta charset="utf-8"><style>{app_css()}</style></head>
        <body class="{body_class}">{DAEMONS_ALERT_FIXTURE}</body></html>""",
    )
    return browser.execute_script(MEASURE_ALERT_SCRIPT)


def test_daemons_alert_box_outline_stays_visible_in_both_themes(browser, tmp_path):
    dark = _measure_daemons_alert(browser, tmp_path, "theme-dark")
    light = _measure_daemons_alert(browser, tmp_path, "theme-light")

    for theme, measured in (("dark", dark), ("light", light)):
        assert measured["borderWidth"] != "0px", f"{theme}: alert lost its only container affordance: {measured}"
        ratio = wcag_contrast_ratio(measured["border"], measured["background"])
        # Printed so a passing run still reports what it measured (`pytest -s`); the assertion below
        # is what enforces it.
        print(f"daemons alert outline: {theme} {measured['border']} on {measured['background']} = {ratio:.2f}:1")
        assert ratio >= NON_TEXT_CONTRAST_MINIMUM, (
            f"{theme} theme: Daemons alert border {measured['border']} on {measured['background']} "
            f"({measured['backgroundOwner']}) measures {ratio:.1f}:1, below the {NON_TEXT_CONTRAST_MINIMUM}:1 "
            f"WCAG non-text minimum: {measured}"
        )

    # A single token value shared by both themes is the defect shape this test exists to catch: the
    # border must actually repaint when the theme flips, not merely happen to clear 3:1 once.
    assert dark["border"] != light["border"], {"dark": dark, "light": light}


# The real Daemons Refresh control in both states. `aria-disabled` (never `disabled`) carries the
# in-flight state, because setting `disabled` blurs the element the moment the attribute lands and
# the user loses focus mid-refresh. The dimming still has to come from the ONE shared
# `:where(button:disabled, button[aria-disabled="true"])` owner -- when that selector listed only
# `:disabled`, the switch to `aria-disabled` silently removed the "something is happening" signal.
REFRESH_BUTTON_FIXTURE = """
<div class="panel js-debug-panel">
  <div class="js-debug-subview js-debug-system-view" data-js-debug-system>
    <div class="js-debug-system-region" data-js-debug-system-region="summary">
      <button type="button" id="refresh-busy" class="preferences-inline-action" data-js-debug-system-refresh
              data-js-debug-system-focus-key="roster-refresh" aria-disabled="true">Refresh</button>
      <button type="button" id="refresh-idle" class="preferences-inline-action" data-js-debug-system-refresh
              data-js-debug-system-focus-key="roster-refresh">Refresh</button>
    </div>
  </div>
</div>
"""

MEASURE_REFRESH_SCRIPT = """
const read = id => {
  const style = getComputedStyle(document.getElementById(id));
  return {opacity: Number(style.opacity), cursor: style.cursor, color: style.color};
};
return {busy: read('refresh-busy'), idle: read('refresh-idle')};
"""


def test_daemons_refresh_button_dims_while_a_refresh_is_in_flight(browser, tmp_path):
    page = tmp_path / "daemons-refresh-busy.html"
    load_static_html_fixture(
        browser,
        page.parent,
        page.name,
        f"""<!doctype html><html><head><meta charset="utf-8"><style>{app_css()}</style></head>
        <body class="theme-dark">{REFRESH_BUTTON_FIXTURE}</body></html>""",
    )
    measured = browser.execute_script(MEASURE_REFRESH_SCRIPT)

    # Measured against the identical idle sibling rather than a pinned 0.55, so retuning the shared
    # disabled appearance keeps this green while dropping the state entirely does not.
    print(f"daemons refresh: busy {measured['busy']} idle {measured['idle']}")
    assert measured["idle"]["opacity"] == 1.0, measured
    assert measured["busy"]["opacity"] < measured["idle"]["opacity"], (
        f"in-flight Refresh is not dimmed: aria-disabled opacity {measured['busy']['opacity']} "
        f"equals the idle {measured['idle']['opacity']}: {measured}"
    )
    # The shared owner's muted `color` cannot show here -- `.preferences-inline-action` sets its own
    # colour at higher specificity than the zero-specificity `:where(...)` rule, for the native
    # `:disabled` state too. Opacity and cursor are what this control actually carries.
    assert measured["busy"]["cursor"] == "not-allowed", measured

"""Explicit compatibility map for incremental mega-module subsystem moves."""

SUBSYSTEM_COMPATIBILITY = {
    "tests.gate_harness": {
        "CounterDelta": "tests.gate_helpers.reliability.CounterDelta",
        "RepeatFailure": "tests.gate_helpers.reliability.RepeatFailure",
        "assert_counter_delta": "tests.gate_helpers.reliability.assert_counter_delta",
        "repeat": "tests.gate_helpers.reliability.repeat",
        "sample_counter_delta": "tests.gate_helpers.reliability.sample_counter_delta",
    },
    "tests.browser_helpers.browser_layout": {
        "BrowserBootRoute": "tests.helpers.browser_boot.BrowserBootRoute",
        "BrowserBootScenario": "tests.helpers.browser_boot.BrowserBootScenario",
        "css_hex_to_rgb": "tests.browser_helpers.visual_contracts.css_hex_to_rgb",
        "wcag_contrast_ratio": "tests.browser_helpers.visual_contracts.wcag_contrast_ratio",
    },
    "tests.test_browser_layout": {
        "_agent_status_glyph_html": "tests.helpers.agent_status_fixtures.agent_status_glyph_html",
        "_tabber_window_button_html": "tests.helpers.agent_status_fixtures.tabber_window_button_html",
        "_working_agent_glyph_html": "tests.helpers.agent_status_fixtures.working_agent_glyph_html",
    },
    "tests.test_app": {
        "_StubOperationReservation": "tests.helpers.operation_reservations.StubOperationReservation",
        "_reservation_must_not_release": "tests.helpers.operation_reservations.reservation_must_not_release",
    },
}

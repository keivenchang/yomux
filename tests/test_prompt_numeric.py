# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import pytest

from yomux_lib.approval import prompt_detector


@pytest.mark.parametrize("text", ["9" * 400 + "s", "9" * 306 + "h"], ids=["overflow", "scaled-overflow"])
def test_status_duration_skips_unrepresentable_elapsed_time(text):
    assert prompt_detector._parse_duration_seconds(text) is None


@pytest.mark.parametrize("text", ["9" * 400 + " tokens", "9" * 306 + "M tokens"], ids=["overflow", "scaled-overflow"])
def test_status_tokens_skip_unrepresentable_counts(text):
    assert prompt_detector._parse_status_token_count(text) is None


def test_status_tool_uses_skips_unrepresentable_count():
    assert prompt_detector._parse_status_tool_uses("9" * 5000 + " tool uses") is None


@pytest.mark.parametrize("inline", [False, True])
@pytest.mark.parametrize("consumer", ["selection", "options"])
def test_unrepresentable_menu_numbers_do_not_abort_prompt_detection(inline, consumer):
    line = "❯ " + "9" * 5000 + ". Yes"
    if inline:
        line += " 2. No"
    if consumer == "selection":
        assert prompt_detector.selected_prompt_option(line) == 0
    else:
        assert [row["index"] for row in prompt_detector._prompt_options(line)] == ([2] if inline else [])


def test_finite_counters_and_menu_numbers_preserve_values():
    assert prompt_detector._parse_duration_seconds("0s") == 0
    assert prompt_detector._parse_status_token_count("1.2M tokens") == 1200000
    assert prompt_detector._parse_status_tool_uses(str(2**64) + " tool uses") == 2**64
    assert prompt_detector.selected_prompt_option("❯ 2. No") == 2
    assert [row["index"] for row in prompt_detector._prompt_options("❯ 1. Yes 2. No")] == [1, 2]

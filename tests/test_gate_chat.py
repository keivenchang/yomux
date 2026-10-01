"""Gate J: YO!chat user-visible answer delivery."""

import pytest

from tests.browser_helpers.browser_layout import browser
from tests.browser_helpers.browser_layout import load_live_runtime_boot_fixture
from tests.gate_harness import run_when_browser_ready


pytestmark = pytest.mark.browser


@pytest.mark.parametrize(
    ("query", "expected_answer"),
    (
        ("/yo where is the regression gate?", "YO!agent answer for: where is the regression gate?"),
        ("are you there?", "YO!agent answer for: are you there?"),
    ),
)
def test_j1_chat_message_issues_a_request_and_renders_an_answer(browser, tmp_path, query, expected_answer):
    """A slash-command or ordinary chat message makes the agent request and renders the corresponding answer."""
    load_live_runtime_boot_fixture(
        browser,
        tmp_path,
        "?sessions=chat&layout=slot1&tabs=slot1:chat",
        sessions=["1"],
    )
    result = run_when_browser_ready(
        browser,
        """
        const input = document.querySelector('#panel-__chat__ [data-chat-input]');
        input.value = arguments[0];
        input.dispatchEvent(new Event('input', {bubbles: true}));
        input.closest('[data-chat-form]').dispatchEvent(new Event('submit', {bubbles: true, cancelable: true}));
        return true;
        """,
        query,
        globals_required={"submitChatDraft": "function"},
        dom_anchors=("#panel-__chat__ [data-chat-input]",),
    )
    assert result is True
    answer = browser.execute_async_script(
        """
        const expectedAnswer = arguments[0];
        const done = arguments[arguments.length - 1];
        const started = performance.now();
        const poll = () => {
          const fetches = window.__bootFetches || [];
          const answer = Array.from(document.querySelectorAll('#panel-__chat__ [data-chat-message-id]'))
            .map(node => node.textContent || '')
            .find(text => text.includes(expectedAnswer));
          if (fetches.some(item => item.path === '/api/chat/send')
              && fetches.some(item => item.path === '/api/chat/yoagent')
              && answer) {
            done({fetches, answer});
            return;
          }
          if (performance.now() - started > 750) {
            done({fetches, answer: '', timedOut: true});
            return;
          }
          requestAnimationFrame(poll);
        };
        poll();
        """,
        expected_answer,
    )
    assert any(item["path"] == "/api/chat/send" for item in answer["fetches"]), answer
    assert any(item["path"] == "/api/chat/yoagent" for item in answer["fetches"]), answer
    assert expected_answer in answer["answer"], answer

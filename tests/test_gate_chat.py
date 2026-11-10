"""Gate J: YO!chat user-visible answer delivery."""

import json
import sqlite3
import time
from urllib.request import urlopen

import pytest
from selenium.webdriver.support.ui import WebDriverWait

from tests.browser_helpers.browser_layout import browser
from tests.browser_helpers.browser_layout import load_live_runtime_boot_fixture
from tests.gate_harness import run_when_browser_ready
from tests.isolated_dev_server import isolated_dev_server_factory  # noqa: F401


pytestmark = pytest.mark.browser


def test_native_chat_mount_loads_the_unread_bootstrap_continuation(browser, isolated_dev_server_factory, request):
    server = isolated_dev_server_factory('chat-unread-continuation')
    request.addfinalizer(lambda: browser.get('about:blank'))
    with urlopen(server.base_url + '/api/chat/bootstrap?browser_instance_id=before-unread') as response:
        initial = json.load(response)
    assert initial['messages'] == [] and initial['read_up_to_id'] == 0
    databases = list((server.paths.state_dir / 'hosts').glob('*/yochat.sqlite3'))
    assert len(databases) == 1
    def append_messages(first, stop):
        with sqlite3.connect(databases[0]) as database:
            database.executemany(
                'INSERT INTO chat_messages(created_at_utc,room_id,username,sender_instance_id,client_message_uuid,body,is_question) VALUES(?,?,?,?,?,?,?)',
                [(time.time(), 'global', 'fixture-sender', 'unread-fixture', str(index), f'unread message {index}', 0)
                 for index in range(first, stop)],
            )

    append_messages(1, 56)
    browser.get(server.base_url + '/?sessions=chat&layout=slot1&tabs=slot1:chat')
    WebDriverWait(browser, 15).until(lambda driver: driver.execute_script("""
      return typeof chatState !== 'undefined' && chatState.loaded
        && chatState.messages.size===55 && chatState.messages.has(55)
        && document.querySelector('#panel-__chat__ [data-chat-message-id="55"]');
    """))
    assert browser.execute_script('return [...chatState.messages.keys()]') == list(range(1, 56))
    append_messages(56, 456)
    WebDriverWait(browser, 15).until(lambda driver: driver.execute_script("""
      return chatState.messages.size===455 && chatState.messages.has(455)
        && document.querySelector('#panel-__chat__ [data-chat-message-id="455"]');
    """))
    assert browser.execute_script('return [...chatState.messages.keys()]') == list(range(1, 456))


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

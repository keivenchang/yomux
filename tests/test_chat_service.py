# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

from __future__ import annotations

from pathlib import Path

import pytest

from yolomux_lib.chat_questions import chat_message_is_question
from yolomux_lib.chat_service import ChatService
from yolomux_lib.chat_service import ChatServiceError
from yolomux_lib.chat_store import ChatStore


QUESTION_CASES = {
    "ar": "كيف يعمل", "de": "Wie funktioniert das", "en": "How does this work", "es": "Cómo funciona",
    "fr": "Comment ça marche", "he": "איך זה עובד", "hi": "कैसे काम करता है", "it": "Come funziona",
    "ja": "どう動く", "ko": "어떻게 작동해", "nl": "Hoe werkt dit", "pl": "Jak to działa",
    "pt-BR": "Como funciona", "ru": "Как это работает", "th": "อย่างไรจึงทำงาน", "tr": "Nasıl çalışır",
    "vi": "Ai đang làm", "zh-Hans": "怎么工作", "zh-Hant": "怎麼工作",
}

EMOJI_MATRIX = ("😀", "👍🏽", "👩‍💻", "👨‍👩‍👧‍👦", "🏳️‍🌈", "🇺🇸", "1️⃣", "☕️", "مرحبا 😀")


@pytest.mark.parametrize(("locale", "body"), QUESTION_CASES.items())
def test_question_classifier_supports_every_locale(locale, body):
    assert chat_message_is_question(body, locale) is True


@pytest.mark.parametrize("punctuation", ["?", "？", "؟", "՞", ";", "፧"])
def test_question_classifier_supports_unicode_question_punctuation(punctuation):
    assert chat_message_is_question(f"status{punctuation}", "en") is True


@pytest.mark.parametrize("body", ["https://example.test/search?q=status", "`value = query?x:y`", '"How does this work?"', "This is rhetorical text about why things happen", "😀", ""])
def test_question_classifier_avoids_url_code_quote_and_substring_false_positives(body):
    assert chat_message_is_question(body, "en") is False


def _service(path: Path, *, clock=None) -> ChatService:
    store = ChatStore(path / "yochat.sqlite3")
    kwargs = {"clock": clock} if clock is not None else {}
    return ChatService(store, cursor_secret_path=path / "chat-cursor.key", **kwargs)


def test_chat_service_authoritative_identity_exact_text_and_idempotent_retry(tmp_path):
    service = _service(tmp_path)
    payload = {
        "browser_instance_id": "browser-1", "client_message_uuid": "message-1",
        "body": "<script>alert(1)</script> 👨‍👩‍👧‍👦", "username": "mallory", "created_at_utc": 0, "is_question": True,
        "sender_ip": "203.0.113.99",
    }
    first, created = service.send(username="MiXeD.User", sender_ip="10.1.123.12", payload=payload, locale="en")
    retried, retry_created = service.send(username="MiXeD.User", sender_ip="10.9.9.9", payload=payload, locale="en")

    assert created is True and retry_created is False
    assert retried["message"] == first["message"]
    assert first["message"]["username"] == "MiXeD.User"
    assert first["message"]["sender_ip"] == "10.1.123.12"
    assert first["message"]["body"] == payload["body"]
    assert first["message"]["created_at_utc"] != 0
    assert first["message"]["is_question"] is False
    send_metrics = service.diagnostics()["operations"]["send"]
    assert send_metrics["count"] == 2 and send_metrics["rows"] == 2
    assert send_metrics["bytes"] > 0 and send_metrics["latency_ms_max"] >= 0
    assert payload["body"] not in str(send_metrics)


def test_chat_service_two_users_keep_private_read_state_while_same_person_browsers_share_it(tmp_path):
    service = _service(tmp_path)
    assert service.bootstrap(username="alice", browser_instance_id="browser-a1")["messages"] == []
    service.send(username="alice", payload={"browser_instance_id": "browser-a2", "client_message_uuid": "m-a2", "body": "hello from another browser"}, locale="en")
    service.send(username="bob", payload={"browser_instance_id": "browser-b", "client_message_uuid": "m-b", "body": "question?"}, locale="en")
    alice = service.bootstrap(username="alice", browser_instance_id="browser-a1")
    other_alice = service.bootstrap(username="alice", browser_instance_id="browser-a2")
    bob = service.bootstrap(username="bob", browser_instance_id="browser-b")

    assert [message["username"] for message in alice["messages"]] == ["alice", "bob"]
    assert other_alice["first_registration"] is False
    assert [message["username"] for message in other_alice["messages"]] == ["alice", "bob"]
    assert bob["first_registration"] is True and bob["messages"] == []


def test_chat_service_bootstrap_exposes_only_real_older_history(tmp_path):
    service = _service(tmp_path)
    empty = service.bootstrap(username="alice", browser_instance_id="browser")
    assert empty["has_more_older"] is False and empty["older_cursor"] is None
    service.send(username="bob", payload={"browser_instance_id": "bob-browser", "client_message_uuid": "m-1", "body": "new"}, locale="en")
    unread = service.bootstrap(username="alice", browser_instance_id="browser")
    assert unread["has_more_older"] is False and unread["older_cursor"] is None
    service.send(username="bob", payload={"browser_instance_id": "bob-browser", "client_message_uuid": "m-2", "body": "newer"}, locale="en")
    service.read(username="alice", message_id=2)
    history = service.bootstrap(username="alice", browser_instance_id="browser")
    assert history["has_more_older"] is True and history["older_cursor"]


def test_chat_service_yoagent_source_is_owned_and_reply_is_idempotent(tmp_path):
    service = _service(tmp_path)
    sent, _created = service.send(
        username="guest",
        sender_ip="10.1.2.3",
        payload={"browser_instance_id": "browser-a", "client_message_uuid": "m-yo", "body": "/yo summarize current tasks"},
        locale="en",
    )
    source, query = service.yoagent_source(username="guest", browser_instance_id="browser-a", message_id=sent["message"]["id"])
    assert query == "summarize current tasks"
    first, created = service.record_yoagent_reply(source=source, answer="Current tasks…")
    duplicate, duplicate_created = service.record_yoagent_reply(source=source, answer="must not replace")
    assert created is True and duplicate_created is False
    assert duplicate["message"] == first["message"]
    assert first["message"]["username"] == "YO!agent"
    assert first["message"]["sender_ip"] == ""
    with pytest.raises(ChatServiceError, match="does not belong"):
        service.yoagent_source(username="other", browser_instance_id="browser-a", message_id=source.id)


def test_chat_service_signed_cursor_rejects_invalid_and_forged_values(tmp_path):
    service = _service(tmp_path)
    service.send(username="alice", payload={"browser_instance_id": "browser-a", "client_message_uuid": "m-1", "body": "hello"}, locale="en")
    assert service.page(username="alice", limit=1)["messages"][0]["body"] == "hello"
    with pytest.raises(ChatServiceError, match="invalid chat cursor"):
        service.page(username="alice", before="not-a-cursor")
    token = service.cursor_codec.encode("older", 1)
    forged = token[:-1] + ("A" if token[-1] != "A" else "B")
    with pytest.raises(ChatServiceError, match="invalid chat cursor"):
        service.page(username="alice", before=forged)
    with pytest.raises(ChatServiceError, match="invalid chat cursor"):
        service.delta(username="alice", after=token)


def test_chat_service_context_search_and_stable_tie_order(tmp_path):
    store = ChatStore(tmp_path / "yochat.sqlite3", clock=lambda: 2_000_000.0)
    service = ChatService(store, cursor_secret_path=tmp_path / "chat-cursor.key")
    ids = []
    for index, body in enumerate(("alpha 😀", "middle", "alpha 👩‍💻")):
        message, _ = store.insert_message(username="alice", sender_instance_id="browser-a", client_message_uuid=f"m-{index}", body=body, is_question=False, created_at_utc=1_999_999.0)
        ids.append(message.id)
    assert [item["id"] for item in service.delta(username="alice", after="")["messages"]] == ids
    assert service.context(username="alice", message_id=ids[1])["target"]["body"] == "middle"
    assert [hit["target"]["body"] for hit in service.search(username="alice", query="alpha")["hits"]] == ["alpha 👩‍💻", "alpha 😀"]
    service.page(username="alice", limit=2)
    diagnostics = service.diagnostics()
    assert diagnostics["operations"]["page"]["rows"] == 2
    assert diagnostics["operations"]["search"]["rows"] == 2
    assert diagnostics["operations"]["page"]["bytes"] > 0
    assert diagnostics["operations"]["search"]["latency_ms_max"] >= 0
    assert "alpha" not in str(diagnostics)


def test_chat_service_limits_ids_bodies_and_rate(tmp_path):
    now = [100.0]
    service = _service(tmp_path, clock=lambda: now[0])
    with pytest.raises(ChatServiceError, match="invalid browser instance ID"):
        service.send(username="alice", payload={"browser_instance_id": "bad id", "client_message_uuid": "m", "body": "hello"}, locale="en")
    with pytest.raises(ChatServiceError, match="8 KiB"):
        service.send(username="alice", payload={"browser_instance_id": "browser-a", "client_message_uuid": "large", "body": "😀" * 3000}, locale="en")
    for index in range(20):
        service.send(username="rate-user", payload={"browser_instance_id": "browser-a", "client_message_uuid": f"m-{index}", "body": "ok"}, locale="en")
    with pytest.raises(ChatServiceError) as caught:
        service.send(username="rate-user", payload={"browser_instance_id": "browser-a", "client_message_uuid": "m-over", "body": "no"}, locale="en")
    assert caught.value.status == 429
    now[0] += 61
    service.send(username="rate-user", payload={"browser_instance_id": "browser-a", "client_message_uuid": "m-later", "body": "ok"}, locale="en")


def test_chat_service_exact_emoji_matrix_survives_send_delta_context_and_search(tmp_path):
    service = _service(tmp_path)
    ids = []
    for index, body in enumerate(EMOJI_MATRIX):
        response, created = service.send(
            username="alice",
            payload={"browser_instance_id": "emoji-browser", "client_message_uuid": f"emoji-{index}", "body": body},
            locale="ar" if body.startswith("مرحبا") else "en",
        )
        assert created is True
        assert response["message"]["body"] == body
        ids.append(response["message"]["id"])

    delta = service.delta(username="alice", after="")
    assert [message["body"] for message in delta["messages"]] == list(EMOJI_MATRIX)
    for message_id, body in zip(ids, EMOJI_MATRIX, strict=True):
        assert service.context(username="alice", message_id=message_id, before=0, after=0)["target"]["body"] == body
        assert body in [hit["target"]["body"] for hit in service.search(username="alice", query=body, limit=10)["hits"]]

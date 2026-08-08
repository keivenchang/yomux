import json
from http import HTTPStatus
from types import SimpleNamespace

import pytest

from yolomux_lib import app as app_module
from yolomux_lib import statusd
from yolomux_lib import statusd_client
from yolomux_lib import web


def test_activity_summary_app_boundaries_fail_closed_before_statusd_or_assembly():
    webapp = object.__new__(app_module.TmuxWebtermApp)
    webapp.status_client = SimpleNamespace(
        activity_summary=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("disabled activity summary must not launch or call statusd")
        )
    )
    webapp.activity_session_names = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("disabled activity summary must not begin assembly")
    )

    body, response_status = webapp.activity_summary_bytes(force=True, locale="ja", session_scope="all", hours=336)

    assert response_status == HTTPStatus.SERVICE_UNAVAILABLE
    assert json.loads(body) == {
        "status": "feature_disabled",
        "code": "feature_disabled",
        "reason": "async_replacement_required",
        "retryable": False,
        "terminal": True,
    }
    with pytest.raises(Exception, match="async_replacement_required"):
        webapp.activity_summary_payload(force=True)
    with pytest.raises(Exception, match="async_replacement_required"):
        webapp.assemble_activity_summary_payload(force=True)


def test_activity_summary_status_client_and_daemon_fail_before_rpc_decode_or_app_work(monkeypatch):
    client = object.__new__(statusd_client.StatusClient)
    monkeypatch.setattr(
        client,
        "ensure_started",
        lambda: (_ for _ in ()).throw(AssertionError("disabled activity summary must not ensure statusd")),
    )
    response, body = client.activity_summary(
        ["1"],
        force=True,
        locale="en",
        session_scope="all",
        hours=24,
        work_by_session={"1": {}},
    )
    assert response["status"] == HTTPStatus.SERVICE_UNAVAILABLE
    assert json.loads(body)["reason"] == "async_replacement_required"

    service = object.__new__(statusd.PersistentStatusService)
    service._sessions = lambda _request: (_ for _ in ()).throw(
        AssertionError("disabled daemon action must not decode sessions")
    )
    metadata, daemon_body = service._activity_summary({"sessions": ["1"]}, b"not-json")
    assert metadata["status"] == HTTPStatus.SERVICE_UNAVAILABLE
    assert json.loads(daemon_body)["reason"] == "async_replacement_required"


def test_activity_summary_watch_demand_and_publication_are_disabled_before_work():
    webapp = object.__new__(app_module.TmuxWebtermApp)
    for revision in range(10):
        assert webapp.normalized_client_activity_summary({
            "visible": True,
            "locale": "ja",
            "scope": "all",
            "hours": 336,
            "revision": revision,
        }) == {}
    webapp.prune_client_watch_descriptors = lambda: (_ for _ in ()).throw(
        AssertionError("disabled activity publication must not inspect watch demand")
    )
    for revision in range(10):
        assert webapp.publish_activity_summary_ready_events(trigger="watch_state") == []


@pytest.mark.parametrize("bootstrap_value", [None, {}, {"enabled": False}, {"enabled": 1}, {"enabled": "true"}])
def test_activity_summary_html_bootstrap_is_fail_closed(bootstrap_value, monkeypatch):
    if bootstrap_value is not None:
        monkeypatch.setattr(web, "activity_summary_bootstrap", lambda: bootstrap_value)
    page = web.html_page([])
    marker = '<script id="yolomux-bootstrap" type="application/json">'
    payload = json.loads(page.split(marker, 1)[1].split("</script>", 1)[0])
    assert payload["activitySummary"] == {"enabled": False, "reason": "async_replacement_required"}

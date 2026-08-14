import importlib.util
import hashlib
import json
import math
import re
import subprocess
import threading
import copy
from pathlib import Path

import pytest
from urllib.parse import urlsplit

from yolomux_lib import live_browser_soak as soak
from yolomux_lib import browser_diagnostic_receipts
from tests.browser_helpers.browser_layout import browser  # noqa: F401
from tests.gate_harness import gate_live_server  # noqa: F401
from tests.gate_harness import gate_runtime_paths  # noqa: F401
from tests.gate_harness import gate_http_port  # noqa: F401
from tests.gate_harness import gate_tmux  # noqa: F401
from tests.gate_harness import load_gate_browser


TOOLS_MODULE = importlib.util.spec_from_file_location("live_browser_soak_tool", Path(__file__).parents[1] / "tools" / "live_browser_soak.py")
assert TOOLS_MODULE is not None and TOOLS_MODULE.loader is not None
tool = importlib.util.module_from_spec(TOOLS_MODULE)
TOOLS_MODULE.loader.exec_module(tool)


RECORDED_LAYOUT_URL = (
    "https://localhost:7443/?sessions=differ,yo7771,debug"
    "&layout=row@18(slot1,row@48(left,slot2))"
    "&tabs=slot1:@side-left,finder,differ*,tabber;left:yo7771,yo7775;slot2:debug"
    "&state=%7B%22v%22%3A1%2C%22finder%22%3A%7B%22root%22%3A%22%2Fhome%2Fkeivenc%22%7D%7D"
)


def acknowledge_and_consume_only_expected_js_debug_failures(driver, expected):
    """Consume the exact failures created by this isolated forward-port test."""

    event_ids = [entry["id"] for entry in expected]
    consumed = driver.execute_script(
        """
        const ids = new Set(arguments[0]);
        const before = jsDebugEvents.length;
        for (let index = jsDebugEvents.length - 1; index >= 0; index -= 1) {
          if (ids.has(jsDebugEvents[index]?.id)) jsDebugEvents.splice(index, 1);
        }
        return before - jsDebugEvents.length;
        """,
        event_ids,
    )
    assert consumed == len(event_ids)


def clean_receipt_barrier(epoch="page-1", accepted=0):
    return {"epoch": epoch, "accepted": accepted, "pending": 0, "retrying": 0, "rejected": 0, "dropped": 0, "quiescent": True, "blocking": []}


def test_validate_arguments_rejects_unsafe_scope_and_short_duration(tmp_path):
    safe_url = "https://localhost:7443/?sessions=files%2C1&layout=row%4050%28left%2Cright%29"
    soak.validate_arguments(safe_url, 600, "a" * 40, "b" * 64, Path("/tmp/result.json"))
    with pytest.raises(ValueError, match="canonical"):
        soak.validate_arguments("https://example.test:7443/", 600, "a" * 40, "b" * 64, Path("/tmp/result.json"))
    with pytest.raises(ValueError, match="at least 600"):
        soak.validate_arguments("https://localhost:7443/", 599, "a" * 40, "b" * 64, Path("/tmp/result.json"))
    with pytest.raises(ValueError, match="under /tmp"):
        soak.validate_arguments("https://localhost:7443/", 600, "a" * 40, "b" * 64, Path("/var/tmp/result.json"))
    with pytest.raises(ValueError, match="canonical"):
        soak.validate_arguments("https://localhost:7443/app", 600, "a" * 40, "b" * 64, Path("/tmp/result.json"))
    with pytest.raises(ValueError, match="lowercase"):
        soak.validate_arguments("https://localhost:7443/", 600, "A" * 40, "b" * 64, Path("/tmp/result.json"))
    for unsafe in (
        "https://user:password@localhost:7443/?sessions=1&layout=left",
        "https://localhost/?sessions=1&layout=left",
        "https://localhost:7443/app?sessions=1&layout=left",
        "https://localhost:7443/?sessions=1&layout=left#token=secret",
        "https://localhost:7443/?sessions=1&layout=left&token=secret",
        "https://localhost:7443/?layout=left&sessions=1",
        "https://localhost:7443/?sessions=1&sessions=2&layout=left",
        "https://localhost:7443/?sessions=1&layout=left&tabs=slot1:1",
        "https://localhost:7443/?sessions=1&layout=left&state=%7B%22v%22%3A1%7D&tabs=slot1:1",
        "https://localhost:7443/?sessions=1&layout=left&tabs=slot1:1&state=%7B%22v%22%3A2%7D",
        "https://localhost:7443/?sessions=1&layout=left&tabs=slot1:1&state=%7Binvalid",
        "https://localhost:7443/?sessions=1&layout=left&tabs=slot1%ZZ&state=%7B%22v%22%3A1%7D",
    ):
        with pytest.raises(ValueError, match="canonical|explicit HTTPS port"):
            soak.validate_arguments(unsafe, 600, "a" * 40, "b" * 64, Path("/tmp/result.json"))


def test_validate_arguments_accepts_exact_recorded_layout_url_without_rewriting_query():
    soak.validate_arguments(
        RECORDED_LAYOUT_URL,
        600,
        "a" * 40,
        "b" * 64,
        Path("/tmp/result.json"),
    )


def test_main_forwards_and_records_exact_recorded_layout_url(tmp_path, monkeypatch):
    forwarded = {}

    class Options:
        def add_argument(self, _argument):
            return None

        def set_capability(self, _name, _value):
            return None

    class Driver:
        def set_page_load_timeout(self, _timeout):
            return None

        def set_script_timeout(self, _timeout):
            return None

    def run_soak(_driver, **kwargs):
        forwarded.update(kwargs)
        return {"url": kwargs["url"], "samples": []}

    output = tmp_path / "exact-layout-url.json"
    monkeypatch.setattr(tool, "webdriver", type("WebDriver", (), {"ChromeOptions": Options, "Chrome": lambda *_args, **_kwargs: Driver()})())
    monkeypatch.setattr(tool, "run_soak", run_soak)
    monkeypatch.setattr(tool, "cleanup_driver", lambda _driver: None)
    monkeypatch.setattr(tool, "evidence_failed", lambda _artifact: False)
    monkeypatch.setattr(tool, "validate_success_artifact", lambda _artifact: None)

    assert tool.main([
        "--url", RECORDED_LAYOUT_URL,
        "--duration", "600",
        "--expected-head", "a" * 40,
        "--expected-bundle-sha256", "b" * 64,
        "--expected-cwd", "/repo",
        "--output", str(output),
    ]) == 0
    assert forwarded["url"] == RECORDED_LAYOUT_URL
    assert json.loads(output.read_text(encoding="utf-8"))["url"] == RECORDED_LAYOUT_URL


class CookieJarDriver:
    """Model the browser cookie jar instead of accepting every add_cookie call.

    Chrome's cookie store is the authority: `add_cookie` can raise (`unable to set cookie`) and it
    can also report success while the store keeps nothing usable for the current origin, which is
    how an unauthenticated soak used to reach `assert_page_identity` 90 seconds later. `get_cookies`
    returns only what the document's origin can actually send, exactly like WebDriver does.
    """

    def __init__(self, *, store=lambda cookie: cookie, error=None):
        self.navigations = []
        self.cookies = []
        self._store = store
        self._error = error

    def get(self, url):
        self.navigations.append(url)

    def add_cookie(self, cookie):
        if self._error is not None:
            raise self._error
        stored = self._store(dict(cookie))
        if stored is not None:
            self.cookies = [entry for entry in self.cookies if entry["name"] != stored["name"]] + [stored]

    def get_cookies(self):
        return [dict(entry) for entry in self.cookies]


def test_install_auth_cookie_uses_login_without_losing_exact_safe_app_url(monkeypatch):
    driver = CookieJarDriver()
    user = soak.AuthUser(username="admin", password="password", role="admin")
    monkeypatch.setattr(soak, "select_auth_user", lambda: user)
    monkeypatch.setattr(soak, "auth_cookie_value", lambda _username, _password: "c" * 64)
    exact_url = "https://localhost:7443/?sessions=files%2C1&layout=row%4050%28left%2Cright%29"

    assert soak.install_auth_cookie(driver, exact_url, 7443) == user
    assert driver.navigations == ["https://localhost:7443/login"]
    assert driver.get_cookies() == [{"name": "yolomux_auth_7443", "value": "c" * 64, "path": "/", "secure": True, "httpOnly": True}]


@pytest.mark.parametrize(
    ("label", "store"),
    (
        ("silently dropped", lambda _cookie: None),
        ("stored for another origin", lambda cookie: {**cookie, "name": "yolomux_auth_7444"}),
        ("stored without the Secure attribute", lambda cookie: {**cookie, "secure": False}),
        ("stored with a truncated value", lambda cookie: {**cookie, "value": cookie["value"][:32]}),
    ),
)
def test_install_auth_cookie_fails_when_the_browser_does_not_retain_the_auth_cookie(monkeypatch, label, store):
    """A refused or silently dropped cookie must fail here, not as a page-identity mismatch minutes later."""

    driver = CookieJarDriver(store=store)
    user = soak.AuthUser(username="admin", password="password", role="admin")
    monkeypatch.setattr(soak, "select_auth_user", lambda: user)
    monkeypatch.setattr(soak, "auth_cookie_value", lambda _username, _password: "c" * 64)

    with pytest.raises(RuntimeError, match="browser did not retain the authenticated YOLOmux session cookie"):
        soak.install_auth_cookie(driver, "https://localhost:7443/", 7443)


def test_install_auth_cookie_never_reports_the_session_cookie_value(monkeypatch):
    driver = CookieJarDriver(store=lambda _cookie: None)
    user = soak.AuthUser(username="admin", password="password", role="admin")
    monkeypatch.setattr(soak, "select_auth_user", lambda: user)
    monkeypatch.setattr(soak, "auth_cookie_value", lambda _username, _password: "c" * 64)

    with pytest.raises(RuntimeError) as failure:
        soak.install_auth_cookie(driver, "https://localhost:7443/", 7443)
    assert "c" * 64 not in str(failure.value)
    assert "yolomux_auth_7443" in str(failure.value)


def test_install_auth_cookie_propagates_a_browser_cookie_refusal(monkeypatch):
    """Chrome answers `unable to set cookie` with a WebDriverException; the gate must not swallow it."""

    driver = CookieJarDriver(error=soak.WebDriverException("unable to set cookie"))
    user = soak.AuthUser(username="admin", password="password", role="admin")
    monkeypatch.setattr(soak, "select_auth_user", lambda: user)

    with pytest.raises(soak.WebDriverException, match="unable to set cookie"):
        soak.install_auth_cookie(driver, "https://localhost:7443/", 7443)


def test_evidence_failed_classifies_drops_and_failures():
    assert not soak.evidence_failed({"browserLocalFailures": [], "browserReceiptBarrier": clean_receipt_barrier(), "serverLogErrors": [], "browserLogFailures": [], "serverLogDropped": {"count": 0}})
    assert soak.evidence_failed({"browserLocalFailures": [], "serverLogErrors": [], "browserLogFailures": [], "integrityFailures": ["server log ring dropped records"], "serverLogDropped": {"count": 1}})
    assert soak.evidence_failed({"browserLocalFailures": [{"route": "/x"}], "serverLogErrors": [], "browserLogFailures": [], "serverLogDropped": {"count": 0}})
    assert soak.evidence_failed({"phase": "runtime", "terminal": "RuntimeError", "message": "failed"})


@pytest.mark.parametrize("payload", [
    {"ok": True, "logs": ["not-a-record"], "dropped": {"count": 0}, "epoch": "epoch", "sequence": 0},
    {"ok": True, "logs": [], "dropped": {"count": False}, "epoch": "epoch", "sequence": 0},
])
def test_sample_evidence_fails_closed_for_malformed_server_shapes(payload):
    class EvidenceDriver:
        def execute_async_script(self, _script):
            return {"events": [], "failures": [], "receiptBarrier": clean_receipt_barrier(), "logsStatus": 200, "payload": payload}

        def get_log(self, _name):
            return []
    with pytest.raises(AssertionError, match="malformed"):
        soak.sample_evidence(EvidenceDriver())


def test_incremental_classification_ignores_historical_baseline_failure_but_rejects_ring_reset():
    baseline = {"browserLocalFailures": [{"id": 4}], "serverLogErrors": [{"id": 9}], "browserLogFailures": [], "serverLogDropped": {"count": 2}, "cursors": {"js": 4, "server_epoch": "a", "server_sequence": 9, "server_log_ids": [9]}}
    state = soak.evidence_baseline(baseline)
    clean = {"browserLocalFailures": [{"id": 4}], "browserReceiptBarrier": clean_receipt_barrier(), "serverLogErrors": [{"id": 9}], "browserLogFailures": [], "serverLogDropped": {"count": 2}, "cursors": {"js": 4, "server_epoch": "a", "server_sequence": 9, "server_log_ids": [9]}}
    assert not soak.evidence_failed(soak.classify_incremental_evidence(clean, state)[0])
    reset = {**clean, "cursors": {**clean["cursors"], "server_epoch": "b"}}
    classified, _state = soak.classify_incremental_evidence(reset, state)
    assert classified["integrityFailures"] == ["server log epoch changed"]


def test_incremental_evidence_rejects_drop_increase_and_browser_ring_eviction():
    baseline = {"browserLocalFailures": [], "serverLogErrors": [], "browserLogFailures": [], "serverLogDropped": {"count": 0}, "cursors": {"js": 5, "server_epoch": "a", "server_sequence": 5, "server_log_ids": [5]}}
    sample = {"browserEvents": [{"id": 7}], "browserLocalFailures": [], "serverLogErrors": [], "browserLogFailures": [], "serverLogDropped": {"count": 1}, "cursors": {"js": 7, "server_epoch": "a", "server_sequence": 7, "server_log_ids": [7]}}
    classified, _state = soak.classify_incremental_evidence(sample, soak.evidence_baseline(baseline))
    assert set(classified["integrityFailures"]) == {"server log ring dropped records", "browser JS ring eviction or cursor gap", "server log ring eviction"}


def test_incremental_evidence_preserves_existing_integrity_findings():
    baseline = {"browserLocalFailures": [], "serverLogErrors": [], "browserLogFailures": [], "serverLogDropped": {"count": 0}, "cursors": {"js": 0, "server_epoch": "a", "server_sequence": 0, "server_log_ids": []}}
    sample = {**baseline, "browserEvents": [], "integrityFailures": ["browser recorded failing diagnostics during retirement: 1"]}

    classified, _state = soak.classify_incremental_evidence(sample, soak.evidence_baseline(baseline))

    assert classified["integrityFailures"] == ["browser recorded failing diagnostics during retirement: 1"]


@pytest.mark.parametrize("cursors", [
    {"js": False, "server_epoch": "a", "server_sequence": 0, "server_log_ids": []},
    {"js": 0, "server_epoch": "", "server_sequence": 0, "server_log_ids": []},
    {"js": 0, "server_epoch": "a", "server_sequence": -1, "server_log_ids": []},
    {"js": 0, "server_epoch": "a", "server_sequence": 1, "server_log_ids": [2, 1]},
])
def test_evidence_baseline_rejects_malformed_cursor_identity(cursors):
    with pytest.raises(AssertionError, match="cursor evidence"):
        soak.evidence_baseline({"serverLogDropped": {"count": 0}, "cursors": cursors})


def test_redact_chrome_query_credentials():
    canaries = ("query-canary-one", "query-canary-two")
    redacted = soak.redact_log_entry({"message": f"GET /x?token={canaries[0]}&api_key={canaries[1]}"})
    assert all(canary not in redacted["message"] for canary in canaries)
    assert "token=[redacted" in redacted["message"]
    assert "api_key=[redacted" in redacted["message"]


def test_cleanup_driver_terminates_only_its_webdriver_service_when_quit_hangs():
    # Migrated from the pinned process.terminate() contract to the shared lease's equivalent: a hung
    # quit must not become the next orphan owner. The lease TERM/KILLs the EXACT chromedriver service
    # process it captured a generation for and proves it gone - here a real subprocess standing in for
    # chromedriver, so the assertion is that the leased process is actually reaped, not merely poked.
    service_process = subprocess.Popen(["sleep", "60"])
    try:
        class Driver:
            def __init__(self):
                self.service = type("Service", (), {"process": service_process})()
            def quit(self):
                threading.Event().wait(60)
        error = tool.cleanup_driver(Driver(), timeout_seconds=0.1)
        assert error["terminal"] == "WebDriverCleanupTimeout"
        assert "terminated its WebDriver service process" in error["message"]
        # The lease proved it gone by actually signalling the captured PID - the process is reaped.
        assert service_process.wait(timeout=5) is not None
    finally:
        if service_process.poll() is None:
            service_process.kill()
            service_process.wait(timeout=5)


def test_cleanup_timeout_is_preserved_as_a_typed_terminal_artifact(tmp_path, monkeypatch):
    class Options:
        def add_argument(self, _arg):
            return None

        def set_capability(self, _name, _value):
            return None

    class Driver:
        def set_page_load_timeout(self, _timeout):
            return None

        def set_script_timeout(self, _timeout):
            return None

    output = tmp_path / "artifact.json"
    monkeypatch.setattr(tool, "webdriver", type("WebDriver", (), {"ChromeOptions": Options, "Chrome": lambda *_args, **_kwargs: Driver()})())
    monkeypatch.setattr(tool, "validate_arguments", lambda *_args: None)
    monkeypatch.setattr(tool, "run_soak", lambda *_args, **_kwargs: {"samples": []})
    monkeypatch.setattr(tool, "cleanup_driver", lambda _driver: {"phase": "cleanup", "terminal": "WebDriverCleanupTimeout", "message": "driver quit exceeded cleanup deadline; terminated its WebDriver service process"})
    assert tool.main(["--url", "https://localhost:7443/", "--duration", "600", "--expected-head", "a" * 40, "--expected-bundle-sha256", "b" * 64, "--expected-cwd", "/repo", "--output", str(output)]) == 1
    artifact = json.loads(output.read_text(encoding="utf-8"))
    assert artifact["cleanup_failure"]["terminal"] == "WebDriverCleanupTimeout"


def test_main_persists_cleanup_exception_and_returns_nonzero(tmp_path, monkeypatch):
    class Options:
        def add_argument(self, _arg): return None
        def set_capability(self, _name, _value): return None

    class Driver:
        def set_page_load_timeout(self, _timeout): return None
        def set_script_timeout(self, _timeout): return None
        def get_log(self, _name): return []

    output = tmp_path / "artifact.json"
    monkeypatch.setattr(tool, "webdriver", type("WebDriver", (), {"ChromeOptions": Options, "Chrome": lambda *_args, **_kwargs: Driver()})())
    monkeypatch.setattr(tool, "validate_arguments", lambda *_args: None)
    monkeypatch.setattr(tool, "run_soak", lambda *_args, **_kwargs: {"samples": []})
    monkeypatch.setattr(tool, "cleanup_driver", lambda _driver: (_ for _ in ()).throw(RuntimeError("cleanup failed")))
    assert tool.main(["--url", "https://localhost:7443/", "--duration", "600", "--expected-head", "a" * 40, "--expected-bundle-sha256", "b" * 64, "--expected-cwd", "/repo", "--output", str(output)]) == 1
    artifact = json.loads(output.read_text(encoding="utf-8"))
    assert artifact["cleanup_failure"]["terminal"] == "RuntimeError"


@pytest.mark.parametrize("phase", ["preflight", "runtime"])
def test_terminal_exception_artifacts_are_typed_mode_0600_and_redacted(tmp_path, phase):
    output = tmp_path / f"{phase}.json"
    artifact = {"failure": soak.terminal_failure(phase, RuntimeError("Bearer top-secret token=also-secret password=hush"))}
    soak.write_artifact(output, artifact)
    persisted = json.loads(output.read_text(encoding="utf-8"))
    assert persisted["failure"]["phase"] == phase
    assert persisted["failure"]["terminal"] == "RuntimeError"
    assert all(secret not in persisted["failure"]["message"] for secret in ("top-secret", "also-secret", "hush"))
    assert persisted["failure"]["message"].count("[redacted") == 3
    assert output.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("phase", ["preflight", "runtime"])
def test_main_terminal_exceptions_fail_and_redact_every_artifact_channel(tmp_path, monkeypatch, phase):
    class Options:
        def add_argument(self, _arg):
            return None

        def set_capability(self, _name, _value):
            return None

    class Driver:
        def set_page_load_timeout(self, _timeout):
            return None

        def set_script_timeout(self, _timeout):
            return None

    secret = "Bearer browser-secret password=server-secret"
    output = tmp_path / f"{phase}.json"
    monkeypatch.setattr(tool, "cleanup_driver", lambda _driver: None)
    if phase == "preflight":
        monkeypatch.setattr(tool, "validate_arguments", lambda *_args: (_ for _ in ()).throw(RuntimeError(secret)))
    else:
        monkeypatch.setattr(tool, "webdriver", type("WebDriver", (), {"ChromeOptions": Options, "Chrome": lambda *_args, **_kwargs: Driver()})())
        monkeypatch.setattr(tool, "validate_arguments", lambda *_args: None)
        monkeypatch.setattr(tool, "run_soak", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError(secret)))
    assert tool.main(["--url", "https://localhost:7443/", "--duration", "600", "--expected-head", "a" * 40, "--expected-bundle-sha256", "b" * 64, "--expected-cwd", "/repo", "--output", str(output)]) == 1
    persisted = json.loads(output.read_text(encoding="utf-8"))
    assert persisted["failure"]["phase"] == phase
    assert persisted["failure"]["terminal"] == "RuntimeError"
    assert all(secret not in persisted["failure"]["message"] for secret in ("browser-secret", "server-secret"))
    assert persisted["failure"]["message"].count("[redacted") == 2
    assert output.stat().st_mode & 0o777 == 0o600


def test_write_artifact_redacts_every_retained_evidence_channel(tmp_path):
    output = tmp_path / "artifact.json"
    artifact = {
        "browserEvents": [{"message": "Bearer browser-secret"}],
        "browserLocalFailures": [{"message": "password=local-secret"}],
        "serverLogErrors": [{"message": "token=server-secret"}],
        "browserLogFailures": [{"message": "authorization: chrome-secret"}],
        "failure": {"message": "cookie=terminal-secret"},
    }
    soak.write_artifact(output, artifact)
    saved = output.read_text(encoding="utf-8")
    for secret in ("browser-secret", "local-secret", "server-secret", "chrome-secret", "terminal-secret"):
        assert secret not in saved
    restored = json.loads(saved)
    for channel in ("browserEvents", "browserLocalFailures", "serverLogErrors", "browserLogFailures"):
        assert "[redacted" in restored[channel][0]["message"]
    assert "[redacted" in restored["failure"]["message"]


def receipt_row(event_id=1, status="accepted", *, epoch="page-1", request_id="", source="/", route="/", event="error", wall_time="", delivery="failed", http_status=None):
    return {"key": f"{epoch}:{event_id}", "epoch": epoch, "eventId": event_id, "requestId": request_id, "source": source, "route": route, "event": event, "wallTime": wall_time, "deliveryOutcome": delivery, "httpStatus": http_status, "status": status}


def receipt_projection(receipts=(), *, epoch="page-1"):
    values = [dict(receipt) for receipt in receipts]
    counts = {status: sum(receipt["status"] == status for receipt in values) for status in ("accepted", "pending", "retrying", "rejected", "dropped")}
    blocking = [receipt for receipt in values if receipt["status"] != "accepted"]
    return {"receipts": values, "barrier": {"epoch": epoch, **counts, "quiescent": not blocking, "blocking": blocking}}


def clean_stats_stream(
    delivery_sequence=1,
    accepted_delta_sequence=0,
    cache_generation=1,
    last_delivery_kind="ready",
    *,
    source_generation=None,
    delta_revision=None,
    painted_generation_key="",
):
    source_generation = cache_generation if source_generation is None else source_generation
    delta_revision = accepted_delta_sequence if delta_revision is None else delta_revision
    return {
        "moduleReady": True, "clientReady": True, "controllerReady": True, "generationReady": True,
        "panelVisible": False, "paintedGenerationKey": painted_generation_key, "sampledAtMs": 1000, "everVisible": False,
        "stream": {
            "running": True, "visible": True, "healthy": True, "streamOpen": True, "streamEpoch": 1,
            "deliverySequence": delivery_sequence, "acceptedDeltaSequence": accepted_delta_sequence,
            "lastDeliveryKind": last_delivery_kind, "lastDeliveryAtMs": 1000, "lastDeliveryEpoch": 1,
            "rangeSeconds": 300, "resolutionSeconds": 1, "sourceGeneration": source_generation,
            "cacheGeneration": cache_generation, "deltaRevision": delta_revision,
        },
    }


def clean_settle(sample):
    return {
        "settle_elapsed_seconds": 90.0,
        "settle": {"requested_seconds": 90.0, "started_pt": "start", "ended_pt": "end", "elapsed_seconds": 90.0, "status": "clean", "samples": [sample]},
    }


@pytest.mark.parametrize(
    "current",
    (
        clean_stats_stream(2, 2, 2, "delta", source_generation=1, delta_revision=2),
        clean_stats_stream(2, 2, 2, "delta", source_generation=2, delta_revision=1),
        clean_stats_stream(2, 2, 1, "delta", source_generation=2, delta_revision=2),
        clean_stats_stream(2, 2, 2, "delta", source_generation=2, delta_revision=2, painted_generation_key="painted-2"),
    ),
)
def test_hidden_stats_rejects_incoherent_delta_or_hidden_paint_advancement(current):
    previous = clean_stats_stream(1, 1, 1, "delta", source_generation=1, delta_revision=1, painted_generation_key="painted-1")
    _validated, integrity = soak.classify_hidden_stats_stream(current, previous)
    assert integrity


def test_authenticated_settle_is_measured_before_baseline_and_ignores_only_historical_server_rows(monkeypatch):
    identity = soak.ListenerIdentity(pid=1, cwd="/repo", started="now", head="a" * 40)
    historical = {"id": 1, "level": "warning", "message": "older unrelated warning"}
    sample = {
        "browserEvents": [],
        "browserLocalFailures": [],
        "browserReceiptBarrier": clean_receipt_barrier(),
        "browserReceiptProjection": receipt_projection(),
        "serverLogErrors": [historical],
        "browserLogFailures": [],
        "serverLogDropped": {"count": 0, "by_level": {}},
        "cursors": {
            "js": 0,
            "server_epoch": "server-a",
            "server_sequence": 1,
            "server_log_ids": [1],
            "server_log_records": [historical],
            "server_capacity": 10,
            "server_dropped_by_level": {},
        },
    }
    pre_page = {"epoch": "server-a", "sequence": 1, "capacity": 10, "ids": [1], "dropped": {"count": 0, "by_level": {}}, "logs": [historical]}
    checks = []
    sleeps = []

    class Driver:
        def execute_script(self, _script):
            return {"origin": "https://localhost:7443", "href": "https://localhost:7443/?sessions=1&layout=left", "visibility": "visible", "journeyId": "j-reload-settle"}

    monkeypatch.setattr(soak, "listener_identity", lambda _port: checks.append("identity") or identity)
    monkeypatch.setattr(soak, "assert_stats_hidden", lambda _driver: checks.append("hidden"))
    monkeypatch.setattr(soak, "sample_evidence", lambda _driver: copy.deepcopy(sample))
    monkeypatch.setattr(soak, "sample_hidden_stats_stream", lambda _driver: clean_stats_stream())
    baseline, _cursor, _stats, settle = soak.settle_authenticated_page(
        Driver(),
        expected_url="https://localhost:7443/?sessions=1&layout=left",
        expected_identity=identity,
        pre_page_server=pre_page,
        initial_stats=clean_stats_stream(),
        expected_journey_id="j-reload-settle",
        drift_record=soak.new_page_identity_drift_record(),
        sleep_fn=sleeps.append,
        monotonic_fn=iter((0.0, 0.0, 90.0)).__next__,
    )

    assert baseline is settle["samples"][-1]
    assert baseline["serverLogErrors"] == []
    assert settle["status"] == "clean" and settle["elapsed_seconds"] == 90.0
    assert sleeps == [5.0]
    assert checks == ["identity", "hidden", "identity", "hidden"]


def test_authenticated_settle_fails_page_load_browser_error_before_baseline(monkeypatch):
    identity = soak.ListenerIdentity(pid=1, cwd="/repo", started="now", head="a" * 40)
    page_error = {"id": 1, "level": "error", "message": "page load failed"}
    sample = {
        "browserEvents": [{"id": 1}],
        "browserLocalFailures": [page_error],
        "browserReceiptBarrier": clean_receipt_barrier(),
        "browserReceiptProjection": receipt_projection(),
        "serverLogErrors": [],
        "browserLogFailures": [],
        "serverLogDropped": {"count": 0, "by_level": {}},
        "cursors": {"js": 1, "server_epoch": "server-a", "server_sequence": 0, "server_log_ids": [], "server_log_records": [], "server_capacity": 10, "server_dropped_by_level": {}},
    }
    pre_page = {"epoch": "server-a", "sequence": 0, "capacity": 10, "ids": [], "dropped": {"count": 0, "by_level": {}}, "logs": []}

    class Driver:
        def execute_script(self, _script):
            return {"origin": "https://localhost:7443", "href": "https://localhost:7443/", "visibility": "visible", "journeyId": "j-reload-settle"}

    monkeypatch.setattr(soak, "listener_identity", lambda _port: identity)
    monkeypatch.setattr(soak, "assert_stats_hidden", lambda _driver: None)
    monkeypatch.setattr(soak, "sample_evidence", lambda _driver: copy.deepcopy(sample))
    monkeypatch.setattr(soak, "sample_hidden_stats_stream", lambda _driver: clean_stats_stream())
    baseline, _cursor, _stats, settle = soak.settle_authenticated_page(
        Driver(),
        expected_url="https://localhost:7443/",
        expected_identity=identity,
        pre_page_server=pre_page,
        initial_stats=clean_stats_stream(),
        expected_journey_id="j-reload-settle",
        drift_record=soak.new_page_identity_drift_record(),
        sleep_fn=lambda _seconds: pytest.fail("failed settle must not sleep"),
        monotonic_fn=iter((0.0, 0.0)).__next__,
    )

    assert baseline is None
    assert settle["status"] == "failed"
    assert settle["samples"][0]["browserLocalFailures"] == [page_error]


@pytest.mark.parametrize(
    ("phase", "error"),
    [
        ("preflight", OSError("chrome probe failed")),
        ("runtime", subprocess.TimeoutExpired(["lsof"], 3)),
        ("runtime", subprocess.CalledProcessError(1, ["git", "rev-parse", "HEAD"])),
    ],
)
def test_main_serializes_os_and_subprocess_probe_failures(tmp_path, monkeypatch, phase, error):
    class Options:
        def add_argument(self, _arg):
            return None

        def set_capability(self, _name, _value):
            return None

    class Driver:
        def set_page_load_timeout(self, _timeout):
            return None

        def set_script_timeout(self, _timeout):
            return None

    def raise_error(*_args, **_kwargs):
        raise error

    output = tmp_path / f"{phase}-{type(error).__name__}.json"
    monkeypatch.setattr(tool, "cleanup_driver", lambda _driver: None)
    monkeypatch.setattr(tool, "validate_arguments", lambda *_args: None)
    if phase == "preflight":
        monkeypatch.setattr(tool, "webdriver", type("WebDriver", (), {"ChromeOptions": Options, "Chrome": raise_error})())
    else:
        monkeypatch.setattr(tool, "webdriver", type("WebDriver", (), {"ChromeOptions": Options, "Chrome": lambda *_args, **_kwargs: Driver()})())
        monkeypatch.setattr(tool, "run_soak", raise_error)
    assert tool.main(["--url", "https://localhost:7443/", "--duration", "600", "--expected-head", "a" * 40, "--expected-bundle-sha256", "b" * 64, "--expected-cwd", "/repo", "--output", str(output)]) == 1
    artifact = json.loads(output.read_text(encoding="utf-8"))
    assert artifact["failure"]["phase"] == phase
    assert artifact["failure"]["terminal"] == type(error).__name__
    assert artifact["url"] == "https://localhost:7443/"
    assert output.stat().st_mode & 0o777 == 0o600


def test_write_artifact_redacts_nested_key_context_and_basic_credentials(tmp_path):
    output = tmp_path / "artifact.json"
    canaries = ("nested-password", "nested-secret", "nested-token", "basic-credential", "quoted-password", "spaced-password", "bearer-credential")
    artifact = {
        "browserEvents": [{"context": {"password": "nested-password", "client_secret": "nested-secret", "access_token": "nested-token", "headers": {"Authorization": "Basic basic-credential"}}}],
        "browserLocalFailures": [{"message": '{"password":"quoted-password"} password = spaced-password Authorization: Basic basic-credential Bearer bearer-credential'}],
    }
    soak.write_artifact(output, artifact)
    saved = output.read_text(encoding="utf-8")
    assert all(canary not in saved for canary in canaries)
    restored = json.loads(saved)
    assert restored["browserEvents"][0]["context"]["password"].startswith("[redacted")
    assert restored["browserEvents"][0]["context"]["headers"]["Authorization"].startswith("[redacted")


def test_shared_redactor_covers_probe_channels_and_keeps_the_matching_logs_row(tmp_path):
    output = tmp_path / "artifact.json"
    canary = "P0-LIVE-SOAK-CANARY-DO-NOT-RETAIN"
    diagnostic_url = f"https://localhost:7443/api/diagnostic?token={canary}#token={canary}"
    row = {
        "requestId": "r-web-controlled-1",
        "source": "browser",
        "route": soak.NEGATIVE_ROUTE,
        "event": "api",
        "wallTime": "2026-08-06 13:00:00 PDT",
        "deliveryOutcome": "failed",
        "message": f"controlled browser failure {diagnostic_url}",
    }
    artifact = {
        "browserProbe": {
            "dom": f"<article data-js-debug-log-entry>{diagnostic_url}</article>",
            "clipboard": f"row token={canary}",
            "retained": [{**row, "credentials": {"password": canary, "authorization": f"Bearer {canary}"}}],
            "upload": {"observations": [{"diagnostic_url": diagnostic_url, "api_key": canary}]},
            "storage": {"debug": f"{diagnostic_url}&api_key={canary}"},
        },
        "expected_failure_durably_detected": {"rendered": row},
    }

    soak.write_artifact(output, artifact)

    saved = output.read_text(encoding="utf-8")
    assert canary not in saved
    restored = json.loads(saved)
    persisted_row = restored["expected_failure_durably_detected"]["rendered"]
    assert {field: persisted_row[field] for field in ("requestId", "source", "route", "event", "wallTime", "deliveryOutcome")} == {field: row[field] for field in ("requestId", "source", "route", "event", "wallTime", "deliveryOutcome")}
    assert "[redacted" in persisted_row["message"]


def test_completion_probe_failure_retains_runtime_artifact_and_makes_main_fail(tmp_path, monkeypatch):
    identity = soak.ListenerIdentity(pid=12, cwd="/repo", started="now", head="a" * 40)
    samples = iter((
        {"browserEvents": [], "browserLocalFailures": [], "browserReceiptBarrier": clean_receipt_barrier(), "serverLogErrors": [], "browserLogFailures": [], "serverLogDropped": {"count": 0}, "cursors": {"js": 0, "server_epoch": "a", "server_sequence": 0, "server_log_ids": []}},
        {"browserEvents": [{"id": 1}], "browserLocalFailures": [], "browserReceiptBarrier": clean_receipt_barrier(), "serverLogErrors": [], "browserLogFailures": [], "serverLogDropped": {"count": 0}, "cursors": {"js": 1, "server_epoch": "a", "server_sequence": 0, "server_log_ids": []}},
        {"browserEvents": [{"id": 1}], "browserLocalFailures": [], "browserReceiptBarrier": clean_receipt_barrier(), "serverLogErrors": [], "browserLogFailures": [], "serverLogDropped": {"count": 0}, "cursors": {"js": 1, "server_epoch": "a", "server_sequence": 0, "server_log_ids": []}},
    ))
    identities = iter((identity, subprocess.TimeoutExpired(["lsof"], 3)))

    def completion_timeout(_port):
        outcome = next(identities)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(soak, "listener_identity", completion_timeout)
    monkeypatch.setattr(soak, "install_auth_cookie", lambda *_args: None)
    monkeypatch.setattr(soak, "install_start_of_document_sentinels", lambda *_args: None)
    monkeypatch.setattr(soak, "discover_served_bundle", lambda *_args: ("https://localhost:7443/static/yolomux.js", "b" * 64))
    monkeypatch.setattr(soak, "WebDriverWait", lambda *_args: type("Wait", (), {"until": lambda *_: None})())
    monkeypatch.setattr(soak, "assert_stats_hidden", lambda *_args: None)
    monkeypatch.setattr(soak, "sample_evidence", lambda *_args: next(samples))
    artifact = soak.run_soak(FakeDriver(), url="https://localhost:7443/", duration=0, expected_head="a" * 40, expected_bundle_sha256="b" * 64, expected_cwd="/repo", negative_probe=False, monotonic_fn=iter((0.0, 0.0, 0.1, 0.2)).__next__)
    assert artifact["identity"]["pid"] == 12
    assert artifact["baseline"]["cursors"]["js"] == 0
    assert len(artifact["samples"]) == 2
    assert artifact["failure"]["terminal"] == "TimeoutExpired"
    assert artifact["elapsed_seconds"] == 0.2

    output = tmp_path / "artifact.json"
    monkeypatch.setattr(tool, "validate_arguments", lambda *_args: None)
    monkeypatch.setattr(tool, "run_soak", lambda *_args, **_kwargs: artifact)
    assert tool.main(["--url", "https://localhost:7443/", "--duration", "600", "--expected-head", "a" * 40, "--expected-bundle-sha256", "b" * 64, "--expected-cwd", "/repo", "--output", str(output)]) == 1
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved["identity"]["pid"] == 12
    assert saved["failure"]["terminal"] == "TimeoutExpired"
    assert output.stat().st_mode & 0o777 == 0o600


def test_write_artifact_redacts_complete_ordinary_quoted_assignments(tmp_path):
    output = tmp_path / "artifact.json"
    canaries = ("first fragment", "second fragment", "third fragment", "fourth fragment")
    soak.write_artifact(output, {"browserEvents": [{"message": 'password="first fragment second fragment" password=\'third fragment fourth fragment\''}]})
    saved = output.read_text(encoding="utf-8")
    assert all(canary not in saved for canary in canaries)
    message = json.loads(saved)["browserEvents"][0]["message"]
    assert 'password="[redacted' in message
    assert "password='[redacted" in message


@pytest.mark.parametrize("completion_error", [RuntimeError("listener disappeared"), OSError("cannot resolve listener cwd")])
def test_main_preserves_real_runtime_artifact_when_completion_probe_fails(tmp_path, monkeypatch, completion_error):
    class Options:
        def add_argument(self, _arg):
            return None

        def set_capability(self, _name, _value):
            return None

    class Driver(FakeDriver):
        def set_page_load_timeout(self, _timeout):
            return None

        def set_script_timeout(self, _timeout):
            return None

    identity = soak.ListenerIdentity(pid=12, cwd="/repo", started="now", head="a" * 40)
    outcomes = iter((identity, completion_error))
    samples = iter((
        {"browserEvents": [], "browserLocalFailures": [], "browserReceiptBarrier": clean_receipt_barrier(), "serverLogErrors": [], "browserLogFailures": [], "serverLogDropped": {"count": 0}, "cursors": {"js": 0, "server_epoch": "a", "server_sequence": 0, "server_log_ids": []}},
        {"browserEvents": [{"id": 1}], "browserLocalFailures": [], "browserReceiptBarrier": clean_receipt_barrier(), "serverLogErrors": [], "browserLogFailures": [], "serverLogDropped": {"count": 0}, "cursors": {"js": 1, "server_epoch": "a", "server_sequence": 0, "server_log_ids": []}},
        {"browserEvents": [{"id": 1}], "browserLocalFailures": [], "browserReceiptBarrier": clean_receipt_barrier(), "serverLogErrors": [], "browserLogFailures": [], "serverLogDropped": {"count": 0}, "cursors": {"js": 1, "server_epoch": "a", "server_sequence": 0, "server_log_ids": []}},
    ))

    def listener_probe(_port):
        outcome = next(outcomes)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    output = tmp_path / f"{type(completion_error).__name__}.json"
    monkeypatch.setattr(tool, "webdriver", type("WebDriver", (), {"ChromeOptions": Options, "Chrome": lambda *_args, **_kwargs: Driver()})())
    monkeypatch.setattr(tool, "validate_arguments", lambda *_args: None)
    monkeypatch.setattr(tool, "cleanup_driver", lambda _driver: None)
    monkeypatch.setattr(soak, "listener_identity", listener_probe)
    monkeypatch.setattr(soak, "install_auth_cookie", lambda *_args: None)
    monkeypatch.setattr(soak, "install_start_of_document_sentinels", lambda *_args: None)
    monkeypatch.setattr(soak, "discover_served_bundle", lambda *_args: ("https://localhost:7443/static/yolomux.js", "b" * 64))
    monkeypatch.setattr(soak, "WebDriverWait", lambda *_args: type("Wait", (), {"until": lambda *_: None})())
    monkeypatch.setattr(soak, "assert_stats_hidden", lambda *_args: None)
    monkeypatch.setattr(soak, "sample_evidence", lambda *_args: next(samples))
    assert tool.main(["--url", "https://localhost:7443/", "--duration", "0", "--expected-head", "a" * 40, "--expected-bundle-sha256", "b" * 64, "--expected-cwd", "/repo", "--output", str(output)]) == 1
    artifact = json.loads(output.read_text(encoding="utf-8"))
    assert artifact["identity"]["pid"] == 12
    assert artifact["baseline"]["cursors"]["js"] == 0
    assert len(artifact["samples"]) == 2
    assert artifact["elapsed_seconds"] >= 0
    assert artifact["failure"]["terminal"] == type(completion_error).__name__
    assert output.stat().st_mode & 0o777 == 0o600


def test_write_artifact_preserves_trailing_failure_context_after_header_redaction(tmp_path):
    output = tmp_path / "artifact.json"
    canaries = ("authorization-canary", "cookie-canary")
    soak.write_artifact(output, {"browserEvents": [{"message": f"upstream Authorization: Basic {canaries[0]} failed at /api/activity-summary after 500"}, {"message": f"request Cookie: sid={canaries[1]} failed after reconnect"}]})
    artifact = json.loads(output.read_text(encoding="utf-8"))
    serialized = json.dumps(artifact)
    assert all(canary not in serialized for canary in canaries)
    assert artifact["browserEvents"][0]["message"].endswith("failed at /api/activity-summary after 500")
    assert artifact["browserEvents"][1]["message"].endswith("failed after reconnect")


def test_run_soak_retains_changed_completion_identity(monkeypatch):
    first = soak.ListenerIdentity(pid=1, cwd="/repo", started="first", head="a" * 40)
    second = soak.ListenerIdentity(pid=2, cwd="/repo", started="second", head="a" * 40)
    identities = iter((first, second))
    monkeypatch.setattr(soak, "listener_identity", lambda _port: next(identities))
    monkeypatch.setattr(soak, "install_auth_cookie", lambda *_args: None)
    monkeypatch.setattr(soak, "install_start_of_document_sentinels", lambda *_args: None)
    monkeypatch.setattr(soak, "discover_served_bundle", lambda *_args: ("https://localhost:7443/static/yolomux.js", "b" * 64))
    monkeypatch.setattr(soak, "WebDriverWait", lambda *_args: type("Wait", (), {"until": lambda *_: None})())
    monkeypatch.setattr(soak, "assert_stats_hidden", lambda *_args: None)
    sample = {"browserEvents": [], "browserLocalFailures": [], "browserReceiptBarrier": clean_receipt_barrier(), "serverLogErrors": [], "browserLogFailures": [], "serverLogDropped": {"count": 0}, "cursors": {"js": 0, "server_epoch": "a", "server_sequence": 0, "server_log_ids": []}}
    monkeypatch.setattr(soak, "sample_evidence", lambda *_args: sample)
    artifact = soak.run_soak(FakeDriver(), url="https://localhost:7443/", duration=0, expected_head="a" * 40, expected_bundle_sha256="b" * 64, expected_cwd="/repo", negative_probe=False, monotonic_fn=iter((0.0, 0.0, 0.1, 0.2)).__next__)
    assert artifact["completion_identity"]["pid"] == 2
    assert artifact["failure"]["integrityFailures"] == ["listener identity changed during soak"]


def test_run_soak_fails_for_error_arriving_during_completion_boundary(monkeypatch):
    identity = soak.ListenerIdentity(pid=1, cwd="/repo", started="now", head="a" * 40)
    probe_calls = 0
    sample_calls = 0

    def completion_probe(_port):
        nonlocal probe_calls
        probe_calls += 1
        return identity

    def sample(_driver):
        nonlocal sample_calls
        sample_calls += 1
        if sample_calls < 3:
            return {"browserEvents": [], "browserLocalFailures": [], "browserReceiptBarrier": clean_receipt_barrier(), "serverLogErrors": [], "browserLogFailures": [], "serverLogDropped": {"count": 0}, "cursors": {"js": 0, "server_epoch": "a", "server_sequence": 0, "server_log_ids": []}}
        assert probe_calls == 2
        return {"browserEvents": [{"id": 1}], "browserLocalFailures": [{"id": 1, "requestId": soak.NEGATIVE_REQUEST_ID, "source": soak.NEGATIVE_SOURCE, "route": soak.NEGATIVE_ROUTE, "event": "api", "wallTime": "2026-08-05 12:00:00 PDT", "deliveryOutcome": "failed"}], "browserReceiptBarrier": clean_receipt_barrier(accepted=1), "serverLogErrors": [], "browserLogFailures": [], "serverLogDropped": {"count": 0}, "cursors": {"js": 1, "server_epoch": "a", "server_sequence": 0, "server_log_ids": []}}

    monkeypatch.setattr(soak, "listener_identity", completion_probe)
    monkeypatch.setattr(soak, "install_auth_cookie", lambda *_args: None)
    monkeypatch.setattr(soak, "install_start_of_document_sentinels", lambda *_args: None)
    monkeypatch.setattr(soak, "discover_served_bundle", lambda *_args: ("https://localhost:7443/static/yolomux.js", "b" * 64))
    monkeypatch.setattr(soak, "WebDriverWait", lambda *_args: type("Wait", (), {"until": lambda *_: None})())
    monkeypatch.setattr(soak, "assert_stats_hidden", lambda *_args: None)
    monkeypatch.setattr(soak, "sample_evidence", sample)

    artifact = soak.run_soak(FakeDriver(), url="https://localhost:7443/", duration=0, expected_head="a" * 40, expected_bundle_sha256="b" * 64, expected_cwd="/repo", negative_probe=False, monotonic_fn=iter((0.0, 0.0, 0.1, 0.2)).__next__)

    assert sample_calls == 3
    assert len(artifact["samples"]) == 2
    assert artifact["failure"]["browserLocalFailures"][0] == {"id": 1, "requestId": soak.NEGATIVE_REQUEST_ID, "source": soak.NEGATIVE_SOURCE, "route": soak.NEGATIVE_ROUTE, "event": "api", "wallTime": "2026-08-05 12:00:00 PDT", "deliveryOutcome": "failed"}


@pytest.mark.parametrize(
    ("failure_mode", "error"),
    [
        ("stats", AssertionError("final YO!stats hidden check failed")),
        ("sample", RuntimeError("final evidence read failed")),
    ],
)
def test_main_preserves_runtime_artifact_when_final_boundary_raises(tmp_path, monkeypatch, failure_mode, error):
    class Options:
        def add_argument(self, _arg):
            return None

        def set_capability(self, _name, _value):
            return None

    class Driver(FakeDriver):
        def set_page_load_timeout(self, _timeout):
            return None

        def set_script_timeout(self, _timeout):
            return None

    identity = soak.ListenerIdentity(pid=12, cwd="/repo", started="now", head="a" * 40)
    sample_calls = 0
    hidden_checks = 0

    def sample(_driver):
        nonlocal sample_calls
        sample_calls += 1
        if failure_mode == "sample" and sample_calls == 3:
            raise error
        return {"browserEvents": [], "browserLocalFailures": [], "browserReceiptBarrier": clean_receipt_barrier(), "serverLogErrors": [], "browserLogFailures": [], "serverLogDropped": {"count": 0}, "cursors": {"js": 0, "server_epoch": "a", "server_sequence": 0, "server_log_ids": []}}

    def assert_hidden(_driver):
        nonlocal hidden_checks
        hidden_checks += 1
        if failure_mode == "stats" and hidden_checks == 3:
            raise error

    output = tmp_path / f"{failure_mode}.json"
    monkeypatch.setattr(tool, "webdriver", type("WebDriver", (), {"ChromeOptions": Options, "Chrome": lambda *_args, **_kwargs: Driver()})())
    monkeypatch.setattr(tool, "validate_arguments", lambda *_args: None)
    monkeypatch.setattr(tool, "cleanup_driver", lambda _driver: None)
    monkeypatch.setattr(soak, "listener_identity", lambda _port: identity)
    monkeypatch.setattr(soak, "install_auth_cookie", lambda *_args: None)
    monkeypatch.setattr(soak, "install_start_of_document_sentinels", lambda *_args: None)
    monkeypatch.setattr(soak, "discover_served_bundle", lambda *_args: ("https://localhost:7443/static/yolomux.js", "b" * 64))
    monkeypatch.setattr(soak, "WebDriverWait", lambda *_args: type("Wait", (), {"until": lambda *_: None})())
    monkeypatch.setattr(soak, "assert_stats_hidden", assert_hidden)
    monkeypatch.setattr(soak, "sample_evidence", sample)

    assert tool.main(["--url", "https://localhost:7443/", "--duration", "0", "--expected-head", "a" * 40, "--expected-bundle-sha256", "b" * 64, "--expected-cwd", "/repo", "--output", str(output)]) == 1
    artifact = json.loads(output.read_text(encoding="utf-8"))
    assert artifact["failure"] == {"phase": "runtime", "terminal": type(error).__name__, "message": str(error)}
    assert artifact["identity"]["pid"] == 12
    assert artifact["completion_identity"]["pid"] == 12
    assert artifact["baseline"]["cursors"]["js"] == 0
    assert len(artifact["samples"]) == 1
    assert artifact["samples"][0]["cursors"]["js"] == 0
    assert "started_pt" in artifact and "ended_pt" in artifact and "elapsed_seconds" in artifact


def test_visible_stats_sentinel_rejects_before_next_sample(monkeypatch):
    identity = soak.ListenerIdentity(pid=1, cwd="/repo", started="now", head="a" * 40)
    sample_calls = 0
    hidden_checks = 0

    def sample(_driver):
        nonlocal sample_calls
        sample_calls += 1
        return {"browserEvents": [], "browserLocalFailures": [], "browserReceiptBarrier": clean_receipt_barrier(), "serverLogErrors": [], "browserLogFailures": [], "serverLogDropped": {"count": 0}, "cursors": {"js": 0, "server_epoch": "a", "server_sequence": 0, "server_log_ids": []}}

    def assert_hidden(_driver):
        nonlocal hidden_checks
        hidden_checks += 1
        if hidden_checks == 2:
            raise AssertionError("YO!stats is visible")

    monkeypatch.setattr(soak, "listener_identity", lambda _port: identity)
    monkeypatch.setattr(soak, "install_auth_cookie", lambda *_args: None)
    monkeypatch.setattr(soak, "install_start_of_document_sentinels", lambda *_args: None)
    monkeypatch.setattr(soak, "discover_served_bundle", lambda *_args: ("https://localhost:7443/static/yolomux.js", "b" * 64))
    monkeypatch.setattr(soak, "WebDriverWait", lambda *_args: type("Wait", (), {"until": lambda *_: None})())
    monkeypatch.setattr(soak, "assert_stats_hidden", assert_hidden)
    monkeypatch.setattr(soak, "sample_evidence", sample)
    artifact = soak.run_soak(FakeDriver(), url="https://localhost:7443/", duration=1, expected_head="a" * 40, expected_bundle_sha256="b" * 64, expected_cwd="/repo", negative_probe=False)
    assert artifact["failure"]["terminal"] == "AssertionError"
    assert sample_calls == 2


def test_discover_served_bundle_rejects_cross_origin_bundle():
    class Driver:
        def get(self, _url): pass
        def execute_script(self, _script): return "https://example.test/static/yolomux.js"
        def execute_async_script(self, _script, _url): return {"status": 200, "text": "bundle"}
    with pytest.raises(RuntimeError, match="same-origin"):
        soak.discover_served_bundle(Driver(), "https://localhost:7443/")


def test_discover_served_bundle_navigates_to_exact_recorded_layout_url():
    navigations = []

    class Driver:
        def get(self, url):
            navigations.append(url)

        def execute_script(self, _script):
            return "https://localhost:7443/static/yolomux.js"

        def execute_async_script(self, _script, _url):
            return {"status": 200, "text": "bundle"}

    bundle_url, _bundle_sha256 = soak.discover_served_bundle(Driver(), RECORDED_LAYOUT_URL)

    assert navigations == [RECORDED_LAYOUT_URL]
    assert bundle_url == "https://localhost:7443/static/yolomux.js"


def test_run_soak_authenticates_before_bundle_and_rejects_completion_identity_change(monkeypatch):
    calls = []
    first = soak.ListenerIdentity(1, "/repo", "one", "a" * 40)
    second = soak.ListenerIdentity(2, "/repo", "two", "a" * 40)
    identities = iter((first, second))
    monkeypatch.setattr(soak, "listener_identity", lambda _port: next(identities))
    monkeypatch.setattr(soak, "install_auth_cookie", lambda *_args: calls.append("auth"))
    monkeypatch.setattr(soak, "install_start_of_document_sentinels", lambda *_args: None)
    monkeypatch.setattr(soak, "discover_served_bundle", lambda *_args: (calls.append("bundle") or ("https://localhost:7443/static/yolomux.js", "b" * 64)))
    monkeypatch.setattr(soak, "WebDriverWait", lambda *_args: type("W", (), {"until": lambda *_: None})())
    monkeypatch.setattr(soak, "assert_stats_hidden", lambda *_args: None)
    sample = {"browserEvents": [], "browserLocalFailures": [], "browserReceiptBarrier": clean_receipt_barrier(), "serverLogErrors": [], "browserLogFailures": [], "serverLogDropped": {"count": 0}, "cursors": {"js": 0, "server_epoch": "a", "server_sequence": 0, "server_log_ids": []}}
    monkeypatch.setattr(soak, "sample_evidence", lambda *_args: sample)
    artifact = soak.run_soak(FakeDriver(), url="https://localhost:7443/", duration=1, expected_head="a" * 40, expected_bundle_sha256="b" * 64, negative_probe=False, monotonic_fn=iter((0, 1, 1, 1)).__next__)
    assert calls == ["auth", "bundle", "bundle"]
    assert artifact["failure"]["integrityFailures"] == ["listener identity changed during soak"]


def test_sample_evidence_retains_product_failure_correlation_fields():
    class EvidenceDriver:
        def execute_async_script(self, _script):
            return {
                "events": [{"id": 4, "type": "api", "endpoint": soak.NEGATIVE_ROUTE, "source": soak.NEGATIVE_SOURCE, "requestId": soak.NEGATIVE_REQUEST_ID, "status": 500, "ok": False, "error": "P0 fixed negative probe", "wallTime": "2026-08-05 12:00:00 PDT"}],
                "failures": [{"id": 4, "type": "api", "endpoint": soak.NEGATIVE_ROUTE, "source": soak.NEGATIVE_SOURCE, "requestId": soak.NEGATIVE_REQUEST_ID, "status": 500, "ok": False, "error": "P0 fixed negative probe", "wallTime": "2026-08-05 12:00:00 PDT"}],
                "receiptBarrier": clean_receipt_barrier(accepted=1),
                "receiptProjection": receipt_projection([receipt_row(4, request_id=soak.NEGATIVE_REQUEST_ID, source=soak.NEGATIVE_SOURCE, route=soak.NEGATIVE_ROUTE, event="api", wall_time="2026-08-05 12:00:00 PDT", http_status=500)]),
                "logsStatus": 200,
                "payload": {"ok": True, "logs": [], "capacity": 100, "dropped": {"count": 0, "by_level": {}}, "epoch": "epoch", "sequence": 0},
            }

        def get_log(self, _name):
            return []
    failure = soak.sample_evidence(EvidenceDriver())["browserLocalFailures"]
    assert failure == [{"id": 4, "level": "error", "message": "P0 fixed negative probe", "requestId": soak.NEGATIVE_REQUEST_ID, "source": soak.NEGATIVE_SOURCE, "route": soak.NEGATIVE_ROUTE, "event": "api", "wallTime": "2026-08-05 12:00:00 PDT", "deliveryOutcome": "failed", "status": 500}]


@pytest.mark.parametrize(
    "barrier",
    [None, {}, {"epoch": "page-1", "accepted": 0, "pending": "0", "retrying": 0, "rejected": 0, "dropped": 0, "quiescent": True, "blocking": []}],
)
def test_sample_evidence_rejects_missing_or_malformed_receipt_barrier(barrier):
    class EvidenceDriver:
        def execute_async_script(self, _script):
            return {
                "events": [],
                "failures": [],
                "receiptBarrier": barrier,
                "logsStatus": 200,
                "payload": {"ok": True, "logs": [], "capacity": 100, "dropped": {"count": 0, "by_level": {}}, "epoch": "epoch", "sequence": 0},
            }

        def get_log(self, _name):
            return []

    with pytest.raises(AssertionError, match="receipt barrier"):
        soak.sample_evidence(EvidenceDriver())


def test_live_soak_receipt_consumer_calls_the_shared_production_owner(monkeypatch):
    calls = []
    expected = clean_receipt_barrier()

    def validate(value):
        calls.append(value)
        return dict(value)

    monkeypatch.setattr(browser_diagnostic_receipts, "validate_browser_receipt_barrier", validate)

    assert soak.evidence_failed({"browserReceiptBarrier": expected}) is False
    assert calls == [expected]


def test_sample_evidence_fences_an_event_arriving_during_server_log_fetch():
    class EvidenceDriver:
        def execute_async_script(self, script, *args):
            assert script.count("jsDebugFailureEvents") >= 2, "browser failures must be sampled again after /api/logs"
            assert "flushJsDebugCurrentObservations" in script, "the post-log snapshot must await the durable receipt flush"
            return {
                "events": [{"id": 1, "type": "client_failure", "error": "arrived during logs", "wallTime": "2026-08-05 12:00:00 PDT"}],
                "failures": [{"id": 1, "type": "client_failure", "error": "arrived during logs", "wallTime": "2026-08-05 12:00:00 PDT"}],
                "receiptBarrier": {"epoch": "page-1", "accepted": 1, "pending": 0, "retrying": 0, "rejected": 0, "dropped": 0, "quiescent": True, "blocking": []},
                "receiptProjection": receipt_projection([receipt_row(1, source="/", route="/", event="client_failure", wall_time="2026-08-05 12:00:00 PDT")]),
                "logsStatus": 200,
                "payload": {"ok": True, "logs": [], "capacity": 100, "dropped": {"count": 0, "by_level": {}}, "epoch": "epoch", "sequence": 0},
            }

        def get_log(self, _name):
            return []

    failures = soak.sample_evidence(EvidenceDriver())["browserLocalFailures"]
    assert failures[0]["message"] == "arrived during logs"


def test_success_artifact_requires_receipt_barrier_in_baseline_and_final_sample():
    identity = {"pid": 1, "cwd": "/repo", "process_started": "now", "head": "a" * 40, "bundle_url": "https://localhost/static/yolomux.js", "bundle_sha256": "b" * 64}
    evidence = {"browserEvents": [], "browserLocalFailures": [], "serverLogErrors": [], "browserLogFailures": [], "serverLogDropped": {"count": 0}, "cursors": {"js": 0, "server_epoch": "a", "server_sequence": 0, "server_log_ids": []}}
    artifact = {"url": "https://localhost/", "started_pt": "start", "ended_pt": "end", "requested_duration_seconds": 600, "elapsed_seconds": 600, "identity": identity, "completion_identity": identity, "baseline": evidence, "samples": [evidence, evidence], **clean_settle(evidence)}
    with pytest.raises(soak.ArtifactIntegrityError, match="exact clean finalBoundary"):
        soak.validate_success_artifact(artifact)


def test_success_artifact_rejects_well_formed_nonquiescent_receipt_barriers():
    identity = {"pid": 1, "cwd": "/repo", "process_started": "now", "head": "a" * 40, "bundle_url": "https://localhost/static/yolomux.js", "bundle_sha256": "b" * 64}
    blocker = {"key": "page-1:1", "epoch": "page-1", "eventId": 1, "requestId": "r", "source": "/", "route": "/", "event": "api", "wallTime": "", "deliveryOutcome": "failed", "httpStatus": 500, "status": "pending"}
    receipt = {"epoch": "page-1", "accepted": 0, "pending": 1, "retrying": 0, "rejected": 0, "dropped": 0, "quiescent": False, "blocking": [blocker]}
    evidence = {"browserEvents": [], "browserLocalFailures": [], "browserReceiptBarrier": receipt, "serverLogErrors": [], "browserLogFailures": [], "serverLogDropped": {"count": 0}, "cursors": {"js": 0, "server_epoch": "a", "server_sequence": 0, "server_log_ids": []}}
    artifact = {"url": "https://localhost/", "started_pt": "start", "ended_pt": "end", "requested_duration_seconds": 600, "elapsed_seconds": 600, "identity": identity, "completion_identity": identity, "baseline": evidence, "samples": [evidence, evidence], **clean_settle(evidence)}

    with pytest.raises(soak.ArtifactIntegrityError, match="exact clean finalBoundary"):
        soak.validate_success_artifact(artifact)


@pytest.mark.parametrize("failure_field", ("browserLocalFailures", "serverLogErrors", "browserLogFailures", "integrityFailures"))
def test_success_artifact_rejects_nonempty_failure_evidence_without_run_soak(failure_field):
    identity = {"pid": 1, "cwd": "/repo", "process_started": "now", "head": "a" * 40, "bundle_url": "https://localhost/static/yolomux.js", "bundle_sha256": "b" * 64}
    evidence = {"browserEvents": [], "browserLocalFailures": [], "browserReceiptBarrier": clean_receipt_barrier(), "serverLogErrors": [], "browserLogFailures": [], "integrityFailures": [], "serverLogDropped": {"count": 0}, "cursors": {"js": 0, "server_epoch": "a", "server_sequence": 0, "server_log_ids": []}}
    evidence[failure_field] = [{"message": "must fail closed"}]
    artifact = {"url": "https://localhost/", "started_pt": "start", "ended_pt": "end", "requested_duration_seconds": 600, "elapsed_seconds": 600, "identity": identity, "completion_identity": identity, "baseline": evidence, "samples": [evidence, evidence], **clean_settle(evidence)}

    with pytest.raises(soak.ArtifactIntegrityError, match="exact clean finalBoundary"):
        soak.validate_success_artifact(artifact)


@pytest.mark.parametrize(
    ("requested", "elapsed", "message"),
    (
        (None, 600, "requested duration"),
        (True, 600, "requested duration"),
        (599, 600, "requested duration"),
        (600, 599.99, "before requested duration"),
    ),
)
def test_success_artifact_requires_exact_minimum_requested_and_elapsed_duration(requested, elapsed, message):
    artifact = {"url": "https://localhost/", "started_pt": "start", "ended_pt": "end", "requested_duration_seconds": requested, "elapsed_seconds": elapsed}
    with pytest.raises(soak.ArtifactIntegrityError, match=message):
        soak.validate_success_artifact(artifact)


class FakeDriver:
    def __init__(self):
        self.scripts = []

    def execute_script(self, script, *args):
        self.scripts.append((script, args))
        if "recordApiDebugEvent" in script:
            return receipt_row(
                request_id=soak.NEGATIVE_REQUEST_ID, source=soak.NEGATIVE_SOURCE, route=soak.NEGATIVE_ROUTE,
                event="api", wall_time="2026-08-05 12:00:00 PDT", http_status=500, status="pending",
            )
        return True

    def get(self, _url):
        return None

    def execute_cdp_cmd(self, _command, _payload):
        return None


def test_negative_probe_uses_real_yolo_rules_failure_and_rendered_logs_path():
    event = {
        "id": 7,
        "type": "api",
        "endpoint": soak.NEGATIVE_ROUTE,
        "requestId": "r-web-controlled-7",
        "status": 500,
        "ok": False,
        "wallTime": "2026-08-06 13:00:00 PDT",
    }
    receipt = receipt_row(
        7,
        request_id=event["requestId"],
        source=soak.NEGATIVE_ROUTE,
        route=soak.NEGATIVE_ROUTE,
        event="api",
        wall_time=event["wallTime"],
        http_status=500,
    )

    class Driver:
        def __init__(self):
            self.script = ""
            self.args = ()

        def execute_async_script(self, script, *args):
            self.script = script
            self.args = args
            return {
                "event": event,
                "projection": receipt_projection([receipt]),
                "rendered": {"matchingRows": 1, "requestId": event["requestId"], "source": "browser", "route": soak.NEGATIVE_ROUTE, "event": "api", "text": "controlled browser failure HTTP 500"},
                "redaction": {"dom": True, "clipboard": True, "retained": True, "upload": True, "storage": True},
            }

    driver = Driver()
    handle = soak.produce_negative_browser_failure(driver)
    assert driver.args == ("/api/yolo-rules", "controlled browser failure", soak.NEGATIVE_CANARY)
    assert "refreshYoloRulesStatus({silent: true})" in driver.script
    assert "yoloRules: typeof yoloRulesPayload" in driver.script
    assert "refreshActivitySummary" not in driver.script and "activitySummaryState" not in driver.script
    assert "selectSession(debugPaneItemId" in driver.script
    assert "setDebugSubTab('logs')" in driver.script
    assert "pollDebugLogs({force: true})" in driver.script
    assert "article[data-js-debug-log-entry]" in driver.script
    assert "recordApiDebugEvent" not in driver.script and "recordJsDebugEvent" not in driver.script
    assert handle["requestId"] == event["requestId"]
    assert handle["rendered"]["matchingRows"] == 1


def test_negative_command_runs_tool_main_after_settle_short_window_and_attribution(tmp_path, monkeypatch):
    calls = []
    identity = soak.ListenerIdentity(pid=7, cwd="/repo", started="now", head="a" * 40)
    projection = receipt_projection()
    baseline = {
        "browserEvents": [], "browserLocalFailures": [], "browserReceiptBarrier": projection["barrier"], "browserReceiptProjection": projection,
        "statsStreamEvidence": clean_stats_stream(), "serverLogErrors": [], "browserLogFailures": [], "integrityFailures": [],
        "serverLogDropped": {"count": 0, "by_level": {}},
        "cursors": {"js": 0, "server_epoch": "server-a", "server_sequence": 0, "server_log_ids": [], "server_log_records": [], "server_capacity": 10, "server_dropped_by_level": {}},
    }
    previous = soak.evidence_baseline(baseline)
    observation = copy.deepcopy(baseline)
    receipt = receipt_row(1, request_id="r-web-controlled-1", source=soak.NEGATIVE_ROUTE, route=soak.NEGATIVE_ROUTE, event="api", wall_time="2026-08-06 13:00:00 PDT", http_status=500)
    rendered = {"matchingRows": 1, "requestId": receipt["requestId"], "source": soak.NEGATIVE_SOURCE, "route": soak.NEGATIVE_ROUTE, "event": "api", "text": "controlled browser failure HTTP 500"}
    redaction = {channel: True for channel in ("dom", "clipboard", "retained", "upload", "storage")}
    handle = {**receipt, "source": soak.NEGATIVE_SOURCE, "receiptSource": soak.NEGATIVE_ROUTE, "rendered": rendered, "redaction": redaction}
    accepted_projection = receipt_projection([receipt])
    local_failure = {"id": 1, "level": "error", "message": "", "requestId": receipt["requestId"], "source": soak.NEGATIVE_SOURCE, "route": soak.NEGATIVE_ROUTE, "event": "api", "wallTime": receipt["wallTime"], "deliveryOutcome": "failed", "status": 500}
    raw_event = {"id": 1, "type": "api", "endpoint": soak.NEGATIVE_ROUTE, "requestId": receipt["requestId"], "status": 500, "ok": False}
    final_evidence = {**copy.deepcopy(observation), "browserEvents": [raw_event], "browserLocalFailures": [local_failure], "browserReceiptBarrier": accepted_projection["barrier"], "browserReceiptProjection": accepted_projection, "statsStreamEvidence": clean_stats_stream(2, 1, 2, "delta", source_generation=2, delta_revision=1)}

    class Options:
        def add_argument(self, _arg): return None
        def set_capability(self, _name, _value): return None

    class Driver:
        def set_page_load_timeout(self, _timeout): return None
        def set_script_timeout(self, _timeout): return None
        def get_log(self, _name): return []

    output = tmp_path / "negative.json"
    monkeypatch.setattr(tool, "webdriver", type("WebDriver", (), {"ChromeOptions": Options, "Chrome": lambda *_args, **_kwargs: Driver()})())
    monkeypatch.setattr(tool, "validate_arguments", lambda *_args: None)
    monkeypatch.setattr(tool, "cleanup_driver", lambda _driver: None)
    monkeypatch.setattr(soak, "listener_identity", lambda _port: identity)
    monkeypatch.setattr(soak, "install_auth_cookie", lambda *_args: soak.AuthUser("admin", "password", "admin"))
    monkeypatch.setattr(soak, "install_start_of_document_sentinels", lambda _driver: None)
    monkeypatch.setattr(soak, "authenticated_server_log_reader", lambda *_args: lambda: finalizer_server_payload())
    monkeypatch.setattr(soak, "chrome_failure_entries", lambda _entries: [])
    monkeypatch.setattr(soak, "discover_served_bundle", lambda *_args, **_kwargs: ("https://localhost:7443/static/yolomux.js", "b" * 64))
    monkeypatch.setattr(soak, "WebDriverWait", lambda *_args: type("Wait", (), {"until": lambda *_: None})())
    monkeypatch.setattr(soak, "assert_page_identity", lambda *_args: {"reasons": [], "drift": {"hrefChanged": False}, "journeyId": "j-reload-fake"})
    monkeypatch.setattr(soak, "assert_stats_hidden", lambda *_args: None)
    monkeypatch.setattr(soak, "wait_for_hidden_stats_stream", lambda *_args: clean_stats_stream())
    monkeypatch.setattr(soak, "settle_authenticated_page", lambda *_args, **_kwargs: (calls.append("settle") or (baseline, previous, clean_stats_stream(), {"requested_seconds": 90.0, "started_pt": "start", "ended_pt": "end", "elapsed_seconds": 90.0, "status": "clean", "samples": [baseline]})))
    monkeypatch.setattr(soak, "sample_evidence", lambda _driver: calls.append("observation") or copy.deepcopy(observation))
    monkeypatch.setattr(soak, "sample_hidden_stats_stream", lambda _driver: clean_stats_stream(2, 1, 2, "delta", source_generation=2, delta_revision=1))
    monkeypatch.setattr(soak, "produce_negative_browser_failure", lambda _driver: calls.append("producer") or handle)

    monkeypatch.setattr(soak, "finalize_live_browser_soak", lambda *_args, **_kwargs: pytest.fail("the negative acceptance phase must never retire the page"))
    monkeypatch.setattr(soak, "fence_browser_uploader", lambda _driver: calls.append("fence") or {"cursor": 1, "projection": accepted_projection, "completions": 1})
    monkeypatch.setattr(soak, "classify_incremental_evidence", lambda _evidence, _previous: (copy.deepcopy(final_evidence if "producer" in calls else observation), previous))
    monotonic = iter((0.0, 0.0, 30.0, 30.0, 30.0)).__next__
    monkeypatch.setattr(tool, "run_soak", lambda driver, **kwargs: soak.run_soak(driver, **kwargs, sleep_fn=lambda _seconds: pytest.fail("measured observation must not sleep after reaching the window"), monotonic_fn=monotonic))
    prerequisite = tmp_path / "clean.json"
    prerequisite.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(tool, "validate_clean_soak_prerequisite", lambda *_args, **_kwargs: {"path": str(prerequisite), "sha256": "c" * 64, "url": "https://localhost:7443/?sessions=1&layout=left", "identity": {"head": "a" * 40}, "requested_duration_seconds": 600, "elapsed_seconds": 603.4, "final_boundary_status": "clean"})

    assert tool.main(["--url", "https://localhost:7443/?sessions=1&layout=left", "--duration", str(soak.NEGATIVE_PROBE_OBSERVATION_SECONDS), "--expected-head", "a" * 40, "--expected-bundle-sha256", "b" * 64, "--expected-cwd", "/repo", "--output", str(output), "--negative-browser-error-probe", "--clean-soak-artifact", str(prerequisite)]) == 1
    artifact = json.loads(output.read_text(encoding="utf-8"))
    assert calls == ["settle", "observation", "producer", "fence", "observation"]
    assert artifact["phase"] == "negative_probe"
    assert artifact["clean_soak_prerequisite"]["final_boundary_status"] == "clean"
    assert artifact["clean_soak_prerequisite"]["elapsed_seconds"] >= soak.MIN_OBSERVATION_SECONDS
    assert "finalBoundary" not in artifact
    assert artifact["negativeProbe"]["phaseFailures"] == []
    assert artifact["negativeProbe"]["status"] == "attributed"
    assert artifact["negativeProbe"]["attribution"]["soleCause"] is True
    proof = artifact["expected_failure_durably_detected"]
    assert {field: proof[field] for field in ("requestId", "source", "route", "event", "wallTime", "deliveryOutcome")} == {field: handle[field] for field in ("requestId", "source", "route", "event", "wallTime", "deliveryOutcome")}
    assert proof["rendered"]["matchingRows"] == 1
    assert proof["receipt"]["status"] == "accepted"


def test_negative_probe_rejects_matching_baseline_correlation_under_another_key():
    wall_time = "2026-08-05 12:00:00 PDT"
    handle = receipt_row(
        7,
        status="pending",
        epoch="probe-page",
        request_id=soak.NEGATIVE_REQUEST_ID,
        source=soak.NEGATIVE_SOURCE,
        route=soak.NEGATIVE_ROUTE,
        event="api",
        wall_time=wall_time,
        http_status=500,
    )
    accepted = {**handle, "status": "accepted"}
    collision = receipt_row(
        99,
        epoch="old-page",
        request_id=soak.NEGATIVE_REQUEST_ID,
        source=soak.NEGATIVE_SOURCE,
        route=soak.NEGATIVE_ROUTE,
        event="api",
        wall_time="2026-08-04 12:00:00 PDT",
        http_status=500,
    )
    raw = {
        "id": 7,
        "type": "api",
        "endpoint": soak.NEGATIVE_ROUTE,
        "source": soak.NEGATIVE_SOURCE,
        "requestId": soak.NEGATIVE_REQUEST_ID,
        "status": 500,
        "ok": False,
        "error": "P0 fixed negative probe",
    }
    local = {
        "id": 7,
        "level": "error",
        "message": "P0 fixed negative probe",
        "requestId": soak.NEGATIVE_REQUEST_ID,
        "source": soak.NEGATIVE_SOURCE,
        "route": soak.NEGATIVE_ROUTE,
        "event": "api",
        "wallTime": wall_time,
        "deliveryOutcome": "failed",
        "status": 500,
    }
    evidence = {
        "browserEvents": [raw],
        "browserLocalFailures": [local],
        "browserReceiptProjection": receipt_projection([collision, accepted], epoch="all"),
        "integrityFailures": [],
    }

    soak.require_negative_acknowledgement(
        evidence,
        handle,
        receipt_projection([collision], epoch="all"),
    )

    assert evidence["integrityFailures"] == ["negative browser error probe was not retained exactly"]


def test_negative_probe_baseline_allows_historical_browser_failure_with_a_different_event_key():
    collision = receipt_row(
        99,
        epoch="old-page",
        request_id=soak.NEGATIVE_REQUEST_ID,
        source=soak.NEGATIVE_SOURCE,
        route=soak.NEGATIVE_ROUTE,
        event="api",
        wall_time="2026-08-04 12:00:00 PDT",
        http_status=500,
    )

    assert soak.validate_negative_probe_baseline(receipt_projection([collision], epoch="all"))["receipts"] == [collision]


@pytest.mark.browser
@pytest.mark.socket
def test_negative_probe_owned_product_to_statsd_receipt_lifecycle(browser, gate_live_server):
    load_gate_browser(browser, gate_live_server)
    baseline = browser.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        if (jsDebugCurrentObservationState.livenessTimer !== null) {
          clearInterval(jsDebugCurrentObservationState.livenessTimer);
          jsDebugCurrentObservationState.livenessTimer = null;
        }
        flushJsDebugCurrentObservations().then(
          () => done(jsDebugCurrentObservationReceiptProjection()),
          error => done({error: String(error)}),
        );
        """
    )
    validated_baseline = soak.validate_negative_probe_baseline(baseline)
    handle = soak.produce_negative_browser_failure(browser)
    raw = browser.execute_script(
        "return jsDebugEvents.find(event => event?.id === arguments[0]) || null;",
        handle["eventId"],
    )
    assert isinstance(raw, dict)

    try:
        evidence = soak.sample_evidence(browser)
        evidence.setdefault("integrityFailures", [])
        soak.require_negative_acknowledgement(evidence, handle, validated_baseline)

        assert evidence["integrityFailures"] == []
        receipt = next(row for row in evidence["browserReceiptProjection"]["receipts"] if row["key"] == handle["key"])
        assert receipt["status"] == "accepted"
        sampled_raw = next(event for event in evidence["browserEvents"] if event.get("id") == handle["eventId"])
        assert sampled_raw == raw
        assert raw["endpoint"] == soak.NEGATIVE_ROUTE
        assert raw["requestId"] == handle["requestId"]
        assert handle["rendered"]["matchingRows"] == 1
        assert all(handle["redaction"].values())
    finally:
        acknowledge_and_consume_only_expected_js_debug_failures(browser, (raw,))


def test_run_soak_rejects_dirty_authenticated_baseline(monkeypatch):
    driver = FakeDriver()
    identity = soak.ListenerIdentity(pid=12, cwd="/repo", started="now", head="a" * 40)
    baseline = {"browserEvents": [{"id": 1}], "browserLocalFailures": [{"id": 1, "message": "preexisting failure"}], "browserReceiptBarrier": clean_receipt_barrier(accepted=1), "serverLogErrors": [], "browserLogFailures": [], "serverLogDropped": {"count": 0}, "cursors": {"js": 1, "server_epoch": "a", "server_sequence": 0, "server_log_ids": []}}
    monkeypatch.setattr(soak, "listener_identity", lambda _port: identity)
    monkeypatch.setattr(soak, "install_auth_cookie", lambda *_args: None)
    monkeypatch.setattr(soak, "install_start_of_document_sentinels", lambda *_args: None)
    monkeypatch.setattr(soak, "discover_served_bundle", lambda *_args, **_kwargs: ("https://localhost:7443/static/yolomux.js", "b" * 64))
    monkeypatch.setattr(soak, "WebDriverWait", lambda *_args: type("Wait", (), {"until": lambda *_: None})())
    monkeypatch.setattr(soak, "assert_stats_hidden", lambda *_args: None)
    monkeypatch.setattr(soak, "wait_for_hidden_stats_stream", lambda *_args: clean_stats_stream())
    monkeypatch.setattr(soak, "sample_hidden_stats_stream", lambda *_args: clean_stats_stream(2))
    monkeypatch.setattr(soak, "sample_evidence", lambda *_args: baseline)

    artifact = soak.run_soak(driver, url="https://localhost:7443/", duration=600, expected_head="a" * 40, expected_bundle_sha256="b" * 64, negative_probe=False, monotonic_fn=iter((0.0, 0.0, 0.1, 0.2)).__next__)

    assert artifact["failure"]["browserLocalFailures"] == baseline["browserLocalFailures"]


def test_assert_stats_hidden_rejects_visible_or_missing_state():
    class HiddenDriver:
        def execute_script(self, _script):
            return False
    with pytest.raises(AssertionError, match="YO!stats"):
        soak.assert_stats_hidden(HiddenDriver())


class SentinelDriver:
    def __init__(self):
        self.commands = []

    def execute_cdp_cmd(self, command, payload):
        self.commands.append((command, payload))


def test_document_start_sentinel_records_any_transient_stats_visibility():
    driver = SentinelDriver()
    soak.install_start_of_document_sentinels(driver)
    assert [command for command, _payload in driver.commands] == ["Page.addScriptToEvaluateOnNewDocument"]
    source = driver.commands[0][1]["source"]
    assert "MutationObserver" in source
    assert "everVisible" in source
    assert "document.querySelectorAll('.js-debug-panel')" in source


def test_document_start_sentinel_installs_journey_tracking_with_the_helper_contract():
    """The soak owns the journey gate because production ships none; its shape must match the pytest helper."""

    driver = SentinelDriver()
    soak.install_start_of_document_sentinels(driver)
    source = driver.commands[0][1]["source"]
    assert "window.__yolomuxBrowserJourneyGate" in source
    for field in ("visitedSurfaces", "consumedServerLogIds", "serverLogEpoch", "observer", "observe"):
        assert field in source
    assert "journey.visitedSurfaces.push('stats')" in source
    assert source.count("visiblePanels().length") == 2  # one shared predicate serves both sentinels


def test_document_start_sentinel_install_failure_is_terminal():
    class Driver:
        def execute_cdp_cmd(self, _command, _payload):
            raise soak.WebDriverException("no cdp")

    with pytest.raises(RuntimeError, match="journey sentinels"):
        soak.install_start_of_document_sentinels(Driver())


def test_finalizer_journey_snapshot_requires_the_full_gate_contract_and_refreshes_surfaces():
    driver = FinalizerDriver()
    soak.finalize_live_browser_soak(
        driver,
        server_reader=iter((finalizer_server_payload(), finalizer_server_payload())).__next__,
        expected_url="https://localhost:7443/",
        previous=finalizer_previous(),
        previous_stats=clean_stats_stream(1),
        baseline_projection=driver.projection,
        negative_handle=None,
    )
    atomic = next(script for script in driver.scripts if "location.replace('about:blank')" in script)
    assert "Array.isArray(gate.consumedServerLogIds)" in atomic
    assert "typeof gate.observe === 'function'" in atomic
    assert "if (gateReachable) gate.observe();" in atomic
    assert "reachable: gateReachable" in atomic


def test_write_artifact_preserves_identity_and_failure_shape(tmp_path):
    output = tmp_path / "artifact.json"
    artifact = {"identity": {"pid": 12, "cwd": "/repo", "head": "a" * 40, "bundle_sha256": "b" * 64}, "baseline": {"at_pt": "2026-08-05 12:00:00 PDT"}, "samples": [], "failure": {"browserLocalFailures": [{"requestId": "p0-negative-fixed", "source": "browser", "route": soak.NEGATIVE_ROUTE, "event": "api", "wallTime": "2026-08-05 12:00:00 PDT", "deliveryOutcome": "failed"}]}}
    soak.write_artifact(output, artifact)
    assert output.read_text(encoding="utf-8").endswith("\n")
    assert "p0-negative-fixed" in output.read_text(encoding="utf-8")
    assert output.stat().st_mode & 0o777 == 0o600


def test_run_soak_refuses_listener_and_bundle_mismatches_before_authentication(monkeypatch):
    driver = FakeDriver()
    identity = soak.ListenerIdentity(pid=12, cwd="/repo", started="now", head="a" * 40)
    monkeypatch.setattr(soak, "listener_identity", lambda _port: identity)
    with pytest.raises(RuntimeError, match="HEAD mismatch"):
        soak.run_soak(driver, url="https://localhost:7443/", duration=600, expected_head="c" * 40, expected_bundle_sha256="b" * 64, negative_probe=False)
    monkeypatch.setattr(soak, "discover_served_bundle", lambda *_args, **_kwargs: ("https://localhost:7443/static/yolomux.js", "c" * 64))
    monkeypatch.setattr(soak, "install_auth_cookie", lambda *_args: None)
    monkeypatch.setattr(soak, "install_start_of_document_sentinels", lambda _driver: None)
    with pytest.raises(RuntimeError, match="bundle SHA256 mismatch"):
        soak.run_soak(driver, url="https://localhost:7443/", duration=600, expected_head="a" * 40, expected_bundle_sha256="b" * 64, negative_probe=False)


def test_negative_probe_returns_nonzero_gate_evidence_without_waiting(monkeypatch):
    driver = FakeDriver()
    monkeypatch.setattr(soak, "listener_identity", lambda _port: soak.ListenerIdentity(pid=12, cwd="/repo", started="now", head="a" * 40))
    monkeypatch.setattr(soak, "discover_served_bundle", lambda *_args, **_kwargs: ("https://localhost:7443/static/yolomux.js", "b" * 64))
    monkeypatch.setattr(soak, "install_auth_cookie", lambda *_args: None)
    monkeypatch.setattr(soak, "install_start_of_document_sentinels", lambda _driver: None)
    monkeypatch.setattr(soak, "assert_stats_hidden", lambda _driver: None)
    monkeypatch.setattr(soak, "wait_for_hidden_stats_stream", lambda *_args: clean_stats_stream())
    monkeypatch.setattr(soak, "sample_hidden_stats_stream", lambda *_args: clean_stats_stream(2))
    monkeypatch.setattr(soak, "WebDriverWait", lambda _driver, _timeout: type("Wait", (), {"until": lambda self, predicate: predicate(driver)})())
    samples = iter((
        {"browserLocalFailures": [], "browserReceiptBarrier": clean_receipt_barrier(), "browserReceiptProjection": receipt_projection(), "serverLogErrors": [], "browserLogFailures": [], "serverLogDropped": {"count": 0}, "cursors": {"js": 0, "server_epoch": "a", "server_sequence": 0, "server_log_ids": []}},
        {"browserLocalFailures": [{"id": 1, "requestId": soak.NEGATIVE_REQUEST_ID, "source": soak.NEGATIVE_SOURCE, "route": soak.NEGATIVE_ROUTE, "event": "api", "wallTime": "2026-08-05 12:00:00 PDT", "deliveryOutcome": "failed"}], "browserReceiptBarrier": clean_receipt_barrier(accepted=1), "browserReceiptProjection": receipt_projection([receipt_row(request_id=soak.NEGATIVE_REQUEST_ID, source=soak.NEGATIVE_SOURCE, route=soak.NEGATIVE_ROUTE, event="api", wall_time="2026-08-05 12:00:00 PDT", http_status=500)]), "serverLogErrors": [], "browserLogFailures": [], "serverLogDropped": {"count": 0}, "cursors": {"js": 1, "server_epoch": "a", "server_sequence": 0, "server_log_ids": []}},
        {"browserLocalFailures": [{"id": 1, "requestId": soak.NEGATIVE_REQUEST_ID, "source": soak.NEGATIVE_SOURCE, "route": soak.NEGATIVE_ROUTE, "event": "api", "wallTime": "2026-08-05 12:00:00 PDT", "deliveryOutcome": "failed"}], "browserReceiptBarrier": clean_receipt_barrier(accepted=1), "browserReceiptProjection": receipt_projection([receipt_row(request_id=soak.NEGATIVE_REQUEST_ID, source=soak.NEGATIVE_SOURCE, route=soak.NEGATIVE_ROUTE, event="api", wall_time="2026-08-05 12:00:00 PDT", http_status=500)]), "serverLogErrors": [], "browserLogFailures": [], "serverLogDropped": {"count": 0}, "cursors": {"js": 1, "server_epoch": "a", "server_sequence": 0, "server_log_ids": []}},
    ))
    monkeypatch.setattr(soak, "sample_evidence", lambda _driver: next(samples))
    artifact = soak.run_soak(driver, url="https://localhost:7443/", duration=soak.NEGATIVE_PROBE_OBSERVATION_SECONDS, expected_head="a" * 40, expected_bundle_sha256="b" * 64, negative_probe=True, clean_soak_prerequisite={"path": "/tmp/clean.json", "sha256": "c" * 64, "final_boundary_status": "clean"}, monotonic_fn=iter((0.0, 0.0, 0.1, 0.1, 0.2)).__next__)
    assert soak.evidence_failed(artifact["failure"])
    assert artifact["failure"]["browserLocalFailures"][0]["requestId"] == soak.NEGATIVE_REQUEST_ID


def test_main_rejects_incomplete_success_artifact(tmp_path, monkeypatch):
    class Options:
        def add_argument(self, _arg): return None
        def set_capability(self, _name, _value): return None

    class Driver:
        def set_page_load_timeout(self, _timeout): return None
        def set_script_timeout(self, _timeout): return None

    output = tmp_path / "artifact.json"
    monkeypatch.setattr(tool, "webdriver", type("WebDriver", (), {"ChromeOptions": Options, "Chrome": lambda *_args, **_kwargs: Driver()})())
    monkeypatch.setattr(tool, "validate_arguments", lambda *_args: None)
    monkeypatch.setattr(tool, "cleanup_driver", lambda _driver: None)
    monkeypatch.setattr(tool, "run_soak", lambda *_args, **_kwargs: {"samples": []})
    assert tool.main(["--url", "https://localhost:7443/", "--duration", "600", "--expected-head", "a" * 40, "--expected-bundle-sha256", "b" * 64, "--expected-cwd", "/repo", "--output", str(output)]) == 1
    artifact = json.loads(output.read_text(encoding="utf-8"))
    assert artifact["failure"]["phase"] == "runtime"
    assert artifact["failure"]["terminal"] == "ArtifactIntegrityError"


def test_negative_probe_missing_exact_acknowledgement_fails_integrity():
    expected = receipt_row(7, request_id="r-web-expected", source=soak.NEGATIVE_ROUTE, route=soak.NEGATIVE_ROUTE, event="api", wall_time="2026-08-06 13:00:00 PDT", http_status=500)
    handle = {**expected, "source": soak.NEGATIVE_SOURCE, "receiptSource": soak.NEGATIVE_ROUTE, "rendered": {"matchingRows": 1, "requestId": "r-web-expected", "source": soak.NEGATIVE_SOURCE, "route": soak.NEGATIVE_ROUTE, "event": "api", "text": "failure"}, "redaction": {channel: True for channel in ("dom", "clipboard", "retained", "upload", "storage")}}
    other = receipt_row(1, request_id="other", source="browser", route="/other", event="api")
    evidence = {
        "browserEvents": [{"id": 1, "type": "api", "endpoint": "/other", "requestId": "other", "status": 500, "ok": False}],
        "browserLocalFailures": [{"id": 1, "level": "error", "requestId": "other", "source": "browser", "route": "/other", "event": "api", "wallTime": "2026-08-06 13:00:00 PDT", "deliveryOutcome": "failed", "status": 500}],
        "browserReceiptProjection": receipt_projection([other]),
        "integrityFailures": [],
    }

    soak.require_negative_acknowledgement(evidence, handle, receipt_projection())

    assert evidence["integrityFailures"] == ["negative browser error probe was not retained exactly"]


@pytest.mark.parametrize(
    ("events", "payload", "message"),
    [
        ([{"id": 0}], {"logs": [], "capacity": 2, "sequence": 0, "dropped": {"count": 0, "by_level": {}}}, "browser JS cursor"),
        ([{"id": 1}, {"id": 3}], {"logs": [], "capacity": 2, "sequence": 0, "dropped": {"count": 0, "by_level": {}}}, "browser JS cursor"),
        ([], {"logs": [{"id": 1}, {"id": 3}], "capacity": 3, "sequence": 3, "dropped": {"count": 1, "by_level": {"info": 1}}}, "server log cursor"),
        ([], {"logs": [], "capacity": 2, "sequence": 1, "dropped": {"count": 0, "by_level": {}}}, "server log cursor accounting"),
    ],
)
def test_sample_evidence_rejects_unaccounted_or_noncontiguous_cursors(events, payload, message):
    class EvidenceDriver:
        def execute_async_script(self, _script):
            return {"events": events, "failures": [], "receiptBarrier": clean_receipt_barrier(), "receiptProjection": receipt_projection(), "logsStatus": 200, "payload": {"ok": True, "epoch": "epoch", **payload}}

        def get_log(self, _name):
            return []

    with pytest.raises(AssertionError, match=message):
        soak.sample_evidence(EvidenceDriver())


def test_incremental_evidence_rejects_sequence_advance_without_record():
    previous = soak.evidence_baseline({"serverLogDropped": {"count": 0}, "cursors": {"js": 0, "server_epoch": "a", "server_sequence": 0, "server_log_ids": []}})
    evidence = {"browserEvents": [], "browserLocalFailures": [], "serverLogErrors": [], "browserLogFailures": [], "serverLogDropped": {"count": 0}, "cursors": {"js": 0, "server_epoch": "a", "server_sequence": 1, "server_log_ids": []}}
    classified, _ = soak.classify_incremental_evidence(evidence, previous)
    assert classified["integrityFailures"] == ["server log sequence advanced without retained or dropped record"]


def test_main_completion_bundle_check_does_not_navigate_away_late_browser_failure(tmp_path, monkeypatch):
    class Options:
        def add_argument(self, _arg): return None
        def set_capability(self, _name, _value): return None

    class Driver(FakeDriver):
        def __init__(self):
            super().__init__()
            self.navigations = 0
            self.late_event = None

        def set_page_load_timeout(self, _timeout): return None
        def set_script_timeout(self, _timeout): return None

        def get(self, _url):
            self.navigations += 1
            if self.navigations >= 3:
                self.late_event = None

        def execute_script(self, script, *args):
            if "document.scripts" in script:
                return "https://localhost:7443/static/yolomux.js"
            if "document.getElementById" in script:
                return True
            return super().execute_script(script, *args)

        def execute_async_script(self, _script, _url):
            return {"status": 200, "text": "bundle"}

    driver = Driver()
    identity = soak.ListenerIdentity(pid=1, cwd="/repo", started="now", head="a" * 40)
    samples = 0

    def sample(_driver):
        nonlocal samples
        samples += 1
        evidence = {"browserEvents": [], "browserLocalFailures": [], "browserReceiptBarrier": clean_receipt_barrier(), "serverLogErrors": [], "browserLogFailures": [], "serverLogDropped": {"count": 0}, "cursors": {"js": 0, "server_epoch": "a", "server_sequence": 0, "server_log_ids": []}}
        if samples == 2:
            driver.late_event = {"id": 1, "requestId": "late", "source": "browser", "route": "/late", "event": "api", "deliveryOutcome": "failed"}
            return evidence
        if driver.late_event is not None:
            evidence["browserEvents"] = [{"id": 1}]
            evidence["browserLocalFailures"] = [driver.late_event]
            evidence["cursors"]["js"] = 1
        return evidence

    output = tmp_path / "artifact.json"
    monkeypatch.setattr(tool, "webdriver", type("WebDriver", (), {"ChromeOptions": Options, "Chrome": lambda *_args, **_kwargs: driver})())
    monkeypatch.setattr(tool, "validate_arguments", lambda *_args: None)
    monkeypatch.setattr(tool, "cleanup_driver", lambda _driver: None)
    monkeypatch.setattr(soak, "listener_identity", lambda _port: identity)
    monkeypatch.setattr(soak, "install_auth_cookie", lambda *_args: None)
    monkeypatch.setattr(soak, "install_start_of_document_sentinels", lambda *_args: None)
    monkeypatch.setattr(soak, "assert_stats_hidden", lambda *_args: None)
    monkeypatch.setattr(soak, "sample_evidence", sample)
    monkeypatch.setattr(soak, "pacific_wall_time", lambda: "2026-08-05 12:00:00 PDT")
    monkeypatch.setattr(soak.time, "monotonic", iter((0.0, 0.0, 0.1, 0.2)).__next__)

    assert tool.main(["--url", "https://localhost:7443/", "--duration", "0", "--expected-head", "a" * 40, "--expected-bundle-sha256", hashlib.sha256(b"bundle").hexdigest(), "--expected-cwd", "/repo", "--output", str(output)]) == 1
    artifact = json.loads(output.read_text(encoding="utf-8"))
    assert artifact["failure"]["browserLocalFailures"][0]["requestId"] == "late"
    assert driver.navigations == 1


def finalizer_server_payload(logs=(), *, epoch="server-a", sequence=0, capacity=10, dropped=0):
    return {"ok": True, "logs": [dict(entry) for entry in logs], "epoch": epoch, "sequence": sequence, "capacity": capacity, "dropped": {"count": dropped, "by_level": {} if dropped == 0 else {"info": dropped}}}


def finalizer_previous(logs=(), *, epoch="server-a", sequence=0, capacity=10, dropped=0):
    values = [dict(entry) for entry in logs]
    return {
        "js": 0,
        "server_epoch": epoch,
        "server_sequence": sequence,
        "server_log_ids": [entry["id"] for entry in values],
        "server_log_records": values,
        "server_capacity": capacity,
        "drop_count": dropped,
        "server_dropped_by_level": {} if dropped == 0 else {"info": dropped},
    }


class FinalizerDriver:
    def __init__(self, *, projection=None, events=None, failures=None, chrome=None, stats=None):
        self.calls = []
        self.current_url = "https://localhost:7443/"
        self.projection = projection or receipt_projection(epoch="all")
        self.events = events or []
        self.failures = failures or []
        self.chrome = iter(chrome or ([], []))
        self.stats = stats or clean_stats_stream(2, 1, 2, "delta", source_generation=2, delta_revision=1)
        self.retired = False
        self.storage = {}
        self.scripts = []

    def get_log(self, _name):
        self.calls.append("chrome")
        return next(self.chrome)

    def execute_async_script(self, _script, *args):
        self.calls.append("fence")
        cursor = self.events[-1]["id"] if self.events else 0
        point = {"cursor": cursor, "projection": self.projection}
        return {"a": point, "b": point, "c": point, "completions": 1}

    def execute_script(self, _script, *args):
        self.scripts.append(_script)
        if "location.replace('about:blank')" in _script:
            self.calls.append("atomic")
            self.retired = True
            self.current_url = "about:blank"
            snapshot = {
                "events": self.events,
                "failures": self.failures,
                "projection": self.projection,
                "stats": self.stats,
                "journey": {"id": "journey-1", "reachable": True, "visitedSurfaces": []},
                "hiddenSentinel": {"installed": True, "everVisible": False, "checks": 3},
                "panelVisible": False,
                "page": {"visibility": "visible", "origin": "https://localhost:7443", "href": "https://localhost:7443/"},
            }
            self.storage[args[1]] = json.dumps({
                "marker": args[0],
                "phase": "unload",
                "snapshot": {
                    "events": self.events,
                    "failures": self.failures,
                    "projection": self.projection,
                },
            })
            return snapshot
        if "document.readyState" in _script:
            self.calls.append("blank")
            assert self.retired
            return {"href": "about:blank", "readyState": "complete"}
        assert not self.retired, "no app-page diagnostic call is allowed after atomic retirement"
        raise AssertionError(f"unexpected finalizer script: {_script}")

    def execute_cdp_cmd(self, command, payload):
        if command == "DOMStorage.getDOMStorageItems":
            self.calls.append("bridge")
            return {"entries": [[key, value] for key, value in self.storage.items()]}
        if command == "DOMStorage.removeDOMStorageItem":
            self.calls.append("cleanup")
            self.storage.pop(payload["key"], None)
            return {}
        raise AssertionError(f"unexpected CDP command: {command}")

    def get(self, url):
        self.calls.append("navigate")
        self.retired = True
        self.current_url = url


def test_atomic_finalizer_clean_call_order_and_retirement(monkeypatch):
    calls = []
    payloads = iter((finalizer_server_payload(), finalizer_server_payload()))
    driver = FinalizerDriver()
    monkeypatch.setattr(soak, "WebDriverWait", lambda current, _timeout: type("Wait", (), {"until": lambda self, predicate: calls.append("wait") or predicate(current)})())

    boundary = soak.finalize_live_browser_soak(
        driver,
        server_reader=lambda: calls.append("server") or next(payloads),
        expected_url="https://localhost:7443/",
        previous=finalizer_previous(),
        previous_stats=clean_stats_stream(1),
        baseline_projection=receipt_projection(epoch="all"),
        negative_handle=None,
    )

    assert calls == ["server", "wait", "server"]
    assert driver.calls == ["chrome", "fence", "atomic", "blank", "bridge", "cleanup", "chrome"]
    assert driver.current_url == "about:blank"
    assert not soak.evidence_failed(boundary["evidence"])


@pytest.mark.browser
@pytest.mark.socket
@pytest.mark.parametrize(
    ("lifecycle_mode", "failure_event"),
    (
        ("pagehide", "pagehide"),
        ("beforeunload", "beforeunload"),
        ("beforeunload_registers_unload", "unload"),
    ),
)
def test_atomic_finalizer_rejects_browser_failure_recorded_during_lifecycle(browser, gate_live_server, lifecycle_mode, failure_event):
    load_gate_browser(browser, gate_live_server)
    expected_url = browser.current_url
    setup = browser.execute_async_script(
        """
        const lifecycleMode = arguments[0];
        const done = arguments[arguments.length - 1];
        (async () => {
          if (jsDebugCurrentObservationState.livenessTimer !== null) {
            clearInterval(jsDebugCurrentObservationState.livenessTimer);
            jsDebugCurrentObservationState.livenessTimer = null;
          }
          await flushJsDebugCurrentObservations();
          const now = Date.now();
          window.__yolomuxStatsHiddenSentinel = {installed: true, everVisible: false, checks: 1};
          window.jsDebugCurrentStatsStreamEvidence = () => ({
            moduleReady: true,
            clientReady: true,
            controllerReady: true,
            generationReady: true,
            panelVisible: false,
            paintedGenerationKey: '',
            sampledAtMs: Date.now(),
            everVisible: false,
            stream: {
              running: true,
              visible: true,
              healthy: true,
              streamOpen: true,
              streamEpoch: 1,
              deliverySequence: 1,
              acceptedDeltaSequence: 0,
              lastDeliveryKind: 'ready',
              lastDeliveryAtMs: now,
              lastDeliveryEpoch: 1,
              rangeSeconds: 300,
              resolutionSeconds: 1,
              sourceGeneration: 1,
              cacheGeneration: 1,
              deltaRevision: 0,
              },
            });
          const recordFailure = eventType => {
            recordJsDebugEvent('client_failure', {
              level: 'error',
              message: `${eventType} retirement failure`,
              requestId: `r-${eventType}-retirement-1`,
              source: 'browser-lifecycle',
              route: `/browser/${eventType}`,
              eventType,
              deliveryOutcome: 'failed',
              wallTime: '2026-08-06 14:30:00 PDT',
            });
          };
          if (lifecycleMode === 'beforeunload_registers_unload') {
            window.addEventListener('beforeunload', () => {
              window.addEventListener('unload', () => recordFailure('unload'), {once: true});
            }, {once: true});
          } else {
            window.addEventListener(lifecycleMode, () => recordFailure(lifecycleMode), {once: true});
          }
          done({
            stats: window.jsDebugCurrentStatsStreamEvidence(),
            projection: jsDebugCurrentObservationReceiptProjection(),
          });
        })().catch(error => done({error: String(error)}));
        """,
        lifecycle_mode,
    )
    assert setup.get("error") is None, setup
    baseline_projection = soak.validate_negative_probe_baseline(setup["projection"])
    browser.get_log("browser")

    try:
        boundary = soak.finalize_live_browser_soak(
            browser,
            server_reader=lambda: finalizer_server_payload(),
            expected_url=expected_url,
            previous=finalizer_previous(),
            previous_stats=setup["stats"],
            baseline_projection=baseline_projection,
            negative_handle=None,
        )

        assert boundary["status"] == "failed", boundary
        assert boundary["phaseFailures"] == [], boundary
        request_id = f"r-{failure_event}-retirement-1"
        failures = [entry for entry in boundary["evidence"]["browserLocalFailures"] if entry["requestId"] == request_id]
        assert failures == [{
            "id": failures[0]["id"],
            "level": "error",
            "message": f"{failure_event} retirement failure",
            "requestId": request_id,
            "source": "browser-lifecycle",
            "route": f"/browser/{failure_event}",
            "event": failure_event,
            "wallTime": "2026-08-06 14:30:00 PDT",
            "deliveryOutcome": "failed",
            "status": None,
        }]
        projection = boundary["evidence"]["browserReceiptProjection"]
        matching_receipts = [receipt for receipt in projection["receipts"] if receipt["requestId"] == request_id]
        assert len(matching_receipts) == 1
        matching_receipt = matching_receipts[0]
        assert {
            field: matching_receipt[field]
            for field in ("eventId", "requestId", "source", "route", "event", "wallTime", "deliveryOutcome")
        } == {
            "eventId": failures[0]["id"],
            "requestId": request_id,
            "source": "/browser-lifecycle",
            "route": f"/browser/{failure_event}",
            "event": failure_event,
            "wallTime": "2026-08-06 14:30:00 PDT",
            "deliveryOutcome": "failed",
        }
        assert matching_receipt["status"] in {"accepted", "pending", "retrying", "rejected", "dropped"}
        if matching_receipt["status"] == "accepted":
            assert matching_receipt not in projection["barrier"]["blocking"]
        else:
            assert matching_receipt in projection["barrier"]["blocking"]
        baseline_by_key = {receipt["key"]: receipt for receipt in baseline_projection["receipts"]}
        final_by_key = {receipt["key"]: receipt for receipt in projection["receipts"]}
        assert all(final_by_key.get(key) == receipt for key, receipt in baseline_by_key.items())
        delta = soak.validate_browser_retirement_delta(boundary["eventRing"]["delta"])
        assert delta["reason"] == soak.RETIREMENT_DELTA_RECORDED_FAILURES, delta
        assert delta["recordedFailures"] >= 1, delta
        assert f"browser recorded failing diagnostics during retirement: {delta['recordedFailures']}" in boundary["evidence"]["integrityFailures"], boundary["evidence"]["integrityFailures"]
        assert boundary["blankReadiness"] == {"href": "about:blank", "readyState": "complete"}
        assert boundary["chromeBeforeRetirement"] is not None
        assert boundary["chromeAfterRetirement"] is not None
        assert boundary["serverAfter"] is not None
    finally:
        load_gate_browser(browser, gate_live_server)


SATURATED_RING_SETUP_SCRIPT = """
const injectWarning = arguments[0];
const done = arguments[arguments.length - 1];
(async () => {
  if (jsDebugCurrentObservationState.livenessTimer !== null) {
    clearInterval(jsDebugCurrentObservationState.livenessTimer);
    jsDebugCurrentObservationState.livenessTimer = null;
  }
  await flushJsDebugCurrentObservations();
  const now = Date.now();
  window.__yolomuxStatsHiddenSentinel = {installed: true, everVisible: false, checks: 1};
  window.jsDebugCurrentStatsStreamEvidence = () => ({
    moduleReady: true,
    clientReady: true,
    controllerReady: true,
    generationReady: true,
    panelVisible: false,
    paintedGenerationKey: '',
    sampledAtMs: Date.now(),
    everVisible: false,
    stream: {
      running: true,
      visible: true,
      healthy: true,
      streamOpen: true,
      streamEpoch: 1,
      deliverySequence: 1,
      acceptedDeltaSequence: 0,
      lastDeliveryKind: 'ready',
      lastDeliveryAtMs: now,
      lastDeliveryEpoch: 1,
      rangeSeconds: 300,
      resolutionSeconds: 1,
      sourceGeneration: 1,
      cacheGeneration: 1,
      deltaRevision: 0,
    },
  });
  // Silence the live stats diagnostic producer so the retirement window contains only
  // the frames this test records; the warning case injects its own through it.
  const recordStatsDiagnostic = window.recordJsDebugStatsDiagnostic;
  window.recordJsDebugStatsDiagnostic = () => {};
  // Own the complete retained window. Filling only the unused slots leaves live API/SSE
  // events in the ring; those events can still receive response-byte or phase-timing
  // enrichment after the atomic snapshot, which is a real same-id mutation but unrelated
  // to the immutable ring-eviction lifecycle this fixture is meant to exercise.
  jsDebugEvents.splice(0, jsDebugEvents.length);
  while (jsDebugEvents.length < jsDebugEventLimit) {
    recordJsDebugEvent('long_task', {durationMs: 1, testTag: 'retirement-owned-saturation'});
  }
  window.addEventListener('pagehide', () => {
    recordJsDebugEvent('long_task', {durationMs: 2});
    if (injectWarning) {
      recordStatsDiagnostic('warning', 'YO!stats: injected retirement warning', {route: '/api/stats-stream'});
    }
  }, {once: true});
  done({
    limit: jsDebugEventLimit,
    length: jsDebugEvents.length,
    firstId: jsDebugEvents[0].id,
    lastId: jsDebugEvents[jsDebugEvents.length - 1].id,
    ownedEvents: jsDebugEvents.filter(event => event.type === 'long_task' && event.testTag === 'retirement-owned-saturation').length,
    stats: window.jsDebugCurrentStatsStreamEvidence(),
    projection: jsDebugCurrentObservationReceiptProjection(),
  });
})().catch(error => done({error: String(error)}));
"""


@pytest.mark.browser
@pytest.mark.socket
def test_atomic_finalizer_accepts_observed_browser_ring_eviction_during_retirement(browser, gate_live_server):
    """A saturated jsDebugEvents ring evicts already-observed events at retirement.

    ``jsDebugEvents`` is capped at ``jsDebugEventLimit``; once it is full, each event
    recorded while the page retires drops the oldest retained event, so the retirement
    snapshot is not a raw prefix extension of the atomic snapshot. Nothing the finalizer
    already read is lost, so the finalizer must report the eviction as a typed outcome
    instead of raising.
    """
    load_gate_browser(browser, gate_live_server)
    expected_url = browser.current_url
    setup = browser.execute_async_script(SATURATED_RING_SETUP_SCRIPT, False)
    assert setup.get("error") is None, setup
    assert setup["length"] == setup["limit"], setup
    assert setup["ownedEvents"] == setup["limit"], setup
    assert setup["lastId"] - setup["firstId"] + 1 == setup["limit"], setup
    # Inject a deterministic pre-atomic shift, then ONE deliberate benign interleaving event.
    # The shift models the load this test exists to tolerate; the interleaving models a single
    # benign event that arrives between the setup snapshot and the finalizer's fence - which is
    # exactly why anchoring the retained window to `setup["firstId"] + shift` was the stale
    # oracle. Both counts are arbitrary but fixed and must stay below the ring limit, and each
    # is measured synchronously so the immediate-shift relation is exact with no async gap.
    PRE_ATOMIC_SHIFT = 4
    # Deterministically model the one producer that made the cross-round-trip oracle flaky: a real
    # long_task PerformanceObserver event can fire between the setup snapshot and the shift script
    # under load, sliding the saturated ring one extra id. Recording it here proves the tolerance
    # without depending on host load to (occasionally) produce it.
    browser.execute_script("recordJsDebugEvent('long_task', {durationMs: 2});")
    SHIFT_TAG = "retirement-pre-atomic-shift"
    shifted = browser.execute_script(
        "const baselineFirstId = jsDebugEvents[0].id;"
        "const baselineLastId = jsDebugEvents[jsDebugEvents.length - 1].id;"
        "const injected = [];"
        "for (let i = 0; i < arguments[0]; i += 1) {"
        "  recordJsDebugEvent('long_task', {durationMs: 3, testTag: arguments[1]});"
        "  const event = jsDebugEvents[jsDebugEvents.length - 1];"
        "  injected.push({id: event.id, type: event.type, tag: event.testTag});"
        "}"
        "return {baselineFirstId, baselineLastId, injected, firstId: jsDebugEvents[0].id, lastId: jsDebugEvents[jsDebugEvents.length - 1].id};",
        PRE_ATOMIC_SHIFT,
        SHIFT_TAG,
    )
    # Ownership-safe immediate-shift relation, made atomic and proven by exact tagged IDs: baseline
    # head/tail ids are captured INSIDE the shift script, and the PRE_ATOMIC_SHIFT test-owned events
    # (uniquely tagged SHIFT_TAG so an ambient untagged long_task can never be mistaken for one) are
    # the consecutive ids baselineLastId+1..+PRE_ATOMIC_SHIFT that both extend the tail and evict
    # exactly that many from the head of a saturated ring - no async gap. Anchoring to setup["firstId"]
    # across the Selenium round-trip was the stale oracle: an ambient long_task landing in that gap
    # slides the saturated ring and made the cross-round-trip equality false while the shift itself is
    # still exactly PRE_ATOMIC_SHIFT test-owned events.
    assert len(shifted["injected"]) == PRE_ATOMIC_SHIFT, {"shifted": shifted, "setup": setup}
    assert [event["id"] for event in shifted["injected"]] == [shifted["baselineLastId"] + offset for offset in range(1, PRE_ATOMIC_SHIFT + 1)], {"shifted": shifted, "setup": setup}
    assert all(event["type"] == "long_task" and event["tag"] == SHIFT_TAG for event in shifted["injected"]), {"shifted": shifted, "setup": setup}
    assert shifted["lastId"] == shifted["baselineLastId"] + PRE_ATOMIC_SHIFT, {"shifted": shifted, "setup": setup}
    assert shifted["firstId"] == shifted["baselineFirstId"] + PRE_ATOMIC_SHIFT, {"shifted": shifted, "setup": setup}
    BENIGN_INTERLEAVE = 1
    INTERLEAVE_TAG = "retirement-benign-interleave"
    interleaved = browser.execute_script(
        "const baselineFirstId = jsDebugEvents[0].id;"
        "const baselineLastId = jsDebugEvents[jsDebugEvents.length - 1].id;"
        "const injected = [];"
        "for (let i = 0; i < arguments[0]; i += 1) {"
        "  recordJsDebugEvent('long_task', {durationMs: 4, testTag: arguments[1]});"
        "  const event = jsDebugEvents[jsDebugEvents.length - 1];"
        "  injected.push({id: event.id, type: event.type, tag: event.testTag});"
        "}"
        "return {baselineFirstId, baselineLastId, injected, firstId: jsDebugEvents[0].id, lastId: jsDebugEvents[jsDebugEvents.length - 1].id};",
        BENIGN_INTERLEAVE,
        INTERLEAVE_TAG,
    )
    # The one deliberate benign interleaving, tagged distinctly from the shift and proven by exact id,
    # atomically self-baselined so a further ambient long_task between the shift and this script cannot
    # perturb the accounting.
    assert len(interleaved["injected"]) == BENIGN_INTERLEAVE, {"interleaved": interleaved, "shifted": shifted}
    assert [event["id"] for event in interleaved["injected"]] == [interleaved["baselineLastId"] + offset for offset in range(1, BENIGN_INTERLEAVE + 1)], {"interleaved": interleaved, "shifted": shifted}
    assert all(event["type"] == "long_task" and event["tag"] == INTERLEAVE_TAG for event in interleaved["injected"]), {"interleaved": interleaved, "shifted": shifted}
    assert interleaved["firstId"] == interleaved["baselineFirstId"] + BENIGN_INTERLEAVE, {"interleaved": interleaved, "shifted": shifted}
    baseline_projection = soak.validate_negative_probe_baseline(setup["projection"])
    browser.get_log("browser")

    try:
        boundary = soak.finalize_live_browser_soak(
            browser,
            server_reader=lambda: finalizer_server_payload(),
            expected_url=expected_url,
            previous={**finalizer_previous(), "js": setup["lastId"]},
            previous_stats=setup["stats"],
            baseline_projection=baseline_projection,
            negative_handle=None,
        )

        assert boundary["phaseFailures"] == [], boundary
        ring = boundary["eventRing"]["retirement"]
        assert soak.validate_browser_event_ring_outcome(ring, phase="retirement") == ring
        assert ring["reason"] == soak.BROWSER_EVENT_RING_EVICTED, ring
        assert ring["appended"] >= 1 and ring["evicted"] == ring["appended"], ring
        atomic_ring = boundary["eventRing"]["atomic"]
        # Cross-phase handoff is the one relation validate_browser_event_ring_outcome cannot
        # see: retirement's prior window must be the window the atomic snapshot retained.
        # Anchoring to `setup` was wrong because benign events can arrive between the setup
        # snapshot and the finalizer's fence, shifting a saturated ring before retirement.
        assert (ring["priorFirstId"], ring["priorCursor"]) == (
            atomic_ring["retainedFirstId"],
            atomic_ring["retainedLastId"],
        ), {"atomic": atomic_ring, "retirement": ring}
        # The retained-window arithmetic itself is owned by the validator called above; a
        # local recomputation here would be a second copy of one fact and would drift again.
        # Anchoring the retained window to `setup["firstId"] + PRE_ATOMIC_SHIFT` was the stale
        # oracle: the deliberate benign interleaving above already slid the saturated ring past
        # it, and any further legitimate repaint before the fence slides it more. So this asserts
        # only the monotone floor - the interleaved window is the least the atomic snapshot could
        # retain - and leaves the exact value to the observed-window relations.
        assert atomic_ring["retainedFirstId"] >= interleaved["firstId"], {
            "atomic": atomic_ring,
            "interleaved": interleaved,
        }
        assert len(boundary["evidence"]["browserEvents"]) == setup["limit"], boundary["evidence"]["browserEvents"]
        delta = soak.validate_browser_retirement_delta(boundary["eventRing"]["delta"])
        assert delta["reason"] == soak.RETIREMENT_DELTA_BENIGN, delta
        assert delta["appendedEvents"] == ring["appended"] and delta["evictedEvents"] == ring["evicted"], delta
        assert delta["recordedFailures"] == 0 and delta["evictedFailures"] == 0 and delta["addedBlockingReceipts"] == 0, delta
        assert delta["integrityFailures"] == [], delta
        assert boundary["evidence"]["integrityFailures"] == [], boundary["evidence"]["integrityFailures"]
        assert boundary["blankReadiness"] == {"href": "about:blank", "readyState": "complete"}
    finally:
        load_gate_browser(browser, gate_live_server)


class RingEvictionFinalizerDriver(FinalizerDriver):
    """Retire with a saturated ring that evicts observed events while appending new ones."""

    def __init__(self, *, limit, appended, retired_failures=None, retired_projection=None, events=None, **kwargs):
        super().__init__(events=[{"id": index} for index in range(1, limit + 1)] if events is None else events, **kwargs)
        self.limit = limit
        self.appended = appended
        self.retired_failures = [] if retired_failures is None else [dict(entry) for entry in retired_failures]
        self.retired_projection = retired_projection

    def execute_script(self, _script, *args):
        snapshot = super().execute_script(_script, *args)
        if "location.replace('about:blank')" not in _script:
            return snapshot
        total = self.limit + self.appended
        observed = {event["id"]: event for event in self.events}
        retired = [dict(observed.get(index, {"id": index})) for index in range(total - self.limit + 1, total + 1)]
        self.storage[args[1]] = json.dumps({
            "marker": args[0],
            "phase": "unload",
            "snapshot": {
                "events": retired,
                "failures": self.retired_failures,
                "projection": self.projection if self.retired_projection is None else self.retired_projection,
            },
        })
        return snapshot


def retirement_boundary(monkeypatch, driver, *, previous_js=3):
    payloads = iter((finalizer_server_payload(), finalizer_server_payload()))
    monkeypatch.setattr(soak, "WebDriverWait", lambda current, _timeout: type("Wait", (), {"until": lambda self, predicate: predicate(current)})())
    return soak.finalize_live_browser_soak(
        driver,
        server_reader=lambda: next(payloads),
        expected_url="https://localhost:7443/",
        previous={**finalizer_previous(), "js": previous_js},
        previous_stats=clean_stats_stream(1),
        baseline_projection=receipt_projection(epoch="all"),
        negative_handle=None,
    )


def test_atomic_finalizer_reports_saturated_ring_eviction_as_typed_outcome(monkeypatch):
    payloads = iter((finalizer_server_payload(), finalizer_server_payload()))
    driver = RingEvictionFinalizerDriver(limit=200, appended=3)
    monkeypatch.setattr(soak, "WebDriverWait", lambda current, _timeout: type("Wait", (), {"until": lambda self, predicate: predicate(current)})())

    boundary = soak.finalize_live_browser_soak(
        driver,
        server_reader=lambda: next(payloads),
        expected_url="https://localhost:7443/",
        previous={**finalizer_previous(), "js": 3},
        previous_stats=clean_stats_stream(1),
        baseline_projection=receipt_projection(epoch="all"),
        negative_handle=None,
    )

    assert boundary["phaseFailures"] == [], boundary
    assert boundary["eventRing"]["retirement"] == {
        "phase": "retirement",
        "reason": soak.BROWSER_EVENT_RING_EVICTED,
        "evicted": 3,
        "appended": 3,
        "priorCursor": 200,
        "priorFirstId": 1,
        "retainedFirstId": 4,
        "retainedLastId": 203,
    }
    assert [entry["id"] for entry in boundary["evidence"]["browserEvents"]] == list(range(4, 204))
    assert boundary["evidence"]["integrityFailures"] == []
    assert boundary["eventRing"]["delta"] == {
        "reason": soak.RETIREMENT_DELTA_BENIGN,
        "appendedEvents": 3,
        "evictedEvents": 3,
        "mutatedEvents": 0,
        "recordedFailures": 0,
        "evictedFailures": 0,
        "addedReceipts": 0,
        "addedBlockingReceipts": 0,
        "integrityFailures": [],
    }
    assert not soak.evidence_failed(boundary["evidence"]), boundary["evidence"]


def test_atomic_finalizer_rejects_ring_eviction_of_unobserved_events(monkeypatch):
    """More than one ring capacity of events during retirement evicts events nobody read."""
    payloads = iter((finalizer_server_payload(), finalizer_server_payload()))
    driver = RingEvictionFinalizerDriver(limit=200, appended=205)
    monkeypatch.setattr(soak, "WebDriverWait", lambda current, _timeout: type("Wait", (), {"until": lambda self, predicate: predicate(current)})())

    boundary = soak.finalize_live_browser_soak(
        driver,
        server_reader=lambda: next(payloads),
        expected_url="https://localhost:7443/",
        previous=finalizer_previous(),
        previous_stats=clean_stats_stream(1),
        baseline_projection=receipt_projection(epoch="all"),
        negative_handle=None,
    )

    assert [(item["phase"], item["message"]) for item in boundary["phaseFailures"]] == [
        ("retirementBridge", "browser retirement event cursor evicted unobserved events"),
    ]


@pytest.mark.parametrize(
    ("current_ids", "prior_ids", "prior_cursor", "message"),
    (
        ([2, 3, 5], [2, 3], 3, "browser retirement event cursor is malformed"),
        ([0, 1], [1], 1, "browser retirement event cursor is malformed"),
        ([1, 2], [1, 2, 3], 3, "browser retirement event cursor moved backwards"),
        ([5, 6, 7], [1, 2, 3], 3, "browser retirement event cursor evicted unobserved events"),
        ([3, 4, 5], [1, 2, 4], 4, "browser retirement event cursor lost its observed window"),
    ),
)
def test_browser_event_ring_extension_rejects_loss_reordering_and_gaps(current_ids, prior_ids, prior_cursor, message):
    with pytest.raises(AssertionError, match=re.escape(message)):
        soak.browser_event_ring_extension(current_ids, phase="retirement", prior_cursor=prior_cursor, prior_ids=prior_ids)


def test_browser_event_ring_extension_reports_observed_eviction_and_intact_windows():
    evicted = soak.browser_event_ring_extension([3, 4, 5], phase="retirement", prior_cursor=4, prior_ids=[1, 2, 3, 4])
    assert evicted == {
        "phase": "retirement",
        "reason": soak.BROWSER_EVENT_RING_EVICTED,
        "evicted": 2,
        "appended": 1,
        "priorCursor": 4,
        "priorFirstId": 1,
        "retainedFirstId": 3,
        "retainedLastId": 5,
    }
    intact = soak.browser_event_ring_extension([1, 2, 3], phase="atomic", prior_cursor=2)
    assert intact["reason"] == soak.BROWSER_EVENT_RING_INTACT and intact["evicted"] == 0 and intact["appended"] == 1


@pytest.mark.browser
@pytest.mark.socket
def test_atomic_finalizer_rejects_warning_recorded_during_saturated_ring_retirement(browser, gate_live_server):
    """Narrowing benign ring churn must not swallow a warning recorded in the same window.

    This is the control for the narrowed retirement check: the ring is saturated and
    evicting, benign frames are appended alongside a single warning-level diagnostic,
    and the soak must still go red naming the warning.
    """
    load_gate_browser(browser, gate_live_server)
    expected_url = browser.current_url
    setup = browser.execute_async_script(SATURATED_RING_SETUP_SCRIPT, True)
    assert setup.get("error") is None, setup
    assert setup["length"] == setup["limit"], setup
    assert setup["ownedEvents"] == setup["limit"], setup
    baseline_projection = soak.validate_negative_probe_baseline(setup["projection"])
    browser.get_log("browser")

    try:
        boundary = soak.finalize_live_browser_soak(
            browser,
            server_reader=lambda: finalizer_server_payload(),
            expected_url=expected_url,
            previous=finalizer_previous(),
            previous_stats=setup["stats"],
            baseline_projection=baseline_projection,
            negative_handle=None,
        )

        assert boundary["phaseFailures"] == [], boundary
        ring = boundary["eventRing"]["retirement"]
        assert ring["reason"] == soak.BROWSER_EVENT_RING_EVICTED and ring["evicted"] >= 1, ring
        delta = soak.validate_browser_retirement_delta(boundary["eventRing"]["delta"])
        assert delta["reason"] == soak.RETIREMENT_DELTA_RECORDED_FAILURES, delta
        assert delta["recordedFailures"] >= 1, delta
        assert f"browser recorded failing diagnostics during retirement: {delta['recordedFailures']}" in boundary["evidence"]["integrityFailures"], boundary["evidence"]["integrityFailures"]
        injected = [entry for entry in boundary["evidence"]["browserLocalFailures"] if entry["message"] == "YO!stats: injected retirement warning"]
        assert len(injected) == 1 and injected[0]["level"] == "warning", boundary["evidence"]["browserLocalFailures"]
        assert soak.evidence_failed(boundary["evidence"]), boundary["evidence"]
        assert boundary["status"] == "failed", boundary
    finally:
        load_gate_browser(browser, gate_live_server)


@pytest.mark.parametrize(("status", "blocking"), (("pending", 1), ("rejected", 1), ("accepted", 0)))
def test_atomic_finalizer_rejects_any_receipt_added_during_retirement(monkeypatch, status, blocking):
    """Every added receipt key is signal, including one already flipped to accepted.

    ``queueJsDebugCurrentObservation`` writes a receipt key only under
    ``if (releaseBlocking)``, so a key that did not exist at the atomic snapshot means a
    release-blocking event was recorded during retirement no matter what status it
    reached before the page unloaded.
    """
    added = receipt_row(event_id=201, status=status, epoch="page-1", source="/api/stats-stream", route="/api/stats-stream", event="stats-generation")
    driver = RingEvictionFinalizerDriver(limit=200, appended=3, retired_projection=receipt_projection([added], epoch="all"))

    boundary = retirement_boundary(monkeypatch, driver)

    assert boundary["phaseFailures"] == [], boundary
    delta = soak.validate_browser_retirement_delta(boundary["eventRing"]["delta"])
    assert delta["reason"] == soak.RETIREMENT_DELTA_ADDED_RECEIPTS, delta
    assert delta["addedReceipts"] == 1 and delta["addedBlockingReceipts"] == blocking, delta
    assert f"browser retirement added durable receipts: 1 ({blocking} blocking)" in boundary["evidence"]["integrityFailures"]
    assert soak.evidence_failed(boundary["evidence"])


def test_atomic_finalizer_rejects_failure_recorded_during_retirement_under_eviction(monkeypatch):
    recorded = {"id": 201, "type": "client_failure", "level": "error", "message": "retirement failure", "requestId": "r-1"}
    driver = RingEvictionFinalizerDriver(limit=200, appended=3, retired_failures=[recorded])

    boundary = retirement_boundary(monkeypatch, driver)

    delta = soak.validate_browser_retirement_delta(boundary["eventRing"]["delta"])
    assert delta["reason"] == soak.RETIREMENT_DELTA_RECORDED_FAILURES, delta
    assert delta["recordedFailures"] == 1, delta
    assert "browser recorded failing diagnostics during retirement: 1" in boundary["evidence"]["integrityFailures"]
    assert soak.evidence_failed(boundary["evidence"])


def test_atomic_finalizer_rejects_eviction_of_an_observed_failure(monkeypatch):
    """Tolerating ring eviction must never discard a failure the finalizer already read."""
    observed = {"id": 1, "type": "client_failure", "level": "error", "message": "observed failure", "requestId": "r-0"}
    driver = RingEvictionFinalizerDriver(limit=200, appended=3, failures=[observed])

    boundary = retirement_boundary(monkeypatch, driver)

    delta = soak.validate_browser_retirement_delta(boundary["eventRing"]["delta"])
    assert delta["reason"] == soak.RETIREMENT_DELTA_EVICTED_FAILURES, delta
    assert delta["evictedFailures"] == 1, delta
    assert "browser retirement evicted observed failing diagnostics: 1" in boundary["evidence"]["integrityFailures"]
    assert soak.evidence_failed(boundary["evidence"])


class RewrittenEventFinalizerDriver(RingEvictionFinalizerDriver):
    """Retire with an already-observed event whose retained copy differs under the same id."""

    def __init__(self, *, rewrite, **kwargs):
        super().__init__(**kwargs)
        self.rewrite = rewrite

    def execute_script(self, _script, *args):
        snapshot = super().execute_script(_script, *args)
        if "location.replace('about:blank')" not in _script:
            return snapshot
        journal = json.loads(self.storage[args[1]])
        first = journal["snapshot"]["events"][0]
        journal["snapshot"]["events"][0] = self.rewrite(first)
        self.storage[args[1]] = json.dumps(journal)
        return snapshot


def test_atomic_finalizer_rejects_content_change_of_an_observed_event(monkeypatch):
    """Tolerating ring eviction must not tolerate rewriting an event already read."""
    driver = RewrittenEventFinalizerDriver(
        limit=200,
        appended=3,
        events=[{"id": index, "type": "sse", "message": "original"} for index in range(1, 201)],
        rewrite=lambda event: {**event, "message": "rewritten after observation"},
    )

    boundary = retirement_boundary(monkeypatch, driver)

    delta = soak.validate_browser_retirement_delta(boundary["eventRing"]["delta"])
    assert delta["reason"] == soak.RETIREMENT_DELTA_MUTATED_EVENTS, delta
    assert delta["mutatedEvents"] == 1, delta
    assert "browser retirement mutated observed diagnostics: 1" in boundary["evidence"]["integrityFailures"]
    assert soak.evidence_failed(boundary["evidence"])


@pytest.mark.parametrize("rewrite", (
    lambda event: {key: value for key, value in event.items() if value is not None},
    lambda event: {**event, "route": None, "computeMs": None},
), ids=("journal_drops_null_keys", "webdriver_materialises_null_keys"))
def test_atomic_finalizer_accepts_null_key_asymmetry_between_capture_channels(monkeypatch, rewrite):
    """Null-valued keys differ by capture channel, not by content, and are not mutation.

    The atomic snapshot returns through WebDriver marshalling, which materialises
    ``undefined`` as ``null``; the retirement journal goes through ``JSON.stringify``,
    which drops those keys. Measured on live ``sse`` events: identical values on every
    shared key, differing key sets on ``error``/``ok``/``route``/``source``/
    ``deliveryOutcome``/``computeMs``/``disconnectEpisode``/``disconnectedMs``/
    ``serverEventId``.
    """
    driver = RewrittenEventFinalizerDriver(
        limit=200,
        appended=3,
        events=[{"id": index, "type": "sse", "error": None, "ok": None, "route": None} for index in range(1, 201)],
        rewrite=rewrite,
    )

    boundary = retirement_boundary(monkeypatch, driver)

    delta = soak.validate_browser_retirement_delta(boundary["eventRing"]["delta"])
    assert delta["reason"] == soak.RETIREMENT_DELTA_BENIGN, delta
    assert delta["mutatedEvents"] == 0, delta
    assert boundary["evidence"]["integrityFailures"] == []
    assert not soak.evidence_failed(boundary["evidence"])


CLEAN_RETIREMENT_DELTA = {"reason": "quiet", "appendedEvents": 0, "evictedEvents": 0, "mutatedEvents": 0, "recordedFailures": 0, "evictedFailures": 0, "addedReceipts": 0, "addedBlockingReceipts": 0, "integrityFailures": []}


@pytest.mark.parametrize("delta", (
    {**CLEAN_RETIREMENT_DELTA, "appendedEvents": 1},
    {**CLEAN_RETIREMENT_DELTA, "reason": "benign_activity"},
    {**CLEAN_RETIREMENT_DELTA, "reason": "benign_activity", "appendedEvents": 1, "recordedFailures": 1, "integrityFailures": ["x"]},
    {**CLEAN_RETIREMENT_DELTA, "reason": "recorded_failures", "appendedEvents": 1, "recordedFailures": 1},
    {**CLEAN_RETIREMENT_DELTA, "reason": "recorded_failures", "appendedEvents": 1, "integrityFailures": ["x"]},
    {**CLEAN_RETIREMENT_DELTA, "reason": "mutated_events", "mutatedEvents": 0, "integrityFailures": ["x"]},
    {**CLEAN_RETIREMENT_DELTA, "reason": "added_receipts", "addedReceipts": 1, "addedBlockingReceipts": 2, "integrityFailures": ["x"]},
    {**CLEAN_RETIREMENT_DELTA, "reason": "unknown"},
    {key: value for key, value in CLEAN_RETIREMENT_DELTA.items() if key != "mutatedEvents"},
))
def test_validate_browser_retirement_delta_rejects_incoherent_outcomes(delta):
    with pytest.raises(AssertionError, match="browser retirement delta is malformed"):
        soak.validate_browser_retirement_delta(delta)


@pytest.mark.parametrize("delta", (
    CLEAN_RETIREMENT_DELTA,
    {**CLEAN_RETIREMENT_DELTA, "reason": "benign_activity", "appendedEvents": 3, "evictedEvents": 3},
    {**CLEAN_RETIREMENT_DELTA, "reason": "added_receipts", "addedReceipts": 1, "addedBlockingReceipts": 1, "integrityFailures": ["browser retirement added durable receipts: 1 (1 blocking)"]},
))
def test_validate_browser_retirement_delta_accepts_every_reason_the_classifier_emits(delta):
    """A signal-carrying delta with no ring activity is reachable and must validate."""
    assert soak.validate_browser_retirement_delta(delta) == delta


RING_OUTCOMES_THE_PRODUCER_EMITS = (
    ("empty ring with no prior window", [], 0, None),
    ("intact first observation", [1, 2, 3], 0, None),
    ("intact after a fence cursor", [1, 2, 3, 4, 5], 3, None),
    ("cursor-only eviction", list(range(51, 61)), 55, None),
    ("prior window fully retained", [1, 2, 3, 4, 5], 3, [1, 2, 3]),
    ("prior window losing its oldest event", [2, 3, 4, 5, 6], 4, [1, 2, 3, 4]),
    ("prior window evicted entirely", [5, 6, 7, 8, 9], 4, [1, 2, 3, 4]),
    ("saturated live-shaped ring", list(range(132, 332)), 330, list(range(131, 331))),
)


@pytest.mark.parametrize(
    ("ids", "prior_cursor", "prior_ids"),
    [case[1:] for case in RING_OUTCOMES_THE_PRODUCER_EMITS],
    ids=[case[0] for case in RING_OUTCOMES_THE_PRODUCER_EMITS],
)
def test_validate_browser_event_ring_outcome_accepts_every_outcome_the_producer_emits(ids, prior_cursor, prior_ids):
    """Replaying the recorded window through the producer must accept everything it can emit."""
    produced = soak.browser_event_ring_extension(ids, phase="retirement", prior_cursor=prior_cursor, prior_ids=prior_ids)

    assert soak.validate_browser_event_ring_outcome(produced, phase="retirement") == produced


@pytest.mark.parametrize(
    "outcome",
    (
        {"phase": "atomic", "reason": "intact", "evicted": 0, "appended": 2, "priorCursor": 50, "priorFirstId": 0, "retainedFirstId": 1, "retainedLastId": 2},
        {"phase": "atomic", "reason": "intact", "evicted": 0, "appended": 999, "priorCursor": 50, "priorFirstId": 0, "retainedFirstId": 1, "retainedLastId": 51},
        {"phase": "atomic", "reason": "intact", "evicted": 0, "appended": 0, "priorCursor": 0, "priorFirstId": 0, "retainedFirstId": 900, "retainedLastId": 5},
        {"phase": "atomic", "reason": "intact", "evicted": 0, "appended": 0, "priorCursor": 10, "priorFirstId": 500, "retainedFirstId": 10, "retainedLastId": 10},
        {"phase": "atomic", "reason": "ring_eviction", "evicted": 7777, "appended": 1, "priorCursor": 330, "priorFirstId": 131, "retainedFirstId": 132, "retainedLastId": 331},
        {"phase": "atomic", "reason": "ring_eviction", "evicted": 4, "appended": 0, "priorCursor": 0, "priorFirstId": 0, "retainedFirstId": 0, "retainedLastId": 0},
        {"phase": "atomic", "reason": "ring_eviction", "evicted": 11, "appended": 9, "priorCursor": 10, "priorFirstId": 1, "retainedFirstId": 12, "retainedLastId": 20},
        {"phase": "atomic", "reason": "intact", "evicted": 0, "appended": 0, "priorCursor": 0, "priorFirstId": 0, "retainedFirstId": 0, "retainedLastId": 4},
    ),
    ids=(
        "intact range 1..2 after a prior cursor of 50",
        "appended 999 for a range ending at 51",
        "inverted retained window",
        "prior window ahead of its own cursor",
        "eviction count unrelated to its windows",
        "empty ring claiming evictions",
        "retained range skipping unobserved events",
        "half-empty retained window",
    ),
)
def test_validate_browser_event_ring_outcome_rejects_impossible_ring_arithmetic(outcome):
    """Presence, types and reason-versus-eviction left every counter free of its own window."""
    with pytest.raises(AssertionError, match="event ring outcome is malformed"):
        soak.validate_browser_event_ring_outcome(outcome, phase="atomic")


@pytest.mark.parametrize("field", ("evicted", "appended", "priorCursor", "priorFirstId", "retainedFirstId", "retainedLastId"))
@pytest.mark.parametrize("step", (1, -1))
def test_validate_browser_event_ring_outcome_binds_every_counter_it_records(field, step):
    """Negative control: each recorded counter must be able to fail on its own, not as a pair."""
    produced = soak.browser_event_ring_extension(list(range(132, 332)), phase="retirement", prior_cursor=330, prior_ids=list(range(131, 331)))
    assert soak.validate_browser_event_ring_outcome(produced, phase="retirement") == produced

    with pytest.raises(AssertionError, match="event ring outcome is malformed"):
        soak.validate_browser_event_ring_outcome({**produced, field: produced[field] + step}, phase="retirement")


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        ("dropped_event", "retained browser event ids"),
        ("renumbered_event", "retained browser event ids"),
        ("event_count", "atomic snapshot event count"),
        ("final_cursor", "final browser cursor"),
    ),
)
def test_success_artifact_binds_the_retirement_delta_to_its_retained_event_ids(monkeypatch, mutation, message):
    """Two counters that agree with each other still agree when both are wrong.

    The retained event ids are in the artifact: `finalBoundary.evidence.browserEvents` is the
    retirement journal's own window, so the delta is graded against those ids and the ring window
    they came from rather than against a second copy of the same count.
    """
    artifact = complete_success_artifact(monkeypatch, driver=RingEvictionFinalizerDriver(limit=200, appended=3), previous_js=3)
    boundary = artifact["finalBoundary"]
    assert len(boundary["evidence"]["browserEvents"]) == 200, "the fixture must retain a saturated event window"
    if mutation == "dropped_event":
        boundary["evidence"]["browserEvents"] = boundary["evidence"]["browserEvents"][:-1]
    if mutation == "renumbered_event":
        boundary["evidence"]["browserEvents"][0] = {**boundary["evidence"]["browserEvents"][0], "id": 999999}
    if mutation == "event_count":
        boundary["atomicSnapshot"]["eventCount"] += 1
    if mutation == "final_cursor":
        boundary["evidence"]["cursors"]["js"] += 1

    with pytest.raises(soak.ArtifactIntegrityError, match="browser event ring outcomes") as raised:
        soak.validate_success_artifact(artifact)
    assert message in str(raised.value.__cause__)


QUIET_RETAINED_WINDOW = {"retainedFirstId": 0, "retainedLastId": 0, "priorCursor": 0}
SATURATED_RETAINED_WINDOW = {"retainedFirstId": 132, "retainedLastId": 331, "priorCursor": 330}
SATURATED_RETAINED_EVENTS = [{"id": event_id} for event_id in range(132, 332)]


def test_retirement_delta_evidence_accepts_the_counts_its_retained_events_support():
    soak.validate_browser_retirement_delta_evidence(
        {**CLEAN_RETIREMENT_DELTA, "reason": "benign_activity", "appendedEvents": 1, "evictedEvents": 1},
        retirement=SATURATED_RETAINED_WINDOW,
        retained_events=SATURATED_RETAINED_EVENTS,
        event_count=200,
        retained_cursor=331,
        fence_receipts=0,
        retained_receipts=0,
    )


@pytest.mark.parametrize(
    ("delta_overrides", "message"),
    (
        ({"appendedEvents": 2}, "appended count does not match"),
        ({"appendedEvents": 1, "mutatedEvents": 200}, "mutated more events than the retained window carried over"),
        ({"appendedEvents": 1, "recordedFailures": 2}, "recorded more failures than it appended events"),
    ),
)
def test_retirement_delta_evidence_bounds_every_count_by_its_retained_events(delta_overrides, message):
    """Negative control: each count the delta records must be refutable by the underlying ids."""
    with pytest.raises(AssertionError, match=message):
        soak.validate_browser_retirement_delta_evidence(
            {**CLEAN_RETIREMENT_DELTA, "reason": "benign_activity", "evictedEvents": 1, **delta_overrides},
            retirement=SATURATED_RETAINED_WINDOW,
            retained_events=SATURATED_RETAINED_EVENTS,
            event_count=200,
            retained_cursor=331,
            fence_receipts=0,
            retained_receipts=0,
        )


def test_retirement_delta_evidence_bounds_added_receipts_by_its_fenced_projection():
    """`addedReceipts` is graded against the projections, not against its own blocking counter."""
    delta = {**CLEAN_RETIREMENT_DELTA, "reason": "added_receipts", "addedReceipts": 1, "addedBlockingReceipts": 1, "integrityFailures": ["browser retirement added durable receipts: 1 (1 blocking)"]}

    soak.validate_browser_retirement_delta_evidence(
        delta, retirement=QUIET_RETAINED_WINDOW, retained_events=[], event_count=0, retained_cursor=0, fence_receipts=1, retained_receipts=2,
    )

    with pytest.raises(AssertionError, match="added more receipts than its fenced projection grew by"):
        soak.validate_browser_retirement_delta_evidence(
            {**delta, "addedReceipts": 3, "addedBlockingReceipts": 3},
            retirement=QUIET_RETAINED_WINDOW, retained_events=[], event_count=0, retained_cursor=0, fence_receipts=1, retained_receipts=2,
        )


@pytest.mark.parametrize("phase", ("server_after", "chrome_before", "chrome_after", "browser_atomic"))
def test_atomic_finalizer_retains_failures_from_every_retirement_phase(monkeypatch, phase):
    server_after = finalizer_server_payload()
    chrome = [[], []]
    events = []
    failures = []
    projection = receipt_projection(epoch="all")
    if phase == "server_after":
        server_after = finalizer_server_payload([{"id": 1, "level": "error", "message": "late server"}], sequence=1)
    if phase == "chrome_before":
        chrome[0] = [{"level": "WARNING", "message": "before retirement"}]
    if phase == "chrome_after":
        chrome[1] = [{"level": "SEVERE", "message": "after retirement"}]
    if phase == "browser_atomic":
        event = {"id": 1, "type": "error", "message": "atomic browser", "wallTime": "2026-08-05 12:00:00 PDT"}
        events = [event]
        failures = [event]
        projection = receipt_projection([receipt_row(1)], epoch="all")
    payloads = iter((finalizer_server_payload(), server_after))
    driver = FinalizerDriver(projection=projection, events=events, failures=failures, chrome=chrome)
    monkeypatch.setattr(soak, "WebDriverWait", lambda current, _timeout: type("Wait", (), {"until": lambda self, predicate: predicate(current)})())

    boundary = soak.finalize_live_browser_soak(
        driver,
        server_reader=lambda: next(payloads),
        expected_url="https://localhost:7443/",
        previous=finalizer_previous(),
        previous_stats=clean_stats_stream(1),
        baseline_projection=receipt_projection(epoch="all"),
        negative_handle=None,
    )

    assert soak.evidence_failed(boundary["evidence"])


@pytest.mark.parametrize("mutation", ("epoch", "capacity", "sequence", "drop", "drop_level", "overlap", "content", "gap"))
def test_atomic_finalizer_server_ring_transition_rejects_every_cursor_defect(mutation):
    previous = {"server_epoch": "a", "server_sequence": 2, "server_log_ids": [1, 2], "server_log_records": [{"id": 1}, {"id": 2}], "server_capacity": 10, "drop_count": 0, "server_dropped_by_level": {}}
    current = {"epoch": "a", "sequence": 3, "capacity": 10, "ids": [1, 2, 3], "dropped": {"count": 0, "by_level": {}}, "logs": [{"id": 1}, {"id": 2}, {"id": 3}]}
    if mutation == "epoch": current["epoch"] = "b"
    if mutation == "capacity": current["capacity"] = 11
    if mutation == "sequence": current["sequence"] = 1
    if mutation == "drop": current["dropped"]["count"] = 1
    if mutation == "drop_level": current["dropped"]["by_level"] = {"warning": 0}
    if mutation == "overlap": current.update(ids=[3], logs=[{"id": 3}])
    if mutation == "content": current["logs"][1] = {"id": 2, "level": "error"}
    if mutation == "gap": current.update(sequence=4, ids=[1, 2, 4], logs=[{"id": 1}, {"id": 2}, {"id": 4}])

    with pytest.raises(AssertionError):
        soak.validate_server_ring_transition(previous, current)


def test_atomic_finalizer_rejects_nonquiescent_uploader_fence():
    blocker = receipt_row(status="retrying")
    projection = receipt_projection([blocker], epoch="all")
    driver = FinalizerDriver(projection=projection)

    boundary = soak.finalize_live_browser_soak(
        driver,
        server_reader=lambda: finalizer_server_payload(),
        expected_url="https://localhost:7443/",
        previous=finalizer_previous(),
        previous_stats=clean_stats_stream(1),
        baseline_projection=receipt_projection(epoch="all"),
        negative_handle=None,
    )

    assert boundary["status"] == "failed"
    assert any(failure["phase"] == "uploaderFence" for failure in boundary["phaseFailures"])
    assert boundary["blankReadiness"] == {"href": "about:blank", "readyState": "complete"}
    assert boundary["chromeAfterRetirement"] == []
    assert boundary["serverAfter"] is not None


@pytest.mark.parametrize(
    ("phase", "expected_failure"),
    (
        ("inputs", "inputs"),
        ("serverBefore", "serverBefore"),
        ("chromeBeforeRetirement", "chromeBeforeRetirement"),
        ("uploaderFence", "uploaderFence"),
        ("atomicSnapshot", "atomicSnapshot"),
        ("stats", "atomicSnapshot"),
        ("blankReadiness", "blankReadiness"),
        ("chromeAfterRetirement", "chromeAfterRetirement"),
        ("serverAfter", "serverAfter"),
    ),
)
def test_atomic_finalizer_accumulates_phase_failure_and_still_retires(phase, expected_failure, monkeypatch):
    class PhaseDriver(FinalizerDriver):
        def __init__(self):
            super().__init__()
            self.chrome_reads = 0
            if phase == "stats":
                self.stats = None

        def get_log(self, name):
            self.chrome_reads += 1
            self.calls.append(f"chrome-{self.chrome_reads}")
            if phase == "chromeBeforeRetirement" and self.chrome_reads == 1:
                raise RuntimeError("chrome before failed")
            if phase == "chromeAfterRetirement" and self.chrome_reads == 2:
                raise RuntimeError("chrome after failed")
            return []

        def execute_async_script(self, script, *args):
            if phase == "uploaderFence":
                self.calls.append("fence")
                raise RuntimeError("fence failed")
            return super().execute_async_script(script, *args)

        def execute_script(self, script, *args):
            if "location.replace('about:blank')" in script and phase == "atomicSnapshot":
                self.calls.append("atomic")
                self.retired = True
                raise RuntimeError("atomic failed")
            if "document.readyState" in script and phase == "blankReadiness":
                self.calls.append("blank")
                raise RuntimeError("blank readiness failed")
            return super().execute_script(script, *args)

    reads = []

    def server_reader():
        reads.append(len(reads) + 1)
        if phase == "serverBefore" and len(reads) == 1:
            raise RuntimeError("server before failed")
        if phase == "serverAfter" and len(reads) == 2:
            raise RuntimeError("server after failed")
        return finalizer_server_payload()

    monkeypatch.setattr(soak, "WebDriverWait", lambda current, _timeout: type("Wait", (), {"until": lambda self, predicate: predicate(current)})())
    previous = finalizer_previous()
    if phase == "inputs":
        previous = {**previous, "server_log_records": None}
    driver = PhaseDriver()

    boundary = soak.finalize_live_browser_soak(
        driver,
        server_reader=server_reader,
        expected_url="https://localhost:7443/",
        previous=previous,
        previous_stats=clean_stats_stream(1),
        baseline_projection=receipt_projection(epoch="all"),
        negative_handle=None,
    )

    assert boundary["status"] == "failed"
    assert expected_failure in [failure["phase"] for failure in boundary["phaseFailures"]]
    assert reads == [1, 2], "both authenticated server-ring reads are always attempted"
    assert driver.chrome_reads == 2, "both Chrome drains are always attempted"
    assert driver.retired is True
    if phase != "blankReadiness":
        assert boundary["blankReadiness"] == {"href": "about:blank", "readyState": "complete"}


def test_atomic_finalizer_retains_blocker_created_after_fence(monkeypatch):
    blocker_projection = receipt_projection([receipt_row(status="retrying")], epoch="all")

    class PostFenceDriver(FinalizerDriver):
        def execute_async_script(self, script, *args):
            result = super().execute_async_script(script, *args)
            self.projection = blocker_projection
            return result

    driver = PostFenceDriver()
    monkeypatch.setattr(soak, "WebDriverWait", lambda current, _timeout: type("Wait", (), {"until": lambda self, predicate: predicate(current)})())
    boundary = soak.finalize_live_browser_soak(
        driver,
        server_reader=lambda: finalizer_server_payload(),
        expected_url="https://localhost:7443/",
        previous=finalizer_previous(),
        previous_stats=clean_stats_stream(1),
        baseline_projection=receipt_projection(epoch="all"),
        negative_handle=None,
    )

    assert boundary["status"] == "failed"
    assert boundary["phaseFailures"] == []
    assert boundary["evidence"]["browserReceiptProjection"] == blocker_projection
    assert boundary["evidence"]["browserReceiptBarrier"] == blocker_projection["barrier"]
    assert boundary["evidence"]["browserReceiptBarrier"]["quiescent"] is False
    assert boundary["blankReadiness"] == {"href": "about:blank", "readyState": "complete"}
    assert boundary["chromeAfterRetirement"] == []
    assert boundary["serverAfter"] is not None


def test_atomic_finalizer_accepts_append_only_evidence_after_fence(monkeypatch):
    first_event = {"id": 1, "type": "info", "message": "fenced"}
    second_event = {"id": 2, "type": "info", "message": "after fence"}
    first_receipt = receipt_row(1)
    second_receipt = receipt_row(2, request_id="r-2")
    fenced_projection = receipt_projection([first_receipt], epoch="all")
    appended_projection = receipt_projection([first_receipt, second_receipt], epoch="all")

    class AppendOnlyDriver(FinalizerDriver):
        def execute_async_script(self, script, *args):
            result = super().execute_async_script(script, *args)
            self.events = [first_event, second_event]
            self.projection = appended_projection
            return result

    driver = AppendOnlyDriver(projection=fenced_projection, events=[first_event])
    monkeypatch.setattr(soak, "WebDriverWait", lambda current, _timeout: type("Wait", (), {"until": lambda self, predicate: predicate(current)})())
    boundary = soak.finalize_live_browser_soak(
        driver,
        server_reader=lambda: finalizer_server_payload(),
        expected_url="https://localhost:7443/",
        previous=finalizer_previous(),
        previous_stats=clean_stats_stream(1),
        baseline_projection=receipt_projection(epoch="all"),
        negative_handle=None,
    )

    assert boundary["status"] == "clean"
    assert boundary["phaseFailures"] == []
    assert [event["id"] for event in boundary["evidence"]["browserEvents"]] == [1, 2]
    assert boundary["evidence"]["browserReceiptProjection"] == appended_projection


def test_atomic_finalizer_rejects_accepted_browser_failure_appended_after_fence(monkeypatch):
    failure = {
        "id": 1,
        "level": "error",
        "message": "accepted after fence",
        "requestId": "r-accepted-after-fence",
        "source": "browser-lifecycle",
        "route": "/browser/pagehide",
        "event": "pagehide",
        "wallTime": "2026-08-06 14:30:00 PDT",
        "deliveryOutcome": "failed",
        "status": None,
    }
    accepted = receipt_row(
        1,
        request_id=failure["requestId"],
        source="/browser-lifecycle",
        route=failure["route"],
        event=failure["event"],
        wall_time=failure["wallTime"],
    )
    appended_projection = receipt_projection([accepted], epoch="all")

    class AcceptedFailureDriver(FinalizerDriver):
        def execute_async_script(self, script, *args):
            result = super().execute_async_script(script, *args)
            self.events = [{"id": 1, "type": "client_failure", "message": failure["message"]}]
            self.failures = [failure]
            self.projection = appended_projection
            return result

    driver = AcceptedFailureDriver()
    monkeypatch.setattr(soak, "WebDriverWait", lambda current, _timeout: type("Wait", (), {"until": lambda self, predicate: predicate(current)})())
    boundary = soak.finalize_live_browser_soak(
        driver,
        server_reader=lambda: finalizer_server_payload(),
        expected_url="https://localhost:7443/",
        previous=finalizer_previous(),
        previous_stats=clean_stats_stream(1),
        baseline_projection=receipt_projection(epoch="all"),
        negative_handle=None,
    )

    assert boundary["status"] == "failed"
    assert boundary["phaseFailures"] == []
    assert boundary["evidence"]["browserLocalFailures"] == [failure]
    assert boundary["evidence"]["browserReceiptProjection"] == appended_projection
    assert boundary["evidence"]["browserReceiptBarrier"]["quiescent"] is True
    assert boundary["evidence"]["browserReceiptBarrier"]["blocking"] == []


def test_atomic_finalizer_rejects_fenced_receipt_mutated_before_atomic(monkeypatch):
    fenced_receipt = receipt_row(1)
    mutated_receipt = {**fenced_receipt, "status": "retrying"}

    class MutatedReceiptDriver(FinalizerDriver):
        def execute_async_script(self, script, *args):
            result = super().execute_async_script(script, *args)
            self.projection = receipt_projection([mutated_receipt], epoch="all")
            return result

    driver = MutatedReceiptDriver(projection=receipt_projection([fenced_receipt], epoch="all"))
    monkeypatch.setattr(soak, "WebDriverWait", lambda current, _timeout: type("Wait", (), {"until": lambda self, predicate: predicate(current)})())
    boundary = soak.finalize_live_browser_soak(
        driver,
        server_reader=lambda: finalizer_server_payload(),
        expected_url="https://localhost:7443/",
        previous=finalizer_previous(),
        previous_stats=clean_stats_stream(1),
        baseline_projection=receipt_projection(epoch="all"),
        negative_handle=None,
    )

    assert boundary["status"] == "failed"
    assert any(failure["phase"] == "atomicSnapshot" for failure in boundary["phaseFailures"])


def test_atomic_finalizer_rejects_fenced_cursor_evicted_before_atomic(monkeypatch):
    fenced_event = {"id": 1, "type": "info", "message": "fenced"}
    replacement_event = {"id": 2, "type": "info", "message": "replacement"}

    class EvictedCursorDriver(FinalizerDriver):
        def execute_async_script(self, script, *args):
            result = super().execute_async_script(script, *args)
            self.events = [replacement_event]
            return result

    driver = EvictedCursorDriver(events=[fenced_event])
    monkeypatch.setattr(soak, "WebDriverWait", lambda current, _timeout: type("Wait", (), {"until": lambda self, predicate: predicate(current)})())
    boundary = soak.finalize_live_browser_soak(
        driver,
        server_reader=lambda: finalizer_server_payload(),
        expected_url="https://localhost:7443/",
        previous=finalizer_previous(),
        previous_stats=clean_stats_stream(1),
        baseline_projection=receipt_projection(epoch="all"),
        negative_handle=None,
    )

    assert boundary["status"] == "failed"
    assert any(failure["phase"] == "atomicSnapshot" for failure in boundary["phaseFailures"])


def test_atomic_finalizer_allows_logs_visibility_only_for_expected_negative_probe(monkeypatch):
    event = {"id": 1, "type": "api", "endpoint": soak.NEGATIVE_ROUTE, "requestId": "r-web-controlled-1", "status": 500, "ok": False, "wallTime": "2026-08-06 13:00:00 PDT"}
    receipt = receipt_row(1, request_id=event["requestId"], source=soak.NEGATIVE_ROUTE, route=soak.NEGATIVE_ROUTE, event="api", wall_time=event["wallTime"], http_status=500)
    projection = receipt_projection([receipt], epoch="all")
    proof = {
        **receipt,
        "source": soak.NEGATIVE_SOURCE,
        "receiptSource": soak.NEGATIVE_ROUTE,
        "rendered": {"matchingRows": 1, "requestId": event["requestId"], "source": soak.NEGATIVE_SOURCE, "route": soak.NEGATIVE_ROUTE, "event": "api", "text": "controlled failure"},
        "redaction": {channel: True for channel in ("dom", "clipboard", "retained", "upload", "storage")},
    }

    class VisibleLogsDriver(FinalizerDriver):
        def execute_script(self, script, *args):
            result = super().execute_script(script, *args)
            if "location.replace('about:blank')" in script and isinstance(result, dict):
                result["hiddenSentinel"]["everVisible"] = True
                result["panelVisible"] = True
                result["stats"]["panelVisible"] = True
                result["stats"]["everVisible"] = True
            return result

    driver = VisibleLogsDriver(projection=projection, events=[event], failures=[event])
    monkeypatch.setattr(soak, "WebDriverWait", lambda current, _timeout: type("Wait", (), {"until": lambda self, predicate: predicate(current)})())
    boundary = soak.finalize_live_browser_soak(
        driver,
        server_reader=lambda: finalizer_server_payload(),
        expected_url="https://localhost:7443/",
        previous=finalizer_previous(),
        previous_stats=clean_stats_stream(),
        baseline_projection=receipt_projection(epoch="all"),
        negative_handle=proof,
    )

    assert boundary["phaseFailures"] == []
    assert boundary["atomicSnapshot"]["panelVisible"] is True
    assert boundary["evidence"]["integrityFailures"] == []
    assert boundary["status"] == "failed", "the exactly acknowledged producer error still makes the negative command exit 1"


def test_atomic_finalizer_waits_for_complete_blank_before_final_chrome_drain(monkeypatch):
    driver = FinalizerDriver(chrome=[[], [{"level": "SEVERE", "message": "late blank error"}]])
    readiness_calls = []
    original_execute_script = driver.execute_script

    class Wait:
        def until(self, predicate):
            readiness_calls.append("loading")
            driver.retired = True
            driver.execute_script = lambda script, *args: (
                ({"href": "about:blank", "readyState": "loading"}
                 if len(readiness_calls) == 1
                 else {"href": "about:blank", "readyState": "complete"})
                if "document.readyState" in script
                else original_execute_script(script, *args)
            )
            assert predicate(driver) is False
            readiness_calls.append("complete")
            return predicate(driver)

    monkeypatch.setattr(soak, "WebDriverWait", lambda _current, _timeout: Wait())
    boundary = soak.finalize_live_browser_soak(
        driver,
        server_reader=lambda: finalizer_server_payload(),
        expected_url="https://localhost:7443/",
        previous=finalizer_previous(),
        previous_stats=clean_stats_stream(1),
        baseline_projection=receipt_projection(epoch="all"),
        negative_handle=None,
    )

    assert readiness_calls == ["loading", "complete"]
    assert boundary["blankReadiness"] == {"href": "about:blank", "readyState": "complete"}
    assert boundary["chromeAfterRetirement"] == [{"level": "SEVERE", "message": "late blank error"}]
    assert boundary["status"] == "failed"


def cadence_samples(sample, *, key, span_seconds, step=soak.SAMPLE_SECONDS):
    """Stamp one clean evidence dict across a window at the cadence `run_soak` actually samples at.

    A two-entry `samples` list beside `elapsed_seconds: 600` is not a shape `run_soak` can produce
    for a ten-minute soak; building the fixture that way is what let `validate_success_artifact`
    ignore the entire observation interval without one test going red. A real 600-second artifact
    carries 118 samples about five seconds apart, and a real 90-second settle carries 18.
    """

    marks = [round(index * step, 6) for index in range(int(span_seconds // step) + 1)]
    if marks[-1] < span_seconds:
        marks.append(float(span_seconds))
    return [{**sample, key: mark} for mark in marks]


def complete_success_artifact(monkeypatch, *, driver=None, previous_js=0):
    monkeypatch.setattr(soak, "WebDriverWait", lambda current, _timeout: type("Wait", (), {"until": lambda self, predicate: predicate(current)})())
    projection = receipt_projection(epoch="all")
    baseline_stats = clean_stats_stream(1)
    baseline_cursor = {**finalizer_previous(), "js": previous_js}
    settled = {
        "browserEvents": [],
        "browserLocalFailures": [],
        "browserReceiptBarrier": projection["barrier"],
        "browserReceiptProjection": projection,
        "statsStreamEvidence": baseline_stats,
        "serverLogErrors": [],
        "browserLogFailures": [],
        "integrityFailures": [],
        "serverLogDropped": {"count": 0, "by_level": {}},
        "cursors": {
            "js": previous_js,
            "server_epoch": baseline_cursor["server_epoch"],
            "server_sequence": baseline_cursor["server_sequence"],
            "server_log_ids": baseline_cursor["server_log_ids"],
            "server_log_records": baseline_cursor["server_log_records"],
            "server_capacity": baseline_cursor["server_capacity"],
            "server_dropped_by_level": baseline_cursor["server_dropped_by_level"],
        },
    }
    boundary = soak.finalize_live_browser_soak(
        FinalizerDriver() if driver is None else driver,
        server_reader=lambda: finalizer_server_payload(),
        expected_url="https://localhost:7443/",
        previous=baseline_cursor,
        previous_stats=baseline_stats,
        baseline_projection=projection,
        negative_handle=None,
        expected_journey_id="journey-1",
    )
    identity = {
        "pid": 1,
        "cwd": "/repo",
        "process_started": "now",
        "head": "a" * 40,
        "bundle_url": "https://localhost:7443/static/yolomux.js",
        "bundle_sha256": "b" * 64,
    }
    # `run_soak` records the settle window, keeps its last sample as the observation baseline, and
    # then appends one observation sample every SAMPLE_SECONDS; the final boundary evidence is not
    # one of them. The fixture mirrors that so the retained interval can actually be validated.
    settle_samples = cadence_samples(settled, key="settle_elapsed_seconds", span_seconds=soak.SETTLE_SECONDS)
    baseline = settle_samples[-1]
    return {
        "url": "https://localhost:7443/",
        "started_pt": "start",
        "ended_pt": "end",
        "requested_duration_seconds": 600,
        "settle_elapsed_seconds": settle_samples[-1]["settle_elapsed_seconds"],
        "settle": {"requested_seconds": 90.0, "started_pt": "settle-start", "ended_pt": "settle-end", "elapsed_seconds": settle_samples[-1]["settle_elapsed_seconds"], "status": "clean", "samples": settle_samples},
        "elapsed_seconds": 600,
        "identity": identity,
        "completion_identity": identity,
        "pageIdentity": {"url": "https://localhost:7443/", "journeyId": "journey-1"},
        "pageIdentityDrift": soak.new_page_identity_drift_record(),
        "baseline": baseline,
        "samples": cadence_samples(settled, key="elapsed_seconds", span_seconds=600),
        "finalBoundary": boundary,
    }


def test_success_artifact_requires_complete_atomic_retirement_proof(monkeypatch):
    artifact = complete_success_artifact(monkeypatch)

    soak.validate_success_artifact(artifact)


def test_success_artifact_accepts_saturated_ring_eviction_and_states_the_delta(monkeypatch):
    """An r5-shaped boundary — full ring, benign appends, evictions — is a valid success.

    The artifact must positively state what the browser did while retiring rather than
    merely omitting a failure, so the delta counts are asserted here as evidence.
    """
    driver = RingEvictionFinalizerDriver(limit=200, appended=3)
    artifact = complete_success_artifact(monkeypatch, driver=driver, previous_js=3)

    soak.validate_success_artifact(artifact)

    event_ring = artifact["finalBoundary"]["eventRing"]
    assert event_ring["retirement"]["reason"] == soak.BROWSER_EVENT_RING_EVICTED
    assert event_ring["delta"] == {
        "reason": soak.RETIREMENT_DELTA_BENIGN,
        "appendedEvents": 3,
        "evictedEvents": 3,
        "mutatedEvents": 0,
        "recordedFailures": 0,
        "evictedFailures": 0,
        "addedReceipts": 0,
        "addedBlockingReceipts": 0,
        "integrityFailures": [],
    }
    assert artifact["finalBoundary"]["evidence"]["integrityFailures"] == []


@pytest.mark.parametrize("mutation", ("missing_delta", "signal_reason", "delta_disagrees_with_ring", "delta_appended_disagrees_with_ring"))
def test_success_artifact_rejects_missing_or_signal_carrying_retirement_delta(monkeypatch, mutation):
    artifact = complete_success_artifact(monkeypatch, driver=RingEvictionFinalizerDriver(limit=200, appended=3), previous_js=3)
    event_ring = artifact["finalBoundary"]["eventRing"]
    if mutation == "missing_delta":
        del event_ring["delta"]
        expected = "malformed browser event ring outcomes"
    if mutation == "signal_reason":
        event_ring["delta"] = {**event_ring["delta"], "reason": soak.RETIREMENT_DELTA_RECORDED_FAILURES, "recordedFailures": 1, "integrityFailures": ["browser recorded failing diagnostics during retirement: 1"]}
        expected = "signal-carrying browser diagnostics"
    if mutation == "delta_disagrees_with_ring":
        event_ring["delta"] = {**event_ring["delta"], "evictedEvents": 2}
        expected = "delta disagrees with its event ring"
    if mutation == "delta_appended_disagrees_with_ring":
        event_ring["delta"] = {**event_ring["delta"], "appendedEvents": 2}
        expected = "delta disagrees with its event ring"

    with pytest.raises(soak.ArtifactIntegrityError, match=expected):
        soak.validate_success_artifact(artifact)


def test_success_artifact_requires_full_measured_settle_before_observation(monkeypatch):
    artifact = complete_success_artifact(monkeypatch)
    artifact["settle_elapsed_seconds"] = 89.99
    artifact["settle"]["elapsed_seconds"] = 89.99

    with pytest.raises(soak.ArtifactIntegrityError, match="measured authenticated settle"):
        soak.validate_success_artifact(artifact)


def test_success_artifact_validates_the_whole_observation_interval_it_retained(monkeypatch):
    """The observation interval is the property a clean soak exists to prove, so grade all of it.

    Measured on a real stage-6 artifact: 118 samples, a browser Error injected into sample 59, and
    `validate_success_artifact` accepted it. The two-endpoint loop it used to run could not see any
    sample between the baseline and the final boundary evidence.
    """
    artifact = complete_success_artifact(monkeypatch)

    soak.validate_success_artifact(artifact)

    assert len(artifact["samples"]) >= math.ceil(600 / soak.MAX_SAMPLE_GAP_SECONDS)
    assert len(artifact["settle"]["samples"]) >= math.ceil(soak.SETTLE_SECONDS / soak.MAX_SAMPLE_GAP_SECONDS)


@pytest.mark.parametrize("failure_field", ("browserLocalFailures", "serverLogErrors", "browserLogFailures", "integrityFailures"))
@pytest.mark.parametrize("series", ("samples", "settle"))
def test_success_artifact_rejects_a_failure_in_any_intermediate_sample(monkeypatch, series, failure_field):
    artifact = complete_success_artifact(monkeypatch)
    samples = artifact["samples"] if series == "samples" else artifact["settle"]["samples"]
    index = len(samples) // 2
    assert 0 < index < len(samples) - 1, "the injected sample must be an intermediate one"
    samples[index] = {**samples[index], failure_field: [{"id": 999999, "level": "error", "message": "injected mid-interval"}]}

    with pytest.raises(soak.ArtifactIntegrityError, match=f"contains {failure_field} \\({'observation' if series == 'samples' else 'settle'} sample {index}\\)"):
        soak.validate_success_artifact(artifact)


@pytest.mark.parametrize("series", ("samples", "settle"))
def test_success_artifact_rejects_a_nonquiescent_barrier_in_any_intermediate_sample(monkeypatch, series):
    artifact = complete_success_artifact(monkeypatch)
    samples = artifact["samples"] if series == "samples" else artifact["settle"]["samples"]
    index = len(samples) // 2
    blocker = receipt_projection([receipt_row(status="retrying")])
    samples[index] = {**samples[index], "browserReceiptBarrier": blocker["barrier"], "browserReceiptProjection": blocker}

    with pytest.raises(soak.ArtifactIntegrityError, match="non-quiescent receipt barrier evidence"):
        soak.validate_success_artifact(artifact)


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        ("truncated", "fewer than the 30"),
        ("late_start", "sampling starts"),
        ("repeated_mark", "does not advance"),
        ("backwards", "does not advance"),
        ("gap", "skipped"),
        ("stops_short", "sampling stops"),
        ("unmarked", "carries no measured"),
    ),
)
def test_success_artifact_rejects_an_observation_interval_without_continuous_cadence(monkeypatch, mutation, message):
    """`elapsed_seconds: 600` beside two samples proved nothing, and the validator accepted it."""
    artifact = complete_success_artifact(monkeypatch)
    samples = artifact["samples"]
    if mutation == "truncated":
        artifact["samples"] = [samples[0], samples[-1]]
    if mutation == "late_start":
        artifact["samples"] = samples[6:]
    if mutation == "repeated_mark":
        samples[40] = {**samples[40], "elapsed_seconds": samples[39]["elapsed_seconds"]}
    if mutation == "backwards":
        samples[40] = {**samples[40], "elapsed_seconds": samples[39]["elapsed_seconds"] - 1.0}
    if mutation == "gap":
        artifact["samples"] = [*samples[:40], *samples[45:]]
    if mutation == "stops_short":
        artifact["samples"] = samples[:-1]
    if mutation == "unmarked":
        samples[40] = {key: value for key, value in samples[40].items() if key != "elapsed_seconds"}

    with pytest.raises(soak.ArtifactIntegrityError, match=message):
        soak.validate_success_artifact(artifact)


def test_success_artifact_rejects_a_settle_window_without_continuous_cadence(monkeypatch):
    artifact = complete_success_artifact(monkeypatch)
    settle = artifact["settle"]
    settle["samples"] = [settle["samples"][0], settle["samples"][-1]]

    with pytest.raises(soak.ArtifactIntegrityError, match="fewer than the 5"):
        soak.validate_success_artifact(artifact)


@pytest.mark.parametrize("mutation", ("substituted_baseline", "reordered_settle"))
def test_success_artifact_requires_the_baseline_to_be_the_measured_end_of_settle(monkeypatch, mutation):
    artifact = complete_success_artifact(monkeypatch)
    if mutation == "substituted_baseline":
        artifact["baseline"] = {**artifact["settle"]["samples"][0]}
    if mutation == "reordered_settle":
        artifact["settle"]["samples"] = [artifact["settle"]["samples"][-1], *artifact["settle"]["samples"][:-1]]

    with pytest.raises(soak.ArtifactIntegrityError):
        soak.validate_success_artifact(artifact)


def test_success_artifact_rejects_elapsed_time_its_own_samples_never_measured(monkeypatch):
    artifact = complete_success_artifact(monkeypatch)
    artifact["samples"][-1] = {**artifact["samples"][-1], "elapsed_seconds": artifact["elapsed_seconds"] + 1.0}

    with pytest.raises(soak.ArtifactIntegrityError, match="less elapsed time than its own samples measured"):
        soak.validate_success_artifact(artifact)


def test_persisted_validation_never_delegates_to_the_runtime_early_fail(monkeypatch):
    """Runtime early-fail and persisted validation stay independent proofs of the same soak.

    If `validate_success_artifact` reached for `evidence_failed`, a defect in one would certify the
    other; poisoning `evidence_failed` proves the persisted path derives its own verdict.
    """
    artifact = complete_success_artifact(monkeypatch)

    def refuse(_evidence):
        raise AssertionError("validate_success_artifact must not delegate to the runtime early-fail")

    monkeypatch.setattr(soak, "evidence_failed", refuse)
    soak.validate_success_artifact(artifact)

    artifact["samples"][40] = {**artifact["samples"][40], "serverLogErrors": [{"id": 1, "level": "error"}]}
    with pytest.raises(soak.ArtifactIntegrityError, match="contains serverLogErrors"):
        soak.validate_success_artifact(artifact)


def test_success_artifact_rejects_ready_only_stats_without_coherent_delta(monkeypatch):
    artifact = complete_success_artifact(monkeypatch)
    final_stream = artifact["finalBoundary"]["evidence"]["statsStreamEvidence"]["stream"]
    baseline_stream = artifact["baseline"]["statsStreamEvidence"]["stream"]
    for field in ("acceptedDeltaSequence", "cacheGeneration", "sourceGeneration", "deltaRevision"):
        final_stream[field] = baseline_stream[field]
    final_stream["lastDeliveryKind"] = "ready"
    assert final_stream["lastDeliveryKind"] == "ready"
    assert final_stream["acceptedDeltaSequence"] == baseline_stream["acceptedDeltaSequence"]

    with pytest.raises(soak.ArtifactIntegrityError, match="coherent accepted delta"):
        soak.validate_success_artifact(artifact)


@pytest.mark.parametrize(
    "mutation",
    ("missing_boundary", "failed_status", "missing_baseline_projection", "missing_final_projection", "nonquiescent_fence", "incomplete_blank"),
)
def test_success_artifact_rejects_missing_or_malformed_final_boundary(monkeypatch, mutation):
    artifact = complete_success_artifact(monkeypatch)
    if mutation == "missing_boundary":
        del artifact["finalBoundary"]
    if mutation == "failed_status":
        artifact["finalBoundary"]["status"] = "failed"
    if mutation == "missing_baseline_projection":
        del artifact["baseline"]["browserReceiptProjection"]
    if mutation == "missing_final_projection":
        del artifact["finalBoundary"]["evidence"]["browserReceiptProjection"]
    if mutation == "nonquiescent_fence":
        blocker = receipt_row(status="retrying")
        artifact["finalBoundary"]["uploaderFence"]["projection"] = receipt_projection([blocker], epoch="all")
    if mutation == "incomplete_blank":
        artifact["finalBoundary"]["blankReadiness"]["readyState"] = "loading"

    with pytest.raises(soak.ArtifactIntegrityError):
        soak.validate_success_artifact(artifact)


LIVE_SS_LISTEN_OUTPUT = (
    "State  Recv-Q Send-Q Local Address:Port Peer Address:PortProcess\n"
    "LISTEN 0      64           0.0.0.0:19771      0.0.0.0:*    users:((\"python3\",pid=3364478,fd=6))\n"
)


def record_listener_probe(monkeypatch, stdout, *, platform_name, ss_present=True):
    """Capture the exact listener-probe argv and let the caller supply its stdout."""

    calls = []

    def fake_run(args, **kwargs):
        calls.append((list(args), kwargs.get("timeout")))
        return subprocess.CompletedProcess(list(args), 0, stdout, "")

    monkeypatch.setattr(soak.platform, "system", lambda: platform_name)
    monkeypatch.setattr(soak.shutil, "which", lambda name: "/usr/bin/ss" if (name == "ss" and ss_present) else ("/usr/bin/lsof" if name == "lsof" else None))
    monkeypatch.setattr(soak.subprocess, "run", fake_run)
    return calls


def test_listener_pid_uses_ss_on_linux_and_never_walks_every_fd_with_lsof(monkeypatch):
    """lsof -iTCP walks all open FDs and takes ~9s on a busy host, so Linux must use ss like boot.sh does."""

    calls = record_listener_probe(monkeypatch, LIVE_SS_LISTEN_OUTPUT, platform_name="Linux")

    assert soak.listener_pid(19771) == 3364478
    assert calls == [(["ss", "-ltnp", "sport = :19771"], soak.LISTENER_PROBE_TIMEOUT_SECONDS)]
    assert all("lsof" not in argv[0] for argv, _timeout in calls)


def test_listener_pid_keeps_lsof_on_macos(monkeypatch):
    calls = record_listener_probe(monkeypatch, "3364478\n", platform_name="Darwin")

    assert soak.listener_pid(19771) == 3364478
    assert calls == [(["lsof", "-nP", "-iTCP:19771", "-sTCP:LISTEN", "-t"], soak.LISTENER_PROBE_TIMEOUT_SECONDS)]


def test_listener_pid_falls_back_to_lsof_when_linux_lacks_ss(monkeypatch):
    calls = record_listener_probe(monkeypatch, "3364478\n", platform_name="Linux", ss_present=False)

    assert soak.listener_pid(19771) == 3364478
    assert calls == [(["lsof", "-nP", "-iTCP:19771", "-sTCP:LISTEN", "-t"], soak.LISTENER_PROBE_TIMEOUT_SECONDS)]


def test_listener_pid_dedupes_one_process_holding_several_listening_fds(monkeypatch):
    dual_stack = (
        "LISTEN 0 64 0.0.0.0:19771 0.0.0.0:* users:((\"python3\",pid=3364478,fd=6))\n"
        "LISTEN 0 64    [::]:19771    [::]:* users:((\"python3\",pid=3364478,fd=7),(\"python3\",pid=3364478,fd=8))\n"
    )
    record_listener_probe(monkeypatch, dual_stack, platform_name="Linux")

    assert soak.listener_pid(19771) == 3364478


@pytest.mark.parametrize(
    ("stdout", "expected"),
    (
        ("State Recv-Q Send-Q Local Address:Port\n", "none"),
        (
            "LISTEN 0 64 0.0.0.0:19771 0.0.0.0:* users:((\"python3\",pid=3364478,fd=6))\n"
            "LISTEN 0 64    [::]:19771    [::]:* users:((\"python3\",pid=4242424,fd=6))\n",
            "['3364478', '4242424']",
        ),
        ("LISTEN 0 64 0.0.0.0:19771 0.0.0.0:*\n", "none"),
    ),
)
def test_listener_pid_stays_strict_about_exactly_one_identified_listener(monkeypatch, stdout, expected):
    record_listener_probe(monkeypatch, stdout, platform_name="Linux")

    with pytest.raises(RuntimeError, match=f"expected exactly one listener on port 19771, found {re.escape(expected)}"):
        soak.listener_pid(19771)


# The two hrefs below were measured on the live 7771 server on 2026-08-07 with the soak's own
# headless Chrome options: the app pruned the tab of a tmux session that had been killed
# (1693 -> 1663 characters) and, two seconds after a layout refresh, appended its terminal scroll
# snapshot to the `state` value (1663 -> 1984 characters). Both rewrites go through
# `history.replaceState` in `updateActiveSessionParam`, so both are production behaviour.
LIVE_MEASURED_URL = (
    "https://localhost:7891/?sessions=1,bullpen-74ea25007664&layout=row@50(left,right)"
    "&tabs=left:1,135-interleave-lanes-DIS-2381,finder;right:yo7770,yo7771"
    "&state=%7B%22v%22%3A1%2C%22finder%22%3A%7B%22root%22%3A%22%2Fhome%2Fkeivenc%22%7D%7D"
)
LIVE_PRUNED_AND_SCROLLED_URL = (
    "https://localhost:7891/?sessions=1,bullpen-74ea25007664&layout=row@50(left,right)"
    "&tabs=left:1,finder;right:yo7770,yo7771"
    "&state=%7B%22v%22%3A1%2C%22finder%22%3A%7B%22root%22%3A%22%2Fhome%2Fkeivenc%22%7D%2C%22scroll"
    "%22%3A%5B%7B%22target%22%3A%22terminal%3A1%22%2C%22top%22%3A0%7D%5D%7D"
)


def live_page_identity(href, *, visibility="visible", journey_id="j-reload-1", origin="https://localhost:7891"):
    return {"origin": origin, "href": href, "visibility": visibility, "journeyId": journey_id}


def test_page_identity_accepts_the_app_owned_url_rewrites_measured_on_the_live_server():
    identity = soak.classify_page_identity(
        live_page_identity(LIVE_PRUNED_AND_SCROLLED_URL),
        expected_url=LIVE_MEASURED_URL,
        expected_journey_id="j-reload-1",
    )

    assert identity["reasons"] == []
    assert identity["journeyId"] == "j-reload-1"
    assert identity["drift"] == {
        "hrefChanged": True,
        "sessionsRemoved": [],
        "tabsRemoved": ["135-interleave-lanes-DIS-2381"],
        "layoutChanged": False,
        "stateChanged": True,
        "hrefLength": len(LIVE_PRUNED_AND_SCROLLED_URL),
    }


def test_page_identity_accepts_a_bare_root_url_growing_its_own_view_state():
    identity = soak.classify_page_identity(
        live_page_identity(LIVE_MEASURED_URL),
        expected_url="https://localhost:7891/",
        expected_journey_id="j-reload-1",
    )

    assert identity["reasons"] == []
    assert identity["drift"]["hrefChanged"] is True


@pytest.mark.parametrize(
    ("actual", "expected_url", "reasons"),
    (
        (live_page_identity(LIVE_MEASURED_URL.replace("localhost:7891", "localhost:7892"), origin="https://localhost:7892"), LIVE_MEASURED_URL, ["origin_changed"]),
        (live_page_identity("https://localhost:7891/outside-app"), LIVE_MEASURED_URL, ["path_changed", "sessions_emptied"]),
        (live_page_identity(LIVE_MEASURED_URL, visibility="hidden"), LIVE_MEASURED_URL, ["page_hidden"]),
        (live_page_identity(LIVE_MEASURED_URL, journey_id=""), LIVE_MEASURED_URL, ["document_journey_unavailable"]),
        (live_page_identity(LIVE_MEASURED_URL, journey_id="j-reload-2"), LIVE_MEASURED_URL, ["document_replaced"]),
        (live_page_identity(LIVE_MEASURED_URL + "&unexpected=1"), LIVE_MEASURED_URL, ["query_field_count_exceeded", "query_keys_added"]),
        (live_page_identity(LIVE_MEASURED_URL.replace("sessions=1,", "sessions=1,other-session,")), LIVE_MEASURED_URL, ["sessions_substituted"]),
        (live_page_identity(LIVE_MEASURED_URL.replace("sessions=1,bullpen-74ea25007664&", "sessions=&")), LIVE_MEASURED_URL, ["sessions_emptied"]),
        (live_page_identity(LIVE_MEASURED_URL.replace("&tabs=left:", "&tabs=elsewhere:")), LIVE_MEASURED_URL, ["tab_slots_substituted", "tabs_substituted"]),
        (live_page_identity(LIVE_MEASURED_URL.replace("&tabs=left:1,", "&tabs=left:1,foreign-session,")), LIVE_MEASURED_URL, ["tabs_substituted"]),
        ({"origin": "https://localhost:7891", "href": LIVE_MEASURED_URL, "visibility": "visible"}, LIVE_MEASURED_URL, ["page_identity_unreadable"]),
        (None, LIVE_MEASURED_URL, ["page_identity_unreadable"]),
    ),
)
def test_page_identity_still_reports_every_substitution_with_a_typed_reason(actual, expected_url, reasons):
    identity = soak.classify_page_identity(actual, expected_url=expected_url, expected_journey_id="j-reload-1")

    assert identity["reasons"] == reasons


def test_assert_page_identity_names_its_reasons_and_pins_the_live_document():
    class Driver:
        def __init__(self, journey_id):
            self.journey_id = journey_id

        def execute_script(self, _script):
            return live_page_identity(LIVE_PRUNED_AND_SCROLLED_URL, journey_id=self.journey_id)

    pinned = soak.assert_page_identity(Driver("j-reload-1"), LIVE_MEASURED_URL)
    assert pinned["journeyId"] == "j-reload-1"
    assert soak.assert_page_identity(Driver("j-reload-1"), LIVE_MEASURED_URL, "j-reload-1")["drift"]["hrefChanged"] is True

    with pytest.raises(AssertionError, match="authenticated app page identity changed: document_replaced"):
        soak.assert_page_identity(Driver("j-reload-2"), LIVE_MEASURED_URL, "j-reload-1")


def test_page_identity_drift_record_retains_distinct_rewrites_and_stays_bounded():
    record = soak.new_page_identity_drift_record()
    unchanged = soak.classify_page_identity(live_page_identity(LIVE_MEASURED_URL), expected_url=LIVE_MEASURED_URL)
    pruned = soak.classify_page_identity(live_page_identity(LIVE_PRUNED_AND_SCROLLED_URL), expected_url=LIVE_MEASURED_URL)
    scrolled = soak.classify_page_identity(live_page_identity(LIVE_PRUNED_AND_SCROLLED_URL + "%20"), expected_url=LIVE_MEASURED_URL)

    soak.note_page_identity_drift(record, unchanged, phase="settle", elapsed=1.0)
    assert record == {"observed": 0, "entries": []}

    for index in range(soak.MAX_RECORDED_PAGE_IDENTITY_DRIFT + 3):
        soak.note_page_identity_drift(record, pruned, phase="observation", elapsed=float(index))
        soak.note_page_identity_drift(record, pruned, phase="observation", elapsed=float(index))
        soak.note_page_identity_drift(record, scrolled, phase="observation", elapsed=float(index))

    assert record["observed"] == 2 * (soak.MAX_RECORDED_PAGE_IDENTITY_DRIFT + 3)
    assert len(record["entries"]) == soak.MAX_RECORDED_PAGE_IDENTITY_DRIFT
    assert record["entries"][0]["phase"] == "observation"
    assert record["entries"][0]["drift"]["tabsRemoved"] == ["135-interleave-lanes-DIS-2381"]


def finalizer_boundary_for_page(monkeypatch, page, *, journey_id="journey-1", expected_journey_id="journey-1"):
    monkeypatch.setattr(soak, "WebDriverWait", lambda current, _timeout: type("Wait", (), {"until": lambda self, predicate: predicate(current)})())
    projection = receipt_projection(epoch="all")
    driver = FinalizerDriver()
    original = driver.execute_script

    def execute_script(script, *args):
        snapshot = original(script, *args)
        if isinstance(snapshot, dict) and "page" in snapshot:
            snapshot["page"] = page
            snapshot["journey"] = {"id": journey_id, "reachable": True, "visitedSurfaces": []}
        return snapshot

    driver.execute_script = execute_script
    return soak.finalize_live_browser_soak(
        driver,
        server_reader=lambda: finalizer_server_payload(),
        expected_url="https://localhost:7443/?sessions=1&layout=left&tabs=left:1,finder&state=%7B%22v%22%3A1%7D",
        previous=finalizer_previous(),
        previous_stats=clean_stats_stream(1),
        baseline_projection=projection,
        negative_handle=None,
        expected_journey_id=expected_journey_id,
    )


def test_atomic_boundary_accepts_app_owned_drift_and_records_it(monkeypatch):
    drifted = "https://localhost:7443/?sessions=1&layout=left&tabs=left:1&state=%7B%22v%22%3A1%2C%22scroll%22%3A%5B%5D%7D"
    boundary = finalizer_boundary_for_page(monkeypatch, {"visibility": "visible", "origin": "https://localhost:7443", "href": drifted})

    assert boundary["phaseFailures"] == []
    assert boundary["atomicSnapshot"]["pageDrift"] == {
        "hrefChanged": True,
        "sessionsRemoved": [],
        "tabsRemoved": ["finder"],
        "layoutChanged": False,
        "stateChanged": True,
        "hrefLength": len(drifted),
    }


@pytest.mark.parametrize(
    ("page", "journey_id", "reason"),
    (
        ({"visibility": "visible", "origin": "https://localhost:7443", "href": "https://localhost:7443/?sessions=9&layout=left&tabs=left:9&state=%7B%22v%22%3A1%7D"}, "journey-1", "sessions_substituted, tabs_substituted"),
        ({"visibility": "visible", "origin": "https://localhost:7443", "href": "https://localhost:7443/?sessions=1&layout=left&tabs=left:1,finder&state=%7B%22v%22%3A1%7D"}, "journey-2", "document_replaced"),
        ({"visibility": "hidden", "origin": "https://localhost:7443", "href": "https://localhost:7443/?sessions=1&layout=left&tabs=left:1,finder&state=%7B%22v%22%3A1%7D"}, "journey-1", "page_hidden"),
    ),
)
def test_atomic_boundary_still_fails_a_substituted_page(monkeypatch, page, journey_id, reason):
    boundary = finalizer_boundary_for_page(monkeypatch, page, journey_id=journey_id)

    assert [entry["phase"] for entry in boundary["phaseFailures"]] == ["atomicSnapshot"]
    assert boundary["phaseFailures"][0]["message"] == f"atomic page identity was substituted: {reason}"
    assert boundary["status"] == "failed"


@pytest.mark.browser
def test_live_page_identity_survives_the_app_rewriting_its_own_url_but_catches_substitution(browser, gate_live_server):  # noqa: F811
    load_gate_browser(browser, gate_live_server)
    expected_url = browser.current_url
    pinned = soak.assert_page_identity(browser, expected_url)
    assert pinned["journeyId"].startswith("j-reload-")

    # The same rewrite the real bundle performs in `updateActiveSessionParam`: replace the whole
    # query with the app's serialized view state through `history.replaceState`, pruning a tab whose
    # session went away and growing `state` with a scroll snapshot.
    rewritten = browser.execute_script(
        """
        const params = new URLSearchParams(location.search);
        params.set('state', JSON.stringify({v: 1, scroll: [{target: 'terminal:1', top: 0}]}));
        const tabs = params.get('tabs');
        if (tabs) {
          params.set('tabs', tabs.split(';').map(slot => {
            const [name, items] = slot.split(':');
            const kept = String(items || '').split(',').slice(0, -1).join(',');
            return `${name}:${kept}`;
          }).join(';'));
        }
        history.replaceState(null, '', `${location.pathname}?${params.toString()}`);
        return location.href;
        """
    )
    assert rewritten != expected_url
    drifted = soak.assert_page_identity(browser, expected_url, pinned["journeyId"])
    assert drifted["drift"]["hrefChanged"] is True and drifted["drift"]["stateChanged"] is True
    assert drifted["journeyId"] == pinned["journeyId"]

    substituted_from = browser.current_url
    browser.execute_script(
        """
        const params = new URLSearchParams(location.search);
        params.set('sessions', `${params.get('sessions') || ''},ghost-session`);
        history.replaceState(null, '', `${location.pathname}?${params.toString()}`);
        """
    )
    with pytest.raises(AssertionError, match="sessions_substituted"):
        soak.assert_page_identity(browser, substituted_from, pinned["journeyId"])

    # A reload lands on the identical href, so only the pinned live document instance can catch it.
    browser.execute_script(f"history.replaceState(null, '', {json.dumps(expected_url)});")
    load_gate_browser(browser, gate_live_server)
    assert browser.current_url == expected_url
    with pytest.raises(AssertionError, match="document_replaced"):
        soak.assert_page_identity(browser, expected_url, pinned["journeyId"])
    assert soak.assert_page_identity(browser, expected_url)["journeyId"] != pinned["journeyId"]


def clean_soak_artifact_file(tmp_path, monkeypatch, **overrides):
    artifact = complete_success_artifact(monkeypatch)
    artifact.update(overrides)
    path = tmp_path / "clean-soak.json"
    path.write_text(json.dumps(artifact), encoding="utf-8")
    return path, artifact


def test_negative_phase_prerequisite_accepts_only_a_clean_soak_on_this_identity(tmp_path, monkeypatch):
    path, artifact = clean_soak_artifact_file(tmp_path, monkeypatch)

    prerequisite = soak.validate_clean_soak_prerequisite(
        path,
        url=artifact["url"],
        expected_head=artifact["identity"]["head"],
        expected_bundle_sha256=artifact["identity"]["bundle_sha256"],
        expected_cwd=artifact["identity"]["cwd"],
    )

    assert prerequisite["final_boundary_status"] == "clean"
    assert prerequisite["elapsed_seconds"] >= soak.MIN_OBSERVATION_SECONDS
    assert prerequisite["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("head", "c" * 40, "different identity"),
        ("bundle_sha256", "d" * 64, "different identity"),
        ("cwd", "/other", "different identity"),
    ),
)
def test_negative_phase_prerequisite_rejects_a_foreign_identity(tmp_path, monkeypatch, field, value, message):
    path, artifact = clean_soak_artifact_file(tmp_path, monkeypatch)
    expected = {"url": artifact["url"], "expected_head": artifact["identity"]["head"], "expected_bundle_sha256": artifact["identity"]["bundle_sha256"], "expected_cwd": artifact["identity"]["cwd"]}
    expected[{"head": "expected_head", "bundle_sha256": "expected_bundle_sha256", "cwd": "expected_cwd"}[field]] = value

    with pytest.raises(soak.ArtifactIntegrityError, match=message):
        soak.validate_clean_soak_prerequisite(path, **expected)


def test_negative_phase_prerequisite_rejects_a_truncated_clean_soak_artifact(tmp_path, monkeypatch):
    """The short negative phase leans on this artifact for the whole ten-minute clean journey."""
    path, artifact = clean_soak_artifact_file(tmp_path, monkeypatch)
    identity = {"url": artifact["url"], "expected_head": artifact["identity"]["head"], "expected_bundle_sha256": artifact["identity"]["bundle_sha256"], "expected_cwd": artifact["identity"]["cwd"]}
    truncated = json.loads(path.read_text(encoding="utf-8"))
    truncated["samples"] = [truncated["samples"][0], truncated["samples"][-1]]
    path.write_text(json.dumps(truncated), encoding="utf-8")

    with pytest.raises(soak.ArtifactIntegrityError, match="fewer than the 30"):
        soak.validate_clean_soak_prerequisite(path, **identity)


def test_negative_phase_prerequisite_rejects_a_short_or_failed_soak(tmp_path, monkeypatch):
    path, artifact = clean_soak_artifact_file(tmp_path, monkeypatch)
    identity = {"url": artifact["url"], "expected_head": artifact["identity"]["head"], "expected_bundle_sha256": artifact["identity"]["bundle_sha256"], "expected_cwd": artifact["identity"]["cwd"]}
    failed = json.loads(path.read_text(encoding="utf-8"))
    failed["finalBoundary"]["status"] = "failed"
    path.write_text(json.dumps(failed), encoding="utf-8")

    with pytest.raises(soak.ArtifactIntegrityError):
        soak.validate_clean_soak_prerequisite(path, **identity)

    short = json.loads(json.dumps(artifact))
    short["requested_duration_seconds"] = soak.NEGATIVE_PROBE_OBSERVATION_SECONDS
    path.write_text(json.dumps(short), encoding="utf-8")

    with pytest.raises(soak.ArtifactIntegrityError):
        soak.validate_clean_soak_prerequisite(path, **identity)


def test_negative_phase_duration_is_exact_and_the_clean_soak_keeps_its_ten_minute_floor(tmp_path):
    output = tmp_path / "artifact.json"
    common = ("a" * 40, "b" * 64, Path("/tmp/x.json"), "/repo")

    with pytest.raises(ValueError, match=f"exactly {soak.NEGATIVE_PROBE_OBSERVATION_SECONDS} seconds"):
        soak.validate_arguments("https://localhost:7443/", 600, *common, True)
    with pytest.raises(ValueError, match=f"at least {soak.MIN_OBSERVATION_SECONDS} seconds"):
        soak.validate_arguments("https://localhost:7443/", soak.NEGATIVE_PROBE_OBSERVATION_SECONDS, *common, False)

    soak.validate_arguments("https://localhost:7443/", soak.NEGATIVE_PROBE_OBSERVATION_SECONDS, *common, True)
    soak.validate_arguments("https://localhost:7443/", soak.MIN_OBSERVATION_SECONDS, *common, False)
    assert not output.exists()


def negative_attribution_inputs():
    receipt = receipt_row(1, request_id="r-web-controlled-9", source=soak.NEGATIVE_ROUTE, route=soak.NEGATIVE_ROUTE, event="api", wall_time="2026-08-07 18:05:12 PDT", http_status=500)
    handle = {
        **receipt,
        "source": soak.NEGATIVE_SOURCE,
        "receiptSource": soak.NEGATIVE_ROUTE,
        "rendered": {"matchingRows": 1, "requestId": receipt["requestId"], "source": soak.NEGATIVE_SOURCE, "route": soak.NEGATIVE_ROUTE, "event": "api", "text": "controlled browser failure HTTP 500"},
        "redaction": {channel: True for channel in ("dom", "clipboard", "retained", "upload", "storage")},
    }
    projection = receipt_projection([receipt])
    evidence = {
        "browserEvents": [{"id": 1, "type": "api", "endpoint": soak.NEGATIVE_ROUTE, "requestId": receipt["requestId"], "status": 500, "ok": False}],
        "browserLocalFailures": [{"id": 1, "level": "error", "message": "", "requestId": receipt["requestId"], "source": soak.NEGATIVE_SOURCE, "route": soak.NEGATIVE_ROUTE, "event": "api", "wallTime": receipt["wallTime"], "deliveryOutcome": "failed", "status": 500}],
        "browserReceiptBarrier": projection["barrier"],
        "browserReceiptProjection": projection,
        "serverLogErrors": [],
        "browserLogFailures": [],
        "integrityFailures": [],
        "serverLogDropped": {"count": 0, "by_level": {}},
        "cursors": {"js": 1, "server_epoch": "page-1", "server_sequence": 0, "server_log_ids": [], "server_log_records": [], "server_capacity": 10, "server_dropped_by_level": {}},
    }
    return handle, projection, evidence


class AttributionDriver:
    def __init__(self, chrome=()):
        self.chrome = list(chrome)
        self.navigated = []

    def get_log(self, _name):
        return self.chrome

    def get(self, url):
        self.navigated.append(url)

    def execute_script(self, script, *_args):
        raise AssertionError(f"the negative acceptance phase must not run page scripts: {script[:40]}")


def run_attribution(monkeypatch, evidence, *, chrome=(), handle=None, projection=None):
    default_handle, default_projection, _evidence = negative_attribution_inputs()
    monkeypatch.setattr(soak, "fence_browser_uploader", lambda _driver: {"cursor": 1, "projection": projection or default_projection, "completions": 1})
    monkeypatch.setattr(soak, "sample_evidence", lambda _driver: copy.deepcopy(evidence))
    monkeypatch.setattr(soak, "classify_incremental_evidence", lambda value, previous: (dict(value), previous))
    driver = AttributionDriver(chrome)
    boundary = soak.attribute_negative_probe(
        driver,
        previous=soak.evidence_baseline(evidence),
        baseline_projection=receipt_projection(),
        negative_handle=handle or default_handle,
    )
    return boundary, driver


def test_negative_attribution_credits_the_injected_error_without_retiring_the_page(monkeypatch):
    handle, projection, evidence = negative_attribution_inputs()

    boundary, driver = run_attribution(monkeypatch, evidence, handle=handle, projection=projection)

    assert boundary["phaseFailures"] == []
    assert boundary["status"] == "attributed"
    assert boundary["attribution"]["soleCause"] is True
    assert driver.navigated == []
    injected = boundary["attribution"]["injected"]
    assert {field: injected[field] for field in ("requestId", "source", "route", "event", "wallTime", "deliveryOutcome")} == {
        "requestId": handle["requestId"], "source": soak.NEGATIVE_SOURCE, "route": soak.NEGATIVE_ROUTE,
        "event": "api", "wallTime": handle["wallTime"], "deliveryOutcome": "failed",
    }
    assert injected == soak.negative_probe_product(handle)


@pytest.mark.parametrize(
    ("mutate", "counter"),
    (
        (lambda evidence: evidence["browserLocalFailures"].append({"id": 2, "level": "error", "requestId": "other", "source": "browser", "route": "/other", "event": "api", "wallTime": "2026-08-07 18:05:13 PDT", "deliveryOutcome": "failed", "status": 500}), "extraBrowserFailures"),
        (lambda evidence: evidence["serverLogErrors"].append({"id": 9, "level": "error", "message": "unrelated"}), "serverLogErrors"),
        (lambda evidence: evidence["browserLogFailures"].append({"level": "SEVERE", "message": "unrelated console error"}), "browserLogFailures"),
        (lambda evidence: evidence["serverLogDropped"].update({"count": 1}), "serverLogDropped"),
    ),
)
def test_negative_attribution_refuses_a_red_it_cannot_attribute_to_the_injection(monkeypatch, mutate, counter):
    handle, projection, evidence = negative_attribution_inputs()
    mutate(evidence)

    boundary, _driver = run_attribution(monkeypatch, evidence, handle=handle, projection=projection)

    assert boundary["status"] == "failed"
    assert boundary["phaseFailures"][0]["message"] == "the injected browser Error is not the sole cause of this negative probe"
    assert boundary["attribution"]["soleCause"] is False
    assert boundary["attribution"]["unrelated"][counter]


def test_negative_attribution_counts_an_unrelated_chrome_console_error(monkeypatch):
    handle, projection, evidence = negative_attribution_inputs()

    boundary, _driver = run_attribution(monkeypatch, evidence, chrome=[{"level": "SEVERE", "message": "unrelated"}], handle=handle, projection=projection)

    assert boundary["status"] == "failed"
    assert boundary["attribution"]["unrelated"]["browserLogFailures"] == 1


def test_negative_attribution_rejects_a_missing_or_mismatched_injection(monkeypatch):
    handle, projection, evidence = negative_attribution_inputs()
    evidence["browserLocalFailures"][0]["requestId"] = "r-web-someone-else"

    boundary, _driver = run_attribution(monkeypatch, evidence, handle=handle, projection=projection)

    assert boundary["status"] == "failed"
    assert boundary["attribution"]["unrelated"]["integrityFailures"] == ["negative browser error probe was not retained exactly"]


# `URLSearchParams.get()` (static_src/js/yolomux/20_layout_state.js:1387-1393) returns the FIRST value
# of a repeated key, so collapsing the parsed pairs last-wins let a URL whose first `sessions` value
# was foreign and whose last was the expected one drive the app from the foreign value while this
# classifier reported no substitution at all.
DUPLICATE_EXPECTED_URL = "https://localhost:7891/?sessions=1&layout=left&tabs=left:1&state=%7B%22v%22%3A1%7D"


def duplicate_identity(href):
    """Derive the origin from the href so a port rewrite can never desynchronize the two."""
    parsed = urlsplit(href)
    return {"origin": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}", "href": href, "visibility": "visible", "journeyId": "j-reload-1"}


@pytest.mark.parametrize(
    ("label", "href", "reason"),
    (
        ("leading foreign session", "https://localhost:7891/?sessions=ghost&sessions=1&layout=left&tabs=left:1&state=%7B%22v%22%3A1%7D", "query_keys_duplicated"),
        ("trailing foreign session", "https://localhost:7891/?sessions=1&sessions=ghost&layout=left&tabs=left:1&state=%7B%22v%22%3A1%7D", "query_keys_duplicated"),
        ("leading foreign tab slot", "https://localhost:7891/?sessions=1&layout=left&tabs=left:ghost;left:1&state=%7B%22v%22%3A1%7D", "tab_slots_duplicated"),
        ("trailing foreign tab slot", "https://localhost:7891/?sessions=1&layout=left&tabs=left:1;left:ghost&state=%7B%22v%22%3A1%7D", "tab_slots_duplicated"),
        ("duplicate layout", "https://localhost:7891/?sessions=1&layout=ghost&layout=left&tabs=left:1&state=%7B%22v%22%3A1%7D", "query_keys_duplicated"),
        ("duplicate state", "https://localhost:7891/?sessions=1&layout=left&tabs=left:1&state=%7B%22v%22%3A9%7D&state=%7B%22v%22%3A1%7D", "query_keys_duplicated"),
        ("duplicate tabs key", "https://localhost:7891/?sessions=1&layout=left&tabs=right:ghost&tabs=left:1", "query_keys_duplicated"),
    ),
)
def test_page_identity_rejects_a_duplicate_key_or_slot_the_browser_would_read_first(label, href, reason):
    identity = soak.classify_page_identity(duplicate_identity(href), expected_url=DUPLICATE_EXPECTED_URL, expected_journey_id="j-reload-1")

    assert reason in identity["reasons"], label


def test_page_identity_normalizes_repeated_keys_the_way_the_browser_reads_them():
    """First-wins, so the value this gate compares is the value the app is actually driven from."""

    first_wins = soak.page_identity_view_state("https://localhost:7891/?sessions=ghost&sessions=1&layout=left")

    assert first_wins["sessions"] == ["ghost"]
    assert "query_keys_duplicated" in first_wins["anomalies"]
    slots = soak.page_identity_view_state("https://localhost:7891/?sessions=1&layout=left&tabs=left:ghost;left:1")
    assert slots["tabs"]["left"] == ["ghost"]
    assert slots["slotOrder"] == ["left", "left"]
    assert "tab_slots_duplicated" in slots["anomalies"]


@pytest.mark.parametrize("key", soak.APP_OWNED_QUERY_KEYS)
def test_page_identity_rejects_a_duplicate_of_every_app_owned_key(key):
    expected = soak.page_identity_view_state(DUPLICATE_EXPECTED_URL)
    value = {"sessions": "1", "layout": "left", "tabs": "left:1", "state": "%7B%22v%22%3A1%7D"}[key]
    href = DUPLICATE_EXPECTED_URL.replace(f"{key}={value}", f"{key}=ghost&{key}={value}", 1)

    identity = soak.classify_page_identity(duplicate_identity(href), expected_url=DUPLICATE_EXPECTED_URL, expected_journey_id="j-reload-1")

    assert "query_keys_duplicated" in identity["reasons"]
    assert expected["anomalies"] == []


@pytest.mark.parametrize(
    ("slot_order", "duplicated"),
    (
        ("left:1;right:2", False),
        ("right:2;left:1", False),
        ("left:1;right:2;left:3", True),
        ("right:2;right:3;left:1", True),
    ),
)
def test_page_identity_accepts_any_slot_order_but_never_a_repeated_slot(slot_order, duplicated):
    expected_url = "https://localhost:7891/?sessions=1&layout=left&tabs=left:1;right:2&state=%7B%22v%22%3A1%7D"
    href = f"https://localhost:7891/?sessions=1&layout=left&tabs={slot_order}&state=%7B%22v%22%3A1%7D"

    identity = soak.classify_page_identity(duplicate_identity(href), expected_url=expected_url, expected_journey_id="j-reload-1")

    assert ("tab_slots_duplicated" in identity["reasons"]) is duplicated


@pytest.mark.parametrize(
    ("label", "href", "reason"),
    (
        ("malformed percent escape", "https://localhost:7891/?sessions=1&layout=left&tabs=left:1&state=%zz", "query_escapes_malformed"),
        ("truncated percent escape", "https://localhost:7891/?sessions=1&layout=left&tabs=left:1&state=%7", "query_escapes_malformed"),
        ("over-limit field count", "https://localhost:7891/?sessions=1&layout=left&tabs=left:1&state=%7B%22v%22%3A1%7D&unexpected=1", "query_field_count_exceeded"),
        ("field without a value assignment", "https://localhost:7891/?sessions=1&layout=left&tabs&state=%7B%22v%22%3A1%7D", "query_unparsable"),
    ),
)
def test_page_identity_rejects_the_sibling_malformations_on_the_live_url(label, href, reason):
    identity = soak.classify_page_identity(duplicate_identity(href), expected_url=DUPLICATE_EXPECTED_URL, expected_journey_id="j-reload-1")

    assert reason in identity["reasons"], label


def test_page_identity_fails_closed_when_the_measured_url_itself_is_malformed():
    identity = soak.classify_page_identity(
        duplicate_identity(DUPLICATE_EXPECTED_URL),
        expected_url="https://localhost:7891/?sessions=1&sessions=ghost&layout=left",
        expected_journey_id="j-reload-1",
    )

    assert "expected_url_malformed" in identity["reasons"]


def test_page_identity_still_accepts_the_live_app_rewrites_after_the_duplicate_fix():
    for href in (LIVE_PRUNED_AND_SCROLLED_URL, "https://localhost:7891/?sessions=1&layout=left&tabs=left:1&state=%7B%22v%22%3A1%2C%22scroll%22%3A%5B%5D%7D"):
        expected = LIVE_MEASURED_URL if href == LIVE_PRUNED_AND_SCROLLED_URL else DUPLICATE_EXPECTED_URL
        assert soak.classify_page_identity(duplicate_identity(href), expected_url=expected, expected_journey_id="j-reload-1")["reasons"] == []


def oversized_app_owned_url(key):
    """Grow one app-owned value past its per-key cap using nothing but expected items."""
    limit = soak.LIVE_SOAK_QUERY_VALUE_BYTES[key]
    padding = {
        "sessions": lambda size: ",".join(["1"] * ((size + 1) // 2)),
        "layout": lambda size: "l" * size,
        "tabs": lambda size: "left:" + ",".join(["1"] * ((size - 4) // 2)),
        "state": lambda size: '{"v":1,"pad":"' + "a" * size + '"}',
    }[key](limit + 8)
    value = {"sessions": "1", "layout": "left", "tabs": "left:1", "state": "%7B%22v%22%3A1%7D"}[key]
    href = DUPLICATE_EXPECTED_URL.replace(f"{key}={value}", f"{key}={padding}", 1)
    assert len(padding.encode("utf-8")) > limit, key
    return href


@pytest.mark.parametrize("key", soak.APP_OWNED_QUERY_KEYS)
def test_page_identity_rejects_an_app_owned_value_past_the_cap_the_preflight_enforces(key):
    """A live `tabs` field of 20 KB built only from expected items classified with no reason at all.

    `page_identity_view_state` capped the whole query at MAX_LIVE_QUERY_BYTES and nothing per key, so
    the classifier was weaker *after* navigation than `_valid_live_soak_query` is at launch.
    """
    href = oversized_app_owned_url(key)

    identity = soak.classify_page_identity(duplicate_identity(href), expected_url=DUPLICATE_EXPECTED_URL, expected_journey_id="j-reload-1")

    assert "query_value_bytes_exceeded" in identity["reasons"], key
    assert soak._valid_live_soak_query(urlsplit(href).query) is False, key


@pytest.mark.parametrize("key", soak.APP_OWNED_QUERY_KEYS)
def test_page_identity_accepts_an_app_owned_value_exactly_at_its_byte_cap(key):
    """Negative control: the cap has to be a boundary, not a check that is always on or always off."""
    limit = soak.LIVE_SOAK_QUERY_VALUE_BYTES[key]
    at_cap = {"sessions": "1" * limit, "layout": "l" * limit, "tabs": "left:" + "1" * (limit - 5), "state": '{"v":1,"pad":"' + "a" * (limit - 16) + '"}'}[key]
    assert len(at_cap.encode("utf-8")) == limit, key

    assert "query_value_bytes_exceeded" not in soak.live_query_value_anomalies(key, at_cap), key
    assert "query_value_bytes_exceeded" in soak.live_query_value_anomalies(key, at_cap + "1"), key


MALFORMED_LIVE_VALUES = (
    ("raw control character in tabs", "tabs", "left:1\x07", "query_value_control_characters"),
    ("percent-encoded control character in tabs", "tabs", "left:1%07", "query_value_control_characters"),
    ("delete character in sessions", "sessions", "1\x7f", "query_value_control_characters"),
    ("state that is not the app's versioned object", "state", "%5B%5D", "query_state_malformed"),
    ("state at a foreign version", "state", "%7B%22v%22%3A2%7D", "query_state_malformed"),
    ("state that is not JSON at all", "state", "not-json", "query_state_malformed"),
)


@pytest.mark.parametrize(
    ("key", "replacement", "reason"),
    [case[1:] for case in MALFORMED_LIVE_VALUES],
    ids=[case[0] for case in MALFORMED_LIVE_VALUES],
)
def test_page_identity_grades_live_and_expected_urls_by_the_same_per_value_rules(key, replacement, reason):
    """One owner, both readers: a URL the launch preflight refuses cannot classify clean when live."""
    value = {"sessions": "1", "layout": "left", "tabs": "left:1", "state": "%7B%22v%22%3A1%7D"}[key]
    href = DUPLICATE_EXPECTED_URL.replace(f"{key}={value}", f"{key}={replacement}", 1)

    assert reason in soak.page_identity_view_state(href)["anomalies"]
    assert reason in soak.classify_page_identity(duplicate_identity(href), expected_url=DUPLICATE_EXPECTED_URL, expected_journey_id="j-reload-1")["reasons"]
    assert soak._valid_live_soak_query(urlsplit(href).query) is False
    # The same URL named as the *expected* one is refused too, so neither side is the weaker reader.
    assert "expected_url_malformed" in soak.classify_page_identity(duplicate_identity(DUPLICATE_EXPECTED_URL), expected_url=href, expected_journey_id="j-reload-1")["reasons"]


def test_page_identity_keeps_accepting_the_app_owned_rewrites_it_measured_on_the_live_server():
    """Negative control for the new per-value rules: real app rewrites still classify clean."""
    for href, expected in ((LIVE_PRUNED_AND_SCROLLED_URL, LIVE_MEASURED_URL), (LIVE_MEASURED_URL, LIVE_MEASURED_URL), (DUPLICATE_EXPECTED_URL, DUPLICATE_EXPECTED_URL)):
        assert soak.page_identity_view_state(href)["anomalies"] == [], href
        assert soak.classify_page_identity(duplicate_identity(href), expected_url=expected, expected_journey_id="j-reload-1")["reasons"] == [], href

import copy
import threading

import yolomux_lib.app as app_module
from yolomux_lib.app import METADATA_BADGE_PULSE_UNTIL_STATE_KEY
from yolomux_lib.app import METADATA_BADGE_SIGNATURES_STATE_KEY
from yolomux_lib.app import TmuxWebtermApp


def make_metadata_app():
    app = object.__new__(TmuxWebtermApp)
    app.metadata_badge_lock = threading.Lock()
    app.metadata_badge_signatures = {}
    app.metadata_badge_pulse_until = {}
    app.load_metadata_badge_state()
    return app


def session_payload(pr, branch="feature"):
    return {
        "s": {
            "project": {
                "git": {"branch": branch, "head": "abc123 example"},
                "pull_request": copy.deepcopy(pr),
            }
        }
    }


def pending_pr():
    return {
        "number": 10,
        "state": "open",
        "checks": {
            "state": "pending",
            "summary": "CI pending",
            "total": 2,
            "failing": [],
            "pending": [{"name": "unit"}],
        },
    }


def merged_pr():
    return {
        "number": 10,
        "state": "closed",
        "merged": True,
        "checks": {
            "state": "passing",
            "summary": "CI passing",
            "total": 2,
            "failing": [],
            "pending": [],
        },
    }


def passing_pr(summary="CI passing", total=2):
    return {
        "number": 10,
        "state": "open",
        "checks": {
            "state": "passing",
            "summary": summary,
            "total": total,
            "failing": [],
            "pending": [],
        },
    }


def unknown_pr():
    return {
        "number": 10,
        "state": None,
        "merged": False,
        "checks": {
            "state": "unknown",
            "summary": "CI unknown",
            "total": 0,
            "failing": [],
            "pending": [],
        },
    }


def install_state_store(monkeypatch, state):
    def read_state():
        return copy.deepcopy(state)

    def update_state(updates):
        state.update(copy.deepcopy(updates))

    monkeypatch.setattr(app_module, "read_yolomux_state", read_state)
    monkeypatch.setattr(app_module, "update_yolomux_state", update_state)


def test_metadata_badge_pulse_does_not_persist_across_server_restart(monkeypatch):
    state = {}
    now = [1000.0]
    install_state_store(monkeypatch, state)
    monkeypatch.setattr(app_module.time, "time", lambda: now[0])

    app = make_metadata_app()
    first_payload = session_payload(pending_pr())
    app.apply_metadata_badge_pulses(first_payload)
    assert "metadata_badge_pulse_remaining_ms" not in first_payload["s"]
    assert state[METADATA_BADGE_SIGNATURES_STATE_KEY]["s"]["status"] == "open"
    assert state[METADATA_BADGE_SIGNATURES_STATE_KEY]["s"]["ci"] == "pending"

    changed_payload = session_payload(merged_pr())
    app.apply_metadata_badge_pulses(changed_payload)
    assert changed_payload["s"]["metadata_badge_pulse_remaining_ms"]["status"] == 60000
    assert state[METADATA_BADGE_SIGNATURES_STATE_KEY]["s"]["status"] == "merged"
    assert state.get(METADATA_BADGE_PULSE_UNTIL_STATE_KEY, {}) == {}

    now[0] = 1005.0
    restarted_app = make_metadata_app()
    restarted_payload = session_payload(merged_pr())
    restarted_app.apply_metadata_badge_pulses(restarted_payload)
    assert "metadata_badge_pulse_remaining_ms" not in restarted_payload["s"]


def test_metadata_badge_pulse_starts_when_restarted_server_detects_change(monkeypatch):
    state = {}
    now = [2000.0]
    install_state_store(monkeypatch, state)
    monkeypatch.setattr(app_module.time, "time", lambda: now[0])

    app = make_metadata_app()
    app.apply_metadata_badge_pulses(session_payload(pending_pr()))

    now[0] = 2100.0
    restarted_app = make_metadata_app()
    changed_payload = session_payload(merged_pr())
    restarted_app.apply_metadata_badge_pulses(changed_payload)

    assert changed_payload["s"]["metadata_badge_pulse_remaining_ms"]["status"] == 60000
    assert state.get(METADATA_BADGE_PULSE_UNTIL_STATE_KEY, {}) == {}


def test_metadata_badge_pulse_ignores_initial_github_enrichment(monkeypatch):
    state = {}
    now = [3000.0]
    install_state_store(monkeypatch, state)
    monkeypatch.setattr(app_module.time, "time", lambda: now[0])

    app = make_metadata_app()
    app.apply_metadata_badge_pulses(session_payload(unknown_pr()))

    now[0] = 3005.0
    enriched_payload = session_payload(merged_pr())
    app.apply_metadata_badge_pulses(enriched_payload)

    assert "metadata_badge_pulse_remaining_ms" not in enriched_payload["s"]
    assert state[METADATA_BADGE_SIGNATURES_STATE_KEY]["s"]["status"] == "merged"
    assert state.get(METADATA_BADGE_PULSE_UNTIL_STATE_KEY, {}) == {}


def test_metadata_badge_pulse_ignores_restart_cold_cache_degradation(monkeypatch):
    state = {
        METADATA_BADGE_SIGNATURES_STATE_KEY: {
            "s": {
                "main": "",
                "pr": "10",
                "status": "merged",
                "ci": "",
            }
        }
    }
    now = [4000.0]
    install_state_store(monkeypatch, state)
    monkeypatch.setattr(app_module.time, "time", lambda: now[0])

    restarted_app = make_metadata_app()
    cold_payload = session_payload(unknown_pr())
    restarted_app.apply_metadata_badge_pulses(cold_payload)

    assert "metadata_badge_pulse_remaining_ms" not in cold_payload["s"]
    assert state[METADATA_BADGE_SIGNATURES_STATE_KEY]["s"]["status"] == "merged"
    assert state.get(METADATA_BADGE_PULSE_UNTIL_STATE_KEY, {}) == {}


def test_metadata_badge_pulse_ignores_ci_check_list_churn(monkeypatch):
    state = {}
    now = [5000.0]
    install_state_store(monkeypatch, state)
    monkeypatch.setattr(app_module.time, "time", lambda: now[0])

    app = make_metadata_app()
    app.apply_metadata_badge_pulses(session_payload(pending_pr()))

    churned = pending_pr()
    churned["checks"]["summary"] = "CI pending: docs"
    churned["checks"]["pending"] = [{"name": "docs"}]
    churned_payload = session_payload(churned)
    app.apply_metadata_badge_pulses(churned_payload)

    assert "metadata_badge_pulse_remaining_ms" not in churned_payload["s"]
    assert state[METADATA_BADGE_SIGNATURES_STATE_KEY]["s"]["ci"] == "pending"


def test_metadata_badge_pulse_does_not_rearm_terminal_churn(monkeypatch):
    state = {}
    now = [6000.0]
    install_state_store(monkeypatch, state)
    monkeypatch.setattr(app_module.time, "time", lambda: now[0])

    app = make_metadata_app()
    app.apply_metadata_badge_pulses(session_payload(pending_pr()))
    merged_payload = session_payload(merged_pr())
    app.apply_metadata_badge_pulses(merged_payload)

    assert merged_payload["s"]["metadata_badge_pulse_remaining_ms"]["status"] == 60000
    assert merged_payload["s"]["metadata_badge_pulse_remaining_ms"]["ci"] == 60000
    assert state.get(METADATA_BADGE_PULSE_UNTIL_STATE_KEY, {}) == {}

    now[0] = 6010.0
    churned = merged_pr()
    churned["checks"]["summary"] = "CI passing after rerun"
    churned["checks"]["total"] = 3
    churned_payload = session_payload(churned)
    app.apply_metadata_badge_pulses(churned_payload)

    assert churned_payload["s"]["metadata_badge_pulse_remaining_ms"]["status"] == 50000
    assert churned_payload["s"]["metadata_badge_pulse_remaining_ms"]["ci"] == 50000
    assert state.get(METADATA_BADGE_PULSE_UNTIL_STATE_KEY, {}) == {}


def test_metadata_badge_ci_pulses_only_when_ci_enters_terminal_state(monkeypatch):
    state = {}
    now = [7000.0]
    install_state_store(monkeypatch, state)
    monkeypatch.setattr(app_module.time, "time", lambda: now[0])

    app = make_metadata_app()
    app.apply_metadata_badge_pulses(session_payload(pending_pr()))
    passing_payload = session_payload(passing_pr())
    app.apply_metadata_badge_pulses(passing_payload)

    assert "status" not in passing_payload["s"]["metadata_badge_pulse_remaining_ms"]
    assert passing_payload["s"]["metadata_badge_pulse_remaining_ms"]["ci"] == 60000

    now[0] = 7010.0
    passing_churn_payload = session_payload(passing_pr(summary="CI passing with renamed check", total=4))
    app.apply_metadata_badge_pulses(passing_churn_payload)

    assert passing_churn_payload["s"]["metadata_badge_pulse_remaining_ms"]["ci"] == 50000
    assert state.get(METADATA_BADGE_PULSE_UNTIL_STATE_KEY, {}) == {}

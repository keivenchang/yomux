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


def test_metadata_badge_pulse_survives_server_restart(monkeypatch):
    state = {}
    now = [1000.0]
    install_state_store(monkeypatch, state)
    monkeypatch.setattr(app_module.time, "time", lambda: now[0])

    app = make_metadata_app()
    first_payload = session_payload(pending_pr())
    app.apply_metadata_badge_pulses(first_payload)
    assert "metadata_badge_pulse_remaining_ms" not in first_payload["s"]
    assert state[METADATA_BADGE_SIGNATURES_STATE_KEY]["s"]["status"] == "open · CI pending"

    changed_payload = session_payload(merged_pr())
    app.apply_metadata_badge_pulses(changed_payload)
    assert changed_payload["s"]["metadata_badge_pulse_remaining_ms"]["status"] == 20000
    assert state[METADATA_BADGE_SIGNATURES_STATE_KEY]["s"]["status"] == "merged"
    assert state[METADATA_BADGE_PULSE_UNTIL_STATE_KEY]["s"]["status"] == 1020.0

    now[0] = 1005.0
    restarted_app = make_metadata_app()
    restarted_payload = session_payload(merged_pr())
    restarted_app.apply_metadata_badge_pulses(restarted_payload)
    assert restarted_payload["s"]["metadata_badge_pulse_remaining_ms"]["status"] == 15000


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

    assert changed_payload["s"]["metadata_badge_pulse_remaining_ms"]["status"] == 20000
    assert state[METADATA_BADGE_PULSE_UNTIL_STATE_KEY]["s"]["status"] == 2120.0


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

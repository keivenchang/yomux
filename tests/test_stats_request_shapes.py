"""Contract test: the python request-shape mirror is byte-equal to the client owner.

The shared goldens (tests/fixtures/stats_request_shapes.json) are generated from the
client's `jsDebugStatsSampleQuery`; the node side asserts the same goldens in
tests/yostats_performance.test.js. A change to the client request shape must
regenerate the goldens, and both languages fail until they agree — no probe or
fixture can silently drift from what the browser actually sends again."""
import json
from pathlib import Path

from tests.browser_helpers.stats_request_shapes import legacy_reader_history_request
from tests.browser_helpers.stats_request_shapes import reader_history_request
from tests.browser_helpers.stats_request_shapes import stats_sample_query
from tests.browser_helpers.stats_request_shapes import token_resolution_for_range

GOLDENS = json.loads((Path(__file__).parent / "fixtures" / "stats_request_shapes.json").read_text(encoding="utf-8"))


def _mirror_params(params: dict) -> dict:
    mapping = {
        "since": "since", "clientId": "client_id", "tokenConsumer": "token_consumer",
        "historyStart": "history_start", "historyEnd": "history_end",
        "historyResolution": "history_resolution", "historyMaxPoints": "history_max_points",
        "history": "history",
    }
    return {mapping[key]: value for key, value in params.items()}


def test_python_mirror_matches_every_client_golden_query():
    assert len(GOLDENS["cases"]) >= 11
    for case in GOLDENS["cases"]:
        assert stats_sample_query(**_mirror_params(case["params"])) == case["query"], case["name"]


def test_client_queries_carry_no_legacy_token_params():
    """ONE history stream: current clients never send the retired compact
    token-stream params. The server still ACCEPTS them from old clients
    (legacy compat, retired in a later release), but no golden — and therefore
    no runtime query — may reintroduce them."""
    for case in GOLDENS["cases"]:
        for param in ("token_since=", "token_resolution=", "token_history_start=", "token_history_end="):
            assert param not in case["query"], f"{case['name']}: legacy token param {param!r} returned to the client wire"


def test_reader_request_shapes_match_the_goldens_without_token_params():
    """The reader dialect is `start`/`end`/`resolution_seconds`/`max_points` — produced by
    the REAL web-layer translator, never hand-mapped wire names (a hand-mapped
    `history_*` dict silently queries the whole unwindowed store). Token detail
    rides every record, so the translated request carries no live token selection."""
    now = int(GOLDENS["nowSeconds"])
    checked = 0
    for case in GOLDENS["cases"]:
        expected = case.get("readerRequest")
        if not expected or case["name"] == "full-retention-prefetch":
            continue
        produced = reader_history_request(case["rangeSeconds"], now, client_id="golden-client")
        assert produced == expected, case["name"]
        assert "history_start" not in produced and produced["start"] == now - case["rangeSeconds"], case["name"]
        assert produced["token_resolution_seconds"] == 0, case["name"]
        checked += 1
    assert checked >= 9


def test_legacy_reader_request_still_builds_the_old_client_shape():
    """The server keeps serving old clients (never-hard-gate): the legacy request
    builder produces the pre-single-stream shape, token params included, through
    the REAL translator. Retire together with the server's legacy path."""
    now = int(GOLDENS["nowSeconds"])
    legacy = legacy_reader_history_request(24 * 3600, now)
    assert legacy["token_resolution_seconds"] == 300
    assert legacy["token_history_start"] == now - 24 * 3600
    assert legacy["token_history_end"] == 0
    short = legacy_reader_history_request(3600, now)
    assert short["token_resolution_seconds"] == 0


def test_token_display_floor_rule_matches_the_client_tiers():
    """token_resolution_for_range is the token charts' DISPLAY FLOOR mirror
    (debugGraphAgentTokenResolution) — no longer a wire parameter for current
    clients, still the legacy-compat resolution an old client would send."""
    assert token_resolution_for_range(300) == 0
    assert token_resolution_for_range(3600) == 0
    assert token_resolution_for_range(4 * 3600 - 1) == 0
    assert token_resolution_for_range(4 * 3600) == 120
    assert token_resolution_for_range(8 * 3600) == 120
    assert token_resolution_for_range(16 * 3600) == 300
    assert token_resolution_for_range(24 * 3600) == 300


def test_no_test_hand_rolls_a_stats_sample_query_string():
    """Every stats-sample query CONSTRUCTED in tests must come from the shared owners —
    building the URL by hand is how the wrong-shape probe class starts. URL inspection
    (`includes(`/`startsWith(`) is fine, and a deliberate route-parser test may opt out
    with an adjacent `request-shape-exempt:` comment naming its reason."""
    tests_dir = Path(__file__).parent
    offenders = []
    for path in tests_dir.rglob("*.py"):
        if path.name in {"test_stats_request_shapes.py", "stats_request_shapes.py"}:
            continue  # the contract test and the owner/mirror themselves
        lines = path.read_text(encoding="utf-8").splitlines()
        for number, line in enumerate(lines, start=1):
            if "/api/stats-sample?" not in line:
                continue
            if "includes(" in line or "startsWith(" in line:
                continue  # inspecting an incoming URL, not constructing a request
            context = "\n".join(lines[max(0, number - 4):number])
            if "request-shape-exempt:" in context:
                continue
            offenders.append(f"{path.relative_to(tests_dir)}:{number}")
    assert offenders == [], f"hand-rolled stats-sample queries (route through the shared owners): {offenders}"

"""Operator-only authenticated browser soak gate for a local YOLOmux server."""

from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import re
import shutil
import ssl
import subprocess
import time
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from numbers import Real
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl
from urllib.parse import urljoin
from urllib.parse import urlsplit
from urllib.request import Request
from urllib.request import urlopen
from zoneinfo import ZoneInfo

from selenium.common.exceptions import WebDriverException
from selenium.webdriver.support.ui import WebDriverWait

from .auth import AUTH_CONFIG_PATH
from .auth import AUTH_COOKIE_NAME
from .auth import AuthUser
from .auth import auth_cookie_value
from .auth import read_auth_users
from . import browser_diagnostic_receipts
from .diagnostic_redaction import redact_diagnostic_value
from .tmux.sessions import process_cwd


SAMPLE_SECONDS = 5.0
SETTLE_SECONDS = 90.0
# The persisted validator has to prove a retained sample series actually covers its window, because
# `elapsed_seconds: 603.4` next to two samples proves nothing about the 600 seconds in between. The
# loop samples every SAMPLE_SECONDS; the worst gap measured across four real artifacts on this box
# is 10.8s (settle window of /tmp/yo7771-soak-r5.json) because a loaded box stretches the WebDriver
# round trip, so four times the cadence leaves real headroom while still requiring at least 30
# samples across a 600-second observation — more than any truncated or hand-built artifact carries.
MAX_SAMPLE_GAP_SECONDS = 4 * SAMPLE_SECONDS
MIN_OBSERVATION_SECONDS = 600
# The negative phase is an acceptance step for the injected Error, not a second clean journey: the
# clean 600-second observation is proven by the `--clean-soak-artifact` prerequisite. 30 seconds is
# the shortest honest window here — six samples at SAMPLE_SECONDS give a baseline plus five
# incremental transitions, and at the 10-second stats resolution it covers about three deltas, so
# `classify_hidden_stats_stream` can prove coherent advancement rather than one frozen reading.
NEGATIVE_PROBE_OBSERVATION_SECONDS = 30
CANONICAL_HOST = "localhost"
NEGATIVE_REQUEST_ID = "r-p0-negative-fixed"
NEGATIVE_ROUTE = "/api/yolo-rules"
NEGATIVE_SOURCE = "browser"
NEGATIVE_MESSAGE = "controlled browser failure"
NEGATIVE_CANARY = "P0-LIVE-SOAK-CANARY-DO-NOT-RETAIN"
STATS_READINESS_SECONDS = 30
LISTENER_PROBE_TIMEOUT_SECONDS = 3
LIVE_SOAK_QUERY_SHAPES = (
    ("sessions", "layout"),
    ("sessions", "layout", "tabs", "state"),
)
LIVE_SOAK_QUERY_VALUE_BYTES = {
    "sessions": 4 * 1024,
    "layout": 4 * 1024,
    "tabs": 16 * 1024,
    "state": 64 * 1024,
}


class ArtifactIntegrityError(RuntimeError):
    """A persisted artifact cannot prove the soak completed cleanly."""


@dataclass(frozen=True)
class ListenerIdentity:
    pid: int
    cwd: str
    started: str
    head: str


def pacific_wall_time() -> str:
    return datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d %H:%M:%S %Z")


def _valid_query_percent_escapes(raw_value: str) -> bool:
    for index, character in enumerate(raw_value):
        if character == "%" and (
            index + 2 >= len(raw_value)
            or any(candidate not in "0123456789abcdefABCDEF" for candidate in raw_value[index + 1:index + 3])
        ):
            return False
    return True


def _valid_live_soak_state(raw_value: str) -> bool:
    """The app's own `state` writer emits a versioned object; anything else is not this app's URL."""

    try:
        state = json.loads(raw_value)
    except (TypeError, ValueError):
        return False
    return isinstance(state, dict) and not isinstance(state.get("v"), bool) and state.get("v") == 1


def live_query_value_anomalies(name: str, value: str) -> list[str]:
    """Grade one query value; the launch preflight and the live page classifier share this owner.

    `page_identity_view_state` used to cap only the *total* query length at MAX_LIVE_QUERY_BYTES, so
    a live URL carrying a 20 KB `tabs` field built only from expected items classified with no reason
    at all — the classifier was weaker after navigation than `_valid_live_soak_query` is at launch.
    Both readers now apply the same per-key byte cap, control-character rule and `state` shape.
    """

    anomalies: list[str] = []
    limit = LIVE_SOAK_QUERY_VALUE_BYTES.get(name)
    if limit is not None and len(value.encode("utf-8")) > limit:
        anomalies.append("query_value_bytes_exceeded")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        anomalies.append("query_value_control_characters")
    if name == "state" and not _valid_live_soak_state(value):
        anomalies.append("query_state_malformed")
    return anomalies


def _valid_live_soak_query(raw_query: str) -> bool:
    if not raw_query:
        return True
    raw_fields = raw_query.split("&")
    if any("=" not in field for field in raw_fields):
        return False
    raw_names = tuple(field.split("=", 1)[0] for field in raw_fields)
    if raw_names not in LIVE_SOAK_QUERY_SHAPES:
        return False
    if any(not _valid_query_percent_escapes(field.split("=", 1)[1]) for field in raw_fields):
        return False
    try:
        query = parse_qsl(raw_query, keep_blank_values=True, strict_parsing=True, max_num_fields=4)
    except ValueError:
        return False
    if tuple(name for name, _value in query) != raw_names:
        return False
    # An operator-supplied launch URL must also name every value it declares; the live classifier
    # grades what the app rewrote and states emptiness through its own `sessions_emptied` reason.
    if any(not value or live_query_value_anomalies(name, value) for name, value in query):
        return False
    return True


def validate_clean_soak_prerequisite(
    path: Path,
    *,
    url: str,
    expected_head: str,
    expected_bundle_sha256: str,
    expected_cwd: str | None,
) -> dict[str, Any]:
    """Refuse to run the short negative phase without a complete clean soak on this exact identity.

    The negative probe observes for `NEGATIVE_PROBE_OBSERVATION_SECONDS`, so on its own it proves
    nothing about the ten-minute clean journey. Requiring the clean artifact — validated by the same
    `validate_success_artifact` that gates a real success, and pinned to the live listener's HEAD,
    cwd, bundle and URL — keeps the short phase from ever standing in for the full soak.
    """

    raw = path.read_bytes()
    artifact = json.loads(raw.decode("utf-8"))
    if not isinstance(artifact, dict):
        raise ArtifactIntegrityError("--clean-soak-artifact is not a soak artifact object")
    validate_success_artifact(artifact)
    identity = artifact["identity"]
    if (
        artifact["url"] != url
        or identity["head"] != expected_head
        or identity["bundle_sha256"] != expected_bundle_sha256
        or (expected_cwd is not None and identity["cwd"] != expected_cwd)
    ):
        raise ArtifactIntegrityError("--clean-soak-artifact was recorded on a different identity than this negative probe")
    if artifact["requested_duration_seconds"] < MIN_OBSERVATION_SECONDS or artifact["elapsed_seconds"] < MIN_OBSERVATION_SECONDS:
        raise ArtifactIntegrityError("--clean-soak-artifact did not observe the full clean soak duration")
    return {
        "path": str(path),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "url": artifact["url"],
        "identity": dict(identity),
        "requested_duration_seconds": artifact["requested_duration_seconds"],
        "elapsed_seconds": artifact["elapsed_seconds"],
        "final_boundary_status": artifact["finalBoundary"]["status"],
    }


def negative_probe_product(negative_handle: Mapping[str, Any]) -> dict[str, Any]:
    """Build the one durable record of an injected Error, for every phase that detects one."""

    return {
        "type": "expected_negative_probe",
        **{field: negative_handle[field] for field in ("key", "requestId", "source", "route", "event", "wallTime", "deliveryOutcome", "httpStatus")},
        "receipt": {field: negative_handle[field] for field in ("key", "epoch", "eventId", "requestId", "receiptSource", "route", "event", "wallTime", "deliveryOutcome", "httpStatus", "status")},
        "rendered": dict(negative_handle["rendered"]),
        "redaction": dict(negative_handle["redaction"]),
    }


def validate_arguments(url: str, duration: int, expected_head: str, expected_bundle_sha256: str, output: Path, expected_cwd: str | None = None, negative_probe: bool = False) -> None:
    parsed = urlsplit(url)
    if parsed.port is None:
        raise ValueError("--url must include an explicit HTTPS port")
    if (
        parsed.scheme != "https"
        or parsed.netloc != f"{CANONICAL_HOST}:{parsed.port}"
        or parsed.path != "/"
        or parsed.username
        or parsed.password
        or parsed.fragment
        or not _valid_live_soak_query(parsed.query)
    ):
        raise ValueError("--url must be the canonical https://localhost:<port>/ URL with no query, sessions/layout, or the exact sessions/layout/tabs/state query shape")
    if negative_probe:
        if duration != NEGATIVE_PROBE_OBSERVATION_SECONDS:
            raise ValueError(f"--duration must be exactly {NEGATIVE_PROBE_OBSERVATION_SECONDS} seconds for the negative browser error probe")
    elif duration < MIN_OBSERVATION_SECONDS:
        raise ValueError(f"--duration must be at least {MIN_OBSERVATION_SECONDS} seconds")
    if len(expected_head) != 40 or any(char not in "0123456789abcdef" for char in expected_head):
        raise ValueError("--expected-head must be a lowercase full 40-character SHA")
    if len(expected_bundle_sha256) != 64 or any(char not in "0123456789abcdef" for char in expected_bundle_sha256):
        raise ValueError("--expected-bundle-sha256 must be a lowercase SHA256 hex digest")
    if not output.expanduser().resolve(strict=False).is_relative_to(Path("/tmp")):
        raise ValueError("--output must be under /tmp")
    if expected_cwd is not None and not expected_cwd.startswith("/"):
        raise ValueError("--expected-cwd must be an absolute path")


def listener_pids(port: int) -> list[str]:
    """Find the listening PIDs the way boot.sh:307 port_listener_pids() does: ss on Linux, lsof only as the macOS/no-ss path.

    `lsof -iTCP` walks every open file descriptor on the host, which costs seconds on a box with
    thousands of FDs and made this gate time out before a browser ever launched. `ss` asks the
    kernel for one port. docs/DEVELOPMENT.md:286 already states this platform split as the contract.
    """
    if platform.system() == "Linux" and shutil.which("ss"):
        result = subprocess.run(["ss", "-ltnp", f"sport = :{port}"], capture_output=True, text=True, check=False, timeout=LISTENER_PROBE_TIMEOUT_SECONDS)
        return sorted({match for match in re.findall(r"\bpid=(\d+)", result.stdout)}, key=int)
    result = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"], capture_output=True, text=True, check=False, timeout=LISTENER_PROBE_TIMEOUT_SECONDS)
    return sorted({line.strip() for line in result.stdout.splitlines() if line.strip().isdigit()}, key=int)


def listener_pid(port: int) -> int:
    pids = listener_pids(port)
    if len(pids) != 1:
        raise RuntimeError(f"expected exactly one listener on port {port}, found {pids or 'none'}")
    return int(pids[0])


def listener_identity(port: int) -> ListenerIdentity:
    pid = listener_pid(port)
    cwd = process_cwd(pid)
    if not cwd:
        raise RuntimeError(f"cannot resolve listener cwd for PID {pid}")
    started = subprocess.run(["ps", "-p", str(pid), "-o", "lstart="], capture_output=True, text=True, check=True, timeout=3).stdout.strip()
    head = subprocess.run(["git", "-C", cwd, "rev-parse", "HEAD"], capture_output=True, text=True, check=True, timeout=5).stdout.strip()
    return ListenerIdentity(pid=pid, cwd=cwd, started=started, head=head)


def discover_served_bundle(driver: Any, url: str, navigate: bool = True) -> tuple[str, str]:
    """Hash the canonical bundle, optionally without replacing page-local diagnostics."""
    if navigate:
        driver.get(url)
    bundle_url = driver.execute_script("return [...document.scripts].map(node => node.src).find(src => /\\/static\\/yolomux\\.js(?:$|\\?)/.test(src)) || ''")
    if not isinstance(bundle_url, str) or not bundle_url:
        raise RuntimeError("served page did not name /static/yolomux.js")
    response = driver.execute_async_script("""
        const done = arguments[arguments.length - 1];
        fetch(arguments[0], {cache: 'no-store', credentials: 'same-origin'})
          .then(async value => done({status: value.status, text: await value.text()}))
          .catch(error => done({status: 0, error: String(error)}));
    """, bundle_url)
    if not isinstance(response, Mapping) or response.get("status") != 200 or not isinstance(response.get("text"), str):
        raise RuntimeError("cannot fetch served yolomux bundle")
    resolved = urljoin(url, bundle_url)
    parsed = urlsplit(resolved)
    root = urlsplit(url)
    if (parsed.scheme, parsed.hostname, parsed.port, parsed.path) != (root.scheme, root.hostname, root.port, "/static/yolomux.js"):
        raise RuntimeError("served bundle is not the same-origin canonical yolomux bundle")
    return resolved, hashlib.sha256(response["text"].encode("utf-8")).hexdigest()


def select_auth_user() -> AuthUser:
    users = read_auth_users(AUTH_CONFIG_PATH)
    if not users:
        raise RuntimeError("no configured YOLOmux account is available")
    return next((user for user in users if user.role == "admin"), users[0])


def install_auth_cookie(driver: Any, url: str, port: int) -> AuthUser:
    """Install the session cookie and prove the browser's cookie store kept it.

    `add_cookie` is a request, not a result: Chrome can refuse the cookie outright
    (`unable to set cookie`) and it can also report success while storing nothing the app origin can
    send back. Either way the soak used to keep running unauthenticated and only fail 90 seconds
    later inside `assert_page_identity`, which reports a changed page identity and hides the real
    cause. Reading the jar back turns both into one exact failure at the point of installation. The
    cookie value is a live credential, so no failure path may name it.
    """
    user = select_auth_user()
    cookie = {"name": f"{AUTH_COOKIE_NAME}_{port}", "value": auth_cookie_value(user.username, user.password), "path": "/", "secure": True, "httpOnly": True}
    driver.get(urljoin(url, "/login"))
    driver.add_cookie(cookie)
    installed = [entry for entry in driver.get_cookies() if isinstance(entry, Mapping) and entry.get("name") == cookie["name"]]
    if len(installed) != 1 or any(installed[0].get(field) != cookie[field] for field in ("value", "path", "secure", "httpOnly")):
        retained = sorted(str(entry.get("name")) for entry in driver.get_cookies() if isinstance(entry, Mapping))
        raise RuntimeError(f"the browser did not retain the authenticated YOLOmux session cookie {cookie['name']} for {urljoin(url, '/')}; the cookie store holds {retained}")
    return user


def authenticated_server_log_reader(url: str, port: int, user: AuthUser) -> Any:
    cookie = f"{AUTH_COOKIE_NAME}_{port}={auth_cookie_value(user.username, user.password)}"
    endpoint = urljoin(url, "/api/logs")
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE

    def read() -> dict[str, Any]:
        request = Request(endpoint, headers={"Accept": "application/json", "Cookie": cookie}, method="GET")
        with urlopen(request, timeout=5, context=context) as response:
            if response.status != 200:
                raise AssertionError("authenticated server log read failed")
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise AssertionError("authenticated server log payload is malformed")
        return payload

    return read


def assert_stats_hidden(driver: Any) -> None:
    hidden = driver.execute_script("""
        const sentinel = window.__yolomuxStatsHiddenSentinel;
        return Boolean(sentinel && sentinel.installed && sentinel.everVisible === false && typeof jsDebugStatsPanelVisible === 'function' && jsDebugStatsPanelVisible() === false);
    """)
    if hidden is not True:
        raise AssertionError("YO!stats is visible or its hidden-state diagnostic is unavailable")


PAGE_IDENTITY_SCRIPT = (
    "return {origin: location.origin, href: location.href, visibility: document.visibilityState,"
    " journeyId: typeof reloadClientJourneyId === 'undefined' ? '' : String(reloadClientJourneyId || '')};"
)
PAGE_IDENTITY_FIELDS = {"origin", "href", "visibility", "journeyId"}
APP_OWNED_QUERY_KEYS = ("sessions", "layout", "tabs", "state")
# The acceptance URL shape is at most sessions/layout/tabs/state (LIVE_SOAK_QUERY_SHAPES), so a live
# query carrying more fields than that is already outside what this gate measures.
MAX_LIVE_QUERY_FIELDS = 4
# Parse a bounded margin above the shape so a duplicate key is named as a duplicate instead of
# tripping the field cap first and losing the pairs that identify it.
MAX_LIVE_QUERY_PARSE_FIELDS = 16
MAX_LIVE_QUERY_BYTES = sum(LIVE_SOAK_QUERY_VALUE_BYTES.values())
MAX_RECORDED_PAGE_IDENTITY_DRIFT = 64


def page_identity_view_state(url: str) -> dict[str, Any]:
    """Split one app URL into its identity anchors and its app-owned view state.

    The app serializes its own view state back into the URL with `history.replaceState`
    (`updateActiveSessionParam`): it prunes tabs whose tmux session has gone away and it grows the
    `state` value as terminals report scroll positions. That is production behaviour, not a page
    substitution, so the soak anchors on origin, path and the live document instance and treats the
    query as classified drift rather than identity.
    """

    parsed = urlsplit(url)
    anomalies: list[str] = []
    pairs: list[tuple[str, str]] = []
    if parsed.query:
        fields = parsed.query.split("&")
        if len(fields) > MAX_LIVE_QUERY_FIELDS:
            anomalies.append("query_field_count_exceeded")
        if len(parsed.query.encode("utf-8")) > MAX_LIVE_QUERY_BYTES:
            anomalies.append("query_length_exceeded")
        if any(not _valid_query_percent_escapes(field) for field in fields):
            anomalies.append("query_escapes_malformed")
        try:
            pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True, max_num_fields=MAX_LIVE_QUERY_PARSE_FIELDS)
        except ValueError:
            anomalies.append("query_unparsable")
    names = [name for name, _value in pairs]
    if len(set(names)) != len(names):
        anomalies.append("query_keys_duplicated")
    # Every parsed pair is graded, not just the first-wins winner: a repeated key's losing value is
    # still bytes the live document is carrying, and the same per-key rules run at launch preflight.
    for name, value in pairs:
        for anomaly in live_query_value_anomalies(name, value):
            if anomaly not in anomalies:
                anomalies.append(anomaly)
    # The browser reads this URL with `URLSearchParams.get()` (20_layout_state.js:1387-1393), which
    # returns the FIRST value of a repeated key. Collapsing the parsed pairs with `dict()` would take
    # the last, so a URL whose first `sessions` value is foreign and whose last is the expected one
    # would drive the app from the foreign value while this classifier reported no substitution.
    # Duplicates are rejected above; reading first-wins here keeps the two readers agreeing anyway.
    query: dict[str, str] = {}
    for name, value in pairs:
        query.setdefault(name, value)
    slot_names: list[str] = []
    tabs: dict[str, list[str]] = {}
    for slot in (query.get("tabs") or "").split(";"):
        if not slot:
            continue
        name, _, items = slot.partition(":")
        slot_names.append(name)
        tabs.setdefault(name, [item for item in items.split(",") if item])
    if len(set(slot_names)) != len(slot_names):
        anomalies.append("tab_slots_duplicated")
    return {
        "origin": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}",
        "path": parsed.path,
        "keys": sorted(query),
        "sessions": [item for item in (query.get("sessions") or "").split(",") if item],
        "tabs": tabs,
        "slotOrder": slot_names,
        "layout": query.get("layout", ""),
        "state": query.get("state", ""),
        "anomalies": anomalies,
    }


def classify_page_identity(actual: Any, *, expected_url: str, expected_journey_id: str | None = None) -> dict[str, Any]:
    """Return typed identity-substitution reasons and the allowed app-owned drift, never silence."""

    if not isinstance(actual, Mapping) or set(actual) != PAGE_IDENTITY_FIELDS or any(not isinstance(actual[field], str) for field in PAGE_IDENTITY_FIELDS):
        return {"reasons": ["page_identity_unreadable"], "drift": None, "journeyId": ""}
    expected = page_identity_view_state(expected_url)
    live = page_identity_view_state(actual["href"])
    reasons: list[str] = list(live["anomalies"])
    if expected["anomalies"]:
        reasons.append("expected_url_malformed")
    if actual["origin"] != expected["origin"] or live["origin"] != expected["origin"]:
        reasons.append("origin_changed")
    if live["path"] != expected["path"]:
        reasons.append("path_changed")
    if actual["visibility"] != "visible":
        reasons.append("page_hidden")
    if not actual["journeyId"]:
        reasons.append("document_journey_unavailable")
    elif expected_journey_id is not None and actual["journeyId"] != expected_journey_id:
        reasons.append("document_replaced")
    if [key for key in live["keys"] if key not in APP_OWNED_QUERY_KEYS]:
        reasons.append("query_keys_added")
    # You can only be substituted away from what the measured URL declared: a soak started on a bare
    # `/` legitimately grows sessions/layout/tabs/state as the app restores and serializes its view.
    if expected["sessions"]:
        if [item for item in live["sessions"] if item not in expected["sessions"]]:
            reasons.append("sessions_substituted")
        if not live["sessions"]:
            reasons.append("sessions_emptied")
    if expected["tabs"]:
        if [slot for slot in live["tabs"] if slot not in expected["tabs"]]:
            reasons.append("tab_slots_substituted")
        if any(item not in expected["tabs"].get(slot, []) for slot, items in live["tabs"].items() for item in items):
            reasons.append("tabs_substituted")
    drift = {
        "hrefChanged": actual["href"] != expected_url,
        "sessionsRemoved": [item for item in expected["sessions"] if item not in live["sessions"]],
        "tabsRemoved": sorted({item for slot, items in expected["tabs"].items() for item in items if item not in live["tabs"].get(slot, [])}),
        "layoutChanged": live["layout"] != expected["layout"],
        "stateChanged": live["state"] != expected["state"],
        "hrefLength": len(actual["href"]),
    }
    return {"reasons": reasons, "drift": drift, "journeyId": actual["journeyId"]}


def assert_page_identity(driver: Any, expected_url: str, expected_journey_id: str | None = None) -> dict[str, Any]:
    """Prove the soak is still measuring the same document, and return its classified drift."""

    identity = classify_page_identity(
        driver.execute_script(PAGE_IDENTITY_SCRIPT),
        expected_url=expected_url,
        expected_journey_id=expected_journey_id,
    )
    if identity["reasons"]:
        raise AssertionError(f"authenticated app page identity changed: {', '.join(identity['reasons'])}")
    return identity


def new_page_identity_drift_record() -> dict[str, Any]:
    return {"observed": 0, "entries": []}


def note_page_identity_drift(record: dict[str, Any], identity: Mapping[str, Any], *, phase: str, elapsed: float) -> None:
    """Retain every distinct app-owned URL rewrite; drift is recorded, never discarded."""

    drift = identity["drift"]
    if not drift["hrefChanged"]:
        return
    entries = record["entries"]
    if entries and entries[-1]["drift"] == drift:
        return
    record["observed"] += 1
    if len(entries) < MAX_RECORDED_PAGE_IDENTITY_DRIFT:
        entries.append({"phase": phase, "at_pt": pacific_wall_time(), "elapsed_seconds": elapsed, "drift": dict(drift)})


def server_ring_cursor(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "js": 0,
        "server_epoch": record["epoch"],
        "server_sequence": record["sequence"],
        "server_log_ids": list(record["ids"]),
        "server_log_records": [dict(entry) for entry in record["logs"]],
        "server_capacity": record["capacity"],
        "drop_count": record["dropped"]["count"],
        "server_dropped_by_level": dict(record["dropped"]["by_level"]),
    }


def install_start_of_document_sentinels(driver: Any) -> None:
    """Install both observation sentinels before the protected root loads.

    Production ships neither sentinel, so the soak owns them. Both must observe the same document from
    its first byte, so they are installed by one CDP call: a partial install would let a transient panel
    or an early surface visit escape sampling. One shared visibility predicate serves both so the
    hidden-stats sentinel and the journey gate can never disagree about what "visible" means.
    """
    source = """
      (() => {
        const visiblePanels = () => [...document.querySelectorAll('.js-debug-panel')].filter(panel => {
          if (!panel.isConnected) return false;
          const style = getComputedStyle(panel); const rect = panel.getBoundingClientRect();
          return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
        });
        const stats = window.__yolomuxStatsHiddenSentinel = {installed: true, everVisible: false, checks: 0};
        const journey = window.__yolomuxBrowserJourneyGate = {
          visitedSurfaces: [], consumedServerLogIds: [], serverLogEpoch: null, observer: null, observe: null,
        };
        journey.observe = () => {
          if (visiblePanels().length && !journey.visitedSurfaces.includes('stats')) journey.visitedSurfaces.push('stats');
        };
        const check = () => {
          stats.checks += 1;
          if (visiblePanels().length) stats.everVisible = true;
          journey.observe();
        };
        journey.observer = new MutationObserver(check);
        journey.observer.observe(document, {childList: true, subtree: true, attributes: true, attributeFilter: ['class', 'style', 'hidden']});
        document.addEventListener('DOMContentLoaded', check, {once: true});
        check();
      })();
    """
    try:
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": source})
    except WebDriverException as error:
        raise RuntimeError("cannot install start-of-document YO!stats and journey sentinels") from error


def validate_hidden_stats_stream_evidence(value: Any) -> dict[str, Any] | None:
    """Validate one exact, bounded hidden-stats stream snapshot."""

    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise AssertionError("hidden YO!stats stream evidence is malformed")
    top_fields = {"moduleReady", "clientReady", "controllerReady", "generationReady", "panelVisible", "paintedGenerationKey", "stream", "sampledAtMs", "everVisible"}
    stream_fields = {"running", "visible", "healthy", "streamOpen", "streamEpoch", "deliverySequence", "acceptedDeltaSequence", "lastDeliveryKind", "lastDeliveryAtMs", "lastDeliveryEpoch", "rangeSeconds", "resolutionSeconds", "sourceGeneration", "cacheGeneration", "deltaRevision"}
    stream = value.get("stream")
    if set(value) != top_fields or not isinstance(stream, Mapping) or set(stream) != stream_fields:
        raise AssertionError("hidden YO!stats stream evidence has unexpected fields")
    if any(value.get(field) is not expected for field, expected in (("moduleReady", True), ("clientReady", True), ("controllerReady", True), ("generationReady", True), ("panelVisible", False), ("everVisible", False))):
        return None
    if any(stream.get(field) is not True for field in ("running", "visible", "healthy", "streamOpen")):
        return None
    integer_fields = ("streamEpoch", "deliverySequence", "acceptedDeltaSequence", "lastDeliveryAtMs", "lastDeliveryEpoch", "rangeSeconds", "resolutionSeconds", "sourceGeneration", "cacheGeneration", "deltaRevision")
    if any(isinstance(stream.get(field), bool) or not isinstance(stream.get(field), int) or stream[field] < 0 or stream[field] > browser_diagnostic_receipts.JAVASCRIPT_MAX_SAFE_INTEGER for field in integer_fields):
        raise AssertionError("hidden YO!stats stream counters are malformed")
    sampled_at = value.get("sampledAtMs")
    if isinstance(sampled_at, bool) or not isinstance(sampled_at, int) or sampled_at < 0 or sampled_at > browser_diagnostic_receipts.JAVASCRIPT_MAX_SAFE_INTEGER:
        raise AssertionError("hidden YO!stats sample time is malformed")
    if stream["streamEpoch"] < 1 or stream["deliverySequence"] < 1 or stream["lastDeliveryEpoch"] != stream["streamEpoch"] or stream["lastDeliveryKind"] not in {"ready", "delta"} or stream["rangeSeconds"] < 1 or stream["resolutionSeconds"] < 1 or stream["sourceGeneration"] < 1 or stream["cacheGeneration"] < 1:
        return None
    freshness_ms = max(30_000, stream["resolutionSeconds"] * 3_000)
    if sampled_at < stream["lastDeliveryAtMs"] or sampled_at - stream["lastDeliveryAtMs"] > freshness_ms:
        return None
    return {**{field: value[field] for field in top_fields if field != "stream"}, "stream": dict(stream)}


def sample_hidden_stats_stream(driver: Any, *, start: bool = False) -> dict[str, Any] | None:
    value = driver.execute_script("""
        if (arguments[0] === true && typeof syncJsDebugCurrentStatsClient === 'function') {
          syncJsDebugCurrentStatsClient();
        }
        if (typeof jsDebugCurrentStatsStreamEvidence !== 'function') return null;
        const evidence = jsDebugCurrentStatsStreamEvidence();
        const sentinel = window.__yolomuxStatsHiddenSentinel;
        return {...evidence, sampledAtMs: Date.now(), everVisible: sentinel?.everVisible ?? null};
    """, start)
    return validate_hidden_stats_stream_evidence(value)


def wait_for_hidden_stats_stream(driver: Any) -> dict[str, Any]:
    evidence = WebDriverWait(driver, STATS_READINESS_SECONDS, poll_frequency=0.1).until(
        lambda current: sample_hidden_stats_stream(current, start=True) or False
    )
    if not isinstance(evidence, dict):
        raise AssertionError("hidden YO!stats stream did not become ready")
    return evidence


def classify_hidden_stats_stream(current: dict[str, Any] | None, previous: Mapping[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    if current is None:
        return current, ["hidden YO!stats stream is unavailable, visible, stale, or unready"]
    prior_stream = previous.get("stream") if isinstance(previous.get("stream"), Mapping) else {}
    stream = current["stream"]
    integrity: list[str] = []
    for field in ("streamEpoch", "deliverySequence", "acceptedDeltaSequence", "sourceGeneration", "cacheGeneration", "deltaRevision"):
        if stream[field] < int(prior_stream.get(field) or 0):
            integrity.append(f"hidden YO!stats {field} regressed")
    if (stream["rangeSeconds"], stream["resolutionSeconds"]) != (prior_stream.get("rangeSeconds"), prior_stream.get("resolutionSeconds")):
        integrity.append("hidden YO!stats selection changed")
    coherent_fields = ("acceptedDeltaSequence", "cacheGeneration", "sourceGeneration", "deltaRevision")
    advanced = [stream[field] > int(prior_stream.get(field) or 0) for field in coherent_fields]
    if any(advanced) and not all(advanced):
        integrity.append("hidden YO!stats delta, cache, source generation, and revision did not advance coherently")
    previous_painted = str(previous.get("paintedGenerationKey") or "")
    current_painted = str(current.get("paintedGenerationKey") or "")
    if current_painted and current_painted != previous_painted:
        integrity.append("hidden YO!stats painted generation changed while the panel stayed hidden")
    return current, integrity


def settle_authenticated_page(
    driver: Any,
    *,
    expected_url: str,
    expected_identity: ListenerIdentity,
    pre_page_server: Mapping[str, Any],
    initial_stats: Mapping[str, Any],
    expected_journey_id: str,
    drift_record: dict[str, Any],
    sleep_fn: Any = time.sleep,
    monotonic_fn: Any = time.monotonic,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, Mapping[str, Any], dict[str, Any]]:
    """Measure a clean hidden-stats settle before establishing the evidence baseline."""

    started = monotonic_fn()
    previous = server_ring_cursor(pre_page_server)
    previous_stats: Mapping[str, Any] = initial_stats
    record: dict[str, Any] = {
        "requested_seconds": SETTLE_SECONDS,
        "started_pt": pacific_wall_time(),
        "samples": [],
    }
    baseline: dict[str, Any] | None = None
    while True:
        if listener_identity(urlsplit(expected_url).port or 0) != expected_identity:
            raise AssertionError("listener identity changed during authenticated settle")
        page_identity = assert_page_identity(driver, expected_url, expected_journey_id)
        assert_stats_hidden(driver)
        evidence, previous = classify_incremental_evidence(sample_evidence(driver), previous)
        current_stats, stats_integrity = classify_hidden_stats_stream(sample_hidden_stats_stream(driver), previous_stats)
        if current_stats is not None:
            evidence["statsStreamEvidence"] = current_stats
            previous_stats = current_stats
        evidence.setdefault("integrityFailures", []).extend(stats_integrity)
        elapsed = monotonic_fn() - started
        evidence["settle_elapsed_seconds"] = elapsed
        note_page_identity_drift(drift_record, page_identity, phase="settle", elapsed=elapsed)
        record["samples"].append(evidence)
        if evidence_failed(evidence):
            record.update({"ended_pt": pacific_wall_time(), "elapsed_seconds": elapsed, "status": "failed"})
            return None, previous, previous_stats, record
        if elapsed >= SETTLE_SECONDS:
            baseline = evidence
            break
        sleep_fn(min(SAMPLE_SECONDS, SETTLE_SECONDS - elapsed))
    record.update({"ended_pt": pacific_wall_time(), "elapsed_seconds": baseline["settle_elapsed_seconds"], "status": "clean"})
    return baseline, previous, previous_stats, record


def validate_browser_receipt_projection_prefix(
    previous: Mapping[str, Any],
    current: Mapping[str, Any],
) -> None:
    previous_by_key = {receipt["key"]: receipt for receipt in previous["receipts"]}
    current_by_key = {receipt["key"]: receipt for receipt in current["receipts"]}
    if any(current_by_key.get(key) != receipt for key, receipt in previous_by_key.items()):
        raise AssertionError("browser receipt projection lost or changed its prior prefix")


BROWSER_EVENT_RING_INTACT = "intact"
BROWSER_EVENT_RING_EVICTED = "ring_eviction"


def browser_event_ring_extension(
    current_ids: Sequence[Any],
    *,
    phase: str,
    prior_cursor: int,
    prior_ids: Sequence[Any] | None = None,
) -> dict[str, Any]:
    """Verify a bounded browser diagnostic ring only evicted already-observed events.

    ``jsDebugEvents`` is a ring capped at ``jsDebugEventLimit`` entries, so a later
    snapshot is not a raw prefix-preserving extension of an earlier one: once the ring
    is full, every appended event evicts the oldest retained event. Evicting an event
    the caller already read loses nothing, so it is returned as a typed outcome instead
    of being discarded or treated as loss. Evicting an event that was never observed by
    the caller is real evidence loss and raises, as does any gap or reordering.

    ``prior_ids`` is the caller's full observed window when it has one; when only a
    cursor is available (the uploader fence records a cursor, not a list) the reported
    ``evicted`` count is the number of events the ring dropped before this snapshot.
    """
    if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in current_ids):
        raise AssertionError(f"browser {phase} event cursor is malformed")
    ids = [int(value) for value in current_ids]
    if ids and ids != list(range(ids[0], ids[-1] + 1)):
        raise AssertionError(f"browser {phase} event cursor is malformed")
    first_id = ids[0] if ids else 0
    last_id = ids[-1] if ids else 0
    if last_id < prior_cursor:
        raise AssertionError(f"browser {phase} event cursor moved backwards")
    appended = [event_id for event_id in ids if event_id > prior_cursor]
    if appended != list(range(prior_cursor + 1, last_id + 1)):
        raise AssertionError(f"browser {phase} event cursor evicted unobserved events")
    evicted = 0
    if prior_ids is not None:
        observed = [int(value) for value in prior_ids]
        retained = [event_id for event_id in observed if event_id >= first_id]
        if retained != [event_id for event_id in ids if event_id <= prior_cursor]:
            raise AssertionError(f"browser {phase} event cursor lost its observed window")
        evicted = len(observed) - len(retained)
    elif prior_cursor > 0:
        evicted = max(0, first_id - 1)
    return {
        "phase": phase,
        "reason": BROWSER_EVENT_RING_EVICTED if evicted else BROWSER_EVENT_RING_INTACT,
        "evicted": evicted,
        "appended": len(appended),
        "priorCursor": prior_cursor,
        "priorFirstId": int(prior_ids[0]) if prior_ids else 0,
        "retainedFirstId": first_id,
        "retainedLastId": last_id,
    }


def validate_browser_event_ring_outcome(outcome: Any, *, phase: str) -> dict[str, Any]:
    """Validate one machine-readable ring-extension outcome recorded in an artifact.

    Field presence, non-negative counters and reason-versus-eviction leave the counters free of the
    windows they describe: an `intact` outcome could claim a retained range of `1..2` after a prior
    cursor of `50`, or `appended: 999` for a range ending at `51`. Neither is reachable — the ring is
    append-only and its ids are contiguous — so the recorded window is replayed through
    ``browser_event_ring_extension``, the producer itself, and the recorded counters must equal what
    that owner derives. One owner means the two can never drift apart again.
    """
    fields = {"phase", "reason", "evicted", "appended", "priorCursor", "priorFirstId", "retainedFirstId", "retainedLastId"}
    if not isinstance(outcome, Mapping) or set(outcome) != fields or outcome.get("phase") != phase:
        raise AssertionError(f"browser {phase} event ring outcome is malformed")
    if outcome.get("reason") not in {BROWSER_EVENT_RING_INTACT, BROWSER_EVENT_RING_EVICTED}:
        raise AssertionError(f"browser {phase} event ring outcome is malformed")
    counters = ("evicted", "appended", "priorCursor", "priorFirstId", "retainedFirstId", "retainedLastId")
    if any(isinstance(outcome[name], bool) or not isinstance(outcome[name], int) or outcome[name] < 0 for name in counters):
        raise AssertionError(f"browser {phase} event ring outcome is malformed")
    expected_reason = BROWSER_EVENT_RING_EVICTED if outcome["evicted"] else BROWSER_EVENT_RING_INTACT
    if outcome["reason"] != expected_reason:
        raise AssertionError(f"browser {phase} event ring outcome is malformed")
    retained_first = outcome["retainedFirstId"]
    retained_last = outcome["retainedLastId"]
    prior_first = outcome["priorFirstId"]
    prior_cursor = outcome["priorCursor"]
    # Guard the window ordering before replaying it: an inverted range would rebuild as an empty one
    # and be graded against the wrong producer branch instead of being rejected.
    if (retained_first == 0) != (retained_last == 0) or retained_first > retained_last:
        raise AssertionError(f"browser {phase} event ring outcome is malformed: retained window {retained_first}..{retained_last} is not a range")
    if prior_first > prior_cursor:
        raise AssertionError(f"browser {phase} event ring outcome is malformed: prior window {prior_first}..{prior_cursor} is not a range")
    try:
        rebuilt = browser_event_ring_extension(
            list(range(retained_first, retained_last + 1)) if retained_first else [],
            phase=phase,
            prior_cursor=prior_cursor,
            # `priorFirstId` is zero exactly when the producer had a cursor and no window
            # (`finalize_live_browser_soak` records a cursor for the uploader fence), so the prior
            # window is reconstructed only when the outcome states one.
            prior_ids=list(range(prior_first, prior_cursor + 1)) if prior_first else None,
        )
    except AssertionError as error:
        raise AssertionError(f"browser {phase} event ring outcome is malformed: {error}") from error
    if rebuilt != dict(outcome):
        raise AssertionError(f"browser {phase} event ring outcome is malformed: counters disagree with the retained range {retained_first}..{retained_last} after cursor {prior_cursor}")
    return rebuilt


RETIREMENT_DELTA_QUIET = "quiet"
RETIREMENT_DELTA_BENIGN = "benign_activity"
RETIREMENT_DELTA_RECORDED_FAILURES = "recorded_failures"
RETIREMENT_DELTA_EVICTED_FAILURES = "evicted_failures"
RETIREMENT_DELTA_MUTATED_EVENTS = "mutated_events"
RETIREMENT_DELTA_ADDED_RECEIPTS = "added_receipts"
RETIREMENT_DELTA_CLEAN_REASONS = frozenset({RETIREMENT_DELTA_QUIET, RETIREMENT_DELTA_BENIGN})


def classify_browser_retirement_delta(
    *,
    atomic_events: Sequence[Mapping[str, Any]],
    retired_events: Sequence[Mapping[str, Any]],
    atomic_failures: Sequence[Mapping[str, Any]],
    retired_failures: Sequence[Mapping[str, Any]],
    atomic_projection: Mapping[str, Any] | None,
    retired_projection: Mapping[str, Any],
    prior_cursor: int,
    evicted_events: int,
) -> dict[str, Any]:
    """Separate signal-carrying retirement activity from bounded-ring churn.

    A live application keeps recording diagnostics while it navigates away, so "the
    diagnostics changed" is not by itself evidence of a defect: the browser appends
    benign frames and the capped ring evicts equally benign already-observed ones. This
    classifies that activity instead of quiescing the application to avoid it. Fatal is
    exactly what carries signal: a newly recorded warning/error, an already-observed
    failure the ring dropped, an already-observed event whose content changed, or any
    newly created durable receipt.

    Any added receipt key is signal because the producer only ever creates one for a
    release-blocking event (``queueJsDebugCurrentObservation``: the journal entry is
    written under ``if (releaseBlocking)`` and starts at ``pending``). Status changes
    happen by mutating an existing key, which ``validate_browser_receipt_projection_prefix``
    already rejects, so key creation is the only projection motion reaching this
    classifier and there is no benign class of it to admit.

    Failure classification is delegated to ``browser_failures_from_snapshot``, the same
    owner that fills ``browserLocalFailures``, so the two cannot diverge.
    """
    appended_events = [dict(event) for event in retired_events if _event_cursor(event) > prior_cursor]
    appended_failures = [dict(event) for event in retired_failures if _event_cursor(event) > prior_cursor]
    recorded_failures = browser_failures_from_snapshot(appended_failures)
    retained_by_id = {event.get("id"): event for event in retired_events}
    evicted_failures = [dict(event) for event in atomic_failures if event.get("id") not in retained_by_id]
    mutated_events = [
        dict(event) for event in atomic_events
        if event.get("id") in retained_by_id and _event_content_changed(event, retained_by_id[event["id"]])
    ]
    observed_keys = {receipt["key"] for receipt in (atomic_projection or {}).get("receipts", [])}
    added_receipts = [dict(receipt) for receipt in retired_projection["receipts"] if receipt["key"] not in observed_keys]
    blocking_receipts = [
        receipt for receipt in added_receipts
        if receipt.get("status") != "accepted" or receipt.get("globalBlocker") is True
    ]
    integrity: list[str] = []
    reasons: list[str] = []
    if recorded_failures:
        reasons.append(RETIREMENT_DELTA_RECORDED_FAILURES)
        integrity.append(f"browser recorded failing diagnostics during retirement: {len(recorded_failures)}")
    if evicted_failures:
        reasons.append(RETIREMENT_DELTA_EVICTED_FAILURES)
        integrity.append(f"browser retirement evicted observed failing diagnostics: {len(evicted_failures)}")
    if mutated_events:
        reasons.append(RETIREMENT_DELTA_MUTATED_EVENTS)
        integrity.append(f"browser retirement mutated observed diagnostics: {len(mutated_events)}")
    if added_receipts:
        reasons.append(RETIREMENT_DELTA_ADDED_RECEIPTS)
        integrity.append(f"browser retirement added durable receipts: {len(added_receipts)} ({len(blocking_receipts)} blocking)")
    if reasons:
        reason = reasons[0]
    elif appended_events or evicted_events:
        reason = RETIREMENT_DELTA_BENIGN
    else:
        reason = RETIREMENT_DELTA_QUIET
    return {
        "reason": reason,
        "appendedEvents": len(appended_events),
        "evictedEvents": evicted_events,
        "mutatedEvents": len(mutated_events),
        "recordedFailures": len(recorded_failures),
        "evictedFailures": len(evicted_failures),
        "addedReceipts": len(added_receipts),
        "addedBlockingReceipts": len(blocking_receipts),
        "integrityFailures": integrity,
    }


def _comparable_event(event: Mapping[str, Any]) -> dict[str, Any]:
    """Normalise one diagnostic event across the two capture channels before comparing.

    The atomic snapshot returns through WebDriver's ``execute_script`` marshalling, which
    materialises ``undefined`` properties as ``null``; the retirement journal goes through
    ``JSON.stringify`` into ``localStorage``, which omits them. The same ``sse`` event
    therefore arrives with different key sets and identical content, so dropping
    null-valued keys is what makes a real content change detectable at all.
    """
    return {key: value for key, value in event.items() if value is not None}


_JS_DEBUG_MAX_SAFE_INTEGER = 2**53 - 1
_JS_DEBUG_PROTOCOL_TOKEN_CHARACTERS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._/-"
)
_JS_DEBUG_ASYNC_ENRICHMENT_FIELDS = frozenset({"responseBytes", "connectionProtocol", "phaseTimings"})
_JS_DEBUG_ASYNC_PHASE_FIELDS = frozenset({
    "queueMs", "connectMs", "tlsMs", "ttfbMs", "downloadMs", "applyRenderMs",
})
_JS_DEBUG_MAX_ASYNC_PHASE_MS = 86_400_000.0


def _js_debug_async_phase_timing(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, Real)
        and math.isfinite(float(value))
        and 0 <= float(value) <= _JS_DEBUG_MAX_ASYNC_PHASE_MS
    )


def js_debug_event_enrichment_matches(expected: Mapping[str, Any], retired: Mapping[str, Any]) -> bool:
    """Accept only the fields the API producer adds after publishing event identity."""
    added_fields = set(retired) - set(expected)
    if not added_fields <= _JS_DEBUG_ASYNC_ENRICHMENT_FIELDS:
        return False
    if added_fields and expected.get("type") != "api":
        return False
    if any(
        field not in retired or retired[field] != value
        for field, value in expected.items()
        if field != "phaseTimings"
    ):
        return False
    if "responseBytes" in added_fields:
        response_bytes = retired["responseBytes"]
        if isinstance(response_bytes, bool) or not isinstance(response_bytes, int) or not 0 <= response_bytes <= _JS_DEBUG_MAX_SAFE_INTEGER:
            return False
    if "connectionProtocol" in added_fields:
        protocol = retired["connectionProtocol"]
        if (
            not isinstance(protocol, str)
            or len(protocol) > 24
            or protocol != protocol.lower()
            or any(character not in _JS_DEBUG_PROTOCOL_TOKEN_CHARACTERS for character in protocol)
        ):
            return False
    expected_phases = expected.get("phaseTimings")
    retired_phases = retired.get("phaseTimings")
    if "phaseTimings" in expected:
        if not isinstance(expected_phases, Mapping) or not isinstance(retired_phases, Mapping):
            return retired_phases == expected_phases
        if any(key not in retired_phases or retired_phases[key] != value for key, value in expected_phases.items()):
            return False
        added_phases = set(retired_phases) - set(expected_phases)
    elif "phaseTimings" in retired:
        if not isinstance(retired_phases, Mapping):
            return False
        added_phases = set(retired_phases)
    else:
        added_phases = set()
    return (
        (not added_phases or expected.get("type") == "api")
        and added_phases <= _JS_DEBUG_ASYNC_PHASE_FIELDS
        and all(_js_debug_async_phase_timing(retired_phases[key]) for key in added_phases)
    )


def _event_content_changed(observed: Mapping[str, Any], retired: Mapping[str, Any]) -> bool:
    """Compare immutable event content while accepting producer-owned API measurements.

    ``recordApiDebugEvent`` publishes the request/result identity first. Resource timing,
    response-body bytes, and the post-paint duration arrive later through the same live event
    reference. They may therefore be absent from the WebDriver snapshot and present in the
    unload journal without rewriting the diagnostic. Existing measurements remain immutable:
    only valid missing fields or phase keys may be added.
    """
    before = _comparable_event(observed)
    after = _comparable_event(retired)
    return not js_debug_event_enrichment_matches(before, after)


def _event_cursor(event: Mapping[str, Any]) -> int:
    value = event.get("id")
    return value if not isinstance(value, bool) and isinstance(value, int) else 0


def validate_browser_retirement_delta(delta: Any) -> dict[str, Any]:
    """Validate one machine-readable retirement-delta outcome recorded in an artifact."""
    fields = {"reason", "appendedEvents", "evictedEvents", "mutatedEvents", "recordedFailures", "evictedFailures", "addedReceipts", "addedBlockingReceipts", "integrityFailures"}
    reasons = RETIREMENT_DELTA_CLEAN_REASONS | {RETIREMENT_DELTA_RECORDED_FAILURES, RETIREMENT_DELTA_EVICTED_FAILURES, RETIREMENT_DELTA_MUTATED_EVENTS, RETIREMENT_DELTA_ADDED_RECEIPTS}
    if not isinstance(delta, Mapping) or set(delta) != fields or delta.get("reason") not in reasons:
        raise AssertionError("browser retirement delta is malformed")
    counters = ("appendedEvents", "evictedEvents", "mutatedEvents", "recordedFailures", "evictedFailures", "addedReceipts", "addedBlockingReceipts")
    if any(isinstance(delta[name], bool) or not isinstance(delta[name], int) or delta[name] < 0 for name in counters):
        raise AssertionError("browser retirement delta is malformed")
    if not isinstance(delta["integrityFailures"], list) or any(not isinstance(item, str) or not item for item in delta["integrityFailures"]):
        raise AssertionError("browser retirement delta is malformed")
    if delta["addedBlockingReceipts"] > delta["addedReceipts"]:
        raise AssertionError("browser retirement delta is malformed")
    signal = delta["recordedFailures"] or delta["evictedFailures"] or delta["mutatedEvents"] or delta["addedReceipts"]
    if bool(signal) != (delta["reason"] not in RETIREMENT_DELTA_CLEAN_REASONS) or bool(signal) != bool(delta["integrityFailures"]):
        raise AssertionError("browser retirement delta is malformed")
    # "quiet" versus "benign activity" only distinguishes clean outcomes; a signal-carrying
    # delta can legitimately report zero appends when the atomic projection was unavailable.
    if delta["reason"] in RETIREMENT_DELTA_CLEAN_REASONS:
        quiet = not (delta["appendedEvents"] or delta["evictedEvents"])
        if quiet != (delta["reason"] == RETIREMENT_DELTA_QUIET):
            raise AssertionError("browser retirement delta is malformed")
    return dict(delta)


def validate_browser_retirement_delta_evidence(
    delta: Mapping[str, Any],
    *,
    retirement: Mapping[str, Any],
    retained_events: Any,
    event_count: Any,
    retained_cursor: Any,
    fence_receipts: int,
    retained_receipts: int,
) -> None:
    """Bind the retirement delta to the event IDs and projections it claims to summarise.

    ``validate_browser_retirement_delta`` proves the delta is internally coherent and the success
    validator cross-checks it against ``eventRing.retirement``, but two counters that agree with each
    other still agree when both are wrong. The underlying evidence is in the artifact:
    ``finalBoundary.evidence.browserEvents`` is the retirement journal's own retained window (its
    ``atomic_events`` are replaced by ``retired_events`` at the end of the retirement bridge), and
    ``uploaderFence.projection`` versus the final projection bounds how many receipts could have been
    created. ``evictedFailures`` has no such anchor — the artifact keeps only the post-retirement
    failure count — so it stays governed by the clean-reason rule rather than a fabricated bound.
    """

    retained_first = retirement["retainedFirstId"]
    expected_ids = list(range(retained_first, retirement["retainedLastId"] + 1)) if retained_first else []
    if not isinstance(retained_events, list):
        raise AssertionError("retained browser events are malformed")
    ids = [event.get("id") if isinstance(event, Mapping) else None for event in retained_events]
    if ids != expected_ids:
        raise AssertionError("retained browser event ids do not match the retirement ring window")
    if isinstance(event_count, bool) or event_count != len(expected_ids):
        raise AssertionError("atomic snapshot event count does not match the retained event window")
    if retained_cursor != (expected_ids[-1] if expected_ids else 0):
        raise AssertionError("final browser cursor does not match the retained event window")
    appended = [event_id for event_id in expected_ids if event_id > retirement["priorCursor"]]
    if delta["appendedEvents"] != len(appended):
        raise AssertionError("retirement delta appended count does not match the retained event ids")
    if delta["mutatedEvents"] > len(expected_ids) - len(appended):
        raise AssertionError("retirement delta mutated more events than the retained window carried over")
    if delta["recordedFailures"] > len(appended):
        raise AssertionError("retirement delta recorded more failures than it appended events")
    if delta["addedReceipts"] > retained_receipts - fence_receipts:
        raise AssertionError("retirement delta added more receipts than its fenced projection grew by")


def fence_browser_uploader(driver: Any) -> dict[str, Any]:
    """Drive the browser's observation uploader to one quiescent A/B/C fence.

    Both acceptance phases need the same fence before they read evidence: the retirement boundary
    fences before its atomic snapshot, and the short negative probe fences before it attributes the
    injected Error. One owner keeps them from drifting apart.
    """

    fence = driver.execute_async_script("""
        const done = arguments[arguments.length - 1];
        const snapshot = () => ({
          cursor: typeof jsDebugEvents !== 'undefined' && Array.isArray(jsDebugEvents) && jsDebugEvents.length ? jsDebugEvents.at(-1)?.id : 0,
          projection: typeof jsDebugCurrentObservationReceiptProjection === 'function' ? jsDebugCurrentObservationReceiptProjection() : null,
        });
        (async () => {
          let a = snapshot();
          for (let completions = 0; completions < 256; completions += 1) {
            if (typeof window.flushJsDebugCurrentObservations !== 'function') throw new Error('observation uploader is unavailable');
            await window.flushJsDebugCurrentObservations();
            const b = snapshot();
            await Promise.resolve();
            const c = snapshot();
            const stable = JSON.stringify(a) === JSON.stringify(b) && JSON.stringify(b) === JSON.stringify(c);
            if (stable && c.projection?.barrier?.quiescent === true) {
              done({a, b, c, completions: completions + 1});
              return;
            }
            a = c;
          }
          done({exhausted: true, a});
        })().catch(error => done({error: String(error)}));
    """)
    if not isinstance(fence, Mapping) or fence.get("exhausted") or fence.get("error") or not all(isinstance(fence.get(key), Mapping) for key in ("a", "b", "c")):
        raise AssertionError("browser uploader fence did not become quiescent")
    projections = [browser_diagnostic_receipts.validate_browser_receipt_projection(fence[key].get("projection")) for key in ("a", "b", "c")]
    cursors = [fence[key].get("cursor") for key in ("a", "b", "c")]
    if any(isinstance(cursor, bool) or not isinstance(cursor, int) or cursor < 0 for cursor in cursors) or len(set(cursors)) != 1 or projections[0] != projections[1] or projections[1] != projections[2] or projections[2]["barrier"]["quiescent"] is not True:
        raise AssertionError("browser uploader fence changed across A/B/C")
    return {"cursor": cursors[2], "projection": projections[2], "completions": fence.get("completions")}


def finalize_live_browser_soak(
    driver: Any,
    *,
    server_reader: Any,
    expected_url: str,
    previous: Mapping[str, Any],
    previous_stats: Mapping[str, Any],
    baseline_projection: Mapping[str, Any],
    negative_handle: Mapping[str, Any] | None,
    expected_journey_id: str | None = None,
) -> dict[str, Any]:
    phase_errors = (AssertionError, OSError, RuntimeError, TypeError, ValueError, KeyError, WebDriverException)
    boundary: dict[str, Any] = {
        "status": "failed",
        "phaseFailures": [],
        "serverBefore": None,
        "serverBeforeTransition": None,
        "chromeBeforeRetirement": None,
        "uploaderFence": None,
        "eventRing": {"atomic": None, "retirement": None, "delta": None},
        "atomicSnapshot": None,
        "blankReadiness": None,
        "chromeAfterRetirement": None,
        "serverAfter": None,
        "serverAfterTransition": None,
        "evidence": None,
    }

    def fail(phase: str, error: BaseException) -> None:
        boundary["phaseFailures"].append({
            "phase": phase,
            "terminal": type(error).__name__,
            "message": redact_text(str(error)),
        })

    validated_previous: dict[str, Any] | None = None
    validated_baseline_projection: dict[str, Any] | None = None
    if not isinstance(expected_url, str) or urlsplit(expected_url).path != "/":
        fail("inputs", AssertionError("expected finalizer URL is malformed"))
    try:
        required_previous = {"js", "server_epoch", "server_sequence", "server_log_ids", "server_log_records", "server_capacity", "drop_count", "server_dropped_by_level"}
        if not required_previous.issubset(previous):
            raise AssertionError("incoming final browser/server cursors are incomplete")
        validated_previous = dict(previous)
        validate_server_ring_transition(
            previous,
            {
                "epoch": previous["server_epoch"],
                "sequence": previous["server_sequence"],
                "capacity": previous["server_capacity"],
                "ids": list(previous["server_log_ids"]),
                "dropped": {"count": previous["drop_count"], "by_level": dict(previous["server_dropped_by_level"])},
                "logs": [dict(entry) for entry in previous["server_log_records"]],
            },
        )
        validated_baseline_projection = browser_diagnostic_receipts.validate_browser_receipt_projection(baseline_projection)
        if not isinstance(previous_stats.get("stream"), Mapping):
            raise AssertionError("incoming final YO!stats cursor is malformed")
    except phase_errors as error:
        fail("inputs", error)

    before_new_logs: list[dict[str, Any]] = []
    try:
        boundary["serverBefore"] = server_ring_record(server_reader())
        if validated_previous is None:
            raise AssertionError("server-before transition has no validated incoming cursor")
        before_new_logs = validate_server_ring_transition(validated_previous, boundary["serverBefore"])
        boundary["serverBeforeTransition"] = {"newLogs": before_new_logs}
    except phase_errors as error:
        fail("serverBefore", error)

    try:
        boundary["chromeBeforeRetirement"] = chrome_failure_entries(driver.get_log("browser"))
    except phase_errors as error:
        fail("chromeBeforeRetirement", error)

    fence_cursor: int | None = None
    fence_projection: dict[str, Any] | None = None
    try:
        boundary["uploaderFence"] = fence_browser_uploader(driver)
        fence_cursor = boundary["uploaderFence"]["cursor"]
        fence_projection = boundary["uploaderFence"]["projection"]
    except phase_errors as error:
        fail("uploaderFence", error)

    atomic_events: list[dict[str, Any]] = []
    atomic_failures: list[dict[str, Any]] = []
    atomic_projection: dict[str, Any] | None = None
    current_stats: dict[str, Any] | None = None
    stats_integrity: list[str] = []
    retirement_integrity: list[str] = []
    atomic_call_completed = False
    retirement_marker = hashlib.sha256(os.urandom(32)).hexdigest()
    retirement_storage_key = f"__yolomux_retirement_{retirement_marker}"
    expected = urlsplit(expected_url)
    expected_origin = f"{expected.scheme}://{expected.hostname}:{expected.port}"
    retirement_storage_id = {"securityOrigin": expected_origin, "isLocalStorage": True}
    retirement_snapshot: Mapping[str, Any] | None = None
    try:
        retirement = driver.execute_script("""
            const retirementMarker = arguments[0];
            const retirementStorageKey = arguments[1];
            const diagnosticSnapshot = () => ({
              events: typeof jsDebugEvents !== 'undefined' && Array.isArray(jsDebugEvents) ? jsDebugEvents.map(event => ({...event})) : null,
              failures: typeof window.jsDebugFailureEvents === 'function' ? window.jsDebugFailureEvents().map(event => ({...event})) : null,
              projection: typeof window.jsDebugCurrentObservationReceiptProjection === 'function' ? window.jsDebugCurrentObservationReceiptProjection() : null,
            });
            const diagnostics = diagnosticSnapshot();
            const {events, failures, projection} = diagnostics;
            const stats = typeof window.jsDebugCurrentStatsStreamEvidence === 'function'
              ? {...window.jsDebugCurrentStatsStreamEvidence(), sampledAtMs: Date.now(), everVisible: window.__yolomuxStatsHiddenSentinel?.everVisible ?? null}
              : null;
            const gate = window.__yolomuxBrowserJourneyGate;
            const gateReachable = Boolean(gate && typeof gate === 'object' && Array.isArray(gate.visitedSurfaces)
              && Array.isArray(gate.consumedServerLogIds) && typeof gate.observe === 'function' && gate.observer);
            if (gateReachable) gate.observe();
            const snapshot = {
              events, failures, projection, stats,
              journey: {
                id: typeof reloadClientJourneyId === 'undefined' ? '' : String(reloadClientJourneyId || ''),
                reachable: gateReachable,
                visitedSurfaces: gateReachable ? [...gate.visitedSurfaces] : [],
              },
              hiddenSentinel: window.__yolomuxStatsHiddenSentinel ? {...window.__yolomuxStatsHiddenSentinel} : null,
              panelVisible: typeof window.jsDebugStatsPanelVisible === 'function' ? window.jsDebugStatsPanelVisible() : null,
              page: {visibility: document.visibilityState, origin: location.origin, href: location.href},
            };
            const capture = phase => localStorage.setItem(retirementStorageKey, JSON.stringify({
              marker: retirementMarker,
              phase,
              snapshot: diagnosticSnapshot(),
            }));
            const installUnloadTail = () => window.addEventListener('unload', () => capture('unload'), {once: true});
            capture('armed');
            window.addEventListener('beforeunload', () => {
              capture('beforeunload');
              installUnloadTail();
            }, {once: true});
            window.addEventListener('pagehide', () => {
              capture('pagehide');
              installUnloadTail();
            }, {once: true});
            installUnloadTail();
            location.replace('about:blank');
            return snapshot;
        """, retirement_marker, retirement_storage_key)
        atomic_call_completed = True
        if (
            not isinstance(retirement, Mapping)
            or set(retirement) != {"events", "failures", "projection", "stats", "journey", "hiddenSentinel", "panelVisible", "page"}
        ):
            raise AssertionError("browser atomic retirement snapshot is malformed")
        atomic = retirement
        if not isinstance(atomic, Mapping) or set(atomic) != {"events", "failures", "projection", "stats", "journey", "hiddenSentinel", "panelVisible", "page"} or not isinstance(atomic.get("events"), list) or not isinstance(atomic.get("failures"), list) or any(not isinstance(entry, Mapping) for entry in [*atomic["events"], *atomic["failures"]]):
            raise AssertionError("atomic browser retirement snapshot is malformed")
        atomic_events = [dict(entry) for entry in atomic["events"]]
        atomic_failures = [dict(entry) for entry in atomic["failures"]]
        atomic_projection = browser_diagnostic_receipts.validate_browser_receipt_projection(atomic.get("projection"))
        ids = [entry.get("id") for entry in atomic_events]
        if fence_cursor is None or fence_projection is None:
            raise AssertionError("browser uploader fence evidence is unavailable")
        boundary["eventRing"]["atomic"] = browser_event_ring_extension(ids, phase="atomic", prior_cursor=fence_cursor)
        # The fence runs microseconds before this snapshot, so the fenced event must still
        # be resident: unlike the retirement bridge, no capacity eviction is expected here.
        if fence_cursor > 0 and fence_cursor not in set(ids):
            raise AssertionError("browser atomic event cursor lost its uploader-fence prefix")
        validate_browser_receipt_projection_prefix(fence_projection, atomic_projection)
        hidden = atomic.get("hiddenSentinel")
        expected_probe = negative_handle is not None
        if (
            not isinstance(hidden, Mapping)
            or set(hidden) != {"installed", "everVisible", "checks"}
            or hidden.get("installed") is not True
            or not isinstance(hidden.get("everVisible"), bool)
            or isinstance(hidden.get("checks"), bool)
            or not isinstance(hidden.get("checks"), int)
            or not 1 <= hidden["checks"] <= browser_diagnostic_receipts.JAVASCRIPT_MAX_SAFE_INTEGER
            or not isinstance(atomic.get("panelVisible"), bool)
            or (not expected_probe and (hidden.get("everVisible") is not False or atomic.get("panelVisible") is not False))
        ):
            raise AssertionError("YO!stats hidden sentinel is malformed")
        journey = atomic.get("journey")
        if not isinstance(journey, Mapping) or set(journey) != {"id", "reachable", "visitedSurfaces"} or journey.get("reachable") is not True or not isinstance(journey.get("id"), str) or not 1 <= len(journey["id"]) <= 256 or not isinstance(journey.get("visitedSurfaces"), list) or len(journey["visitedSurfaces"]) > 16 or any(not isinstance(surface, str) or not surface or len(surface) > 64 for surface in journey["visitedSurfaces"]):
            raise AssertionError("atomic journey evidence is malformed")
        page = atomic.get("page")
        if not isinstance(page, Mapping) or set(page) != {"visibility", "origin", "href"}:
            raise AssertionError("atomic page identity is malformed")
        page_identity = classify_page_identity(
            {**page, "journeyId": journey["id"]},
            expected_url=expected_url,
            expected_journey_id=expected_journey_id,
        )
        if page_identity["reasons"]:
            raise AssertionError(f"atomic page identity was substituted: {', '.join(page_identity['reasons'])}")
        if expected_probe:
            current_stats = dict(atomic["stats"]) if isinstance(atomic.get("stats"), Mapping) else None
        else:
            current_stats = validate_hidden_stats_stream_evidence(atomic.get("stats"))
            if current_stats is None:
                raise AssertionError("atomic hidden YO!stats evidence is unready")
            _, stats_integrity = classify_hidden_stats_stream(current_stats, previous_stats)
        boundary["atomicSnapshot"] = {
            "journey": dict(journey),
            "hiddenSentinel": dict(hidden),
            "panelVisible": atomic["panelVisible"],
            "page": dict(page),
            "pageDrift": dict(page_identity["drift"]),
            "stats": current_stats,
            "eventCount": len(atomic_events),
            "failureCount": len(atomic_failures),
        }
    except phase_errors as error:
        fail("atomicSnapshot", error)

    if not atomic_call_completed:
        try:
            driver.get("about:blank")
        except phase_errors as error:
            fail("blankNavigation", error)
    try:
        readiness = WebDriverWait(driver, 5).until(
            lambda current: (
                value if isinstance((value := current.execute_script(
                    "return {href: location.href, readyState: document.readyState};"
                )), Mapping) and value.get("href") == "about:blank" and value.get("readyState") == "complete" else False
            )
        )
        boundary["blankReadiness"] = {"href": readiness["href"], "readyState": readiness["readyState"]}
    except phase_errors as error:
        fail("blankReadiness", error)

    try:
        storage = driver.execute_cdp_cmd("DOMStorage.getDOMStorageItems", {"storageId": retirement_storage_id})
        entries = storage.get("entries") if isinstance(storage, Mapping) else None
        if not isinstance(entries, list) or any(not isinstance(entry, list) or len(entry) != 2 or any(not isinstance(value, str) for value in entry) for entry in entries):
            raise AssertionError("browser retirement storage journal is malformed")
        values = [value for key, value in entries if key == retirement_storage_key]
        if len(values) != 1:
            raise AssertionError("browser retirement storage journal is missing or duplicated")
        journal = json.loads(values[0])
        if (
            not isinstance(journal, Mapping)
            or set(journal) != {"marker", "phase", "snapshot"}
            or journal.get("marker") != retirement_marker
            or journal.get("phase") not in {"beforeunload", "pagehide", "unload"}
            or not isinstance(journal.get("snapshot"), Mapping)
        ):
            raise AssertionError("browser retirement storage journal did not reach a lifecycle capture")
        retirement_snapshot = journal["snapshot"]
        if (
            not isinstance(retirement_snapshot, Mapping)
            or set(retirement_snapshot) != {"events", "failures", "projection"}
            or not isinstance(retirement_snapshot.get("events"), list)
            or not isinstance(retirement_snapshot.get("failures"), list)
            or any(not isinstance(entry, Mapping) for entry in [*retirement_snapshot["events"], *retirement_snapshot["failures"]])
        ):
            raise AssertionError("browser retirement diagnostic snapshot is malformed")
        retired_events = [dict(entry) for entry in retirement_snapshot["events"]]
        retired_failures = [dict(entry) for entry in retirement_snapshot["failures"]]
        retired_projection = browser_diagnostic_receipts.validate_browser_receipt_projection(
            retirement_snapshot.get("projection")
        )
        retired_ids = [entry.get("id") for entry in retired_events]
        atomic_ids = [entry.get("id") for entry in atomic_events]
        boundary["eventRing"]["retirement"] = browser_event_ring_extension(
            retired_ids,
            phase="retirement",
            prior_cursor=int(atomic_ids[-1]) if atomic_ids else 0,
            prior_ids=atomic_ids,
        )
        failure_ids = [entry.get("id") for entry in retired_failures]
        if any(event_id not in set(retired_ids) for event_id in failure_ids):
            raise AssertionError("browser retirement failure is absent from the retained event cursor")
        if atomic_projection is not None:
            validate_browser_receipt_projection_prefix(atomic_projection, retired_projection)
        boundary["eventRing"]["delta"] = classify_browser_retirement_delta(
            atomic_events=atomic_events,
            retired_events=retired_events,
            atomic_failures=atomic_failures,
            retired_failures=retired_failures,
            atomic_projection=atomic_projection,
            retired_projection=retired_projection,
            prior_cursor=int(atomic_ids[-1]) if atomic_ids else 0,
            evicted_events=boundary["eventRing"]["retirement"]["evicted"],
        )
        retirement_integrity.extend(boundary["eventRing"]["delta"]["integrityFailures"])
        atomic_events = retired_events
        atomic_failures = retired_failures
        atomic_projection = retired_projection
        if boundary["atomicSnapshot"] is not None:
            boundary["atomicSnapshot"]["eventCount"] = len(atomic_events)
            boundary["atomicSnapshot"]["failureCount"] = len(atomic_failures)
    except phase_errors as error:
        fail("retirementBridge", error)
    finally:
        try:
            driver.execute_cdp_cmd(
                "DOMStorage.removeDOMStorageItem",
                {"storageId": retirement_storage_id, "key": retirement_storage_key},
            )
        except phase_errors as error:
            fail("retirementBridgeCleanup", error)

    try:
        boundary["chromeAfterRetirement"] = chrome_failure_entries(driver.get_log("browser"))
    except phase_errors as error:
        fail("chromeAfterRetirement", error)

    after_new_logs: list[dict[str, Any]] = []
    try:
        boundary["serverAfter"] = server_ring_record(server_reader())
        if boundary["serverBefore"] is None:
            raise AssertionError("server-after transition has no server-before record")
        after_new_logs = validate_server_ring_transition(boundary["serverBefore"], boundary["serverAfter"])
        boundary["serverAfterTransition"] = {"newLogs": after_new_logs}
    except phase_errors as error:
        fail("serverAfter", error)

    server_after = boundary["serverAfter"]
    cursor_record = server_after if isinstance(server_after, Mapping) else boundary["serverBefore"]
    browser_local = browser_failures_from_snapshot(atomic_failures) if atomic_failures else []
    server_failures = [dict(entry) for entry in [*before_new_logs, *after_new_logs] if str(entry.get("level") or "").lower() in {"warning", "error"}]
    chrome_failures = [
        *list(boundary["chromeBeforeRetirement"] or []),
        *list(boundary["chromeAfterRetirement"] or []),
    ]
    evidence = {
        "at_pt": pacific_wall_time(),
        "browserEvents": atomic_events,
        "browserLocalFailures": browser_local,
        "browserReceiptBarrier": atomic_projection["barrier"] if atomic_projection is not None else None,
        "browserReceiptProjection": atomic_projection,
        "statsStreamEvidence": current_stats,
        "serverLogErrors": server_failures,
        "browserLogFailures": chrome_failures,
        "serverLogDropped": dict(cursor_record["dropped"]) if isinstance(cursor_record, Mapping) else {"count": -1, "by_level": {}},
        "integrityFailures": [*stats_integrity, *retirement_integrity, *[f"{item['phase']}: {item['message']}" for item in boundary["phaseFailures"]]],
        "cursors": {
            "js": atomic_events[-1]["id"] if atomic_events else 0,
            "server_epoch": cursor_record["epoch"] if isinstance(cursor_record, Mapping) else "",
            "server_sequence": cursor_record["sequence"] if isinstance(cursor_record, Mapping) else -1,
            "server_log_ids": list(cursor_record["ids"]) if isinstance(cursor_record, Mapping) else [],
            "server_log_records": list(cursor_record["logs"]) if isinstance(cursor_record, Mapping) else [],
            "server_capacity": cursor_record["capacity"] if isinstance(cursor_record, Mapping) else 0,
            "server_retained_count": len(cursor_record["ids"]) if isinstance(cursor_record, Mapping) else 0,
            "server_dropped_by_level": dict(cursor_record["dropped"]["by_level"]) if isinstance(cursor_record, Mapping) else {},
        },
    }
    if validated_previous is not None and isinstance(cursor_record, Mapping) and atomic_projection is not None:
        try:
            evidence, _ = classify_incremental_evidence(evidence, validated_previous)
            evidence["integrityFailures"].extend(
                f"{item['phase']}: {item['message']}" for item in boundary["phaseFailures"]
                if f"{item['phase']}: {item['message']}" not in evidence["integrityFailures"]
            )
        except phase_errors as error:
            fail("classification", error)
            evidence["integrityFailures"].append(f"classification: {redact_text(str(error))}")
    if negative_handle is not None:
        require_negative_acknowledgement(evidence, negative_handle, validated_baseline_projection or {})
    boundary["evidence"] = evidence
    boundary["status"] = "clean" if not boundary["phaseFailures"] and not evidence_failed(evidence) else "failed"
    return boundary


def sample_evidence(driver: Any) -> dict[str, Any]:
    result = driver.execute_async_script(r"""
        const done = arguments[arguments.length - 1];
        const snapshot = () => ({
          events: typeof jsDebugEvents !== 'undefined' && Array.isArray(jsDebugEvents) ? [...jsDebugEvents] : null,
          failures: typeof window.jsDebugFailureEvents === 'function' ? window.jsDebugFailureEvents() : null,
          receiptBarrier: typeof window.jsDebugCurrentObservationReceiptBarrier === 'function'
            ? window.jsDebugCurrentObservationReceiptBarrier() : null,
          receiptProjection: typeof window.jsDebugCurrentObservationReceiptProjection === 'function'
            ? window.jsDebugCurrentObservationReceiptProjection() : null,
        });
        const cursor = value => Array.isArray(value?.events) && value.events.length
          ? value.events[value.events.length - 1]?.id : 0;
        (async () => {
          const before = snapshot();
          let response;
          try {
            response = await fetch('/api/logs', {cache: 'no-store', credentials: 'same-origin'});
          } catch (error) {
            done({...snapshot(), beforeCursor: cursor(before), logsStatus: 0, parseError: String(error)});
            return;
          }
          let payload = null;
          try {
            payload = JSON.parse(await response.text());
          } catch (error) {
            done({...snapshot(), beforeCursor: cursor(before), logsStatus: response.status, parseError: String(error)});
            return;
          }
          let latest = snapshot();
          for (let attempt = 0; attempt < 4; attempt += 1) {
            if (typeof window.flushJsDebugCurrentObservations === 'function') {
              await window.flushJsDebugCurrentObservations();
            }
            latest = snapshot();
            await Promise.resolve();
            const fenced = snapshot();
            const stable = cursor(latest) === cursor(fenced)
              && JSON.stringify(latest.receiptBarrier) === JSON.stringify(fenced.receiptBarrier);
            latest = fenced;
            if (stable) {
              done({...latest, beforeCursor: cursor(before), logsStatus: response.status, payload});
              return;
            }
          }
          done({...latest, beforeCursor: cursor(before), logsStatus: response.status, payload,
            stabilityError: 'browser evidence changed across the post-log receipt fence'});
        })().catch(error => done({...snapshot(), logsStatus: 0, parseError: String(error)}));
    """)
    raw_console_entries = driver.get_log("browser")
    if not isinstance(raw_console_entries, list) or any(not isinstance(entry, Mapping) for entry in raw_console_entries):
        raise AssertionError("browser Chrome log evidence is malformed")
    console_entries = tuple(redact_log_entry(dict(entry)) for entry in raw_console_entries)
    if not isinstance(result, Mapping) or not isinstance(result.get("events"), list) or not isinstance(result.get("failures"), list):
        shape = {
            "type": type(result).__name__,
            "keys": sorted(str(key) for key in result) if isinstance(result, Mapping) else [],
            "eventsType": type(result.get("events")).__name__ if isinstance(result, Mapping) else None,
            "failuresType": type(result.get("failures")).__name__ if isinstance(result, Mapping) else None,
            "parseError": redact_text(str(result.get("parseError") or "")) if isinstance(result, Mapping) else "",
        }
        raise AssertionError(f"browser JS diagnostics are unavailable or malformed: {json.dumps(shape, sort_keys=True)}")
    if result.get("stabilityError"):
        raise AssertionError(str(result["stabilityError"]))
    if any(not isinstance(entry, Mapping) for entry in result["events"]) or any(not isinstance(entry, Mapping) for entry in result["failures"]):
        raise AssertionError("browser JS diagnostic entry is malformed")
    receipt_barrier = browser_diagnostic_receipts.validate_browser_receipt_barrier(result.get("receiptBarrier"))
    receipt_projection = browser_diagnostic_receipts.validate_browser_receipt_projection(result.get("receiptProjection"))
    if receipt_projection["barrier"] != receipt_barrier:
        raise AssertionError("browser receipt projection disagrees with its barrier")
    payload = result.get("payload")
    if result.get("logsStatus") != 200 or not isinstance(payload, Mapping) or payload.get("ok") is not True or not isinstance(payload.get("logs"), list) or not isinstance(payload.get("dropped"), Mapping):
        raise AssertionError("authenticated /api/logs evidence is unavailable or malformed")
    if any(not isinstance(entry, Mapping) for entry in payload["logs"]):
        raise AssertionError("server log entry is malformed")
    dropped = payload["dropped"]
    capacity = payload.get("capacity")
    if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 1:
        raise AssertionError("server log capacity evidence is malformed")
    if isinstance(dropped.get("count"), bool) or not isinstance(dropped.get("count"), int) or dropped["count"] < 0 or not isinstance(dropped.get("by_level"), Mapping):
        raise AssertionError("server log drop evidence is malformed")
    if any(not isinstance(level, str) or isinstance(count, bool) or not isinstance(count, int) or count < 0 for level, count in dropped["by_level"].items()) or sum(dropped["by_level"].values()) != dropped["count"]:
        raise AssertionError("server log drop-by-level evidence is malformed")
    epoch = payload.get("epoch")
    sequence = payload.get("sequence")
    if not isinstance(epoch, str) or not epoch or isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
        raise AssertionError("server log cursor evidence is malformed")
    local = browser_failures_from_snapshot(result["failures"])
    server = [dict(entry) for entry in payload["logs"] if str(entry.get("level") or "").lower() in {"warning", "error"}]
    chrome = [entry for entry in console_entries if str(entry.get("level") or "").upper() in {"WARNING", "SEVERE"}]
    js_ids = [entry.get("id") for entry in result["events"]]
    if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in js_ids) or (js_ids and js_ids != list(range(js_ids[0], js_ids[-1] + 1))):
        raise AssertionError("browser JS cursor evidence is malformed")
    server_ids = [entry.get("id") for entry in payload["logs"]]
    if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in server_ids) or (server_ids and server_ids != list(range(server_ids[0], server_ids[-1] + 1))):
        raise AssertionError("server log cursor evidence is malformed")
    if len(server_ids) > capacity or sequence != dropped["count"] + len(server_ids) or (server_ids and server_ids[-1] != sequence):
        raise AssertionError("server log cursor accounting is malformed")
    server_records = [redact_log_entry(dict(entry)) for entry in payload["logs"]]
    return {"at_pt": pacific_wall_time(), "browserEvents": [dict(entry) for entry in result["events"]], "browserLocalFailures": local, "browserReceiptBarrier": receipt_barrier, "browserReceiptProjection": receipt_projection, "serverLogErrors": server, "browserLogFailures": chrome, "serverLogDropped": dict(dropped), "eventCount": len(result["events"]), "cursors": {"js": js_ids[-1] if js_ids else 0, "server_epoch": epoch, "server_sequence": sequence, "server_log_ids": server_ids, "server_log_records": server_records, "server_capacity": capacity, "server_retained_count": len(server_ids), "server_dropped_by_level": dict(dropped["by_level"])}}


def server_ring_record(payload: Mapping[str, Any]) -> dict[str, Any]:
    logs = payload.get("logs")
    dropped = payload.get("dropped")
    epoch = payload.get("epoch")
    sequence = payload.get("sequence")
    capacity = payload.get("capacity")
    if payload.get("ok") is not True or not isinstance(logs, list) or any(not isinstance(entry, Mapping) for entry in logs) or not isinstance(dropped, Mapping):
        raise AssertionError("server log ring is malformed")
    if not isinstance(epoch, str) or not epoch or isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0 or isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 1:
        raise AssertionError("server log ring cursor is malformed")
    count = dropped.get("count")
    levels = dropped.get("by_level")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0 or not isinstance(levels, Mapping) or any(not isinstance(key, str) or isinstance(value, bool) or not isinstance(value, int) or value < 0 for key, value in levels.items()) or sum(levels.values()) != count:
        raise AssertionError("server log ring drops are malformed")
    ids = [entry.get("id") for entry in logs]
    if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in ids) or ids != list(range(ids[0], ids[-1] + 1)) if ids else False:
        raise AssertionError("server log ring IDs are noncontiguous")
    if len(ids) > capacity or sequence != count + len(ids) or (ids and ids[-1] != sequence):
        raise AssertionError("server log ring accounting is malformed")
    records = [redact_log_entry(dict(entry)) for entry in logs]
    record_digests = [
        {
            "id": entry["id"],
            "sha256": hashlib.sha256(
                json.dumps(entry, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
            ).hexdigest(),
        }
        for entry in records
    ]
    return {
        "epoch": epoch,
        "sequence": sequence,
        "capacity": capacity,
        "ids": ids,
        "dropped": {"count": count, "by_level": dict(levels)},
        "logs": records,
        "recordDigests": record_digests,
    }


def validate_server_ring_transition(previous: Mapping[str, Any], current: Mapping[str, Any]) -> list[dict[str, Any]]:
    prior_epoch = previous.get("server_epoch", previous.get("epoch"))
    prior_sequence = int(previous.get("server_sequence", previous.get("sequence", 0)) or 0)
    prior_capacity = previous.get("server_capacity", previous.get("capacity"))
    prior_ids = list(previous.get("server_log_ids", previous.get("ids", [])))
    prior_drop = int(previous.get("drop_count", previous.get("dropped", {}).get("count", 0)) or 0)
    prior_drop_levels = dict(previous.get("server_dropped_by_level", previous.get("dropped", {}).get("by_level", {})))
    prior_records = list(previous.get("server_log_records", previous.get("logs", [])))
    if current["epoch"] != prior_epoch or current["capacity"] != prior_capacity or current["sequence"] < prior_sequence or current["dropped"]["count"] != prior_drop or current["dropped"]["by_level"] != prior_drop_levels:
        raise AssertionError("server log ring identity, capacity, sequence, or drops changed invalidly")
    if prior_ids and prior_ids[-1] not in current["ids"]:
        raise AssertionError("server log ring overlap was lost")
    prior_by_id = {entry.get("id"): dict(entry) for entry in prior_records if isinstance(entry, Mapping)}
    current_by_id = {entry.get("id"): dict(entry) for entry in current["logs"]}
    if set(prior_by_id) != set(prior_ids) or any(current_by_id.get(event_id) != prior_by_id[event_id] for event_id in set(prior_ids) & set(current["ids"])):
        raise AssertionError("server log ring retained record content changed")
    new_logs = [entry for entry in current["logs"] if isinstance(entry.get("id"), int) and entry["id"] > prior_sequence]
    expected = list(range(prior_sequence + 1, current["sequence"] + 1))
    if [entry["id"] for entry in new_logs] != expected:
        raise AssertionError("server log ring transition has an ID gap")
    return new_logs


def chrome_failure_entries(entries: Any) -> list[dict[str, Any]]:
    if not isinstance(entries, list) or any(not isinstance(entry, Mapping) for entry in entries):
        raise AssertionError("Chrome log drain is malformed")
    return [redact_log_entry(dict(entry)) for entry in entries if str(entry.get("level") or "").upper() in {"WARNING", "SEVERE"}]


def browser_failures_from_snapshot(failures: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    local: list[dict[str, Any]] = []
    seen: set[object] = set()
    for event in failures:
        marker = event.get("id") if event.get("id") is not None else json.dumps(dict(event), sort_keys=True)
        if marker in seen:
            continue
        seen.add(marker)
        status = event.get("status") if isinstance(event.get("status"), int) else None
        failed = bool(event.get("error") or event.get("ok") is False or (status is not None and status >= 400))
        local.append({"id": event.get("id"), "level": str(event.get("level") or "error"), "message": str(event.get("message") or event.get("error") or ""), "requestId": str(event.get("requestId") or ""), "source": str(event.get("source") or "browser"), "route": str(event.get("route") or event.get("endpoint") or event.get("url") or ""), "event": str(event.get("eventType") or event.get("event") or event.get("type") or ""), "wallTime": str(event.get("wallTime") or event.get("ts") or ""), "deliveryOutcome": str(event.get("deliveryOutcome") or event.get("delivery") or ("failed" if failed else "unknown")), "status": status})
    return local


def redact_log_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Keep browser diagnostics useful through the one shared redaction owner."""
    redacted = redact_diagnostic_value(entry)
    if not isinstance(redacted, Mapping):
        raise TypeError("shared diagnostic redactor returned a malformed log entry")
    return dict(redacted)


def redact_text(value: str) -> str:
    """Redact one diagnostic string through the shared product owner."""
    redacted = redact_diagnostic_value(value)
    if not isinstance(redacted, str):
        raise TypeError("shared diagnostic redactor returned malformed text")
    return redacted


def terminal_failure(phase: str, error: BaseException) -> dict[str, str]:
    """Return a stable, redacted terminal failure suitable for a mode-0600 artifact."""
    if phase not in {"preflight", "runtime", "cleanup"}:
        raise ValueError(f"unknown terminal failure phase: {phase}")
    return {"phase": phase, "terminal": type(error).__name__, "message": redact_text(str(error))}


def evidence_baseline(evidence: Mapping[str, Any]) -> dict[str, Any]:
    cursors = evidence.get("cursors")
    dropped = evidence.get("serverLogDropped")
    if not isinstance(cursors, Mapping) or not isinstance(dropped, Mapping):
        raise AssertionError("baseline evidence is malformed")
    js = cursors.get("js")
    epoch = cursors.get("server_epoch")
    sequence = cursors.get("server_sequence")
    server_ids = cursors.get("server_log_ids")
    server_records = cursors.get("server_log_records", [])
    server_capacity = cursors.get("server_capacity")
    dropped_by_level = cursors.get("server_dropped_by_level", dropped.get("by_level", {}))
    drop_count = dropped.get("count")
    if isinstance(js, bool) or not isinstance(js, int) or js < 0 or not isinstance(epoch, str) or not epoch or isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0 or not isinstance(server_ids, list) or any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in server_ids) or server_ids != sorted(set(server_ids)) or not isinstance(server_records, list) or any(not isinstance(entry, Mapping) for entry in server_records) or (server_records and [entry.get("id") for entry in server_records] != server_ids) or not isinstance(dropped_by_level, Mapping) or any(not isinstance(level, str) or isinstance(count, bool) or not isinstance(count, int) or count < 0 for level, count in dropped_by_level.items()) or isinstance(drop_count, bool) or not isinstance(drop_count, int) or drop_count < 0:
        raise AssertionError("baseline cursor evidence is malformed")
    return {"js": js, "server_epoch": epoch, "server_sequence": sequence, "server_log_ids": list(server_ids), "server_log_records": [dict(entry) for entry in server_records], "server_capacity": server_capacity, "drop_count": drop_count, "server_dropped_by_level": dict(dropped_by_level)}


def classify_incremental_evidence(evidence: Mapping[str, Any], previous: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    current = evidence_baseline(evidence)
    incoming_integrity = evidence.get("integrityFailures", [])
    if not isinstance(incoming_integrity, list) or any(not isinstance(item, str) or not item for item in incoming_integrity):
        raise AssertionError("incremental integrity evidence is malformed")
    integrity = list(incoming_integrity)
    if current["server_epoch"] != previous["server_epoch"]:
        integrity.append("server log epoch changed")
    if not isinstance(current["drop_count"], int) or current["drop_count"] < previous["drop_count"]:
        integrity.append("server log drop counter is malformed or reset")
    elif current["drop_count"] > previous["drop_count"]:
        integrity.append("server log ring dropped records")
    browser_ids = [entry.get("id") for entry in evidence.get("browserEvents", []) if isinstance(entry, Mapping)]
    if current["js"] < previous["js"]:
        integrity.append("browser JS cursor reset")
    elif current["js"] > previous["js"] and (not browser_ids or browser_ids != list(range(browser_ids[0], browser_ids[-1] + 1))):
        integrity.append("browser JS cursor gap")
    elif current["js"] > previous["js"] and [event_id for event_id in browser_ids if event_id > previous["js"]] != list(range(previous["js"] + 1, current["js"] + 1)):
        integrity.append("browser JS ring eviction or cursor gap")
    previous_server_ids = previous["server_log_ids"]
    current_server_ids = current["server_log_ids"]
    if previous_server_ids and previous_server_ids[-1] not in current_server_ids and current["server_sequence"] >= previous["server_sequence"]:
        integrity.append("server log ring eviction")
    if current["server_sequence"] < previous["server_sequence"]:
        integrity.append("server log sequence reset")
    elif current["server_sequence"] > previous["server_sequence"] and not any(event_id > previous["server_sequence"] for event_id in current_server_ids) and current["drop_count"] == previous["drop_count"]:
        integrity.append("server log sequence advanced without retained or dropped record")
    js_cursor = previous["js"]
    server_cursor = previous_server_ids[-1] if previous_server_ids else 0
    classified = dict(evidence)
    classified["browserLocalFailures"] = [entry for entry in evidence["browserLocalFailures"] if isinstance(entry.get("id"), int) and entry["id"] > js_cursor]
    classified["serverLogErrors"] = [entry for entry in evidence["serverLogErrors"] if isinstance(entry.get("id"), int) and entry["id"] > server_cursor]
    classified["integrityFailures"] = integrity
    return classified, current


def evidence_failed(evidence: Mapping[str, Any]) -> bool:
    receipt = evidence.get("browserReceiptBarrier")
    receipt_required = any(name in evidence for name in ("browserEvents", "browserLocalFailures", "cursors", "browserReceiptBarrier"))
    receipt_failed = False
    if receipt_required:
        try:
            receipt_failed = browser_diagnostic_receipts.validate_browser_receipt_barrier(receipt).get("quiescent") is not True
        except AssertionError:
            receipt_failed = True
    return bool(evidence.get("terminal") or evidence.get("browserLocalFailures") or evidence.get("serverLogErrors") or evidence.get("browserLogFailures") or evidence.get("integrityFailures") or receipt_failed or evidence.get("failure") or evidence.get("additional_failures") or evidence.get("cleanup_failure"))


def record_failure(artifact: dict[str, Any], failure: Mapping[str, Any]) -> None:
    """Keep the first failure as the primary cause and retain independent later failures."""
    if "failure" not in artifact:
        artifact["failure"] = dict(failure)
    else:
        artifact.setdefault("additional_failures", []).append(dict(failure))


def require_negative_acknowledgement(evidence: dict[str, Any], handle: Mapping[str, Any], baseline_projection: Mapping[str, Any]) -> None:
    matching = [
        entry for entry in evidence.get("browserLocalFailures", [])
        if isinstance(entry.get("id"), int)
        and entry.get("level") == "error"
        and entry.get("id") == handle.get("eventId")
        and entry.get("requestId") == handle.get("requestId")
        and entry.get("source") == NEGATIVE_SOURCE
        and entry.get("route") == NEGATIVE_ROUTE
        and entry.get("event") == "api"
        and isinstance(entry.get("wallTime"), str) and bool(entry["wallTime"])
        and entry.get("deliveryOutcome") == "failed"
        and entry.get("status") == 500
    ]
    event = next((entry for entry in evidence.get("browserEvents", []) if isinstance(entry, Mapping) and entry.get("id") == (matching[0].get("id") if len(matching) == 1 else None)), None)
    raw_matches = event is not None and event.get("type") == "api" and event.get("endpoint") == NEGATIVE_ROUTE and event.get("requestId") == handle.get("requestId") and event.get("status") == 500 and event.get("ok") is False
    try:
        projection = browser_diagnostic_receipts.validate_browser_receipt_projection(evidence.get("browserReceiptProjection"))
        barrier = projection["barrier"]
        receipts = [receipt for receipt in projection["receipts"] if receipt.get("key") == handle.get("key")]
        receipt_matches = (
            barrier["quiescent"] is True
            and len(receipts) == 1
            and receipts[0].get("status") == "accepted"
            and all(receipts[0].get(field) == handle.get(field) for field in ("key", "epoch", "eventId", "requestId", "route", "event", "wallTime", "deliveryOutcome", "httpStatus"))
            and receipts[0].get("source") == handle.get("receiptSource")
        )
        baseline_receipts = list(baseline_projection.get("receipts", [])) if isinstance(baseline_projection.get("receipts"), list) else []
        receipt_matches = receipt_matches and not any(receipt.get("key") == handle.get("key") for receipt in baseline_receipts)
    except AssertionError:
        receipt_matches = False
    rendered = handle.get("rendered")
    redaction = handle.get("redaction")
    proof_matches = (
        isinstance(rendered, Mapping)
        and rendered.get("matchingRows") == 1
        and rendered.get("requestId") == handle.get("requestId")
        and rendered.get("source") == NEGATIVE_SOURCE
        and rendered.get("route") == NEGATIVE_ROUTE
        and rendered.get("event") == "api"
        and isinstance(redaction, Mapping)
        and all(redaction.get(channel) is True for channel in ("dom", "clipboard", "retained", "upload", "storage"))
    )
    if len(matching) != 1 or not raw_matches or not receipt_matches or not proof_matches:
        evidence.setdefault("integrityFailures", []).append("negative browser error probe was not retained exactly")


def validate_negative_probe_baseline(value: Any) -> dict[str, Any]:
    """Validate the receipt baseline before the producer returns its fresh request ID."""

    return browser_diagnostic_receipts.validate_browser_receipt_projection(value)


def validate_success_sample_evidence(evidence: Any, *, label: str) -> None:
    """Validate one persisted evidence sample. Every retained sample goes through this one owner.

    `validate_success_artifact` used to apply these rules to `[baseline, final_evidence]` only and to
    check merely that the settle samples list was non-empty, so a 600-second artifact with 118 real
    samples was still accepted with a browser Error injected into sample 59, and a forged three-sample
    artifact was accepted with a browser Error in an intermediate sample and a server Error in its
    only settle sample. The observation interval is the property the clean soak is supposed to prove,
    so every sample in it is graded, by this function, with no per-call-site copy of the rules.

    This is deliberately not `evidence_failed`: the runtime early-fail and the persisted record stay
    independent proofs, so a defect in one cannot certify the other.
    """

    if not isinstance(evidence, Mapping) or not {"browserEvents", "browserLocalFailures", "serverLogErrors", "browserLogFailures", "serverLogDropped", "cursors"}.issubset(evidence):
        raise ArtifactIntegrityError(f"success artifact is missing final evidence fields ({label})")
    if "browserReceiptBarrier" not in evidence or "browserReceiptProjection" not in evidence:
        raise ArtifactIntegrityError(f"success artifact is missing receipt projection evidence ({label})")
    if any(not isinstance(evidence[name], list) for name in ("browserEvents", "browserLocalFailures", "serverLogErrors", "browserLogFailures")):
        raise ArtifactIntegrityError(f"success artifact has malformed final evidence fields ({label})")
    dropped = evidence.get("serverLogDropped")
    if not isinstance(dropped, Mapping) or dropped.get("count") != 0 or dropped.get("by_level", {}) != {}:
        raise ArtifactIntegrityError(f"success artifact contains server log drops ({label})")
    try:
        receipt_barrier = browser_diagnostic_receipts.validate_browser_receipt_barrier(evidence["browserReceiptBarrier"])
        receipt_projection = browser_diagnostic_receipts.validate_browser_receipt_projection(evidence["browserReceiptProjection"])
    except AssertionError as error:
        raise ArtifactIntegrityError(f"success artifact has malformed receipt projection evidence ({label})") from error
    if receipt_barrier["quiescent"] is not True or receipt_projection["barrier"] != receipt_barrier:
        raise ArtifactIntegrityError(f"success artifact has non-quiescent receipt barrier evidence ({label})")
    for failure_field in ("browserLocalFailures", "serverLogErrors", "browserLogFailures", "integrityFailures"):
        failures = evidence.get(failure_field, [])
        if not isinstance(failures, list):
            raise ArtifactIntegrityError(f"success artifact has malformed {failure_field} ({label})")
        if failures:
            raise ArtifactIntegrityError(f"success artifact contains {failure_field} ({label})")
    if evidence.get("failure") or evidence.get("additional_failures") or evidence.get("cleanup_failure") or evidence.get("terminal"):
        raise ArtifactIntegrityError(f"success artifact contains nested failure evidence ({label})")
    try:
        evidence_baseline(evidence)
    except AssertionError as error:
        raise ArtifactIntegrityError(f"success artifact has malformed cursor evidence ({label})") from error


def validate_sample_cadence(samples: Sequence[Any], *, key: str, label: str, span_seconds: float) -> float:
    """Prove a retained sample series measured its whole window at the sampling cadence.

    `elapsed_seconds: 603.4` beside two samples proves nothing about the 600 seconds in between, and
    the persisted validator accepted exactly that. A real series starts inside one sampling gap of
    zero, advances strictly, never skips more than MAX_SAMPLE_GAP_SECONDS, reaches the requested
    span, and therefore cannot be shorter than the count that span and that gap imply.
    """

    minimum = math.ceil(span_seconds / MAX_SAMPLE_GAP_SECONDS)
    if not isinstance(samples, list) or len(samples) < minimum:
        raise ArtifactIntegrityError(f"success artifact retained {len(samples) if isinstance(samples, list) else 0} {label} samples, fewer than the {minimum} its {span_seconds:g}-second window requires")
    marks: list[float] = []
    for index, sample in enumerate(samples):
        mark = sample.get(key) if isinstance(sample, Mapping) else None
        if isinstance(mark, bool) or not isinstance(mark, (int, float)) or mark < 0:
            raise ArtifactIntegrityError(f"success artifact {label} sample {index} carries no measured {key}")
        marks.append(float(mark))
    if marks[0] > MAX_SAMPLE_GAP_SECONDS:
        raise ArtifactIntegrityError(f"success artifact {label} sampling starts {marks[0]:g}s in, past one {MAX_SAMPLE_GAP_SECONDS:g}s gap")
    for index, (earlier, later) in enumerate(zip(marks, marks[1:])):
        if later <= earlier:
            raise ArtifactIntegrityError(f"success artifact {label} sample {index + 1} does not advance {key} past {earlier:g}s")
        if later - earlier > MAX_SAMPLE_GAP_SECONDS:
            raise ArtifactIntegrityError(f"success artifact {label} skipped {later - earlier:g}s between samples {index} and {index + 1}, past one {MAX_SAMPLE_GAP_SECONDS:g}s gap")
    if marks[-1] < span_seconds:
        raise ArtifactIntegrityError(f"success artifact {label} sampling stops at {marks[-1]:g}s, short of its {span_seconds:g}s window")
    return marks[-1]


def validate_success_artifact(artifact: Mapping[str, Any]) -> None:
    """Fail closed: exit zero is reserved for a complete, internally coherent soak record."""
    required_identity = {"pid", "cwd", "process_started", "head", "bundle_url", "bundle_sha256"}
    requested_duration = artifact.get("requested_duration_seconds")
    if not isinstance(artifact.get("url"), str) or not isinstance(artifact.get("started_pt"), str) or not isinstance(artifact.get("ended_pt"), str) or isinstance(artifact.get("elapsed_seconds"), bool) or not isinstance(artifact.get("elapsed_seconds"), (int, float)) or artifact["elapsed_seconds"] < 0:
        raise ArtifactIntegrityError("success artifact is missing run identity or timing")
    if isinstance(requested_duration, bool) or not isinstance(requested_duration, int) or requested_duration < MIN_OBSERVATION_SECONDS:
        raise ArtifactIntegrityError("success artifact has malformed requested duration")
    if artifact["elapsed_seconds"] < requested_duration:
        raise ArtifactIntegrityError("success artifact ended before requested duration")
    settle = artifact.get("settle")
    settle_elapsed = artifact.get("settle_elapsed_seconds")
    if (
        not isinstance(settle, Mapping)
        or settle.get("status") != "clean"
        or settle.get("requested_seconds") != SETTLE_SECONDS
        or isinstance(settle_elapsed, bool)
        or not isinstance(settle_elapsed, (int, float))
        or settle_elapsed < SETTLE_SECONDS
        or settle.get("elapsed_seconds") != settle_elapsed
        or not isinstance(settle.get("samples"), list)
        or not settle["samples"]
    ):
        raise ArtifactIntegrityError("success artifact is missing measured authenticated settle")
    for key in ("identity", "completion_identity"):
        value = artifact.get(key)
        if not isinstance(value, Mapping) or not required_identity.issubset(value):
            raise ArtifactIntegrityError(f"success artifact is missing {key}")
    if artifact["identity"] != artifact["completion_identity"]:
        raise ArtifactIntegrityError("success artifact identity changed during soak")
    baseline = artifact.get("baseline")
    samples = artifact.get("samples")
    if not isinstance(baseline, Mapping) or not isinstance(samples, list) or len(samples) < 2:
        raise ArtifactIntegrityError("success artifact is missing baseline, duration, or final samples")
    final_boundary = artifact.get("finalBoundary")
    boundary_fields = {"status", "phaseFailures", "serverBefore", "serverBeforeTransition", "chromeBeforeRetirement", "uploaderFence", "eventRing", "atomicSnapshot", "blankReadiness", "chromeAfterRetirement", "serverAfter", "serverAfterTransition", "evidence"}
    if not isinstance(final_boundary, Mapping) or set(final_boundary) != boundary_fields or final_boundary.get("status") != "clean" or final_boundary.get("phaseFailures") != []:
        raise ArtifactIntegrityError("success artifact is missing exact clean finalBoundary")
    final_evidence = final_boundary.get("evidence")
    try:
        baseline_cursor = evidence_baseline(baseline)
        server_fields = {"epoch", "sequence", "capacity", "ids", "dropped", "logs", "recordDigests"}
        if set(final_boundary["serverBefore"]) != server_fields or set(final_boundary["serverAfter"]) != server_fields:
            raise AssertionError("final server rings have unexpected fields")
        server_before = server_ring_record({"ok": True, **dict(final_boundary["serverBefore"])})
        server_after = server_ring_record({"ok": True, **dict(final_boundary["serverAfter"])})
        before_logs = validate_server_ring_transition(baseline_cursor, server_before)
        after_logs = validate_server_ring_transition(server_before, server_after)
    except (AssertionError, TypeError, ValueError) as error:
        raise ArtifactIntegrityError("success artifact has malformed final server boundary") from error
    if final_boundary.get("serverBeforeTransition") != {"newLogs": before_logs} or final_boundary.get("serverAfterTransition") != {"newLogs": after_logs}:
        raise ArtifactIntegrityError("success artifact has malformed final server transitions")
    if server_before["dropped"]["count"] != 0 or server_after["dropped"]["count"] != 0:
        raise ArtifactIntegrityError("success artifact final server boundary contains drops")
    for field in ("chromeBeforeRetirement", "chromeAfterRetirement"):
        if final_boundary.get(field) != []:
            raise ArtifactIntegrityError(f"success artifact has nonempty {field}")
    fence = final_boundary.get("uploaderFence")
    if not isinstance(fence, Mapping) or set(fence) != {"cursor", "projection", "completions"} or isinstance(fence.get("cursor"), bool) or not isinstance(fence.get("cursor"), int) or fence["cursor"] < 0 or isinstance(fence.get("completions"), bool) or not isinstance(fence.get("completions"), int) or not 1 <= fence["completions"] <= 256:
        raise ArtifactIntegrityError("success artifact has malformed uploader fence")
    try:
        fence_projection = browser_diagnostic_receipts.validate_browser_receipt_projection(fence["projection"])
    except AssertionError as error:
        raise ArtifactIntegrityError("success artifact has malformed uploader fence projection") from error
    if fence_projection["barrier"]["quiescent"] is not True:
        raise ArtifactIntegrityError("success artifact uploader fence is not quiescent")
    event_ring = final_boundary.get("eventRing")
    if not isinstance(event_ring, Mapping) or set(event_ring) != {"atomic", "retirement", "delta"}:
        raise ArtifactIntegrityError("success artifact has malformed browser event ring outcomes")
    try:
        for ring_phase in ("atomic", "retirement"):
            validate_browser_event_ring_outcome(event_ring[ring_phase], phase=ring_phase)
        retirement_delta = validate_browser_retirement_delta(event_ring["delta"])
    except AssertionError as error:
        raise ArtifactIntegrityError("success artifact has malformed browser event ring outcomes") from error
    # A success artifact must positively state what the browser did while retiring, not
    # merely omit a failure: the delta carries the exact appended/evicted/receipt counts.
    if retirement_delta["reason"] not in RETIREMENT_DELTA_CLEAN_REASONS or retirement_delta["integrityFailures"]:
        raise ArtifactIntegrityError("success artifact retired with signal-carrying browser diagnostics")
    if event_ring["retirement"]["evicted"] != retirement_delta["evictedEvents"] or event_ring["retirement"]["appended"] != retirement_delta["appendedEvents"]:
        raise ArtifactIntegrityError("success artifact retirement delta disagrees with its event ring")
    atomic = final_boundary.get("atomicSnapshot")
    # Artifacts recorded before the page-identity change carry no `pageDrift`/`pageIdentity`; they are
    # validated below by the exact-href rule they were produced under. Which branch applies is decided
    # by a field the artifact either states or does not, never by a default substituted for a missing
    # value, and both branches fail closed.
    pinned_identity_artifact = "pageIdentity" in artifact
    atomic_fields = {"journey", "hiddenSentinel", "panelVisible", "page", "stats", "eventCount", "failureCount"} | ({"pageDrift"} if pinned_identity_artifact else set())
    if not isinstance(atomic, Mapping) or set(atomic) != atomic_fields or atomic.get("panelVisible") is not False or isinstance(atomic.get("eventCount"), bool) or not isinstance(atomic.get("eventCount"), int) or atomic["eventCount"] < 0 or atomic.get("failureCount") != 0 or validate_hidden_stats_stream_evidence(atomic.get("stats")) is None:
        raise ArtifactIntegrityError("success artifact has malformed atomic snapshot")
    journey = atomic.get("journey")
    hidden = atomic.get("hiddenSentinel")
    page = atomic.get("page")
    if not isinstance(journey, Mapping) or set(journey) != {"id", "reachable", "visitedSurfaces"} or journey.get("reachable") is not True or not isinstance(journey.get("id"), str) or not 1 <= len(journey["id"]) <= 256 or not isinstance(journey.get("visitedSurfaces"), list) or len(journey["visitedSurfaces"]) > 16 or any(not isinstance(surface, str) or not surface or len(surface) > 64 for surface in journey["visitedSurfaces"]):
        raise ArtifactIntegrityError("success artifact has malformed atomic journey")
    if not isinstance(hidden, Mapping) or set(hidden) != {"installed", "everVisible", "checks"} or hidden.get("installed") is not True or hidden.get("everVisible") is not False or isinstance(hidden.get("checks"), bool) or not isinstance(hidden.get("checks"), int) or not 1 <= hidden["checks"] <= browser_diagnostic_receipts.JAVASCRIPT_MAX_SAFE_INTEGER:
        raise ArtifactIntegrityError("success artifact has malformed hidden sentinel")
    if not isinstance(page, Mapping) or set(page) != {"visibility", "origin", "href"} or page.get("visibility") != "visible":
        raise ArtifactIntegrityError("success artifact has malformed atomic page identity")
    if not pinned_identity_artifact:
        expected = urlsplit(artifact["url"])
        if page.get("origin") != f"{expected.scheme}://{expected.hostname}:{expected.port}" or page.get("href") != artifact["url"]:
            raise ArtifactIntegrityError("success artifact has malformed atomic page identity")
    else:
        pinned = artifact["pageIdentity"]
        if not isinstance(pinned, Mapping) or set(pinned) != {"url", "journeyId"} or pinned.get("url") != artifact["url"] or not isinstance(pinned.get("journeyId"), str) or not pinned["journeyId"]:
            raise ArtifactIntegrityError("success artifact is missing the pinned live document identity")
        page_identity = classify_page_identity({**page, "journeyId": journey["id"]}, expected_url=artifact["url"], expected_journey_id=pinned["journeyId"])
        if page_identity["reasons"] or page_identity["drift"] != atomic["pageDrift"]:
            raise ArtifactIntegrityError("success artifact atomic page identity was substituted or its drift was rewritten")
        drift_record = artifact.get("pageIdentityDrift")
        if not isinstance(drift_record, Mapping) or set(drift_record) != {"observed", "entries"} or isinstance(drift_record["observed"], bool) or not isinstance(drift_record["observed"], int) or drift_record["observed"] < 0 or not isinstance(drift_record["entries"], list) or len(drift_record["entries"]) > MAX_RECORDED_PAGE_IDENTITY_DRIFT or len(drift_record["entries"]) > drift_record["observed"]:
            raise ArtifactIntegrityError("success artifact has malformed app-owned page drift record")
    if final_boundary.get("blankReadiness") != {"href": "about:blank", "readyState": "complete"}:
        raise ArtifactIntegrityError("success artifact has incomplete about:blank retirement")
    # Every retained sample is graded, not just the two endpoints: the observation interval is the
    # property a clean soak exists to prove, so a browser Error in sample 59 of 118 must fail here.
    validate_success_sample_evidence(baseline, label="baseline")
    validate_success_sample_evidence(final_evidence, label="final evidence")
    for index, sample in enumerate(settle["samples"]):
        validate_success_sample_evidence(sample, label=f"settle sample {index}")
    for index, sample in enumerate(samples):
        validate_success_sample_evidence(sample, label=f"observation sample {index}")
    # The settle window ends on the baseline the observation interval is measured against; a baseline
    # chosen from anywhere else is a hand-picked clean sample, not the measured end of the settle.
    settle_end = validate_sample_cadence(settle["samples"], key="settle_elapsed_seconds", label="settle", span_seconds=SETTLE_SECONDS)
    if settle_end != settle_elapsed or settle["samples"][-1] != baseline:
        raise ArtifactIntegrityError("success artifact baseline is not the measured end of its settle window")
    observation_end = validate_sample_cadence(samples, key="elapsed_seconds", label="observation", span_seconds=requested_duration)
    if artifact["elapsed_seconds"] < observation_end:
        raise ArtifactIntegrityError("success artifact reports less elapsed time than its own samples measured")
    if final_evidence["browserReceiptProjection"] != fence_projection:
        raise ArtifactIntegrityError("success artifact final receipt projection changed after its fence")
    try:
        validate_browser_retirement_delta_evidence(
            retirement_delta,
            retirement=event_ring["retirement"],
            retained_events=final_evidence["browserEvents"],
            event_count=atomic["eventCount"],
            retained_cursor=final_evidence["cursors"]["js"],
            fence_receipts=len(fence_projection["receipts"]),
            retained_receipts=len(final_evidence["browserReceiptProjection"]["receipts"]),
        )
    except (AssertionError, KeyError, TypeError) as error:
        raise ArtifactIntegrityError("success artifact has malformed browser event ring outcomes") from error
    baseline_stats = baseline.get("statsStreamEvidence")
    final_stats = final_evidence.get("statsStreamEvidence")
    if not isinstance(baseline_stats, Mapping) or not isinstance(final_stats, Mapping):
        raise ArtifactIntegrityError("success artifact is missing hidden YO!stats stream evidence")
    baseline_stream = baseline_stats.get("stream") if isinstance(baseline_stats.get("stream"), Mapping) else {}
    final_stream = final_stats.get("stream") if isinstance(final_stats.get("stream"), Mapping) else {}
    if int(final_stream.get("deliverySequence") or 0) <= int(baseline_stream.get("deliverySequence") or 0):
        raise ArtifactIntegrityError("success artifact has no post-baseline hidden YO!stats delivery")
    coherent_fields = ("acceptedDeltaSequence", "cacheGeneration", "sourceGeneration", "deltaRevision")
    if not all(int(final_stream.get(field) or 0) > int(baseline_stream.get(field) or 0) for field in coherent_fields):
        raise ArtifactIntegrityError("success artifact lacks coherent accepted delta, cache, source generation, and revision advancement")
    baseline_painted = str(baseline_stats.get("paintedGenerationKey") or "")
    final_painted = str(final_stats.get("paintedGenerationKey") or "")
    if final_painted and final_painted != baseline_painted:
        raise ArtifactIntegrityError("success artifact painted a hidden YO!stats generation")


def produce_negative_browser_failure(driver: Any) -> dict[str, Any]:
    """Use a production browser request and Logs renderer to prove one controlled failure."""

    result = driver.execute_async_script(r"""
        const done = arguments[arguments.length - 1];
        const route = arguments[0];
        const message = arguments[1];
        const canary = arguments[2];
        (async () => {
          if (typeof refreshYoloRulesStatus !== 'function'
              || typeof selectSession !== 'function'
              || typeof setDebugSubTab !== 'function'
              || typeof pollDebugLogs !== 'function'
              || typeof jsDebugCurrentObservationReceiptProjection !== 'function') {
            throw new Error('controlled browser failure production path is unavailable');
          }
          const originalFetch = window.fetch;
          const beforeId = Array.isArray(jsDebugEvents) && jsDebugEvents.length ? Number(jsDebugEvents.at(-1)?.id || 0) : 0;
          const diagnosticUrl = `https://localhost/api/diagnostic?token=${canary}#token=${canary}`;
          let intercepted = 0;
          window.fetch = (input, options = {}) => {
            const requestUrl = new URL(String(input), location.href);
            if (requestUrl.pathname !== route) return originalFetch(input, options);
            intercepted += 1;
            return Promise.resolve(new Response(JSON.stringify({
              error: message,
              diagnostic: {diagnostic_url: diagnosticUrl, credentials: {password: canary, authorization: `Bearer ${canary}`}},
            }), {status: 500, statusText: 'Controlled Failure', headers: {'Content-Type': 'application/json'}}));
          };
          try {
            await refreshYoloRulesStatus({silent: true});
          } finally {
            window.fetch = originalFetch;
          }
          if (intercepted !== 1) throw new Error(`expected one controlled browser request, observed ${intercepted}`);
          const matches = jsDebugFailureEvents().filter(event => Number(event?.id || 0) > beforeId
            && event?.type === 'api' && event?.endpoint === route && event?.status === 500 && event?.ok === false);
          if (matches.length !== 1) throw new Error(`expected one controlled browser event, observed ${matches.length}`);
          const event = matches[0];
          const queued = typeof jsDebugObservationBatchForEntries === 'function'
            ? jsDebugObservationBatchForEntries(jsDebugCurrentObservationState?.queue || []) : null;
          if (typeof flushJsDebugCurrentObservations !== 'function') throw new Error('observation uploader is unavailable');
          await flushJsDebugCurrentObservations();
          const projection = jsDebugCurrentObservationReceiptProjection();
          selectSession(debugPaneItemId, {userInitiated: true});
          setDebugSubTab('logs');
          await pollDebugLogs({force: true});
          if (typeof refreshDebugLogsViews === 'function') refreshDebugLogsViews();
          await Promise.resolve();
          const rows = [...document.querySelectorAll('article[data-js-debug-log-entry]')].filter(row =>
            row.querySelector('[data-js-debug-log-request-id]')?.textContent === String(event.requestId || '')
            && row.querySelector('[data-js-debug-log-route]')?.textContent === route
            && (row.querySelector('[data-js-debug-log-event]')?.textContent
              || row.querySelector('[data-js-debug-log-category]')?.textContent) === 'api');
          const row = rows.length === 1 ? rows[0] : null;
          const rendered = {
            matchingRows: rows.length,
            requestId: String(row?.querySelector('[data-js-debug-log-request-id]')?.textContent || ''),
            source: String(row?.querySelector('[data-js-debug-log-source]')?.textContent || ''),
            route: String(row?.querySelector('[data-js-debug-log-route]')?.textContent || ''),
            event: String(row?.querySelector('[data-js-debug-log-event]')?.textContent
              || row?.querySelector('[data-js-debug-log-category]')?.textContent || ''),
            text: String(row?.textContent || '').replace(/\s+/g, ' ').trim(),
          };
          const storageValues = storage => {
            try { return Array.from({length: storage?.length || 0}, (_, index) => `${storage.key(index)}=${storage.getItem(storage.key(index))}`); }
            catch (_) { return []; }
          };
          const retained = {
            events: jsDebugEvents,
            failures: jsDebugFailureEvents(),
            projection,
            yoloRules: typeof yoloRulesPayload === 'object' ? yoloRulesPayload : null,
            logs: typeof jsDebugLogsState === 'object' ? jsDebugLogsState.payload : null,
          };
          const channels = {
            dom: document.documentElement.outerHTML,
            clipboard: typeof debugLogsTextForClipboard === 'function' ? debugLogsTextForClipboard() : '',
            retained: JSON.stringify(retained),
            upload: JSON.stringify(queued),
            storage: JSON.stringify([...storageValues(window.localStorage), ...storageValues(window.sessionStorage)]),
          };
          const secrets = [canary, diagnosticUrl, `token=${canary}`, `#token=${canary}`, `Bearer ${canary}`];
          const redaction = Object.fromEntries(Object.entries(channels).map(([name, value]) => [name, secrets.every(secret => !String(value).includes(secret))]));
          done({event: {...event}, projection, rendered, redaction});
        })().catch(error => done({error: String(error)}));
    """, NEGATIVE_ROUTE, NEGATIVE_MESSAGE, NEGATIVE_CANARY)
    if not isinstance(result, Mapping) or result.get("error"):
        raise RuntimeError(f"controlled browser failure producer failed: {result.get('error') if isinstance(result, Mapping) else type(result).__name__}")
    if set(result) != {"event", "projection", "rendered", "redaction"}:
        raise RuntimeError("controlled browser failure proof is malformed")
    event = result.get("event")
    rendered = result.get("rendered")
    redaction = result.get("redaction")
    projection = browser_diagnostic_receipts.validate_browser_receipt_projection(result.get("projection"))
    if not isinstance(event, Mapping) or not isinstance(rendered, Mapping) or not isinstance(redaction, Mapping):
        raise RuntimeError("controlled browser failure proof has malformed channels")
    receipts = [receipt for receipt in projection["receipts"] if receipt.get("eventId") == event.get("id")]
    if (
        event.get("type") != "api"
        or event.get("endpoint") != NEGATIVE_ROUTE
        or event.get("status") != 500
        or event.get("ok") is not False
        or not isinstance(event.get("requestId"), str)
        or not event["requestId"]
        or not isinstance(event.get("wallTime"), str)
        or not event["wallTime"]
        or len(receipts) != 1
        or receipts[0].get("requestId") != event.get("requestId")
        or receipts[0].get("route") != NEGATIVE_ROUTE
        or receipts[0].get("event") != "api"
        or receipts[0].get("deliveryOutcome") != "failed"
        or receipts[0].get("httpStatus") != 500
        or receipts[0].get("status") != "accepted"
        or rendered.get("matchingRows") != 1
        or rendered.get("requestId") != event.get("requestId")
        or rendered.get("source") != NEGATIVE_SOURCE
        or rendered.get("route") != NEGATIVE_ROUTE
        or rendered.get("event") != "api"
        or not all(redaction.get(channel) is True for channel in ("dom", "clipboard", "retained", "upload", "storage"))
    ):
        raise RuntimeError("controlled browser failure correlation, rendered row, receipt, or redaction proof is incomplete")
    return {**receipts[0], "source": NEGATIVE_SOURCE, "receiptSource": receipts[0]["source"], "rendered": dict(rendered), "redaction": dict(redaction)}


def attribute_negative_probe(
    driver: Any,
    *,
    previous: Mapping[str, Any],
    baseline_projection: Mapping[str, Any],
    negative_handle: Mapping[str, Any],
) -> dict[str, Any]:
    """Prove the injected Error is the sole cause of this red, without retiring the page.

    This phase never navigates, never runs `location.replace('about:blank')` and never bridges
    DOMStorage, so no atomic retirement snapshot exists to veto a correctly injected Error the way
    it did in the r3 and r9 artifacts.
    """

    boundary: dict[str, Any] = {"status": "failed", "phaseFailures": [], "uploaderFence": None, "chromeFailures": None, "evidence": None, "attribution": None}

    def fail(phase: str, error: BaseException) -> None:
        boundary["phaseFailures"].append({"phase": phase, "terminal": type(error).__name__, "message": redact_text(str(error))})

    phase_errors = (AssertionError, OSError, RuntimeError, TypeError, ValueError, KeyError, WebDriverException)
    try:
        boundary["uploaderFence"] = fence_browser_uploader(driver)
    except phase_errors as error:
        fail("uploaderFence", error)
    try:
        boundary["chromeFailures"] = chrome_failure_entries(driver.get_log("browser"))
    except phase_errors as error:
        fail("chromeFailures", error)
    try:
        evidence, _cursor = classify_incremental_evidence(sample_evidence(driver), previous)
        require_negative_acknowledgement(evidence, negative_handle, baseline_projection)
        boundary["evidence"] = evidence
        unrelated = {
            "extraBrowserFailures": max(0, len(evidence.get("browserLocalFailures") or []) - 1),
            "serverLogErrors": len(evidence.get("serverLogErrors") or []),
            "browserLogFailures": len(evidence.get("browserLogFailures") or []) + len(boundary["chromeFailures"] or []),
            "serverLogDropped": int((evidence.get("serverLogDropped") or {}).get("count") or 0),
            "integrityFailures": [item for item in (evidence.get("integrityFailures") or [])],
        }
        sole_cause = not any(unrelated[name] for name in ("extraBrowserFailures", "serverLogErrors", "browserLogFailures", "serverLogDropped")) and not unrelated["integrityFailures"]
        boundary["attribution"] = {"soleCause": sole_cause, "unrelated": unrelated, "injected": negative_probe_product(negative_handle)}
        if not sole_cause:
            raise AssertionError("the injected browser Error is not the sole cause of this negative probe")
        boundary["status"] = "attributed"
    except phase_errors as error:
        fail("attribution", error)
    return boundary


def listener_identity_evidence(identity: ListenerIdentity) -> dict[str, object]:
    return {"pid": identity.pid, "cwd": identity.cwd, "process_started": identity.started, "head": identity.head}


def run_soak(driver: Any, *, url: str, duration: int, expected_head: str, expected_bundle_sha256: str, expected_cwd: str | None = None, negative_probe: bool, clean_soak_prerequisite: Mapping[str, Any] | None = None, sleep_fn=time.sleep, monotonic_fn=time.monotonic) -> dict[str, Any]:
    parsed = urlsplit(url)
    identity = listener_identity(parsed.port or 0)
    artifact: dict[str, Any] = {"url": url, "started_pt": pacific_wall_time(), "requested_duration_seconds": duration, "identity": listener_identity_evidence(identity), "samples": []}
    if negative_probe:
        if clean_soak_prerequisite is None:
            raise RuntimeError("the negative browser error probe requires a validated clean soak artifact")
        artifact["phase"] = "negative_probe"
        artifact["clean_soak_prerequisite"] = dict(clean_soak_prerequisite)
    if identity.head != expected_head or (expected_cwd is not None and identity.cwd != expected_cwd):
        raise RuntimeError(f"listener HEAD mismatch: expected {expected_head}, got {identity.head}")
    auth_user = install_auth_cookie(driver, url, parsed.port or 0)
    install_start_of_document_sentinels(driver)
    production_finalizer = (duration >= MIN_OBSERVATION_SECONDS or negative_probe) and isinstance(auth_user, AuthUser)
    server_reader = authenticated_server_log_reader(url, parsed.port or 0, auth_user) if production_finalizer else None
    pre_page_server = server_ring_record(server_reader()) if server_reader is not None else None
    if production_finalizer:
        chrome_failure_entries(driver.get_log("browser"))
    bundle_url, bundle_sha256 = discover_served_bundle(driver, url)
    artifact["identity"].update({"bundle_url": bundle_url, "bundle_sha256": bundle_sha256})
    if bundle_sha256 != expected_bundle_sha256:
        raise RuntimeError("served bundle SHA256 mismatch")
    WebDriverWait(driver, 30).until(lambda current: current.execute_script("return document.getElementById('grid') !== null && typeof jsDebugFailureEvents === 'function' && typeof jsDebugCurrentObservationReceiptBarrier === 'function'"))
    drift_record = new_page_identity_drift_record()
    artifact["pageIdentityDrift"] = drift_record
    expected_journey_id = ""
    if production_finalizer:
        # The first reading after the final navigation pins the live document instance: every later
        # check proves the same document, which an href comparison alone cannot do because a reload
        # lands on the identical URL.
        expected_journey_id = assert_page_identity(driver, url)["journeyId"]
        artifact["pageIdentity"] = {"url": url, "journeyId": expected_journey_id}
    assert_stats_hidden(driver)
    stats_evidence = wait_for_hidden_stats_stream(driver) if production_finalizer else None
    started = monotonic_fn()
    previous: dict[str, Any] | None = None
    negative_handle: dict[str, Any] | None = None
    previous_stats: Mapping[str, Any] | None = stats_evidence
    try:
        if production_finalizer:
            if pre_page_server is None or stats_evidence is None:
                raise AssertionError("authenticated settle inputs are unavailable")
            baseline, previous, previous_stats, settle = settle_authenticated_page(
                driver,
                expected_url=url,
                expected_identity=identity,
                pre_page_server=pre_page_server,
                initial_stats=stats_evidence,
                expected_journey_id=expected_journey_id,
                drift_record=drift_record,
                sleep_fn=sleep_fn,
                monotonic_fn=monotonic_fn,
            )
            artifact["settle"] = settle
            artifact["settle_elapsed_seconds"] = settle["elapsed_seconds"]
            if baseline is None:
                record_failure(artifact, settle["samples"][-1])
                previous = None
            else:
                artifact["baseline"] = baseline
            started = monotonic_fn()
        else:
            artifact["baseline"] = sample_evidence(driver)
            previous = evidence_baseline(artifact["baseline"])
        if "baseline" not in artifact:
            pass
        elif evidence_failed(artifact["baseline"]):
            record_failure(artifact, artifact["baseline"])
        else:
            if negative_probe:
                validate_negative_probe_baseline(artifact["baseline"].get("browserReceiptProjection"))
            while True:
                if production_finalizer and listener_identity(parsed.port or 0) != identity:
                    raise AssertionError("listener identity changed during observation")
                page_identity = assert_page_identity(driver, url, expected_journey_id) if production_finalizer else None
                assert_stats_hidden(driver)
                evidence, previous = classify_incremental_evidence(sample_evidence(driver), previous)
                if previous_stats is not None:
                    current_stats, stats_integrity = classify_hidden_stats_stream(sample_hidden_stats_stream(driver), previous_stats)
                    if current_stats is not None:
                        evidence["statsStreamEvidence"] = current_stats
                        previous_stats = current_stats
                    evidence.setdefault("integrityFailures", []).extend(stats_integrity)
                evidence["elapsed_seconds"] = monotonic_fn() - started
                if page_identity is not None:
                    note_page_identity_drift(drift_record, page_identity, phase="observation", elapsed=evidence["elapsed_seconds"])
                artifact["samples"].append(evidence)
                if evidence_failed(evidence):
                    record_failure(artifact, evidence)
                    break
                if evidence["elapsed_seconds"] >= duration:
                    break
                sleep_fn(SAMPLE_SECONDS)
            if negative_probe and not evidence_failed(artifact):
                negative_handle = produce_negative_browser_failure(driver)
    except (AssertionError, OSError, RuntimeError, subprocess.SubprocessError, WebDriverException) as error:
        record_failure(artifact, terminal_failure("runtime", error))
    artifact["ended_pt"] = pacific_wall_time()
    artifact["elapsed_seconds"] = monotonic_fn() - started
    try:
        completion_identity = listener_identity(parsed.port or 0)
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        record_failure(artifact, terminal_failure("runtime", error))
    else:
        artifact["completion_identity"] = listener_identity_evidence(completion_identity)
        if completion_identity != identity:
            record_failure(artifact, {"integrityFailures": ["listener identity changed during soak"]})
        try:
            completion_bundle_url, completion_bundle_sha256 = discover_served_bundle(driver, url, False)
            artifact["completion_identity"].update({"bundle_url": completion_bundle_url, "bundle_sha256": completion_bundle_sha256})
            if completion_bundle_sha256 != expected_bundle_sha256 or completion_bundle_sha256 != bundle_sha256:
                record_failure(artifact, {"integrityFailures": ["served bundle identity changed during soak"]})
        except (AssertionError, OSError, RuntimeError, subprocess.SubprocessError, WebDriverException) as error:
            record_failure(artifact, terminal_failure("runtime", error))
    if negative_probe:
        if previous is not None and negative_handle is not None:
            try:
                artifact["negativeProbe"] = attribute_negative_probe(
                    driver,
                    previous=previous,
                    baseline_projection=artifact["baseline"].get("browserReceiptProjection", {}),
                    negative_handle=negative_handle,
                )
            except (AssertionError, OSError, RuntimeError, subprocess.SubprocessError, WebDriverException) as error:
                record_failure(artifact, terminal_failure("runtime", error))
            else:
                probe = artifact["negativeProbe"]
                if probe["status"] == "attributed":
                    artifact["expected_failure_durably_detected"] = negative_probe_product(negative_handle)
                    record_failure(artifact, {"expected_negative_probe": True, "browserLocalFailures": probe["evidence"]["browserLocalFailures"], "browserReceiptProjection": probe["evidence"]["browserReceiptProjection"]})
                else:
                    record_failure(artifact, {"integrityFailures": [f"negativeProbe: {entry['message']}" for entry in probe["phaseFailures"]] or ["negative probe produced no attribution"]})
        elif not evidence_failed(artifact):
            record_failure(artifact, {"integrityFailures": ["negative probe did not reach its injection"]})
    elif previous is not None:
        if production_finalizer and previous_stats is not None:
            try:
                server_reader = authenticated_server_log_reader(url, parsed.port or 0, auth_user)
                artifact["finalBoundary"] = finalize_live_browser_soak(
                    driver,
                    server_reader=server_reader,
                    expected_url=url,
                    previous=previous,
                    previous_stats=previous_stats,
                    baseline_projection=artifact["baseline"].get("browserReceiptProjection", {}),
                    negative_handle=negative_handle,
                    expected_journey_id=expected_journey_id,
                )
                final_evidence = artifact["finalBoundary"]["evidence"]
                artifact["ended_pt"] = pacific_wall_time()
                artifact["elapsed_seconds"] = monotonic_fn() - started
                if negative_handle is not None and not final_evidence.get("integrityFailures") and len(final_evidence.get("browserLocalFailures", [])) == 1 and not final_evidence.get("serverLogErrors") and not final_evidence.get("browserLogFailures"):
                    artifact["expected_failure_durably_detected"] = negative_probe_product(negative_handle)
                    record_failure(artifact, {"expected_negative_probe": True, "browserLocalFailures": final_evidence["browserLocalFailures"], "browserReceiptProjection": final_evidence["browserReceiptProjection"]})
                elif evidence_failed(final_evidence):
                    record_failure(artifact, final_evidence)
            except (AssertionError, OSError, RuntimeError, subprocess.SubprocessError, WebDriverException) as error:
                record_failure(artifact, terminal_failure("runtime", error))
        else:
            try:
                assert_stats_hidden(driver)
                final_evidence, _ = classify_incremental_evidence(sample_evidence(driver), previous)
                if previous_stats is not None:
                    final_stats, stats_integrity = classify_hidden_stats_stream(sample_hidden_stats_stream(driver), previous_stats)
                    if final_stats is not None:
                        final_evidence["statsStreamEvidence"] = final_stats
                    final_evidence.setdefault("integrityFailures", []).extend(stats_integrity)
                final_evidence["elapsed_seconds"] = monotonic_fn() - started
                artifact["samples"].append(final_evidence)
                artifact["ended_pt"] = pacific_wall_time()
                artifact["elapsed_seconds"] = final_evidence["elapsed_seconds"]
                if evidence_failed(final_evidence):
                    record_failure(artifact, final_evidence)
            except (AssertionError, OSError, RuntimeError, subprocess.SubprocessError, WebDriverException) as error:
                record_failure(artifact, terminal_failure("runtime", error))
    return artifact


def write_artifact(output: Path, artifact: Mapping[str, Any]) -> None:
    payload = (json.dumps(redact_evidence(artifact), sort_keys=True) + "\n").encode("utf-8")
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        remaining = memoryview(payload)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("artifact write made no progress")
            remaining = remaining[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def redact_evidence(value: Any) -> Any:
    """Redact every retained evidence channel through the shared product owner."""
    return redact_diagnostic_value(value)

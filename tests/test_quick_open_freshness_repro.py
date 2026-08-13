"""M11: the Quick Open freshness defect - reproduction, and the contract that fixed it.

The originating incident (QA of 0.7.0 on live 7771): Quick Open answered as if it were
current while its producer (`indexd`) was not running, with no warning anywhere in the UI.

Mechanism, as it stood before the fix:

1. Every web process is structurally a follower for Quick Open.  `search_index_can_build`
   (`yolomux_lib/app.py:2920-2922`) returns False for the search-index role, so only the
   `indexd` child may build.  Followers always take a snapshot-serving branch.
2. The persisted SQLite snapshot was validated ONLY by `_sqlite_metadata_matches`
   (`yolomux_lib/search/file_index.py`), which compares root, version, storage == "sqlite"
   and skip_signature.  That is a configuration-shape match.  There was no generation term,
   no mtime term, no age term and no producer-liveness term, so a snapshot written months
   ago by a long-dead `indexd` passed identically to one written a second ago.
3. `refreshing_elsewhere: True` claims some other process is currently refreshing this
   index.  It was emitted with no proof at `filesystem/search.py:497`, `:525`, `:553`,
   `:605` and `:640`, and at `:850` it was literally `state == "follower"` - a role
   predicate wearing a freshness name.  `:553` looked gated on
   `not refresh_result.get("fallback")`, but `request_background_owner_refresh` returns
   `{"ok": False, "accepted": False, "fallback": False}` when no requester is wired, so the
   guard passed with no owner at all.
4. A dead `indexd` surfaced only when there was no snapshot to hide behind
   (`search.py` raises 424 FAILED_DEPENDENCY).  With a snapshot present, control never
   reached that line.

The fix keeps the shape predicate pure and adds ONE freshness owner,
`file_index.index_freshness()`, returning a typed `SnapshotFreshness`
{state, reason, snapshot_age_seconds, producer_epoch, ...}.  `ready`/`full` now needs
shape match AND a live producer epoch AND custody within bound.  Anything else is still
SERVED - a stale snapshot is useful - but labelled.

Test naming in this file.  The first half of M11 wrote `test_bug_*` tests that pinned the
wrong behaviour and `test_contract_*` tests marked `xfail(strict=True)`.  The fix flipped
both: the contract tests now pass with their markers removed, and each `test_bug_*` was
rewritten in place - same fixture, same discriminator, inverted verdict - under a
`test_fixed_*` name.  The mapping, so the original node ids stay traceable:

* `test_bug_sqlite_metadata_match_accepts_a_200_day_old_snapshot`
  -> `test_fixed_shape_predicate_stays_pure_and_freshness_answers_separately`
* `test_bug_follower_serves_stale_snapshot_as_ready_full_while_indexd_is_dead`
  -> `test_fixed_follower_labels_a_stale_snapshot_instead_of_claiming_ready_full`
* `test_bug_refreshing_elsewhere_is_true_with_nothing_refreshing`
  -> `test_fixed_refreshing_elsewhere_is_false_with_nothing_refreshing`
* `test_bug_warming_owner_claims_refreshing_elsewhere_from_a_child_snapshot`
  -> `test_fixed_warming_owner_does_not_claim_a_refresh_elsewhere`
* `test_bug_index_status_refreshing_elsewhere_is_only_a_role_predicate`
  -> `test_fixed_index_status_refreshing_elsewhere_is_not_a_role_predicate`

One deviation from the M11 brief is recorded here deliberately.  The brief's contract test
`test_contract_sqlite_metadata_match_must_reject_an_unvouchable_snapshot` asserted that
`_sqlite_metadata_matches` itself must return False for an unvouchable snapshot.  That
conflicts with the mandated design, which keeps that predicate as the pure shape/readability
answer ("can I read these rows at all") so a stale snapshot stays servable, and puts the
authoritative verdict in a sibling record.  The intent - an unvouchable snapshot must not
validate as authoritative - is preserved verbatim below against the sibling, under the name
`test_contract_authoritative_check_must_reject_an_unvouchable_snapshot`.

Run: `python3 -m pytest tests/test_quick_open_freshness_repro.py -q -p no:randomly`
"""

import json
import os
from pathlib import Path
import sqlite3
import time

import pytest

from yolomux_lib import file_index
from yolomux_lib import filesystem


ONE_DAY_SECONDS = 24.0 * 60.0 * 60.0
# Deliberately absurd: 200 days is far past any plausible "this is still current" window and
# ~9600x `INDEX_TTL_SECONDS` (30 minutes).
ANCIENT_SNAPSHOT_AGE_SECONDS = 200.0 * ONE_DAY_SECONDS

# Fields the search payload needs in order to be honest about a snapshot it cannot vouch
# for.  None of them existed before the fix; the contract requires at least one.
FRESHNESS_FIELDS = ("stale", "degraded", "snapshot_age_seconds", "built_at", "producer_state", "freshness")

GHOST_NAME = "quickopen_freshness_sentinel_ghost.py"
FRESH_NAME = "quickopen_freshness_sentinel_fresh.py"
SENTINEL_QUERY = "quickopen_freshness_sentinel"


def _clear_registry() -> None:
    with file_index._REGISTRY_LOCK:
        file_index._REGISTRY.clear()
    file_index.reset_producer_liveness_cache()
    file_index.clear_accepted_refreshes()


def _absent_pid() -> int:
    """A PID with no process behind it, so an epoch recording it cannot be live."""

    for candidate in range(4_190_000, 4_190_400):
        try:
            os.kill(candidate, 0)
        except ProcessLookupError:
            return candidate
        except PermissionError:
            continue
    raise RuntimeError("no absent PID available for the dead-producer fixture")


def _dead_producer_epoch() -> str:
    """The epoch a long-dead `indexd` would have left behind: PID gone entirely."""

    return f"{_absent_pid()}:proc:4242"


def _reused_pid_producer_epoch() -> str:
    """A LIVE pid whose birth identity differs - i.e. the PID was reused, not our producer."""

    return f"{os.getpid()}:proc:1"


def _build_persisted_snapshot(root: Path) -> file_index.RootIndex:
    """Build + persist a snapshot the way the real `indexd` child does."""

    return file_index.build_now(
        root,
        filesystem.SEARCH_SKIP_DIRS,
        exclude_path=filesystem._path_is_secret,
        exclude_signature=filesystem.SEARCH_SECRET_EXCLUDE_SIGNATURE,
    )


def _write_producer_heartbeat(root: Path, epoch: str, at: float) -> None:
    path = file_index._producer_heartbeat_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"producer_epoch": epoch, "at": float(at), "root": str(root)}), encoding="utf-8")


def _set_producer_epoch(root: Path, epoch: str, *, heartbeat_at: float) -> None:
    """Rewrite the producer identity everywhere the writer stamps it."""

    disk_path = file_index._index_disk_path(root)
    manifest_path = file_index._index_manifest_path(root)
    with sqlite3.connect(disk_path) as conn:
        conn.execute("UPDATE metadata SET value = ? WHERE key = 'producer_epoch'", (epoch,))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["producer_epoch"] = epoch
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    _write_producer_heartbeat(root, epoch, heartbeat_at)
    file_index.reset_producer_liveness_cache()


def _age_persisted_snapshot(root: Path, age_seconds: float) -> float:
    """Backdate every age signal the snapshot carries: metadata, manifest and file mtimes."""

    built_at = time.time() - age_seconds
    disk_path = file_index._index_disk_path(root)
    manifest_path = file_index._index_manifest_path(root)
    with sqlite3.connect(disk_path) as conn:
        conn.execute("UPDATE metadata SET value = ? WHERE key = 'built_at'", (repr(float(built_at)),))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["built_at"] = repr(float(built_at))
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    for path in (disk_path, manifest_path):
        os.utime(path, (built_at, built_at))
    return built_at


def _kill_producer(root: Path, *, built_at: float, epoch: str | None = None) -> str:
    """Replace this test process's producer identity with a dead one, as of `built_at`."""

    dead_epoch = epoch or _dead_producer_epoch()
    _set_producer_epoch(root, dead_epoch, heartbeat_at=built_at)
    return dead_epoch


def _persisted_metadata(root: Path) -> dict[str, str]:
    with sqlite3.connect(f"file:{file_index._index_disk_path(root).as_posix()}?mode=ro", uri=True) as conn:
        return {str(key): str(value) for key, value in conn.execute("SELECT key, value FROM metadata")}


def _dead_indexd(calls: list[dict]):
    """The RPC a web process would use to reach `indexd`, with `indexd` not running.

    Recording the calls is the measurement: an empty list proves the snapshot-serving
    branch never made a per-query round trip to its producer.
    """

    def requester(payload: dict) -> dict:
        calls.append(dict(payload))
        return {"ok": False, "status": "unavailable", "reason": "indexd is not running", "terminal": True}

    return requester


def _follower_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, list[dict]]:
    """A root with a persisted snapshot, read as a follower.  Producer still alive."""

    _clear_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    root = tmp_path / "root"
    root.mkdir()
    ghost = root / GHOST_NAME
    ghost.write_text("indexed, then deleted\n", encoding="utf-8")
    _build_persisted_snapshot(root)
    _clear_registry()
    # Every web process is a follower: app.py:2920-2922 returns False for this role.
    file_index.set_background_owner_checker(lambda _role: False)
    indexd_calls: list[dict] = []
    file_index.set_background_index_search_requester(_dead_indexd(indexd_calls))
    return root, ghost, indexd_calls


def _follower_root_with_stale_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, list[dict]]:
    """A root whose only Quick Open answer is a 200-day-old snapshot from a DEAD producer.

    Returns (root, ghost_path, indexd_calls).  The snapshot contains a file that has since
    been deleted and is missing a file that has since been created, so "stale" here is a
    content fact, not just a timestamp.
    """

    _clear_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    root = tmp_path / "root"
    root.mkdir()
    ghost = root / GHOST_NAME
    ghost.write_text("indexed, then deleted\n", encoding="utf-8")

    _build_persisted_snapshot(root)
    built_at = _age_persisted_snapshot(root, ANCIENT_SNAPSHOT_AGE_SECONDS)
    _kill_producer(root, built_at=built_at)

    # The tree moved on after the snapshot was written.
    ghost.unlink()
    (root / FRESH_NAME).write_text("created after the snapshot\n", encoding="utf-8")

    _clear_registry()
    file_index.set_background_owner_checker(lambda _role: False)
    indexd_calls: list[dict] = []
    file_index.set_background_index_search_requester(_dead_indexd(indexd_calls))
    return root, ghost, indexd_calls


@pytest.fixture(autouse=True)
def _restore_index_seams():
    yield
    file_index.set_background_owner_checker(None)
    file_index.set_background_index_search_requester(None)
    file_index.set_background_owner_refresh_requester(None)
    _clear_registry()


# --------------------------------------------------------------------------------------
# (c) The shape predicate, and the freshness sibling that now gates authority.
# --------------------------------------------------------------------------------------


def test_fixed_shape_predicate_stays_pure_and_freshness_answers_separately(tmp_path, monkeypatch):
    """The shape match is unchanged - and is no longer the thing that grants authority.

    `_sqlite_metadata_matches` still answers only "can I read these rows as this root's
    index", which is what keeps a stale snapshot SERVABLE.  A snapshot backdated 200 days
    with a dead producer still passes it byte-for-byte identically to a fresh one; what
    changed is that passing it no longer means ready/full.
    """

    _clear_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    root = tmp_path / "root"
    root.mkdir()
    (root / "notes.md").write_text("notes\n", encoding="utf-8")
    _build_persisted_snapshot(root)

    skip_dirs = filesystem.SEARCH_SKIP_DIRS
    signature = filesystem.SEARCH_SECRET_EXCLUDE_SIGNATURE
    fresh_metadata = _persisted_metadata(root)
    assert file_index._sqlite_metadata_matches(fresh_metadata, root, skip_dirs, signature) is True
    assert file_index.index_freshness(None, root, skip_dirs, signature).authoritative is True

    built_at = _age_persisted_snapshot(root, ANCIENT_SNAPSHOT_AGE_SECONDS)
    dead_epoch = _kill_producer(root, built_at=built_at)
    ancient_metadata = _persisted_metadata(root)
    age_seconds = time.time() - float(ancient_metadata["built_at"])
    assert age_seconds > 199.0 * ONE_DAY_SECONDS
    assert file_index._index_disk_path(root).stat().st_mtime == pytest.approx(built_at, abs=2.0)

    # Readability is unchanged: the rows are still reachable, on every reader.
    assert file_index._sqlite_metadata_matches(ancient_metadata, root, skip_dirs, signature) is True
    assert file_index._read_sqlite_index(root, skip_dirs, signature) is not None
    assert file_index._load_disk_metadata(root, skip_dirs, signature) is not None
    # The two metadata dicts now differ in the producer identity as well as the age, and
    # both of those are inputs to the freshness verdict.
    differing = {key for key in set(fresh_metadata) | set(ancient_metadata) if fresh_metadata.get(key) != ancient_metadata.get(key)}
    assert differing == {"built_at", "producer_epoch"}

    # Authority is answered by the sibling, and it says no.
    freshness = file_index.index_freshness(None, root, skip_dirs, signature)
    assert freshness.authoritative is False
    assert freshness.state == file_index.FRESHNESS_ORPHANED
    assert freshness.reason == "producer_not_running"
    assert freshness.producer_epoch == dead_epoch
    assert freshness.producer_state == file_index.PRODUCER_NOT_RUNNING
    assert freshness.snapshot_age_seconds > 199.0 * ONE_DAY_SECONDS


def test_contract_authoritative_check_must_reject_an_unvouchable_snapshot(tmp_path, monkeypatch):
    """CONTRACT: shape match is necessary, not sufficient.

    Accepting a snapshot as authoritative additionally requires a freshness proof - an
    epoch that a currently-live producer still owns, and custody within bound.  A
    200-day-old snapshot with no live producer must not validate as authoritative.
    (Serving it is still allowed; that goes through the explicitly stale path, not the
    authoritative one - see the read-only serving tests below.)
    """

    _clear_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    root = tmp_path / "root"
    root.mkdir()
    (root / "notes.md").write_text("notes\n", encoding="utf-8")
    _build_persisted_snapshot(root)
    built_at = _age_persisted_snapshot(root, ANCIENT_SNAPSHOT_AGE_SECONDS)
    _kill_producer(root, built_at=built_at)
    _clear_registry()

    accepted = file_index.index_freshness(
        None,
        root,
        filesystem.SEARCH_SKIP_DIRS,
        filesystem.SEARCH_SECRET_EXCLUDE_SIGNATURE,
    ).authoritative
    assert accepted is False, "a 200-day-old snapshot with no live producer must not validate as authoritative"


# --------------------------------------------------------------------------------------
# Age alone is wrong in BOTH directions.  Both directions get a test.
# --------------------------------------------------------------------------------------


def test_an_old_snapshot_from_a_live_idle_producer_stays_authoritative(tmp_path, monkeypatch):
    """40 minutes old, past INDEX_TTL_SECONDS, but the producer is alive and still vouching.

    This is the direction a pure age bound gets wrong.  The producer heartbeat, not
    `built_at`, is what proves current custody.
    """

    _clear_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    root = tmp_path / "root"
    root.mkdir()
    (root / "notes.md").write_text("notes\n", encoding="utf-8")
    _build_persisted_snapshot(root)
    built_at = _age_persisted_snapshot(root, 40.0 * 60.0)
    assert 40.0 * 60.0 > file_index.INDEX_TTL_SECONDS
    # The live producer refreshed its custody claim a moment ago without rebuilding.
    _write_producer_heartbeat(root, file_index.self_process_epoch(), time.time())
    file_index.reset_producer_liveness_cache()
    _clear_registry()
    file_index.set_background_owner_checker(lambda _role: False)

    freshness = file_index.index_freshness(None, root, filesystem.SEARCH_SKIP_DIRS, filesystem.SEARCH_SECRET_EXCLUDE_SIGNATURE)

    assert freshness.built_at == pytest.approx(built_at, abs=2.0)
    assert freshness.snapshot_age_seconds > file_index.INDEX_TTL_SECONDS
    assert freshness.producer_state == file_index.PRODUCER_RUNNING
    assert freshness.state == file_index.FRESHNESS_FRESH
    assert freshness.authoritative is True


def test_negative_control_in_bound_age_with_a_dead_producer_is_not_authoritative(tmp_path, monkeypatch):
    """NEGATIVE CONTROL: a 10-second-old snapshot whose producer died 5 seconds later.

    Every age term is comfortably inside every bound, so an age-only rule would call this
    fresh.  The producer-liveness term is the only thing that can catch it.
    """

    _clear_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    root = tmp_path / "root"
    root.mkdir()
    (root / "notes.md").write_text("notes\n", encoding="utf-8")
    _build_persisted_snapshot(root)
    built_at = _age_persisted_snapshot(root, 10.0)
    # The producer heartbeat is 5 seconds old - well inside PRODUCER_VOUCH_MAX_AGE_SECONDS.
    _kill_producer(root, built_at=time.time() - 5.0)
    _clear_registry()
    file_index.set_background_owner_checker(lambda _role: False)

    freshness = file_index.index_freshness(None, root, filesystem.SEARCH_SKIP_DIRS, filesystem.SEARCH_SECRET_EXCLUDE_SIGNATURE)

    assert freshness.built_at == pytest.approx(built_at, abs=2.0)
    assert freshness.snapshot_age_seconds < 60.0
    assert freshness.vouched_age_seconds is None
    assert freshness.shape_matches is True, "the rows are readable; only the vouch is missing"
    assert freshness.producer_state == file_index.PRODUCER_NOT_RUNNING
    assert freshness.state == file_index.FRESHNESS_ORPHANED
    assert freshness.authoritative is False


def test_negative_control_a_recorded_epoch_for_a_dead_process_is_not_live(tmp_path, monkeypatch):
    """NEGATIVE CONTROL: a live-looking epoch string does not make the process exist.

    Two ways a recorded epoch can be a lie, both proven here: the PID is gone entirely,
    and the PID is alive but was REUSED by a different process (a bare PID check would
    accept the second one).
    """

    absent = _absent_pid()
    absent_epoch = f"{absent}:proc:4242"
    assert file_index.process_epoch_is_live(absent_epoch) is False

    reused = _reused_pid_producer_epoch()
    os.kill(os.getpid(), 0)  # the PID in that epoch IS alive
    assert file_index.process_epoch_is_live(reused) is False, "a reused PID is not the recorded producer"

    assert file_index.process_epoch_is_live(file_index.self_process_epoch()) is True
    assert file_index.process_epoch_is_live("") is False
    assert file_index.process_epoch_is_live("1:proc:1") is False

    # ...and the payload built on it refuses to claim ready.
    _clear_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    root = tmp_path / "root"
    root.mkdir()
    (root / GHOST_NAME).write_text("rows\n", encoding="utf-8")
    _build_persisted_snapshot(root)
    _kill_producer(root, built_at=time.time(), epoch=reused)
    _clear_registry()
    file_index.set_background_owner_checker(lambda _role: False)

    payload = filesystem.search_files(str(root), query=SENTINEL_QUERY, limit=50, recursive=True)

    assert payload["files"], "the rows are still served"
    assert payload["index_state"] == "follower-stale"
    assert payload["producer_state"] == file_index.PRODUCER_NOT_RUNNING


# --------------------------------------------------------------------------------------
# (a) The incident: stale data presented as authoritative, producer never consulted.
# --------------------------------------------------------------------------------------


def test_fixed_follower_labels_a_stale_snapshot_instead_of_claiming_ready_full(tmp_path, monkeypatch):
    """This is the exact 7771 incident, now labelled instead of hidden.

    A follower (every web process) with a 200-day-old snapshot on disk and no running
    `indexd` still SERVES the snapshot - refusing to answer is worse - but reports
    `follower-stale` with `unverified` coverage, an explicit reason, the snapshot age and
    the producer state.  It still asks its producer nothing per query.
    """

    root, ghost, indexd_calls = _follower_root_with_stale_snapshot(tmp_path, monkeypatch)
    payload = filesystem.search_files(str(root), query=SENTINEL_QUERY, limit=50, recursive=True)

    # The rows are still served: the snapshot is useful even when it cannot be vouched for.
    names = [entry["name"] for entry in payload["files"]]
    assert names == [GHOST_NAME]
    assert not ghost.exists()
    assert (root / FRESH_NAME).exists()

    # ...and the payload no longer claims to be current about the tree it just described.
    assert payload["index_state"] == "follower-stale"
    assert payload["index_coverage"] == "unverified"
    assert payload["stale"] is True
    assert payload["freshness"] == file_index.FRESHNESS_ORPHANED
    assert payload["freshness_reason"] == "producer_not_running"
    assert payload["producer_state"] == file_index.PRODUCER_NOT_RUNNING
    assert payload["snapshot_age_seconds"] > 199.0 * ONE_DAY_SECONDS

    # The proof cost no round trip to the producer: it is a file read plus a /proc check.
    assert indexd_calls == [], "the freshness proof must not become a per-query RPC"

    # The Quick Open UI now has something to render inline.
    assert [field for field in FRESHNESS_FIELDS if field in payload] != []


def test_contract_stale_snapshot_must_not_be_served_as_ready_and_full(tmp_path, monkeypatch):
    """CONTRACT: a snapshot the process cannot vouch for is labelled.

    The result may still be served - refusing to answer is worse - but it must not claim
    `follower-ready` + `full`, and it must carry explicit stale/degraded metadata that the
    Quick Open UI can render inline.
    """

    root, _ghost, _indexd_calls = _follower_root_with_stale_snapshot(tmp_path, monkeypatch)
    payload = filesystem.search_files(str(root), query=SENTINEL_QUERY, limit=50, recursive=True)

    present = [field for field in FRESHNESS_FIELDS if field in payload]
    assert present, f"a stale snapshot payload must carry one of {FRESHNESS_FIELDS}; got keys {sorted(payload)}"
    assert (payload["index_state"], payload["index_coverage"]) != ("follower-ready", "full"), (
        "a 200-day-old snapshot from a dead producer must not be reported as ready with full coverage"
    )


def test_positive_control_live_producer_snapshot_is_still_served_as_ready_and_full(tmp_path, monkeypatch):
    """The other half of the contract: a vouched snapshot must NOT be degraded.

    Without this, "never claim ready" would pass by never claiming anything.
    """

    root, _ghost, indexd_calls = _follower_root(tmp_path, monkeypatch)
    payload = filesystem.search_files(str(root), query=SENTINEL_QUERY, limit=50, recursive=True)

    assert [entry["name"] for entry in payload["files"]] == [GHOST_NAME]
    assert payload["index_state"] == "follower-ready"
    assert payload["index_coverage"] == "full"
    assert payload["stale"] is False
    assert payload["freshness"] == file_index.FRESHNESS_FRESH
    assert payload["producer_state"] == file_index.PRODUCER_RUNNING
    assert indexd_calls == []


# --------------------------------------------------------------------------------------
# (b) `refreshing_elsewhere` must name an observed refresher.
# --------------------------------------------------------------------------------------


def _refreshing_elsewhere_payload(site: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[dict, list[dict]]:
    """Drive one previously-unproven `refreshing_elsewhere` site as a follower with dead indexd."""

    root, _ghost, indexd_calls = _follower_root_with_stale_snapshot(tmp_path, monkeypatch)
    if site == "search.py:525":
        # Manifest gone, sqlite intact: `disk_metadata_ready` stays False so search.py:468
        # is skipped and the fallback read at search.py:504-527 answers instead.
        file_index._index_manifest_path(root).unlink()
        _clear_registry()
        file_index.set_background_owner_checker(lambda _role: False)
    if site == "search.py:640":
        # Empty query on a full-tree root: the recent-slice branch, search.py:625-641.
        return filesystem.search_files(str(root), query="", limit=50, recursive=True), indexd_calls
    return filesystem.search_files(str(root), query=SENTINEL_QUERY, limit=50, recursive=True), indexd_calls


@pytest.mark.parametrize("site", ("search.py:497", "search.py:525", "search.py:640"))
def test_fixed_refreshing_elsewhere_is_false_with_nothing_refreshing(site, tmp_path, monkeypatch):
    """`refreshing_elsewhere` is a claim about another process, and now needs proof.

    In each of these three branches the only possible refresher is `indexd`, which is not
    running.  The flag is now derived from the freshness record, so it reads False and the
    payload says why - without the producer being asked anything.
    """

    payload, indexd_calls = _refreshing_elsewhere_payload(site, tmp_path, monkeypatch)

    # Discriminator: :497 and :525 emit an identical payload shape, so pin which branch ran.
    root = Path(payload["root"])
    manifest_loaded = file_index._load_disk_metadata(root, filesystem.SEARCH_SKIP_DIRS, filesystem.SEARCH_SECRET_EXCLUDE_SIGNATURE)
    assert (manifest_loaded is None) is (site == "search.py:525")

    assert payload["refreshing_elsewhere"] is False
    assert payload["refresh_requested"] is False
    assert payload["index_state"] == "follower-stale"
    assert payload["freshness_reason"] == "producer_not_running"
    assert indexd_calls == [], "no producer was consulted"
    assert payload["files"], "the branch under test must actually have served snapshot rows"


@pytest.mark.parametrize("site", ("search.py:497", "search.py:525", "search.py:640"))
def test_contract_refreshing_elsewhere_requires_a_live_refresher(site, tmp_path, monkeypatch):
    """CONTRACT: the flag means a refresh is actually in flight.

    With `indexd` down, no branch may assert that someone else is refreshing.  The correct
    value here is False (or the field omitted) plus an explicit degraded/stale reason.
    """

    payload, _indexd_calls = _refreshing_elsewhere_payload(site, tmp_path, monkeypatch)

    assert payload.get("refreshing_elsewhere", False) is False, (
        "refreshing_elsewhere must be derived from an observed live refresher, not from being a follower"
    )


def test_negative_control_refreshing_elsewhere_needs_both_a_live_producer_and_an_accepted_refresh(tmp_path, monkeypatch):
    """NEGATIVE CONTROL: each term of `refreshing_elsewhere` is separately load-bearing.

    Four combinations of (producer live, refresh accepted) are driven through
    `search.py:553` - the site whose old guard, `not refresh_result.get("fallback")`,
    passed even with no requester wired at all, because
    `request_background_owner_refresh` returns `{"accepted": False, "fallback": False}`
    in that case.
    """

    _clear_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    root = tmp_path / "root"
    root.mkdir()
    (root / "notes.md").write_text("notes\n", encoding="utf-8")
    file_index.set_background_owner_checker(lambda _role: False)
    live_epoch = file_index.self_process_epoch()
    dead_epoch = _dead_producer_epoch()

    def payload_for(*, producer_epoch: str, accepting: bool) -> dict:
        _clear_registry()
        _write_producer_heartbeat(root, producer_epoch, time.time())
        file_index.reset_producer_liveness_cache()
        if accepting:
            file_index.set_background_owner_refresh_requester(lambda _role, _payload: {"ok": True, "accepted": True, "fallback": False})
        else:
            # No requester at all: `fallback` is falsy, which the old guard read as proof.
            file_index.set_background_owner_refresh_requester(None)
        return filesystem.search_files(str(root), query="notes", limit=20, recursive=True)

    neither = payload_for(producer_epoch=dead_epoch, accepting=False)
    assert neither["index_state"] == "follower"
    assert neither["refreshing_elsewhere"] is False
    assert neither["refresh_requested"] is False

    accepted_only = payload_for(producer_epoch=dead_epoch, accepting=True)
    assert accepted_only["refresh_requested"] is True, "the acceptance term must be true here"
    assert accepted_only["producer_state"] == file_index.PRODUCER_NOT_RUNNING
    assert accepted_only["refreshing_elsewhere"] is False, "an accepted refresh from a dead producer is not a refresh in flight"

    live_only = payload_for(producer_epoch=live_epoch, accepting=False)
    assert live_only["producer_state"] == file_index.PRODUCER_RUNNING, "the liveness term must be true here"
    assert live_only["refresh_requested"] is False
    assert live_only["refreshing_elsewhere"] is False, "a live but idle producer is not refreshing this root"

    both = payload_for(producer_epoch=live_epoch, accepting=True)
    assert both["producer_state"] == file_index.PRODUCER_RUNNING
    assert both["refresh_requested"] is True
    assert both["refreshing_elsewhere"] is True, "with both proofs the flag must still be able to be True"


def test_fixed_warming_owner_does_not_claim_a_refresh_elsewhere(tmp_path, monkeypatch):
    """search.py:605 - the owner path.

    A build-capable process with no snapshot for `root` serves rows out of an already
    persisted CHILD snapshot.  The only refresh that could exist is this very process's
    own build, which is "here", not "elsewhere" - and here it has not even started.
    """

    _clear_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    root = tmp_path / "root"
    child = root / "notes"
    child.mkdir(parents=True)
    target = child / GHOST_NAME
    target.write_text("child snapshot row\n", encoding="utf-8")
    _build_persisted_snapshot(child)
    _age_persisted_snapshot(child, ANCIENT_SNAPSHOT_AGE_SECONDS)
    _clear_registry()

    # Freeze the process in the window before its own build thread runs, so no refresh
    # exists anywhere while the payload below is produced.
    builds: list[Path] = []
    monkeypatch.setattr(file_index, "_start_build", lambda ri, *_args, **_kwargs: builds.append(ri.root))
    file_index.set_background_owner_checker(lambda _role: True)

    payload = filesystem.search_files(str(root), query=SENTINEL_QUERY, limit=50, recursive=True)

    assert [entry["path"] for entry in payload["files"]] == [str(target)]
    assert payload["index_state"] == "warming"
    assert payload["index_coverage"] == "partial"
    assert payload["refreshing_elsewhere"] is False
    assert payload["refresh_requested"] is False
    assert builds == [root], "the only refresh in existence is this process's own, not elsewhere"


def test_contract_warming_owner_must_not_claim_a_refresh_elsewhere(tmp_path, monkeypatch):
    """CONTRACT: a process's own warming build is not a refresh 'elsewhere'."""

    _clear_registry()
    monkeypatch.setattr(file_index, "INDEX_DIR", tmp_path / "idx")
    root = tmp_path / "root"
    child = root / "notes"
    child.mkdir(parents=True)
    (child / GHOST_NAME).write_text("child snapshot row\n", encoding="utf-8")
    _build_persisted_snapshot(child)
    _clear_registry()
    monkeypatch.setattr(file_index, "_start_build", lambda *_args, **_kwargs: None)
    file_index.set_background_owner_checker(lambda _role: True)

    payload = filesystem.search_files(str(root), query=SENTINEL_QUERY, limit=50, recursive=True)

    assert payload.get("refreshing_elsewhere", False) is False, (
        "the owner's own in-process warming build must not be reported as a refresh elsewhere"
    )


# --------------------------------------------------------------------------------------
# The role predicate that used to wear a freshness name (search.py:850).
# --------------------------------------------------------------------------------------


def test_fixed_index_status_refreshing_elsewhere_is_not_a_role_predicate(tmp_path, monkeypatch):
    """`search.py:850` used to compute `refreshing_elsewhere` as `state == "follower"`.

    `state` still reports the role, because that is a real and separate fact.  The two
    freshness claims beside it - `ready_elsewhere` and `refreshing_elsewhere` - now come
    from the freshness record, and the `age` this projection always computed is now an
    input to a verdict rather than a number nothing reads.
    """

    root, _ghost, indexd_calls = _follower_root_with_stale_snapshot(tmp_path, monkeypatch)
    status = filesystem.index_status(str(root))

    assert status["state"] == "follower", "the role predicate is unchanged"
    assert status["refreshing_elsewhere"] is False
    assert status["ready_elsewhere"] is False
    assert status["freshness"] == file_index.FRESHNESS_ORPHANED
    assert status["freshness_reason"] == "producer_not_running"
    assert status["age"] > 199.0 * ONE_DAY_SECONDS
    assert status["snapshot_age_seconds"] == pytest.approx(status["age"], rel=1e-6)
    assert indexd_calls == []


def test_index_status_ready_elsewhere_survives_for_a_vouched_snapshot(tmp_path, monkeypatch):
    """Positive control for the same projection: a live producer keeps `ready_elsewhere`."""

    root, _ghost, _indexd_calls = _follower_root(tmp_path, monkeypatch)
    status = filesystem.index_status(str(root))

    assert status["state"] == "follower"
    assert status["ready_elsewhere"] is True
    assert status["freshness"] == file_index.FRESHNESS_FRESH
    assert status["producer_state"] == file_index.PRODUCER_RUNNING

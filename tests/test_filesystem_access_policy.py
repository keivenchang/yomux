# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Cross-port confused-deputy contract for shared-jobd filesystem execution.

A filesystem job descriptor carries `op`, `path` and `args`.  Nothing on it says which server
accepted the request, so the shared `jobd` daemon authorizes the path with `YOLOMUX_FS_ROOTS` from
its own process environment -- the environment of whichever server launched it first.  Two servers
on two ports with different configured roots therefore do not get their own access policy: they get
the launcher's.  A restricted caller's own read returns `403 fs.error.outsideRoots`, and the very
same descriptor executed by a jobd launched under a broader policy returns the file contents.

These tests execute the real registered task function with the daemon's environment installed,
which is exactly what the worker process does with the descriptor the accepting server built.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from yolomux_lib import app as app_module
from yolomux_lib import filesystem
from yolomux_lib.filesystem import paths
from yolomux_lib.infra import jobd

BLOCKED_SENTINEL = "BLOCKED_SENTINEL_DO_NOT_EXPOSE"


def _use_roots(monkeypatch, root: Path) -> None:
    """Install one server's (or one daemon's) configured filesystem roots."""
    monkeypatch.setenv(paths.FS_ROOTS_ENV, str(root))
    paths.invalidate_path_policy_caches()


@pytest.fixture
def two_policies(monkeypatch, tmp_path):
    """A broad root that contains a narrow root plus one file only the broad root admits."""
    broad = Path(str(tmp_path)).resolve()
    narrow = broad / "narrow"
    narrow.mkdir()
    (narrow / "own.txt").write_text("narrow caller's own file\n", encoding="utf-8")
    outside = broad / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text(f"{BLOCKED_SENTINEL}\n", encoding="utf-8")
    try:
        yield broad, narrow, secret
    finally:
        paths.invalidate_path_policy_caches()


def _descriptor(operation: str, path: Path) -> bytes:
    """Build the descriptor exactly as the accepting server's HTTP path builds it."""
    payload, _product_key = app_module.filesystem_operation_submission(
        operation,
        str(path),
        {},
        scope="local",
        generation="watchd:test:1",
    )
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _refusal_or_leak(descriptor: bytes) -> jobd.JobdFilesystemOperationFailure:
    """Execute the descriptor in the daemon's environment; fail loudly with any leaked bytes."""
    try:
        body = jobd.run_registered_task("filesystem_operation", descriptor)
    except jobd.JobdFilesystemOperationFailure as refusal:
        return refusal
    leaked = json.loads(body.decode("utf-8"))
    pytest.fail(
        "jobd executed a descriptor its accepting server's policy refuses and returned "
        f"content={leaked.get('content')!r}"
    )


def test_broad_daemon_must_not_execute_a_narrow_callers_descriptor(monkeypatch, two_policies):
    """A restricted server's descriptor keeps the restricted policy in a broader daemon."""

    broad, narrow, secret = two_policies

    _use_roots(monkeypatch, narrow)
    with pytest.raises(filesystem.FilesystemError) as direct:
        filesystem.read_file(str(secret))
    assert direct.value.status == 403
    assert direct.value.message_key == "fs.error.outsideRoots"
    descriptor = _descriptor("read", secret)

    _use_roots(monkeypatch, broad)
    refusal = _refusal_or_leak(descriptor)
    assert refusal.status == 403
    assert refusal.payload.get("user_message", {}).get("key") == "fs.error.outsideRoots"
    assert BLOCKED_SENTINEL not in json.dumps(refusal.payload)


def test_narrow_daemon_must_not_deny_a_broad_callers_descriptor(monkeypatch, two_policies):
    """The fix may not be 'take the most restrictive policy present'."""

    broad, narrow, secret = two_policies

    _use_roots(monkeypatch, broad)
    assert BLOCKED_SENTINEL in filesystem.read_file(str(secret))["content"]
    descriptor = _descriptor("read", secret)

    _use_roots(monkeypatch, narrow)
    result = json.loads(jobd.run_registered_task("filesystem_operation", descriptor).decode("utf-8"))
    assert BLOCKED_SENTINEL in result["content"]


def test_a_descriptor_without_an_access_policy_is_refused(monkeypatch, two_policies):
    """Negative control: absent policy denies; it never falls back to the daemon environment."""

    broad, _narrow, secret = two_policies

    _use_roots(monkeypatch, broad)
    descriptor = json.dumps({"op": "read", "path": str(secret), "args": {}}).encode("utf-8")
    refusal = _refusal_or_leak(descriptor)
    assert refusal.status == 403
    assert refusal.payload["diagnostic"] == "filesystem access policy refused: policy_missing"
    assert BLOCKED_SENTINEL not in json.dumps(refusal.payload)


@pytest.mark.parametrize(("policy", "reason"), [
    (None, "policy_missing"),
    ("/tmp", "policy_malformed"),
    ({"roots": ["/"], "digest": ""}, "policy_version_invalid"),
    ({"version": True, "roots": ["/"], "digest": ""}, "policy_version_invalid"),
    ({"version": paths.FS_ACCESS_POLICY_VERSION + 1, "roots": ["/"], "digest": ""}, "policy_version_mismatch"),
    ({"version": paths.FS_ACCESS_POLICY_VERSION, "roots": "/", "digest": ""}, "policy_roots_invalid"),
    ({"version": paths.FS_ACCESS_POLICY_VERSION, "roots": ["relative"], "digest": ""}, "policy_roots_invalid"),
    ({"version": paths.FS_ACCESS_POLICY_VERSION, "roots": ["/"], "digest": "wrong"}, "policy_digest_mismatch"),
])
def test_every_unusable_policy_fails_closed(monkeypatch, two_policies, policy, reason):
    """Missing, malformed, wrong-version and tampered policies all deny -- none default open."""

    broad, _narrow, secret = two_policies

    _use_roots(monkeypatch, broad)
    descriptor = json.dumps({"op": "read", "path": str(secret), "args": {}, "access_policy": policy}).encode("utf-8")
    refusal = _refusal_or_leak(descriptor)
    assert refusal.status == 403
    assert refusal.payload["diagnostic"].startswith(f"filesystem access policy refused: {reason}")
    assert BLOCKED_SENTINEL not in json.dumps(refusal.payload)


def test_a_batch_without_an_access_policy_is_refused(monkeypatch, two_policies):
    """The Finder batch product is the same shared-daemon boundary and fails closed too."""

    broad, _narrow, secret = two_policies

    _use_roots(monkeypatch, broad)
    with pytest.raises(filesystem.FilesystemError) as refusal:
        filesystem.filesystem_batch_result({
            "requests": [{"id": 0, "type": "info", "path": str(secret)}],
        })
    assert refusal.value.status == 403
    assert refusal.value.diagnostic == "filesystem access policy refused: policy_missing"


def test_a_broad_daemon_must_not_execute_a_narrow_callers_batch(monkeypatch, two_policies):
    """The batch boundary keeps the accepting server's policy, exactly like a single operation."""

    broad, narrow, secret = two_policies

    _use_roots(monkeypatch, narrow)
    batch_payload, _key, _ids = app_module.filesystem_batch_submission(
        {"requests": [{"id": 0, "type": "info", "path": str(secret)}], "client_scope": "browser"},
        key_prefix="fs-batch",
    )

    _use_roots(monkeypatch, broad)
    response = jobd.run_registered_task(
        "filesystem_batch",
        json.dumps(batch_payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
    )
    result = json.loads(response.decode("utf-8"))["responses"][0]
    assert result["ok"] is False
    assert result["status"] == 403
    assert result["user_message"]["key"] == "fs.error.outsideRoots"


def test_two_policies_never_share_one_retained_product(monkeypatch, two_policies):
    """The policy is part of the coalescing identity, so a narrow server cannot read a broad
    server's already-retained answer for the same path."""

    broad, narrow, secret = two_policies

    _use_roots(monkeypatch, broad)
    _payload, broad_key = app_module.filesystem_operation_submission(
        "read", str(secret), {}, scope="local", generation="watchd:test:1",
    )
    _use_roots(monkeypatch, narrow)
    _payload, narrow_key = app_module.filesystem_operation_submission(
        "read", str(secret), {}, scope="local", generation="watchd:test:1",
    )
    assert broad_key != narrow_key

    _use_roots(monkeypatch, broad)
    _batch, broad_batch_key, _ids = app_module.filesystem_batch_submission(
        {"requests": [{"id": 0, "type": "info", "path": str(secret)}]}, key_prefix="fs-batch",
    )
    _use_roots(monkeypatch, narrow)
    _batch, narrow_batch_key, _ids = app_module.filesystem_batch_submission(
        {"requests": [{"id": 0, "type": "info", "path": str(secret)}]}, key_prefix="fs-batch",
    )
    assert broad_batch_key != narrow_batch_key


# Every operation-key literal under `tests/`, with the number of times it may appear and why it is
# not a filesystem job descriptor.  A descriptor built anywhere but
# `app.filesystem_operation_descriptor()` carries no access policy, and the shared worker refuses
# it.
#
# Test fixtures are in scope because they were the ones that got this wrong: three stand-ins for
# `filesystem_operation_http_payload`, `filesystem_operation_relay` and `fs_batch_http_payload`
# each hand-rolled their own descriptor, and eight filesystem routes answered 403/500.  The earlier
# version of this test read only `app.py` and `jobd.py`, so it could not see them.
#
# The count is part of the key on purpose: allowlisting a line by text alone would let a second,
# genuinely hand-built copy of that same line through.
#
# The scan skips the region between the two markers below.  This table, and the key it searches
# for, necessarily quote the very literal they govern, and would otherwise report themselves
# instead of the real offender.
_SCAN_SKIP_BEGIN = "DESCRIPTOR-ALLOWLIST-BEGIN"
_SCAN_SKIP_END = "DESCRIPTOR-ALLOWLIST-END"

# DESCRIPTOR-ALLOWLIST-BEGIN
_DESCRIPTOR_KEY = '"op":'
_DESCRIPTOR_LITERAL_ALLOWLIST: dict[tuple[str, str], tuple[int, str]] = {
    ("tests/test_app.py", '"op": "raw",'): (
        1, "expected-payload assertion; the same dict pins the access policy the relay must send",
    ),
    ("tests/test_app.py", '"op": "list",'): (
        1, "expected-payload assertion; the same dict pins the access policy the submission must send",
    ),
    ("tests/test_chat_store.py", '"op": self.label,'): (
        1, "chat-store operation label, unrelated to filesystem job descriptors",
    ),
    ("tests/test_filesystem_access_policy.py",
     'descriptor = json.dumps({"op": "read", "path": str(secret), "args": {}}).encode("utf-8")'): (
        1, "the absent-policy negative control; it must be built without a policy to prove denial",
    ),
    ("tests/test_filesystem_access_policy.py",
     'descriptor = json.dumps({"op": "read", "path": str(secret), "args": {}, "access_policy": policy}).encode("utf-8")'): (
        1, "the unusable-policy matrix; it must carry a deliberately broken policy",
    ),
    ("tests/test_filesystem_access_policy.py",
     '''assert '{"op":' not in source, f"{module.__name__} builds a filesystem descriptor inline"'''): (
        1, "this guard's own product-source assertion",
    ),
    ("tests/test_filesystem_access_policy.py", '''assert app_source.count('"op": str(operation),') == 1'''): (
        1, "this guard's own owner-uniqueness assertion",
    ),
    ("tests/test_gate_route_sweep.py",
     '"post_fs_batch": _json_body([{"id": "route-sweep-read", "op": "read", "path": str(fixture.text_file)}]),'): (
        1, "an HTTP request body; the real route captures the policy at accept time",
    ),
    ("tests/test_jobd.py", 'accepting server does rather than hand-rolling `{"op": ..., "path": ...}`.'): (
        1, "a docstring naming the forbidden shape",
    ),
    ("tests/test_jobd.py",
     'read = service._queue_record("filesystem_operation", {"op": "read"}, "point", 1, "point-read")'): (
        1, "lane classification only; `_queue_record` never executes the payload",
    ),
    ("tests/test_jobd.py",
     'index_status = service._queue_record("filesystem_operation", {"op": "index_status"}, "point", 1, "point-index")'): (
        1, "lane classification only; `_queue_record` never executes the payload",
    ),
    ("tests/test_jobd.py", '"task": "json_compact", "payload": {"op": "read", "path": "/repo/note.md"},'): (
        1, "a `json_compact` payload; it never reaches a filesystem operation",
    ),
    ("tests/test_jobd.py", '"payload": {"op": "read", "path": "/repo/note.md"},'): (
        1, "a `json_compact` payload; it never reaches a filesystem operation",
    ),
}
# DESCRIPTOR-ALLOWLIST-END


def test_one_owner_builds_every_filesystem_job_descriptor():
    """No second construction site, in product source OR a test fixture, may build a descriptor."""

    for module in (app_module, jobd):
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert '{"op":' not in source, f"{module.__name__} builds a filesystem descriptor inline"
    app_source = Path(app_module.__file__).read_text(encoding="utf-8")
    assert app_source.count("def filesystem_operation_descriptor(") == 1
    assert app_source.count('"op": str(operation),') == 1

    tests_root = Path(__file__).resolve().parent
    found: Counter[tuple[str, str]] = Counter()
    for path in sorted(tests_root.rglob("*.py")):
        relative = path.relative_to(tests_root.parent).as_posix()
        skipping = False
        for line in path.read_text(encoding="utf-8").splitlines():
            if _SCAN_SKIP_BEGIN in line:
                skipping = True
            elif _SCAN_SKIP_END in line:
                skipping = False
            elif not skipping and _DESCRIPTOR_KEY in line:
                found[(relative, line.strip())] += 1
    expected = {key: count for key, (count, _reason) in _DESCRIPTOR_LITERAL_ALLOWLIST.items()}
    unowned = sorted(f"{key[0]}: {key[1]}" for key, count in found.items() if expected.get(key) != count)
    stale = sorted(f"{key[0]}: {key[1]}" for key, count in expected.items() if found.get(key, 0) != count)
    assert not unowned and not stale, (
        "a test builds a filesystem job descriptor by hand: route it through "
        "app.filesystem_operation_descriptor(), or add it to _DESCRIPTOR_LITERAL_ALLOWLIST "
        f"with the reason it is not a descriptor\n  unowned: {unowned}\n  stale allowlist: {stale}"
    )

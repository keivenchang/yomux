# Two-host NFS shared-config lock acceptance

This is an operator-run acceptance procedure for `yolomux_lib.infra.shared_config_lock` on one NFS export. It is not pytest evidence and is incomplete until a person runs it on two hosts. A same-host result does not establish this property: the exporter-local and NFS-client kernel lock paths can differ.

## Pass rule

Use a disposable `auth.yaml`, never real configuration. PASS requires both lock directions, crash release, independent-key merge, same-key typed conflict, and complete parseable YAML throughout. Every lock scenario also requires event records proving the waiter called `fcntl.lockf(LOCK_EX)` while the other host still held its lock; a run without that overlap is INCONCLUSIVE, not PASS.

## Topology and preflight

`lin1` exports `/srv/yolomux-nfs-acceptance` and accesses that directory locally. `lin2` mounts the same export at `/mnt/yolomux-nfs-acceptance`. Use NFSv4.2 with `local_lock=none`; `local_lock=all`, `local_lock=flock`, or `local_lock=posix` can make a lock local-only and invalidate this run.

On `lin1`, create the disposable export. Replace `lin2.example.internal` only with the real client name.

```bash
set -euo pipefail
export ACCEPT_EXPORT=/srv/yolomux-nfs-acceptance
sudo install -d -m 0700 "$ACCEPT_EXPORT"
printf '%s\n' "$ACCEPT_EXPORT lin2.example.internal(rw,sync,no_subtree_check)" | sudo tee /etc/exports.d/yolomux-nfs-acceptance.exports
sudo exportfs -rav
sudo exportfs -v | rg -F "$ACCEPT_EXPORT"
```

Expected output names `$ACCEPT_EXPORT`, the intended client, and `sync`. A different client/path or no export is a FAIL before testing.

On `lin2`, mount and prove the negotiated topology.

```bash
set -euo pipefail
export ACCEPT_MOUNT=/mnt/yolomux-nfs-acceptance
sudo install -d -m 0700 "$ACCEPT_MOUNT"
sudo mount -t nfs4 -o rw,hard,nfsvers=4.2,local_lock=none lin1:/srv/yolomux-nfs-acceptance "$ACCEPT_MOUNT"
findmnt -T "$ACCEPT_MOUNT" -no SOURCE,FSTYPE,OPTIONS
nfsstat -m | sed -n "/$ACCEPT_MOUNT/,+2p"
```

Expected output identifies `lin1:/srv/yolomux-nfs-acceptance`, NFSv4, `vers=4.2`, and `local_lock=none`. Any other server/path, a local filesystem, omitted `local_lock=none`, or a non-v4 mount is FAIL or untested topology. If v4.2 is unavailable, stop, record the negotiated NFSv4 version, and rerun every scenario with that explicit `nfsvers` value.

Prove both hosts read one file: run the first block on `lin1`, then the second on `lin2`. The SHA-256 values must match.

```bash
export ACCEPT_ROOT=/srv/yolomux-nfs-acceptance
printf 'acceptance-probe: lin1\n' > "$ACCEPT_ROOT/auth.yaml"
sync
sha256sum "$ACCEPT_ROOT/auth.yaml"
```

```bash
export ACCEPT_ROOT=/mnt/yolomux-nfs-acceptance
sha256sum "$ACCEPT_ROOT/auth.yaml"
test "$(cat "$ACCEPT_ROOT/auth.yaml")" = 'acceptance-probe: lin1'
```

Different bytes mean the hosts are not exercising one shared file, so stop rather than interpret later lock output.

## Install the harness on both hosts

Both hosts must use the same YOLOmux commit. On each host, `cd` to that checkout, compare `git rev-parse HEAD`, set `ACCEPT_ROOT` to the host-local path above, and run this command. It writes only `/tmp/yolomux-nfs-lock-accept.py`; all acceptance data stays below `$ACCEPT_ROOT`.

```bash
cat > /tmp/yolomux-nfs-lock-accept.py <<'PY'
#!/usr/bin/env python3
import argparse, json, os, time
from pathlib import Path
import yaml
from yolomux_lib.infra.shared_config_lock import SharedConfigRevisionConflict, read_shared_document, shared_config_lock, update_shared_yaml, write_shared_document
p = argparse.ArgumentParser()
for name in ("root", "host", "tag", "operation"):
    p.add_argument(f"--{name}", required=True)
p.add_argument("--key", default="")
p.add_argument("--value", default="")
p.add_argument("--hold-seconds", type=float, default=0.0)
p.add_argument("--duration", type=float, default=0.0)
p.add_argument("--expected-revision", default="")
a = p.parse_args(); root = Path(a.root); path = root / "auth.yaml"; events = root / f"events.{a.host}.jsonl"
def event(name, **fields):
    row = {"event": name, "host": a.host, "tag": a.tag, "wall_ns": time.time_ns(), "pid": os.getpid(), **fields}
    with events.open("a", encoding="utf-8") as out: out.write(json.dumps(row, sort_keys=True) + "\n"); out.flush(); os.fsync(out.fileno())
    print(json.dumps(row, sort_keys=True), flush=True)
root.mkdir(parents=True, exist_ok=True)
if a.operation == "monitor":
    deadline = time.monotonic() + a.duration; checks = 0; event("monitor_started")
    while time.monotonic() < deadline:
        try:
            assert isinstance(yaml.safe_load(path.read_text(encoding="utf-8")), dict)
        except Exception as error:
            event("parse_failed", error=type(error).__name__); raise SystemExit(2)
        checks += 1; time.sleep(0.01)
    event("monitor_passed", checks=checks)
elif a.operation == "hold":
    event("wait_started")
    with shared_config_lock(path): event("lock_acquired"); time.sleep(a.hold_seconds); event("lock_releasing")
elif a.operation == "merge":
    if a.expected_revision: base = a.expected_revision
    else: _, base = read_shared_document(path)
    event("wait_started", expected_revision=base)
    with shared_config_lock(path):
        event("lock_acquired"); time.sleep(a.hold_seconds); result = update_shared_yaml(path, {a.key: a.value}, expected_revision=base); event("merge_finished", revision_conflict=result.revision_conflict)
elif a.operation == "replace":
    if a.expected_revision: base = a.expected_revision
    else: _, base = read_shared_document(path)
    event("wait_started", expected_revision=base)
    with shared_config_lock(path):
        event("lock_acquired"); time.sleep(a.hold_seconds)
        try: write_shared_document(path, yaml.safe_dump({a.key: a.value}, sort_keys=True), expected_revision=base)
        except SharedConfigRevisionConflict: event("revision_conflict")
        else: event("replace_finished")
PY
chmod 0700 /tmp/yolomux-nfs-lock-accept.py
python3 /tmp/yolomux-nfs-lock-accept.py --help | head -1
```

Expected output begins with `usage:`. An import or revision mismatch is a FAIL because the two hosts would test different code.

Run this reset on `lin1` before each scenario, then wait for the file to become visible on `lin2`.

```bash
export ACCEPT_ROOT=/srv/yolomux-nfs-acceptance
rm -f "$ACCEPT_ROOT"/events.lin1.jsonl "$ACCEPT_ROOT"/events.lin2.jsonl
printf 'base: true\n' > "$ACCEPT_ROOT/auth.yaml"
python3 - "$ACCEPT_ROOT/auth.yaml" <<'PY' > "$ACCEPT_ROOT/base-revision.txt"
import hashlib, sys
from pathlib import Path
print(hashlib.sha256(Path(sys.argv[1]).read_bytes()).hexdigest())
PY
sync
```

After every scenario, run this on either host. It is the proof that the harness genuinely contended rather than merely ran two non-overlapping commands.

```bash
python3 - "$ACCEPT_ROOT" <<'PY'
import json, sys, yaml
from pathlib import Path
root = Path(sys.argv[1]); rows = []
for file in root.glob("events.*.jsonl"): rows.extend(json.loads(line) for line in file.read_text(encoding="utf-8").splitlines() if line)
for row in sorted(rows, key=lambda row: row["wall_ns"]): print(row["host"], row["tag"], row["event"], row["wall_ns"], row.get("revision_conflict", ""))
value = yaml.safe_load((root / "auth.yaml").read_text(encoding="utf-8")); assert isinstance(value, dict), value
print(value)
PY
```

PASS overlap evidence is: holder `lock_acquired`, then waiter `wait_started`, then holder `lock_releasing`, then waiter `lock_acquired`. A different ordering is INCONCLUSIVE or FAIL; `parse_failed` or an assertion failure is always FAIL and means a torn or unreadable file was visible.

## Scenario 1: exporter-local holder blocks NFS-client waiter

Reset. Start this on `lin1`; after it prints `lock_acquired` and before 20 seconds elapse, start the second command on `lin2`.

```bash
export ACCEPT_ROOT=/srv/yolomux-nfs-acceptance
python3 /tmp/yolomux-nfs-lock-accept.py --root "$ACCEPT_ROOT" --host lin1 --tag exporter-holder --operation hold --hold-seconds 20 &
echo "exporter_holder_pid=$!"
```

```bash
export ACCEPT_ROOT=/mnt/yolomux-nfs-acceptance
python3 /tmp/yolomux-nfs-lock-accept.py --root "$ACCEPT_ROOT" --host lin2 --tag client-waiter --operation hold --hold-seconds 0
```

PASS is the required event ordering. Client acquisition before exporter release is FAIL: exporter-local POSIX locking does not exclude the NFS client.

## Scenario 2: NFS-client holder blocks exporter-local waiter

Reset. Start this on `lin2`; after `lock_acquired`, start the second command on `lin1` before 20 seconds elapse.

```bash
export ACCEPT_ROOT=/mnt/yolomux-nfs-acceptance
python3 /tmp/yolomux-nfs-lock-accept.py --root "$ACCEPT_ROOT" --host lin2 --tag client-holder --operation hold --hold-seconds 20 &
echo "client_holder_pid=$!"
```

```bash
export ACCEPT_ROOT=/srv/yolomux-nfs-acceptance
python3 /tmp/yolomux-nfs-lock-accept.py --root "$ACCEPT_ROOT" --host lin1 --tag exporter-waiter --operation hold --hold-seconds 0
```

PASS is the reverse required event ordering. Exporter acquisition before client release is FAIL; this is the direction a naive one-way test misses.

## Scenario 3: crash release

Reset. On `lin1`, start the holder in the background and record only its printed PID. Wait for its `lock_acquired`, start the `lin2` waiter while it holds, confirm the overlap events, then kill exactly that PID on `lin1`.

```bash
export ACCEPT_ROOT=/srv/yolomux-nfs-acceptance
python3 /tmp/yolomux-nfs-lock-accept.py --root "$ACCEPT_ROOT" --host lin1 --tag crash-holder --operation hold --hold-seconds 120 &
holder_pid=$!
echo "holder_pid=$holder_pid"
```

```bash
export ACCEPT_ROOT=/mnt/yolomux-nfs-acceptance
python3 /tmp/yolomux-nfs-lock-accept.py --root "$ACCEPT_ROOT" --host lin2 --tag crash-waiter --operation hold --hold-seconds 0
```

PASS is a waiter `lock_acquired` after the killed holder's earlier acquisition, without deleting any lock file. A waiter that remains blocked is FAIL; do not manually remove the lock file because that hides crash-release failure.

Once the `lin2` command has emitted `wait_started` but not `lock_acquired`, run this exact command on `lin1`.

```bash
kill -KILL "$holder_pid"
wait "$holder_pid" || test $? -eq 137
```

## Scenario 4: independent keys and no torn file

Reset. On `lin1`, read `BASE_REV` from the reset file and start the monitor and exporter update. On `lin2`, read the same `BASE_REV` before starting the client update. After the exporter emits `lock_acquired`, start the client before its five-second hold ends.

```bash
export ACCEPT_ROOT=/srv/yolomux-nfs-acceptance
BASE_REV=$(cat "$ACCEPT_ROOT/base-revision.txt")
python3 /tmp/yolomux-nfs-lock-accept.py --root "$ACCEPT_ROOT" --host lin1 --tag exporter-monitor --operation monitor --duration 20 &
exporter_monitor_pid=$!
python3 /tmp/yolomux-nfs-lock-accept.py --root "$ACCEPT_ROOT" --host lin1 --tag exporter-key --operation merge --key exporter_key --value exporter --hold-seconds 5 --expected-revision "$BASE_REV" &
echo "exporter_key_pid=$!"
```

```bash
export ACCEPT_ROOT=/mnt/yolomux-nfs-acceptance
BASE_REV=$(cat "$ACCEPT_ROOT/base-revision.txt")
python3 /tmp/yolomux-nfs-lock-accept.py --root "$ACCEPT_ROOT" --host lin2 --tag client-monitor --operation monitor --duration 20 &
client_monitor_pid=$!
python3 /tmp/yolomux-nfs-lock-accept.py --root "$ACCEPT_ROOT" --host lin2 --tag client-key --operation merge --key client_key --value client --hold-seconds 0 --expected-revision "$BASE_REV"
wait "$client_monitor_pid"
```

PASS requires both `merge_finished` events, `revision_conflict: true` for the second stale-base update, final keys `base`, `exporter_key`, and `client_key`, and `monitor_passed`. The conflict flag is expected; key-level merge must preserve both keys. A missing key, early acquisition, or parse failure is FAIL.

After the client command has returned, run this on `lin1` before inspecting events.

```bash
wait "$exporter_key_pid" "$exporter_monitor_pid"
```

## Scenario 5: same key and typed revision conflict

Reset and start the monitor as in scenario 4. Read the shared `BASE_REV` on both hosts before starting either writer, then start the exporter document writer and the client writer while the exporter holds.

```bash
export ACCEPT_ROOT=/srv/yolomux-nfs-acceptance
BASE_REV=$(cat "$ACCEPT_ROOT/base-revision.txt")
python3 /tmp/yolomux-nfs-lock-accept.py --root "$ACCEPT_ROOT" --host lin1 --tag exporter-monitor --operation monitor --duration 20 &
exporter_monitor_pid=$!
python3 /tmp/yolomux-nfs-lock-accept.py --root "$ACCEPT_ROOT" --host lin1 --tag exporter-replace --operation replace --key winner --value exporter --hold-seconds 5 --expected-revision "$BASE_REV" &
echo "exporter_replace_pid=$!"
```

```bash
export ACCEPT_ROOT=/mnt/yolomux-nfs-acceptance
BASE_REV=$(cat "$ACCEPT_ROOT/base-revision.txt")
python3 /tmp/yolomux-nfs-lock-accept.py --root "$ACCEPT_ROOT" --host lin2 --tag client-monitor --operation monitor --duration 20 &
client_monitor_pid=$!
python3 /tmp/yolomux-nfs-lock-accept.py --root "$ACCEPT_ROOT" --host lin2 --tag client-replace --operation replace --key winner --value client --hold-seconds 0 --expected-revision "$BASE_REV"
wait "$client_monitor_pid"
```

PASS requires serialized events, exactly one `replace_finished`, one `revision_conflict`, one final `winner`, and `monitor_passed`. The loser must receive `SharedConfigRevisionConflict`; a silent overwrite or parse failure is FAIL.

After the client command has returned, run this on `lin1` before inspecting events.

```bash
wait "$exporter_replace_pid" "$exporter_monitor_pid"
```

## Failure fallback and cleanup

If any scenario fails or is inconclusive, retain mount output, event files, and the final `auth.yaml` outside the repository. Use separate local `YOLOMUX_CONFIG_DIR` roots, a shared configuration mounted read-only, or one designated configuration writer. Do not cite same-host multiprocessing tests as cross-host evidence.

```bash
sudo umount /mnt/yolomux-nfs-acceptance
sudo rm -f /etc/exports.d/yolomux-nfs-acceptance.exports
sudo exportfs -rav
sudo rm -rf /srv/yolomux-nfs-acceptance
rm -f /tmp/yolomux-nfs-lock-accept.py
```

Run this acceptance when two machines genuinely need to update one writable configuration root. Otherwise the one-writer or separate-local-config model is cheaper and remains the supported choice until this evidence exists.

#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Move default YOmux data roots with an external recovery record."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
from typing import Any
import uuid

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.product_environment import product_env, product_env_name
from tools.instance_isolation import normalize_yomux_environment

MIGRATION_HOME = Path(".local/share/yomux-migrations")
LEGACY_ROOT_KEYS = {
    "config": product_env_name('CONFIG_DIR'),
    "state": product_env_name('STATE_DIR'),
    "cache": product_env_name('CACHE_DIR'),
    "ca": product_env_name('CA_DIR'),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def path_present(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def data_present(path: Path) -> bool:
    if not path_present(path):
        return False
    if path.is_symlink():
        return True
    return True if not path.is_dir() else any(path.iterdir())


def root_inventory(source: Path, target: Path, label: str) -> dict[str, Any]:
    source_is_symlink = source.is_symlink()
    source_present = data_present(source)
    target_present = data_present(target)
    if source_is_symlink:
        state = "source_symlink"
    elif source_present and target_present:
        state = "conflict_both_contain_data"
    elif source_present:
        state = "ready_to_move"
    elif target_present:
        state = "destination_only"
    else:
        state = "empty_or_missing"
    return {
        "label": label,
        "source": str(source),
        "target": str(target),
        "source_exists": path_present(source),
        "source_is_symlink": source_is_symlink,
        "target_exists": path_present(target),
        "source_has_data": source_present,
        "target_has_data": target_present,
        "state": state,
    }


def default_roots(home: Path, environ: dict[str, str]) -> list[dict[str, str]]:
    environ = normalize_yomux_environment(environ)
    if product_env('ROOT', environ=environ):
        return []
    roots = []
    definitions = (
        ("config", ".config/yolomux", ".config/yomux", "CONFIG_DIR"),
        ("state", ".local/state/yolomux", ".local/state/yomux", "STATE_DIR"),
        ("cache", ".cache/yolomux", ".cache/yomux", "CACHE_DIR"),
        ("ca", ".local/share/yolomux-ca", ".local/share/yomux-ca", "CA_DIR"),
    )
    for label, source, target, setting in definitions:
        if product_env(setting, environ=environ):
            continue
        if label == "cache" and environ.get("XDG_CACHE_HOME"):
            cache_home = Path(environ["XDG_CACHE_HOME"]).expanduser()
            if not cache_home.is_absolute():
                continue
            source = str(cache_home / "yolomux")
            target = str(cache_home / "yomux")
        roots.append({"label": label, "source": str(home / source), "target": str(home / target)})
    return roots


def active_writer_processes(
    proc_root: Path = Path("/proc"),
    own_pid: int | None = None,
    *,
    roots: list[dict[str, str]] | None = None,
    home: Path | None = None,
) -> list[dict[str, Any]]:
    """Return visible local YOmux server/worker processes that may write these roots."""
    matches: list[dict[str, Any]] = []
    if not proc_root.is_dir():
        if sys.platform == "darwin":
            try:
                result = subprocess.run(
                    ["ps", "-axo", "pid=,command="],
                    check=False,
                    capture_output=True,
                    timeout=5,
                )
            except (OSError, subprocess.TimeoutExpired):
                result = None
            if result is None or result.returncode != 0:
                return [{"pid": None, "command": "macOS process census unavailable; refusing migration"}]
            for line in os.fsdecode(result.stdout).splitlines():
                fields = line.strip().split(None, 1)
                if len(fields) != 2:
                    continue
                try:
                    pid = int(fields[0])
                except ValueError:
                    continue
                if pid == (os.getpid() if own_pid is None else own_pid) or not is_writer_command(fields[1]):
                    continue
                # `ps` cannot expose the writer's environment portably. Require every
                # YOmux writer to stop on macOS rather than guessing which roots it uses.
                try:
                    executable = Path(shlex.split(fields[1])[0]).name
                except (ValueError, IndexError):
                    executable = "YOmux process"
                matches.append({"pid": pid, "command": f"{executable} (YOmux writer; root scope unavailable on macOS)"})
            return matches
        return [{"pid": None, "command": f"process census unavailable at {proc_root}; refusing migration"}]
    current_pid = os.getpid() if own_pid is None else own_pid
    for entry in proc_root.iterdir():
        if not entry.name.isdigit() or int(entry.name) == current_pid:
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError as error:
            try:
                process_stat = entry.stat()
            except FileNotFoundError:
                continue
            except OSError:
                process_stat = None
            if process_stat is None or process_stat.st_uid == os.getuid():
                matches.append({"pid": int(entry.name), "command": f"YOmux process command line unavailable; refusing migration ({error.__class__.__name__})"})
            continue
        command = raw.replace(b"\0", b" ").decode("utf-8", errors="replace").strip()
        try:
            process_environment = parse_process_environment((entry / "environ").read_bytes())
        except OSError as error:
            process_environment = {}
            environment_error = error
        else:
            environment_error = None
        if is_writer_command(command) or configured_go_binary_matches(command, process_environment):
            if roots is not None:
                if environment_error is not None:
                    matches.append({"pid": int(entry.name), "command": "YOmux process environment unavailable; refusing migration"})
                    continue
                if not process_uses_migrated_roots(process_environment, roots, home):
                    continue
            matches.append({"pid": int(entry.name), "command": Path(shlex.split(command)[0]).name + " (YOmux writer)"})
    return matches


def parse_process_environment(raw: bytes) -> dict[str, str]:
    environment: dict[str, str] = {}
    for item in raw.split(b"\0"):
        key, separator, value = item.partition(b"=")
        if separator:
            environment[key.decode("utf-8", errors="replace")] = value.decode("utf-8", errors="replace")
    return environment


def process_uses_migrated_roots(
    environment: dict[str, str], roots: list[dict[str, str]], home: Path | None
) -> bool:
    environment = normalize_yomux_environment(environment)
    if not roots:
        return False
    selected = [Path(root[key]).resolve(strict=False) for root in roots for key in ("source", "target")]

    def overlaps(path: Path) -> bool:
        candidate = path.expanduser().resolve(strict=False)
        return any(candidate == root or candidate.is_relative_to(root) or root.is_relative_to(candidate) for root in selected)

    for setting in ("ROOT", "CONFIG_DIR", "STATE_DIR", "CACHE_DIR", "CA_DIR"):
        key = product_env_name(setting)
        value = environment.get(key, "")
        if value and overlaps(Path(value)):
            return True

    process_home = environment.get("HOME")
    if not process_home or home is None or Path(process_home).expanduser().resolve(strict=False) != home.resolve(strict=False):
        return False
    return any(
        overlaps(Path(root[key]))
        for root in default_roots(Path(process_home), environment)
        for key in ("source", "target")
    )


def is_writer_command(command: str) -> bool:
    """Match the executable or Python entry point, not arbitrary arguments or paths."""
    try:
        arguments = shlex.split(command)
    except ValueError:
        return False
    if not arguments:
        return False
    executable = Path(arguments[0]).name
    if executable in {"yomux-go", "yolomux-go", "yomux_go", "yolomux_go", "yomux", "yolomux"}:
        return True
    if executable.startswith("python"):
        if any(Path(argument).name in {"yomux.py", "yolomux.py"} for argument in arguments[1:]):
            return True
        return any(
            arguments[index + 1].startswith(("yomux_lib.", "yolomux_lib."))
            for index, argument in enumerate(arguments[:-1])
            if argument == "-m"
        )
    return False


def configured_go_binary_matches(command: str, environment: dict[str, str]) -> bool:
    """Recognize custom Go server binaries configured by the product launcher."""
    try:
        arguments = shlex.split(command)
    except ValueError:
        return False
    if not arguments:
        return False
    executable = Path(arguments[0]).expanduser().resolve(strict=False)
    configured = product_env('GO_BINARY', "", environ=environment).strip()
    return bool(configured) and executable == Path(configured).expanduser().resolve(strict=False)


def ensure_external(path: Path, roots: list[dict[str, str]]) -> None:
    resolved = path.resolve(strict=False)
    if resolved.is_relative_to(REPO_ROOT):
        raise ValueError(f"recovery data must be outside the repository: {resolved}")
    for root in roots:
        for key in ("source", "target"):
            candidate = Path(root[key]).resolve(strict=False)
            if resolved == candidate or resolved.is_relative_to(candidate):
                raise ValueError(f"recovery data must be outside migrated roots: {resolved}")


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_path = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(raw_path)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def tree_signature(root: Path) -> list[tuple[str, str, int]]:
    signature: list[tuple[str, str, int]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            signature.append((relative, "symlink:" + os.readlink(path), path.lstat().st_mode))
        elif path.is_file():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            signature.append((relative, digest, path.stat().st_mode))
        elif path.is_dir():
            signature.append((relative, "directory", path.stat().st_mode))
    return signature


def backup_root(source: Path, backup: Path) -> None:
    if source.is_symlink() or not source.is_dir():
        raise ValueError(f"source root must be a real directory: {source}")
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, backup, symlinks=True, copy_function=shutil.copy2)
    if tree_signature(source) != tree_signature(backup):
        raise OSError(f"backup verification failed for {source}")


def prepare_mapping(roots: list[dict[str, str]], mapping_path: Path) -> dict[str, Any]:
    stamp = utc_now()
    backup_root_dir = mapping_path.parent / "backups" / mapping_path.stem
    ensure_external(mapping_path, roots)
    ensure_external(backup_root_dir, roots)
    entries = []
    for root in roots:
        inventory = root_inventory(Path(root["source"]), Path(root["target"]), root["label"])
        entries.append({**root, "state": inventory["state"], "backup": None})
    return {
        "format": 1,
        "migration_id": mapping_path.stem,
        "created_at_utc": stamp,
        "cutover_at_utc": None,
        "state": "prepared",
        "entries": entries,
    }


def apply_mapping(mapping: dict[str, Any], mapping_path: Path) -> int:
    conflicts = [entry for entry in mapping["entries"] if entry["state"] in {"conflict_both_contain_data", "destination_only", "source_symlink"}]
    if conflicts:
        print("ERROR: data-root conflicts require manual resolution, including unsafe source roots; no roots were moved.", file=sys.stderr)
        print(json.dumps(conflicts, indent=2, sort_keys=True), file=sys.stderr)
        return 2
    mapping["state"] = "applying"
    write_json_atomic(mapping_path, mapping)
    backup_dir = mapping_path.parent / "backups" / mapping_path.stem
    try:
        for entry in mapping["entries"]:
            if entry["state"] != "ready_to_move":
                continue
            source = Path(entry["source"])
            target = Path(entry["target"])
            if path_present(target):
                raise FileExistsError(f"destination appeared after preflight: {target}")
            backup = backup_dir / entry["label"]
            backup_root(source, backup)
            entry["backup"] = str(backup)
            entry["state"] = "backed_up"
            write_json_atomic(mapping_path, mapping)
            target.parent.mkdir(parents=True, exist_ok=True)
            os.rename(source, target)
            entry["state"] = "moved"
            write_json_atomic(mapping_path, mapping)
    except (OSError, ValueError) as error:
        mapping["state"] = "partial_failure"
        mapping["error"] = str(error)
        write_json_atomic(mapping_path, mapping)
        print(f"ERROR: migration stopped with a recoverable mapping at {mapping_path}: {error}", file=sys.stderr)
        return 1
    mapping["state"] = "applied"
    mapping["cutover_at_utc"] = utc_now()
    write_json_atomic(mapping_path, mapping)
    print(f"Applied migration {mapping['migration_id']}; recovery mapping: {mapping_path}")
    return 0


def rollback_mapping(mapping: dict[str, Any], mapping_path: Path) -> int:
    try:
        for entry in reversed(mapping["entries"]):
            source = Path(entry["source"])
            target = Path(entry["target"])
            if path_present(source) and data_present(source) and path_present(target) and data_present(target):
                raise FileExistsError(f"both rollback roots contain data: {source} and {target}")
            backup_value = entry.get("backup")
            backup = Path(backup_value) if backup_value else None
            if path_present(target) and backup is not None:
                if not backup.exists() or tree_signature(target) != tree_signature(backup):
                    raise OSError(f"destination changed since migration; refusing rollback move: {target}")
            if path_present(target) and not path_present(source):
                source.parent.mkdir(parents=True, exist_ok=True)
                os.rename(target, source)
                entry["state"] = "rolled_back"
                write_json_atomic(mapping_path, mapping)
            elif path_present(source) and not path_present(target):
                entry["state"] = "rolled_back"
            elif entry["state"] not in {"empty_or_missing", "rolled_back"}:
                raise FileNotFoundError(f"neither migration root exists: {source} and {target}")
    except OSError as error:
        mapping["state"] = "rollback_partial_failure"
        mapping["error"] = str(error)
        write_json_atomic(mapping_path, mapping)
        print(f"ERROR: rollback stopped with a recoverable mapping at {mapping_path}: {error}", file=sys.stderr)
        return 1
    mapping["state"] = "rolled_back"
    mapping["rolled_back_at_utc"] = utc_now()
    write_json_atomic(mapping_path, mapping)
    print(f"Rolled back migration {mapping['migration_id']}; recovery mapping: {mapping_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("inventory", "dry-run", "apply", "rollback"))
    parser.add_argument("--home", type=Path, default=Path.home(), help="home containing the default old/new roots")
    parser.add_argument("--mapping", type=Path, help="external recovery JSON path; rollback always requires one")
    parser.add_argument("--writers-stopped", action="store_true", help="assert all fleet writers were quiesced before apply or rollback")
    args = parser.parse_args(argv)
    home = args.home.expanduser().resolve(strict=False)
    if not home.is_absolute() or home == Path(home.anchor):
        parser.error("--home must be an absolute non-root directory")
    environ = normalize_yomux_environment(os.environ, warn=True)
    roots = default_roots(home, environ)
    if args.mapping is not None:
        mapping_path = args.mapping.expanduser().resolve(strict=False)
    else:
        mapping_path = home / MIGRATION_HOME / f"migration-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}.json"
    ensure_external(mapping_path, roots)
    if args.mode == "inventory":
        print(json.dumps([root_inventory(Path(item["source"]), Path(item["target"]), item["label"]) for item in roots], indent=2, sort_keys=True))
        if not roots:
            print("No default roots selected; an explicit YOMUX_ROOT or per-root override is active.")
        return 0
    if args.mode == "dry-run":
        inventory = [root_inventory(Path(item["source"]), Path(item["target"]), item["label"]) for item in roots]
        print(json.dumps({"mode": "dry-run", "recovery_mapping": str(mapping_path), "roots": inventory}, indent=2, sort_keys=True))
        return 2 if any(item["state"] in {"conflict_both_contain_data", "destination_only", "source_symlink"} for item in inventory) else 0
    if args.mode == "rollback":
        if args.mapping is None:
            parser.error("rollback requires --mapping from a prior apply")
        if not args.writers_stopped:
            parser.error("rollback requires --writers-stopped after all fleet writers are quiesced")
        try:
            mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            print(f"ERROR: cannot read recovery mapping {mapping_path}: {error}", file=sys.stderr)
            return 2
        entries = mapping.get("entries")
        if not isinstance(entries, list) or any(
            not isinstance(entry, dict) or not entry.get("source") or not entry.get("target")
            for entry in entries
        ):
            print(f"ERROR: recovery mapping has invalid root entries: {mapping_path}", file=sys.stderr)
            return 2
        mapped_roots = [
            {"label": str(entry.get("label", "unknown")), "source": str(entry["source"]), "target": str(entry["target"])}
            for entry in entries
        ]
        try:
            ensure_external(mapping_path, mapped_roots)
        except ValueError as error:
            print(f"ERROR: {error}", file=sys.stderr)
            return 2
        writers = active_writer_processes(roots=mapped_roots, home=home)
        if writers:
            print("ERROR: YOmux writers are still running; rollback refused.", file=sys.stderr)
            print(json.dumps(writers, indent=2, sort_keys=True), file=sys.stderr)
            return 3
        return rollback_mapping(mapping, mapping_path)
    if not args.writers_stopped:
        parser.error("apply requires --writers-stopped after all fleet writers are quiesced")
    if mapping_path.exists():
        try:
            existing = json.loads(mapping_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            print(f"ERROR: existing mapping is unreadable: {error}", file=sys.stderr)
            return 2
        if existing.get("state") == "applied":
            changed = [
                entry
                for entry in existing.get("entries", [])
                if entry.get("backup")
                and (
                    not Path(entry["backup"]).exists()
                    or not Path(entry["target"]).exists()
                    or tree_signature(Path(entry["backup"])) != tree_signature(Path(entry["target"]))
                )
            ]
            if changed:
                print("ERROR: applied migration destinations differ from their recovery backups; refusing repeat apply.", file=sys.stderr)
                print(json.dumps(changed, indent=2, sort_keys=True), file=sys.stderr)
                return 2
            print(f"Migration already applied; recovery mapping: {mapping_path}")
            return 0
        print(f"ERROR: recovery mapping already exists and is not applied: {mapping_path}", file=sys.stderr)
        return 2
    writers = active_writer_processes(roots=roots, home=home)
    if writers:
        print("ERROR: YOmux writers are still running; apply refused.", file=sys.stderr)
        print(json.dumps(writers, indent=2, sort_keys=True), file=sys.stderr)
        return 3
    mapping = prepare_mapping(roots, mapping_path)
    return apply_mapping(mapping, mapping_path)


if __name__ == "__main__":
    raise SystemExit(main())

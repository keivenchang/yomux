import sqlite3

import pytest

from tools.statsd_fixture_double import MIRRORS, double
from tools import statsd_fixture_double
from tools.measurement import merged_transaction_append_cost as append_cost
from tools.measurement import pragma_joint_grid
from tools.verify_go_stats_restart import facts


def _copy_fixture(writer, source, target):
    if writer == "append_cost":
        return append_cost.prepare_store(str(target), topology="copy", source=str(source))
    if writer == "pragma_grid":
        return pragma_joint_grid.copy_store(source, target)
    return double(source, target)


def _create_store(path):
    with sqlite3.connect(path) as connection:
        for table, columns, _suffixed in MIRRORS:
            connection.execute(f'CREATE TABLE "{table}" ({", ".join(columns)})')
            values = [1.0 if column == "observed_at" else "{}" if column == "payload_json" else "fixture" for column in columns]
            connection.execute(f'INSERT INTO "{table}" VALUES ({", ".join("?" for _ in columns)})', values)
        connection.execute("CREATE TABLE aggregate_rings (retained INTEGER)")
        connection.execute("INSERT INTO aggregate_rings VALUES (1)")


@pytest.mark.parametrize("alias", ["same", "symlink", "hardlink", "target_sidecar", "source_sidecar"])
@pytest.mark.parametrize("writer", ["double", "append_cost", "pragma_grid"])
def test_double_refuses_source_aliases_before_mutating_any_file(tmp_path, alias, writer):
    source, target = tmp_path / "source.db", tmp_path / "target.db"
    if alias == "same":
        target = source
    elif alias == "target_sidecar":
        source = target.with_name(target.name + "-wal")
    elif alias == "source_sidecar":
        target = source.with_name(source.name + "-shm")
    source.write_bytes(b"original source")
    if alias == "symlink":
        target.symlink_to(source)
    elif alias == "hardlink":
        target.hardlink_to(source)
    elif target != source:
        target.write_bytes(b"existing target")
    paths = [source, target, target.with_name(target.name + "-wal"), target.with_name(target.name + "-shm")]
    before = {path: path.read_bytes() for path in paths if path.exists()}
    with pytest.raises(ValueError, match="overlap"):
        _copy_fixture(writer, source, target)
    assert {path: path.read_bytes() for path in paths if path.exists()} == before


@pytest.mark.parametrize("writer", ["double", "append_cost", "pragma_grid"])
def test_double_refuses_missing_source_before_removing_target_or_sidecars(tmp_path, writer):
    target = tmp_path / "target.db"
    paths = [target, target.with_name(target.name + "-wal"), target.with_name(target.name + "-shm")]
    for path in paths:
        path.write_bytes(path.name.encode())
    before = {path: path.read_bytes() for path in paths}
    with pytest.raises(FileNotFoundError):
        _copy_fixture(writer, tmp_path / "missing.db", target)
    assert {path: path.read_bytes() for path in paths} == before


@pytest.mark.parametrize("writer", ["double", "append_cost", "pragma_grid"])
@pytest.mark.parametrize("link", ["symlink", "hardlink"])
@pytest.mark.parametrize("suffix", ["", "-wal", "-shm"])
def test_fixture_copy_replaces_destination_links_without_changing_their_other_owner(tmp_path, writer, link, suffix):
    source, target, unrelated = (tmp_path / name for name in ("source.db", "target.db", "unrelated"))
    _create_store(source)
    unrelated.write_bytes(b"unrelated original")
    destination = target.with_name(target.name + suffix)
    if link == "symlink":
        destination.symlink_to(unrelated)
    else:
        destination.hardlink_to(unrelated)
    _copy_fixture(writer, source, target)
    assert unrelated.read_bytes() == b"unrelated original"
    assert target.is_file() and not target.is_symlink()


@pytest.mark.parametrize("writer", ["double", "append_cost", "pragma_grid"])
def test_fixture_copy_removes_stale_destination_sidecars_absent_from_source(tmp_path, writer):
    source, target = tmp_path / "source.db", tmp_path / "target.db"
    _create_store(source)
    sidecars = [target.with_name(target.name + suffix) for suffix in ("-wal", "-shm")]
    for sidecar in sidecars:
        sidecar.write_bytes(b"stale destination sidecar")
    _copy_fixture(writer, source, target)
    assert all(not sidecar.exists() for sidecar in sidecars)


@pytest.mark.parametrize("directory", ["ordinary", "reserved?#% space"])
def test_double_mirrors_retained_rows_and_preserves_source_and_derived_rings(tmp_path, directory):
    tmp_path = tmp_path / directory
    tmp_path.mkdir()
    source, target = tmp_path / "source.db", tmp_path / "target.db"
    _create_store(source)
    original = source.read_bytes()
    result = double(source, target)
    assert source.read_bytes() == original
    assert result["integrity"] == "ok"
    assert all(result["after"]["rows"][table] == 2 for table, _columns, _suffixed in MIRRORS)
    assert result["after"]["rows"]["aggregate_rings"] == 1


def test_double_retains_committed_source_wal_facts(tmp_path):
    source, target = tmp_path / "source.db", tmp_path / "target.db"
    _create_store(source)
    with sqlite3.connect(source) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA wal_autocheckpoint=0")
        connection.execute("INSERT INTO observations SELECT * FROM observations")
        connection.commit()
        assert source.with_name(source.name + "-wal").stat().st_size > 0
        result = double(source, target)
        assert result["source"]["rows"]["observations"] == 2
        assert result["after"]["rows"]["observations"] == 4


def test_double_cli_replaces_destination_symlink_without_overwriting_its_owner(tmp_path):
    source, target, unrelated = (tmp_path / name for name in ("source.db", "target.db", "unrelated"))
    _create_store(source)
    unrelated.write_bytes(b"unrelated original")
    target.symlink_to(unrelated)
    assert statsd_fixture_double.main(["double", "--source", str(source), "--target", str(target)]) == 0
    assert unrelated.read_bytes() == b"unrelated original"
    assert target.is_file() and not target.is_symlink()


@pytest.mark.parametrize("reader", ["census", "facts", "counts", "newest", "published", "quick_check"])
def test_fixture_readers_preserve_reserved_filename_bytes_and_read_only_mode(tmp_path, reader):
    database = tmp_path / "reserved?#% space.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript("""
            CREATE TABLE observations(family,source_id,observed_at,payload_json);
            INSERT INTO observations VALUES('cpu','fixture',3,'{}');
            CREATE TABLE usage_atoms(event_id,payload_json);
            INSERT INTO usage_atoms VALUES('fixture','{"quantity":2}');
            CREATE TABLE collector_checkpoints(kind,source_id,revision,byte_offset,file_size);
            CREATE TABLE schema_meta(singleton,source_generation);
            INSERT INTO schema_meta VALUES(1,7);
            CREATE TABLE aggregate_publication(singleton,source_generation);
            INSERT INTO aggregate_publication VALUES(1,7);
        """)
    readers = {
        "census": (lambda path: statsd_fixture_double.census(path)["rows"]["observations"], 1),
        "facts": (lambda path: facts(path)["tokens"], 2),
        "counts": (lambda path: append_cost.table_counts(path)["observations"], 1),
        "newest": (append_cost.newest_observed_at, 3),
        "published": (append_cost.published_generation, 7),
        "quick_check": (pragma_joint_grid.quick_check, "ok"),
    }
    operation, expected = readers[reader]
    original = database.read_bytes()
    assert operation(database) == expected
    assert database.read_bytes() == original
    assert list(tmp_path.iterdir()) == [database]

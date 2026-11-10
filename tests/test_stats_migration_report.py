# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import sqlite3

from yomux_lib.stats_current import migration, storage


def test_schema7_migration_reports_each_rebuild_issue(tmp_path):
    target = tmp_path / storage.DATABASE_FILENAME
    source = tmp_path / migration.V7_DATABASE_FILENAME
    with storage.Store.open(target):
        pass
    target.rename(source)
    with sqlite3.connect(source) as connection:
        connection.execute("PRAGMA user_version = 7")

    report = migration.migrate_current_v7_database(tmp_path, target, source, now=100)

    assert [issue.kind for issue in report.issues] == ["current_schema", "ring_payload_rebuild"]
    assert report.issue_count == len(report.issues)

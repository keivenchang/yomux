// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package finder

import (
	"context"
	"database/sql"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/keivenchang/yomux/internal/atomicfile"
)

func TestSQLitePreviewCommittedWALAndQuotedTable(t *testing.T) {
	service, root := newTestService(t, nil)
	path := filepath.Join(root, "data.sqlite")
	db, err := sql.Open("sqlite3", atomicfile.SQLiteURI(path, "_journal_mode=WAL"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	db.SetMaxOpenConns(1)
	for _, query := range []string{
		`CREATE TABLE "table""name" (id INTEGER PRIMARY KEY, text TEXT, bytes BLOB)`,
		`CREATE VIEW ignored AS SELECT * FROM "table""name"`,
		`PRAGMA wal_checkpoint(TRUNCATE)`,
		`WITH RECURSIVE nums(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM nums WHERE x<55)
			INSERT INTO "table""name" SELECT x, CASE WHEN x=1 THEN NULL ELSE '<em>literal</em>' END, x'0001' FROM nums`,
	} {
		if _, err := db.Exec(query); err != nil {
			t.Fatal(err)
		}
	}
	before, err := os.ReadFile(path + "-wal")
	if err != nil || len(before) == 0 {
		t.Fatalf("fixture WAL = %d bytes, %v", len(before), err)
	}
	ctx := context.Background()
	first, failure := service.SQLitePreview(ctx, path, "", 0, false)
	if failure != nil {
		t.Fatal(failure)
	}
	tables := first["tables"].([]string)
	rows := first["rows"].([][]*string)
	if len(tables) != 1 || tables[0] != `table"name` || len(rows) != 50 || first["more"] != true ||
		*rows[0][0] != "1" || rows[0][1] != nil || *rows[0][2] != "[BLOB: 2 bytes]" {
		t.Fatalf("unexpected table inventory or first page: %v, %v", tables, first)
	}
	second, failure := service.SQLitePreview(ctx, path, `table"name`, 1, false)
	if failure != nil {
		t.Fatal(failure)
	}
	rows = second["rows"].([][]*string)
	if len(rows) != 5 || second["more"] != false || *rows[0][0] != "51" || *rows[0][1] != "<em>literal</em>" ||
		!strings.HasPrefix(second["schema"].(string), "CREATE TABLE") {
		t.Fatalf("unexpected second page: %v", second)
	}
	if _, failure := service.SQLitePreview(ctx, path, `ignored`, 0, false); failure == nil {
		t.Fatal("view was admitted as a table")
	}
	fallback, failure := service.SQLitePreview(ctx, path, "removed-table", 1, true)
	if failure != nil || fallback["table"] != first["table"] || fallback["page"] != 0 {
		t.Fatalf("missing table fallback = %v, %v", fallback, failure)
	}
	after, err := os.ReadFile(path + "-wal")
	if err != nil || string(after) != string(before) {
		t.Fatal("preview changed the source WAL")
	}
}

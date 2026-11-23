// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package finder

import (
	"bytes"
	"context"
	"database/sql"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"syscall"

	"github.com/keivenchang/yomux/internal/atomicfile"
)

const sqlitePreviewPageSize = 50
const sqlitePreviewMaxPage = 10000

type sqlitePreviewFile struct {
	file *os.File
	info os.FileInfo
	path string
}

func (s *Service) sqliteRollbackJournalIdle(path string) bool {
	file, _, resolved, failure := s.open(path, false)
	if failure != nil {
		return failure.Status == http.StatusNotFound
	}
	defer file.Close()
	if resolved != path {
		return false
	}
	var header [28]byte
	size, err := file.ReadAt(header[:], 0)
	return (size == 0 && err == io.EOF) || (err == nil && header == [28]byte{})
}

func (s *Service) sqlitePreviewSnapshot(ctx context.Context, path, destination string) *Error {
	file, requested, resolved, failure := s.open(path, false)
	if failure != nil {
		return failure
	}
	defer file.Close()
	main, err := file.Stat()
	if err != nil {
		return filesystemError(requested, err)
	}
	if main.Size() > MaxReadBytes {
		return tooLarge(requested, main.Size(), MaxReadBytes)
	}
	var header [100]byte
	if _, err := file.ReadAt(header[:], 0); err != nil || !bytes.HasPrefix(header[:], []byte("SQLite format 3\x00")) {
		return invalid(requested, "File is not a SQLite database")
	}

	// An active or interrupted rollback-journal transaction may have modified pages in the
	// main file. Never treat those bytes as a standalone committed snapshot.
	if !s.sqliteRollbackJournalIdle(resolved + "-journal") {
		return changed(requested)
	}
	files := []sqlitePreviewFile{{file, main, resolved}}
	if header[18] == 2 || header[19] == 2 {
		wal, _, walPath, walFailure := s.open(resolved+"-wal", false)
		switch {
		case walFailure == nil:
			defer wal.Close()
			if walPath != resolved+"-wal" {
				return invalid(requested, "SQLite WAL must be next to its database")
			}
			info, err := wal.Stat()
			if err != nil {
				return filesystemError(requested, err)
			}
			files = append(files, sqlitePreviewFile{wal, info, walPath})
		case walFailure.Status != http.StatusNotFound:
			return walFailure
		}
	}
	var total int64
	for _, input := range files {
		total += input.info.Size()
		if total > MaxReadBytes {
			return tooLarge(requested, total, MaxReadBytes)
		}
		data, err := readDescriptorContext(ctx, input.file, input.info.Size()+1)
		if err != nil {
			if ctx.Err() != nil {
				return cancelled(requested)
			}
			return filesystemError(requested, err)
		}
		if int64(len(data)) != input.info.Size() {
			return changed(requested)
		}
		name := destination + strings.TrimPrefix(input.path, resolved)
		if err := os.WriteFile(name, data, 0o600); err != nil {
			return filesystemError(requested, err)
		}
	}
	for _, input := range files {
		current, err := os.Lstat(input.path)
		if err != nil || !s.pathStillStable(input.file, input.info) || !os.SameFile(input.info, current) ||
			current.Size() != input.info.Size() || !current.ModTime().Equal(input.info.ModTime()) {
			return changed(requested)
		}
		before, beforeOK := input.info.Sys().(*syscall.Stat_t)
		after, afterOK := current.Sys().(*syscall.Stat_t)
		if !beforeOK || !afterOK || statCTime(before) != statCTime(after) {
			return changed(requested)
		}
	}
	if !s.sqliteRollbackJournalIdle(resolved + "-journal") {
		return changed(requested)
	}
	if len(files) == 1 && (header[18] == 2 || header[19] == 2) {
		if _, err := os.Lstat(resolved + "-wal"); !os.IsNotExist(err) {
			return changed(requested)
		}
	}
	return nil
}

func (s *Service) SQLitePreview(ctx context.Context, path, table string, page int, recoverTable bool) (map[string]any, *Error) {
	if page < 0 || page > sqlitePreviewMaxPage || strings.ContainsRune(table, 0) {
		return nil, invalid(path, "Invalid SQLite preview page or table")
	}
	scratch, err := os.MkdirTemp("", "yomux-sqlite-preview-")
	if err != nil {
		return nil, filesystemError(path, err)
	}
	defer os.RemoveAll(scratch)
	database := filepath.Join(scratch, "preview.sqlite")
	if failure := s.sqlitePreviewSnapshot(ctx, path, database); failure != nil {
		return nil, failure
	}
	db, err := sql.Open("sqlite3", atomicfile.SQLiteURI(database, "mode=ro&_query_only=1&_busy_timeout=250"))
	if err != nil {
		return nil, filesystemError(path, err)
	}
	defer db.Close()
	db.SetMaxOpenConns(1)
	result, err := readSQLitePreview(ctx, db, table, page, recoverTable)
	if err != nil {
		if ctx.Err() != nil {
			return nil, cancelled(path)
		}
		return nil, invalid(path, err.Error())
	}
	return result, nil
}

func sqlitePreviewStrings(ctx context.Context, db *sql.DB, query string, args ...any) ([]string, error) {
	rows, err := db.QueryContext(ctx, query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	values := []string{}
	for rows.Next() {
		var value string
		if err := rows.Scan(&value); err != nil {
			return nil, err
		}
		values = append(values, value)
	}
	return values, rows.Err()
}

func readSQLitePreview(ctx context.Context, db *sql.DB, table string, page int, recoverTable bool) (map[string]any, error) {
	if _, err := db.ExecContext(ctx, "PRAGMA trusted_schema=OFF"); err != nil {
		return nil, err
	}
	tables, err := sqlitePreviewStrings(ctx, db, `SELECT name FROM pragma_table_list
		WHERE schema='main' AND type='table' AND name NOT GLOB 'sqlite_*' ORDER BY name LIMIT 513`)
	if err != nil {
		return nil, err
	}
	if len(tables) > 512 {
		return nil, fmt.Errorf("Database has more than 512 tables")
	}
	if recoverTable && table != "" && !slices.Contains(tables, table) {
		table, page = "", 0
	}
	result := map[string]any{"tables": tables, "table": table, "page": page}
	if len(tables) == 0 && table == "" {
		return result, nil
	}
	if table == "" {
		table = tables[0]
	}
	schema, err := sqlitePreviewStrings(ctx, db, `SELECT substr(sql,1,16385) FROM main.sqlite_schema
		WHERE name=? AND type='table' AND name IN
		(SELECT name FROM pragma_table_list WHERE schema='main' AND type='table' AND name NOT GLOB 'sqlite_*')`, table)
	if err != nil {
		return nil, err
	}
	if len(schema) != 1 {
		return nil, fmt.Errorf("Table is unavailable")
	}
	columns, err := sqlitePreviewStrings(ctx, db,
		`SELECT name FROM pragma_table_xinfo(?, 'main') WHERE hidden!=1 ORDER BY cid LIMIT 129`, table)
	if err != nil {
		return nil, err
	}
	if len(columns) == 0 || len(columns) > 128 {
		return nil, fmt.Errorf("Table must have between 1 and 128 columns")
	}
	quoteIdentifier := func(value string) string { return "\"" + strings.ReplaceAll(value, "\"", "\"\"") + "\"" }
	selects := make([]string, len(columns))
	order := make([]string, len(columns))
	for index, name := range columns {
		column := quoteIdentifier(name)
		order[index] = column
		selects[index] = fmt.Sprintf(`CASE typeof(%[1]s) WHEN 'blob' THEN printf('[BLOB: %%d bytes]',length(%[1]s))
			WHEN 'text' THEN substr(%[1]s,1,512) || CASE WHEN length(%[1]s)>512 THEN '…' ELSE '' END
			ELSE CAST(%[1]s AS TEXT) END`, column)
	}
	rows, err := db.QueryContext(ctx, "SELECT "+strings.Join(selects, ",")+" FROM main."+quoteIdentifier(table)+
		" ORDER BY "+strings.Join(order, ",")+" LIMIT ? OFFSET ?",
		sqlitePreviewPageSize+1, page*sqlitePreviewPageSize)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	values := make([][]*string, 0, sqlitePreviewPageSize)
	more := false
	for rows.Next() {
		if len(values) == sqlitePreviewPageSize {
			more = page < sqlitePreviewMaxPage
			break
		}
		row := make([]*string, len(columns))
		targets := make([]any, len(columns))
		for index := range row {
			targets[index] = &row[index]
		}
		if err := rows.Scan(targets...); err != nil {
			return nil, err
		}
		values = append(values, row)
	}
	result["table"], result["columns"], result["rows"], result["more"] = table, columns, values, more
	result["schema"], result["schema_truncated"] = schema[0], len([]rune(schema[0])) > 16384
	if result["schema_truncated"] == true {
		result["schema"] = string([]rune(schema[0])[:16384])
	}
	return result, rows.Err()
}

// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package finder

import (
	"context"
	"database/sql"
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/mattn/go-sqlite3"
	"golang.org/x/sys/unix"
)

const indexTransientCeiling = int64(2 * 1024 * 1024 * 1024)

func readOnlyIndex(path string) (*sql.DB, error) {
	return sql.Open("sqlite3", "file:"+filepath.ToSlash(path)+"?mode=ro&_busy_timeout=5000")
}

// Probe SQLite headers without opening SQLite: even mode=ro can create WAL
// sidecars. Committed WAL page-one frames carry the effective user_version.
func indexSchema(path string) (int, error) {
	file, err := os.Open(path)
	if err != nil {
		return 0, err
	}
	defer file.Close()
	var header [100]byte
	if _, err := io.ReadFull(file, header[:]); err != nil {
		return 0, err
	}
	if string(header[:16]) != "SQLite format 3\x00" {
		return 0, errors.New("invalid SQLite index header")
	}
	version := int(binary.BigEndian.Uint32(header[60:64]))
	wal, err := os.Open(path + "-wal")
	if errors.Is(err, os.ErrNotExist) {
		return version, nil
	}
	if err != nil {
		return 0, err
	}
	defer wal.Close()
	before, err := wal.Stat()
	if err != nil {
		return 0, err
	}
	if before.Size() == 0 {
		return version, nil
	}
	if before.Size() > indexTransientCeiling {
		return 0, errors.New("index WAL exceeds inspection bound")
	}
	var walHeader [32]byte
	if _, err := io.ReadFull(wal, walHeader[:]); err != nil {
		return 0, err
	}
	magic := binary.BigEndian.Uint32(walHeader[:4])
	if magic != 0x377f0682 && magic != 0x377f0683 {
		return 0, errors.New("invalid SQLite WAL header")
	}
	pageSize := int(binary.BigEndian.Uint32(walHeader[8:12]))
	if pageSize < 512 || pageSize > 65536 || pageSize&(pageSize-1) != 0 {
		return 0, errors.New("invalid SQLite WAL page size")
	}
	var order binary.ByteOrder = binary.LittleEndian
	if magic&1 != 0 {
		order = binary.BigEndian
	}
	checksum := func(data []byte, first, second uint32) (uint32, uint32) {
		for index := 0; index < len(data); index += 8 {
			first += order.Uint32(data[index:index+4]) + second
			second += order.Uint32(data[index+4:index+8]) + first
		}
		return first, second
	}
	first, second := checksum(walHeader[:24], 0, 0)
	if first != binary.BigEndian.Uint32(walHeader[24:28]) || second != binary.BigEndian.Uint32(walHeader[28:32]) {
		return 0, errors.New("invalid SQLite WAL header checksum")
	}
	frame := make([]byte, 24+pageSize)
	pendingVersion := version
	for offset := int64(32); offset+int64(len(frame)) <= before.Size(); offset += int64(len(frame)) {
		if _, err := wal.ReadAt(frame, offset); err != nil {
			return 0, err
		}
		if string(frame[8:16]) != string(walHeader[16:24]) {
			break
		}
		first, second = checksum(frame[:8], first, second)
		first, second = checksum(frame[24:], first, second)
		if first != binary.BigEndian.Uint32(frame[16:20]) || second != binary.BigEndian.Uint32(frame[20:24]) {
			break
		}
		if binary.BigEndian.Uint32(frame[:4]) == 1 {
			pendingVersion = int(binary.BigEndian.Uint32(frame[84:88]))
		}
		if binary.BigEndian.Uint32(frame[4:8]) != 0 {
			version = pendingVersion
		}
	}
	after, err := wal.Stat()
	if err != nil {
		return 0, err
	}
	if before.Size() != after.Size() || !before.ModTime().Equal(after.ModTime()) {
		return 0, errors.New("index WAL changed during inspection")
	}
	return version, nil
}

func writeNormalizedPaths(tx *sql.Tx, root string, generation int, paths []discoveredIndexPath) error {
	if _, err := tx.Exec(`INSERT OR IGNORE INTO root_keys(path) VALUES(?)`, root); err != nil {
		return err
	}
	var rootID int64
	if err := tx.QueryRow(`SELECT id FROM root_keys WHERE path=?`, root).Scan(&rootID); err != nil {
		return err
	}
	directories := map[string]int64{"/": 1}
	var directoryID func(string) (int64, error)
	directoryID = func(path string) (int64, error) {
		path = filepath.Clean(path)
		if id, ok := directories[path]; ok {
			return id, nil
		}
		parent, err := directoryID(filepath.Dir(path))
		if err != nil {
			return 0, err
		}
		if _, err := tx.Exec(`INSERT OR IGNORE INTO directories(parent,name) VALUES(?,?)`, parent, filepath.Base(path)); err != nil {
			return 0, err
		}
		var id int64
		if err := tx.QueryRow(`SELECT id FROM directories WHERE parent=? AND name=?`, parent, filepath.Base(path)).Scan(&id); err != nil {
			return 0, err
		}
		directories[path] = id
		return id, nil
	}
	statement, err := tx.Prepare(`INSERT OR REPLACE INTO entries(root,generation,directory,name,file_identity,size,mtime_ns) VALUES(?,?,?,?,?,?,?)`)
	if err != nil {
		return err
	}
	defer statement.Close()
	for _, path := range paths {
		absolute := filepath.Join(root, filepath.FromSlash(path.relativePath()))
		if !indexRootWithin(root, absolute) || absolute == root || filepath.Base(absolute) != path.name {
			return errors.New("invalid normalized index path")
		}
		id, err := directoryID(filepath.Dir(absolute))
		if err != nil {
			return err
		}
		if _, err := statement.Exec(rootID, generation, id, path.name, path.identity, path.size, path.mtimeNS); err != nil {
			return err
		}
	}
	return nil
}

func (store *searchIndexStore) loadNormalizedPaths(ctx context.Context, root string, generation int) ([]discoveredIndexPath, error) {
	// Share directory prefixes across files; no directory ancestry is duplicated on disk.
	rows, err := store.db.QueryContext(ctx, `WITH RECURSIVE paths(id,path) AS (SELECT 1,'/' UNION ALL SELECT d.id,CASE WHEN p.path='/' THEN p.path||d.name ELSE p.path||'/'||d.name END FROM directories d JOIN paths p ON d.parent=p.id) SELECT p.path,e.name,e.file_identity,e.size,e.mtime_ns FROM entries e JOIN paths p ON p.id=e.directory WHERE e.root=(SELECT id FROM root_keys WHERE path=?) AND e.generation=?`, root, generation)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var result []discoveredIndexPath
	prefixes := map[string]string{}
	for rows.Next() {
		var directory string
		var path discoveredIndexPath
		if err := rows.Scan(&directory, &path.name, &path.identity, &path.size, &path.mtimeNS); err != nil {
			return nil, err
		}
		prefix, exists := prefixes[directory]
		if !exists {
			relative, err := filepath.Rel(root, directory)
			if err != nil || !indexRootWithin(root, directory) {
				return nil, errors.New("invalid index directory relationship")
			}
			if relative != "." {
				prefix = filepath.ToSlash(relative) + "/"
			}
			prefixes[directory] = prefix
		}
		path.directory = prefix
		result = append(result, path)
	}
	return result, rows.Err()
}

func validateIndex(path string) error {
	db, err := sql.Open("sqlite3", "file:"+filepath.ToSlash(path)+"?mode=ro&immutable=1")
	if err != nil {
		return err
	}
	defer db.Close()
	var result string
	if err := db.QueryRow(`PRAGMA integrity_check`).Scan(&result); err != nil {
		return err
	}
	if result != "ok" {
		return fmt.Errorf("index integrity: %s", result)
	}
	var version, broken int
	if err := db.QueryRow(`PRAGMA user_version`).Scan(&version); err != nil {
		return err
	}
	if version != searchIndexSchemaVersion {
		return fmt.Errorf("invalid publication schema %d", version)
	}
	if err := db.QueryRow(`SELECT COUNT(*) FROM entries e LEFT JOIN directories d ON d.id=e.directory LEFT JOIN root_keys r ON r.id=e.root WHERE d.id IS NULL OR r.id IS NULL`).Scan(&broken); err != nil {
		return err
	}
	if broken != 0 {
		return errors.New("index has dangling relationships")
	}
	if err := db.QueryRow(`WITH RECURSIVE reachable(id) AS (SELECT 1 UNION ALL SELECT d.id FROM directories d JOIN reachable p ON d.parent=p.id) SELECT COUNT(*) FROM directories WHERE id NOT IN reachable`).Scan(&broken); err != nil {
		return err
	}
	if broken != 0 {
		return errors.New("index directory ancestry is disconnected")
	}
	return nil
}

type indexPublication struct {
	Phase       string `json:"phase"`
	Candidate   string `json:"candidate"`
	Backup      string `json:"backup"`
	Interrupted string `json:"interrupted,omitempty"`
}

func syncIndexFile(path string) error {
	file, err := os.Open(path)
	if err != nil {
		return err
	}
	defer file.Close()
	return file.Sync()
}

var indexMigrationPhase = func(string) error { return nil }

func saveIndexPublication(path string, publication indexPublication) error {
	data, err := json.Marshal(publication)
	if err != nil {
		return err
	}
	temporary, err := os.CreateTemp(filepath.Dir(path), filepath.Base(path)+".publication-")
	if err != nil {
		return err
	}
	if _, err := temporary.Write(data); err != nil {
		temporary.Close()
		return err
	}
	if err := temporary.Sync(); err != nil {
		temporary.Close()
		return err
	}
	if err := temporary.Close(); err != nil {
		return err
	}
	if err := os.Rename(temporary.Name(), path+".publication.json"); err != nil {
		return err
	}
	if err := syncIndexFile(filepath.Dir(path)); err != nil {
		return err
	}
	return indexMigrationPhase(publication.Phase)
}

func recoverIndexPublication(path string) error {
	if _, err := os.Stat(path); err == nil {
		version, err := indexSchema(path)
		if err == nil && version > searchIndexSchemaVersion {
			return fmt.Errorf("Finder index schema %d is newer than supported version %d", version, searchIndexSchemaVersion)
		}
	}
	data, err := os.ReadFile(path + ".publication.json")
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}
	var publication indexPublication
	if err := json.Unmarshal(data, &publication); err != nil {
		return err
	}
	for _, candidate := range []string{publication.Candidate, publication.Backup} {
		if filepath.Dir(candidate) != filepath.Dir(path) || !strings.HasPrefix(candidate, path+".") {
			return errors.New("invalid index recovery path")
		}
	}
	if publication.Phase == "published" || publication.Phase == "backed_up" || publication.Phase == "restoring" {
		if publication.Phase != "restoring" {
			if err := validateIndex(path); err == nil {
				return nil
			}
		}
		// Never overwrite the active inode or its WAL during restoration. A distinct
		// hard link gives SQLite its original checkpointed generation back atomically.
		if publication.Interrupted == "" {
			publication.Interrupted = path + fmt.Sprintf(".interrupted-%d", time.Now().UnixNano())
		}
		if filepath.Dir(publication.Interrupted) != filepath.Dir(path) || !strings.HasPrefix(publication.Interrupted, path+".interrupted-") {
			return errors.New("invalid interrupted index path")
		}
		publication.Phase = "restoring"
		if err := saveIndexPublication(path, publication); err != nil {
			return err
		}
		for _, suffix := range []string{"", "-wal", "-shm"} {
			if _, err := os.Stat(publication.Interrupted + suffix); err == nil {
				continue
			}
			if err := os.Rename(path+suffix, publication.Interrupted+suffix); err != nil && !errors.Is(err, os.ErrNotExist) {
				return err
			}
		}
		restored := path + fmt.Sprintf(".restore-%d", time.Now().UnixNano())
		if err := os.Link(publication.Backup, restored); err != nil {
			return err
		}
		if err := os.Rename(restored, path); err != nil {
			return err
		}
		if err := syncIndexFile(filepath.Dir(path)); err != nil {
			return err
		}
		publication.Phase = "restored"
		return saveIndexPublication(path, publication)
	}
	return nil
}

func checkpointIndex(db *sql.DB) error {
	var busy, logPages, checkpointed int
	if err := db.QueryRow(`PRAGMA wal_checkpoint(TRUNCATE)`).Scan(&busy, &logPages, &checkpointed); err != nil {
		return err
	}
	if busy != 0 || logPages != checkpointed {
		return errors.New("index checkpoint is blocked by a reader")
	}
	return nil
}

func migrateSearchIndex(path string, initialPolicy ...*indexRequest) error {
	var policy *indexRequest
	if len(initialPolicy) > 0 {
		policy = initialPolicy[0]
	}
	if _, err := os.Stat(path); errors.Is(err, os.ErrNotExist) {
		return nil
	} else if err != nil {
		return err
	}
	version, err := indexSchema(path)
	if err != nil {
		return err
	}
	if version > searchIndexSchemaVersion {
		return fmt.Errorf("Finder index schema %d is newer than supported version %d", version, searchIndexSchemaVersion)
	}
	if version == searchIndexSchemaVersion {
		return nil
	}
	var filesystem unix.Statfs_t
	if err := unix.Statfs(filepath.Dir(path), &filesystem); err != nil {
		return err
	}
	info, err := os.Stat(path)
	if err != nil {
		return err
	}
	if int64(filesystem.Bavail)*int64(filesystem.Bsize) < info.Size()+32*1024*1024 {
		return errors.New("insufficient free space for Finder index migration")
	}
	if info.Size() > indexTransientCeiling {
		return errors.New("Finder migration source exceeds transient ceiling")
	}
	original, err := sql.Open("sqlite3", "file:"+filepath.ToSlash(path)+"?_busy_timeout=5000")
	if err != nil {
		return err
	}
	err = checkpointIndex(original)
	closeErr := original.Close()
	if err != nil {
		return err
	}
	if closeErr != nil {
		return closeErr
	}
	usageStore := &searchIndexStore{path: path}
	usage, err := usageStore.diskUsage()
	if err != nil {
		return err
	}
	abandoned := usage["staging_bytes"]
	if info.Size()*2+abandoned+32*1024*1024 > indexTransientCeiling {
		return errors.New("retained candidates block index migration transient budget")
	}
	publication := indexPublication{Phase: "building", Candidate: path + fmt.Sprintf(".candidate-%d", time.Now().UnixNano()), Backup: path + fmt.Sprintf(".rollback-%d", time.Now().UnixNano())}
	if err := saveIndexPublication(path, publication); err != nil {
		return err
	}
	candidate, err := createSearchIndexStore(publication.Candidate)
	if err != nil {
		return err
	}
	defer candidate.db.Close()
	candidate.db.SetMaxOpenConns(1)
	sourceURI := "file:" + filepath.ToSlash(path) + "?mode=ro"
	if _, err := candidate.db.Exec(`ATTACH DATABASE ? AS source`, sourceURI); err != nil {
		return err
	}
	// Copy only supported Go tables. Obsolete Python snapshots stay in the rollback inode.
	for _, table := range []string{"roots", "root_generations", "settings_fences", "settings_state"} {
		var exists int
		if err := candidate.db.QueryRow(`SELECT COUNT(*) FROM source.sqlite_master WHERE type='table' AND name=?`, table).Scan(&exists); err != nil {
			return err
		}
		if exists == 0 {
			continue
		}
		rows, err := candidate.db.Query(`PRAGMA source.table_info(` + table + `)`)
		if err != nil {
			return err
		}
		var columns []string
		for rows.Next() {
			var ordinal, notNull, primary int
			var name, kind string
			var value any
			if err := rows.Scan(&ordinal, &name, &kind, &notNull, &value, &primary); err != nil {
				rows.Close()
				return err
			}
			columns = append(columns, name)
		}
		if err := rows.Err(); err != nil {
			rows.Close()
			return err
		}
		rows.Close()
		list := strings.Join(columns, ",")
		if _, err := candidate.db.Exec(`INSERT INTO ` + table + `(` + list + `) SELECT ` + list + ` FROM source.` + table); err != nil {
			return err
		}
	}
	if _, err := candidate.db.Exec(`UPDATE roots SET active_revision=settings_revision`); err != nil {
		return err
	}
	var entriesExist int
	if err := candidate.db.QueryRow(`SELECT COUNT(*) FROM source.sqlite_master WHERE name='entries' AND type='table'`).Scan(&entriesExist); err != nil {
		return err
	}
	if entriesExist != 0 {
		source, err := readOnlyIndex(path)
		if err != nil {
			return err
		}
		defer source.Close()
		rows, err := source.Query(`SELECT root,generation,relative_path,name,file_identity,size,mtime_ns FROM entries ORDER BY root,generation,relative_path`)
		if err != nil {
			return err
		}
		defer rows.Close()
		var batch []discoveredIndexPath
		var batchRoot string
		var batchGeneration int
		flush := func() error {
			if len(batch) == 0 {
				return nil
			}
			tx, err := candidate.db.Begin()
			if err != nil {
				return err
			}
			if err := writeNormalizedPaths(tx, batchRoot, batchGeneration, batch); err != nil {
				tx.Rollback()
				return err
			}
			if err := tx.Commit(); err != nil {
				return err
			}
			batch = nil
			if err := checkpointIndex(candidate.db); err != nil {
				return err
			}
			allocation, err := candidate.allocatedBytes()
			if err != nil {
				return err
			}
			if info.Size()+abandoned+allocation+32*1024*1024 > indexTransientCeiling {
				return errors.New("index migration transient ceiling reached")
			}
			return nil
		}
		for rows.Next() {
			var root string
			var generation int
			var entry discoveredIndexPath
			if err := rows.Scan(&root, &generation, &entry.relative, &entry.name, &entry.identity, &entry.size, &entry.mtimeNS); err != nil {
				return err
			}
			if len(batch) > 0 && (batchRoot != root || batchGeneration != generation || len(batch) == indexBatchSize) {
				if err := flush(); err != nil {
					return err
				}
			}
			batchRoot, batchGeneration = root, generation
			batch = append(batch, entry)
		}
		if err := rows.Err(); err != nil {
			return err
		}
		if err := flush(); err != nil {
			return err
		}
	}
	if entriesExist != 0 {
		var mismatch int
		query := `WITH RECURSIVE paths(id,path) AS (SELECT 1,'/' UNION ALL SELECT d.id,CASE WHEN p.path='/' THEN p.path||d.name ELSE p.path||'/'||d.name END FROM directories d JOIN paths p ON d.parent=p.id), normalized AS (SELECT r.path AS root,e.generation,CASE WHEN r.path='/' THEN substr(CASE WHEN p.path='/' THEN p.path||e.name ELSE p.path||'/'||e.name END,2) ELSE substr(p.path||'/'||e.name,length(r.path)+2) END AS relative_path,e.name,e.file_identity,e.size,e.mtime_ns FROM entries e JOIN paths p ON p.id=e.directory JOIN root_keys r ON r.id=e.root) SELECT COUNT(*) FROM (SELECT root,generation,relative_path,name,file_identity,size,mtime_ns FROM source.entries EXCEPT SELECT * FROM normalized)`
		if err := candidate.db.QueryRow(query).Scan(&mismatch); err != nil {
			return err
		}
		if mismatch != 0 {
			return errors.New("index migration changed source records")
		}
	}
	if _, err := candidate.db.Exec(`DETACH DATABASE source`); err != nil {
		return err
	}
	if policy != nil {
		engine := newSearchIndexEngine(candidate)
		if _, err := engine.fenceSettings(policy.FenceRoots, policy.SettingsRevision, false, policy.PreserveSnapshots || engine.compatibleStoredPolicy(*policy)); err != nil {
			return err
		}
	}
	if err := candidate.reclaimFreePages(); err != nil {
		return err
	}
	if err := checkpointIndex(candidate.db); err != nil {
		return err
	}
	var sourceCount, candidateCount int
	source, err := readOnlyIndex(path)
	if err != nil {
		return err
	}
	if entriesExist != 0 {
		err = source.QueryRow(`SELECT COUNT(*) FROM entries`).Scan(&sourceCount)
	}
	source.Close()
	if err != nil {
		return err
	}
	if err := candidate.db.QueryRow(`SELECT COUNT(*) FROM entries`).Scan(&candidateCount); err != nil {
		return err
	}
	if policy == nil && sourceCount != candidateCount {
		return errors.New("index migration entry count mismatch")
	}
	if err := candidate.db.Close(); err != nil {
		return err
	}
	if err := validateIndex(publication.Candidate); err != nil {
		return err
	}
	candidateInfo, err := os.Stat(publication.Candidate)
	if err != nil {
		return err
	}
	if info.Size()+candidateInfo.Size() > indexTransientCeiling {
		return errors.New("index migration exceeds transient ceiling")
	}
	if err := syncIndexFile(path); err != nil {
		return err
	}
	if err := syncIndexFile(publication.Candidate); err != nil {
		return err
	}
	if err := os.Link(path, publication.Backup); err != nil {
		return err
	}
	publication.Phase = "backed_up"
	if err := saveIndexPublication(path, publication); err != nil {
		return err
	}
	if err := os.Rename(publication.Candidate, path); err != nil {
		return err
	}
	if err := syncIndexFile(filepath.Dir(path)); err != nil {
		return err
	}
	publication.Phase = "published"
	return saveIndexPublication(path, publication)
}

// A replacement is built in a private compact database. Disk admission uses
// measured allocation, so a rejected refresh cannot shrink the searchable generation.
func (engine *searchIndexEngine) replaceMeasuredSnapshot(ctx context.Context, job indexBuildJob, prior int, paths []discoveredIndexPath, coverage, reason string, started time.Time) (resultErr error) {
	engine.mu.Lock()
	mutation := engine.mutationGeneration
	engine.budgetBytes = job.maxBytes
	engine.mu.Unlock()
	if job.maxBytes <= 0 {
		job.maxBytes = DefaultIndexBytes
	}
	candidatePath := engine.store.path + fmt.Sprintf(".staging-%d", time.Now().UnixNano())
	if engine.store.path == ":memory:" {
		candidatePath = ":memory:"
	}
	var candidate *searchIndexStore
	// This invocation owns the unique staging path even when VACUUM fails before
	// the candidate can be opened. Never sweep files left by other invocations.
	defer func() {
		if candidate != nil {
			resultErr = errors.Join(resultErr, candidate.db.Close())
		}
		if candidatePath != ":memory:" {
			for _, suffix := range []string{"", "-wal", "-shm", "-journal"} {
				if err := os.Remove(candidatePath + suffix); err != nil && !errors.Is(err, os.ErrNotExist) {
					resultErr = errors.Join(resultErr, err)
				}
			}
		}
	}()
	var err error
	if candidatePath != ":memory:" {
		allocated, err := engine.store.allocatedBytes()
		if err != nil {
			return err
		}
		usage, err := engine.store.diskUsage()
		if err != nil {
			return err
		}
		abandoned := usage["staging_bytes"]
		if allocated*2+abandoned+32*1024*1024 > indexTransientCeiling {
			return engine.fail(job.root, job.identity, prior, errors.New("retained staging files block index transient budget"))
		}
		var filesystem unix.Statfs_t
		if err := unix.Statfs(filepath.Dir(candidatePath), &filesystem); err != nil {
			return err
		}
		if int64(filesystem.Bavail)*int64(filesystem.Bsize) < allocated+32*1024*1024 {
			return engine.fail(job.root, job.identity, prior, errors.New("insufficient space for index replacement"))
		}
		if allocated*2+32*1024*1024 > indexTransientCeiling {
			return engine.fail(job.root, job.identity, prior, errors.New("retained index blocks replacement transient budget"))
		}
		if _, err := engine.store.db.ExecContext(ctx, `VACUUM INTO ?`, candidatePath); err != nil {
			return engine.fail(job.root, job.identity, prior, err)
		}
		candidate, err = createSearchIndexStore(candidatePath)
	} else {
		candidate, err = createSearchIndexStore(":memory:")
		// Memory mode has no disk snapshot to reopen; copy supported state through SQLite backup.
		if err == nil {
			err = copyIndexDatabase(engine.store.db, candidate.db)
		}
	}
	if err != nil {
		return engine.fail(job.root, job.identity, prior, err)
	}
	candidateEngine := newSearchIndexEngine(candidate)
	if err := candidate.deleteGenerations(job.root, -1); err != nil {
		return err
	}
	if _, err := candidate.db.Exec(`DELETE FROM roots WHERE root=?`, job.root); err != nil {
		return err
	}
	if err := candidate.reclaimDirectories(); err != nil {
		return err
	}
	if err := candidate.reclaimFreePages(); err != nil {
		return err
	}
	// High-water marks live in the retained store even when a candidate is rejected.
	engine.mu.Lock()
	var generation int
	err = engine.store.db.QueryRow(`SELECT generation FROM root_generations WHERE root=?`, job.root).Scan(&generation)
	if errors.Is(err, sql.ErrNoRows) {
		generation = prior
		err = nil
	}
	if err == nil {
		generation++
		_, err = engine.store.db.Exec(`INSERT INTO root_generations(root,generation) VALUES(?,?) ON CONFLICT(root) DO UPDATE SET generation=excluded.generation`, job.root, generation)
	}
	engine.mu.Unlock()
	if err != nil {
		return err
	}
	if _, err := candidate.db.Exec(`INSERT INTO root_generations(root,generation) VALUES(?,?) ON CONFLICT(root) DO UPDATE SET generation=excluded.generation`, job.root, generation); err != nil {
		return err
	}
	if err := candidate.setStorageBudget(job.maxBytes); err != nil {
		return engine.fail(job.root, job.identity, prior, err)
	}
	count := 0
	for start := 0; start < len(paths); start += indexBatchSize {
		if err := ctx.Err(); err != nil {
			return err
		}
		end := min(start+indexBatchSize, len(paths))
		tx, err := candidate.db.Begin()
		if err != nil {
			return err
		}
		err = writeNormalizedPaths(tx, job.root, generation, paths[start:end])
		if err == nil {
			var pages, free, size int64
			for _, item := range []struct {
				query string
				value *int64
			}{{"PRAGMA page_count", &pages}, {"PRAGMA freelist_count", &free}, {"PRAGMA page_size", &size}} {
				if err = tx.QueryRow(item.query).Scan(item.value); err != nil {
					break
				}
			}
			if err == nil && (pages-free)*size > indexStorageLimit(job.maxBytes) {
				err = errors.New(indexCoverageStorageLimit)
			}
		}
		if err != nil {
			tx.Rollback()
			if prior > 0 || (!isSQLiteStorageFull(err) && err.Error() != indexCoverageStorageLimit) {
				return engine.fail(job.root, job.identity, prior, err)
			}
			coverage, reason = "partial", indexCoverageStorageLimit
			break
		}
		if err := tx.Commit(); err != nil {
			return engine.fail(job.root, job.identity, prior, err)
		}
		count = end
		if err := checkpointIndex(candidate.db); err != nil {
			return engine.fail(job.root, job.identity, prior, err)
		}
		activeBytes, err := engine.store.allocatedBytes()
		if err != nil {
			return err
		}
		_, err = candidate.allocatedBytes()
		if err != nil {
			return err
		}
		usage, err := engine.store.diskUsage()
		if err != nil {
			return err
		}
		if activeBytes+usage["staging_bytes"]+32*1024*1024 > indexTransientCeiling {
			return engine.fail(job.root, job.identity, prior, errors.New("index transient storage ceiling reached"))
		}
	}
	if err := candidate.markRootRefreshing(job.root, job.identity, prior, time.Now().UnixNano()); err != nil {
		return err
	}
	state := "ready"
	if coverage == "partial" {
		state = "partial"
	}
	used, err := candidate.usedBytes()
	if err != nil {
		return err
	}
	snapshot := &searchIndexSnapshot{generation: generation, identity: job.identity, entries: shareIndexDirectories(paths[:count])}
	candidateEngine.settingsFloorSet = false
	candidateEngine.fences[job.root] = job.fence
	if _, err := candidateEngine.publishGeneration(job, generation, state, coverage, count, used, reason, time.Since(started), snapshot); err != nil {
		return err
	}
	if err := candidate.reclaimFreePages(); err != nil {
		return err
	}
	if err := checkpointIndex(candidate.db); err != nil {
		return err
	}
	allocated, err := candidate.allocatedBytes()
	if err != nil {
		return err
	}
	if allocated > job.maxBytes {
		return engine.fail(job.root, job.identity, prior, errors.New(indexCoverageStorageLimit))
	}
	engine.crawlPause.Lock()
	defer engine.crawlPause.Unlock()
	engine.mu.Lock()
	defer engine.mu.Unlock()
	if engine.mutationGeneration != mutation || engine.snapshotMutations > 0 || engine.fences[job.root] != job.fence || !engine.settingsRevisionAcceptedLocked(job.root, job.settingsRevision) {
		return errors.New("index candidate invalidated before publication")
	}
	info, err := os.Stat(job.root)
	if err != nil || fileID(info) != job.identity {
		return errors.New("index root identity changed before publication")
	}
	if engine.store.path == ":memory:" {
		old := engine.store.db
		engine.store.db = candidate.db
		candidate = nil
		engine.snapshots[job.root] = snapshot
		return old.Close()
	}
	if err := candidate.db.Close(); err != nil {
		return err
	}
	if err := validateIndex(candidatePath); err != nil {
		return err
	}
	if err := syncIndexFile(candidatePath); err != nil {
		return err
	}
	if err := checkpointIndex(engine.store.db); err != nil {
		return err
	}
	if err := engine.store.db.Close(); err != nil {
		return err
	}
	// Every exit after close must restore a usable connection, including a
	// directory fsync failure after the atomic pathname publication succeeded.
	defer func() {
		reopened, err := createSearchIndexStore(engine.store.path)
		if err != nil {
			resultErr = errors.Join(resultErr, err)
			return
		}
		engine.store.db = reopened.db
	}()
	if err := os.Rename(candidatePath, engine.store.path); err != nil {
		return err
	}
	if err := syncIndexFile(filepath.Dir(engine.store.path)); err != nil {
		return err
	}
	engine.snapshots[job.root] = snapshot
	return nil
}

func shareIndexDirectories(paths []discoveredIndexPath) []discoveredIndexPath {
	prefixes := map[string]string{}
	for index := range paths {
		relative := paths[index].relative
		if relative == "" {
			continue
		}
		prefix := strings.TrimSuffix(relative, paths[index].name)
		shared, exists := prefixes[prefix]
		if !exists {
			shared = prefix
			prefixes[prefix] = shared
		}
		paths[index].directory = shared
		paths[index].relative = ""
	}
	return paths
}

func copyIndexDatabase(source, destination *sql.DB) error {
	ctx := context.Background()
	sourceConn, err := source.Conn(ctx)
	if err != nil {
		return err
	}
	defer sourceConn.Close()
	destinationConn, err := destination.Conn(ctx)
	if err != nil {
		return err
	}
	defer destinationConn.Close()
	return sourceConn.Raw(func(rawSource any) error {
		return destinationConn.Raw(func(rawDestination any) error {
			backup, err := rawDestination.(*sqlite3.SQLiteConn).Backup("main", rawSource.(*sqlite3.SQLiteConn), "main")
			if err != nil {
				return err
			}
			done, stepErr := backup.Step(-1)
			finishErr := backup.Finish()
			if stepErr != nil {
				return stepErr
			}
			if !done {
				return errors.New("index backup did not finish")
			}
			return finishErr
		})
	})
}

func (store *searchIndexStore) diskUsage() (map[string]int64, error) {
	usage := map[string]int64{"database_bytes": 0, "wal_bytes": 0, "staging_bytes": 0, "backup_bytes": 0}
	if store.path == ":memory:" {
		usage["memory_database_bytes"], _ = store.allocatedBytes()
		return usage, nil
	}
	for suffix, key := range map[string]string{"": "database_bytes", "-wal": "wal_bytes"} {
		info, err := os.Stat(store.path + suffix)
		if errors.Is(err, os.ErrNotExist) {
			continue
		}
		if err != nil {
			return nil, err
		}
		usage[key] = info.Size()
	}
	entries, err := os.ReadDir(filepath.Dir(store.path))
	if err != nil {
		return nil, err
	}
	for _, entry := range entries {
		name := entry.Name()
		base := filepath.Base(store.path)
		key := ""
		if strings.HasPrefix(name, base+".rollback-") || strings.HasPrefix(name, base+".interrupted-") || strings.HasPrefix(name, base+".restore-") {
			key = "backup_bytes"
		}
		if strings.HasPrefix(name, base+".candidate-") || strings.HasPrefix(name, base+".staging-") {
			key = "staging_bytes"
		}
		if key != "" {
			info, err := entry.Info()
			// SQLite can retire a journal between directory enumeration and stat.
			if errors.Is(err, os.ErrNotExist) {
				continue
			}
			if err != nil {
				return nil, err
			}
			usage[key] += info.Size()
		}
	}
	return usage, nil
}

const reclaimDirectoriesSQL = `DELETE FROM directories WHERE id IN (SELECT d.id FROM directories d WHERE d.id<>1 AND d.id NOT IN (SELECT directory FROM entries) AND NOT EXISTS(SELECT 1 FROM directories child WHERE child.parent=d.id) LIMIT 512)`

func (store *searchIndexStore) reclaimDirectories() error {
	// Removing only leaves preserves every ancestry still needed by retained descendants.
	// Materialize referenced directory IDs once; a correlated entries probe scans
	// the entire file table for each directory during startup maintenance.
	_, err := store.db.Exec(reclaimDirectoriesSQL)
	return err
}

package stats

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"syscall"
)

const checkpointSchemaVersion int64 = 10
const checkpointWriterProtocol = 26

var ErrCheckpointConflict = errors.New("stale YO!stats checkpoint revision")

// CheckpointMetadata is safe to retain for scheduling; parser state and exact
// comparison fragments are fetched only after the source receives admission.
type CheckpointMetadata struct {
	Key, Kind, SourceID             string
	Revision                        int64
	Version                         int
	Offset, FileSize, FileModTimeNS int64
	Device, Inode                   uint64
	Metadata                        []byte
	DecodedBytes                    int64
}

type CheckpointComparisonSamples struct {
	Prefix, ParsedTail []byte
}

type Checkpoint struct {
	Key, Kind, SourceID                                                string
	Revision, ExpectedRevision                                         int64
	Version                                                            int
	Offset, FileSize, FileModTimeNS                                    int64
	Device, Inode                                                      uint64
	Metadata                                                           []byte
	DecodedBytes                                                       int64
	State, Prefix, ParsedTail, PendingLine, RebuildBuffer, RebuildLine []byte
}

const checkpointTableSQL = `CREATE TABLE collector_checkpoints (
 checkpoint_key TEXT PRIMARY KEY, kind TEXT NOT NULL, source_id TEXT NOT NULL,
 revision INTEGER NOT NULL CHECK(revision>0), version INTEGER NOT NULL CHECK(version>0),
 byte_offset INTEGER NOT NULL CHECK(byte_offset>=0), file_size INTEGER NOT NULL CHECK(file_size>=0),
 file_mtime_ns INTEGER NOT NULL, device TEXT NOT NULL, inode TEXT NOT NULL,
 metadata BLOB NOT NULL, decoded_bytes INTEGER NOT NULL CHECK(decoded_bytes>=0),
 state BLOB NOT NULL, prefix BLOB NOT NULL, parsed_tail BLOB NOT NULL,
 pending_line BLOB NOT NULL, rebuild_buffer BLOB NOT NULL, rebuild_line BLOB NOT NULL
) WITHOUT ROWID`

func checkpointBlob(value []byte) []byte {
	if value == nil {
		return []byte{}
	}
	return value
}

func writeCheckpoints(ctx context.Context, tx *sql.Tx, checkpoints []Checkpoint) error {
	seen := make(map[string]bool, len(checkpoints))
	for _, c := range checkpoints {
		if c.Key == "" || c.Kind == "" || c.Version < 1 || c.ExpectedRevision < 0 || c.ExpectedRevision == math.MaxInt64 || c.Offset < 0 || c.FileSize < 0 || c.DecodedBytes < 0 || len(c.Metadata) > 64*1024 {
			return errors.New("invalid YO!stats checkpoint")
		}
		if seen[c.Key] {
			return errors.New("duplicate YO!stats checkpoint key")
		}
		seen[c.Key] = true
		size := int64(len(c.State) + len(c.Metadata) + len(c.Prefix) + len(c.ParsedTail) + len(c.PendingLine) + len(c.RebuildBuffer) + len(c.RebuildLine))
		if c.DecodedBytes < size {
			c.DecodedBytes = size
		}
		var revision int64
		err := tx.QueryRowContext(ctx, "SELECT revision FROM collector_checkpoints WHERE checkpoint_key=?", c.Key).Scan(&revision)
		if err != nil && !errors.Is(err, sql.ErrNoRows) {
			return err
		}
		if revision != c.ExpectedRevision {
			return fmt.Errorf("%w: %s", ErrCheckpointConflict, c.Key)
		}
		result, err := tx.ExecContext(ctx, `INSERT INTO collector_checkpoints VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
   ON CONFLICT(checkpoint_key) DO UPDATE SET kind=excluded.kind,source_id=excluded.source_id,revision=excluded.revision,version=excluded.version,
   byte_offset=excluded.byte_offset,file_size=excluded.file_size,file_mtime_ns=excluded.file_mtime_ns,device=excluded.device,inode=excluded.inode,
   metadata=excluded.metadata,decoded_bytes=excluded.decoded_bytes,state=excluded.state,prefix=excluded.prefix,parsed_tail=excluded.parsed_tail,
   pending_line=excluded.pending_line,rebuild_buffer=excluded.rebuild_buffer,rebuild_line=excluded.rebuild_line
   WHERE collector_checkpoints.revision=?`,
			c.Key, c.Kind, c.SourceID, c.ExpectedRevision+1, c.Version, c.Offset, c.FileSize, c.FileModTimeNS, fmt.Sprint(c.Device), fmt.Sprint(c.Inode), checkpointBlob(c.Metadata), c.DecodedBytes,
			checkpointBlob(c.State), checkpointBlob(c.Prefix), checkpointBlob(c.ParsedTail), checkpointBlob(c.PendingLine), checkpointBlob(c.RebuildBuffer), checkpointBlob(c.RebuildLine), c.ExpectedRevision)
		if err != nil {
			return fmt.Errorf("write checkpoint: %w", err)
		}
		count, err := result.RowsAffected()
		if err != nil {
			return err
		}
		if count != 1 {
			return fmt.Errorf("%w: %s", ErrCheckpointConflict, c.Key)
		}

	}
	return nil
}

func (w *Writer) LoadCheckpoint(ctx context.Context, key string) (Checkpoint, bool, error) {
	var c Checkpoint
	if w == nil || w.db == nil || w.closed.Load() {
		return c, false, errors.New("nil YO!stats writer")
	}
	err := w.db.QueryRowContext(ctx, `SELECT checkpoint_key,kind,source_id,revision,version,byte_offset,file_size,file_mtime_ns,device,inode,metadata,decoded_bytes,state,prefix,parsed_tail,pending_line,rebuild_buffer,rebuild_line FROM collector_checkpoints WHERE checkpoint_key=?`, key).Scan(
		&c.Key, &c.Kind, &c.SourceID, &c.Revision, &c.Version, &c.Offset, &c.FileSize, &c.FileModTimeNS, &c.Device, &c.Inode, &c.Metadata, &c.DecodedBytes, &c.State, &c.Prefix, &c.ParsedTail, &c.PendingLine, &c.RebuildBuffer, &c.RebuildLine)
	if errors.Is(err, sql.ErrNoRows) {
		return Checkpoint{}, false, nil
	}
	c.ExpectedRevision = c.Revision
	return c, err == nil, err
}

func (w *Writer) ListCheckpointMetadata(ctx context.Context, kind string) ([]CheckpointMetadata, error) {
	if w == nil || w.db == nil || w.closed.Load() {
		return nil, errors.New("nil YO!stats writer")
	}
	rows, err := w.db.QueryContext(ctx, `SELECT checkpoint_key,kind,source_id,revision,version,byte_offset,file_size,file_mtime_ns,device,inode,metadata,decoded_bytes FROM collector_checkpoints WHERE kind=? ORDER BY checkpoint_key`, kind)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var result []CheckpointMetadata
	for rows.Next() {
		var c CheckpointMetadata
		if err := rows.Scan(&c.Key, &c.Kind, &c.SourceID, &c.Revision, &c.Version, &c.Offset, &c.FileSize, &c.FileModTimeNS, &c.Device, &c.Inode, &c.Metadata, &c.DecodedBytes); err != nil {
			return nil, err
		}
		result = append(result, c)
	}
	return result, rows.Err()
}

// ListCheckpointMetadataForKeys bounds retained metadata to sources currently
// participating in collector scheduling instead of copying every historical
// checkpoint into memory on each cycle.
func (w *Writer) ListCheckpointMetadataForKeys(ctx context.Context, kind string, keys []string) ([]CheckpointMetadata, error) {
	if w == nil || w.db == nil || w.closed.Load() {
		return nil, errors.New("nil YO!stats writer")
	}
	if len(keys) == 0 {
		return nil, nil
	}
	const queryBatchSize = 500
	result := make([]CheckpointMetadata, 0, len(keys))
	seen := make(map[string]struct{}, len(keys))
	for start := 0; start < len(keys); start += queryBatchSize {
		end := start + queryBatchSize
		if end > len(keys) {
			end = len(keys)
		}
		placeholders := make([]string, end-start)
		args := make([]any, 0, end-start+1)
		args = append(args, kind)
		for i, key := range keys[start:end] {
			placeholders[i] = "?"
			args = append(args, key)
		}
		rows, err := w.db.QueryContext(ctx, `SELECT checkpoint_key,kind,source_id,revision,version,byte_offset,file_size,file_mtime_ns,device,inode,metadata,decoded_bytes FROM collector_checkpoints WHERE kind=? AND checkpoint_key IN (`+strings.Join(placeholders, ",")+") ORDER BY checkpoint_key", args...)
		if err != nil {
			return nil, err
		}
		for rows.Next() {
			var checkpoint CheckpointMetadata
			if err := rows.Scan(&checkpoint.Key, &checkpoint.Kind, &checkpoint.SourceID, &checkpoint.Revision, &checkpoint.Version, &checkpoint.Offset, &checkpoint.FileSize, &checkpoint.FileModTimeNS, &checkpoint.Device, &checkpoint.Inode, &checkpoint.Metadata, &checkpoint.DecodedBytes); err != nil {
				_ = rows.Close()
				return nil, err
			}
			if _, exists := seen[checkpoint.Key]; !exists {
				seen[checkpoint.Key] = struct{}{}
				result = append(result, checkpoint)
			}
		}
		if err := rows.Err(); err != nil {
			_ = rows.Close()
			return nil, err
		}
		if err := rows.Close(); err != nil {
			return nil, err
		}
	}
	return result, nil
}

// LoadCheckpointComparisonSamples reads bounded exact fragments only after a
// source passes lightweight metadata classification and collector admission.
func (w *Writer) LoadCheckpointComparisonSamples(ctx context.Context, key string) (CheckpointComparisonSamples, error) {
	var samples CheckpointComparisonSamples
	if w == nil || w.db == nil || w.closed.Load() {
		return samples, errors.New("nil YO!stats writer")
	}
	err := w.db.QueryRowContext(ctx, `SELECT prefix,parsed_tail FROM collector_checkpoints WHERE checkpoint_key=?`, key).Scan(&samples.Prefix, &samples.ParsedTail)
	if err != nil {
		return CheckpointComparisonSamples{}, err
	}
	return samples, nil
}

type CheckpointRetirement struct {
	Kind                        string
	KeepKeys                    []string
	PreserveExistingSourceFiles bool
}

func writeCheckpointRetirement(ctx context.Context, tx *sql.Tx, retirement *CheckpointRetirement) error {
	if retirement == nil {
		return nil
	}
	if retirement.Kind == "" {
		return errors.New("invalid YO!stats checkpoint retirement")
	}
	if _, err := tx.ExecContext(ctx, `CREATE TEMP TABLE IF NOT EXISTS checkpoint_retirement_keys(checkpoint_key TEXT PRIMARY KEY) WITHOUT ROWID`); err != nil {
		return err
	}
	if _, err := tx.ExecContext(ctx, `DELETE FROM checkpoint_retirement_keys`); err != nil {
		return err
	}
	for _, key := range retirement.KeepKeys {
		if key == "" {
			return errors.New("invalid empty checkpoint retirement key")
		}
		if _, err := tx.ExecContext(ctx, `INSERT OR IGNORE INTO checkpoint_retirement_keys(checkpoint_key) VALUES(?)`, key); err != nil {
			return err
		}
	}
	if retirement.PreserveExistingSourceFiles {
		rows, err := tx.QueryContext(ctx, `SELECT checkpoint_key,source_id FROM collector_checkpoints
		 WHERE kind=? AND checkpoint_key NOT LIKE 'codex-fork-repair%' AND checkpoint_key NOT IN (SELECT checkpoint_key FROM checkpoint_retirement_keys)`, retirement.Kind)
		if err != nil {
			return err
		}
		var existingKeys []string
		for rows.Next() {
			var key, sourceID string
			if err := rows.Scan(&key, &sourceID); err != nil {
				_ = rows.Close()
				return err
			}
			if sourceID == "" {
				continue
			}
			if _, err := os.Stat(sourceID); err == nil || !os.IsNotExist(err) {
				existingKeys = append(existingKeys, key)
			}
		}
		if err := rows.Err(); err != nil {
			_ = rows.Close()
			return err
		}
		if err := rows.Close(); err != nil {
			return err
		}
		for _, key := range existingKeys {
			if _, err := tx.ExecContext(ctx, `INSERT OR IGNORE INTO checkpoint_retirement_keys(checkpoint_key) VALUES(?)`, key); err != nil {
				return err
			}
		}
	}
	// Fork-repair cursors are retained until their dedicated frontier can prove
	// that a repair root is complete; ordinary transcript retirement must not
	// erase deferred repair progress.
	var exhausted int64
	if err := tx.QueryRowContext(ctx, `SELECT COUNT(*) FROM collector_checkpoints
	 WHERE kind=? AND checkpoint_key NOT LIKE 'codex-fork-repair%' AND checkpoint_key NOT IN (SELECT checkpoint_key FROM checkpoint_retirement_keys) AND revision=?`, retirement.Kind, int64(math.MaxInt64)).Scan(&exhausted); err != nil {
		return err
	}
	if exhausted > 0 {
		return errors.New("YO!stats checkpoint revision exhausted during retirement")
	}
	if _, err := tx.ExecContext(ctx, `UPDATE collector_checkpoints SET kind='retired',source_id='',revision=revision+1,version=1,
	 byte_offset=0,file_size=0,file_mtime_ns=0,device='0',inode='0',metadata=x'',decoded_bytes=0,state=x'',prefix=x'',parsed_tail=x'',
	 pending_line=x'',rebuild_buffer=x'',rebuild_line=x''
	 WHERE kind=? AND checkpoint_key NOT LIKE 'codex-fork-repair%' AND checkpoint_key NOT IN (SELECT checkpoint_key FROM checkpoint_retirement_keys)`, retirement.Kind); err != nil {
		return err
	}
	_, err := tx.ExecContext(ctx, `DROP TABLE checkpoint_retirement_keys`)
	return err
}

// EnsureCheckpoints migrates only when a collector opts into durable progress.
// The writer lease and legacy cursor locks keep the backup pair consistent.
// A failed migration leaves the original schema and every backup intact.
func (w *Writer) EnsureCheckpoints(ctx context.Context, legacyPaths []string) error {
	if w == nil || w.db == nil || w.closed.Load() || w.lease == nil {
		return errors.New("nil YO!stats writer")
	}
	w.mutationMu.Lock()
	defer w.mutationMu.Unlock()
	var version int64
	if err := w.db.QueryRowContext(ctx, "PRAGMA user_version").Scan(&version); err != nil {
		return err
	}
	if version == checkpointSchemaVersion {
		return publishWriterFenceVersion(w.lease.path, checkpointSchemaVersion, checkpointWriterProtocol)
	}
	if version != SchemaVersion {
		return errors.New("unsupported checkpoint migration source")
	}
	ownedChanges, err := w.startupOwnedChanges(ctx)
	if err != nil {
		return err
	}
	defer w.excludeOwnedStartupChanges(ownedChanges)
	paths := append([]string(nil), legacyPaths...)
	sort.Strings(paths)
	var locks []*os.File
	defer func() {
		for i := len(locks) - 1; i >= 0; i-- {
			_ = syscall.Flock(int(locks[i].Fd()), syscall.LOCK_UN)
			_ = locks[i].Close()
		}
	}()
	previous := ""
	for _, path := range paths {
		if path == "" || path == previous {
			continue
		}
		previous = path
		if _, err := os.Stat(filepath.Dir(path)); os.IsNotExist(err) {
			continue
		} else if err != nil {
			return err
		}
		f, err := os.OpenFile(path+".lock", os.O_CREATE|os.O_RDWR, 0600)
		if err != nil {
			return err
		}
		if err = syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
			_ = f.Close()
			return fmt.Errorf("legacy checkpoint busy: %w", err)
		}
		locks = append(locks, f)
	}
	backup, err := os.MkdirTemp(filepath.Dir(w.lease.path), filepath.Base(w.lease.path)+".pre-checkpoints-")
	if err != nil {
		return err
	}
	if _, err = w.db.ExecContext(ctx, "VACUUM INTO ?", filepath.Join(backup, filepath.Base(w.lease.path))); err != nil {
		return fmt.Errorf("backup checkpoint database: %w", err)
	}
	manifest := make(map[string]string)
	paths = append(paths, filepath.Join(filepath.Dir(w.lease.path), writerFenceFilename))
	for i, path := range paths {
		if path == "" {
			continue
		}
		data, readErr := os.ReadFile(path)
		if os.IsNotExist(readErr) {
			continue
		}
		if readErr != nil {
			return readErr
		}
		name := fmt.Sprintf("%03d-%s", i, filepath.Base(path))
		if err := writeSyncedFile(filepath.Join(backup, name), data); err != nil {
			return err
		}
		manifest[path] = name
	}
	data, err := json.Marshal(manifest)
	if err != nil {
		return err
	}
	if err := writeSyncedFile(filepath.Join(backup, "cursor-manifest.json"), data); err != nil {
		return err
	}
	// Sync the database and directory before publishing the schema transition.
	f, err := os.Open(filepath.Join(backup, filepath.Base(w.lease.path)))
	if err != nil {
		return err
	}
	err = f.Sync()
	closeErr := f.Close()
	if err != nil {
		return err
	}
	if closeErr != nil {
		return closeErr
	}
	if err := syncDirectory(backup); err != nil {
		return err
	}
	if err := syncDirectory(filepath.Dir(backup)); err != nil {
		return err
	}
	tx, err := w.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	for _, statement := range []string{checkpointTableSQL, "CREATE INDEX collector_checkpoints_kind ON collector_checkpoints(kind)", "PRAGMA user_version=10", "UPDATE schema_meta SET minimum_writer_protocol=26,minimum_writer_build=8 WHERE singleton=1"} {
		if _, err := tx.ExecContext(ctx, statement); err != nil {
			return err
		}
	}
	if _, err := tx.ExecContext(ctx, `INSERT INTO migration_reconciliation(migration_id,completed_at,source_digest,details_json) VALUES('go-checkpoints-v1',strftime('%s','now'),?,?)`, backup, string(data)); err != nil {
		return err
	}
	if err := tx.Commit(); err != nil {
		return err
	}
	return publishWriterFenceVersion(w.lease.path, checkpointSchemaVersion, checkpointWriterProtocol)
}

func writeSyncedFile(path string, data []byte) error {
	f, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		return err
	}
	_, err = f.Write(data)
	if err == nil {
		err = f.Sync()
	}
	closeErr := f.Close()
	if err != nil {
		return err
	}
	return closeErr
}
func syncDirectory(path string) error {
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	err = f.Sync()
	closeErr := f.Close()
	if err != nil {
		return err
	}
	return closeErr
}

// Track connection-local mutations separately from external data_version.
// SQLite total_changes includes rolled-back writes; a rejected checkpoint must
// not make the next append mistake its own changes for imported facts.
func (w *Writer) startupOwnedChanges(ctx context.Context) (int64, error) {
	if !w.startupReconciledDataReady {
		return 0, nil
	}
	var before int64
	err := w.db.QueryRowContext(ctx, "SELECT total_changes()").Scan(&before)
	return before, err
}
func (w *Writer) excludeOwnedStartupChanges(before int64) {
	if !w.startupReconciledDataReady {
		return
	}
	var after int64
	if err := w.db.QueryRowContext(context.Background(), "SELECT total_changes()").Scan(&after); err != nil {
		// An unavailable performance baseline falls back to reconciliation.
		w.startupReconciledDataReady = false
		return
	}
	w.startupReconciledTotalChanges += after - before
}

package stats

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"syscall"
)

const writerFenceFilename = "stats-writer-compat-v9.json"

type writerLease struct {
	file *os.File
	path string
}

type writerFence struct {
	ApplicationID         uint32 `json:"application_id"`
	DatabaseFilename      string `json:"database_filename"`
	SchemaVersion         int64  `json:"schema_version"`
	MinimumWriterProtocol int64  `json:"minimum_writer_protocol"`
	MinimumWriterBuild    int64  `json:"minimum_writer_build"`
}

// canonicalWriterPath resolves aliases of the database's parent directory so
// Python and Go contend on one lease and one fence even when their configured
// paths use different symlinked roots. The database file itself must not be a
// symlink; resolving it here would otherwise make a rejected path writable.
func canonicalWriterPath(path string) (string, error) {
	abs, err := filepath.Abs(path)
	if err != nil {
		return "", fmt.Errorf("resolve YO!stats database path: %w", err)
	}
	info, err := os.Lstat(abs)
	if err == nil && info.Mode()&os.ModeSymlink != 0 {
		return "", fmt.Errorf("YO!stats database cannot be a symbolic link")
	}
	if err != nil && !os.IsNotExist(err) {
		return "", fmt.Errorf("inspect YO!stats database path: %w", err)
	}
	parent, err := filepath.EvalSymlinks(filepath.Dir(abs))
	if err != nil {
		if !os.IsNotExist(err) {
			return "", fmt.Errorf("resolve YO!stats database directory: %w", err)
		}
		parent = filepath.Dir(abs)
	}
	return filepath.Join(parent, filepath.Base(abs)), nil
}

func acquireWriterLease(path string) (*writerLease, error) {
	abs, err := canonicalWriterPath(path)
	if err != nil {
		return nil, fmt.Errorf("resolve YO!stats writer lease path: %w", err)
	}
	file, err := os.OpenFile(abs+".writer.lock", os.O_CREATE|os.O_RDWR, 0o600)
	if err != nil {
		return nil, fmt.Errorf("open YO!stats writer lease: %w", err)
	}
	if err := syscall.Flock(int(file.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		_ = file.Close()
		if errors.Is(err, syscall.EWOULDBLOCK) || errors.Is(err, syscall.EAGAIN) {
			return nil, fmt.Errorf("YO!stats writer is already owned")
		}
		return nil, fmt.Errorf("acquire YO!stats writer lease: %w", err)
	}
	return &writerLease{file: file, path: abs}, nil
}

func (lease *writerLease) close() error {
	if lease == nil || lease.file == nil {
		return nil
	}
	err := syscall.Flock(int(lease.file.Fd()), syscall.LOCK_UN)
	closeErr := lease.file.Close()
	if err != nil {
		return err
	}
	return closeErr
}

func validateWriterFence(path string) error {
	canonical, err := canonicalWriterPath(path)
	if err != nil {
		return err
	}
	path = canonical
	fencePath := filepath.Join(filepath.Dir(path), writerFenceFilename)
	data, err := os.ReadFile(fencePath)
	if os.IsNotExist(err) {
		return nil
	}
	if err != nil {
		return fmt.Errorf("read YO!stats writer fence: %w", err)
	}
	var fence writerFence
	if err := json.Unmarshal(data, &fence); err != nil {
		return fmt.Errorf("writer fence is malformed: %w", err)
	}
	if fence.ApplicationID != ApplicationID || fence.DatabaseFilename != filepath.Base(path) || (fence.SchemaVersion != SchemaVersion && fence.SchemaVersion != checkpointSchemaVersion) || fence.MinimumWriterProtocol > checkpointWriterProtocol || fence.MinimumWriterBuild > writerBuild {
		return &SchemaTooNewError{Found: fence.SchemaVersion, Supported: SchemaVersion}
	}
	return nil
}

func writerReadOnlyURI(path string) string {
	abs, _ := filepath.Abs(path)
	// `mode=ro` still reads a committed WAL after an owner is terminated. Do not
	// use `immutable=1` here: immutable connections ignore the WAL, so a clean
	// schema created immediately before a crash can look like an incompatible
	// empty database on the next writer startup. Read-only mode prevents this
	// preflight from checkpointing or otherwise mutating the store.
	return "file:" + filepath.ToSlash(abs) + "?mode=ro&_busy_timeout=5000"
}

func publishWriterFence(path string) error {
	return publishWriterFenceVersion(path, SchemaVersion, writerProtocol)
}

func publishWriterFenceVersion(path string, version int64, protocol int) error {
	fence := writerFence{ApplicationID: ApplicationID, DatabaseFilename: filepath.Base(path), SchemaVersion: version, MinimumWriterProtocol: int64(protocol), MinimumWriterBuild: writerBuild}
	data, err := json.Marshal(fence)
	if err != nil {
		return err
	}
	dir := filepath.Dir(path)
	temp, err := os.CreateTemp(dir, ".stats-writer-fence-*")
	if err != nil {
		return err
	}
	tempName := temp.Name()
	defer os.Remove(tempName)
	if err := temp.Chmod(0o600); err != nil {
		_ = temp.Close()
		return err
	}
	if _, err := temp.Write(append(data, '\n')); err != nil {
		_ = temp.Close()
		return err
	}
	if err := temp.Sync(); err != nil {
		_ = temp.Close()
		return err
	}
	if err := temp.Close(); err != nil {
		return err
	}
	return os.Rename(tempName, filepath.Join(dir, writerFenceFilename))
}

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

func acquireWriterLease(path string) (*writerLease, error) {
	file, err := os.OpenFile(path+".writer.lock", os.O_CREATE|os.O_RDWR, 0o600)
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
	return &writerLease{file: file, path: path}, nil
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
	if fence.ApplicationID != ApplicationID || fence.DatabaseFilename != filepath.Base(path) || fence.SchemaVersion != SchemaVersion || fence.MinimumWriterProtocol > writerProtocol || fence.MinimumWriterBuild > writerBuild {
		return &SchemaTooNewError{Found: fence.SchemaVersion, Supported: SchemaVersion}
	}
	return nil
}

func publishWriterFence(path string) error {
	fence := writerFence{ApplicationID: ApplicationID, DatabaseFilename: filepath.Base(path), SchemaVersion: SchemaVersion, MinimumWriterProtocol: writerProtocol, MinimumWriterBuild: writerBuild}
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

package stats

import (
	"os"
	"path/filepath"
	"syscall"
)

// withSidecarLock serializes cursor read/modify/write transactions across
// processes. The in-memory mutex on each cursor only protects one owner.
func withSidecarLock(path string, fn func() error) error {
	lockPath := path + ".lock"
	if err := os.MkdirAll(filepath.Dir(lockPath), 0o700); err != nil {
		return err
	}
	lock, err := os.OpenFile(lockPath, os.O_CREATE|os.O_RDWR, 0o600)
	if err != nil {
		return err
	}
	defer lock.Close()
	if err := syscall.Flock(int(lock.Fd()), syscall.LOCK_EX); err != nil {
		return err
	}
	defer syscall.Flock(int(lock.Fd()), syscall.LOCK_UN)
	return fn()
}

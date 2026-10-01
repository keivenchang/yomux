//go:build linux

package stats

import (
	"fmt"
	"io"
	"os"
	"reflect"
	"syscall"
)

const codexDirentReadBufferBytes = 32 * 1024

func readCodexDirectoryPage(file *os.File, cookie int64) ([]string, int64, bool, error) {
	if cookie < 0 {
		return nil, cookie, false, fmt.Errorf("Codex directory continuation is negative")
	}
	fd := int(file.Fd())
	if cookie > 0 {
		if _, err := syscall.Seek(fd, cookie, io.SeekStart); err != nil {
			return nil, cookie, false, fmt.Errorf("seek Codex directory continuation: %w", err)
		}
	}
	buffer := make([]byte, codexDirentReadBufferBytes)
	count, err := syscall.ReadDirent(fd, buffer)
	if err != nil {
		return nil, cookie, false, fmt.Errorf("read Codex directory page: %w", err)
	}
	nextCookie, err := syscall.Seek(fd, 0, io.SeekCurrent)
	if err != nil {
		return nil, cookie, false, fmt.Errorf("read Codex directory continuation: %w", err)
	}
	if nextCookie < 0 {
		return nil, cookie, false, fmt.Errorf("Codex directory continuation is negative")
	}
	_, _, names := syscall.ParseDirent(buffer[:count], -1, nil)
	return names, nextCookie, count == 0, nil
}

func codexDirectoryChangeTimeNS(info os.FileInfo) int64 {
	stat, ok := info.Sys().(*syscall.Stat_t)
	if !ok {
		return 0
	}
	value := reflect.ValueOf(stat).Elem()
	for _, name := range []string{"Ctim", "Ctimespec"} {
		timestamp := value.FieldByName(name)
		if !timestamp.IsValid() {
			continue
		}
		seconds, nanoseconds := timestamp.FieldByName("Sec"), timestamp.FieldByName("Nsec")
		if seconds.IsValid() && nanoseconds.IsValid() {
			return seconds.Int()*1e9 + nanoseconds.Int()
		}
	}
	return 0
}

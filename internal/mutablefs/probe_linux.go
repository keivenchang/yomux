// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package mutablefs

import (
	"fmt"
	"syscall"
)

func filesystemType(path string) (string, error) {
	var stat syscall.Statfs_t
	if err := syscall.Statfs(path, &stat); err != nil {
		return "", err
	}
	switch uint64(stat.Type) {
	case 0xef53:
		return "ext4", nil
	case 0x9123683e:
		return "btrfs", nil
	case 0x794c7630:
		return "overlay", nil
	case 0x01021994:
		return "tmpfs", nil
	case 0x58465342:
		return "xfs", nil
	case 0x2fc12fc1:
		return "zfs", nil
	case 0x6969:
		return "nfs", nil
	case 0xff534d42, 0xfe534d42:
		return "cifs", nil
	case 0x01021997:
		return "9p", nil
	case 0x65735546:
		return "fuse", nil
	}
	return fmt.Sprintf("unknown:0x%x", uint64(stat.Type)), nil
}

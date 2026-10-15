// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"fmt"
	"os"
	"syscall"
	"unsafe"
)

func openPTY(rows, cols int) (*ptyPair, error) {
	masterFD, err := syscall.Open("/dev/ptmx", syscall.O_RDWR|syscall.O_NOCTTY|syscall.O_CLOEXEC|syscall.O_NONBLOCK, 0)
	if err != nil {
		return nil, err
	}
	unlock := 0
	if _, _, errno := syscall.Syscall(syscall.SYS_IOCTL, uintptr(masterFD), uintptr(0x40045431), uintptr(unsafe.Pointer(&unlock))); errno != 0 {
		_ = syscall.Close(masterFD)
		return nil, errno
	}
	ptyNumber := 0
	if _, _, errno := syscall.Syscall(syscall.SYS_IOCTL, uintptr(masterFD), uintptr(0x80045430), uintptr(unsafe.Pointer(&ptyNumber))); errno != 0 {
		_ = syscall.Close(masterFD)
		return nil, errno
	}
	slaveFD, err := syscall.Open(fmt.Sprintf("/dev/pts/%d", ptyNumber), syscall.O_RDWR|syscall.O_NOCTTY|syscall.O_CLOEXEC, 0)
	if err != nil {
		_ = syscall.Close(masterFD)
		return nil, err
	}
	pair := &ptyPair{
		master: os.NewFile(uintptr(masterFD), "pty-master"),
		slave:  os.NewFile(uintptr(slaveFD), "pty-slave"),
		tty:    fmt.Sprintf("/dev/pts/%d", ptyNumber),
	}
	if err := pair.resize(rows, cols); err != nil {
		pair.close()
		return nil, err
	}
	return pair, nil
}

func (p *ptyPair) resizeDimensions(rows, cols int) error {
	size := winsize{rows: uint16(rows), cols: uint16(cols)}
	return p.withMasterDescriptor(func(fd uintptr) error {
		_, _, errno := syscall.Syscall(syscall.SYS_IOCTL, fd, uintptr(0x5414), uintptr(unsafe.Pointer(&size)))
		if errno != 0 {
			return errno
		}
		return nil
	})
}

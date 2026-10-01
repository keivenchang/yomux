// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
//go:build darwin

package main

import (
	"fmt"
	"os"
	"unsafe"

	"golang.org/x/sys/unix"
)

func openPTY(rows, cols int) (*ptyPair, error) {
	masterFD, err := unix.Open("/dev/ptmx", unix.O_RDWR|unix.O_NOCTTY|unix.O_CLOEXEC, 0)
	if err != nil {
		return nil, err
	}
	if err = unix.IoctlSetInt(masterFD, unix.TIOCPTYGRANT, 0); err != nil {
		_ = unix.Close(masterFD)
		return nil, err
	}
	if err = unix.IoctlSetInt(masterFD, unix.TIOCPTYUNLK, 0); err != nil {
		_ = unix.Close(masterFD)
		return nil, err
	}
	name := make([]byte, 256)
	_, _, errno := unix.Syscall(unix.SYS_IOCTL, uintptr(masterFD), uintptr(unix.TIOCPTYGNAME), uintptr(unsafe.Pointer(&name[0])))
	if errno != 0 {
		_ = unix.Close(masterFD)
		return nil, errno
	}
	end := 0
	for end < len(name) && name[end] != 0 {
		end++
	}
	if end == len(name) {
		_ = unix.Close(masterFD)
		return nil, fmt.Errorf("PTY slave name was not terminated")
	}
	slaveFD, err := unix.Open(string(name[:end]), unix.O_RDWR|unix.O_NOCTTY|unix.O_CLOEXEC, 0)
	if err != nil {
		_ = unix.Close(masterFD)
		return nil, err
	}
	pair := &ptyPair{master: os.NewFile(uintptr(masterFD), "pty-master"), slave: os.NewFile(uintptr(slaveFD), "pty-slave"), tty: string(name[:end])}
	if err := pair.resize(rows, cols); err != nil {
		pair.close()
		return nil, err
	}
	return pair, nil
}
func (p *ptyPair) resize(rows, cols int) error {
	size := winsize{rows: uint16(rows), cols: uint16(cols)}
	return unix.IoctlSetWinsize(int(p.master.Fd()), unix.TIOCSWINSZ, &unix.Winsize{Row: size.rows, Col: size.cols})
}

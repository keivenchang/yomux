// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"errors"
	"fmt"
	"time"

	"golang.org/x/sys/unix"
)

func nativeAgentTestObserveProcessExit(pid int, deadline time.Time) (func() error, func() error, error) {
	fd, err := unix.Kqueue()
	if err != nil {
		return nil, nil, err
	}
	unix.CloseOnExec(fd)
	change := unix.Kevent_t{Fflags: unix.NOTE_EXIT}
	unix.SetKevent(&change, pid, unix.EVFILT_PROC, unix.EV_ADD|unix.EV_ENABLE|unix.EV_ONESHOT)
	if _, err := unix.Kevent(fd, []unix.Kevent_t{change}, nil, nil); err != nil {
		return nil, nil, errors.Join(err, unix.Close(fd))
	}
	wait := func() error {
		for {
			remaining := time.Until(deadline)
			if remaining <= 0 {
				return fmt.Errorf("provider child %d did not exit before the fixture deadline", pid)
			}
			timeout := unix.NsecToTimespec(remaining.Nanoseconds())
			events := make([]unix.Kevent_t, 1)
			n, waitErr := unix.Kevent(fd, nil, events, &timeout)
			if errors.Is(waitErr, unix.EINTR) {
				continue
			}
			if waitErr != nil {
				return waitErr
			}
			if n != 1 || events[0].Ident != uint64(pid) || events[0].Fflags&unix.NOTE_EXIT == 0 || events[0].Flags&unix.EV_ERROR != 0 {
				return fmt.Errorf("provider child %d exit event missing: events=%v", pid, events[:n])
			}
			return nil
		}
	}
	return wait, func() error { return unix.Close(fd) }, nil
}

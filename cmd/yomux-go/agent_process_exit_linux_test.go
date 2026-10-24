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
	fd, err := unix.PidfdOpen(pid, 0)
	if err != nil {
		return nil, nil, err
	}
	wait := func() error {
		for {
			remaining := time.Until(deadline)
			if remaining <= 0 {
				return fmt.Errorf("provider child %d did not exit before the fixture deadline", pid)
			}
			events := []unix.PollFd{{Fd: int32(fd), Events: unix.POLLIN}}
			n, pollErr := unix.Poll(events, int((remaining+time.Millisecond-1)/time.Millisecond))
			if errors.Is(pollErr, unix.EINTR) {
				continue
			}
			if pollErr != nil {
				return pollErr
			}
			if n == 0 || events[0].Revents&(unix.POLLIN|unix.POLLHUP) == 0 {
				return fmt.Errorf("provider child %d exit event missing: events=%d", pid, events[0].Revents)
			}
			return nil
		}
	}
	return wait, func() error { return unix.Close(fd) }, nil
}

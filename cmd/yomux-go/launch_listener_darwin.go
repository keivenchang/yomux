// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import "golang.org/x/sys/unix"

func launchSocketListening(fd int) (bool, error) {
	// Darwin exposes SO_ACCEPTCONN as a socket flag, but not as a getsockopt
	// query. Reading TCP state also leaves queued connections untouched.
	info, err := unix.GetsockoptTCPConnectionInfo(fd, unix.IPPROTO_TCP, unix.TCP_CONNECTION_INFO)
	if err != nil {
		return false, err
	}
	const tcpListen = 1 // TCPS_LISTEN in netinet/tcp_fsm.h.
	return info.State == tcpListen, nil
}

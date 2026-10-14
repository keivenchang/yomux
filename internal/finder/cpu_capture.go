// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package finder

import (
	"bytes"
	"errors"
	"runtime/pprof"
	"sync"
	"time"
)

// CPUCapture is shared by the server and its index worker. Only completed, bounded
// profiles are retained, and capture never holds an HTTP or worker request open.
type CPUCapture struct {
	mu        sync.Mutex
	active    bool
	started   time.Time
	seconds   int
	artifact  []byte
	lastError string
}

type boundedCPUBuffer struct {
	bytes.Buffer
	overflow bool
}

func (b *boundedCPUBuffer) Write(data []byte) (int, error) {
	if len(data)+b.Len() > 32*1024*1024 {
		b.overflow = true
		return 0, errors.New("CPU profile exceeds 32 MiB")
	}
	return b.Buffer.Write(data)
}

func (p *CPUCapture) Start(seconds int) error {
	if seconds == 0 {
		seconds = 30
	}
	if seconds < 1 || seconds > 60 {
		return errors.New("capture duration must be 1-60 seconds")
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.active {
		return errors.New("CPU capture already running")
	}
	buffer := &boundedCPUBuffer{}
	if err := pprof.StartCPUProfile(buffer); err != nil {
		return err
	}
	p.active = true
	p.started = time.Now()
	p.seconds = seconds
	p.lastError = ""
	p.artifact = nil
	go func() {
		timer := time.NewTimer(time.Duration(seconds) * time.Second)
		<-timer.C
		pprof.StopCPUProfile()
		p.mu.Lock()
		defer p.mu.Unlock()
		p.active = false
		if buffer.overflow {
			p.lastError = "CPU profile exceeds 32 MiB"
			return
		}
		p.artifact = append([]byte(nil), buffer.Bytes()...)
	}()
	return nil
}

func (p *CPUCapture) Status() map[string]any {
	p.mu.Lock()
	defer p.mu.Unlock()
	return map[string]any{"active": p.active, "started": p.started.Format(time.RFC3339), "seconds": p.seconds, "artifact_bytes": len(p.artifact), "error": p.lastError}
}
func (p *CPUCapture) Artifact() []byte {
	p.mu.Lock()
	defer p.mu.Unlock()
	return append([]byte(nil), p.artifact...)
}

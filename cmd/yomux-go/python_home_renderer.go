// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"os/exec"
	"sync"
	"time"
)

const pythonHomeRendererScript = `import json,sys
from yomux_lib.web import html_page
for line in sys.stdin:
 request=json.loads(line)
 html=html_page(request["sessions"], access_role=request["role"], auth_username=request["username"], accept_language=request["language"], terminal_only=False, capability_snapshot=request["capabilities"], stats_only=request["stats_only"], backend_label=request["backend"], dangerously_yolo=request["dangerously_yolo"])
 response={"html":html}
 print(json.dumps(response,separators=(",",":")),flush=True)
`

type pythonHomeRenderer struct {
	command   *exec.Cmd
	input     io.WriteCloser
	output    *bufio.Reader
	done      chan struct{}
	closeOnce sync.Once
}

func startPythonHomeRenderer(root string, config string) (*pythonHomeRenderer, error) {
	command := exec.Command("python3", "-u", "-c", pythonHomeRendererScript)
	command.Dir = root
	command.Env = pythonBridgeEnvironment(root, config)
	command.Stderr = os.Stderr
	input, err := command.StdinPipe()
	if err != nil {
		return nil, err
	}
	output, err := command.StdoutPipe()
	if err != nil {
		_ = input.Close()
		return nil, err
	}
	if err := command.Start(); err != nil {
		_ = input.Close()
		return nil, err
	}
	renderer := &pythonHomeRenderer{command: command, input: input, output: bufio.NewReader(output), done: make(chan struct{})}
	go func() { _ = command.Wait(); close(renderer.done) }()
	return renderer, nil
}

func (renderer *pythonHomeRenderer) render(request map[string]any) ([]byte, error) {
	encoded, err := json.Marshal(request)
	if err != nil {
		return nil, err
	}
	if _, err := renderer.input.Write(append(encoded, '\n')); err != nil {
		return nil, err
	}
	line, err := readRendererLine(renderer.output)
	if err != nil {
		return nil, err
	}
	var response struct {
		HTML string `json:"html"`
	}
	if err := json.Unmarshal(line, &response); err != nil {
		return nil, err
	}
	if response.HTML == "" {
		return nil, fmt.Errorf("Python HTML renderer returned no page")
	}
	return []byte(response.HTML), nil
}

const pythonRenderTimeout = 10 * time.Second
const pythonRenderLimit = 8 * 1024 * 1024

func readRendererLine(reader *bufio.Reader) ([]byte, error) {
	var line []byte
	for {
		fragment, err := reader.ReadSlice('\n')
		if len(line)+len(fragment) > pythonRenderLimit {
			return nil, fmt.Errorf("Python HTML response exceeds limit")
		}
		line = append(line, fragment...)
		if err == bufio.ErrBufferFull {
			continue
		}
		return line, err
	}
}

func (renderer *pythonHomeRenderer) renderContext(ctx context.Context, request map[string]any) ([]byte, error) {
	ctx, cancel := context.WithTimeout(ctx, pythonRenderTimeout)
	defer cancel()
	type result struct {
		body []byte
		err  error
	}
	finished := make(chan result, 1)
	go func() { body, err := renderer.render(request); finished <- result{body, err} }()
	select {
	case reply := <-finished:
		if reply.err != nil {
			renderer.close()
		}
		return reply.body, reply.err
	case <-ctx.Done():
		renderer.close()
		<-finished
		return nil, ctx.Err()
	}
}

func (renderer *pythonHomeRenderer) close() {
	renderer.closeOnce.Do(func() {
		if renderer.command.Process != nil {
			_ = renderer.command.Process.Kill()
		}
		_ = renderer.input.Close()
		<-renderer.done
	})
}

// Admission is shared by all HTML bridge calls. It is nonblocking so a stalled
// child cannot accumulate an unbounded queue of HTTP handlers.
func (s *server) admitPythonRender(parent context.Context) (context.Context, func(), error) {
	s.homeRendererMu.Lock()
	defer s.homeRendererMu.Unlock()
	if s.htmlClosed {
		return nil, nil, fmt.Errorf("HTML renderer closed")
	}
	if s.htmlAdmission == nil {
		s.htmlAdmission = make(chan struct{}, 1)
		s.htmlContext, s.htmlCancel = context.WithCancel(context.Background())
	}
	select {
	case s.htmlAdmission <- struct{}{}:
	default:
		return nil, nil, fmt.Errorf("HTML renderer busy")
	}
	ctx, cancel := context.WithTimeout(parent, pythonRenderTimeout)
	stop := context.AfterFunc(s.htmlContext, cancel)
	return ctx, func() { stop(); cancel(); <-s.htmlAdmission }, nil
}

func (s *server) renderPythonPage(parent context.Context, script string, args ...string) ([]byte, error) {
	ctx, release, err := s.admitPythonRender(parent)
	if err != nil {
		return nil, err
	}
	defer release()
	command := exec.CommandContext(ctx, "python3", append([]string{"-c", script}, args...)...)
	command.Dir = s.root
	command.Env = pythonBridgeEnvironment(s.root, s.config)
	output, err := command.StdoutPipe()
	if err != nil {
		return nil, err
	}
	if err = command.Start(); err != nil {
		return nil, err
	}
	stopRead := context.AfterFunc(ctx, func() { _ = output.Close() })
	defer stopRead()
	body, readErr := io.ReadAll(io.LimitReader(output, pythonRenderLimit+1))
	if readErr != nil || len(body) > pythonRenderLimit {
		_ = command.Process.Kill()
	}
	waitErr := command.Wait()
	if readErr != nil {
		return nil, readErr
	}
	if len(body) > pythonRenderLimit {
		return nil, fmt.Errorf("Python HTML response exceeds limit")
	}
	return body, waitErr
}

type pythonHTMLState struct {
	homeRendererMu sync.Mutex
	homeRenderer   *pythonHomeRenderer
	htmlClosed     bool
	htmlAdmission  chan struct{}
	htmlContext    context.Context
	htmlCancel     context.CancelFunc
}

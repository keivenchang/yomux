// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"os/exec"
)

const pythonHomeRendererScript = `import json,sys
from yolomux_lib.web import html_page
for line in sys.stdin:
 request=json.loads(line)
 html=html_page(request["sessions"], access_role=request["role"], auth_username=request["username"], accept_language=request["language"], terminal_only=False, capability_snapshot=request["capabilities"], stats_only=request["stats_only"], backend_label=request["backend"], dangerously_yolo=request["dangerously_yolo"])
 response={"html":html}
 print(json.dumps(response,separators=(",",":")),flush=True)
`

type pythonHomeRenderer struct {
	command *exec.Cmd
	input   io.WriteCloser
	output  *bufio.Reader
	done    chan error
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
	renderer := &pythonHomeRenderer{command: command, input: input, output: bufio.NewReader(output), done: make(chan error, 1)}
	go func() { renderer.done <- command.Wait() }()
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
	line, err := renderer.output.ReadBytes('\n')
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

func (renderer *pythonHomeRenderer) close() {
	_ = renderer.input.Close()
	if renderer.command.Process != nil {
		select {
		case <-renderer.done:
		default:
			_ = renderer.command.Process.Kill()
			<-renderer.done
		}
	}
}

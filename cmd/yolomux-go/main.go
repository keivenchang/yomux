package main

import (
	"bufio"
	"crypto/sha1"
	"encoding/base64"
	"encoding/binary"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"log/slog"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"
)

const version = "0.1.0-phase2"
const wsGUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

type server struct {
	root     string
	host     string
	mu       sync.Mutex
	children map[*exec.Cmd]struct{}
}

type wsConn struct {
	net.Conn
	writeMu sync.Mutex
}

func (c *wsConn) frame(opcode byte, payload []byte) error {
	c.writeMu.Lock()
	defer c.writeMu.Unlock()
	return writeFrame(c.Conn, opcode, payload)
}

func main() {
	port := flag.Int("port", 7221, "HTTP port")
	host := flag.String("host", "127.0.0.1", "bind address")
	root := flag.String("root", ".", "YOLOmux checkout root")
	showVersion := flag.Bool("version", false, "print version")
	flag.Parse()
	if *showVersion {
		fmt.Printf("yolomux-go %s\n", version)
		return
	}
	if *port < 1 || *port > 65535 {
		fatal("invalid port")
	}
	s := &server{root: *root, host: *host, children: make(map[*exec.Cmd]struct{})}
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", s.health)
	mux.HandleFunc("/livez", s.health)
	mux.HandleFunc("/api/ping", s.ping)
	mux.HandleFunc("/api/tmux", s.tmux)
	mux.HandleFunc("/api/tmux-session-exists", s.sessionExists)
	mux.HandleFunc("/api/create-session-plan", s.createSessionPlan)
	mux.HandleFunc("/api/create-session", s.createSession)
	mux.HandleFunc("/api/kill-session", s.killSession)
	mux.HandleFunc("/ws", s.websocket)
	mux.HandleFunc("/static/", s.static)
	mux.HandleFunc("/", s.index)
	addr := fmt.Sprintf("%s:%d", *host, *port)
	httpServer := &http.Server{Addr: addr, Handler: logging(mux)}
	go s.reaper()
	slog.Info("YOLOmux Go backend", "version", version, "backend", "go", "addr", addr)
	if err := httpServer.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		fatal(err.Error())
	}
}

func fatal(message string) { slog.Error(message); os.Exit(1) }

func logging(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		next.ServeHTTP(w, r)
		slog.Info("request", "method", r.Method, "path", r.URL.Path, "duration_ms", time.Since(start).Milliseconds())
	})
}

func (s *server) health(w http.ResponseWriter, _ *http.Request) {
	jsonResponse(w, http.StatusOK, map[string]any{"ok": true, "backend": "go", "version": version})
}

func (s *server) ping(w http.ResponseWriter, _ *http.Request) {
	jsonResponse(w, http.StatusOK, map[string]any{"ok": true, "backend": "go", "capabilities": map[string]any{"terminal.websocket.v1": true, "session.roster.v1": true, "session.create.v1": true, "session.kill.v1": true}})
}

func (s *server) tmux(w http.ResponseWriter, _ *http.Request) {
	out, err := exec.Command("tmux", "list-sessions", "-F", "#{session_name}").Output()
	sessions := []string{}
	if err == nil {
		for _, line := range strings.Split(strings.TrimSpace(string(out)), "\n") {
			if line != "" {
				sessions = append(sessions, line)
			}
		}
	}
	jsonResponse(w, http.StatusOK, map[string]any{"sessions": sessions, "backend": "go"})
}

func (s *server) sessionExists(w http.ResponseWriter, r *http.Request) {
	name := r.URL.Query().Get("session")
	err := exec.Command("tmux", "has-session", "-t", name).Run()
	jsonResponse(w, http.StatusOK, map[string]any{"session": name, "exists": err == nil})
}

func (s *server) createSession(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	name := r.URL.Query().Get("session")
	if name == "" {
		name = r.FormValue("session")
	}
	if name == "" {
		name = "yo-" + strconv.FormatInt(time.Now().UnixNano(), 10)
	}
	if err := exec.Command("tmux", "new-session", "-d", "-s", name).Run(); err != nil {
		jsonResponse(w, http.StatusBadRequest, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	jsonResponse(w, http.StatusOK, map[string]any{"ok": true, "created": true, "session": name, "sessions": s.sessionNames()})
}

func (s *server) createSessionPlan(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	name := "yo-" + strconv.FormatInt(time.Now().UnixNano(), 10)
	jsonResponse(w, http.StatusOK, map[string]any{
		"ok": true, "session": name, "generation": time.Now().UnixNano(), "backend": "go",
	})
}

func (s *server) unsupported(w http.ResponseWriter, _ *http.Request) {
	jsonResponse(w, http.StatusNotImplemented, map[string]any{"error": "feature_unavailable", "reason": map[string]string{"code": "not_implemented", "messageKey": "capabilityUnavailable"}})
}

func (s *server) killSession(w http.ResponseWriter, r *http.Request) {
	name := r.URL.Query().Get("session")
	if name == "" {
		name = r.FormValue("session")
	}
	err := exec.Command("tmux", "kill-session", "-t", name).Run()
	if err != nil {
		jsonResponse(w, http.StatusBadRequest, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	jsonResponse(w, http.StatusOK, map[string]any{"ok": true, "session": name})
}

func (s *server) index(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path != "/" {
		http.NotFound(w, r)
		return
	}
	bootstrap := map[string]any{
		"sessions": s.sessionNames(), "recentSessions": s.sessionNames(), "accessRole": "admin", "backend": "go",
		"capabilities": map[string]any{"terminal.websocket.v1": map[string]any{"available": true, "binaryOutput": true, "refreshAck": true, "resize": true}, "pane.finder.v1": map[string]any{"available": false, "reason": map[string]string{"code": "not_implemented", "messageKey": "capability.finderUnavailable"}}},
	}
	b, _ := json.Marshal(bootstrap)
	root := filepath.Clean(s.root)
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	fmt.Fprintf(w, `<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>YOLOmux Go</title><link rel="stylesheet" href="/static/yolomux.css"></head><body><main style="font:16px sans-serif;padding:2rem"><h1>YOLOmux Go backend</h1><p>Phase 2 terminal MVP is running on <code>%s</code>.</p><p>Sessions: <code>%s</code></p><pre id="yolomux-bootstrap" style="display:none">%s</pre></main><script>window.yolomuxBackend='go';</script></body></html>`, root, strings.Join(s.sessionNames(), ", "), b)
}

func (s *server) static(w http.ResponseWriter, r *http.Request) {
	path := filepath.Join(s.root, filepath.Clean(strings.TrimPrefix(r.URL.Path, "/static/")))
	if !strings.HasPrefix(path, filepath.Join(s.root, "static")) {
		http.NotFound(w, r)
		return
	}
	http.ServeFile(w, r, path)
}

func (s *server) sessionNames() []string {
	out, err := exec.Command("tmux", "list-sessions", "-F", "#{session_name}").Output()
	if err != nil {
		return []string{}
	}
	var result []string
	for _, line := range strings.Split(strings.TrimSpace(string(out)), "\n") {
		if line != "" {
			result = append(result, line)
		}
	}
	return result
}

func (s *server) websocket(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	if r.Header.Get("Origin") != "" && r.Header.Get("Origin") != "http://"+r.Host && r.Header.Get("Origin") != "https://"+r.Host {
		http.Error(w, "origin denied", http.StatusForbidden)
		return
	}
	session := r.URL.Query().Get("session")
	if session == "" || exec.Command("tmux", "has-session", "-t", session).Run() != nil {
		http.Error(w, "unknown session: "+session, http.StatusNotFound)
		return
	}
	key := r.Header.Get("Sec-WebSocket-Key")
	if key == "" {
		http.Error(w, "missing Sec-WebSocket-Key", http.StatusBadRequest)
		return
	}
	if r.Header.Get("Sec-WebSocket-Version") != "13" {
		http.Error(w, "unsupported websocket version", http.StatusBadRequest)
		return
	}
	hijack, ok := w.(http.Hijacker)
	if !ok {
		http.Error(w, "websocket unavailable", http.StatusInternalServerError)
		return
	}
	conn, rw, err := hijack.Hijack()
	if err != nil {
		return
	}
	accept := sha1.Sum([]byte(key + wsGUID))
	fmt.Fprintf(rw, "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: %s\r\n\r\n", base64.StdEncoding.EncodeToString(accept[:]))
	if err := rw.Flush(); err != nil {
		conn.Close()
		return
	}
	s.bridge(&wsConn{Conn: conn}, session, r.URL.Query().Get("readonly") == "1")
}

func (s *server) bridge(conn *wsConn, session string, readonly bool) {
	defer conn.Close()
	attach := "tmux attach-session"
	if readonly {
		attach += " -r"
	}
	cmd := exec.Command("script", "-qfec", attach+" -t "+shellQuote(session), "/dev/null")
	cmd.Env = append(os.Environ(), "TERM=xterm-256color")
	stdin, err := cmd.StdinPipe()
	if err != nil {
		return
	}
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return
	}
	if err := cmd.Start(); err != nil {
		return
	}
	s.mu.Lock()
	s.children[cmd] = struct{}{}
	s.mu.Unlock()
	defer func() { _ = cmd.Process.Kill(); _ = cmd.Wait(); s.mu.Lock(); delete(s.children, cmd); s.mu.Unlock() }()
	go func() {
		buf := make([]byte, 32*1024)
		for {
			n, err := stdout.Read(buf)
			if n > 0 {
				_ = conn.frame(2, buf[:n])
			}
			if err != nil {
				return
			}
		}
	}()
	reader := bufio.NewReader(conn)
	for {
		opcode, payload, err := readFrame(reader)
		if err != nil {
			return
		}
		if opcode == 8 {
			return
		}
		if opcode == 9 {
			_ = conn.frame(10, payload)
			continue
		}
		if opcode != 1 && opcode != 2 {
			continue
		}
		s.handleMessage(stdin, conn, session, payload, !readonly)
	}
}

func (s *server) handleMessage(stdin io.Writer, conn *wsConn, session string, payload []byte, writable bool) {
	var msg struct {
		Type string `json:"type"`
		Data string `json:"data"`
		Rows int    `json:"rows"`
		Cols int    `json:"cols"`
		Txn  int    `json:"txn"`
	}
	if json.Unmarshal(payload, &msg) != nil {
		if writable {
			_, _ = stdin.Write(payload)
		}
		return
	}
	if !writable && msg.Type != "refresh" {
		return
	}
	switch msg.Type {
	case "input":
		_, _ = io.WriteString(stdin, msg.Data)
	case "resize": /* script owns the PTY size in this MVP */
		if msg.Rows > 0 && msg.Cols > 0 {
			_ = exec.Command("tmux", "resize-window", "-t", session, "-x", strconv.Itoa(msg.Cols), "-y", strconv.Itoa(msg.Rows)).Run()
		}
	case "refresh":
		_ = exec.Command("tmux", "refresh-client", "-t", session).Run()
		if msg.Txn > 0 {
			b, _ := json.Marshal(map[string]any{"type": "refresh-ack", "txn": msg.Txn})
			_ = conn.frame(1, b)
		}
	}
}

func shellQuote(value string) string { return "'" + strings.ReplaceAll(value, "'", "'\\''") + "'" }

func (s *server) reaper() {
	for {
		time.Sleep(10 * time.Second)
		s.mu.Lock()
		for cmd := range s.children {
			if cmd.ProcessState != nil {
				delete(s.children, cmd)
			}
		}
		s.mu.Unlock()
	}
}

func jsonResponse(w http.ResponseWriter, status int, value any) {
	b, _ := json.Marshal(value)
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_, _ = w.Write(b)
}

func readFrame(r *bufio.Reader) (byte, []byte, error) {
	b1, err := r.ReadByte()
	if err != nil {
		return 0, nil, err
	}
	b2, err := r.ReadByte()
	if err != nil {
		return 0, nil, err
	}
	opcode := b1 & 0x0f
	masked := b2&0x80 != 0
	length := int64(b2 & 0x7f)
	if length == 126 {
		var x uint16
		if err := binaryRead(r, &x); err != nil {
			return 0, nil, err
		}
		length = int64(x)
	} else if length == 127 {
		var x uint64
		if err := binaryRead(r, &x); err != nil {
			return 0, nil, err
		}
		if x > 16<<20 {
			return 0, nil, fmt.Errorf("frame too large")
		}
		length = int64(x)
	}
	if length > 16<<20 {
		return 0, nil, fmt.Errorf("frame too large")
	}
	var mask [4]byte
	if masked {
		if _, err := io.ReadFull(r, mask[:]); err != nil {
			return 0, nil, err
		}
	}
	p := make([]byte, length)
	if _, err := io.ReadFull(r, p); err != nil {
		return 0, nil, err
	}
	if masked {
		for i := range p {
			p[i] ^= mask[i%4]
		}
	}
	return opcode, p, nil
}

func binaryRead(r io.Reader, data any) error { return binary.Read(r, binary.BigEndian, data) }
func writeFrame(w io.Writer, opcode byte, payload []byte) error {
	h := []byte{0x80 | opcode}
	n := len(payload)
	if n < 126 {
		h = append(h, byte(n))
	} else if n <= 65535 {
		h = append(h, 126, byte(n>>8), byte(n))
	} else {
		h = append(h, 127, 0, 0, 0, 0, 0, 0, 0, 0, byte(n>>24), byte(n>>16), byte(n>>8), byte(n))
	}
	_, err := w.Write(append(h, payload...))
	return err
}

package main

import (
	"bufio"
	"crypto/hmac"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha1"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/base64"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"encoding/pem"
	"flag"
	"fmt"
	"io"
	"log/slog"
	"math/big"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"
	"unsafe"
)

const version = "0.1.0-phase2"
const wsGUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

type server struct {
	root     string
	host     string
	config   string
	mu       sync.Mutex
	children map[*exec.Cmd]struct{}
}

type wsConn struct {
	net.Conn
	writeMu sync.Mutex
}

type authUser struct{ username, password, role string }

type winsize struct{ rows, cols, x, y uint16 }

type ptyPair struct{ master, slave *os.File }

func clampDimension(value, minimum, maximum, fallback int) int {
	if value <= 0 {
		return fallback
	}
	if value < minimum {
		return minimum
	}
	if value > maximum {
		return maximum
	}
	return value
}

func openPTY(rows, cols int) (*ptyPair, error) {
	masterFD, err := syscall.Open("/dev/ptmx", syscall.O_RDWR|syscall.O_NOCTTY, 0)
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
	slaveFD, err := syscall.Open(fmt.Sprintf("/dev/pts/%d", ptyNumber), syscall.O_RDWR|syscall.O_NOCTTY, 0)
	if err != nil {
		_ = syscall.Close(masterFD)
		return nil, err
	}
	pair := &ptyPair{master: os.NewFile(uintptr(masterFD), "pty-master"), slave: os.NewFile(uintptr(slaveFD), "pty-slave")}
	if err := pair.resize(rows, cols); err != nil {
		pair.close()
		return nil, err
	}
	return pair, nil
}

func (p *ptyPair) resize(rows, cols int) error {
	size := winsize{rows: uint16(rows), cols: uint16(cols)}
	_, _, errno := syscall.Syscall(syscall.SYS_IOCTL, p.master.Fd(), uintptr(0x5414), uintptr(unsafe.Pointer(&size)))
	if errno != 0 {
		return errno
	}
	return nil
}

func (p *ptyPair) close() { _ = p.master.Close(); _ = p.slave.Close() }

func (c *wsConn) frame(opcode byte, payload []byte) error {
	c.writeMu.Lock()
	defer c.writeMu.Unlock()
	return writeFrame(c.Conn, opcode, payload)
}

func main() {
	port := flag.Int("port", 7221, "HTTP port")
	host := flag.String("host", "127.0.0.1", "bind address")
	root := flag.String("root", ".", "YOLOmux checkout root")
	configDir := flag.String("config", os.Getenv("YOLOMUX_CONFIG_DIR"), "YOLOmux config directory")
	httpMode := flag.Bool("http", false, "serve plain HTTP instead of the default HTTPS")
	certPath := flag.String("cert", "", "TLS certificate PEM path")
	keyPath := flag.String("key", "", "TLS private key PEM path")
	showVersion := flag.Bool("version", false, "print version")
	flag.Parse()
	if *showVersion {
		fmt.Printf("yolomux-go %s\n", version)
		return
	}
	if *port < 1 || *port > 65535 {
		fatal("invalid port")
	}
	if *configDir == "" {
		*configDir = filepath.Join(os.Getenv("HOME"), ".config", "yolomux")
	}
	s := &server{root: *root, host: *host, config: *configDir, children: make(map[*exec.Cmd]struct{})}
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", s.health)
	mux.HandleFunc("/livez", s.health)
	mux.HandleFunc("/login", s.login)
	mux.HandleFunc("/logout", s.logout)
	mux.HandleFunc("/api/auth-setup", s.authSetup)
	mux.Handle("/api/ping", s.protected(http.HandlerFunc(s.ping), "readonly"))
	mux.Handle("/api/client-events", s.protected(http.HandlerFunc(s.clientEvents), "readonly"))
	mux.Handle("/api/tmux", s.protected(http.HandlerFunc(s.tmux), "readonly"))
	mux.Handle("/api/tmux-session-exists", s.protected(http.HandlerFunc(s.sessionExists), "readonly"))
	mux.Handle("/api/ensure-session", s.protected(http.HandlerFunc(s.ensureSession), "admin"))
	mux.Handle("/api/create-session-plan", s.protected(http.HandlerFunc(s.createSessionPlan), "admin"))
	mux.Handle("/api/create-session", s.protected(http.HandlerFunc(s.createSession), "admin"))
	mux.Handle("/api/kill-session", s.protected(http.HandlerFunc(s.killSession), "admin"))
	mux.Handle("/", s.protected(http.HandlerFunc(s.index), "readonly"))
	mux.HandleFunc("/ws", s.websocket)
	mux.HandleFunc("/static/", s.static)
	addr := fmt.Sprintf("%s:%d", *host, *port)
	httpServer := &http.Server{Addr: addr, Handler: logging(mux)}
	go s.reaper()
	listener, err := net.Listen("tcp", addr)
	if err != nil {
		fatal(err.Error())
	}
	if !*httpMode {
		certFile, keyFile, err := tlsFiles(*certPath, *keyPath, *host, *port)
		if err != nil {
			_ = listener.Close()
			fatal("TLS setup failed: " + err.Error())
		}
		config, err := tlsConfig(certFile, keyFile)
		if err != nil {
			_ = listener.Close()
			fatal("TLS setup failed: " + err.Error())
		}
		listener = tls.NewListener(listener, config)
	}
	scheme := "https"
	if *httpMode {
		scheme = "http"
	}
	slog.Info("YOLOmux Go backend", "version", version, "backend", "go", "addr", addr, "scheme", scheme)
	if err := httpServer.Serve(listener); err != nil && err != http.ErrServerClosed {
		fatal(err.Error())
	}
}

func fatal(message string) { slog.Error(message); os.Exit(1) }

func (s *server) users() []authUser {
	b, err := os.ReadFile(filepath.Join(s.config, "auth.yaml"))
	if err != nil {
		return nil
	}
	var users []authUser
	var current authUser
	seen := false
	for _, line := range strings.Split(string(b), "\n") {
		line = strings.TrimSpace(strings.SplitN(line, "#", 2)[0])
		if strings.HasPrefix(line, "- username:") {
			if seen && current.username != "" && current.password != "" {
				users = append(users, current)
			}
			current = authUser{username: yamlValue(strings.TrimSpace(strings.TrimPrefix(line, "- username:")))}
			seen = true
		} else if strings.HasPrefix(line, "password:") || strings.HasPrefix(line, "password_hash:") {
			current.password = yamlValue(strings.TrimSpace(strings.SplitN(line, ":", 2)[1]))
		} else if strings.HasPrefix(line, "role:") {
			current.role = strings.ToLower(yamlValue(strings.TrimSpace(strings.SplitN(line, ":", 2)[1])))
		}
	}
	if seen && current.username != "" && current.password != "" {
		users = append(users, current)
	}
	return users
}

func yamlValue(value string) string {
	value = strings.TrimSpace(value)
	if len(value) >= 2 && value[0] == '"' && value[len(value)-1] == '"' {
		value = value[1 : len(value)-1]
	}
	return strings.ReplaceAll(value, `\"`, `"`)
}

func (s *server) identity(r *http.Request) (authUser, bool) {
	cookieValue := ""
	if cookie, err := r.Cookie("yolomux_auth"); err == nil {
		cookieValue = cookie.Value
	}
	for _, user := range s.users() {
		if cookieValue != "" && hmac.Equal([]byte(cookieValue), []byte(s.cookieValue(user))) {
			return user, true
		}
	}
	basic := r.Header.Get("Authorization")
	if strings.HasPrefix(strings.ToLower(basic), "basic ") {
		decoded, err := base64.StdEncoding.DecodeString(strings.TrimSpace(basic[6:]))
		if err == nil {
			username, password, ok := strings.Cut(string(decoded), ":")
			if ok {
				for _, user := range s.users() {
					if user.username == username && passwordMatches(password, user.password) {
						return user, true
					}
				}
			}
		}
	}
	return authUser{}, false
}

func (s *server) cookieValue(user authUser) string {
	raw, err := os.ReadFile(filepath.Join(s.config, "auth-cookie-secret"))
	if err != nil {
		return ""
	}
	secret, err := hex.DecodeString(strings.TrimSpace(string(raw)))
	if err != nil {
		return ""
	}
	h := hmac.New(sha256.New, secret)
	_, _ = h.Write([]byte(user.username + ":" + user.password))
	return hex.EncodeToString(h.Sum(nil))
}

func passwordMatches(password, stored string) bool {
	parts := strings.Split(stored, "$")
	if len(parts) != 4 || parts[0] != "pbkdf2_sha256" {
		return hmac.Equal([]byte(password), []byte(stored))
	}
	iterations, err := strconv.Atoi(parts[1])
	if err != nil || iterations < 1 {
		return false
	}
	salt := []byte(parts[2])
	digest := pbkdf2SHA256([]byte(password), salt, iterations, 32)
	expected, err := hex.DecodeString(parts[3])
	return err == nil && hmac.Equal(digest, expected)
}

func pbkdf2SHA256(password, salt []byte, iterations, length int) []byte {
	result := make([]byte, 0, length)
	for block := 1; len(result) < length; block++ {
		mac := hmac.New(sha256.New, password)
		_, _ = mac.Write(salt)
		mac.Write([]byte{byte(block >> 24), byte(block >> 16), byte(block >> 8), byte(block)})
		u := mac.Sum(nil)
		t := append([]byte(nil), u...)
		for i := 1; i < iterations; i++ {
			mac = hmac.New(sha256.New, password)
			_, _ = mac.Write(u)
			u = mac.Sum(nil)
			for j := range t {
				t[j] ^= u[j]
			}
		}
		result = append(result, t...)
	}
	return result[:length]
}

func (s *server) protected(next http.Handler, role string) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		user, ok := s.identity(r)
		if !ok || (role == "admin" && user.role != "admin") {
			if r.Method == http.MethodGet && r.URL.Path == "/" {
				http.Redirect(w, r, "/login?next=/", http.StatusFound)
				return
			}
			jsonResponse(w, http.StatusUnauthorized, map[string]any{"error": "authentication_required"})
			return
		}
		next.ServeHTTP(w, r)
	})
}

func (s *server) authSetup(w http.ResponseWriter, _ *http.Request) {
	jsonResponse(w, http.StatusOK, map[string]any{"setup_required": len(s.users()) == 0})
}

func (s *server) login(w http.ResponseWriter, r *http.Request) {
	if r.Method == http.MethodGet {
		next := r.URL.Query().Get("next")
		if !strings.HasPrefix(next, "/") {
			next = "/"
		}
		s.writePythonLogin(w, r, next, "")
		return
	}
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	_ = r.ParseForm()
	username, password := r.FormValue("username"), r.FormValue("password")
	for _, user := range s.users() {
		if user.username == username && passwordMatches(password, user.password) {
			http.SetCookie(w, &http.Cookie{Name: "yolomux_auth", Value: s.cookieValue(user), Path: "/", MaxAge: 90 * 24 * 60 * 60, HttpOnly: true, SameSite: http.SameSiteLaxMode, Secure: true})
			next := r.FormValue("next")
			if !strings.HasPrefix(next, "/") {
				next = "/"
			}
			http.Redirect(w, r, next, http.StatusFound)
			return
		}
	}
	w.WriteHeader(http.StatusUnauthorized)
	s.writePythonLogin(w, r, r.FormValue("next"), "Invalid username or password.")
}

func (s *server) writePythonLogin(w http.ResponseWriter, r *http.Request, next, message string) {
	if !strings.HasPrefix(next, "/") {
		next = "/"
	}
	locale := ""
	if cookie, err := r.Cookie("yolomux_locale"); err == nil {
		locale = cookie.Value
	}
	script := `import sys
from yolomux_lib.web import login_html
print(login_html(next_path=sys.argv[1], error=sys.argv[2], secure=True, current_locale=sys.argv[3], accept_language=sys.argv[4]), end="")`
	command := exec.Command("python3", "-c", script, next, message, locale, r.Header.Get("Accept-Language"))
	command.Dir = s.root
	command.Env = append(os.Environ(), "PYTHONPATH="+s.root)
	body, err := command.Output()
	if err != nil {
		http.Error(w, "login page unavailable", http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	_, _ = w.Write(body)
}

func (s *server) logout(w http.ResponseWriter, r *http.Request) {
	http.SetCookie(w, &http.Cookie{Name: "yolomux_auth", Value: "", Path: "/", MaxAge: -1, HttpOnly: true, Secure: true})
	http.Redirect(w, r, "/login", http.StatusFound)
}

func tlsFiles(certPath, keyPath, host string, port int) (string, string, error) {
	if (certPath == "") != (keyPath == "") {
		return "", "", fmt.Errorf("--cert and --key must be provided together")
	}
	if certPath != "" {
		return certPath, keyPath, nil
	}
	dir := filepath.Join(os.TempDir(), "yolomux-go-tls-"+strconv.Itoa(port))
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return "", "", err
	}
	certFile := filepath.Join(dir, "self-signed.crt")
	keyFile := filepath.Join(dir, "self-signed.key")
	if _, certErr := os.Stat(certFile); certErr == nil {
		if _, keyErr := os.Stat(keyFile); keyErr == nil {
			return certFile, keyFile, nil
		}
	}
	privateKey, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		return "", "", err
	}
	serial, err := rand.Int(rand.Reader, new(big.Int).Lsh(big.NewInt(1), 120))
	if err != nil {
		return "", "", err
	}
	template := &x509.Certificate{
		SerialNumber:          serial,
		Subject:               pkix.Name{CommonName: host},
		NotBefore:             time.Now().Add(-time.Minute),
		NotAfter:              time.Now().Add(365 * 24 * time.Hour),
		KeyUsage:              x509.KeyUsageDigitalSignature | x509.KeyUsageKeyEncipherment,
		ExtKeyUsage:           []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
		BasicConstraintsValid: true,
	}
	if ip := net.ParseIP(host); ip != nil {
		template.IPAddresses = []net.IP{ip}
	} else {
		template.DNSNames = []string{host, "localhost"}
	}
	if host == "0.0.0.0" || host == "::" {
		template.DNSNames = append(template.DNSNames, "localhost")
		if interfaces, err := net.Interfaces(); err == nil {
			for _, iface := range interfaces {
				addresses, _ := iface.Addrs()
				for _, address := range addresses {
					text := strings.Split(address.String(), "/")[0]
					if ip := net.ParseIP(text); ip != nil && !ip.IsLoopback() {
						template.IPAddresses = append(template.IPAddresses, ip)
					}
				}
			}
		}
	}
	der, err := x509.CreateCertificate(rand.Reader, template, template, &privateKey.PublicKey, privateKey)
	if err != nil {
		return "", "", err
	}
	certBytes := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})
	keyBytes := pem.EncodeToMemory(&pem.Block{Type: "RSA PRIVATE KEY", Bytes: x509.MarshalPKCS1PrivateKey(privateKey)})
	if err := os.WriteFile(certFile, certBytes, 0o600); err != nil {
		return "", "", err
	}
	if err := os.WriteFile(keyFile, keyBytes, 0o600); err != nil {
		return "", "", err
	}
	return certFile, keyFile, nil
}

func tlsConfig(certFile, keyFile string) (*tls.Config, error) {
	certificate, err := tls.LoadX509KeyPair(certFile, keyFile)
	if err != nil {
		return nil, err
	}
	return &tls.Config{Certificates: []tls.Certificate{certificate}, MinVersion: tls.VersionTLS12}, nil
}

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
	jsonResponse(w, http.StatusOK, map[string]any{"ok": true, "backend": "go", "capabilities": map[string]any{"terminal.websocket.v1": true, "session.roster.v1": true, "session.create.v1": false, "session.kill.v1": false}})
}

func (s *server) clientEvents(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	flusher, ok := w.(http.Flusher)
	if !ok {
		http.Error(w, "streaming unavailable", http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	writeSSE := func(event string, payload any) {
		data, _ := json.Marshal(payload)
		fmt.Fprintf(w, "event: %s\ndata: %s\n\n", event, data)
		flusher.Flush()
	}
	writeSSE("ready", map[string]any{"epoch": "go-phase2", "resource_revisions": map[string]any{}, "channels": strings.Split(r.URL.Query().Get("channels"), ",")})
	ticker := time.NewTicker(15 * time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-r.Context().Done():
			return
		case <-ticker.C:
			writeSSE("ping", map[string]any{"epoch": "go-phase2"})
		}
	}
}

func (s *server) tmux(w http.ResponseWriter, _ *http.Request) {
	out, err := s.tmuxCommand("list-sessions", "-F", "#{session_name}").Output()
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
	err := s.tmuxCommand("has-session", "-t", name).Run()
	jsonResponse(w, http.StatusOK, map[string]any{"session": name, "exists": err == nil})
}

func (s *server) ensureSession(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	name := r.URL.Query().Get("session")
	if name == "" {
		name = r.FormValue("session")
	}
	if name == "" {
		jsonResponse(w, http.StatusBadRequest, map[string]any{"error": "missing session"})
		return
	}
	exists := s.tmuxCommand("has-session", "-t", name).Run() == nil
	if !exists {
		jsonResponse(w, http.StatusNotFound, map[string]any{"ok": false, "created": false, "session": name, "error": "unknown session"})
		return
	}
	jsonResponse(w, http.StatusOK, map[string]any{"ok": true, "created": false, "session": name})
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
	if err := s.tmuxCommand("new-session", "-d", "-s", name).Run(); err != nil {
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
	err := s.tmuxCommand("kill-session", "-t", name).Run()
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
	user, _ := s.identity(r)
	s.writePythonHome(w, r, user)
}

func (s *server) writePythonHome(w http.ResponseWriter, r *http.Request, user authUser) {
	script := `import json,sys
from yolomux_lib.web import html_page
print(html_page(json.loads(sys.argv[1]), access_role=sys.argv[2], auth_username=sys.argv[3], accept_language=sys.argv[4], terminal_only=True), end="")`
	sessions, _ := json.Marshal(s.sessionNames())
	command := exec.Command("python3", "-c", script, string(sessions), user.role, user.username, r.Header.Get("Accept-Language"))
	command.Dir = s.root
	command.Env = append(os.Environ(), "PYTHONPATH="+s.root)
	body, err := command.Output()
	if err != nil {
		http.Error(w, "application unavailable", http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	_, _ = w.Write(body)
}

func (s *server) static(w http.ResponseWriter, r *http.Request) {
	relative := filepath.Clean(strings.TrimPrefix(r.URL.Path, "/static/"))
	staticRoot := filepath.Join(s.root, "static")
	path := filepath.Join(staticRoot, relative)
	if path != staticRoot && !strings.HasPrefix(path, staticRoot+string(filepath.Separator)) {
		http.NotFound(w, r)
		return
	}
	http.ServeFile(w, r, path)
}

func (s *server) sessionNames() []string {
	out, err := s.tmuxCommand("list-sessions", "-F", "#{session_name}").Output()
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

func (s *server) tmuxCommand(args ...string) *exec.Cmd {
	values := []string{}
	if socket := strings.TrimSpace(os.Getenv("YOLOMUX_TMUX_SOCKET")); socket != "" {
		values = append(values, "-S", socket)
	}
	return exec.Command("tmux", append(values, args...)...)
}

func (s *server) exactTarget(session string) string { return "=" + session + ":" }

func (s *server) supportsIgnoreSize() bool {
	out, err := s.tmuxCommand("attach-session", "-?").CombinedOutput()
	return err == nil && strings.Contains(string(out), "ignore-size")
}

func (s *server) configureTmuxSession(session string) {
	target := s.exactTarget(session)
	for _, args := range [][]string{{"set-option", "-s", "set-clipboard", "on"}, {"set-option", "-t", target, "window-size", "largest"}, {"set-option", "-wg", "aggressive-resize", "on"}} {
		_ = s.tmuxCommand(args...).Run()
	}
}

func (s *server) claimResizeAuthority(session, activeClient string, cols, rows int) {
	if activeClient == "" || !s.supportsIgnoreSize() {
		return
	}
	target := s.exactTarget(session)
	format := "#{client_tty}\t#{client_session}\t#{client_width}\t#{client_height}"
	out, err := s.tmuxCommand("list-clients", "-F", format).Output()
	if err != nil {
		return
	}
	for _, line := range strings.Split(strings.TrimSpace(string(out)), "\n") {
		fields := strings.Split(line, "\t")
		if len(fields) != 4 || fields[1] != session {
			continue
		}
		client := fields[0]
		width, _ := strconv.Atoi(fields[2])
		height, _ := strconv.Atoi(fields[3])
		if client == activeClient || (width <= cols && height <= rows) {
			_ = s.tmuxCommand("set-option", "-t", client, "-f", "ignore-size", "off").Run()
		} else {
			_ = s.tmuxCommand("set-option", "-t", client, "-f", "ignore-size", "on").Run()
		}
	}
	_ = s.tmuxCommand("refresh-client", "-t", target).Run()
}

func (s *server) refreshAfterAttach(session string) {
	target := "=" + session + ":"
	_ = s.tmuxCommand("refresh-client", "-t", target).Run()
	for _, delay := range []time.Duration{100 * time.Millisecond, 500 * time.Millisecond} {
		go func(wait time.Duration) {
			time.Sleep(wait)
			_ = s.tmuxCommand("refresh-client", "-t", target).Run()
		}(delay)
	}
}

func (s *server) websocket(w http.ResponseWriter, r *http.Request) {
	user, ok := s.identity(r)
	if !ok {
		http.Error(w, "authentication required", http.StatusUnauthorized)
		return
	}
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	if r.Header.Get("Origin") != "" && r.Header.Get("Origin") != "http://"+r.Host && r.Header.Get("Origin") != "https://"+r.Host {
		http.Error(w, "origin denied", http.StatusForbidden)
		return
	}
	session := r.URL.Query().Get("session")
	if session == "" || s.tmuxCommand("has-session", "-t", session).Run() != nil {
		http.Error(w, "unknown session: "+session, http.StatusNotFound)
		return
	}
	readonly := user.role != "admin"
	clientID := strings.TrimSpace(r.URL.Query().Get("client"))
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
	s.bridge(&wsConn{Conn: conn}, session, readonly, clientID)
}

func (s *server) bridge(conn *wsConn, session string, readonly bool, clientID string) {
	defer conn.Close()
	rows, cols := 36, 120
	reader := bufio.NewReader(conn)
	_ = conn.SetReadDeadline(time.Now().Add(750 * time.Millisecond))
	var pending [][]byte
	for {
		opcode, payload, err := readFrame(reader)
		if err != nil {
			break
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
		var message struct {
			Type       string `json:"type"`
			Rows       int    `json:"rows"`
			Cols       int    `json:"cols"`
			Foreground *bool  `json:"foreground"`
		}
		if json.Unmarshal(payload, &message) == nil && message.Type == "resize" {
			if message.Foreground == nil || *message.Foreground {
				rows = clampDimension(message.Rows, 2, 300, rows)
				cols = clampDimension(message.Cols, 20, 500, cols)
			}
			continue
		}
		pending = append(pending, payload)
		break
	}
	_ = conn.SetReadDeadline(time.Time{})
	pty, err := openPTY(rows, cols)
	if err != nil {
		slog.Error("pty allocation failed", "session", session, "error", err)
		return
	}
	defer pty.close()
	s.configureTmuxSession(session)
	args := []string{"attach-session", "-t", session}
	if s.supportsIgnoreSize() {
		args = []string{"attach-session", "-f", "ignore-size", "-t", s.exactTarget(session)}
	}
	if readonly {
		args = []string{"attach-session", "-r", "-t", s.exactTarget(session)}
		if s.supportsIgnoreSize() {
			args = []string{"attach-session", "-r", "-f", "ignore-size", "-t", s.exactTarget(session)}
		}
	}
	cmd := s.tmuxCommand(args...)
	cmd.SysProcAttr = &syscall.SysProcAttr{
		Setsid:  true,
		Setctty: true,
		Ctty:    0,
	}
	cmd.Env = append(os.Environ(), "TERM=xterm-256color")
	cmd.Stdin = pty.slave
	cmd.Stdout = pty.slave
	cmd.Stderr = pty.slave
	if err := cmd.Start(); err != nil {
		slog.Error("tmux attach failed", "session", session, "error", err)
		return
	}
	activeTTY := pty.slave.Name()
	s.claimResizeAuthority(session, activeTTY, cols, rows)
	s.refreshAfterAttach(session)
	s.mu.Lock()
	s.children[cmd] = struct{}{}
	s.mu.Unlock()
	defer func() {
		if cmd.ProcessState == nil {
			_ = cmd.Process.Kill()
		}
		err := cmd.Wait()
		slog.Info("tmux attach exited", "session", session, "error", err)
		s.mu.Lock()
		delete(s.children, cmd)
		s.mu.Unlock()
	}()
	go func() {
		buf := make([]byte, 32*1024)
		for {
			n, err := pty.master.Read(buf)
			if n > 0 {
				_ = conn.frame(2, buf[:n])
			}
			if err != nil {
				return
			}
		}
	}()
	_ = s.tmuxCommand("refresh-client", "-t", "="+session+":").Run()
	for _, payload := range pending {
		s.handleMessage(pty.master, pty, cmd, conn, session, clientID, payload, !readonly)
	}
	for {
		opcode, payload, err := readFrame(reader)
		if err != nil {
			break
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
		s.handleMessage(pty.master, pty, cmd, conn, session, clientID, payload, !readonly)
	}
}

func (s *server) handleMessage(stdin io.Writer, pty *ptyPair, process *exec.Cmd, conn *wsConn, session, clientID string, payload []byte, writable bool) {
	var msg struct {
		Type       string `json:"type"`
		Data       string `json:"data"`
		Rows       int    `json:"rows"`
		Cols       int    `json:"cols"`
		Txn        int    `json:"txn"`
		Direction  string `json:"direction"`
		Lines      int    `json:"lines"`
		Reason     string `json:"reason"`
		Foreground *bool  `json:"foreground"`
		Activate   bool   `json:"activate"`
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
	case "resize":
		if msg.Foreground != nil && !*msg.Foreground {
			return
		}
		if msg.Rows > 0 && msg.Cols > 0 {
			rows := clampDimension(msg.Rows, 2, 300, 36)
			cols := clampDimension(msg.Cols, 20, 500, 120)
			if msg.Activate || (msg.Foreground != nil && *msg.Foreground) {
				s.claimResizeAuthority(session, pty.slave.Name(), cols, rows)
			}
			if pty.resize(rows, cols) == nil && process.Process != nil {
				_ = process.Process.Signal(syscall.SIGWINCH)
			}
		}
	case "tmux-scroll":
		if (msg.Direction == "up" || msg.Direction == "down") && msg.Lines > 0 {
			lines := clampDimension(msg.Lines, 1, 80, 1)
			if msg.Direction == "up" {
				_ = s.tmuxCommand("copy-mode", "-e", "-t", s.exactTarget(session)).Run()
			}
			key := "scroll-down"
			if msg.Direction == "up" {
				key = "scroll-up"
			}
			_ = s.tmuxCommand("send-keys", "-X", "-N", strconv.Itoa(lines), key, "-t", s.exactTarget(session)).Run()
		}
	case "refresh":
		_ = s.tmuxCommand("refresh-client", "-t", "="+session+":").Run()
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

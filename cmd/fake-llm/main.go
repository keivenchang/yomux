package main

import (
	"context"
	"flag"
	"fmt"
	"net"
	"os"
	"os/signal"
	"syscall"

	"github.com/keivenchang/yolomux/internal/fakellm"
)

func main() {
	host := flag.String("host", "127.0.0.1", "bind address")
	port := flag.Int("port", 8080, "HTTP port")
	flag.Parse()
	if *port < 0 || *port > 65535 {
		fatal("invalid port")
	}
	listener, err := net.Listen("tcp", fmt.Sprintf("%s:%d", *host, *port))
	if err != nil {
		fatal(err.Error())
	}
	defer listener.Close()
	server := fakellm.NewServer(fakellm.ConfigFromEnv(os.Getenv))
	context, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	go func() {
		<-context.Done()
		_ = server.Shutdown(context)
	}()
	if err := server.Serve(listener); err != nil && err.Error() != "http: Server closed" {
		fatal(err.Error())
	}
}

func fatal(message string) {
	_, _ = fmt.Fprintln(os.Stderr, message)
	os.Exit(1)
}

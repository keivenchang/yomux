#!/usr/bin/env bash
# setup-tls.sh — set up trusted HTTPS for YOLOmux via a local CA.
#
# What it does (idempotent — safe to re-run):
#   1. Creates a local CA once (~/.local/share/yolomux-ca), reuses it thereafter.
#   2. Issues a server leaf cert covering localhost + every non-docker LAN IP +
#      this host's name (+ any extra --san you pass), signed by that CA.
#   3. Installs the leaf where YOLOmux serves it (STATE_DIR/tls/self-signed.*),
#      backing up whatever was there.
#   4. Trusts the CA in THIS machine's trust store (Linux/macOS), if it can.
#   5. Prints the CA path + fingerprint + client-import instructions.
#
# After running, RESTART the server(s) so they load the new cert:
#   bash <checkout>/boot.sh <port>
#
# Usage:
#   tools/setup-tls.sh [--san NAME_OR_IP]... [--no-trust] [--dry-run]
#                        [--state-dir DIR] [--ca-dir DIR]
#   --san      extra hostname/IP the server is reached by (repeatable) — e.g. a
#              DNS name, VPN/Tailscale IP, or public hostname.
#   --no-trust don't touch the local trust store (just write the cert).
#   --dry-run  print the computed SAN and planned actions; write nothing.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
STATE_DIR=""
CA_DIR="${YOLOMUX_CA_DIR:-$HOME/.local/share/yolomux-ca}"
LEAF_DAYS="${YOLOMUX_LEAF_DAYS:-825}"
CA_DAYS="${YOLOMUX_CA_DAYS:-3650}"
INSTALL_TRUST=1
DRY_RUN=0
EXTRA_SANS=()

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
note() { printf '%s\n' "$*"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --san) [ $# -ge 2 ] || die "--san requires a value"; EXTRA_SANS+=("$2"); shift 2 ;;
    --no-trust) INSTALL_TRUST=0; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --state-dir) [ $# -ge 2 ] || die "--state-dir requires a value"; STATE_DIR="$2"; shift 2 ;;
    --ca-dir) [ $# -ge 2 ] || die "--ca-dir requires a value"; CA_DIR="$2"; shift 2 ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    *) die "unknown arg: $1" ;;
  esac
done

command -v openssl >/dev/null || die "openssl not found"
command -v "$PYTHON_BIN" >/dev/null || die "$PYTHON_BIN not found"
if [ -z "$STATE_DIR" ]; then
  STATE_DIR="$(cd "$REPO_ROOT" && "$PYTHON_BIN" -c 'from yolomux_lib.common import STATE_DIR; print(STATE_DIR)')"
fi
TLS_DIR="$STATE_DIR/tls"

# --- Reuse the server's portable SAN owner, then append explicit extras.
build_san() {
  local out e
  out="$(cd "$REPO_ROOT" && "$PYTHON_BIN" -c 'from yolomux_lib.cli import self_signed_san; print(self_signed_san())')"
  for e in "${EXTRA_SANS[@]}"; do
    if printf '%s' "$e" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$|:'; then
      out="$out,IP:$e"
    else
      out="$out,DNS:$e"
    fi
  done
  # dedupe, preserve order
  printf '%s' "$out" | tr ',' '\n' | awk '!seen[$0]++' | paste -sd, -
}

SAN="$(build_san)"
note "SAN: $SAN"

if [ "$DRY_RUN" = 1 ]; then
  note "[dry-run] CA:        $CA_DIR/rootCA.crt (create if missing)"
  note "[dry-run] leaf ->    $TLS_DIR/self-signed.{crt,key} (${LEAF_DAYS}d)"
  note "[dry-run] trust:     $([ "$INSTALL_TRUST" = 1 ] && echo "install into local store" || echo "skipped")"
  exit 0
fi

umask 077
mkdir -p "$CA_DIR" "$TLS_DIR"
chmod 700 "$CA_DIR"

# --- Root CA (create once; reused on later runs so clients never re-import). ---
if [ ! -f "$CA_DIR/rootCA.crt" ] || [ ! -f "$CA_DIR/rootCA.key" ]; then
  note "creating local CA"
  openssl genrsa -out "$CA_DIR/rootCA.key" 4096 2>/dev/null
  openssl req -new -key "$CA_DIR/rootCA.key" -subj "/O=YOLOmux Dev/CN=YOLOmux Local CA" \
    -out "$CA_DIR/rootCA.csr" 2>/dev/null
  printf 'basicConstraints=critical,CA:TRUE\nkeyUsage=critical,keyCertSign,cRLSign\nsubjectKeyIdentifier=hash\n' \
    > "$CA_DIR/ca.ext"
  openssl x509 -req -in "$CA_DIR/rootCA.csr" -signkey "$CA_DIR/rootCA.key" -days "$CA_DAYS" \
    -sha256 -extfile "$CA_DIR/ca.ext" -out "$CA_DIR/rootCA.crt" 2>/dev/null
  rm -f "$CA_DIR/rootCA.csr"
else
  note "reusing existing CA at $CA_DIR"
fi
chmod 600 "$CA_DIR/rootCA.key"

# --- Leaf cert signed by the CA (reissued every run so new IPs are picked up). ---
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
openssl genrsa -out "$tmp/leaf.key" 2048 2>/dev/null
openssl req -new -key "$tmp/leaf.key" -subj "/CN=$(hostname) YOLOmux" -out "$tmp/leaf.csr" 2>/dev/null
printf 'subjectAltName=%s\nextendedKeyUsage=serverAuth\nbasicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\n' \
  "$SAN" > "$tmp/leaf.ext"
openssl x509 -req -in "$tmp/leaf.csr" -CA "$CA_DIR/rootCA.crt" -CAkey "$CA_DIR/rootCA.key" \
  -CAcreateserial -days "$LEAF_DAYS" -sha256 -extfile "$tmp/leaf.ext" -out "$tmp/leaf.crt" 2>/dev/null

# --- Install where YOLOmux serves it (back up any existing cert first). ---
stamp="$(date +%Y%m%d-%H%M%S)"
for f in self-signed.crt self-signed.key; do
  [ -f "$TLS_DIR/$f" ] && \cp -f "$TLS_DIR/$f" "$TLS_DIR/$f.bak.$stamp"
done
\cp -f "$tmp/leaf.crt" "$TLS_DIR/self-signed.crt"
\cp -f "$tmp/leaf.key" "$TLS_DIR/self-signed.key"
chmod 600 "$TLS_DIR/self-signed.crt" "$TLS_DIR/self-signed.key"
note "installed leaf -> $TLS_DIR/self-signed.{crt,key}"

# --- Trust the CA on this machine (best-effort). ---
if [ "$INSTALL_TRUST" = 1 ]; then
  case "$(uname -s)" in
    Linux)
      if command -v update-ca-certificates >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
        sudo -n cp "$CA_DIR/rootCA.crt" /usr/local/share/ca-certificates/yolomux-local-ca.crt
        sudo -n update-ca-certificates >/dev/null 2>&1 && note "trusted CA in system store (Linux)"
      else
        note "note: could not auto-trust (need sudo + update-ca-certificates); import $CA_DIR/rootCA.crt manually"
      fi ;;
    Darwin)
      if sudo -n true 2>/dev/null; then
        sudo -n security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain "$CA_DIR/rootCA.crt" \
          && note "trusted CA in System keychain (macOS)"
      else
        note "to trust on macOS: sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain $CA_DIR/rootCA.crt"
      fi ;;
  esac
fi

echo
note "CA (give this to client machines): $CA_DIR/rootCA.crt"
openssl x509 -in "$CA_DIR/rootCA.crt" -noout -fingerprint -sha256 | sed 's/^/  /'
cat <<EOF

Next:
  1. Restart the server(s) to load the new cert:  bash <checkout>/boot.sh <port>
  2. On each CLIENT machine, trust the CA once (then every port is warning-free):
       scp $(hostname):$CA_DIR/rootCA.crt ~/yolomux-local-ca.crt
       # macOS:  sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain ~/yolomux-local-ca.crt
       # Linux:  sudo cp ~/yolomux-local-ca.crt /usr/local/share/ca-certificates/yolomux-local-ca.crt && sudo update-ca-certificates
       # Firefox uses its own store: Settings > Privacy > Certificates > Authorities > Import
EOF

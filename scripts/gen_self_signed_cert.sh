#!/usr/bin/env bash
set -euo pipefail

# Generate a self-signed TLS certificate for local HTTPS (Uvicorn option B).
#
# Why SAN matters:
#   Modern browsers validate SubjectAltName (SAN). If you browse to
#   https://todo.local the cert must include DNS:todo.local; if you browse to
#   https://192.168.1.50 it must include IP:192.168.1.50.
#
# Usage:
#   ./scripts/gen_self_signed_cert.sh
#     -> creates certs/localhost.crt and certs/localhost.key with SAN=localhost,127.0.0.1
#
#   ./scripts/gen_self_signed_cert.sh todo.local 192.168.1.50
#     -> uses CN=todo.local and SAN includes DNS:todo.local, DNS:localhost, IP:127.0.0.1, IP:192.168.1.50
#
#   DAYS=365 OUT_DIR=certs CERT_BASENAME=mycert ./scripts/gen_self_signed_cert.sh todo.local
#

OUT_DIR=${OUT_DIR:-certs}
CERT_BASENAME=${CERT_BASENAME:-localhost}
DAYS=${DAYS:-825}

if ! command -v openssl >/dev/null 2>&1; then
  echo "Error: openssl not found. Please install openssl." >&2
  exit 1
fi

mkdir -p "$OUT_DIR"

# First arg (if present) becomes the CN.
CN=${1:-localhost}
shift || true

# Build SAN list.
# Always include localhost + 127.0.0.1 so it works for local dev.
DNS_SANS=("localhost")
IP_SANS=("127.0.0.1")

# Include CN in DNS SAN if it looks like a hostname.
if [[ "$CN" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  IP_SANS+=("$CN")
else
  DNS_SANS+=("$CN")
fi

# Remaining args: each may be a hostname or an IP.
for entry in "$@"; do
  entry_trimmed=$(echo "$entry" | xargs)
  [[ -z "$entry_trimmed" ]] && continue
  if [[ "$entry_trimmed" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    IP_SANS+=("$entry_trimmed")
  else
    DNS_SANS+=("$entry_trimmed")
  fi
done

# De-duplicate SANs.
# shellcheck disable=SC2207
DNS_SANS=($(printf "%s\n" "${DNS_SANS[@]}" | awk 'NF' | sort -u))
# shellcheck disable=SC2207
IP_SANS=($(printf "%s\n" "${IP_SANS[@]}" | awk 'NF' | sort -u))

SAN_LINES=""
for d in "${DNS_SANS[@]}"; do
  SAN_LINES+="DNS:${d},"
done
for ip in "${IP_SANS[@]}"; do
  SAN_LINES+="IP:${ip},"
done
SAN_LINES=${SAN_LINES%,}

KEY_FILE="$OUT_DIR/$CERT_BASENAME.key"
CERT_FILE="$OUT_DIR/$CERT_BASENAME.crt"

# Use a temp openssl config so we can embed SANs.
TMP_CFG=$(mktemp)
trap 'rm -f "$TMP_CFG"' EXIT

cat > "$TMP_CFG" <<EOF
[req]
distinguished_name = dn
x509_extensions = v3_req
prompt = no

[dn]
CN = $CN

[v3_req]
keyUsage = keyEncipherment, dataEncipherment, digitalSignature
extendedKeyUsage = serverAuth
subjectAltName = $SAN_LINES
EOF

echo "Generating self-signed cert:" 
echo "  CN:   $CN" 
echo "  SAN:  $SAN_LINES" 
echo "  OUT:  $CERT_FILE" 
echo "  KEY:  $KEY_FILE" 

auth_umask=$(umask)
# Private key should be readable only by the owner by default.
umask 077

openssl req -x509 -newkey rsa:2048 -sha256 -nodes \
  -days "$DAYS" \
  -keyout "$KEY_FILE" \
  -out "$CERT_FILE" \
  -config "$TMP_CFG" \
  -extensions v3_req

umask "$auth_umask"

echo
echo "Done."
echo "To run uvicorn with TLS (example):"
echo "  uvicorn main:app --host 0.0.0.0 --port 8443 --ssl-keyfile $KEY_FILE --ssl-certfile $CERT_FILE"

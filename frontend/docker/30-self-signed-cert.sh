#!/bin/sh
# Certificado autofirmado para HTTPS (la cámara y el micrófono exigen contexto seguro fuera de localhost).
# Monte certificados reales en /etc/nginx/certs para sustituirlo.
set -e
CERT_DIR=/etc/nginx/certs
if [ ! -f "$CERT_DIR/tls.crt" ]; then
  mkdir -p "$CERT_DIR"
  openssl req -x509 -nodes -newkey rsa:2048 -days 365 \
    -keyout "$CERT_DIR/tls.key" -out "$CERT_DIR/tls.crt" \
    -subj "/CN=${TLS_COMMON_NAME:-localhost}" \
    -addext "subjectAltName=DNS:localhost,IP:127.0.0.1${TLS_EXTRA_SAN:+,$TLS_EXTRA_SAN}" >/dev/null 2>&1
  echo "Certificado autofirmado generado en $CERT_DIR"
fi

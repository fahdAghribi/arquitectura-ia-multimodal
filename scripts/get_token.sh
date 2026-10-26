#!/usr/bin/env sh
# Obtiene un access token de Keycloak para pruebas con curl (password grant del cliente público pwa-client).
# Uso: TOKEN=$(./scripts/get_token.sh gestor.laura laura123)
set -e
USER="${1:-cliente.ana}"
PASS="${2:-ana123}"
KC="${KEYCLOAK_URL:-http://localhost:${KEYCLOAK_HOST_PORT:-8080}}"
curl -s -X POST "$KC/realms/asesoria-realm/protocol/openid-connect/token" \
  -d grant_type=password -d client_id=pwa-client -d "username=$USER" -d "password=$PASS" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('access_token') or sys.exit(d))"

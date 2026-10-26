#!/bin/sh
# Configuración del cliente en tiempo de ejecución (una misma imagen sirve para cualquier entorno).
set -e
cat > /usr/share/nginx/html/env.js <<JS
window.__ENV__ = {
  KEYCLOAK_URL: "${PWA_PUBLIC_KEYCLOAK_URL:-}",
  KEYCLOAK_REALM: "${REACT_APP_KEYCLOAK_REALM:-asesoria-realm}",
  KEYCLOAK_CLIENT_ID: "${REACT_APP_KEYCLOAK_CLIENT_ID:-pwa-client}"
};
JS

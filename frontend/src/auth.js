import Keycloak from 'keycloak-js'

const env = window.__ENV__ || {}

// Keycloak se sirve en el mismo origen a través de nginx (/realms, /resources):
// funciona igual en localhost que desde un móvil en la red de planta por HTTPS.
export const keycloak = new Keycloak({
  url: env.KEYCLOAK_URL || window.location.origin,
  realm: env.KEYCLOAK_REALM || 'asesoria-realm',
  clientId: env.KEYCLOAK_CLIENT_ID || 'pwa-client',
})

export async function initAuth() {
  // Los tokens viven solo en memoria (sin localStorage); PKCE S256 en el flujo authorization code.
  await keycloak.init({ onLoad: 'login-required', pkceMethod: 'S256', checkLoginIframe: false })
  setInterval(() => keycloak.updateToken(60).catch(() => keycloak.login()), 30_000)
}

export async function freshToken() {
  await keycloak.updateToken(30)
  return keycloak.token
}

export async function apiFetch(path, options = {}) {
  const token = await freshToken()
  const res = await fetch(path, { ...options, headers: { ...(options.headers || {}), Authorization: `Bearer ${token}` } })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail || `HTTP ${res.status}`)
  }
  return res.json()
}

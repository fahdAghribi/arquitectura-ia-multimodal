import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'

// En desarrollo (npm run dev) se replica el proxy same-origin de nginx.
const api = process.env.API_GATEWAY_URL || 'http://localhost:8000'
const keycloak = process.env.KEYCLOAK_URL || 'http://localhost:8080'

export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: 'autoUpdate',
      includeAssets: ['icon.svg'],
      manifest: {
        name: 'Asistente de trámites tributarios',
        short_name: 'Trámites',
        description: 'Ayuda con trámites tributarios para clientes del despacho',
        lang: 'es',
        theme_color: '#0f172a',
        background_color: '#0f172a',
        display: 'standalone',
        orientation: 'portrait',
        start_url: '/',
        icons: [
          { src: '/icon-192.png', sizes: '192x192', type: 'image/png' },
          { src: '/icon-512.png', sizes: '512x512', type: 'image/png' },
          { src: '/icon-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
        ],
      },
      workbox: {
        navigateFallbackDenylist: [/^\/api/, /^\/ws/, /^\/realms/, /^\/resources/, /^\/static/, /^\/health/, /^\/a2a/, /^\/mcp-rag/],
        globIgnores: ['**/env.js'],
        runtimeCaching: [
          {
            // Capturas de los manuales consultadas: disponibles sin conexión.
            urlPattern: ({ url }) => url.pathname.startsWith('/static/extracted_images/'),
            handler: 'CacheFirst',
            options: { cacheName: 'manual-figures', expiration: { maxEntries: 300, maxAgeSeconds: 30 * 24 * 3600 } },
          },
          { urlPattern: ({ url }) => url.pathname === '/env.js', handler: 'NetworkFirst', options: { cacheName: 'runtime-env' } },
        ],
      },
    }),
  ],
  server: {
    proxy: {
      '/api': api,
      '/static': api,
      '/health': api,
      '/ws': { target: api.replace('http', 'ws'), ws: true },
      '/realms': { target: keycloak, changeOrigin: false, xfwd: true },
      '/resources': { target: keycloak, changeOrigin: false, xfwd: true },
    },
  },
})

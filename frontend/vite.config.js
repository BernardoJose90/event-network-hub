import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// /api is proxied to the backend so the browser only ever talks to one origin.
const apiTarget = process.env.VITE_API_PROXY_TARGET || 'http://localhost:8000'
// Extra host names the dev server answers to, e.g. a tunnel for outside testers
// (".trycloudflare.com" allows every subdomain). localhost always works.
const allowedHosts = (process.env.VITE_ALLOWED_HOSTS || '').split(',').map((h) => h.trim()).filter(Boolean)

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    strictPort: true,
    allowedHosts,
    proxy: {
      '/api': { target: apiTarget, changeOrigin: true },
    },
  },
})

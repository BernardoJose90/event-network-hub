import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// /api is proxied to the backend so the browser only ever talks to one origin.
const apiTarget = process.env.VITE_API_PROXY_TARGET || 'http://localhost:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': { target: apiTarget, changeOrigin: true },
    },
  },
})

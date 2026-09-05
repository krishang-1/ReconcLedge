import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // Lets the dev server proxy /v1/* to the real backend, so the
    // frontend never has to hardcode a backend origin - matches how
    // the API client (src/api/client.ts) calls relative /v1/... paths
    // by default. Override VITE_API_BASE_URL if the backend runs
    // somewhere other than localhost:8000.
    proxy: {
      '/v1': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
    },
  },
})

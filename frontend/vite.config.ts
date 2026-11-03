import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

const apiProxyOrigin = process.env.API_PROXY_ORIGIN ?? 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: apiProxyOrigin,
        changeOrigin: true,
      },
    },
  },
})

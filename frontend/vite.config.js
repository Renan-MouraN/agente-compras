import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Em desenvolvimento (npm run dev), /api é encaminhado ao backend local.
// No Docker, quem faz esse proxy é o nginx (ver nginx.conf).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: process.env.BACKEND_URL || 'http://localhost:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})

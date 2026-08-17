import path from 'node:path'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const here = import.meta.dirname

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': path.resolve(here, './src') },
  },
  build: {
    // The shipped pack has no Node server: FastAPI serves this bundle from
    // dist/ at the project root.
    outDir: path.resolve(here, '../dist'),
    emptyOutDir: true,
  },
  server: {
    // Dev only. Lets `npm run dev` talk to the Python backend on :8000.
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
})

import type { EventEmitter } from 'node:events'
import type { ClientRequest } from 'node:http'
import { fileURLToPath } from 'node:url'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// The Python engine serves the built app: index at "/", assets under "/static/".
// Dev: run the engine with `rag-lab-app --serve` (RAG_LAB_PORT=8765 RAG_LAB_TOKEN=devtoken)
// and `npm run dev`, then open http://localhost:5173/#token=devtoken
const engine = process.env.PAGEWISE_ENGINE ?? 'http://127.0.0.1:8765'

export default defineConfig(({ command }) => ({
  base: command === 'build' ? '/static/' : '/',
  plugins: [react()],
  build: {
    outDir: fileURLToPath(new URL('../../src/rag_lab/web', import.meta.url)),
    emptyOutDir: true,
    target: 'es2022',
    sourcemap: false,
    chunkSizeWarningLimit: 800,
  },
  server: {
    proxy: {
      '/api': {
        target: engine,
        changeOrigin: true,
        // The engine rejects foreign Origin headers; the dev proxy runs on the same machine.
        configure: proxy => {
          (proxy as unknown as EventEmitter).on('proxyReq', (req: ClientRequest) => req.removeHeader('origin'))
        },
      },
    },
  },
  test: { environment: 'node' },
}))

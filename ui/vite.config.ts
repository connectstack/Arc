import { fileURLToPath, URL } from 'node:url'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

const API = process.env.REEL_API ?? 'http://127.0.0.1:8765'

// `npm run build` writes the production app straight into the Python package, so `reel serve` needs no Node.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  build: {
    outDir: '../src/reel/server/static',
    emptyOutDir: true,
    sourcemap: false,
    // the app's Content-Security-Policy allows fonts only from its own origin: a small font inlined as a data: URL would be refused
    assetsInlineLimit: 0,
    chunkSizeWarningLimit: 1200,
  },
  server: {
    port: 5173,
    // `make ui-dev` next to `reel serve --port 8765`: the browser talks to this server, which forwards /api. The server only
    // accepts its own Host and Origin, so the proxy presents those (development only; the production app is same-origin).
    // Another server can be used with REEL_API=http://127.0.0.1:PORT.
    proxy: {
      '/api': { target: API, changeOrigin: true, headers: { Origin: API } },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    css: false,
    include: ['src/**/*.test.{ts,tsx}'],
    // generous: the tests wait on a mock engine with simulated latency, and a busy machine (a render running, other suites) slows them
    testTimeout: 20_000,
  },
})

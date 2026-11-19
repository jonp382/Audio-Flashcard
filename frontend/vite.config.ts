import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'
import { viteSingleFile } from 'vite-plugin-singlefile'

// Production output is ONE self-contained index.html (JS/CSS inlined). Python hands it
// to pywebview as an HTML string, so pywebview never starts its local HTTP server
// (agent.md §4). Dev mode (`npm run dev`) serves normally on :5173 for hot reload.
export default defineConfig(({ command }) => ({
  plugins: [react(), tailwindcss(), ...(command === 'build' ? [viteSingleFile()] : [])],
  server: { port: 5173, strictPort: true, host: '127.0.0.1' },
  build: { target: 'es2022' },
}))

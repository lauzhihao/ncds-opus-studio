import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';

// Vite dev 跑在 5173；通过 proxy 把所有后端 API 转发到 nof-server (8810)。
// 生产构建直接走 FastAPI 的 StaticFiles，不需要 proxy。
const BACKEND = process.env.NOF_SERVER || 'http://127.0.0.1:8810';

export default defineConfig({
  base: '/studio/',
  plugins: [react()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      // 后端 REST 端点全部代理；按前缀枚举，避免误代理掉 vite 自身的 /__vite
      '/pipelines': BACKEND,
      '/jobs': BACKEND,
      '/tasks': BACKEND,
      '/templates': BACKEND,
      '/health': BACKEND,
    },
  },
});

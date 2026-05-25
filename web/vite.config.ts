import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';

// Vite dev 跑在 5173；通过 proxy 把所有后端 API 转发到 nof-server (8810)。
// 生产构建直接走 FastAPI 的 StaticFiles，不需要 proxy。
//
// 当用户从 :8810 进、由 FastAPI dev_proxy 反代 /studio/* 时，HMR 客户端的 ws 升级
// 必须指回 :8810（默认会按 location.port 连 5173）。NOF_HMR_PORT 显式覆盖。
const BACKEND = process.env.NOF_SERVER || 'http://127.0.0.1:8810';
const HMR_CLIENT_PORT = Number(process.env.NOF_HMR_PORT || 8810);
const VITE_PORT = Number(process.env.NOF_VITE_PORT || 5173);

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
    port: VITE_PORT,
    strictPort: true,
    hmr: {
      // 让浏览器去连 :8810（FastAPI dev_proxy），由其反代到本进程的 ws
      clientPort: HMR_CLIENT_PORT,
    },
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

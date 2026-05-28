import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';

// Vite dev 跑在 5173；通过 proxy 把所有后端 API 转发到 nof-server (8810)。
// 生产构建直接走 FastAPI 的 StaticFiles，不需要 proxy。
//
// HMR 客户端 WS 的入口由几个 env 变量驱动（按访问入口选择，写到 web/.env 即可）：
//   - 直接 :5173      → 全留空（默认）
//   - FastAPI :8810   → NOF_HMR_PORT=8810
//   - nginx + HTTPS   → NOF_HMR_HOST=dev.jwd.group NOF_HMR_PROTOCOL=wss NOF_HMR_PORT=443
// 不设置就用 vite 自身默认（按 server.port），最适合直连场景。
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const BACKEND = env.NOF_SERVER || 'http://127.0.0.1:8810';
  const VITE_PORT = Number(env.NOF_VITE_PORT || 5173);

  const HMR_HOST = env.NOF_HMR_HOST || undefined;
  const HMR_PROTOCOL = (env.NOF_HMR_PROTOCOL || undefined) as 'ws' | 'wss' | undefined;
  const HMR_CLIENT_PORT = env.NOF_HMR_PORT ? Number(env.NOF_HMR_PORT) : undefined;

  return {
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
      // dev 环境放行所有 Host header（本机 nginx 用 dev.jwd.group/studio 反代进来）
      allowedHosts: true,
      hmr: {
        ...(HMR_HOST && { host: HMR_HOST }),
        ...(HMR_PROTOCOL && { protocol: HMR_PROTOCOL }),
        ...(HMR_CLIENT_PORT && { clientPort: HMR_CLIENT_PORT }),
      },
      proxy: {
        // 后端 REST 端点全部代理；按前缀枚举，避免误代理掉 vite 自身的 /__vite
        '/pipelines': BACKEND,
        '/jobs': BACKEND,
        '/tasks': BACKEND,
        '/templates': BACKEND,
        '/health': BACKEND,
        // preview 节点 iframe 走这个：HTML 模板 + episode.json + audio/picture 资产
        '/preview': BACKEND,
      },
    },
  };
});

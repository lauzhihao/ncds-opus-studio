# syntax=docker/dockerfile:1
# ncds-opus-studio 生产镜像：web/dist + python3.12 server/worker 同一镜像（命令区分角色）。
# 设计依据：app.py 用 parents[3]/web/dist 解析前端 → 必须 editable 安装且代码在 /app/src，
# web/dist 落在 /app/web/dist。

# ─────────────────── Stage 1: 构建 web/dist（vite）───────────────────
FROM node:20-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
# tsc -b && vite build；tsc 类型报错会中断，构建侧暴露问题
RUN npm run build

# ─────────────── Stage 2: python3.12 运行时（server + worker）───────────────
FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# 系统依赖：ffmpeg(媒体处理) + nodejs/npm(.mjs runner) + git/curl/ca-certificates
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg git curl ca-certificates nodejs npm \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 1) python 依赖层（独立缓存）：core + factory 都 editable 安装，
#    让 app.py 的 parents[3]/web/dist 解析到 /app/web/dist。
COPY pyproject.toml ./
COPY packages/ packages/
COPY src/ src/
RUN pip install -e packages/core && pip install -e .

# 2) node runner 依赖层（root package.json：cos/undici/puppeteer-core 等）
COPY package.json package-lock.json ./
RUN npm ci --omit=dev || npm install --omit=dev

# 3) 其余运行期需要的仓库内容（scripts / templates / configs …）
COPY . .

# 4) 前端构建产物（覆盖上一步 COPY 进来的任何旧 dist）
COPY --from=web /web/dist web/dist

# nof-server 默认绑 0.0.0.0:8810（cli_main 读 NOF_SERVER_HOST/PORT）
EXPOSE 8810
CMD ["nof-server"]

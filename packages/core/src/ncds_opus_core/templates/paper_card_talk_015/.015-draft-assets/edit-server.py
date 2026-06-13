#!/usr/bin/env python3
"""
本地编辑服务：
  - 替代 `python3 -m http.server` 在仓库根开 8765 端口提供静态资源
  - 多一条 POST /__save_overlays：把浏览器编辑模式攒下的 overlay patch
    原地合并写回 episode.json
  - 多一条 GET /__reload_events (SSE)：监听 .js/.jsx/.mjs/.css/.html 改动，
    推 'reload' 事件让浏览器自动刷新。episode.json / audio / pictures /
    output 不参与，否则保存 overlay 改动会自杀式 reload 抹掉选中态。

用法（在仓库根目录跑）：
    python3 .015-draft-assets/edit-server.py                # 默认开热重载
    python3 .015-draft-assets/edit-server.py --no-watch     # 关掉
然后访问 http://127.0.0.1:8765/015-draft.html，按 E 进入编辑模式。
"""
import argparse
import json
import os
import queue
import re
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
PORT = int(os.environ.get('EDIT_PORT', '8765'))
SLUG_RE = re.compile(r'^[a-z0-9][a-z0-9\-]{1,40}$')

# 监听这些扩展名才推 reload；其它（episode.json / mp3 / webp / mp4 / 临时文件）忽略
WATCH_EXTS = {'.js', '.jsx', '.mjs', '.css', '.html'}
WATCH_EXCLUDE_DIRS = {'.git', 'node_modules', 'output', 'audio', 'pictures',
                      '.export-frames', 'fonts'}

# 全局 SSE 客户端队列列表 + 锁。watcher 线程往每个 queue put 一次 'reload'。
_clients = []
_clients_lock = threading.Lock()


def episode_path(slug):
    if not SLUG_RE.match(slug):
        raise ValueError(f'bad slug: {slug!r}')
    p = os.path.join(REPO_ROOT, '.' + slug + '-assets', 'episode.json')
    if not os.path.isfile(p):
        raise FileNotFoundError(p)
    return p


def deep_merge(dst, patch):
    """把 patch dict 深合并进 dst dict。list / 标量 整体替换；dict 递归合并。
    patch 里出现的 key 才动；dst 里没在 patch 里的 key 保留。"""
    if not isinstance(dst, dict) or not isinstance(patch, dict):
        return patch
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            dst[k] = deep_merge(dst[k], v)
        else:
            dst[k] = v
    return dst


def apply_path_patches(slug, patches):
    """通用 dot-path patch：patches = {'meta.title': '...', 'visual.palette': 'sage', ...}。
    走中间路径时缺 dict 自动建。值整体替换（不深合并），适合简单标量 / 字符串字段。
    用于 Tweaks 面板这种 meta / visual / playback 全局配置回写。"""
    ep_path = episode_path(slug)
    with open(ep_path, 'r', encoding='utf-8') as f:
        ep = json.load(f)
    for path, value in patches.items():
        if not isinstance(path, str) or not path:
            raise ValueError(f'bad path: {path!r}')
        parts = path.split('.')
        cur = ep
        for p in parts[:-1]:
            if p not in cur or not isinstance(cur[p], dict):
                cur[p] = {}
            cur = cur[p]
        cur[parts[-1]] = value
    tmp = ep_path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(ep, f, ensure_ascii=False, indent=2)
        f.write('\n')
    os.replace(tmp, ep_path)
    return len(patches)


def add_overlay(slug, scene, overlay):
    """往 scenes[scene].overlays 末尾 append 一个新 overlay；返回新分配的 index。
    scene.overlays 不存在 / 不是 list 时初始化为 []。
    与 apply_patches 用法不同：那个按 index 改既有项，这个加新项。"""
    ep_path = episode_path(slug)
    with open(ep_path, 'r', encoding='utf-8') as f:
        ep = json.load(f)
    scenes = ep.get('scenes') or {}
    if scene not in scenes:
        raise KeyError(f'scene not found: {scene}')
    sc = scenes[scene]
    if not isinstance(sc.get('overlays'), list):
        sc['overlays'] = []
    if not isinstance(overlay, dict):
        raise ValueError('overlay must be an object')
    sc['overlays'].append(overlay)
    new_idx = len(sc['overlays']) - 1
    tmp = ep_path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(ep, f, ensure_ascii=False, indent=2)
        f.write('\n')
    os.replace(tmp, ep_path)
    return new_idx


def del_overlay(slug, scene, index):
    """从 scenes[scene].overlays.pop(index)；返回剩余 overlay 数."""
    ep_path = episode_path(slug)
    with open(ep_path, 'r', encoding='utf-8') as f:
        ep = json.load(f)
    scenes = ep.get('scenes') or {}
    if scene not in scenes:
        raise KeyError(f'scene not found: {scene}')
    sc = scenes[scene]
    ovs = sc.get('overlays')
    if not isinstance(ovs, list):
        raise ValueError(f'scene {scene} has no overlays list')
    if not isinstance(index, int) or index < 0 or index >= len(ovs):
        raise IndexError(f'overlay index out of range: {scene}#{index} (len={len(ovs)})')
    ovs.pop(index)
    tmp = ep_path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(ep, f, ensure_ascii=False, indent=2)
        f.write('\n')
    os.replace(tmp, ep_path)
    return len(ovs)


def apply_patches(slug, patches):
    ep_path = episode_path(slug)
    with open(ep_path, 'r', encoding='utf-8') as f:
        ep = json.load(f)
    scenes = ep.get('scenes') or {}
    touched = 0
    for p in patches:
        sid = p.get('scene')
        idx = p.get('index')
        patch = p.get('patch') or {}
        if sid not in scenes:
            raise KeyError(f'scene not found: {sid}')
        scene = scenes[sid]
        ovs = scene.get('overlays') or []
        if not isinstance(idx, int) or idx < 0 or idx >= len(ovs):
            raise IndexError(f'overlay index out of range: {sid}#{idx} (len={len(ovs)})')
        deep_merge(ovs[idx], patch)
        touched += 1
    # 原子写：先写 tmp，再 rename
    tmp = ep_path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(ep, f, ensure_ascii=False, indent=2)
        f.write('\n')
    os.replace(tmp, ep_path)
    return touched


def broadcast_reload(reason):
    msg = ('event: reload\ndata: ' + json.dumps({'reason': reason}, ensure_ascii=False) + '\n\n').encode('utf-8')
    with _clients_lock:
        dead = []
        for q in _clients:
            try:
                q.put_nowait(msg)
            except Exception:
                dead.append(q)
        for q in dead:
            if q in _clients:
                _clients.remove(q)


def watcher_thread():
    """watchfiles 后台线程：扫 REPO_ROOT，遇到 WATCH_EXTS 文件变化就 broadcast。"""
    try:
        from watchfiles import watch, Change
    except ImportError:
        print('[edit-server] watchfiles 不可用，--watch 关闭', file=sys.stderr)
        return

    def filt(change_type, path):
        # path 是绝对路径。先按目录前缀剔除
        rel = os.path.relpath(path, REPO_ROOT)
        parts = rel.split(os.sep)
        if any(p in WATCH_EXCLUDE_DIRS for p in parts):
            return False
        if any(p.startswith('.') and p not in {'.015-draft-assets', '.012-not-fooled-assets',
                                                '.011-reading-confidence-assets'} and not p.startswith('.0') for p in parts):
            # 排除其它 dotfile/dir，但允许 .NNN-*-assets（编辑哪一集就改哪一集）
            return False
        ext = os.path.splitext(path)[1].lower()
        return ext in WATCH_EXTS

    print(f'[edit-server] 热重载已开启：监听 {", ".join(sorted(WATCH_EXTS))} 变化', file=sys.stderr)
    last_push = 0.0
    for changes in watch(REPO_ROOT, watch_filter=filt, recursive=True, debounce=300):
        # 取最具代表性的变更说明，发给浏览器 console
        sample = next(iter(changes))
        reason = os.path.relpath(sample[1], REPO_ROOT)
        # 双重节流：watchfiles debounce 300ms + 这里再 200ms，防重复推
        now = time.time()
        if now - last_push < 0.2:
            continue
        last_push = now
        print(f'[edit-server] reload → {reason} ({len(changes)} files)', file=sys.stderr)
        broadcast_reload(reason)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=REPO_ROOT, **kwargs)

    def log_message(self, fmt, *args):
        # 静态请求噪音很大；只打 POST / SSE 与错误
        msg = fmt % args
        if msg.startswith('"POST') or msg.startswith('"GET /__'):
            sys.stderr.write('[edit-server] %s - %s\n' % (self.address_string(), msg))

    def _json_error(self, code, msg):
        body = json.dumps({'error': msg}, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json_ok(self, payload):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/__reload_events':
            return self._sse_loop()
        if path == '/__ping':
            # 前端启动时探测 edit-server 是否可达 —— 可达则启用保存 + 编辑 UI，
            # 不可达（线上 ncds.cc / 纯静态托管）则前端静默隐藏 UI、不发保存请求。
            return self._json_ok({'ok': True, 'service': 'edit-server'})
        return super().do_GET()

    def _sse_loop(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache, no-store')
        self.send_header('Connection', 'keep-alive')
        self.send_header('X-Accel-Buffering', 'no')
        self.end_headers()
        q = queue.Queue()
        with _clients_lock:
            _clients.append(q)
        try:
            # 先打个招呼，让客户端确认连上了
            self.wfile.write(b'event: hello\ndata: ok\n\n')
            self.wfile.flush()
            while True:
                try:
                    msg = q.get(timeout=15)
                except queue.Empty:
                    # keep-alive 注释行，防中间代理超时
                    self.wfile.write(b': ping\n\n')
                    self.wfile.flush()
                    continue
                self.wfile.write(msg)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            with _clients_lock:
                if q in _clients:
                    _clients.remove(q)

    def do_POST(self):
        path = urlparse(self.path).path
        if path not in ('/__save_overlays', '/__save_episode', '/__add_overlay', '/__del_overlay'):
            return self._json_error(404, 'unknown endpoint')
        try:
            n = int(self.headers.get('Content-Length') or '0')
            if n <= 0 or n > 1_000_000:
                return self._json_error(400, 'bad content-length')
            raw = self.rfile.read(n)
            body = json.loads(raw.decode('utf-8'))
            slug = body.get('slug')
            if not slug:
                return self._json_error(400, 'missing slug')
            if path == '/__save_overlays':
                patches = body.get('patches')
                if not isinstance(patches, list):
                    return self._json_error(400, 'patches must be list')
                touched = apply_patches(slug, patches)
                self._json_ok({'ok': True, 'touched': touched})
            elif path == '/__save_episode':
                patches = body.get('patches')
                if not isinstance(patches, dict):
                    return self._json_error(400, 'patches must be dict of {path: value}')
                touched = apply_path_patches(slug, patches)
                self._json_ok({'ok': True, 'touched': touched})
            elif path == '/__add_overlay':
                scene = body.get('scene')
                overlay = body.get('overlay')
                if not scene:
                    return self._json_error(400, 'missing scene')
                new_idx = add_overlay(slug, scene, overlay or {})
                self._json_ok({'ok': True, 'index': new_idx})
            else:  # /__del_overlay
                scene = body.get('scene')
                index = body.get('index')
                if not scene:
                    return self._json_error(400, 'missing scene')
                if not isinstance(index, int):
                    return self._json_error(400, 'index must be int')
                remaining = del_overlay(slug, scene, index)
                self._json_ok({'ok': True, 'remaining': remaining})
        except (ValueError, KeyError, IndexError, FileNotFoundError) as e:
            self._json_error(400, str(e))
        except Exception as e:
            sys.stderr.write('[edit-server] 500: %r\n' % (e,))
            self._json_error(500, repr(e))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--no-watch', action='store_true', help='关闭文件监听 / SSE 热重载')
    ap.add_argument('--port', type=int, default=PORT)
    args = ap.parse_args()

    os.chdir(REPO_ROOT)
    if not args.no_watch:
        t = threading.Thread(target=watcher_thread, daemon=True)
        t.start()
    srv = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print(f'[edit-server] serving {REPO_ROOT} on http://127.0.0.1:{args.port}')
    print(f'[edit-server] POST /__save_overlays -> patch episode.json')
    if not args.no_watch:
        print(f'[edit-server] GET  /__reload_events (SSE) -> 浏览器收到自动 reload')
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print('\n[edit-server] bye')


if __name__ == '__main__':
    main()

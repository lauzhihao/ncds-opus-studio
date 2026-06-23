/* dev-reload.js — 开发期浏览器自动 reload 客户端
 *
 * Studio preview 通过 /preview/{job_id}/__reload_events 保持一条 SSE 连接。
 *
 * 仅在 Studio preview 入口下生效；不再连接根路径旧接口。
 *
 * 注意：episode.json 在监听之外；预览播放不因产物保存而自刷新。
 */
(function () {
  const PREVIEW_API_BASE = window.__previewApiBase;
  if (!PREVIEW_API_BASE) return;

  let es = null;
  let reloading = false;
  let firstConnect = true;
  let backoff = 500;

  function connect() {
    try { if (es) es.close(); } catch (_) {}
    es = new EventSource(PREVIEW_API_BASE + '/__reload_events');

    es.addEventListener('hello', () => {
      // 连上 / 重连成功时背景刷新一次，让 server 重启后浏览器拿到新版
      // （server 重启场景：用户改了 edit-server.py 自己重启）
      if (!firstConnect && !reloading) {
        reloading = true;
        console.log('[dev-reload] server reconnected → reload');
        setTimeout(() => location.reload(), 50);
        return;
      }
      firstConnect = false;
      backoff = 500;
      console.log('[dev-reload] connected to edit-server SSE');
    });

    es.addEventListener('reload', (ev) => {
      if (reloading) return;
      reloading = true;
      let reason = '';
      try { reason = (JSON.parse(ev.data) || {}).reason || ''; } catch (_) {}
      console.log('[dev-reload] file changed' + (reason ? ': ' + reason : '') + ' → reload');
      setTimeout(() => location.reload(), 50);
    });

    es.onerror = () => {
      try { es.close(); } catch (_) {}
      es = null;
      // 指数退避重连：server 重启 / 网络抖动都靠这条续命
      const delay = Math.min(backoff, 5000);
      backoff = Math.min(backoff * 1.5, 5000);
      setTimeout(connect, delay);
    };
  }

  connect();
})();

/* dev-reload.js — 开发期浏览器自动 reload 客户端
 *
 * edit-server.py --watch 监听 .js/.jsx/.css/.html 改动，通过 SSE 推 'reload' 事件，
 * 这里收到就 location.reload()。
 *
 * 仅在 edit-server 可达时生效（bootstrap.js 的 /__ping 探测结果）；
 * 线上纯静态托管时 window.__editServerOk = false，请求都不发。
 *
 * 注意：episode.json 在监听之外。所以 inspector 的 Save 不会触发 reload，
 * 选中态和 dirty buffer 都不会被自杀式清空。
 */
(function () {
  if (!window.__editServerOk) return;

  let es = null;
  let reloading = false;
  let firstConnect = true;
  let backoff = 500;

  function connect() {
    try { if (es) es.close(); } catch (_) {}
    es = new EventSource('/__reload_events');

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

import { useState, useRef, useEffect, useCallback } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { ArrowLeft, Copy, Download, Loader2, Pencil, Plus, RotateCcw, SendHorizonal, Square, Trash2, X } from 'lucide-react';
import { api } from '../api/client';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { ThemeSwitcher } from '../components/ThemeSwitcher';

const MODE_LABEL: Record<string, string> = { gen: '图片生成' };
const LS_KEY = 'ncds:canvas:images';

function readImageFile(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    if (!file.type.startsWith('image/')) { reject(new Error('not an image')); return; }
    const r = new FileReader();
    r.onload = () => resolve(r.result as string);
    r.onerror = () => reject(r.error);
    r.readAsDataURL(file);
  });
}

const MIN_SIZE = 120;
const MAX_SIZE = 800;
const DEFAULT_SIZE = 300;
const CARD_PAD = 12;

interface CardSize { w: number; h: number }
interface CardPos { x: number; y: number }

interface CanvasImageItem {
  id: string;
  src: string;
  prompt: string;
  ratio: string;
  path?: string;
  cmd?: string;
  taskId?: string;
  loading?: boolean;
  error?: string;
  interrupted?: boolean;
  messages?: ChatMessage[];
}

interface ChatMessage {
  role: 'user' | 'assistant';
  text: string;
  img?: string;
  prompt?: string;
  ratio?: string;
}

function ratioToSize(ratio: string, width = DEFAULT_SIZE): CardSize {
  const [rw, rh] = ratio.split(':').map(Number);
  return { w: width, h: width * rh / rw };
}

function ResizableImageCard({
  src,
  loading,
  error,
  prompt,
  ratio,
  index,
  onRemove,
  onEdit,
  onStop,
  onRetry,
  onDownload,
  onDetail,
  taskId,
  interrupted,
}: {
  src: string;
  loading?: boolean;
  error?: string;
  prompt?: string;
  ratio: string;
  index: number;
  taskId?: string;
  interrupted?: boolean;
  onRemove: () => void;
  onEdit: () => void;
  onStop: () => void;
  onRetry: () => void;
  onDownload: (src: string, prompt?: string) => void;
  onDetail: () => void;
}) {
  const [size, setSize] = useState<CardSize>(() => ratioToSize(ratio));
  const [pos, setPos] = useState<CardPos>(() => ({
    x: 40 + index * 30,
    y: 40 + index * 30,
  }));
  const aspectRef = useRef(ratioToSize(ratio).w / ratioToSize(ratio).h);
  const resizeRef = useRef<{
    startX: number; startY: number; startW: number; startH: number;
  } | null>(null);
  const moveRef = useRef<{
    startX: number; startY: number; startPX: number; startPY: number;
  } | null>(null);
  const [copied, setCopied] = useState(false);
  const startRef = useRef(0);
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    if (!loading || interrupted) return;
    startRef.current = Date.now();
    setElapsed(0);
    const id = setInterval(() => setElapsed(Math.floor((Date.now() - startRef.current) / 1000)), 1000);
    return () => clearInterval(id);
  }, [loading, interrupted]);

  useEffect(() => {
    if (loading || error) {
      const s = ratioToSize(ratio);
      aspectRef.current = s.w / s.h;
      setSize(s);
      return;
    }
    const img = new Image();
    img.onload = () => {
      aspectRef.current = img.naturalWidth / img.naturalHeight;
      const w = Math.min(DEFAULT_SIZE, img.naturalWidth);
      setSize({ w, h: w / aspectRef.current });
    };
    img.src = src;
  }, [src, loading, error, ratio]);

  const onResizeStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault(); e.stopPropagation();
    if (!size) return;
    resizeRef.current = { startX: e.clientX, startY: e.clientY, startW: size.w, startH: size.h };
    const onMove = (ev: MouseEvent) => {
      if (!resizeRef.current) return;
      const { startX, startY, startW } = resizeRef.current;
      const dx = ev.clientX - startX, dy = ev.clientY - startY;
      const newW = Math.min(MAX_SIZE, Math.max(MIN_SIZE, startW + Math.max(dx, dy)));
      setSize({ w: newW, h: newW / aspectRef.current });
    };
    const onUp = () => {
      resizeRef.current = null;
      document.removeEventListener('mousemove', onMove); document.removeEventListener('mouseup', onUp);
      document.body.style.cursor = '';
    };
    document.addEventListener('mousemove', onMove); document.addEventListener('mouseup', onUp);
    document.body.style.cursor = 'nwse-resize';
  }, [size]);

  const onMoveStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault(); e.stopPropagation();
    moveRef.current = { startX: e.clientX, startY: e.clientY, startPX: pos.x, startPY: pos.y };
    const onMove = (ev: MouseEvent) => {
      if (!moveRef.current) return;
      const { startX, startY, startPX, startPY } = moveRef.current;
      setPos({ x: startPX + ev.clientX - startX, y: startPY + ev.clientY - startY });
    };
    const onUp = () => {
      moveRef.current = null;
      document.removeEventListener('mousemove', onMove); document.removeEventListener('mouseup', onUp);
      document.body.style.cursor = '';
    };
    document.addEventListener('mousemove', onMove); document.addEventListener('mouseup', onUp);
    document.body.style.cursor = 'grab';
  }, [pos]);

  return (
    <div className="canvas-image-card" style={{ width: size.w + CARD_PAD * 2, left: pos.x, top: pos.y }}>
      <div className="canvas-image-photo" style={{ height: size.h }} onClick={src && !loading ? onDetail : undefined}>
        {interrupted ? (
          <div className="canvas-image-interrupted">已停止</div>
        ) : loading ? (
          <div className="canvas-image-loading-wrap">
            <div className="canvas-image-loading" />
            <div className="canvas-image-spinner" />
          </div>
        ) : error ? (
          <div className="canvas-image-error">{error}</div>
        ) : (
          <img src={src} alt="" draggable={false} />
        )}
      </div>
      <div className="canvas-image-bar" onMouseDown={onMoveStart}>
        {loading ? (
          <>
            <button type="button" className="canvas-image-bar-btn" onClick={(e) => { e.stopPropagation(); onStop(); }} aria-label="停止" title="停止">
              <Square size={13} strokeWidth={1.7} />
            </button>
            <span className="spacer" />
            <span className="canvas-image-timer">{String(Math.floor(elapsed / 60)).padStart(2, '0')}:{String(elapsed % 60).padStart(2, '0')}</span>
          </>
        ) : interrupted ? (
          <>
            <span className="canvas-image-timer interrupted">interrupted</span>
            <span className="spacer" />
            <button type="button" className="canvas-image-bar-btn" onClick={(e) => { e.stopPropagation(); onRetry(); }} aria-label="重试" title="重新生成">
              <RotateCcw size={13} strokeWidth={1.7} />
            </button>
          </>
        ) : error ? (
          <>
            <button type="button" className="canvas-image-bar-btn" onClick={(e) => { e.stopPropagation(); onRetry(); }} aria-label="重试" title="重新生成">
              <RotateCcw size={13} strokeWidth={1.7} />
            </button>
            <button type="button" className="canvas-image-bar-btn" onClick={(e) => { e.stopPropagation(); navigator.clipboard.writeText(`taskId: ${taskId || ''}\n${error}`); setCopied(true); setTimeout(() => setCopied(false), 1500); }} aria-label="复制错误" title="复制错误信息">
              <Copy size={13} strokeWidth={1.7} />
            </button>
            {copied && <span className="copy-feedback"><span className="check">✓</span> Copied</span>}
          </>
        ) : (
          <>
            <button type="button" className="canvas-image-bar-btn" onClick={(e) => { e.stopPropagation(); onEdit(); }} aria-label="编辑" title="编辑">
              <Pencil size={13} strokeWidth={1.7} />
            </button>
            <button type="button" className="canvas-image-bar-btn" onClick={(e) => { e.stopPropagation(); onDownload(src, prompt); }} aria-label="下载" title="下载">
              <Download size={13} strokeWidth={1.7} />
            </button>
            <button type="button" className="canvas-image-bar-btn" onClick={(e) => { e.stopPropagation(); onRetry(); }} aria-label="重新生成" title="重新生成">
              <RotateCcw size={13} strokeWidth={1.7} />
            </button>
            <span className="spacer" />
            <button type="button" className="canvas-image-bar-btn" onClick={(e) => { e.stopPropagation(); onRemove(); }} aria-label="移除图片" title="删除">
              <Trash2 size={13} strokeWidth={1.7} />
            </button>
          </>
        )}
      </div>
      <div className="canvas-image-resize" onMouseDown={onResizeStart} />
    </div>
  );
}

function loadImages(): CanvasImageItem[] {
  try { return JSON.parse(localStorage.getItem(LS_KEY) || '[]'); } catch { return []; }
}

function saveImages(items: CanvasImageItem[]) {
  localStorage.setItem(LS_KEY, JSON.stringify(items));
}

let idCounter = Date.now();
function genId() { return String(++idCounter); }

export function CanvasPage() {
  const { mode } = useParams<{ mode: string }>();
  const nav = useNavigate();
  const label = (mode && MODE_LABEL[mode]) || '画布';
  const RATIOS = ['3:4', '1:1', '4:3', '9:16', '16:9'] as const;
  const MAX_CONCURRENT = 5;
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [drawerMode, setDrawerMode] = useState<'chat' | 'detail'>('chat');
  const [cmdPrefix, setCmdPrefix] = useState('');
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [ratio, setRatio] = useState('3:4');
  const [concurrentCount, setConcurrentCount] = useState(0);
  const [detailImageId, setDetailImageId] = useState<string | null>(null);
  const [images, setImages] = useState<CanvasImageItem[]>(() => loadImages());
  const [dragOver, setDragOver] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const dragCountRef = useRef(0);
  const cancelledRef = useRef(new Set<string>());

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [messages]);
  useEffect(() => { saveImages(images); }, [images]);

  useEffect(() => {
    if (drawerOpen) {
      const t = window.setTimeout(() => inputRef.current?.focus(), 100);
      return () => clearTimeout(t);
    }
  }, [drawerOpen]);

  function findItem(id: string): CanvasImageItem | undefined {
    return images.find((i) => i.id === id);
  }

  function openDrawerFor(prefix: string, prefill?: string, editId?: string) {
    setDetailImageId(null);
    setCmdPrefix(prefix);
    setInput(prefill || '');
    setMessages([]);
    setEditingId(editId || null);
    setDrawerMode('chat');
    setDrawerOpen(true);
  }

  function openDetailDrawer(id: string) {
    setDetailImageId(id);
    setCmdPrefix('');
    setInput('');
    setMessages([]);
    setEditingId(null);
    setDrawerMode('detail');
    setDrawerOpen(true);
  }

  function closeDrawer() {
    setDrawerOpen(false);
    setDrawerMode('chat');
    setDetailImageId(null);
    setCmdPrefix('');
    setInput('');
    setEditingId(null);
  }

  function updateItem(id: string, patch: Partial<CanvasImageItem>) {
    setImages((prev) => prev.map((item) => (item.id === id ? { ...item, ...patch } : item)));
  }

  function removeItem(id: string) {
    setImages((prev) => prev.filter((item) => item.id !== id));
  }

  async function pollTask(taskId: string, itemId: string, prompt: string, r: string, existingMessages: ChatMessage[]) {
    const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));
    for (let i = 0; i < 120; i++) {
      await wait(5000);
      if (cancelledRef.current.has(taskId)) return;
      try {
        const detail = await api.getTask(taskId);
        if (detail.status === 'cancelled') {
          removeItem(itemId);
          return;
        }
        if (detail.status === 'completed' && detail.result?.images?.length) {
          const serverPath = detail.result.images[0];
          const artifactUrl = detail.artifacts?.[0]?.url || `/artifacts/files/${serverPath}`;
          const doneMsg: ChatMessage = { role: 'assistant', text: '生成完成', img: artifactUrl, prompt, ratio: r };
          const updated = [...existingMessages, doneMsg];
          updateItem(itemId, { src: artifactUrl, path: serverPath, loading: false, messages: updated });
          return;
        }
        if (detail.status === 'failed') {
          const errMsg: ChatMessage = { role: 'assistant', text: `生成失败: ${detail.error || ''}` };
          const updated = [...existingMessages, errMsg];
          updateItem(itemId, { loading: false, error: detail.error || '生成失败', messages: updated });
          return;
        }
      } catch { /* retry */ }
    }
    const timeoutMsg: ChatMessage = { role: 'assistant', text: '查询超时，请稍后查看' };
    const updated = [...existingMessages, timeoutMsg];
    updateItem(itemId, { loading: false, error: '查询超时，请稍后查看', messages: updated });
  }

  function getReferencePath(id: string): string | undefined {
    return findItem(id)?.path;
  }

  async function submitTask(cmd: string, prompt: string, size: string, itemId: string, refPath?: string, existingMessages?: ChatMessage[]) {
    let taskId: string;
    if (cmd === '/wst') {
      const res = await api.submitTask('wst', { prompt, size });
      taskId = res.task_id;
    } else if (cmd === '/tst') {
      if (!refPath) throw new Error('参考图不可用');
      const res = await api.submitTask('tst', { prompt, reference_images: [refPath], size });
      taskId = res.task_id;
    } else throw new Error(`未知命令: ${cmd}`);
    const submitMsg: ChatMessage = { role: 'assistant', text: `任务已提交 (${taskId.slice(0, 8)})` };
    const msgs = [...(existingMessages || []), submitMsg];
    updateItem(itemId, { taskId, messages: msgs });
    setMessages((prev) => [...prev, submitMsg]);
    pollTask(taskId, itemId, prompt, size, msgs);
  }

  async function resubmit(itemId: string) {
    const item = findItem(itemId);
    if (!item) return;
    const cmd = item.cmd || '/wst';
    const refPath = cmd === '/tst' ? item.path : undefined;
    const existingMsgs = item.messages || [];
    const retryMsg: ChatMessage = { role: 'user', text: `[重新生成] ${item.prompt}` };
    const msgs = [...existingMsgs, retryMsg];
    updateItem(itemId, { loading: true, error: undefined, src: '', interrupted: false, messages: msgs });
    try {
      await submitTask(cmd, item.prompt, item.ratio, itemId, refPath, msgs);
    } catch (err) {
      const reason = err instanceof Error ? err.message : '提交失败';
      updateItem(itemId, { loading: false, error: reason });
    }
  }

  async function downloadImage(src: string, prompt?: string) {
    try {
      const res = await fetch(src);
      const blob = await res.blob();
      const ext = blob.type === 'image/png' ? 'png' : blob.type === 'image/webp' ? 'webp' : 'jpg';
      const name = (prompt || 'image').slice(0, 30).replace(/[^a-zA-Z0-9\u4e00-\u9fa5]/g, '_') + '.' + ext;
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = name;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (e) {
      console.error('下载失败', e);
    }
  }

  async function handleStop(itemId: string) {
    const item = findItem(itemId);
    if (item?.taskId) {
      cancelledRef.current.add(item.taskId);
      try { await api.cancelTask(item.taskId); } catch { /* ignore */ }
    }
    updateItem(itemId, { loading: false, interrupted: true });
  }

  async function send() {
    const text = input.trim();
    if (!text || concurrentCount >= MAX_CONCURRENT) return;
    const full = cmdPrefix ? `${cmdPrefix} ${text}` : text;
    const initialMessages: ChatMessage[] = [{ role: 'user', text: full }];
    setMessages((prev) => [...prev, ...initialMessages]);
    const refPath = cmdPrefix === '/tst' && editingId ? getReferencePath(editingId) : undefined;
    if (cmdPrefix === '/tst' && !refPath) {
      setMessages((prev) => [...prev, { role: 'assistant', text: '拖入的图片暂不支持图生图编辑' }]);
      return;
    }
    const placeholder: CanvasImageItem = {
      id: genId(),
      src: '',
      prompt: text,
      ratio,
      cmd: cmdPrefix,
      loading: true,
      messages: initialMessages,
    };
    setImages((prev) => [...prev, placeholder]);
    const itemId = placeholder.id;
    setConcurrentCount((c) => c + 1);
    setInput('');
    const r = ratio;
    try {
      await submitTask(cmdPrefix, text, r, itemId, refPath, initialMessages);
    } catch (err) {
      const reason = err instanceof Error ? err.message : '提交失败';
      const errMsg: ChatMessage = { role: 'assistant', text: `提交失败: ${reason}` };
      const msgs = [...initialMessages, errMsg];
      updateItem(itemId, { loading: false, error: reason, messages: msgs });
      setMessages((prev) => [...prev, errMsg]);
    } finally {
      setConcurrentCount((c) => c - 1);
    }
  }

  const handleDragEnter = useCallback((e: React.DragEvent) => {
    e.preventDefault(); e.stopPropagation();
    dragCountRef.current++; setDragOver(true);
  }, []);
  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault(); e.stopPropagation();
    dragCountRef.current--; if (dragCountRef.current <= 0) { dragCountRef.current = 0; setDragOver(false); }
  }, []);
  const handleDragOver = useCallback((e: React.DragEvent) => { e.preventDefault(); e.stopPropagation(); }, []);
  const handleDrop = useCallback(async (e: React.DragEvent) => {
    e.preventDefault(); e.stopPropagation();
    dragCountRef.current = 0; setDragOver(false);
    const files = Array.from(e.dataTransfer.files).filter((f) => f.type.startsWith('image/'));
    if (files.length === 0) return;
    const dataUrls = await Promise.all(files.map(readImageFile));
    for (const url of dataUrls) {
      const item: CanvasImageItem = { id: genId(), src: url, prompt: '', ratio: '3:4' };
      setImages((prev) => [...prev, item]);
    }
  }, []);

  return (
    <div className="page">
      <div className="topbar">
        <button className="btn ghost sm" onClick={() => nav('/')}><ArrowLeft size={14} strokeWidth={1.6} /> 返回</button>
        <div className="brand"><span className="mark">{label}</span></div>
        <div className="spacer" />
        <ThemeSwitcher />
      </div>
      <div
        className={`canvas-blank${dragOver ? ' is-dragover' : ''}`}
        onDragEnter={handleDragEnter}
        onDragLeave={handleDragLeave}
        onDragOver={handleDragOver}
        onDrop={handleDrop}
      >
        {images.length === 0 ? (
          <button type="button" className="canvas-blank-add" onClick={() => openDrawerFor('/wst')} aria-label="新建对话">
            <Plus size={32} strokeWidth={1.8} />
          </button>
        ) : (
          images.map((item, i) => (
            <ResizableImageCard
              key={item.id}
              src={item.src}
              loading={item.loading}
              error={item.error}
              prompt={item.prompt}
              ratio={item.ratio}
              index={i}
              onRemove={() => setPendingDelete(item.id)}
              onEdit={() => openDrawerFor('/tst', item.prompt, item.id)}
              onStop={() => handleStop(item.id)}
              onRetry={() => resubmit(item.id)}
              onDownload={downloadImage}
              onDetail={() => openDetailDrawer(item.id)}
              taskId={item.taskId}
              interrupted={item.interrupted}
            />
          ))
        )}
        {dragOver && <div className="canvas-drag-hint">松开以上传图片</div>}
      </div>

      <ConfirmDialog
        open={pendingDelete !== null}
        title="删除图片？"
        message="确定删除这张图片？此操作不可撤销。"
        confirmLabel="删除"
        danger
        onConfirm={() => { if (pendingDelete !== null) { removeItem(pendingDelete); setPendingDelete(null); } }}
        onCancel={() => setPendingDelete(null)}
      />

      {drawerOpen && drawerMode === 'chat' && (
        <>
          <div className="drawer-backdrop" onClick={closeDrawer} />
          <aside className="drawer chat-drawer" role="dialog" aria-modal aria-label="AI 对话">
            <div className="head">
              <div className="titles">
                <h3 className="title">{cmdPrefix === '/tst' ? '图生图' : '文生图'}</h3>
                <div className="subtitle">
                  {cmdPrefix === '/tst' ? '以参考图为基础生成' : '描述你想要的图片'}
                  {concurrentCount > 0 && <span className="chat-concurrent-badge">{concurrentCount}/{MAX_CONCURRENT} 进行中</span>}
                </div>
              </div>
              <button className="btn sm icon-only ghost" onClick={closeDrawer} title="关闭 (Esc)" aria-label="关闭">
                <X size={14} strokeWidth={1.6} />
              </button>
            </div>
            <div className="body chat-body">
              {messages.length === 0 && (
                <div className="chat-hint dim-mono">输入描述开始创作</div>
              )}
              {messages.map((msg, i) => (
                <div key={i} className={`chat-msg role-${msg.role}`}>
                  <div className="chat-bubble">
                    {msg.text}
                    {msg.img && <img src={msg.img} alt="" className="chat-thumb" />}
                  </div>
                  {msg.role === 'assistant' && msg.prompt && (
                    <button
                      className="chat-copy-btn"
                      onClick={() => navigator.clipboard.writeText(
                        `提示词: ${msg.prompt}\n尺寸: ${msg.ratio || ''}`
                      )}
                      aria-label="复制图片信息"
                      title="复制图片信息"
                    >
                      <Copy size={12} strokeWidth={1.6} />
                    </button>
                  )}
                </div>
              ))}
              <div ref={bottomRef} />
            </div>
            <div className="chat-ratios">
              {RATIOS.map((r) => (
                <button key={r} type="button" className={`chat-ratio-pill${ratio === r ? ' active' : ''}`} onClick={() => setRatio(r)}>{r}</button>
              ))}
            </div>
            <div className="chat-input-row">
              <div className="chat-input-wrap">
                {cmdPrefix && <span className="chat-input-prefix">{cmdPrefix}</span>}
                <input
                  ref={inputRef}
                  className="chat-input"
                  type="text"
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter') send(); }}
                  placeholder={concurrentCount >= MAX_CONCURRENT ? `已达并发上限(${MAX_CONCURRENT})，请等待…` : '输入指令…'}
                  spellCheck={false}
                />
              </div>
              <button className="btn sm primary icon-only" onClick={send} disabled={concurrentCount >= MAX_CONCURRENT || !input.trim()} aria-label="发送">
                {concurrentCount > 0 ? <Loader2 size={14} strokeWidth={2} className="spin" /> : <SendHorizonal size={14} strokeWidth={1.6} />}
              </button>
            </div>
          </aside>
        </>
      )}

      {drawerOpen && drawerMode === 'detail' && (() => {
        const detailItem = findItem(detailImageId || '');
        if (!detailItem) return null;
        const detailMsgs = detailItem.messages || [];
        return (
          <>
            <div className="drawer-backdrop" onClick={closeDrawer} />
            <aside className="drawer chat-drawer" role="dialog" aria-modal aria-label="图片详情">
              <div className="head">
                <div className="titles">
                  <h3 className="title">图片详情</h3>
                  <div className="subtitle">{detailItem.prompt.slice(0, 60)}</div>
                </div>
                <button className="btn sm icon-only ghost" onClick={closeDrawer} title="关闭" aria-label="关闭">
                  <X size={14} strokeWidth={1.6} />
                </button>
              </div>
              <div className="body chat-body">
                {detailMsgs.length === 0 ? (
                  <div className="chat-hint dim-mono">暂无对话记录</div>
                ) : (
                  detailMsgs.map((msg, i) => (
                    <div key={i} className={`chat-msg role-${msg.role}`}>
                      <div className="chat-bubble">
                        {msg.text}
                        {msg.img && <img src={msg.img} alt="" className="chat-thumb" />}
                      </div>
                      {msg.role === 'assistant' && msg.prompt && (
                        <button
                          className="chat-copy-btn"
                          onClick={() => navigator.clipboard.writeText(
                            `提示词: ${msg.prompt}\n尺寸: ${msg.ratio || ''}`
                          )}
                          aria-label="复制图片信息"
                          title="复制图片信息"
                        >
                          <Copy size={12} strokeWidth={1.6} />
                        </button>
                      )}
                    </div>
                  ))
                )}
                <div ref={bottomRef} />
              </div>
              {detailItem.src && !detailItem.loading && !detailItem.error && (
                <div className="chat-detail-footer">
                  <button className="btn sm ghost" onClick={() => downloadImage(detailItem.src, detailItem.prompt)}>
                    <Download size={13} strokeWidth={1.6} /> 下载
                  </button>
                  <button className="btn sm ghost" onClick={() => { closeDrawer(); resubmit(detailItem.id); }}>
                    <RotateCcw size={13} strokeWidth={1.6} /> 重新生成
                  </button>
                </div>
              )}
            </aside>
          </>
        );
      })()}
    </div>
  );
}

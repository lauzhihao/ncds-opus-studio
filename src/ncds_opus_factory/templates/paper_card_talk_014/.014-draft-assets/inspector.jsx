/* inspector.jsx — 编辑模式下的右下角 overlay 检视面板
 *
 * 仅在 window.__editMode.isActive() 时渲染。
 * 选中 overlay 后允许微调：text / x / y / color / font size / rotation / at.match / motion。
 * 任何改动通过 __editMode.apply(patch) 既改 live DOM 也写 dirty buffer。
 *
 * 自动保存：每次 apply 后 300ms debounce → __editMode.save() POST 写回。
 * 没有手动 Save / Exit 按钮（用 E 退出编辑模式 / 撤回单个 overlay 改动除外）。
 *
 * 复用 tweaks-panel.jsx 暴露的全局组件：TweaksPanel / TweakSection / TweakSlider /
 * TweakText / TweakNumber / TweakColor / TweakButton / TweakSelect / TweakRadio / TweakRow。
 */
(function () {
  // 无 edit-server 时 edit-mode.js 已自我禁用，__editMode 不存在属于预期，静默退出。
  if (!window.__editServerOk) return;
  if (!window.__editMode) { console.error('inspector: __editMode missing'); return; }
  const EM = window.__editMode;

  // 本模块自己的"场景变更"信号：patchScene 直接动 EP 内存但不经 EM，
  // 所以得自己有个 pub-sub 让 Inspector 重渲，否则受控 input 的 value 会被绑回旧值。
  const _localSubs = new Set();
  const fireLocal = () => _localSubs.forEach((cb) => { try { cb(); } catch (e) { console.error(e); } });

  // 强制重渲：订阅 __editMode 变化 + 本模块场景信号，bump 计数让 React 重新跑
  function useEditState() {
    const [n, setN] = React.useState(0);
    React.useEffect(() => {
      const bump = () => setN((x) => x + 1);
      const offEM = EM.onChange(bump);
      _localSubs.add(bump);
      return () => { offEM(); _localSubs.delete(bump); };
    }, []);
    return n;
  }

  // 把 text 中所有 needle 子串包成 <mark>, 用来让 at.match 关键词在下方 beat 列表里发亮.
  function highlightMatches(text, needle) {
    if (!needle || !text || !text.includes(needle)) return text;
    const parts = text.split(needle);
    const out = [];
    parts.forEach((p, i) => {
      if (p) out.push(p);
      if (i < parts.length - 1) {
        out.push(
          React.createElement('mark', {
            key: 'm' + i,
            style: { background: 'rgba(255,214,0,.7)', color: 'inherit', padding: '0 1px', borderRadius: 2 },
          }, needle)
        );
      }
    });
    return out;
  }

  function Inspector() {
    const tick = useEditState();
    const active = EM.isActive();
    // TweaksPanel 默认 open=false，只在收到 __activate_edit_mode 时才打开。
    // 仓库里没人发这条消息，所以面板会一直空。这里跟着编辑模式状态自动 toggle。
    // 副作用：原 Tweaks 面板也会跟着打开（被 edit-mode CSS dim 到左下，不抢戏）。
    React.useEffect(() => {
      window.postMessage({ type: active ? '__activate_edit_mode' : '__deactivate_edit_mode' }, '*');
    }, [active]);
    if (!active) return null;

    const selected = EM.getSelected();
    const sceneIds = EM.getSceneIdsWithOverlays();
    const currentSid = EM.getCurrentSceneId();
    const dirtyCount = EM.getDirtyCount();

    // 当前场景的 overlay 列表（用来让用户在面板里点选）
    const ep = window.EPISODE;
    const sceneOverlays = (currentSid && ep.scenes[currentSid] && ep.scenes[currentSid].overlays) || [];
    const beatsForScene = currentSid ? EM.getBeatsForScene(currentSid) : [];

    return (
      <TweaksPanel title="Overlay Inspector" noDeckControls>
        <TweakSection label="Scene">
          <TweakSelect label="场景" value={currentSid || ''}
            options={sceneIds.map((id) => {
              const n = (ep.scenes[id].overlays || []).length;
              return { value: id, label: `${id}  (${n} 个 overlay)` };
            })}
            onChange={(v) => EM.setSceneId(v)} />
          <div className="twk-row twk-row-h">
            <TweakButton label="◀ 上一个" secondary onClick={() => {
              const i = sceneIds.indexOf(currentSid);
              EM.setSceneId(sceneIds[(i - 1 + sceneIds.length) % sceneIds.length]);
            }} />
            <TweakButton label="下一个 ▶" secondary onClick={() => {
              const i = sceneIds.indexOf(currentSid);
              EM.setSceneId(sceneIds[(i + 1) % sceneIds.length]);
            }} />
          </div>
        </TweakSection>

        {/* 没选 overlay 时才显示场景级（含提示词 / Ken Burns / 图片填充等图片相关）字段；
            一旦点中 overlay，就让 inspector 完全聚焦到 overlay 自身的设置上。 */}
        {currentSid && !selected && <SceneFields sceneId={currentSid} />}

        <TweakSection label="选择 Overlay">
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {sceneOverlays.map((o, i) => {
              const on = selected && selected.sceneId === currentSid && selected.index === i;
              const txt = (o.text || '').slice(0, 8) || '(空)';
              return (
                <button key={i} type="button"
                  style={{
                    appearance: 'none',
                    border: on ? '2px solid #0a84ff' : '1px solid rgba(0,0,0,.18)',
                    background: on ? 'rgba(10,132,255,.12)' : '#fff',
                    color: '#29261b',
                    borderRadius: 6,
                    padding: '4px 8px',
                    font: '11px ui-sans-serif,system-ui,sans-serif',
                    cursor: 'pointer',
                  }}
                  onClick={() => EM.select(currentSid, i)}>
                  #{i} {txt}
                </button>
              );
            })}
            <button type="button" title="新增 overlay 到当前 scene 中心"
              onClick={() => EM.addOverlay(currentSid)}
              style={{
                appearance: 'none',
                border: '1px dashed rgba(0,0,0,.3)',
                background: '#fff', color: '#29261b',
                borderRadius: 6,
                padding: '4px 10px',
                font: '13px ui-sans-serif,system-ui,sans-serif',
                cursor: 'pointer',
                lineHeight: 1,
              }}>+</button>
          </div>
          {sceneOverlays.length === 0 && (
            <div style={{ fontSize: 11, color: 'rgba(41,38,27,.55)', marginTop: 6 }}>当前场景没有 overlay，点末尾的 + 加一个</div>
          )}
        </TweakSection>

        {selected && (
          <OverlayFields selected={selected} beatsForScene={beatsForScene} />
        )}

      </TweaksPanel>
    );
  }

  // 中英对照：所有内置 style / motion 短码 → 中文释义。
  // 派生 dropdown 时统一用 _label(id) 输出 "中文 (id)" 让用户看得懂、又能对照 docs。
  // 空 id ('') / 'auto' / 'none' 等特殊值在调用处自己处理（不走这张表）。
  const ZH = {
    // overlay 样式预设（os-*）
    'os-tag-pill':     '标签胶囊',
    'os-stamp':        '红章压印',
    'os-marker':       '高亮记号笔',
    'os-handwrite':    '手写斜体',
    'os-typewriter':   '便利贴打字机',
    'os-callout':      '衬线大标注',
    'os-callout-red':  '衬线大标注（红）',
    'os-circle-mark':  '圆圈圈起',
    // overlay 入场（含 oa-* 老库 与 mo-ov-* 新库 的语义 enter 名）
    'fade':            '淡入',
    'fly-in':          '飞入',
    'zoom-in':         '放大入场',
    'zoom-out':        '缩小入场',
    'stamp':           '盖章砸入',
    'blur':            '模糊化',
    'zoom-pop':        '弹跳放大',
    'ink-bleed':       '墨水晕染',
    'handwrite':       '手写笔触',
    'slide-clip':      '滑入裁切',
    'iris':            '光圈展开',
    'bounce':          '弹跳',
    'drift-in':        '飘入',
    'spin-in':         '旋转入场',
    'drop-in':         '坠落',
    'unfold':          '展开',
    'letter-spread':   '字符散开',
    'elastic-pop':     '弹性弹出',
    'tilt-in':         '倾斜入场',
    'fold-down':       '折叠下降',
    'blur-pulse':      '模糊脉动',
    'rise-glow':       '升起发光',
    'shimmer-sweep':   '微光扫过',
    // fly-in 方向
    'right':           '从右',
    'left':            '从左',
    'top':             '从上',
    'bottom':          '从下',
    // scene 入场（mo-scene-*）
    'flip-h':          '水平翻转',
    'flip-v':          '垂直翻转',
    'glitch':          '故障干扰',
    'iris-in':         '光圈聚拢',
    'iris-out':        '光圈散开',
    'mask-grid':       '网格揭幕',
    'push-left':       '左推切场',
    'push-right':      '右推切场',
    'slide-down':      '滑入向下',
    'slide-left':      '滑入向左',
    'slide-right':     '滑入向右',
    'slide-up':        '滑入向上',
    'wipe-circle':     '圆形擦除',
    'wipe-down':       '向下擦除',
    'wipe-left':       '向左擦除',
    'wipe-right':      '向右擦除',
    'wipe-up':         '向上擦除',
    // Ken Burns（mo-img-*）
    'pan-l':           '缓推向左',
    'pan-r':           '缓推向右',
    'pan-u':           '缓推向上',
    'pan-d':           '缓推向下',
    'diag-br':         '对角向右下',
    'diag-tl':         '对角向左上',
    'parallax':        '视差',
    'wobble':          '微抖',
    'breathe':         '呼吸缩放',
    // 图片填充
    'contain':         '完整显示',
    'cover':           '填满裁切',
    'fill':            '拉伸填满',
  };
  function _label(id) { return ZH[id] ? `${ZH[id]} (${id})` : id; }

  // scene 入场动效池（motion.css 里的 mo-scene-*）；'' = 不挂动效
  // 注意：iris-in / iris-out / wipe-circle 都是 clip-path: circle() 全屏遮罩，
  // 某些浏览器渲染会导致整张图白屏；池子里直接剔除避免再被选中。
  const SCENE_ENTERS = [
    '', 'fade',
    'flip-h', 'flip-v', 'glitch', 'ink-bleed', 'mask-grid',
    'push-left', 'push-right',
    'slide-down', 'slide-left', 'slide-right', 'slide-up',
    'wipe-down', 'wipe-left', 'wipe-right', 'wipe-up',
    'zoom-in', 'zoom-out',
  ];
  // 图片 Ken Burns 池（motion.css 里的 mo-img-*）；'' = 按 hash 随机；'none' = 静止
  const IMG_KENS_IDS = ['zoom-in', 'zoom-out', 'pan-l', 'pan-r', 'pan-u', 'pan-d',
                        'diag-br', 'diag-tl', 'parallax', 'wobble', 'breathe'];
  const IMG_KENS_OPTS = [
    { value: '', label: '— 自动（按 hash 随机）—' },
    { value: 'none', label: '静止 (none)' },
    ...IMG_KENS_IDS.map((v) => ({ value: v, label: _label(v) })),
  ];
  const IMG_FITS = ['contain', 'cover', 'fill'];

  // 场景级 patch：deep-set EP.scenes[sid][field]（field 支持 'motion.enter' 之类的点路径），
  // 并把同样的 dot-path 排进 debounce 自动保存队列（complex 路径：'scenes.S1-003.motion.enter'）
  const _scenePending = {};
  let _sceneTimer = null;
  function patchScene(sceneId, field, value) {
    if (!sceneId) return;
    const ep = window.EPISODE;
    if (!ep.scenes[sceneId]) return;
    const parts = field.split('.');
    let cur = ep.scenes[sceneId];
    for (let i = 0; i < parts.length - 1; i++) {
      if (!cur[parts[i]] || typeof cur[parts[i]] !== 'object') cur[parts[i]] = {};
      cur = cur[parts[i]];
    }
    cur[parts[parts.length - 1]] = value;
    fireLocal(); // 让 SceneFields 重渲，否则受控 input 改不了
    if (!window.__editServerOk) return;
    _scenePending['scenes.' + sceneId + '.' + field] = value;
    if (_sceneTimer) clearTimeout(_sceneTimer);
    _sceneTimer = setTimeout(flushScenePatches, 300);
  }
  async function flushScenePatches() {
    _sceneTimer = null;
    if (Object.keys(_scenePending).length === 0) return;
    const patches = Object.assign({}, _scenePending);
    for (const k of Object.keys(_scenePending)) delete _scenePending[k];
    const ep = window.EPISODE;
    const slug = ep.__slug || (ep.meta && ep.meta.slug);
    try {
      const res = await fetch('./__save_episode', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ slug, patches }),
      });
      if (!res.ok) console.warn('[inspector] scene save failed', res.status);
    } catch (e) { console.warn('[inspector] scene save error', e); }
  }

  // 动效池：和 overlays.js / motion.css 保持一致；空 = 让引擎按 hash 自动挑
  // 注意：iris (mo-ov-iris) 是 clip-path 圆形遮罩，跟 scene 级 iris-in/wipe-circle
  // 一样有白屏风险，池子里剔除。
  const MOTION_ENTERS = [
    '', // auto
    'fade', 'fly-in', 'zoom-in', 'stamp', 'blur',
    'zoom-pop', 'ink-bleed', 'handwrite', 'slide-clip',
    'bounce', 'drift-in', 'spin-in', 'drop-in', 'unfold',
    'letter-spread', 'elastic-pop', 'tilt-in', 'fold-down',
    'blur-pulse', 'rise-glow', 'shimmer-sweep',
  ];
  const FLY_DIRS = ['right', 'left', 'top', 'bottom'];

  function SceneFields({ sceneId }) {
    const scene = (window.EPISODE.scenes || {})[sceneId] || {};
    const isChapter = scene.type === 'chapter';
    const motion = scene.motion || {};
    const enter = motion.enter || '';
    const dur = motion.duration != null ? motion.duration : 700;
    const imgKen = motion.image || '';
    const fit = scene.imageFit || 'contain';
    return (
      <TweakSection label={`场景设置 (${sceneId}${isChapter ? ' · 章节卡' : ''})`}>
        {isChapter ? (
          <TweakText label="副标题" value={scene.subtitle || ''}
            onChange={(v) => patchScene(sceneId, 'subtitle', v)} />
        ) : (
          <React.Fragment>
            <TweakRow label="提示词 (生图用)">
              <textarea rows={5}
                value={scene.prompt || ''}
                onChange={(e) => patchScene(sceneId, 'prompt', e.target.value)}
                style={{
                  width: '100%', boxSizing: 'border-box',
                  resize: 'vertical',
                  padding: '6px 8px',
                  border: '.5px solid rgba(0,0,0,.12)',
                  borderRadius: 7,
                  background: '#fff7d4',
                  color: '#29261b',
                  font: '11.5px/1.5 ui-sans-serif,system-ui,-apple-system,sans-serif',
                  outline: 'none',
                }} />
            </TweakRow>
            <TweakText label="标签 / 备注" value={scene.label || ''}
              onChange={(v) => patchScene(sceneId, 'label', v)} />
          </React.Fragment>
        )}
        <TweakSelect label="入场效果" value={enter}
          options={SCENE_ENTERS.map((v) => ({ value: v, label: v ? _label(v) : '— 无 —' }))}
          onChange={(v) => patchScene(sceneId, 'motion.enter', v || null)} />
        <TweakSlider label="持续时间 ms" value={dur} min={200} max={2000} step={50}
          onChange={(v) => patchScene(sceneId, 'motion.duration', Number(v))} />
        {!isChapter && (
          <React.Fragment>
            <TweakSelect label="Ken Burns 动效" value={imgKen}
              options={IMG_KENS_OPTS} onChange={(v) => patchScene(sceneId, 'motion.image', v || null)} />
            <TweakRadio label="图片填充" value={fit}
              options={IMG_FITS.map((f) => ({ value: f, label: _label(f) }))}
              onChange={(v) => patchScene(sceneId, 'imageFit', v)} />
          </React.Fragment>
        )}
      </TweakSection>
    );
  }

  function OverlayFields({ selected, beatsForScene }) {
    const m = selected.merged;
    const pos = m.pos || { x: m.xPct != null ? m.xPct : 50, y: m.yPct != null ? m.yPct : 50 };
    // style 三种形态归一：拿到 preset string + inline overrides
    const styleObj = (m.style && typeof m.style === 'object') ? m.style : null;
    const presetStr = typeof m.style === 'string' ? m.style : (styleObj && typeof styleObj.preset === 'string' ? styleObj.preset : '');
    const color = (styleObj && styleObj.color) || '';
    const fontFam = (styleObj && styleObj.font) || '';
    const fontSize = styleObj && styleObj.size != null ? styleObj.size : '';
    const rotation = styleObj && styleObj.rotation != null ? styleObj.rotation : 0;
    const atMatch = (m.at && m.at.match) || '';
    const motion = m.motion || {};
    const motionEnter = motion.enter || '';
    const motionFrom = motion.from || 'right';
    const motionDur = motion.duration != null ? motion.duration : 600;
    const motionDelay = motion.delay != null ? motion.delay : 0;

    const STYLE_PRESETS = (window.__overlays && window.__overlays.STYLES) || [];
    const FONTS = ((window.EPISODE && window.EPISODE.fonts) || []).map((f) => f.family).filter(Boolean);

    // 改 style 子字段时统一走 "保留 preset 字段 + 合并 inline" 模式。
    // 若原 style 是字符串 → 升格成 {preset, ...}；若是纯 inline 对象 → 直接 merge。
    function patchStyle(part) {
      let nextStyle;
      if (typeof m.style === 'string') {
        nextStyle = Object.assign({ preset: m.style }, part);
      } else if (styleObj) {
        nextStyle = Object.assign({}, styleObj, part);
      } else {
        nextStyle = Object.assign({}, part);
      }
      EM.apply({ style: nextStyle });
    }

    function patchAt(matchVal) {
      if (matchVal === '') EM.apply({ at: null });
      else EM.apply({ at: Object.assign({}, m.at || {}, { match: matchVal }) });
    }

    function patchMotion(part) {
      const next = Object.assign({}, motion, part);
      // enter 切到非 fly-in 时清掉 from，避免冗余字段
      if ('enter' in part && part.enter !== 'fly-in') delete next.from;
      EM.apply({ motion: next });
      // 改了入场动效就在面板里立刻预览一次, 让用户能看到效果
      if (selected && EM.previewMotion) {
        // apply 内部可能 renderSceneEdit, 等一帧再 preview, 确保新 el 就位
        requestAnimationFrame(() => EM.previewMotion(selected.sceneId, selected.index));
      }
    }

    return (
      <React.Fragment>
        <TweakSection label={`Overlay #${selected.index}`}>
          <TweakText label="文本" value={m.text || ''}
            onChange={(v) => EM.apply({ text: v })} />
          <TweakNumber label="x %" value={pos.x} min={0} max={100} step={0.5}
            onChange={(v) => EM.apply({ pos: { x: round1(v), y: pos.y } })} />
          <TweakNumber label="y %" value={pos.y} min={0} max={100} step={0.5}
            onChange={(v) => EM.apply({ pos: { x: pos.x, y: round1(v) } })} />
        </TweakSection>

        <TweakSection label="文字样式">
          <TweakSelect label="预设" value={presetStr}
            options={[{ value: '', label: '— 纯 inline / 按 hash 随机 —' }]
              .concat(STYLE_PRESETS.map((s) => ({ value: s, label: _label(s) })))}
            onChange={(v) => {
              if (v === '') {
                // 去掉 preset，保留 inline
                if (styleObj) {
                  const { preset, ...rest } = styleObj;
                  EM.apply({ style: Object.keys(rest).length ? rest : null });
                } else if (typeof m.style === 'string') {
                  EM.apply({ style: null });
                }
              } else if (styleObj) {
                EM.apply({ style: Object.assign({}, styleObj, { preset: v }) });
              } else {
                EM.apply({ style: v });
              }
            }} />
          <TweakColor label="颜色" value={color || '#000000'}
            onChange={(v) => patchStyle({ color: v })} />
          {FONTS.length > 0
            ? <FontPicker value={fontFam} options={FONTS}
                onChange={(v) => patchStyle({ font: v || null })} />
            : <TweakRow label="字体"><div style={{fontSize:11,opacity:.55}}>无可用字体</div></TweakRow>}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <SizeInput value={fontSize} placeholder="字号 px" min={8} max={400}
                onCommit={(v) => patchStyle({ size: v == null ? null : v })} />
            </div>
            <TextStyleToggles styleObj={styleObj} mStyle={m.style} onPatch={(s) => patchStyle(s)} />
          </div>
          <TweakSlider label="旋转 deg" value={rotation} min={-45} max={45} step={1}
            onChange={(v) => patchStyle({ rotation: Number(v) })} />
        </TweakSection>

        <TweakSection label="入场动效">
          <TweakSelect label="enter" value={motionEnter}
            options={MOTION_ENTERS.map((v) => ({ value: v, label: v ? _label(v) : '— 自动（按 hash 随机）—' }))}
            onChange={(v) => patchMotion({ enter: v || null })} />
          {motionEnter === 'fly-in' && (
            <TweakRadio label="from" value={motionFrom}
              options={FLY_DIRS.map((d) => ({ value: d, label: _label(d) }))}
              onChange={(v) => patchMotion({ from: v })} />
          )}
          <TweakSlider label="duration ms" value={motionDur} min={100} max={2000} step={50}
            onChange={(v) => patchMotion({ duration: Number(v) })} />
          <TweakSlider label="delay ms" value={motionDelay} min={0} max={3000} step={50}
            onChange={(v) => patchMotion({ delay: Number(v) })} />
          <div style={{ fontSize: 10, color: 'rgba(41,38,27,.5)', lineHeight: 1.5 }}>
            改 enter / from / duration / delay 会立刻在面板里重放一次入场动画做预览。
          </div>
        </TweakSection>

        <TweakSection label="入场时机">
          <TweakText label="at.match 关键词"
            value={atMatch}
            placeholder="（空 = scene 切入时直接播）"
            onChange={(v) => patchAt(v)} />
          {beatsForScene.length > 0 && (
            <div>
              <div style={{ fontSize: 10, color: 'rgba(41,38,27,.55)', marginBottom: 4 }}>
                点字幕直接填入 at.match，再到上面输入框裁短：
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                {beatsForScene.map((b, i) => {
                  const matched = atMatch && b.zh.includes(atMatch);
                  return (
                  <button key={i} type="button"
                    style={{
                      appearance: 'none',
                      border: matched ? '1px solid rgba(255,200,0,.7)' : '1px solid rgba(0,0,0,.15)',
                      background: matched ? 'rgba(255,235,59,.12)' : '#fff',
                      color: '#29261b', borderRadius: 4,
                      padding: '3px 6px', font: '11px ui-sans-serif,system-ui,sans-serif',
                      cursor: 'pointer', textAlign: 'left',
                    }}
                    title="把这条 beat 的整句写进 at.match"
                    onClick={(e) => {
                      // 不要弹 prompt：原生模态会改焦点 + 触发面板滚动到 chip 元素附近，
                      // 体感像跳回了 overlay 列表。直接写进去，让上面的文本框微调。
                      e.preventDefault();
                      patchAt(b.zh);
                    }}>
                    {highlightMatches(b.zh, atMatch)}
                  </button>
                  );
                })}
              </div>
            </div>
          )}
        </TweakSection>
      </React.Fragment>
    );
  }

  function round1(n) { return Math.round(Number(n) * 10) / 10; }

  // 4 个文字样式开关：B / I / U / S。
  // 切换时把 patch 设成显式值（'normal'/'none'）而非删字段——这样能盖住
  // 预设 class 里 hard-coded 的 font-weight / text-decoration；删字段不行。
  // 想"完全回到预设"用面板底部的「↺ 撤回此 overlay」。
  function TextStyleToggles({ styleObj, mStyle, onPatch }) {
    const cur = styleObj || (typeof mStyle === 'string' ? { preset: mStyle } : {});
    const isBold = Number(cur.weight) >= 600 || cur.weight === 'bold';
    const isItalic = cur.fontStyle === 'italic';
    const decStr = String(cur.textDecoration || '');
    const isUnder = decStr.indexOf('underline') >= 0;
    const isStrike = decStr.indexOf('line-through') >= 0;

    function toggleDec(kind, on) {
      const parts = new Set(decStr.split(/\s+/).filter((p) => p && p !== 'none'));
      if (on) parts.add(kind); else parts.delete(kind);
      onPatch({ textDecoration: parts.size ? [...parts].join(' ') : 'none' });
    }

    const btn = (label, on, onClick, style) => (
      <button type="button" onClick={onClick} aria-pressed={on}
        style={Object.assign({
          appearance: 'none',
          width: 28, height: 26, borderRadius: 5,
          border: on ? '1.5px solid #0a84ff' : '1px solid rgba(0,0,0,.2)',
          background: on ? 'rgba(10,132,255,.15)' : '#fff',
          color: '#29261b',
          font: '13px ui-sans-serif,system-ui,sans-serif',
          cursor: 'pointer',
        }, style)}>{label}</button>
    );
    return (
      <div style={{ display: 'flex', gap: 4, flexShrink: 0 }}>
        {btn('B', isBold,   () => onPatch({ weight: isBold ? 'normal' : 700 }),       { fontWeight: 800 })}
        {btn('I', isItalic, () => onPatch({ fontStyle: isItalic ? 'normal' : 'italic' }), { fontStyle: 'italic', fontFamily: 'serif' })}
        {btn('U', isUnder,  () => toggleDec('underline', !isUnder),                   { textDecoration: 'underline' })}
        {btn('S', isStrike, () => toggleDec('line-through', !isStrike),               { textDecoration: 'line-through' })}
      </div>
    );
  }

  // 字号输入框：普通 text input 只允许整数, 没有 number type 的上下 spinner.
  // 留空 commit → onCommit(null) 让上层把 size 置 null（回退预设默认）。
  function SizeInput({ value, min, max, placeholder, onCommit }) {
    const [draft, setDraft] = React.useState(value == null ? '' : String(value));
    React.useEffect(() => { setDraft(value == null ? '' : String(value)); }, [value]);
    function commit() {
      if (draft === '' || draft == null) { onCommit(null); return; }
      let n = parseInt(draft, 10);
      if (!isFinite(n)) { setDraft(value == null ? '' : String(value)); return; }
      if (min != null && n < min) n = min;
      if (max != null && n > max) n = max;
      setDraft(String(n));
      onCommit(n);
    }
    return (
      <input className="twk-field" type="text" inputMode="numeric"
        value={draft} placeholder={placeholder || ''} title="字号 px (留空 = 走预设)"
        style={{ width: '100%', boxSizing: 'border-box', textAlign: 'right' }}
        onChange={(e) => setDraft(e.target.value.replace(/\D/g, ''))}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === 'Enter') { e.preventDefault(); commit(); e.target.blur(); }
          else if (e.key === 'Escape') { setDraft(value == null ? '' : String(value)); e.target.blur(); }
        }} />
    );
  }

  // 字体选择器: native <option> 不支持 inline font-family,
  // 改成 chip grid, 每 chip 用 fontFamily 渲染示例字 "永和九年" + 小标 family 名.
  const SAMPLE_TEXT = '能成大事';
  function FontPicker({ value, options, onChange }) {
    return (
      <TweakRow label="字体">
        <div style={{
          display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 4,
          maxHeight: 168, overflowY: 'auto',  /* 6 个 chip (3 行 × 2 列) 的高度 */
          background: 'rgba(255,255,255,.4)', borderRadius: 6,
          padding: 4, border: '.5px solid rgba(0,0,0,.08)',
        }}>
          <FontChip family={null} selected={!value} onClick={() => onChange('')} />
          {options.map((f) => (
            <FontChip key={f} family={f} selected={value === f} onClick={() => onChange(f)} />
          ))}
        </div>
      </TweakRow>
    );
  }
  function FontChip({ family, selected, onClick }) {
    const label = family || '— 默认 —';
    return (
      <button type="button" onClick={onClick}
        style={{
          appearance: 'none',
          border: selected ? '2px solid #0a84ff' : '1px solid rgba(0,0,0,.12)',
          background: selected ? 'rgba(10,132,255,.08)' : '#fff',
          color: '#29261b',
          borderRadius: 5,
          padding: '5px 7px',
          cursor: 'pointer',
          textAlign: 'left',
          minWidth: 0,
          lineHeight: 1.1,
        }}>
        <span className="em-font-chip-label"
          style={{ display: 'block', color: 'rgba(41,38,27,.55)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', width: '100%' }}>
          {label}
        </span>
        <span className="em-font-chip-sample"
          style={{
            display: 'block',
            fontFamily: family ? `"${family}", "Noto Serif SC", serif` : 'inherit',
            whiteSpace: 'nowrap',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            width: '100%',
          }}>
          {SAMPLE_TEXT}
        </span>
      </button>
    );
  }

  const mount = document.createElement('div');
  mount.id = 'inspector-mount';
  document.body.appendChild(mount);
  ReactDOM.createRoot(mount).render(<Inspector />);
})();

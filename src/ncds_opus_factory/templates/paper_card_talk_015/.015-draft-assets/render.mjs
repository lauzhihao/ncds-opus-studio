/* 离线渲染：把 012 跑成 1920×1080 30fps MP4，音频从 audio/*.mp3 合成接进去。
 *
 * 流程：
 *   1. 起一个 python http.server 让 chrome 能拿到完整页面
 *   2. puppeteer-core 拉 headless chrome，开页面、等播放器就绪、等所有音频 metadata 加载
 *   3. PuppeteerScreenRecorder 走 CDP Page.startScreencast 录视频（无音频）
 *   4. 调 __player.startRecordingPlayback()（跳过 3 秒倒数，直接进 recording 模式）
 *   5. 监听 body.classList 上 'ending' 的出现 → 消失，知道片子放完
 *   6. ffmpeg concat audio/*.mp3 + 句间 80ms 静音 + 尾部 1.5s 静音
 *   7. ffmpeg 把视频和音频合到最终 mp4
 *
 * 用法（仓库根目录）：
 *   node .015-draft-assets/render.mjs
 *
 * 输出：.015-draft-assets/output/015-draft.mp4
 *
 * 需要：
 *   - /usr/bin/google-chrome
 *   - /usr/bin/ffmpeg
 *   - npm i puppeteer-core puppeteer-screen-recorder（已在 /tmp/node_modules）
 */

import puppeteer from 'puppeteer-core';
import { PuppeteerScreenRecorder } from 'puppeteer-screen-recorder';
import { spawn } from 'node:child_process';
import { promises as fs } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(HERE, '..');
const AUDIO_DIR = path.join(HERE, 'audio');
const OUTPUT_DIR = path.join(HERE, 'output');
const OUTPUT_MP4 = path.join(OUTPUT_DIR, '015-draft.mp4');
const TMP_VIDEO = '/tmp/012-render-silent.mp4';
const TMP_AUDIO = '/tmp/012-render-audio.mp3';
const HTTP_PORT = 8765;
const URL_012 = `http://127.0.0.1:${HTTP_PORT}/015-draft.html`;
const FPS = 30;
const INTRO_MS = 300;   // recorder 起来到 startRecordingPlayback 之间的空白纸面段
const GAP_MS = 80;      // beat 之间的"喘息"间隔，必须跟 player.js 里的 setTimeout 一致
const ENDING_MS = 1500; // body.ending 持续时间，跟 player.js 一致

const log = (...a) => console.log('[render]', ...a);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function runFfmpeg(args) {
  return new Promise((resolve, reject) => {
    const p = spawn('ffmpeg', ['-loglevel', 'error', ...args]);
    let stderr = '';
    p.stderr.on('data', (d) => (stderr += d.toString()));
    p.on('close', (code) => {
      if (code === 0) resolve();
      else reject(new Error(`ffmpeg exit ${code}: ${stderr.slice(-800)}`));
    });
  });
}

async function makeSilence(seconds, outPath) {
  await runFfmpeg([
    '-y',
    '-f', 'lavfi',
    '-i', `anullsrc=r=24000:cl=mono`,
    '-t', String(seconds),
    '-acodec', 'libmp3lame',
    '-q:a', '9',
    outPath,
  ]);
}

async function buildAudioTrack() {
  // 新版按 scene 整段 TTS：episode.json 的 beats[].audioFile 是 scene mp3 路径，
  // 按 scene 出现顺序去重收集。scene 内 beat 不再有 80ms gap（音频天然连续），
  // GAP_MS 只用在 scene 之间（跟 player.js 一致）。
  const ep = JSON.parse(await fs.readFile(path.join(HERE, 'episode.json'), 'utf-8'));
  const seen = new Set();
  const files = [];
  for (const b of ep.beats || []) {
    if (b.audioFile && !seen.has(b.audioFile)) {
      seen.add(b.audioFile);
      files.push(b.audioFile);   // 如 "audio/scene-S1-01.mp3"
    }
  }
  if (files.length === 0) {
    throw new Error('no audioFile in episode.json beats[] — run tts_gen.py first');
  }
  // 新顺序下 recorder.start 在 startRecordingPlayback 之后，视频开头直接
  // 是第一字幕，没 leading 空白 → audio 也不再需要 intro silence。
  log(`audio: ${files.length} scene mp3s + ${GAP_MS}ms gaps + ${ENDING_MS}ms tail silence`);

  const silenceGap = '/tmp/012-silence-gap.mp3';
  const silenceTail = '/tmp/012-silence-tail.mp3';
  await makeSilence(GAP_MS / 1000, silenceGap);
  await makeSilence(ENDING_MS / 1000, silenceTail);

  const concatList = '/tmp/012-concat.txt';
  const lines = [];
  for (let i = 0; i < files.length; i++) {
    lines.push(`file '${path.join(HERE, files[i])}'`);
    if (i < files.length - 1) lines.push(`file '${silenceGap}'`);
  }
  lines.push(`file '${silenceTail}'`);
  await fs.writeFile(concatList, lines.join('\n') + '\n');

  // 重编码而不是 -c copy，避免 mp3 sample rate/channel 不一致拼接出问题
  await runFfmpeg([
    '-y',
    '-f', 'concat',
    '-safe', '0',
    '-i', concatList,
    '-acodec', 'libmp3lame',
    '-b:a', '128k',
    '-ar', '44100',
    '-ac', '1',
    TMP_AUDIO,
  ]);
  return TMP_AUDIO;
}

async function main() {
  await fs.mkdir(OUTPUT_DIR, { recursive: true });

  log('starting http.server on :' + HTTP_PORT);
  const server = spawn('python3', ['-m', 'http.server', String(HTTP_PORT)], {
    cwd: REPO_ROOT,
    stdio: 'ignore',
  });
  process.on('exit', () => server.kill());
  await sleep(800);

  log('launching headless chrome');
  const browser = await puppeteer.launch({
    executablePath: '/usr/bin/google-chrome',
    headless: 'new',
    protocolTimeout: 15 * 60 * 1000, // 整片 ~6:15，默认 180s 不够 waitForFunction 蹲到尾
    args: [
      '--no-sandbox',
      '--mute-audio',
      '--autoplay-policy=no-user-gesture-required',
      '--window-size=1920,1080',
      '--disable-features=site-per-process',
    ],
    defaultViewport: { width: 1920, height: 1080 },
  });

  try {
    const page = await browser.newPage();
    log('loading page');
    await page.goto(URL_012, { waitUntil: 'networkidle0', timeout: 30000 });
    // 等所有 audio 元素 readyState >= 1（metadata loaded），player 才能算 Ken Burns 时长
    await page.waitForFunction(
      () =>
        window.__player &&
        Array.isArray(window.__player.beats) &&
        window.__player.beats.length > 0,
      { timeout: 15000 }
    );
    await sleep(1200);

    // 录前先把 UI 抹掉（控件 / Tweaks 面板 / image-slot ring）。
    log('hiding UI');
    await page.evaluate(() => {
      document.body.classList.add('recording');
      document.getElementById('capZh').textContent = '';
      document.getElementById('capEn').textContent = '';
    });

    // 关键顺序：**先** startRecordingPlayback，**再** recorder.start。
    // startRecordingPlayback 内部第一次 showBeat(0) 同步执行会阻塞主线程
    // ~6 秒（首次 image-slot mount / overlay renderInto / fitBand 重 layout
    // 全堆在一起）。await page.evaluate 会等这段同步部分跑完；返回时第一字幕
    // 已显示、scriptedNext 的 setTimeout 也已入队、wallclock 计时已开始。
    // 这时再 recorder.start，录制器开机就直接拍到第一字幕的画面，
    // 而不是先录到 6 秒空白静态帧。
    log('triggering scripted playback (sync setup, may block ~5s on first showBeat)');
    await page.evaluate(() => window.__player.startRecordingPlayback({ scripted: true }));

    log('starting screen recorder (30fps, 1920×1080)');
    const recorder = new PuppeteerScreenRecorder(page, {
      fps: FPS,
      videoFrame: { width: 1920, height: 1080 },
      videoCrf: 23,
      videoCodec: 'libx264',
      videoPreset: 'fast',
      videoBitrate: 4000,
      aspectRatio: '16:9',
    });
    await recorder.start(TMP_VIDEO);

    log('waiting for ending fade to begin (full playback)');
    await page.waitForFunction(
      () => document.body.classList.contains('ending'),
      { timeout: 30 * 60 * 1000, polling: 200 }
    );
    log('ending fade started');
    await page.waitForFunction(
      () => !document.body.classList.contains('ending'),
      { timeout: 10 * 1000, polling: 100 }
    );
    // 再多录 0.2s 把淡出尾巴吃干净
    await sleep(200);

    log('stopping recorder');
    await recorder.stop();
  } finally {
    await browser.close();
    server.kill();
  }

  log('building audio track');
  await buildAudioTrack();

  log('muxing audio + video → ' + OUTPUT_MP4);
  await runFfmpeg([
    '-y',
    '-i', TMP_VIDEO,
    '-i', TMP_AUDIO,
    '-c:v', 'copy',
    '-c:a', 'aac',
    '-b:a', '160k',
    '-shortest',
    '-movflags', '+faststart',
    OUTPUT_MP4,
  ]);

  const stat = await fs.stat(OUTPUT_MP4);
  log(`done. ${(stat.size / 1024 / 1024).toFixed(1)} MB → ${OUTPUT_MP4}`);
}

await main();

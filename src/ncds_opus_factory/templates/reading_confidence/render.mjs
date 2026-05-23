/* 离线渲染：把 010 跑成 1920×1080 30fps MP4，音频从 audio/*.mp3 合成接进去。
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
 *   node .010-reading-confidence-assets/render.mjs
 *
 * 输出：.010-reading-confidence-assets/output/010-reading-confidence.mp4
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
const OUTPUT_MP4 = path.join(OUTPUT_DIR, '010-reading-confidence.mp4');
const TMP_VIDEO = '/tmp/010-render-silent.mp4';
const TMP_AUDIO = '/tmp/010-render-audio.mp3';
const HTTP_PORT = 8765;
const URL_010 = `http://127.0.0.1:${HTTP_PORT}/010-reading-confidence.html`;
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
  const files = (await fs.readdir(AUDIO_DIR))
    .filter((f) => /^\d+\.mp3$/.test(f))
    .sort();
  if (files.length === 0) throw new Error(`no audio/*.mp3 found in ${AUDIO_DIR}`);
  log(`audio: ${INTRO_MS}ms intro + ${files.length} mp3s + ${GAP_MS}ms gaps + ${ENDING_MS}ms tail silence`);

  const silenceIntro = '/tmp/010-silence-intro.mp3';
  const silenceGap = '/tmp/010-silence-gap.mp3';
  const silenceTail = '/tmp/010-silence-tail.mp3';
  await makeSilence(INTRO_MS / 1000, silenceIntro);
  await makeSilence(GAP_MS / 1000, silenceGap);
  await makeSilence(ENDING_MS / 1000, silenceTail);

  const concatList = '/tmp/010-concat.txt';
  const lines = [`file '${silenceIntro}'`];
  for (let i = 0; i < files.length; i++) {
    lines.push(`file '${path.join(AUDIO_DIR, files[i])}'`);
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
    await page.goto(URL_010, { waitUntil: 'networkidle0', timeout: 30000 });
    // 等所有 audio 元素 readyState >= 1（metadata loaded），player 才能算 Ken Burns 时长
    await page.waitForFunction(
      () =>
        window.__player &&
        Array.isArray(window.__player.beats) &&
        window.__player.beats.length > 0,
      { timeout: 15000 }
    );
    await sleep(1200);

    // 录前先把 UI 抹掉（控件 / Tweaks 面板 / image-slot ring），
    // 不然 recorder 起来的前几帧会拍到左下角的"导演台"。
    log('hiding UI (pre-recording state)');
    await page.evaluate(() => {
      document.body.classList.add('recording');
      document.getElementById('capZh').textContent = '';
      document.getElementById('capEn').textContent = '';
    });

    log('starting screen recorder (30fps, 1920×1080)');
    // viewport 跟 videoFrame 都是 1920×1080，不需要 autopad；带 autopad 时 ffmpeg pad
    // 滤镜会拒绝 3 位 hex 颜色，整段录制会静默失败。
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

    // 小段空白纸面 intro：让 CDP 通道稳一稳，给观众一拍准备时间。
    // 音轨那侧也会插同样长度的 leading silence。
    await sleep(INTRO_MS);

    log('triggering scripted playback (audio-duration-driven, no audio.onended drift)');
    await page.evaluate(() => window.__player.startRecordingPlayback({ scripted: true }));

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

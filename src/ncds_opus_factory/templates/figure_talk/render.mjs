/* figure_talk 录屏器:headless Chrome 录剪影成片 + ffmpeg 合配音。
 * 照搬 stickman/render_stick.mjs 的录屏逻辑(只改输出名 / 端口 / 临时文件名),
 * 等 window.__player.startRecordingPlayback + 监听 body.ending。
 * 跑:  (cd state/figure_jobs/<id> && ln -sf /tmp/node_modules node_modules && node render.mjs)
 */
import puppeteer from 'puppeteer-core';
import { PuppeteerScreenRecorder } from 'puppeteer-screen-recorder';
import { spawn } from 'node:child_process';
import { promises as fs } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const AUDIO_DIR = path.join(HERE, 'audio');
const OUTPUT_DIR = path.join(HERE, 'output');
const OUTPUT_MP4 = path.join(OUTPUT_DIR, 'figure_talk.mp4');
const TMP_VIDEO = '/tmp/figtalk-silent.mp4';
const TMP_AUDIO = '/tmp/figtalk-audio.mp3';
const HTTP_PORT = 8771;
const URL = `http://127.0.0.1:${HTTP_PORT}/index.html`;
const FPS = 30, INTRO_MS = 300, GAP_MS = 80, ENDING_MS = 1500;
const log = (...a) => console.log('[render]', ...a);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function runFfmpeg(args) {
  return new Promise((res, rej) => {
    const p = spawn('ffmpeg', ['-loglevel', 'error', ...args]);
    let e = ''; p.stderr.on('data', (d) => (e += d));
    p.on('close', (c) => (c === 0 ? res() : rej(new Error('ffmpeg ' + c + ': ' + e.slice(-600)))));
  });
}
async function makeSilence(sec, out) {
  await runFfmpeg(['-y', '-f', 'lavfi', '-i', 'anullsrc=r=24000:cl=mono', '-t', String(sec), '-acodec', 'libmp3lame', '-q:a', '9', out]);
}
async function buildAudioTrack(expectedCount) {
  // 数值序而非字典序(否则 1.mp3 2.mp3 10.mp3 会乱序);零填充命名也一并稳住。
  const files = (await fs.readdir(AUDIO_DIR)).filter((f) => /^\d+\.mp3$/.test(f))
    .sort((a, b) => parseInt(a, 10) - parseInt(b, 10));
  if (!files.length) throw new Error('no audio mp3 in ' + AUDIO_DIR);
  // 画面按 beats 出、音轨按目录里的 mp3 拼;数量不一致 = 声画错位的废片,提前报错而不是默默产出。
  if (expectedCount != null && files.length !== expectedCount) {
    throw new Error('audio/beats 数量不一致: ' + files.length + ' 个 mp3 vs ' + expectedCount + ' 个 beats(声画会错位)');
  }
  const si = '/tmp/figtalk-si.mp3', sg = '/tmp/figtalk-sg.mp3', st = '/tmp/figtalk-st.mp3';
  await makeSilence(INTRO_MS / 1000, si); await makeSilence(GAP_MS / 1000, sg); await makeSilence(ENDING_MS / 1000, st);
  const list = '/tmp/figtalk-concat.txt';
  const lines = [`file '${si}'`];
  files.forEach((f, i) => { lines.push(`file '${path.join(AUDIO_DIR, f)}'`); if (i < files.length - 1) lines.push(`file '${sg}'`); });
  lines.push(`file '${st}'`);
  await fs.writeFile(list, lines.join('\n') + '\n');
  await runFfmpeg(['-y', '-f', 'concat', '-safe', '0', '-i', list, '-acodec', 'libmp3lame', '-b:a', '128k', '-ar', '44100', '-ac', '1', TMP_AUDIO]);
  return TMP_AUDIO;
}
async function main() {
  await fs.mkdir(OUTPUT_DIR, { recursive: true });
  log('http.server :' + HTTP_PORT);
  const server = spawn('python3', ['-m', 'http.server', String(HTTP_PORT)], { cwd: HERE, stdio: 'ignore' });
  process.on('exit', () => server.kill());
  await sleep(800);
  log('launch chrome');
  const browser = await puppeteer.launch({
    executablePath: '/usr/bin/google-chrome', headless: 'new', protocolTimeout: 10 * 60 * 1000,
    args: ['--no-sandbox', '--mute-audio', '--autoplay-policy=no-user-gesture-required', '--window-size=1920,1080'],
    defaultViewport: { width: 1920, height: 1080 },
  });
  let beatsCount = null;
  try {
    const page = await browser.newPage();
    // 捕获页面 JS 错误 / console error —— 否则页面脚本炸了也会「静默录出空片」。
    page.on('pageerror', (e) => log('PAGE ERROR:', e.message));
    page.on('console', (m) => { if (m.type() === 'error') log('PAGE CONSOLE ERROR:', m.text()); });
    log('load page'); await page.goto(URL, { waitUntil: 'networkidle0', timeout: 30000 });
    await page.waitForFunction(() => window.__player && typeof window.__player.startRecordingPlayback === 'function', { timeout: 15000 });
    await page.evaluate(() => document.fonts && document.fonts.ready); // 等字体就绪,别录到 fallback 字形
    beatsCount = await page.evaluate(() => (window.BEATS || []).length);
    log('beats =', beatsCount);
    await sleep(1200);
    log('start recorder');
    const rec = new PuppeteerScreenRecorder(page, { fps: FPS, videoFrame: { width: 1920, height: 1080 }, videoCrf: 23, videoCodec: 'libx264', videoPreset: 'fast', videoBitrate: 4000, aspectRatio: '16:9' });
    await rec.start(TMP_VIDEO);
    await sleep(INTRO_MS);
    log('trigger playback'); await page.evaluate(() => window.__player.startRecordingPlayback());
    await page.waitForFunction(() => document.body.classList.contains('ending'), { timeout: 10 * 60 * 1000, polling: 200 });
    await page.waitForFunction(() => !document.body.classList.contains('ending'), { timeout: 10000, polling: 100 });
    await sleep(200);
    log('stop recorder'); await rec.stop();
  } finally { await browser.close(); server.kill(); }
  log('build audio'); await buildAudioTrack(beatsCount);
  log('mux → ' + OUTPUT_MP4);
  await runFfmpeg(['-y', '-i', TMP_VIDEO, '-i', TMP_AUDIO, '-c:v', 'copy', '-c:a', 'aac', '-b:a', '160k', '-shortest', '-movflags', '+faststart', OUTPUT_MP4]);
  const s = await fs.stat(OUTPUT_MP4);
  log(`done ${(s.size / 1048576).toFixed(1)}MB → ${OUTPUT_MP4}`);
}
await main();

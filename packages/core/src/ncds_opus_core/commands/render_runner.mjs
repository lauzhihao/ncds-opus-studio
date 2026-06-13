/* commands/render_runner.mjs —— 通用离线录屏 runner（headless Chrome + ffmpeg）。
 *
 * 与 templates/paper_card_talk/render.mjs 的区别：
 *   - 不启 python http.server；HTML_URL 由调用方提供（必须已可访问）
 *   - 路径全部从 process.env 读，无硬编码
 *   - 进度通过 console.log("[progress] ...") 上送给 Python 端 commands/render.py
 *
 * 必填 env：
 *   HTML_URL          完整可访问的 URL，例如 http://127.0.0.1:8765/011.html
 *   AUDIO_DIR         本地音频目录（含 NNNN.mp3）
 *   OUTPUT_PATH       最终 MP4 输出绝对路径
 *
 * 可选 env：
 *   CHROME_PATH       默认 /usr/bin/google-chrome
 *   FFMPEG_PATH       默认 ffmpeg（PATH 里能找到）
 *   FPS               默认 30
 *   WIDTH/HEIGHT      默认 1920/1080
 *   INTRO_MS          默认 300
 *   GAP_MS            默认 80
 *   ENDING_MS         默认 1500
 *   AUDIO_BITRATE     默认 160k（最终 mux 时的 AAC 比特率）
 *   TMP_DIR           中间文件目录，默认 /tmp
 *
 * NODE_PATH 必须能解析到 puppeteer-core + puppeteer-screen-recorder。
 */

import puppeteer from 'puppeteer-core';
import { PuppeteerScreenRecorder } from 'puppeteer-screen-recorder';
import { spawn } from 'node:child_process';
import { promises as fs } from 'node:fs';
import path from 'node:path';

const HTML_URL = mustEnv('HTML_URL');
const AUDIO_DIR = mustEnv('AUDIO_DIR');
const OUTPUT_PATH = mustEnv('OUTPUT_PATH');

const CHROME_PATH = process.env.CHROME_PATH || '/usr/bin/google-chrome';
const FFMPEG_PATH = process.env.FFMPEG_PATH || 'ffmpeg';
const FPS = parseInt(process.env.FPS || '30', 10);
const WIDTH = parseInt(process.env.WIDTH || '1920', 10);
const HEIGHT = parseInt(process.env.HEIGHT || '1080', 10);
const INTRO_MS = parseInt(process.env.INTRO_MS || '300', 10);
const GAP_MS = parseInt(process.env.GAP_MS || '80', 10);
const ENDING_MS = parseInt(process.env.ENDING_MS || '1500', 10);
const AUDIO_BITRATE = process.env.AUDIO_BITRATE || '160k';
const TMP_DIR = process.env.TMP_DIR || '/tmp';

const TMP_VIDEO = path.join(TMP_DIR, `render-silent-${process.pid}.mp4`);
const TMP_AUDIO = path.join(TMP_DIR, `render-audio-${process.pid}.mp3`);

function mustEnv(name) {
  const v = process.env[name];
  if (!v) throw new Error(`required env var not set: ${name}`);
  return v;
}

function progress(text) {
  console.log(`[progress] ${text}`);
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function runFfmpeg(args) {
  return new Promise((resolve, reject) => {
    const p = spawn(FFMPEG_PATH, ['-loglevel', 'error', ...args]);
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
    '-i', 'anullsrc=r=24000:cl=mono',
    '-t', String(seconds),
    '-acodec', 'libmp3lame',
    '-q:a', '9',
    outPath,
  ]);
}

async function buildAudioTrack() {
  const all = await fs.readdir(AUDIO_DIR);
  const files = all
    .filter((f) => /^\d+\.mp3$/.test(f))
    .sort();
  if (files.length === 0) throw new Error(`no audio/*.mp3 found in ${AUDIO_DIR}`);
  progress(`audio: ${INTRO_MS}ms intro + ${files.length} mp3 + ${GAP_MS}ms gaps + ${ENDING_MS}ms tail`);

  const silenceIntro = path.join(TMP_DIR, `silence-intro-${process.pid}.mp3`);
  const silenceGap = path.join(TMP_DIR, `silence-gap-${process.pid}.mp3`);
  const silenceTail = path.join(TMP_DIR, `silence-tail-${process.pid}.mp3`);
  await makeSilence(INTRO_MS / 1000, silenceIntro);
  await makeSilence(GAP_MS / 1000, silenceGap);
  await makeSilence(ENDING_MS / 1000, silenceTail);

  const concatList = path.join(TMP_DIR, `concat-${process.pid}.txt`);
  const lines = [`file '${silenceIntro}'`];
  for (let i = 0; i < files.length; i++) {
    lines.push(`file '${path.join(AUDIO_DIR, files[i])}'`);
    if (i < files.length - 1) lines.push(`file '${silenceGap}'`);
  }
  lines.push(`file '${silenceTail}'`);
  await fs.writeFile(concatList, lines.join('\n') + '\n');

  // 重编码而非 -c copy：不同 mp3 sample rate/channel 拼接会触发警告/坏帧
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
}

async function main() {
  await fs.mkdir(path.dirname(OUTPUT_PATH), { recursive: true });

  progress(`launching headless chrome (${WIDTH}x${HEIGHT}@${FPS}fps)`);
  const browser = await puppeteer.launch({
    executablePath: CHROME_PATH,
    headless: 'new',
    protocolTimeout: 15 * 60 * 1000,
    args: [
      '--no-sandbox',
      '--mute-audio',
      '--autoplay-policy=no-user-gesture-required',
      `--window-size=${WIDTH},${HEIGHT}`,
      '--disable-features=site-per-process',
    ],
    defaultViewport: { width: WIDTH, height: HEIGHT },
  });

  try {
    const page = await browser.newPage();
    progress(`loading ${HTML_URL}`);
    await page.goto(HTML_URL, { waitUntil: 'networkidle0', timeout: 30000 });

    progress('waiting for window.__player ready');
    await page.waitForFunction(
      () =>
        window.__player &&
        Array.isArray(window.__player.beats) &&
        window.__player.beats.length > 0,
      { timeout: 15000 }
    );
    await sleep(1200);

    progress('hiding UI (recording state)');
    await page.evaluate(() => {
      document.body.classList.add('recording');
      const z = document.getElementById('capZh');
      const e = document.getElementById('capEn');
      if (z) z.textContent = '';
      if (e) e.textContent = '';
    });

    progress(`screen recorder start → ${TMP_VIDEO}`);
    const recorder = new PuppeteerScreenRecorder(page, {
      fps: FPS,
      videoFrame: { width: WIDTH, height: HEIGHT },
      videoCrf: 23,
      videoCodec: 'libx264',
      videoPreset: 'fast',
      videoBitrate: 4000,
      aspectRatio: '16:9',
    });
    await recorder.start(TMP_VIDEO);

    await sleep(INTRO_MS);

    progress('triggering scripted playback');
    await page.evaluate(() => window.__player.startRecordingPlayback({ scripted: true }));

    progress('waiting for ending fade...');
    await page.waitForFunction(
      () => document.body.classList.contains('ending'),
      { timeout: 30 * 60 * 1000, polling: 200 }
    );
    progress('ending fade started');
    await page.waitForFunction(
      () => !document.body.classList.contains('ending'),
      { timeout: 10 * 1000, polling: 100 }
    );
    await sleep(200);

    progress('stopping recorder');
    await recorder.stop();
  } finally {
    await browser.close();
  }

  progress('building audio track');
  await buildAudioTrack();

  progress(`muxing → ${OUTPUT_PATH}`);
  await runFfmpeg([
    '-y',
    '-i', TMP_VIDEO,
    '-i', TMP_AUDIO,
    '-c:v', 'copy',
    '-c:a', 'aac',
    '-b:a', AUDIO_BITRATE,
    '-shortest',
    '-movflags', '+faststart',
    OUTPUT_PATH,
  ]);

  const stat = await fs.stat(OUTPUT_PATH);
  // 结果以 JSON 形式吐到 stdout 最后一行，Python 端解析
  console.log(JSON.stringify({
    output_path: OUTPUT_PATH,
    video_size_bytes: stat.size,
    tmp_video: TMP_VIDEO,
    tmp_audio: TMP_AUDIO,
  }));
}

await main();

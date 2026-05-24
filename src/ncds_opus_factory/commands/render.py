"""/render —— 离线录屏 + 合成 MP4。

包装 commands/render_runner.mjs（subprocess 调 node），让 paper_card_talk
风格的 1920×1080 HTML 播放器跑成可下发的 mp4。

调用约定：
- html_url 必须是 chrome 能访问的完整 URL（http(s)://...）；如果由 daoer 触发，
  daoer 侧需提前把画布编辑器 SPA + episode.json 放到 http 服务上。
- audio_dir 必须是 ncds-server 本机可读的目录，里面有 NNNN.mp3（来自 commands/tts）。
- output_path 是 ncds-server 本机的 MP4 落地路径。

依赖：node + puppeteer-core + puppeteer-screen-recorder + chrome + ffmpeg。
node_modules 默认指向 /tmp/node_modules（render.mjs 原约定），可用
NOF_RENDER_NODE_PATH 或调用参数 node_modules_path 覆盖。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

ProgressFn = Callable[[str], None]

HERE = Path(__file__).resolve().parent
DEFAULT_RUNNER = HERE / "render_runner.mjs"
DEFAULT_NODE_MODULES = os.environ.get("NOF_RENDER_NODE_PATH", "/tmp/node_modules")
DEFAULT_TIMEOUT = int(os.environ.get("NOF_RENDER_TIMEOUT", "1800"))

# render_runner.mjs 所在仓库根；ESM 解析从这里向上找 node_modules
_REPO_ROOT = HERE.parents[2]  # commands → ncds_opus_factory → src → <repo>


def _ensure_node_modules_link(node_modules: Path) -> None:
    """如果仓库根没有 node_modules，建 symlink 指向用户提供的位置。

    ESM 不读 NODE_PATH，也不看 cwd，只从 .mjs 文件目录向上找 node_modules。
    用 symlink 是最干净的桥接：用户已有真 node_modules 不会被覆盖。
    """
    target = _REPO_ROOT / "node_modules"
    if target.exists() or target.is_symlink():
        return
    target.symlink_to(node_modules)


def _noop(_text: str) -> None:
    return None


def run(
    html_url: str,
    audio_dir: str,
    output_path: str,
    fps: int = 30,
    width: int = 1920,
    height: int = 1080,
    intro_ms: int = 300,
    gap_ms: int = 80,
    ending_ms: int = 1500,
    audio_bitrate: str = "160k",
    tmp_dir: str = "/tmp",
    chrome_path: str = "/usr/bin/google-chrome",
    ffmpeg_path: str = "ffmpeg",
    runner_script: str | None = None,
    node_modules_path: str | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT,
    on_progress: ProgressFn = _noop,
) -> dict[str, Any]:
    """渲染 paper-card-talk 风格 HTML → MP4。

    Returns:
        {output_path, video_size_bytes, tmp_video, tmp_audio}
    """
    runner = Path(runner_script) if runner_script else DEFAULT_RUNNER
    if not runner.exists():
        raise RuntimeError(f"render runner script not found: {runner}")

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    # ESM 解析只从 .mjs 文件目录向上找 node_modules（不读 NODE_PATH/cwd），
    # 所以这里在仓库根建一个 symlink 桥接到用户配置的 node_modules。
    node_modules = Path(node_modules_path or DEFAULT_NODE_MODULES)
    if not node_modules.exists():
        raise RuntimeError(
            f"node_modules not found: {node_modules}. "
            "Install puppeteer-core + puppeteer-screen-recorder there, "
            "or pass node_modules_path."
        )
    _ensure_node_modules_link(node_modules)

    env = {
        **os.environ,
        "HTML_URL": html_url,
        "AUDIO_DIR": str(Path(audio_dir).resolve()),
        "OUTPUT_PATH": str(out.resolve()),
        "FPS": str(fps),
        "WIDTH": str(width),
        "HEIGHT": str(height),
        "INTRO_MS": str(intro_ms),
        "GAP_MS": str(gap_ms),
        "ENDING_MS": str(ending_ms),
        "AUDIO_BITRATE": audio_bitrate,
        "TMP_DIR": tmp_dir,
        "CHROME_PATH": chrome_path,
        "FFMPEG_PATH": ffmpeg_path,
    }

    on_progress(f"启动 node render runner（html={html_url}）")
    proc = subprocess.Popen(
        ["node", str(runner)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,  # 行缓冲，确保 [progress] 即时上送
    )

    last_json_line: str | None = None
    try:
        # 同步逐行读 stdout：commands/render.py 由 task_runner 跑在工作线程里，
        # 阻塞读没问题；event loop 不会被卡住
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.rstrip("\n")
            if not line:
                continue
            if line.startswith("[progress]"):
                on_progress(line[len("[progress]"):].strip())
            else:
                # 最后一行 JSON 是结果；其它行也直接转 progress 便于排查
                last_json_line = line
                on_progress(line)
        exit_code = proc.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise RuntimeError(f"render timeout after {timeout_seconds}s")

    if exit_code != 0:
        raise RuntimeError(f"render runner exited with code {exit_code}")

    # 解析最后一行 JSON 作为结果；如果没有，至少返回输出路径
    if last_json_line:
        try:
            payload = json.loads(last_json_line)
            return payload
        except json.JSONDecodeError:
            pass
    return {"output_path": str(out.resolve())}


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nof render", description="录屏合成 MP4（headless Chrome + ffmpeg）")
    parser.add_argument("--html-url", required=True)
    parser.add_argument("--audio-dir", required=True)
    parser.add_argument("--output-path", required=True)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--intro-ms", type=int, default=300)
    parser.add_argument("--gap-ms", type=int, default=80)
    parser.add_argument("--ending-ms", type=int, default=1500)
    parser.add_argument("--audio-bitrate", default="160k")
    parser.add_argument("--tmp-dir", default="/tmp")
    parser.add_argument("--chrome-path", default="/usr/bin/google-chrome")
    parser.add_argument("--ffmpeg-path", default="ffmpeg")
    parser.add_argument("--runner-script", default=None)
    parser.add_argument("--node-modules-path", default=None)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    args = parser.parse_args(argv)

    def on_progress(text: str) -> None:
        print(f"[progress] {text}", file=sys.stderr, flush=True)

    result = run(
        html_url=args.html_url,
        audio_dir=args.audio_dir,
        output_path=args.output_path,
        fps=args.fps,
        width=args.width,
        height=args.height,
        intro_ms=args.intro_ms,
        gap_ms=args.gap_ms,
        ending_ms=args.ending_ms,
        audio_bitrate=args.audio_bitrate,
        tmp_dir=args.tmp_dir,
        chrome_path=args.chrome_path,
        ffmpeg_path=args.ffmpeg_path,
        runner_script=args.runner_script,
        node_modules_path=args.node_modules_path,
        timeout_seconds=args.timeout,
        on_progress=on_progress,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())

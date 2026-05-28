"""/render_014 —— paper_card_talk_014 模板的一键渲染。

封装现有 commands/render.py（通用 puppeteer + ffmpeg 录屏管线），加上：
1. 把模板目录 + 用户 episode/audio/pictures 拼装到一个临时 workdir
2. 在 workdir 上起临时 python http.server（避免依赖外部 FastAPI）
3. 调 render.run() 出 MP4
4. 关 http server，可选清理 workdir

这样 CLI / pipeline runner / daoer 都能直接调，无外部 server 依赖。

调用约定
--------
- episode_path     : 用户编辑后的 episode.json（pipeline runner 来自 02_rw 节点）
- audio_dir        : NNNN.mp3 目录（来自 04_tts 节点）
- picture_dir      : 图片目录，可空（沿用模板自带；来自 03_image 节点产物）
- output_path      : 最终 MP4 落地路径
- template_dir     : 默认 src/.../templates/paper_card_talk_014/
- workdir          : 默认 output_path 同级 _render_workdir/
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from ncds_opus_factory.commands import render as render_cmd

ProgressFn = Callable[[str], None]

HERE = Path(__file__).resolve().parent
_REPO_ROOT = HERE.parents[2]  # commands → ncds_opus_factory → src → <repo>
DEFAULT_TEMPLATE_DIR = (
    _REPO_ROOT / "src" / "ncds_opus_factory" / "templates" / "paper_card_talk_014"
)
ASSETS_SUBDIR = ".014-draft-assets"
HTML_FILENAME = "014-draft.html"


def _noop(_text: str) -> None:
    return None


def _pick_free_port() -> int:
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_port(port: int, host: str = "127.0.0.1", timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
            s.settimeout(0.3)
            try:
                s.connect((host, port))
                return
            except OSError:
                time.sleep(0.05)
    raise RuntimeError(f"http.server on {host}:{port} did not come up within {timeout}s")


def _build_workdir(
    template_dir: Path,
    episode_path: Path,
    audio_dir: Path,
    picture_dir: Path | None,
    workdir: Path,
    on_progress: ProgressFn,
) -> Path:
    """复制模板到 workdir、覆盖 episode/audio/pictures。返回 workdir。

    workdir 结构（与模板一致）：
        workdir/
          014-draft.html
          .014-draft-assets/
            bootstrap.js / player.js / ...
            episode.json          ← 用户版本覆盖
            audio/0001.mp3 ...    ← TTS 产物
            pictures/...          ← 模板自带 + wst 产物覆盖
    """
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    on_progress(f"复制模板 {template_dir.name} -> workdir")
    # copytree(dirs_exist_ok=True) 把模板整套（HTML + 资产 + 自带 pictures）拷进去
    shutil.copytree(template_dir, workdir, dirs_exist_ok=True, symlinks=False)

    assets = workdir / ASSETS_SUBDIR
    if not assets.is_dir():
        raise RuntimeError(f"workdir 缺少资产目录 {ASSETS_SUBDIR}: {workdir}")

    # 覆盖 episode.json（template.json 不影响渲染，留着没事）
    on_progress(f"覆盖 episode.json: {episode_path}")
    shutil.copyfile(episode_path, assets / "episode.json")

    # 注入 audio
    target_audio = assets / "audio"
    if target_audio.exists():
        shutil.rmtree(target_audio)
    target_audio.mkdir(parents=True, exist_ok=True)
    audio_files = sorted(p for p in audio_dir.iterdir() if p.suffix == ".mp3")
    if not audio_files:
        raise RuntimeError(f"audio_dir 内没有 *.mp3: {audio_dir}")
    on_progress(f"链入 audio: {len(audio_files)} 个 mp3")
    for mp3 in audio_files:
        shutil.copyfile(mp3, target_audio / mp3.name)

    # 注入 pictures（覆盖模板自带，仅当用户传了 picture_dir 且非空）
    if picture_dir is not None and picture_dir.exists():
        pic_files = [p for p in picture_dir.iterdir() if p.is_file()]
        if pic_files:
            target_pic = assets / "pictures"
            target_pic.mkdir(parents=True, exist_ok=True)
            on_progress(f"覆盖 pictures: {len(pic_files)} 张")
            for pic in pic_files:
                shutil.copyfile(pic, target_pic / pic.name)

    return workdir


def run(
    episode_path: str,
    audio_dir: str,
    output_path: str,
    picture_dir: str | None = None,
    template_dir: str | None = None,
    workdir: str | None = None,
    fps: int = 30,
    width: int = 1920,
    height: int = 1080,
    intro_ms: int = 300,
    gap_ms: int = 80,
    ending_ms: int = 1500,
    audio_bitrate: str = "160k",
    chrome_path: str = "/usr/bin/google-chrome",
    ffmpeg_path: str = "ffmpeg",
    node_modules_path: str | None = None,
    timeout_seconds: int = render_cmd.DEFAULT_TIMEOUT,
    cleanup_workdir: bool = False,
    on_progress: ProgressFn = _noop,
) -> dict[str, Any]:
    """渲染 paper_card_talk_014 模板。Returns {output_path, video_size_bytes, workdir, http_port}."""
    ep_path = Path(episode_path).resolve()
    audio_p = Path(audio_dir).resolve()
    out_p = Path(output_path).resolve()
    pic_p = Path(picture_dir).resolve() if picture_dir else None
    tpl_p = Path(template_dir).resolve() if template_dir else DEFAULT_TEMPLATE_DIR

    if not ep_path.is_file():
        raise RuntimeError(f"episode.json not found: {ep_path}")
    if not audio_p.is_dir():
        raise RuntimeError(f"audio_dir not found: {audio_p}")
    if not tpl_p.is_dir():
        raise RuntimeError(f"template_dir not found: {tpl_p}")
    if not (tpl_p / HTML_FILENAME).is_file():
        raise RuntimeError(f"template missing {HTML_FILENAME}: {tpl_p}")

    out_p.parent.mkdir(parents=True, exist_ok=True)
    wd = Path(workdir).resolve() if workdir else (out_p.parent / "_render_workdir")

    _build_workdir(tpl_p, ep_path, audio_p, pic_p, wd, on_progress)

    port = _pick_free_port()
    on_progress(f"启动临时 http.server 端口 {port} cwd={wd}")
    http_proc = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
        cwd=str(wd),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_port(port)
        html_url = f"http://127.0.0.1:{port}/{HTML_FILENAME}"
        on_progress(f"HTML_URL={html_url}")

        result = render_cmd.run(
            html_url=html_url,
            audio_dir=str(wd / ASSETS_SUBDIR / "audio"),
            output_path=str(out_p),
            fps=fps,
            width=width,
            height=height,
            intro_ms=intro_ms,
            gap_ms=gap_ms,
            ending_ms=ending_ms,
            audio_bitrate=audio_bitrate,
            chrome_path=chrome_path,
            ffmpeg_path=ffmpeg_path,
            node_modules_path=node_modules_path,
            timeout_seconds=timeout_seconds,
            on_progress=on_progress,
        )
    finally:
        http_proc.terminate()
        with contextlib.suppress(Exception):
            http_proc.wait(timeout=3)

    if cleanup_workdir:
        shutil.rmtree(wd, ignore_errors=True)

    result["workdir"] = str(wd)
    result["http_port"] = port
    return result


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="nof render_014",
        description="渲染 paper_card_talk_014 模板出 MP4（自带临时 http.server）",
    )
    parser.add_argument("--episode-path", required=True, help="episode.json 路径")
    parser.add_argument("--audio-dir", required=True, help="audio/*.mp3 目录")
    parser.add_argument("--output-path", required=True, help="最终 MP4 落地路径")
    parser.add_argument("--picture-dir", default=None, help="可选 pictures/ 目录，覆盖模板自带")
    parser.add_argument("--template-dir", default=None)
    parser.add_argument("--workdir", default=None)
    parser.add_argument("--cleanup-workdir", action="store_true")
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--intro-ms", type=int, default=300)
    parser.add_argument("--gap-ms", type=int, default=80)
    parser.add_argument("--ending-ms", type=int, default=1500)
    parser.add_argument("--audio-bitrate", default="160k")
    parser.add_argument("--chrome-path", default="/usr/bin/google-chrome")
    parser.add_argument("--ffmpeg-path", default="ffmpeg")
    parser.add_argument("--node-modules-path", default=None)
    parser.add_argument("--timeout", type=int, default=render_cmd.DEFAULT_TIMEOUT)
    args = parser.parse_args(argv)

    def on_progress(text: str) -> None:
        print(f"[progress] {text}", file=sys.stderr, flush=True)

    result = run(
        episode_path=args.episode_path,
        audio_dir=args.audio_dir,
        output_path=args.output_path,
        picture_dir=args.picture_dir,
        template_dir=args.template_dir,
        workdir=args.workdir,
        fps=args.fps,
        width=args.width,
        height=args.height,
        intro_ms=args.intro_ms,
        gap_ms=args.gap_ms,
        ending_ms=args.ending_ms,
        audio_bitrate=args.audio_bitrate,
        chrome_path=args.chrome_path,
        ffmpeg_path=args.ffmpeg_path,
        node_modules_path=args.node_modules_path,
        timeout_seconds=args.timeout,
        cleanup_workdir=args.cleanup_workdir,
        on_progress=on_progress,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())

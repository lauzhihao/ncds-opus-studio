"""/render_015 —— paper_card_talk_015 模板的一键渲染。

与 014 的区别：015 按 scene 整段配音（scene-<sid>.mp3 + episode.beats[].audioFile/
audioStart/audioEnd），渲染用 015 模板自带的 render.mjs（它自己起 http.server +
puppeteer 录屏 + ffmpeg 按 episode 的 scene mp3 concat 合音）。

调用约定（同 014）：
- episode_path : 用户编辑后的 episode.json（来自 02_rw 节点，含 tts 写回的时间戳）
- audio_dir    : scene-<sid>.mp3 目录（来自 04_tts 节点的整段合成）
- picture_dir  : 图片目录，可空（来自 03_image 节点）
- output_path  : 最终 MP4 落地路径
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

ProgressFn = Callable[[str], None]

HERE = Path(__file__).resolve().parent
_REPO_ROOT = HERE.parents[2]
DEFAULT_TEMPLATE_DIR = (
    _REPO_ROOT / "src" / "ncds_opus_factory" / "templates" / "paper_card_talk_015"
)
ASSETS_SUBDIR = ".015-draft-assets"
HTML_FILENAME = "015-draft.html"
RENDER_MJS = "render.mjs"
DEFAULT_NODE_MODULES = "/tmp/node_modules"
DEFAULT_OUTPUT_MP4 = "output/015-draft.mp4"  # render.mjs 内部约定的输出路径（相对 assets）

DEFAULT_TIMEOUT = int(os.getenv("NOF_RENDER_TIMEOUT", "1800"))


def _noop(_text: str) -> None:
    return None


def _build_workdir(
    template_dir: Path,
    episode_path: Path,
    audio_dir: Path,
    picture_dir: Path | None,
    workdir: Path,
    on_progress: ProgressFn,
) -> Path:
    """拷模板到 workdir，覆盖 episode/audio/pictures。结构同 015 模板。
    render.mjs 在 workdir/.015-draft-assets/ 下跑，HERE=该目录，自然读到覆盖后的产物。
    """
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    on_progress(f"复制模板 {template_dir.name} -> workdir")
    shutil.copytree(template_dir, workdir, dirs_exist_ok=True, symlinks=False)

    assets = workdir / ASSETS_SUBDIR
    if not assets.is_dir():
        raise RuntimeError(f"workdir 缺少资产目录 {ASSETS_SUBDIR}: {workdir}")

    on_progress(f"覆盖 episode.json: {episode_path}")
    shutil.copyfile(episode_path, assets / "episode.json")

    # 注入 audio（scene-<sid>.mp3）
    target_audio = assets / "audio"
    if target_audio.exists():
        shutil.rmtree(target_audio)
    target_audio.mkdir(parents=True, exist_ok=True)
    audio_files = sorted(p for p in audio_dir.iterdir() if p.suffix == ".mp3")
    if not audio_files:
        raise RuntimeError(f"audio_dir 内没有 *.mp3: {audio_dir}")
    on_progress(f"链入 audio: {len(audio_files)} 段 scene mp3")
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
    node_modules_path: str | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT,
    cleanup_workdir: bool = False,
    on_progress: ProgressFn = _noop,
) -> dict[str, Any]:
    """渲染 paper_card_talk_015。Returns {output_path, video_size_bytes, workdir}."""
    ep_path = Path(episode_path).resolve()
    audio_p = Path(audio_dir).resolve()
    out_p = Path(output_path).resolve()
    pic_p = Path(picture_dir).resolve() if picture_dir else None
    tpl_p = Path(template_dir).resolve() if template_dir else DEFAULT_TEMPLATE_DIR

    if not ep_path.is_file():
        raise RuntimeError(f"episode.json not found: {ep_path}")
    if not audio_p.is_dir():
        raise RuntimeError(f"audio_dir not found: {audio_p}")
    if not (tpl_p / HTML_FILENAME).is_file():
        raise RuntimeError(f"template missing {HTML_FILENAME}: {tpl_p}")

    out_p.parent.mkdir(parents=True, exist_ok=True)
    wd = Path(workdir).resolve() if workdir else (out_p.parent / "_render_workdir")
    _build_workdir(tpl_p, ep_path, audio_p, pic_p, wd, on_progress)

    assets = wd / ASSETS_SUBDIR
    render_script = assets / RENDER_MJS
    if not render_script.is_file():
        raise RuntimeError(f"render.mjs not found: {render_script}")

    node_modules = node_modules_path or DEFAULT_NODE_MODULES
    env = {**os.environ, "NODE_PATH": node_modules}

    on_progress("启动 015 render.mjs（自带 http.server + puppeteer + ffmpeg）")
    proc = subprocess.Popen(
        ["node", str(render_script)],
        cwd=str(wd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    assert proc.stdout is not None
    tail: list[str] = []
    for line in iter(proc.stdout.readline, ""):
        s = line.rstrip("\n")
        if s:
            on_progress(s)
            tail.append(s)
            if len(tail) > 30:
                tail.pop(0)
    proc.stdout.close()
    code = proc.wait(timeout=timeout_seconds)
    if code != 0:
        snippet = "\n".join(tail).strip()
        raise RuntimeError(f"render.mjs exited {code}\n--- last output ---\n{snippet}")

    # render.mjs 输出到 assets/output/015-draft.mp4，拷到目标
    produced = assets / DEFAULT_OUTPUT_MP4
    if not produced.is_file():
        raise RuntimeError(f"render.mjs 未产出 MP4: {produced}")
    shutil.copyfile(produced, out_p)

    size = out_p.stat().st_size
    if cleanup_workdir:
        shutil.rmtree(wd, ignore_errors=True)

    return {"output_path": str(out_p), "video_size_bytes": size, "workdir": str(wd)}


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="nof render_015",
        description="渲染 paper_card_talk_015 模板出 MP4（015 render.mjs scene 整段合音）",
    )
    parser.add_argument("--episode", required=True)
    parser.add_argument("--audio-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--picture-dir", default=None)
    parser.add_argument("--template-dir", default=None)
    parser.add_argument("--workdir", default=None)
    parser.add_argument("--node-modules", default=None)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--cleanup-workdir", action="store_true")
    args = parser.parse_args(argv)

    def on_progress(text: str) -> None:
        print(f"[progress] {text}", file=sys.stderr, flush=True)

    result = run(
        episode_path=args.episode,
        audio_dir=args.audio_dir,
        output_path=args.output,
        picture_dir=args.picture_dir,
        template_dir=args.template_dir,
        workdir=args.workdir,
        node_modules_path=args.node_modules,
        timeout_seconds=args.timeout,
        cleanup_workdir=args.cleanup_workdir,
        on_progress=on_progress,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())

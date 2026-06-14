"""截帧能力：静止帧检测 —— 只采「动画演完的稳定构图」，不取 scene 切变的过渡帧。

底层原语，返回绝对 Path 列表。
"""

from __future__ import annotations

from pathlib import Path


def extract_frames(
    video_path: Path, out_dir: Path, max_frames: int = 8,
    sample_s: float = 0.3, still_th: float = 2.0, min_still: int = 2,
) -> list[Path]:
    """采画面停留段的「最终静止帧」,不取 scene 切变的过渡帧。

    沈括只采静态素材(动效由下游 figure_talk 渲染时后期加);scene 切变帧是元素正飞入/移动的
    过渡瞬间,抠图会带灰边残影。这里按帧差分找「画面停住」的连续段(diff<still_th 且时长>=min_still),
    取每段末帧(动画完全演完的干净构图),段间再按相似度去重,超量则均匀取。
    """
    import cv2
    import numpy as np

    out_dir.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(str(video_path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    step = max(1, int(fps * sample_s))
    samples: list = []  # [(bgr 原帧, 灰度小图)]
    i = 0
    while True:
        ret, fr = cap.read()
        if not ret:
            break
        if i % step == 0:
            g = cv2.resize(cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY), (160, 120)).astype(np.int16)
            samples.append((fr, g))
        i += 1
    cap.release()
    if not samples:
        return []

    diffs = [0.0] + [float(np.mean(np.abs(samples[k][1] - samples[k - 1][1]))) for k in range(1, len(samples))]
    # 连续静止段(diff<still_th 且段长>=min_still),取段末帧=动画演完的最终静态构图
    segs: list[list[int]] = []
    cur: list[int] = []
    for k in range(len(samples)):
        if diffs[k] < still_th:
            cur.append(k)
        else:
            if len(cur) >= min_still:
                segs.append(cur)
            cur = []
    if len(cur) >= min_still:
        segs.append(cur)

    reps: list[int] = []
    prev = None
    for seg in segs:
        r = seg[-1]
        g = samples[r][1]
        if prev is not None and float(np.mean(np.abs(g - prev))) < still_th:
            continue  # 与上一张几乎相同 -> 去重
        prev = g
        reps.append(r)

    if len(reps) > max_frames:  # 超量则均匀取
        sel = np.linspace(0, len(reps) - 1, max_frames).astype(int)
        reps = [reps[j] for j in sel]

    frames: list[Path] = []
    for n, r in enumerate(reps, 1):
        fp = out_dir / f"frame_{n:03d}.jpg"
        cv2.imwrite(str(fp), samples[r][0])
        frames.append(fp)
    return frames

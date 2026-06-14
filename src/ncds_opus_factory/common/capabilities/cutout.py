"""抠图能力：裁舞台区 + 阈值分割(对标号 = 黑剪影/彩色道具 on 浅纸背景)。

底层原语，返回绝对 Path 列表。
"""

from __future__ import annotations

from pathlib import Path

# 舞台区裁剪比例(left,top,right,bottom):对标号 PPT 模板固定版式,去顶标签栏/底字幕/角落水印。
# 因对标号/模板而异,需要时调这里(后续可做成每号配置)。实测「自我重塑」「健康熬夜」两号通用。
STAGE_CROP = (0.04, 0.13, 0.96, 0.82)
# 前景占比合格区间:太低=空帧/纯背景,太高=纯色过场(如纯黑标题帧),都丢弃。
FG_MIN, FG_MAX = 0.02, 0.85


def crop_stage(im, crop: tuple[float, float, float, float] = STAGE_CROP):
    """裁出中间舞台区,去掉顶标签 / 底字幕 / 水印声明 —— 只留主要内容素材。"""
    w, h = im.size
    l, t, r, b = crop
    return im.crop((int(w * l), int(h * t), int(w * r), int(h * b)))


def threshold_cutout(im):
    """黑剪影 + 彩色道具 on 浅纸背景 -> 透明抠图。返回 (RGBA, 前景占比)。

    前景 = 暗(剪影线条) 或 鲜艳(彩色道具如蔬菜);浅米纸纹背景(高亮度低饱和)透明。
    比 rembg(u2net 显著性)更适合简笔画:保留全部元素、边缘锐利、不下大模型。
    """
    import numpy as np
    from PIL import Image

    arr = np.asarray(im.convert("RGB")).astype(np.float32)
    lum = 0.299 * arr[..., 0] + 0.587 * arr[..., 1] + 0.114 * arr[..., 2]
    sat = (arr.max(2) - arr.min(2)) / (arr.max(2) + 1e-6)
    fg = (lum < 150) | (sat > 0.30)
    alpha = fg.astype(np.uint8) * 255
    rgba = np.dstack([arr.astype(np.uint8), alpha])
    return Image.fromarray(rgba, "RGBA"), float(fg.mean())


def rembg_cutout(im):
    """rembg(u2net)去背景:留作彩色照片类复杂素材的备选(--engine rembg)。"""
    from rembg import remove
    return remove(im.convert("RGBA"))


def cutout(frames: list[Path], out_dir: Path, engine: str = "threshold", crop: bool = True) -> list[Path]:
    """逐帧:裁舞台区 -> 抠图 -> 按前景占比过滤纯色/空帧 -> 存透明 png。"""
    from PIL import Image

    out_dir.mkdir(parents=True, exist_ok=True)
    cutouts: list[Path] = []
    for fr in frames:
        with Image.open(fr) as raw:
            im = crop_stage(raw) if crop else raw.convert("RGB")
            if engine == "rembg":
                res, ratio = rembg_cutout(im), None
            else:
                res, ratio = threshold_cutout(im)
        if ratio is not None and not (FG_MIN <= ratio <= FG_MAX):
            continue  # 纯色过场 / 空帧,丢弃
        dst = out_dir / (fr.stem + ".png")
        res.save(dst)
        cutouts.append(dst)
    return cutouts

"""测试期 mock：不真调 agent / codex / opus，只发进度 + 返回假产物。

开关（env）：
    NOF_MOCK_AGENTS=all          # 所有命令走 mock
    NOF_MOCK_AGENTS=liuyong,boya # 指定命令走 mock，其余仍真跑
    （留空 = 全部真跑，线上默认）

约定：mock run 与真 run 同签名 `run(on_progress, **params) -> dict`，
且返回结构与真 run 一致，前端无需区分（柳永返回 drafts[].{text,qc,qc_rubric}）。
"""

from __future__ import annotations

import os
import time
from typing import Any, Callable

from ncds_opus_factory.common import topic_store

RunFn = Callable[..., dict[str, Any]]


def _noop(_text: str) -> None:
    return None


def mock_liuyong(on_progress: Callable[[str], None] = _noop, **params: Any) -> dict[str, Any]:
    """逼真 mock：双模型各一稿 + 两道质检，进度逐条吐（带小停顿,SSE 看得到推进）。"""
    topic = (params.get("topic") or "").strip() or "未命名选题"
    reqs = (params.get("user_requirements") or "").strip()

    on_progress(f"柳永启动(MOCK) 选题: {topic[:40]}")
    time.sleep(0.6)
    on_progress("质检[gpt-5.5]: pass - 密度超阈 0 类 / 硬禁命中 0 类")
    time.sleep(0.6)
    on_progress("质检[gemini]: fail - 密度超阈 1 类 / 硬禁命中 0 类")
    time.sleep(0.5)
    on_progress("  AI 味超标,打回重写第 1 轮...")
    time.sleep(0.5)
    on_progress("  第 1 轮后: pass - 密度超阈 0 类 / 硬禁命中 0 类")
    time.sleep(0.4)
    on_progress("质检2[rubric/opus]: 41/50 良好")
    on_progress("柳永完成(MOCK): 2 稿成功")

    s1 = (f"【钩子】关于「{topic}」，多数人第一反应就做错了。\n\n"
          "真正的高手会先停顿一秒，再反问一句，把模糊的恶意逼成具体的指控。\n\n"
          "记住：先稳住情绪，你才握得住主动权。")
    s2 = (f"你以为「{topic}」很难处理？其实就三步。\n\n"
          "第一步分清是玩笑还是带刺；第二步别接情绪，只接事实；第三步把问题摆上台面。\n\n"
          "会做人的人，从不在上头时回应。")
    if reqs:
        s1 += f"\n\n（已按附加要求：{reqs}）"

    return {
        "job_id": f"MOCK_{int(time.time() * 1000)}",
        "deliverables_dir": "mock",
        "raw_status": "success",
        "drafts": [
            {
                "model": "gpt-5.5", "path": "mock", "text": s1,
                "qc": {"verdict": "pass", "summary": "密度超阈 0 类 / 硬禁命中 0 类",
                       "density": [], "hard": []},
                "qc_rubric": {"available": True,
                              "dims": {"节奏": 8, "真实性": 8, "精炼度": 9, "直接性": 8, "信任度": 8},
                              "total": 41, "grade": "良好", "issues": ["开头钩子可以再狠一点"]},
            },
            {
                "model": "gemini", "path": "mock", "text": s2,
                "qc": {"verdict": "pass", "summary": "密度超阈 0 类 / 硬禁命中 0 类",
                       "density": [], "hard": []},
                "qc_rubric": {"available": True,
                              "dims": {"节奏": 7, "真实性": 7, "精炼度": 8, "直接性": 8, "信任度": 7},
                              "total": 37, "grade": "良好", "issues": []},
            },
        ],
    }


def _generic_mock(cmd: str) -> RunFn:
    """非 liuyong 命令的通用 mock：发两条进度,返回最小成功 dict。"""
    def run(on_progress: Callable[[str], None] = _noop, **params: Any) -> dict[str, Any]:
        on_progress(f"{cmd} 启动(MOCK)")
        time.sleep(0.5)
        on_progress(f"{cmd} 完成(MOCK)")
        return {"ok": True, "mock": True, "cmd": cmd, "params": params}
    return run


_MOCK_KWS = ["钱越省越穷", "老板都爱画饼", "副业先亏后赚", "存款利率一直降", "年轻人不买房了"]


def mock_guiguzi_analyze(on_progress: Callable[[str], None] = _noop, **params: Any) -> dict[str, Any]:
    """第一步 mock：双模型各产一份罐头爆款原因分析（结构化）。"""
    on_progress("鬼谷子启动(MOCK): 双模型分析爆款原因")
    time.sleep(0.3)

    def _analysis(model: str) -> dict[str, Any]:
        return {
            "hook_reason": f"({model} MOCK) 用反差+具体场景戳中打工人的钱焦虑",
            "audience": "20-35 岁一二线打工人",
            "hooks": ["开头一句反常识断言", "给可照搬的具体话术", "结尾留钩子"],
            "direction": "顺着'省钱反而穷'的反差,做认知反转型短口播",
        }

    return {m: {"analysis": _analysis(m), "error": None} for m in ("opus", "deepseek", "agy")}


def mock_guiguzi_topics(on_progress: Callable[[str], None] = _noop, **params: Any) -> dict[str, Any]:
    """第二步 mock：多模型选题（candidates 三栏 + topics 扁平兼容）。

    与真 generate_topics 一样经 topic_store.merge 落库、out 指向真实库文件——卧龙永远真跑
    round 逻辑,续跑段从**库**里挑题(P5),mock 不落库 E2E 就测不到这条链。
    """
    on_progress("鬼谷子(MOCK): 多模型并行出题")
    time.sleep(0.4)

    def _topic(kw: str, i: int, model: str) -> dict[str, Any]:
        return {"title": f"为什么{kw},多数人都想反了", "angle": "反直觉",
                "why": "落地反击感", "potential": 95 - i * 8,
                "anchor_comment": f"评论:{kw}", "source_model": model, "source": "mock"}

    # 三栏:opus / deepseek / agy 各按同样的 5 条评论各出 5 个选题(标题去重,入库不翻倍)
    opus = [_topic(kw, i, "opus") for i, kw in enumerate(_MOCK_KWS)]
    deepseek = [_topic(kw, i, "deepseek") for i, kw in enumerate(_MOCK_KWS)]
    agy = [_topic(kw, i, "agy") for i, kw in enumerate(_MOCK_KWS)]
    candidates = {"opus": {"topics": opus, "error": None},
                  "deepseek": {"topics": deepseek, "error": None},
                  "agy": {"topics": agy, "error": None}}
    flat = opus + deepseek + agy
    # 溯源如实记 mock(条目自带 source=mock,相等则不挪 bench_source):
    # 谎报 guiguzi 会让误开 mock 混入生产库的罐头题事后无法按 source 清理
    merged = topic_store.merge(flat, source="mock")
    on_progress(f"鬼谷子完成(MOCK): 三模型各 {len(_MOCK_KWS)} 个(入库 {merged['added']} 新/{merged['skipped']} 重复)")
    return {"candidates": candidates, "topics": flat,
            "out": str(topic_store.default_topics_path()), "added": merged["added"]}


def mock_guiguzi(on_progress: Callable[[str], None] = _noop, **params: Any) -> dict[str, Any]:
    """端到端 mock（替 guiguzi.run，给卧龙/task-runner）：analyze + topics 组合，附 analysis。"""
    analyses = mock_guiguzi_analyze(on_progress)
    result = mock_guiguzi_topics(on_progress, **params)
    result["analysis"] = analyses
    result["chosen_analysis"] = analyses["opus"]["analysis"]
    return result


MOCK_RUNS: dict[str, RunFn] = {
    "liuyong": mock_liuyong,
    "guiguzi": mock_guiguzi,
}


def maybe_mock_registry(registry: dict[str, RunFn]) -> dict[str, RunFn]:
    """按 NOF_MOCK_AGENTS 把指定命令替换成 mock；不改原 registry,返回副本。"""
    mode = os.environ.get("NOF_MOCK_AGENTS", "").strip()
    if not mode:
        return registry
    targets = set(registry) if mode == "all" else {x.strip() for x in mode.split(",") if x.strip()}
    out = dict(registry)
    for cmd in targets:
        if cmd in registry:
            out[cmd] = MOCK_RUNS.get(cmd) or _generic_mock(cmd)
    return out

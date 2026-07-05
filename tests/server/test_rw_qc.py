"""柳永质检闸门（pipeline_rw_helpers._apply_rw_qc / _purge_ai_taste_rw）单元测试。

mock ai_taste / quality_rubric / purge_ai_taste，验证（同 liuyong.py 质检语义）：
- ai_taste pass → 不打回，带 qc + qc_rubric + draft.qc.json sidecar；
- ai_taste fail → _purge_ai_taste_rw 调 opus 重写 → 二次 scan pass，回写 draft.md。
"""

from __future__ import annotations

from ncds_opus_factory.common import ai_taste, quality_rubric
from ncds_opus_factory.server import pipeline_rw_helpers as rw_helpers


def _seed_draft(tmp_path, text: str):
    md = tmp_path / "opus"
    md.mkdir()
    (md / "draft.md").write_text(text, encoding="utf-8")
    return md


def test_apply_rw_qc_pass(tmp_path, monkeypatch):
    """ai_taste pass：不触发打回重写，qc/qc_rubric 落位 + sidecar 写盘。"""
    md = _seed_draft(tmp_path, "一段干净自然的口播稿正文。")
    monkeypatch.setattr(ai_taste, "scan", lambda t: {"verdict": "pass", "summary": "无问题", "density": []})
    monkeypatch.setattr(
        quality_rubric, "score",
        lambda t, **k: {"available": True, "total": 42, "grade": "A",
                        "dims": {"节奏": 9, "真实性": 8, "精炼度": 8, "直接性": 9, "信任度": 8}, "issues": []},
    )

    def boom(*a, **k):
        raise AssertionError("pass 不该触发打回重写")

    monkeypatch.setattr(rw_helpers, "_call_opus_for_rw", boom)

    out = rw_helpers._apply_rw_qc(md, "opus", lambda *_: None)
    assert out["qc"]["verdict"] == "pass"
    assert out["needs_fix"] is False
    assert out["qc_rubric"]["total"] == 42 and out["qc_rubric"]["grade"] == "A"
    assert (md / "draft.qc.json").is_file()


def test_apply_rw_qc_fail_then_purge(tmp_path, monkeypatch):
    """ai_taste fail → _purge_ai_taste_rw 调 opus 重写 → 二次复检 pass，回写 draft.md。"""
    md = _seed_draft(tmp_path, "有 AI 味的稿子。")
    calls = {"scan": 0}

    def fake_scan(t):
        calls["scan"] += 1
        return {"verdict": "fail" if calls["scan"] == 1 else "pass",
                "summary": "AI 味", "density": ["首先其次", "综上所述"]}

    purged = "消除 AI 味后的干净长稿。" * 30  # >200 字，过 _purge 有效性闸
    monkeypatch.setattr(ai_taste, "scan", fake_scan)
    monkeypatch.setattr(quality_rubric, "score", lambda t, **k: {"available": False, "skipped": "本机无 opus"})
    monkeypatch.setattr(ai_taste, "purge_ai_taste", lambda *_a, **_k: purged)

    out = rw_helpers._apply_rw_qc(md, "gpt5", lambda *_: None)
    assert calls["scan"] == 2                       # 打回重写一轮后复检
    assert out["qc"]["verdict"] == "pass"           # 重写后通过
    assert out["needs_fix"] is False
    assert purged.strip() in (md / "draft.md").read_text(encoding="utf-8")  # 回写重写稿
    assert out["qc_rubric"]["available"] is False   # 本机无 opus 跳过 rubric


def test_apply_rw_qc_fail_purge_empty_keeps_original(tmp_path, monkeypatch):
    """重写未返回有效稿（<200 字）→ 保留上一版，不死循环。"""
    original = "原始有 AI 味的稿子保留。"
    md = _seed_draft(tmp_path, original)
    monkeypatch.setattr(ai_taste, "scan", lambda t: {"verdict": "fail", "summary": "x", "density": ["套话"]})
    monkeypatch.setattr(quality_rubric, "score", lambda t, **k: {"available": False, "skipped": "no opus"})
    monkeypatch.setattr(ai_taste, "purge_ai_taste", lambda *_a, **_k: "太短")  # <200 字，无效

    out = rw_helpers._apply_rw_qc(md, "deepseek", lambda *_: None)
    assert out["qc"]["verdict"] == "fail"           # 仍 fail（重写无效，未死循环）
    assert out["needs_fix"] is True
    assert original in (md / "draft.md").read_text(encoding="utf-8")


def test_purge_ai_taste_rw_falls_back_to_refine(tmp_path, monkeypatch):
    """opus 消 AI 味失败时，用可用 refine 模型兜底，且不把异常细节推给前端。"""
    report = {"verdict": "fail", "summary": "AI 味", "density": [{"rule": "套路", "samples": ["然而"]}]}
    progress: list[str] = []

    def boom(*_a, **_k):
        raise RuntimeError("secret stack")

    monkeypatch.setattr(ai_taste, "purge_ai_taste", boom)
    monkeypatch.setattr(quality_rubric, "refine", lambda *_a, **_k: "兜底优化后的正文" * 30)

    out = rw_helpers._purge_ai_taste_rw("原稿正文" * 30, report, progress.append, model_id="deepseek")

    assert out.startswith("兜底优化后的正文")
    assert all("secret stack" not in line for line in progress)

"""吴道子(wudaozi)单测:库/图标解析、规则选用、不丢句校验、端到端产出(codex 打桩)。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ncds_opus_factory.commands import wudaozi


# --------------------------------------------------------------------------- #
# 读脚本 / 归一化
# --------------------------------------------------------------------------- #
def test_read_script_strips_title():
    body, title = wudaozi.read_script(script_text="一个短标题\n\n正文第一段在这里。\n# 这是 markdown 标题\n正文第二段。")
    assert title == "一个短标题"
    assert "正文第一段在这里" in body
    assert "正文第二段" in body
    assert "markdown 标题" not in body  # # 开头被剥
    assert "一个短标题" not in body      # 标题不计入正文


def test_read_script_requires_input():
    with pytest.raises(ValueError):
        wudaozi.read_script()


def test_normalize_drops_punct():
    assert wudaozi._normalize("你好，世界！ABC。") == "你好世界ABC"


# --------------------------------------------------------------------------- #
# 图标清单(从真 icons.js 解析)
# --------------------------------------------------------------------------- #
def test_load_icons_catalog_from_real_file():
    catalog = wudaozi.load_icons_catalog()
    assert len(catalog) > 20  # icons.js 当前 54 个
    ids = {c["id"] for c in catalog}
    assert "clock" in ids
    clock = next(c for c in catalog if c["id"] == "clock")
    assert "时间" in clock["keywords"]
    assert clock["enter"]  # enter 动效名非空


# --------------------------------------------------------------------------- #
# 规则选用
# --------------------------------------------------------------------------- #
LIB = [
    {"file": "figures/phone.png", "keywords": ["手机", "分心", "低头"], "scene": ["认知", "职场"], "concept": "低头看手机"},
    {"file": "figures/run.png", "keywords": ["行动", "执行", "开始"], "scene": ["成长"], "concept": "迈步向前"},
]


def test_select_figure_picks_best_by_keyword():
    f = wudaozi.select_figure(LIB, ["手机", "拖延"], scene="职场")
    assert f["file"] == "figures/phone.png"
    assert f["_score"] >= 2


def test_select_figure_none_when_no_hit():
    assert wudaozi.select_figure(LIB, ["量子力学"], scene=None) is None
    assert wudaozi.select_figure([], ["手机"], scene=None) is None


def test_select_icons_matches_emphasis():
    catalog = [
        {"id": "clock", "enter": "pop", "keywords": ["时间", "拖延"]},
        {"id": "bulb", "enter": "pop", "keywords": ["灵感", "想法"]},
    ]
    assert wudaozi.select_icons(catalog, ["拖延"]) == ["clock"]
    assert wudaozi.select_icons(catalog, ["想法", "时间"], max_n=2) == ["bulb", "clock"]
    assert wudaozi.select_icons(catalog, []) == []


def test_pick_motion():
    assert wudaozi.pick_motion("hook", 0) == "zoom-in"
    assert wudaozi.pick_motion("close", 5) == "zoom-out"
    assert wudaozi.pick_motion("body", 0) == "pan-left"
    assert wudaozi.pick_motion("body", 1) == "pan-right"


# --------------------------------------------------------------------------- #
# 不丢句硬闸门
# --------------------------------------------------------------------------- #
def test_check_coverage_pass():
    body = "第一句话内容。第二句话道理。第三句话收尾。"
    beats = [{"zh": "第一句话内容"}, {"zh": "第二句话道理"}, {"zh": "第三句话收尾"}]
    qc = wudaozi.check_coverage(beats, body)
    assert qc["verdict"] == "pass"
    assert qc["ratio"] == 1.0


def test_check_coverage_fail_on_dropped_sentence():
    body = "第一句话内容。第二句话道理。第三句话收尾。"
    beats = [{"zh": "第一句话内容"}]  # 丢了两句
    qc = wudaozi.check_coverage(beats, body)
    assert qc["verdict"] == "fail"
    assert qc["ratio"] < 0.95


def test_soft_checks_flags_no_figure():
    beats = [{"zh": "a", "figure": "figures/x.png"}, {"zh": "b"}]
    notes = wudaozi.soft_checks(beats)
    assert any("无主体剪影" in n for n in notes)


# --------------------------------------------------------------------------- #
# build_beats 组装
# --------------------------------------------------------------------------- #
def test_build_beats_assembles_fields():
    storyboard = [
        {"zh": "开场看手机", "keywords": ["手机", "分心"], "kind": "hook", "title": "标题", "tag": "职场 · 认知"},
        {"zh": "然后去行动", "keywords": ["行动"], "kind": "close"},
    ]
    beats, detail = wudaozi.build_beats(storyboard, LIB, [], scene="职场")
    assert beats[0]["figure"] == "figures/phone.png"
    assert beats[0]["title"] == "标题"
    assert beats[0]["motion"] == "zoom-in"  # hook
    assert beats[1]["figure"] == "figures/run.png"
    assert beats[1]["motion"] == "zoom-out"  # close
    assert detail[0]["figure_reason"].startswith("keywords∩")


# --------------------------------------------------------------------------- #
# 端到端 run(codex 打桩)
# --------------------------------------------------------------------------- #
def _make_lib(tmp_path: Path) -> Path:
    lib = tmp_path / "figure_lib"
    (lib / "figures").mkdir(parents=True)
    figures = [
        {"file": "figures/a.png", "keywords": ["测试", "内容"], "scene": ["职场"], "concept": "测试"},
        {"file": "figures/b.png", "keywords": ["道理", "收尾"], "scene": ["职场"], "concept": "讲道理"},
    ]
    for f in figures:
        (lib / f["file"]).write_bytes(b"\x89PNG\r\n")  # 占位:write_instance 只复制不读
    (lib / "library.json").write_text(json.dumps({"figures": figures}, ensure_ascii=False), encoding="utf-8")
    return lib


def test_run_end_to_end_stubbed(tmp_path, monkeypatch):
    body_text = "测试标题\n\n第一句话测试内容。第二句话讲道理。第三句话来收尾。"

    def fake_codex(script_body, title_hint, **kwargs):
        # 逐字切分(去标点后拼接==正文),保证不丢句校验 pass
        return [
            {"zh": "第一句话测试内容", "keywords": ["测试", "内容"], "kind": "hook", "title": "测试标题", "tag": "职场 · 认知"},
            {"zh": "第二句话讲道理", "keywords": ["道理"], "emphasis": [], "kind": "body"},
            {"zh": "第三句话来收尾", "keywords": ["收尾"], "kind": "close"},
        ]

    monkeypatch.setattr(wudaozi, "storyboard_via_codex", fake_codex)

    lib = _make_lib(tmp_path)
    out = tmp_path / "job"
    result = wudaozi.run(script_text=body_text, library_dir=lib, out_dir=out)

    assert result["qc"]["verdict"] == "pass"
    assert len(result["beats"]) == 3
    # 实例文件齐全
    for name in ("index.html", "player.js", "render.mjs", "beats.js", "beats.json", "storyboard.json", "beats.qc.json"):
        assert (out / name).exists(), f"缺 {name}"
    # beats.js 是合法 window.BEATS + 中文双引号(tts_gen 正则只认双引号)
    beats_js = (out / "beats.js").read_text(encoding="utf-8")
    assert beats_js.startswith("/*") and "window.BEATS" in beats_js
    assert '"zh": "第一句话测试内容"' in beats_js
    # 选中的剪影被复制进实例
    assert (out / "figures" / "a.png").exists()
    # storyboard 明细带 reason
    detail = json.loads((out / "storyboard.json").read_text(encoding="utf-8"))
    assert detail[0]["figure"] == "figures/a.png"


def test_run_retries_on_coverage_fail(tmp_path, monkeypatch):
    body_text = "测试标题\n\n第一句话测试内容。第二句话讲道理。第三句话来收尾。"
    calls = {"n": 0}

    def fake_codex(script_body, title_hint, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return [{"zh": "第一句话测试内容", "keywords": ["测试"], "kind": "hook"}]  # 丢句 -> fail
        return [  # 第二次切全
            {"zh": "第一句话测试内容", "keywords": ["测试"], "kind": "hook"},
            {"zh": "第二句话讲道理", "keywords": ["道理"], "kind": "body"},
            {"zh": "第三句话来收尾", "keywords": ["收尾"], "kind": "close"},
        ]

    monkeypatch.setattr(wudaozi, "storyboard_via_codex", fake_codex)
    result = wudaozi.run(script_text=body_text, library_dir=_make_lib(tmp_path), out_dir=tmp_path / "job")
    assert calls["n"] == 2  # 打回重切一次
    assert result["qc"]["verdict"] == "pass"

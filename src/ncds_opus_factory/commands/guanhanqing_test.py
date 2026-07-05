"""关汉卿(guanhanqing)单测：输入文稿 -> director_plan.json。"""

from __future__ import annotations

import json

from ncds_opus_factory.commands import guanhanqing


SAMPLE_SCRIPT = """一杯四块钱的柠檬水，卖出了6万家店、90亿杯，凭什么？不是运气，是把成本算到了骨子里。
别的奶茶店在研究新品、做营销，蜜雪冰城只琢磨一件事：怎么把一杯柠檬水的成本再压低两毛钱。
全国70%的柠檬来自四川安岳。采购量到这种级别，拿到的就是产地成本价。
原料到手，它不找代工厂，自己在河南建厂。奶精、果汁、果酱、茶叶，全部自己生产。
货生产出来，它还有自己的冷链车队。从河南仓库直发，全程冷链送到每一家门店。
大规模采购、自建工厂、自营物流，三招下来，成本低到别人没法跟。"""


def test_read_script_keeps_body_when_no_title():
    doc = guanhanqing.read_script(script_text=SAMPLE_SCRIPT)
    assert doc.title == ""
    assert len(doc.paragraphs) == 6
    assert "四块钱" in doc.body


def test_build_cue_drafts_keeps_single_line_limit():
    doc = guanhanqing.read_script(script_text=SAMPLE_SCRIPT)
    cues = guanhanqing.build_cue_drafts(doc.paragraphs, max_chars=14)
    assert cues
    assert all("\n" not in cue.text for cue in cues)
    assert all(len(cue.text) <= 14 for cue in cues)


def test_director_plan_schema_and_qc_pass():
    doc = guanhanqing.read_script(script_text=SAMPLE_SCRIPT)
    plan = guanhanqing.build_director_plan(doc, duration_seconds=60, max_subtitle_chars=16)

    assert plan["schema_version"] == "director_plan.v1"
    assert plan["agent"]["id"] == "guanhanqing"
    assert plan["source"]["orientation"] == "portrait"
    assert plan["canvas"]["aspect_ratio"] == "9:16"
    assert plan["design_script"]["layout_policy"] == "portrait_vertical_stack"
    assert plan["qc"]["verdict"] == "pass"
    assert plan["self_check"]["verdict"] == "pass"
    assert len(plan["scenes"]) == 6
    assert plan["subtitle"]["policy"] == "single_line"
    assert plan["timeline"]["schema_version"] == "timeline.v1"
    assert plan["timeline"]["total_frames"] == 60 * plan["canvas"]["fps"]

    allowed = set(plan["digital_human"]["allowed_modes"])
    seen_modes: set[str] = set()
    prev_end = 0.0
    for scene in plan["scenes"]:
        assert scene["start"] >= prev_end - 0.05
        assert scene["end"] > scene["start"]
        prev_end = scene["end"]
        assert scene["visual_blocks"]
        for shot in scene["digital_human_shots"]:
            seen_modes.add(shot["digital_human_mode"])
            assert shot["digital_human_mode"] in allowed
        for cue in scene["subtitle_cues"]:
            assert "\n" not in cue["text"]
            assert len(cue["text"]) <= 16

    assert seen_modes <= {"full_screen", "bust_top_half", "head_corner"}
    assert {"full_screen", "bust_top_half"}.issubset(seen_modes)


def test_self_check_rejects_repeated_titles_and_weak_metrics():
    doc = guanhanqing.read_script(script_text=SAMPLE_SCRIPT)
    plan = guanhanqing.build_director_plan(doc, duration_seconds=60)
    for scene in plan["scenes"]:
        scene["title"] = "把成本拆开"
        scene["kicker"] = "成本模型"
    plan["scenes"][0]["visual_blocks"][0] = {
        "id": "scene_001_metrics",
        "type": "metric_card",
        "priority": 1,
        "content": {"items": ["一", "一年"]},
        "layout_hint": {
            "slot": "content_main",
            "variant": "metric_stack",
            "orientation": "portrait",
        },
        "timing_hint": {
            "start": plan["scenes"][0]["start"] + 0.2,
            "end": plan["scenes"][0]["end"] - 0.2,
        },
    }

    self_check = guanhanqing.self_check_director_plan(plan)

    assert self_check["verdict"] == "fail"
    assert any("repeated too often" in err for err in self_check["errors"])
    assert any("weak metric item" in err for err in self_check["errors"])


def test_landscape_plan_uses_landscape_design_script():
    doc = guanhanqing.read_script(script_text=SAMPLE_SCRIPT)
    plan = guanhanqing.build_director_plan(doc, duration_seconds=60, orientation="landscape")

    assert plan["qc"]["verdict"] == "pass"
    assert plan["self_check"]["verdict"] == "pass"
    assert plan["source"]["orientation"] == "landscape"
    assert plan["canvas"]["width"] == 1920
    assert plan["canvas"]["height"] == 1080
    assert plan["canvas"]["aspect_ratio"] == "16:9"
    assert plan["design_script"]["layout_policy"] == "landscape_split_stage"
    assert plan["design_script"]["mode_to_slot"]["bust_top_half"] == "host_bust_left"
    assert "host_left_panel" in plan["canvas"]["safe_areas"]
    assert plan["layout_library"]["library_id"] == "horizontal_video_1920x1080.v1"
    assert len(plan["layout_library"]["layouts"]) >= 8
    assert plan["timeline"]["fps"] == 30
    assert plan["timeline"]["total_frames"] == 1800
    assert len(plan["timeline"]["second_map"]) == 60
    assert plan["timeline"]["tracks"]["presenter"]

    bust_shots = [
        shot
        for scene in plan["scenes"]
        for shot in scene["digital_human_shots"]
        if shot["digital_human_mode"] == "bust_top_half"
    ]
    assert bust_shots
    assert all(shot["layout_id"] for shot in bust_shots)
    assert all(shot["content_slots"]["main"]["w"] >= 1000 for shot in bust_shots)

    full_shots = [
        shot
        for scene in plan["scenes"]
        for shot in scene["digital_human_shots"]
        if shot["digital_human_mode"] == "full_screen"
    ]
    assert full_shots
    assert all(shot["presenter_frame"]["w"] <= 960 for shot in full_shots)


def test_landscape_timeline_tracks_reference_layouts():
    doc = guanhanqing.read_script(script_text=SAMPLE_SCRIPT)
    plan = guanhanqing.build_director_plan(doc, duration_seconds=180, orientation="landscape")

    timeline = plan["timeline"]
    assert timeline["duration_seconds"] == 180
    assert timeline["total_frames"] == 5400
    assert len(timeline["second_map"]) == 180

    presenter_clip = timeline["tracks"]["presenter"][0]
    assert presenter_clip["frame_start"] == 0
    assert presenter_clip["frame_end"] > presenter_clip["frame_start"]
    assert presenter_clip["layout_id"].startswith("landscape_")
    assert presenter_clip["presenter_frame"]["w"] <= 960

    visual_clip = timeline["tracks"]["visuals"][0]
    assert visual_clip["layout_hint"]["layout_id"].startswith("landscape_")
    assert visual_clip["frame_end"] > visual_clip["frame_start"]


def test_director_plan_rejects_invalid_orientation():
    doc = guanhanqing.read_script(script_text=SAMPLE_SCRIPT)
    try:
        guanhanqing.build_director_plan(doc, duration_seconds=60, orientation="square")
    except ValueError as exc:
        assert "orientation" in str(exc)
    else:
        raise AssertionError("invalid orientation should raise ValueError")


def test_validate_rejects_illegal_mode():
    doc = guanhanqing.read_script(script_text=SAMPLE_SCRIPT)
    plan = guanhanqing.build_director_plan(doc, duration_seconds=60)
    plan["scenes"][0]["digital_human_shots"][0]["digital_human_mode"] = "small_center_full_body"

    qc = guanhanqing.validate_director_plan(plan)
    assert qc["verdict"] == "fail"
    assert any("illegal digital_human_mode" in err for err in qc["errors"])


def test_run_writes_director_plan(tmp_path):
    out = tmp_path / "director_plan.json"
    result = guanhanqing.run(
        script_text=SAMPLE_SCRIPT,
        out_path=out,
        duration_seconds=60,
        orientation="landscape",
        brain="rule",
    )

    assert result["qc"]["verdict"] == "pass"
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["agent"]["name"] == "关汉卿"
    assert data["source"]["orientation"] == "landscape"
    assert data["qc"]["verdict"] == "pass"


def test_run_uses_agy_first(tmp_path, monkeypatch):
    doc = guanhanqing.read_script(script_text=SAMPLE_SCRIPT)
    agy_plan = guanhanqing.build_director_plan(doc, duration_seconds=60)
    agy_plan["scenes"][0]["title"] = "AGY导演标题"
    calls = {"agy": 0, "deepseek": 0}

    def fake_agy(prompt, *, timeout_seconds):
        calls["agy"] += 1
        assert "director_plan.v1" in prompt
        return json.dumps(agy_plan, ensure_ascii=False)

    def fake_deepseek(*args, **kwargs):
        calls["deepseek"] += 1
        raise AssertionError("agy 成功时不应调用 deepseek")

    monkeypatch.setattr(guanhanqing, "call_agy", fake_agy)
    monkeypatch.setattr(guanhanqing, "call_deepseek", fake_deepseek)

    result = guanhanqing.run(
        script_text=SAMPLE_SCRIPT,
        out_path=tmp_path / "director_plan.json",
        duration_seconds=60,
        brain="auto",
    )

    assert calls == {"agy": 1, "deepseek": 0}
    assert result["plan"]["generation"]["brain"] == "agy"
    assert result["plan"]["scenes"][0]["title"] == "AGY导演标题"


def test_run_repairs_agy_plan_when_self_check_fails(tmp_path, monkeypatch):
    doc = guanhanqing.read_script(script_text=SAMPLE_SCRIPT)
    bad_plan = guanhanqing.build_director_plan(doc, duration_seconds=60, orientation="landscape")
    for scene in bad_plan["scenes"]:
        scene["title"] = "把成本拆开"
        scene["kicker"] = "成本模型"
    for block in bad_plan["scenes"][0]["visual_blocks"]:
        if block["type"] == "metric_card":
            block["content"]["items"] = ["一", "一年"]
            break

    fixed_plan = guanhanqing.build_director_plan(doc, duration_seconds=60, orientation="landscape")
    fixed_plan["scenes"][0]["title"] = "AGY返工后标题"
    calls: list[str] = []

    def fake_agy(prompt, *, timeout_seconds):
        calls.append(prompt)
        if len(calls) == 1:
            return json.dumps(bad_plan, ensure_ascii=False)
        assert "自检未通过" in prompt
        assert "weak metric item" in prompt
        return json.dumps(fixed_plan, ensure_ascii=False)

    def fake_deepseek(*args, **kwargs):
        raise AssertionError("agy 自我返工成功时不应调用 deepseek")

    monkeypatch.setattr(guanhanqing, "call_agy", fake_agy)
    monkeypatch.setattr(guanhanqing, "call_deepseek", fake_deepseek)

    result = guanhanqing.run(
        script_text=SAMPLE_SCRIPT,
        out_path=tmp_path / "director_plan.json",
        duration_seconds=60,
        orientation="landscape",
        brain="agy",
    )

    assert len(calls) == 2
    assert result["plan"]["self_check"]["verdict"] == "pass"
    assert result["plan"]["generation"]["self_repair_attempts"] == 1
    assert result["plan"]["generation"]["self_check_repaired"] is True
    assert result["plan"]["scenes"][0]["title"] == "AGY返工后标题"


def test_run_falls_back_to_deepseek_when_agy_fails(tmp_path, monkeypatch):
    doc = guanhanqing.read_script(script_text=SAMPLE_SCRIPT)
    ds_plan = guanhanqing.build_director_plan(doc, duration_seconds=60)
    ds_plan["scenes"][0]["title"] = "DeepSeek导演标题"
    calls = {"agy": 0, "deepseek": 0}

    def fake_agy(prompt, *, timeout_seconds):
        calls["agy"] += 1
        raise RuntimeError("agy busy")

    def fake_deepseek(prompt, *, system_prompt="", timeout_seconds):
        calls["deepseek"] += 1
        assert "关汉卿" in system_prompt
        return json.dumps(ds_plan, ensure_ascii=False)

    monkeypatch.setattr(guanhanqing, "call_agy", fake_agy)
    monkeypatch.setattr(guanhanqing, "call_deepseek", fake_deepseek)

    result = guanhanqing.run(
        script_text=SAMPLE_SCRIPT,
        out_path=tmp_path / "director_plan.json",
        duration_seconds=60,
        brain="auto",
    )

    assert calls == {"agy": 1, "deepseek": 1}
    assert result["plan"]["generation"]["brain"] == "deepseek"
    assert result["plan"]["scenes"][0]["title"] == "DeepSeek导演标题"

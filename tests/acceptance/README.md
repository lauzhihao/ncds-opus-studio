# Film backend acceptance

## Frame-first rebuild workflow

`test_film_rebuild_engine_workflow.py` is a self-contained assembled backend
acceptance test for `film_highlight_v1`. It does not mock production
collaborators. The test uses real `InstanceStore`, `InstanceRunner`, recipes,
film performers, FFmpeg, ffprobe, and filesystem artifacts.

The test:

1. creates eight-second 24 fps clean master and reference videos with stereo
   audio, a four-second aligned narration stem, and an ASS narration track;
2. validates a two-cut, frame-first EDL whose source segments are
   non-contiguous and no longer than five seconds;
3. drives `input -> source -> highlight_plan -> storyboard -> edl_review ->
   tts -> voice_review -> render -> quality -> download`, approving each real
   recipe review gate through `InstanceRunner`;
4. probes the final MP4 and checks exact frame count, CFR, audio, persisted
   artifact hashes, producer/input lineage, pinned render inputs, and the QA
   report; and
5. proves that zero-length and out-of-bounds EDLs fail at the assembled engine
   performer boundary with an actionable error.

Run it with:

```bash
.venv/bin/python -m pytest -q \
  tests/acceptance/test_film_rebuild_engine_workflow.py
```

The cases skip only when `ffmpeg` or `ffprobe` is unavailable. All media and
state are generated under pytest's temporary directory; the test never reads
`video-jobs/a2097350bd97` or files from `~/Downloads`.

## Film script source v2 workflow

`test_film_script_source_v2_workflow.py` is an assembled Shenkuo acceptance
test. It starts a separate Python collector process and uses real
ffmpeg/ffprobe plus temporary filesystem artifacts; OCR and Chinese correction
are deterministic dependency seams inside that child process. OCR fixtures run
with one worker so their ordered observations remain deterministic while the
production configuration uses the tiny model, 1 fps sampling, and parallel
workers.

It verifies the v2 `film_script_source` contract: immutable `raw_ocr`, clean
zh-CN `clean_script` JSON/SRT/TXT/report artifacts, cue provenance, temporal
merge boundaries, and deterministic `needs_review` fallback when correction is
unavailable or invalid. It also proves the collected text is exactly the
complete `clean.txt` content and no legacy `film_commentary` or
`film_localization` output is produced. Additional subprocess cases lock down
local temporal merging when every cleaner is unavailable, same-source raw OCR
cache reuse (including reuse of legacy small-model/2 fps raw cues by the newer
tiny-model/1 fps runtime), and retry/split recovery through executable fake
cleaner CLIs.

Run it with:

```bash
.venv/bin/python -m pytest -q \
  tests/acceptance/test_film_script_source_v2_workflow.py
```

The cases skip only when ffmpeg or ffprobe is unavailable.

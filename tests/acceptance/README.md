# Film subtitle OCR acceptance

`test_film_subtitle_workflow.py` is an opt-in backend acceptance test. It does
not mock ffmpeg, RapidOCR, the collector, Guiguzi processing, or filesystem
persistence.

The test:

1. renders deterministic Chinese subtitle frames and uses ffmpeg to encode
   them as an eight-second MP4 with three burned-in subtitle windows;
2. starts a separate Python process and calls the production film collector;
3. runs real 2 fps OCR and checks cross-frame deduplication plus
   `state/works/local/{id}/film_subtitles/` artifacts;
4. builds the deterministic production `film_commentary` baseline with the
   optional external Agent enhancement disabled, then checks dialogue filtering
   and the TXT/SRT/JSON/QA files; and
5. invokes film localization with only its external translation Agent replaced
   by a deterministic seam, proving that Liuyong consumes narration cues only
   and rejects the obsolete `film_script_split` mode.

Run it with:

```bash
NOF_RUN_FILM_OCR_ACCEPTANCE=1 \
  .venv/bin/python -m pytest -q \
  tests/acceptance/test_film_subtitle_workflow.py
```

Requirements:

- `ffmpeg` with an H.264 encoder;
- `rapidocr>=3.9.0`;
- `onnxruntime>=1.17,<2`; and
- a Chinese font in a common macOS/Linux location, or an explicit
  `NOF_FILM_ACCEPTANCE_FONT=/absolute/path/to/font` setting.

Without `NOF_RUN_FILM_OCR_ACCEPTANCE=1`, pytest reports one intentional skip.

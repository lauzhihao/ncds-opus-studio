"""ASR collect execution context for the legacy pipeline runner path."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from ncds_opus_core.common import cancel as _cancel


@dataclass
class PipelineAsrCollectRun:
    """Fast Shenkuo collect pass used by the web pipeline ASR node."""

    runner: Any
    job_id: str
    job_dir: Path
    inputs: dict[str, Any]
    flag_path: Path
    run_in_thread_cancellable: Callable[..., Awaitable[Any]]

    async def run(self) -> dict[str, Any]:
        from ncds_opus_factory.commands import shenkuo
        from ncds_opus_factory.common import tikhub_client

        urls = _collect_urls(self.inputs)
        if not urls:
            raise ValueError("inputs.urls is empty; paste media links into the INPUT node first")

        collect_dir = self.job_dir / "01_collect"
        collect_dir.mkdir(parents=True, exist_ok=True)

        collected_by_idx: dict[int, dict[str, Any]] = {}

        def push_collected() -> None:
            ordered = [collected_by_idx[k] for k in sorted(collected_by_idx)]
            self.runner._push_outputs_patch(self.job_id, "asr", "collected", ordered)

        for idx, url in enumerate(urls, start=1):
            self._on_progress(f"[{idx}/{len(urls)}] 解析作品链接")
            try:
                aweme_id = tikhub_client.resolve_aweme_id(url)
                if not aweme_id:
                    raise RuntimeError(f"解析不出 aweme_id（仅支持抖音链接/口令）：{url}")
                meta: dict[str, Any] = {}
                try:
                    meta = tikhub_client.extract_meta(
                        tikhub_client.fetch_one_video_detail(aweme_id)
                    )
                except Exception as exc:  # noqa: BLE001 — 元数据失败不阻塞主链路
                    self._on_progress(f"[{idx}/{len(urls)}] 元数据获取失败（不阻塞）：{exc}")
                entry = await self.run_in_thread_cancellable(
                    shenkuo.collect_one, self.flag_path,
                    aweme_id, collect_dir,
                    meta=meta, on_progress=self._on_progress,
                    do_audio=False, do_frames=False,
                )
                entry["index"] = idx
                entry["url"] = url
                collected_by_idx[idx] = entry
                push_collected()
                self._on_progress(f"[{idx}/{len(urls)}] 采集完成（文案/评论/数据）")
            except _cancel.TaskCancelled:
                # TaskCancelled 继承 RuntimeError，不能被下面的 except Exception 吞掉。
                raise
            except Exception as exc:  # noqa: BLE001 — 单条失败不拖垮整批
                msg = str(exc)
                first = msg.splitlines()[0] if msg.splitlines() else "未知错误"
                self._on_progress(f"[{idx}/{len(urls)}] 失败：{first}")
                collected_by_idx[idx] = {
                    "index": idx, "url": url, "aweme_id": "", "status": {}, "error": msg,
                }
                push_collected()
                continue

        collected = [collected_by_idx[k] for k in sorted(collected_by_idx)]
        succeeded = [e for e in collected if not e.get("error")]
        if not succeeded:
            raise RuntimeError(f"全部 {len(urls)} 个作品采集失败，详见各作品状态")
        return {"collected": collected, "collect_dir": str(collect_dir)}

    def _on_progress(self, text: str) -> None:
        self.runner._push_progress(self.job_id, "asr", text)


def _collect_urls(inputs: dict[str, Any]) -> list[str]:
    urls = list(inputs.get("urls") or [])
    if urls:
        return urls
    return [
        s["url"] for s in (inputs.get("shares") or [])
        if isinstance(s, dict) and isinstance(s.get("url"), str) and s["url"].strip()
    ]

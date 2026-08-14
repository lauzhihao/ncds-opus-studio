"""ASR collect execution context for the legacy pipeline runner path."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ncds_opus_core.common import cancel as _cancel

from ncds_opus_factory.server.domain_strategy import AsrCollectionPolicy
from ncds_opus_factory.server.pipeline_domain_strategies import pipeline_strategy

TRANSCRIPT_ONLY_COLLECT_MODE = "transcript_only"


def collect_entry_error(entry: dict[str, Any]) -> str | None:
    """Return a user-facing ASR collect error if an entry cannot feed RW."""
    if entry.get("error"):
        return str(entry["error"])

    status = entry.get("status") if isinstance(entry.get("status"), dict) else {}
    download_status = status.get("download")
    transcribe_status = str(status.get("transcribe") or "")
    if download_status == "failed":
        return "下载失败，未产出转写文案"
    if transcribe_status == "failed" or transcribe_status.startswith("error:"):
        return "转写失败，未产出文案"
    if transcribe_status == "empty":
        return None
    if str(entry.get("text") or "").strip():
        return None
    return None


def is_transcript_only_collect(inputs: dict[str, Any] | None) -> bool:
    """是否显式使用沈括纯文案快采模式。"""
    if not isinstance(inputs, dict):
        return False
    mode = str(inputs.get("collect_mode") or inputs.get("shenkuo_collect_mode") or "").strip().lower()
    if mode in {TRANSCRIPT_ONLY_COLLECT_MODE, "text_only", "transcript-only", "text-only"}:
        return True
    if inputs.get("transcript_only") is True:
        return True
    return False


@dataclass
class PipelineAsrCollectRun:
    """Fast Shenkuo collect pass used by the web pipeline ASR node."""

    runner: Any
    job_id: str
    job_dir: Path
    inputs: dict[str, Any]
    flag_path: Path
    run_in_thread_cancellable: Callable[..., Awaitable[Any]]
    policy: AsrCollectionPolicy | None = None

    async def run(self) -> dict[str, Any]:
        from ncds_opus_factory.commands import shenkuo
        from ncds_opus_factory.common import tikhub_client

        urls = _collect_urls(self.inputs)
        if not urls:
            raise ValueError("inputs.urls is empty; paste media links into the INPUT node first")

        collect_dir = self.job_dir / "01_collect"
        collect_dir.mkdir(parents=True, exist_ok=True)
        strategy = pipeline_strategy("asr", self.inputs.get("domain"))
        policy = self.policy
        if policy is None:
            if strategy.asr_policy is None:
                raise RuntimeError("ASR domain strategy has no collection policy")
            policy = strategy.asr_policy(self.inputs)
        if policy.progress_message:
            self._on_progress(policy.progress_message)

        collected_by_idx: dict[int, dict[str, Any]] = {}

        def push_collected() -> None:
            ordered = [collected_by_idx[k] for k in sorted(collected_by_idx)]
            self.runner._push_outputs_patch(self.job_id, "asr", "collected", ordered)

        for idx, url in enumerate(urls, start=1):
            self._on_progress(f"[{idx}/{len(urls)}] 解析作品链接")
            try:
                ref = tikhub_client.resolve_video_ref(url)
                if not ref:
                    raise RuntimeError(f"解析不出作品 id（支持抖音/TK/油管 单作品链接）：{url}")
                aweme_id = ref.video_id
                meta: dict[str, Any] = {}
                if ref.platform == "douyin":
                    try:
                        meta = tikhub_client.extract_meta(
                            tikhub_client.fetch_one_video_detail(aweme_id)
                        )
                    except Exception as exc:  # noqa: BLE001 — 元数据失败不阻塞主链路
                        self._on_progress(f"[{idx}/{len(urls)}] 元数据获取失败（不阻塞）：{exc}")
                else:
                    try:
                        meta = tikhub_client.fetch_video_ref_meta(ref)
                    except Exception as exc:  # noqa: BLE001 — 元数据失败不阻塞主链路
                        self._on_progress(f"[{idx}/{len(urls)}] 元数据获取失败（不阻塞）：{exc}")
                        meta = tikhub_client.video_ref_meta(ref)
                entry = await self.run_in_thread_cancellable(
                    shenkuo.collect_one, self.flag_path,
                    aweme_id, collect_dir,
                    meta=meta, on_progress=self._on_progress,
                    top_comments=policy.top_comments,
                    do_audio=policy.do_audio,
                    do_frames=policy.do_frames,
                    platform=ref.platform, source_url=ref.url,
                    # domain 透传:comedy 等多人对白 domain 靠它触发说话人分离,
                    # 且首趟就写进作品 manifest,enrich 二趟兜底可查。
                    author_domain=self.inputs.get("domain"),
                )
                entry["index"] = idx
                entry["url"] = url
                entry_error = collect_entry_error(entry)
                if entry_error:
                    entry["error"] = entry_error
                collected_by_idx[idx] = entry
                push_collected()
                if entry_error:
                    self._on_progress(f"[{idx}/{len(urls)}] 采集失败：{entry_error}")
                else:
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
        succeeded = [e for e in collected if not collect_entry_error(e)]
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

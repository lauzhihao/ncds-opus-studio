import os
import sys
import json
from http import HTTPStatus
from pathlib import Path
import dashscope
from dashscope import Files
from dashscope.audio.asr import Transcription

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ncds_opus_factory.common.capabilities.transcribe import read_dashscope_key  # noqa: E402

def get_api_key():
    """DashScope key：环境变量 DASHSCOPE_API_KEY > 仓库根 .env（~/.openclaw 已弃用）。"""
    api_key = read_dashscope_key()
    if not api_key:
        raise ValueError("缺少 DASHSCOPE_API_KEY：请设环境变量，或写进仓库根 .env（模板见 .env.example）")
    return api_key

def upload_file(file_path):
    """上传本地文件到 DashScope，返回远程 file_url"""
    abs_path = os.path.abspath(file_path)
    print(f"[ASR] 正在上传文件: {abs_path}")
    resp = Files.upload(file_path=abs_path, purpose="file-extract")
    if resp.status_code != HTTPStatus.OK:
        print(f"[FAIL] 上传失败: {resp.message}")
        return None
    uploaded = resp.output.get('uploaded_files', [])
    if not uploaded:
        print(f"[FAIL] 上传返回为空: {resp.output}")
        return None
    file_id = uploaded[0].get('file_id')
    print(f"[OK] 上传成功，file_id: {file_id}")
    # 通过 Files.get() 获取 OSS 临时访问 URL
    detail = Files.get(file_id)
    file_url = detail.output.get('url', '')
    if not file_url:
        print("[FAIL] 无法获取文件下载 URL")
        return None
    print("[OK] 获取到文件 URL")
    return file_id, file_url

def transcribe_file(file_path):
    api_key = get_api_key()
    dashscope.api_key = api_key

    abs_path = os.path.abspath(file_path)

    # 先上传文件到 DashScope
    upload_result = upload_file(abs_path)
    if not upload_result:
        return None
    file_id, file_url = upload_result

    # 尝试用上传后的 file URL 提交转写
    # DashScope Transcription 支持 dashscope:// 协议或 https:// URL
    # 如果 file_url 不可用，尝试用 file_id 构造
    print("[ASR] 正在提交转写任务...")

    try:
        task_response = Transcription.async_call(
            model='paraformer-v1',
            file_urls=[file_url],
        )
    except Exception as e:
        print(f"[FAIL] 提交异常: {e}")
        return None

    if task_response.status_code != HTTPStatus.OK:
        print(f"[FAIL] 提交失败: {task_response.message} (Code: {task_response.code})")
        return None

    task_id = task_response.output.task_id
    print(f"[OK] 任务已提交，ID: {task_id}")

    result_response = Transcription.wait(task=task_id)
    if result_response.status_code != HTTPStatus.OK:
        print(f"[FAIL] 任务失败: {result_response.message}")
        return None

    # 检查任务实际状态
    output = result_response.output
    task_status = getattr(output, 'task_status', None)
    if task_status == 'FAILED':
        code = getattr(output, 'code', 'unknown')
        msg = getattr(output, 'message', 'unknown')
        print(f"[FAIL] 转写失败: {code} - {msg}")
        return None

    print("[OK] 转写成功")
    return output

def extract_text(result):
    """从转写结果中提取纯文本"""
    texts = []
    results = getattr(result, 'results', None) or []
    for r in results:
        url = r.get('transcription_url', '')
        if url:
            # 下载转写结果 JSON
            import urllib.request
            try:
                with urllib.request.urlopen(url) as resp:
                    data = json.loads(resp.read().decode('utf-8'))
                    transcripts = data.get('transcripts', [])
                    for t in transcripts:
                        text = t.get('text', '')
                        if text:
                            texts.append(text)
            except Exception as e:
                print(f"[WARN] 下载转写结果失败: {e}")
    return '\n'.join(texts)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python3 tingwu_transcribe.py <视频/音频文件路径>")
        sys.exit(1)

    file_path = sys.argv[1]
    if not os.path.exists(file_path):
        print(f"[FAIL] 文件不存在: {file_path}")
        sys.exit(1)

    result = transcribe_file(file_path)

    if result:
        output_file = os.path.splitext(file_path)[0] + ".txt"

        # 尝试提取纯文本
        plain_text = extract_text(result)

        if plain_text:
            with open(output_file, "w", encoding="utf-8") as f:
                f.write(plain_text)
            print(f"[OK] 纯文本已保存至: {output_file}")
            print(f"\n--- 转写文本预览（前500字）---")
            print(plain_text[:500])
            print("-------------------\n")
        else:
            # fallback: 保存原始 JSON
            with open(output_file, "w", encoding="utf-8") as f:
                res_dict = json.loads(json.dumps(result, default=lambda o: o.__dict__))
                json.dump(res_dict, f, ensure_ascii=False, indent=2)
            print(f"[OK] 原始结果已保存至: {output_file}")

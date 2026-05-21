#!/usr/bin/env python3
"""
抖音视频下载器 - TikHub API
支持 modal_id、抖音链接下载
"""

import requests
import json
import os
import re
import sys
import time
from pathlib import Path

CHUNK_SIZE = 1024 * 1024
DOWNLOAD_RETRIES = 3
CONNECT_TIMEOUT = 15
READ_TIMEOUT = 60

TIKHUB_VIDEO_URL = "https://api.tikhub.io/api/v1/douyin/web/fetch_one_video"

def load_config():
    """加载配置文件"""
    config_path = os.path.expanduser("~/.openclaw/config.json")
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            return json.load(f)
    return {}

def get_token(token=None):
    """获取TikHub Token"""
    if token:
        return token
    config = load_config()
    token = config.get("tikhub_api_token")
    if not token:
        raise ValueError("❌ 缺少TikHub API Token\n\n请先配置：\n1. 访问 https://user.tikhub.io/register?referral_code=JtYTGCqJ 注册获取免费Token\n2. 在 ~/.openclaw/config.json 中添加:\n   {\"tikhub_api_token\": \"您的Token\"}")
    return token

def extract_modal_id(text):
    """从文本或URL中提取modal_id"""
    m = re.search(r'modal_id[=:]([\d]+)', text)
    if m:
        return m.group(1)
    m = re.search(r'^(\d{16,})$', text.strip())
    if m:
        return m.group(1)
    return None

def get_video_url_by_modal_id(modal_id, token):
    """通过modal_id获取视频下载链接"""
    url = f"{TIKHUB_VIDEO_URL}?aweme_id={modal_id}&need_anchor_info=false"
    headers = {
        "Authorization": f"Bearer {token}",
    }
    
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    
    text = resp.text
    
    # 提取第一个以 https://www.douyin.com/aweme/v1/play/ 开头的URL
    normalized = text.replace("\\/", "/")
    m = re.search(r'(https://www\.douyin\.com/aweme/v1/play/[^\s"<>\\]+)', normalized)
    if m:
        return m.group(1)
    
    return None

def get_video_info(user_input, token=None):
    """
    获取视频信息（仅返回地址，不下载）
    
    Args:
        user_input: 抖音链接或modal_id
        token: TikHub API Token
    
    Returns:
        dict: {modal_id, video_url}
    """
    token = get_token(token)
    
    # 提取modal_id
    modal_id = extract_modal_id(user_input)
    if not modal_id:
        raise ValueError(f"无法从输入中提取modal_id: {user_input}")
    
    # 获取视频URL
    video_url = get_video_url_by_modal_id(modal_id, token)
    if not video_url:
        raise ValueError(f"无法获取视频链接，请检查modal_id是否正确: {modal_id}")
    
    return {
        "modal_id": modal_id,
        "video_url": video_url
    }

def download_video(url, output_path=None, max_retries=DOWNLOAD_RETRIES):
    """下载视频。

    使用 streaming 写盘，避免大文件下载时一次性读取到内存。
    遇到中途断流时做有限重试，减少瞬时网络抖动导致的整体失败。
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://www.douyin.com/",
    }

    if not output_path:
        output_path = f"douyin_{hash(url) % 100000}.mp4"

    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    temp_file = output_file.with_suffix(f"{output_file.suffix}.part")
    last_error = None

    for attempt in range(1, max_retries + 1):
        try:
            with requests.get(
                url,
                headers=headers,
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
                stream=True,
            ) as resp:
                resp.raise_for_status()
                with open(temp_file, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=CHUNK_SIZE):
                        if chunk:
                            f.write(chunk)

            temp_file.replace(output_file)
            return str(output_file)
        except Exception as error:
            last_error = error
            if temp_file.exists():
                temp_file.unlink()
            if attempt >= max_retries:
                break
            print(f"  重试下载 ({attempt}/{max_retries - 1})...")
            time.sleep(min(attempt, 3))

    raise last_error

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("""
抖音视频下载器
=============

用法:
  python douyin_download.py "抖音链接或modal_id" [--download]
  加 --download 参数可直接下载视频，否则只返回视频下载url            

示例:
  python douyin_download.py "https://www.douyin.com/jingxuan?modal_id=7597329042169220398"
  python douyin_download.py "7597329042169220398" --download

获取免费Token: https://user.tikhub.io/register?referral_code=JtYTGCqJ
""")
        sys.exit(1)
    
    user_input = sys.argv[1]
    download = "--download" in sys.argv
    
    try:
        info = get_video_info(user_input)
        print(f"modal_id: {info['modal_id']}")
        print(f"视频地址: {info['video_url']}")
        
        if download:
            print("\n⬇️  下载中...")
            path = download_video(info['video_url'])
            print(f"✅ 下载完成: {path}")
    except Exception as e:
        print(f"\n❌ 错误: {e}")
        sys.exit(1)

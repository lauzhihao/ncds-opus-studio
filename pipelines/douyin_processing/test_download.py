#!/usr/bin/env python3
"""测试抖音视频下载"""

import os
import subprocess
import sys

def test_douyin_download():
    """测试抖音下载"""
    url = "https://v.douyin.com/JK6WgE-rCGo/"
    
    print(f"测试下载抖音视频: {url}")
    print("=" * 50)
    
    # 设置PATH
    env = os.environ.copy()
    env["PATH"] = f"/Users/ncds/Library/Python/3.9/bin:{env['PATH']}"
    
    # 先获取视频信息而不下载
    cmd = f'yt-dlp --get-title --get-description "{url}"'
    
    print(f"执行命令: {cmd}")
    
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, env=env)
        
        if result.returncode == 0:
            print("✓ 成功获取视频信息")
            print("\n视频标题和描述:")
            print("-" * 40)
            print(result.stdout)
            print("-" * 40)
            return True
        else:
            print(f"✗ 获取信息失败")
            print(f"错误输出: {result.stderr}")
            return False
            
    except Exception as e:
        print(f"✗ 执行出错: {e}")
        return False

if __name__ == "__main__":
    success = test_douyin_download()
    sys.exit(0 if success else 1)
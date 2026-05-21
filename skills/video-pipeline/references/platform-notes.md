# 各平台下载注意事项

## 抖音 (Douyin)

- 短链格式：`https://v.douyin.com/xxxxxx/`
- yt-dlp 需要 Chrome cookies 才能获取无水印视频
- 命令示例：`yt-dlp --cookies-from-browser chrome "URL"`
- 部分视频有地区限制，需中国 IP
- 短链会 302 跳转到长链，yt-dlp 会自动处理

## YouTube

- 标准链接：`https://www.youtube.com/watch?v=ID` 或短链 `https://youtu.be/ID`
- 中国大陆需要 VPN/代理才能访问
- 无需 cookies，直连下载
- 建议下载 720p 以节省空间：`-f "bestvideo[height<=720]+bestaudio/best[height<=720]"`
- 长视频转写耗时较长，whisper base 模型足够

## B站 (Bilibili)

- 标准链接：`https://www.bilibili.com/video/BVxxxxxx`
- 短链：`https://b23.tv/xxxxxx`
- 无需 cookies 即可下载大部分视频
- 会员专属/付费视频需要登录 cookies
- 注意：B站视频音视频分离，yt-dlp 会自动合并

## 小红书 (Xiaohongshu)

- 标准链接：`https://www.xiaohongshu.com/explore/ID`
- 短链：`https://xhslink.com/xxxxxx`
- 需要 Chrome cookies
- 命令示例：`yt-dlp --cookies-from-browser chrome "URL"`
- 小红书视频通常较短（15s-60s），转写速度快
- 注意：部分内容为图文非视频，yt-dlp 无法下载图文

## 通用注意事项

- 所有工具使用绝对路径调用，不依赖 PATH
- 下载失败时先检查网络，再检查 cookies 是否过期
- Cookies 过期后需要在 Chrome 中重新登录对应平台
- whisper 转写使用 base 模型 + 中文语言，平衡速度和质量

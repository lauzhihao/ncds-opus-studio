/// 短视频分享链接探测(纯 Dart,三端零成本复用)。
///
/// iOS 上「不读内容先预判」(`detectedPatterns(for: [.probableWebURL])`)的隐私语义,
/// 由原生薄 channel 提供(见 [ClipboardDetector]);本类只负责拿到文本后按平台域名匹配。
class ShareLinkParser {
  ShareLinkParser._();

  static const Map<String, String> _platforms = <String, String>{
    'douyin.com': '抖音',
    'iesdouyin.com': '抖音',
    'kuaishou.com': '快手',
    'bilibili.com': 'B站',
    'b23.tv': 'B站',
    'xiaohongshu.com': '小红书',
    'xhslink.com': '小红书',
    'youtube.com': 'YouTube',
    'youtu.be': 'YouTube',
  };

  static final RegExp _urlRe = RegExp(r'https?://[^\s]+');

  /// 返回首个命中的分享链接;无则 null。
  static ShareLink? detect(String text) {
    for (final RegExpMatch m in _urlRe.allMatches(text)) {
      final String url = m.group(0)!;
      final String host = Uri.tryParse(url)?.host ?? '';
      for (final MapEntry<String, String> e in _platforms.entries) {
        if (host.contains(e.key)) return ShareLink(platform: e.value, url: url);
      }
    }
    return null;
  }
}

class ShareLink {
  const ShareLink({required this.platform, required this.url});
  final String platform;
  final String url;
}

/// 剪贴板探测可换接口。
/// 平台实现:iOS = 薄 Swift channel 调 `detectedPatterns`(不读内容先预判);
/// Android = 判 MIME 类型 + 读取后用 [ShareLinkParser] 正则。
abstract class ClipboardDetector {
  /// 是否「可能」含可分享 URL(隐私友好,不一定读出明文)。
  Future<bool> hasShareableUrl();

  /// 读取剪贴板文本(可能触发系统提示)。
  Future<String?> readText();
}

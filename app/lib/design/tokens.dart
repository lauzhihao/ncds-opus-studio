import 'package:flutter/widgets.dart';

/// 设计 token —— 从现有 iOS App(ClaudeTrafficLight)的 SwiftUI 视觉提取并复刻。
/// App 仅亮色模式;语义层级靠暖色明度差(sand 底 → ivory 卡 → ink 文字)+ 柔和阴影,
/// 不依赖深浅模式切换。所有取值对应原 Swift 的 Color.ct* 字面量。
class AppColors {
  AppColors._();

  // —— 基底 ——
  static const Color sand = Color(0xFFE8D9BD); // 全局页面背景 (ctTan)
  static const Color ivory = Color(0xFFF7F0E0); // 卡片/面板背景 (ctCard)
  static const Color housing = Color(0xFF29211C); // 深色容器/灯壳 (ctHousing)

  // —— 文字(墨棕) ——
  static const Color ink = Color(0xFF423021); // 主文字 (ctInk),也是阴影染色源
  static Color get inkMuted => ink.withValues(alpha: 0.55); // 次文字/说明
  static Color get inkFaint => ink.withValues(alpha: 0.30); // 禁用/占位

  // —— 品牌强调 ——
  static const Color orange = Color(0xFFD9732E); // 主强调/主按钮 (ctOrange)
  static const Color amber = Color(0xFFFF8000); // 推理/警告 (ctAmber)

  // —— 红绿灯状态(iOS 系统色) ——
  static const Color statusRed = Color(0xFFFF3B30);
  static const Color statusYellow = Color(0xFFFF8000);
  static const Color statusGreen = Color(0xFF34C759);
  static const Color statusAdopted = Color(0xFF348055); // 已采用/成功

  // —— 阴影染色 ——
  static Color get cardShadow => ink.withValues(alpha: 0.08);
  static Color get cardShadowStrong => ink.withValues(alpha: 0.12);

  /// 标签/徽章底:取语义色 @14%,保持视觉层级。
  static Color chipBg(Color tint) => tint.withValues(alpha: 0.14);

  // —— Agent 主题色 ——
  static const Color accentWolong = Color(0xFFB8332E); // 卧龙/操盘手
  static const Color accentCollect = Color(0xFF2E7373); // 采集
  static const Color accentTopic = Color(0xFF4C478C); // 选题
  static const Color accentScript = Color(0xFF577340); // 编剧
  static const Color accentArt = Color(0xFF406694); // 美术
  static const Color accentSound = Color(0xFFA84C66); // 声音
}

/// 间距:基于 2pt 网格(对齐原 SwiftUI 取值)。
class AppSpacing {
  AppSpacing._();
  static const double xs = 4;
  static const double s = 6;
  static const double chipH = 7; // 芯片横向内距
  static const double m = 12; // 卡内区块间距 / 页面顶部
  static const double cardInner = 16; // 卡片标准四边内距
  static const double pageH = 20; // 页面水平边距
  static const double block = 22; // 主区块 VStack 间距
  static const double pageBottom = 28;
  static const double pageTop = 12;
  static const double buttonV = 15; // 决策按钮纵向内距
}

/// 圆角:18pt 主卡片为标志性圆角;Capsule 用 StadiumBorder。
class AppRadii {
  AppRadii._();
  static const double thumb = 6;
  static const double image = 10;
  static const double capture = 12;
  static const double button = 16;
  static const double card = 18;
  static const Radius cardR = Radius.circular(card);
  static const Radius buttonR = Radius.circular(button);
}

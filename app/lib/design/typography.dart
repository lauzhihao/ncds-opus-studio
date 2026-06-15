import 'package:flutter/widgets.dart';
import 'tokens.dart';

/// 字体族(对标网页 _tokens.scss):
/// - Fraunces 衬线(display/标题/衬线正文),中文回退宋体 SC
/// - Geist 无衬线(body/UI),中文回退苹方 SC
/// - GeistMono 等宽(代码/URL/ID/日志),中文回退苹方 SC
class AppFonts {
  AppFonts._();
  static const String serif = 'Fraunces';
  static const String sans = 'Geist';
  static const String mono = 'GeistMono';
  static const List<String> serifFallback = <String>['Songti SC'];
  static const List<String> sansFallback = <String>['PingFang SC'];
}

/// 字号阶(命名 + 字体 + 字号 + 字重),复刻自 iOS CTTypography。
/// 默认色为 ink;次级/反白文字用 .copyWith(color: ...) 在用处覆盖。
class AppTypography {
  AppTypography._();

  // —— 衬线展示族(Fraunces) ——
  static const TextStyle displayTitle = TextStyle(
      fontFamily: AppFonts.serif, fontFamilyFallback: AppFonts.serifFallback,
      fontSize: 24, height: 1.15, fontWeight: FontWeight.w700, color: AppColors.ink);
  static const TextStyle titleL = TextStyle(
      fontFamily: AppFonts.serif, fontFamilyFallback: AppFonts.serifFallback,
      fontSize: 18, fontWeight: FontWeight.w700, color: AppColors.ink);
  static const TextStyle titleM = TextStyle(
      fontFamily: AppFonts.serif, fontFamilyFallback: AppFonts.serifFallback,
      fontSize: 17, fontWeight: FontWeight.w700, color: AppColors.ink);
  static const TextStyle bodySerif = TextStyle(
      fontFamily: AppFonts.serif, fontFamilyFallback: AppFonts.serifFallback,
      fontSize: 16, height: 1.5, fontWeight: FontWeight.w400, color: AppColors.ink);
  static const TextStyle bodySerifS = TextStyle(
      fontFamily: AppFonts.serif, fontFamilyFallback: AppFonts.serifFallback,
      fontSize: 14, height: 1.5, fontWeight: FontWeight.w400, color: AppColors.ink);
  static const TextStyle buttonLabel = TextStyle(
      fontFamily: AppFonts.serif, fontFamilyFallback: AppFonts.serifFallback,
      fontSize: 17, fontWeight: FontWeight.w700);

  // —— 无衬线 UI 族(Geist) ——
  static const TextStyle navTitle = TextStyle(
      fontFamily: AppFonts.sans, fontFamilyFallback: AppFonts.sansFallback,
      fontSize: 17, fontWeight: FontWeight.w600, color: AppColors.ink);
  static const TextStyle bodyInput = TextStyle(
      fontFamily: AppFonts.sans, fontFamilyFallback: AppFonts.sansFallback,
      fontSize: 16, fontWeight: FontWeight.w400, color: AppColors.ink);
  static const TextStyle body = TextStyle(
      fontFamily: AppFonts.sans, fontFamilyFallback: AppFonts.sansFallback,
      fontSize: 15, fontWeight: FontWeight.w400, color: AppColors.ink);
  static const TextStyle subhead = TextStyle(
      fontFamily: AppFonts.sans, fontFamilyFallback: AppFonts.sansFallback,
      fontSize: 14, fontWeight: FontWeight.w600, color: AppColors.ink);
  static const TextStyle label = TextStyle(
      fontFamily: AppFonts.sans, fontFamilyFallback: AppFonts.sansFallback,
      fontSize: 13, fontWeight: FontWeight.w500, color: AppColors.ink);
  static const TextStyle caption = TextStyle(
      fontFamily: AppFonts.sans, fontFamilyFallback: AppFonts.sansFallback,
      fontSize: 12, fontWeight: FontWeight.w400, color: AppColors.ink);
  static const TextStyle caption2 = TextStyle(
      fontFamily: AppFonts.sans, fontFamilyFallback: AppFonts.sansFallback,
      fontSize: 11, fontWeight: FontWeight.w400, color: AppColors.ink);
  static const TextStyle footnote = TextStyle(
      fontFamily: AppFonts.sans, fontFamilyFallback: AppFonts.sansFallback,
      fontSize: 10, fontWeight: FontWeight.w400, color: AppColors.ink);

  // —— 等宽族(GeistMono) ——
  static const TextStyle mono = TextStyle(
      fontFamily: AppFonts.mono, fontFamilyFallback: AppFonts.sansFallback,
      fontSize: 13, fontWeight: FontWeight.w400, color: AppColors.ink);
  static const TextStyle monoS = TextStyle(
      fontFamily: AppFonts.mono, fontFamilyFallback: AppFonts.sansFallback,
      fontSize: 12, fontWeight: FontWeight.w400, color: AppColors.ink);
  static const TextStyle monoXS = TextStyle(
      fontFamily: AppFonts.mono, fontFamilyFallback: AppFonts.sansFallback,
      fontSize: 11, fontWeight: FontWeight.w400, color: AppColors.ink);
}

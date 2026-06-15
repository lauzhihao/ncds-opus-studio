import 'dart:ui' show ImageFilter;
import 'package:flutter/material.dart';
import 'tokens.dart';

/// 自绘玻璃面板(Liquid Glass 风格)。
///
/// - [enableGlass] = true:BackdropFilter 模糊 + 半透明叠色 + 白色高光描边(Impeller 上最佳)。
/// - [enableGlass] = false:降级为静态半透明纯色面板(无模糊)——用于低端机 / 暂不支持的平台
///   (Web、鸿蒙 shader 待 PoC 验证)。
///
/// 设计原则:玻璃效果收敛在此单一组件,限制模糊半径与叠加层数,规避 Impeller blur 已知
/// 性能/白边 issue。后续若要更高保真,可平滑替换为 liquid_glass_renderer 着色器实现而不动调用方。
class LiquidGlass extends StatelessWidget {
  const LiquidGlass({
    super.key,
    required this.child,
    this.enableGlass = true,
    this.blurSigma = 18,
    this.tint,
    this.borderRadius = const BorderRadius.all(AppRadii.cardR),
    this.padding = const EdgeInsets.all(AppSpacing.cardInner),
  });

  final Widget child;
  final bool enableGlass;
  final double blurSigma;
  final Color? tint;
  final BorderRadius borderRadius;
  final EdgeInsetsGeometry padding;

  @override
  Widget build(BuildContext context) {
    final Color base = tint ?? AppColors.ivory;
    final Widget surface = Container(
      padding: padding,
      decoration: BoxDecoration(
        color: base.withValues(alpha: enableGlass ? 0.55 : 0.92),
        borderRadius: borderRadius,
        border: Border.all(
          color: Colors.white.withValues(alpha: enableGlass ? 0.55 : 0.0),
          width: 1,
        ),
        boxShadow: [
          BoxShadow(color: AppColors.cardShadow, blurRadius: 6, offset: const Offset(0, 3)),
        ],
      ),
      child: child,
    );

    if (!enableGlass) {
      return ClipRRect(borderRadius: borderRadius, child: surface);
    }
    return ClipRRect(
      borderRadius: borderRadius,
      child: BackdropFilter(
        filter: ImageFilter.blur(sigmaX: blurSigma, sigmaY: blurSigma),
        child: surface,
      ),
    );
  }
}

/// 毛玻璃磨砂面(导航栏 / 弹层遮罩底):内容从下方透出。与 [LiquidGlass] 同源
/// (BackdropFilter 模糊 + 半透明叠色),但无圆角/阴影,适合全宽贴边铺满。
///
/// 用法:
/// - 顶部导航栏:`AppBar(backgroundColor: transparent, flexibleSpace: GlassBar(bottomHairline: true))`
///   配 `Scaffold(extendBodyBehindAppBar: true)` —— 内容滚动时从磨砂条下透出。
/// - 弹层遮罩:透明路由上 `Positioned.fill(child: GlassBar(tint: Colors.black, glassOpacity: .5))`。
///
/// [enableGlass] = false 时降级为静态半透明(低端机 / 暂不支持平台),不模糊。
class GlassBar extends StatelessWidget {
  const GlassBar({
    super.key,
    this.child,
    this.enableGlass = true,
    this.blurSigma = 20,
    this.tint,
    this.glassOpacity = 0.6,
    this.solidOpacity = 0.96,
    this.bottomHairline = false,
  });

  final Widget? child;
  final bool enableGlass;
  final double blurSigma;
  final Color? tint;
  final double glassOpacity; // 玻璃态叠色不透明度
  final double solidOpacity; // 降级态叠色不透明度
  final bool bottomHairline; // 底部 0.5px 分隔线(导航栏用,弱化边界)

  @override
  Widget build(BuildContext context) {
    final Color base = tint ?? AppColors.sand;
    final Widget fill = DecoratedBox(
      decoration: BoxDecoration(
        color: base.withValues(alpha: enableGlass ? glassOpacity : solidOpacity),
        border: bottomHairline
            ? Border(bottom: BorderSide(color: AppColors.ink.withValues(alpha: 0.06), width: 0.5))
            : null,
      ),
      child: child,
    );
    if (!enableGlass) return fill;
    return ClipRect(
      child: BackdropFilter(
        filter: ImageFilter.blur(sigmaX: blurSigma, sigmaY: blurSigma),
        child: fill,
      ),
    );
  }
}

/// iOS 风格渐隐磨砂顶栏背景:用作 AppBar 的 `flexibleSpace`(配 `Scaffold.extendBodyBehindAppBar: true`)。
///
/// - 顶部(状态栏区)近实色叠色 → 护住时间/电量,正文不与之打架。
/// - 向下渐隐到透明 + 全程 `BackdropFilter` 模糊 → 内容滚动时从磨砂里"虚化浮现",底缘柔和过渡。
/// - 满屏正文页用它替代 [GlassBar](`GlassBar` 均匀半透,正文会穿透;此处要的是渐隐压住正文)。
///
/// [enableGlass]=false 降级为纯实色(低端机 / 暂不支持平台),仍护住状态栏。
class FrostedHeader extends StatelessWidget {
  const FrostedHeader({super.key, this.enableGlass = true, this.blurSigma = 18, this.tint});

  final bool enableGlass;
  final double blurSigma;
  final Color? tint;

  @override
  Widget build(BuildContext context) {
    final Color base = tint ?? AppColors.sand;
    // 用 Container(非 DecoratedBox/ColoredBox):AppBar 的 flexibleSpace 给的是松约束,
    // DecoratedBox 无子会塌成 0 尺寸(整条全透明,状态栏压到正文上);Container 无子会撑满。
    if (!enableGlass) return Container(color: base);
    final Widget fill = Container(
      decoration: BoxDecoration(
        gradient: LinearGradient(
          begin: Alignment.topCenter,
          end: Alignment.bottomCenter,
          // 大部分近实色(护住状态栏 + 钉在其上的 hero 文字),仅底缘渐隐 + 模糊,
          // 让滚过的内容在底边柔和虚化过渡。
          colors: [
            base.withValues(alpha: 0.96),
            base.withValues(alpha: 0.95),
            base.withValues(alpha: 0.0),
          ],
          stops: const [0.0, 0.88, 1.0],
        ),
      ),
    );
    return ClipRect(
      child: BackdropFilter(
        filter: ImageFilter.blur(sigmaX: blurSigma, sigmaY: blurSigma),
        child: fill,
      ),
    );
  }
}

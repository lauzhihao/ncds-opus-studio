import 'package:flutter/material.dart';
import '../tokens.dart';

/// 标准卡片:18pt 圆角 + 象牙底(或深色 housing 底)+ 柔和墨棕阴影。
/// 复刻原 SwiftUI 的 resultCard / roundCard / subtitleBeatCard。
class AppCard extends StatelessWidget {
  const AppCard({
    super.key,
    required this.child,
    this.dark = false,
    this.padding = const EdgeInsets.all(AppSpacing.cardInner),
  });

  final Widget child;
  final bool dark;
  final EdgeInsetsGeometry padding;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: padding,
      decoration: BoxDecoration(
        color: dark ? AppColors.housing : AppColors.ivory,
        borderRadius: const BorderRadius.all(AppRadii.cardR),
        boxShadow: [
          BoxShadow(
            color: dark ? AppColors.cardShadowStrong : AppColors.cardShadow,
            blurRadius: 6,
            offset: const Offset(0, 3),
          ),
        ],
      ),
      child: child,
    );
  }
}

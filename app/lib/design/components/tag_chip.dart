import 'package:flutter/material.dart';
import '../tokens.dart';
import '../typography.dart';

/// 标签/徽章芯片:Capsule + 语义色 @14% 底 + 原色前景。
/// 复刻原 SwiftUI 的 tagChip / stageChip。
class TagChip extends StatelessWidget {
  const TagChip({
    super.key,
    required this.label,
    this.icon,
    this.tint = AppColors.orange,
  });

  final String label;
  final IconData? icon;
  final Color tint;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: AppSpacing.chipH, vertical: 3),
      decoration: ShapeDecoration(color: AppColors.chipBg(tint), shape: const StadiumBorder()),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          if (icon != null) ...[
            Icon(icon, size: 11, color: tint),
            const SizedBox(width: 3),
          ],
          Text(label, style: AppTypography.label.copyWith(color: tint)),
        ],
      ),
    );
  }
}

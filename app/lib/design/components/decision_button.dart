import 'package:flutter/material.dart';
import '../tokens.dart';
import '../typography.dart';

/// 决策按钮(批准/打回等)。filled=主按钮(渐变+渐进阴影),filled=false=次按钮(描边幽灵)。
/// 按下 scaleEffect 0.97,复刻原 SwiftUI 的 FilledDecisionButton / GhostDecisionButton。
/// [icon] 复刻 SwiftUI `Label(text, systemImage:)`——图标在文字左侧,与文字同色。
class DecisionButton extends StatefulWidget {
  const DecisionButton({
    super.key,
    required this.label,
    required this.onPressed,
    this.icon,
    this.filled = true,
    this.tint = AppColors.orange,
  });

  final String label;
  final VoidCallback onPressed;
  final IconData? icon;
  final bool filled;
  final Color tint;

  @override
  State<DecisionButton> createState() => _DecisionButtonState();
}

class _DecisionButtonState extends State<DecisionButton> {
  bool _pressed = false;

  void _setPressed(bool v) => setState(() => _pressed = v);

  @override
  Widget build(BuildContext context) {
    final bool filled = widget.filled;
    final Color tint = widget.tint;
    return GestureDetector(
      onTapDown: (_) => _setPressed(true),
      onTapUp: (_) => _setPressed(false),
      onTapCancel: () => _setPressed(false),
      onTap: widget.onPressed,
      child: AnimatedScale(
        scale: _pressed ? 0.97 : 1.0,
        duration: const Duration(milliseconds: 160),
        curve: Curves.easeOut,
        child: Container(
          width: double.infinity,
          alignment: Alignment.center,
          padding: const EdgeInsets.symmetric(vertical: AppSpacing.buttonV),
          decoration: BoxDecoration(
            borderRadius: const BorderRadius.all(AppRadii.buttonR),
            color: filled ? null : AppColors.ivory,
            gradient: filled
                ? LinearGradient(
                    begin: Alignment.topCenter,
                    end: Alignment.bottomCenter,
                    colors: [tint.withValues(alpha: 0.92), tint.withValues(alpha: 0.78)],
                  )
                : null,
            border: filled ? null : Border.all(color: tint.withValues(alpha: 0.5), width: 1.5),
            boxShadow: filled
                ? [
                    BoxShadow(
                      color: tint.withValues(alpha: _pressed ? 0.15 : 0.4),
                      blurRadius: 9,
                      offset: const Offset(0, 5),
                    ),
                  ]
                : null,
          ),
          child: () {
            final Color fg = filled ? AppColors.ivory : tint;
            final Widget label =
                Text(widget.label, style: AppTypography.buttonLabel.copyWith(color: fg));
            if (widget.icon == null) return label;
            return Row(
              mainAxisSize: MainAxisSize.min,
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                Icon(widget.icon, size: 18, color: fg),
                const SizedBox(width: AppSpacing.s),
                label,
              ],
            );
          }(),
        ),
      ),
    );
  }
}

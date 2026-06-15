import 'package:flutter/material.dart';
import '../tokens.dart';

enum LightColor { red, amber, green }

extension LightColorX on LightColor {
  Color get color {
    switch (this) {
      case LightColor.red:
        return AppColors.statusRed;
      case LightColor.amber:
        return AppColors.statusYellow;
      case LightColor.green:
        return AppColors.statusGreen;
    }
  }
}

/// 红绿灯灯泡:RadialGradient 玻璃高光 + 可选呼吸动效(scale 0.85→1.15 / opacity 0.6→1,周期 2.2s)。
/// 复刻原 SwiftUI 的 TrafficBulb / BreathingDot。
class StatusLight extends StatefulWidget {
  const StatusLight({
    super.key,
    required this.color,
    this.diameter = 30,
    this.breathing = true,
  });

  final LightColor color;
  final double diameter;
  final bool breathing;

  @override
  State<StatusLight> createState() => _StatusLightState();
}

class _StatusLightState extends State<StatusLight> with SingleTickerProviderStateMixin {
  late final AnimationController _controller;

  @override
  void initState() {
    super.initState();
    _controller = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 2200),
    );
    if (widget.breathing) _controller.repeat(reverse: true);
  }

  @override
  void didUpdateWidget(StatusLight oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (widget.breathing && !_controller.isAnimating) {
      _controller.repeat(reverse: true);
    } else if (!widget.breathing && _controller.isAnimating) {
      _controller.stop();
      _controller.value = 1;
    }
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final Color c = widget.color.color;
    if (!widget.breathing) return _bulb(c, 1, 1);
    return AnimatedBuilder(
      animation: _controller,
      builder: (context, _) {
        final double t = Curves.easeInOut.transform(_controller.value);
        return _bulb(c, 0.85 + 0.30 * t, 0.6 + 0.4 * t);
      },
    );
  }

  Widget _bulb(Color c, double scale, double opacity) {
    final double d = widget.diameter;
    return SizedBox(
      width: d,
      height: d,
      child: Center(
        child: Transform.scale(
          scale: scale,
          child: Container(
            width: d,
            height: d,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              gradient: RadialGradient(
                center: const Alignment(-0.3, -0.4),
                colors: [
                  Colors.white.withValues(alpha: 0.55 * opacity),
                  c.withValues(alpha: opacity),
                ],
                stops: const [0.0, 0.7],
              ),
              boxShadow: [
                BoxShadow(color: c.withValues(alpha: 0.7 * opacity), blurRadius: 8, spreadRadius: 1),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

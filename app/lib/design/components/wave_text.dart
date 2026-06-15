// 逐字母波浪文字 —— 1:1 复刻原生 SwiftUI 的 WaveText（ContentView.swift）。
// 把文字拆成单个字母,用一条从左向右滚动的正弦波依次把每个字母「顶起放大」,
// 形成逐字母依次变大的波浪效果。animated=false 时静止平铺(用于 Idle)。
//
// 每字母:lift = max(0, sin(t/period*2π - i*stagger))(animated=false 恒 0)
//   scale  = 1 + amplitude * lift     anchor 底部(.bottom)
//   offset = y: -lift * 3             配合放大轻微上跳,更有弹性
// 字母间距 spacing = 2。

import 'dart:math' as math;

import 'package:flutter/widgets.dart';

class WaveText extends StatefulWidget {
  const WaveText({
    super.key,
    required this.text,
    required this.style,
    this.animated = true,
    this.period = 1.6, // 波浪滚过一轮的秒数(越小越急促)
    this.amplitude = 0.45, // 放大幅度:波峰处字母放大到 1 + amplitude 倍
    this.stagger = 0.6, // 相邻字母相位差(弧度,越大波越「陡」)
  });

  final String text;

  /// 文本样式(含颜色、Fraunces 字族等);由调用方传入,与原生 .font/.foregroundStyle 对应。
  final TextStyle style;

  final bool animated;
  final double period;
  final double amplitude;
  final double stagger;

  @override
  State<WaveText> createState() => _WaveTextState();
}

class _WaveTextState extends State<WaveText> with SingleTickerProviderStateMixin {
  late final AnimationController _controller;

  @override
  void initState() {
    super.initState();
    _controller = AnimationController(
      vsync: this,
      duration: const Duration(seconds: 1),
    );
    if (widget.animated) _controller.repeat();
  }

  @override
  void didUpdateWidget(WaveText oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (widget.animated && !_controller.isAnimating) {
      _controller.repeat();
    } else if (!widget.animated && _controller.isAnimating) {
      _controller.stop();
    }
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    if (!widget.animated) return _row(0); // 静止:lift 恒 0
    return AnimatedBuilder(
      animation: _controller,
      builder: (context, _) {
        final double t = _controller.lastElapsedDuration == null
            ? _controller.value
            : _controller.lastElapsedDuration!.inMicroseconds / Duration.microsecondsPerSecond;
        return _row(t);
      },
    );
  }

  Widget _row(double t) {
    final List<String> chars = widget.text.split('');
    return Row(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.end, // 字母底部对齐,放大锚点在底
      children: [
        for (int i = 0; i < chars.length; i++) ...[
          if (i > 0) const SizedBox(width: 2),
          _letter(chars[i], t, i),
        ],
      ],
    );
  }

  Widget _letter(String ch, double t, int i) {
    final double phase = t / widget.period * 2 * math.pi - i * widget.stagger;
    final double lift = widget.animated ? math.max(0.0, math.sin(phase)) : 0.0;
    return Transform.translate(
      offset: Offset(0, -lift * 3),
      child: Transform.scale(
        scale: 1 + widget.amplitude * lift,
        alignment: Alignment.bottomCenter, // anchor: .bottom
        child: Text(ch, style: widget.style),
      ),
    );
  }
}

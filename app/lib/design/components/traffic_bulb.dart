// 红绿灯灯泡 —— 1:1 复刻原生 SwiftUI 的 TrafficBulb（ContentView.swift）。
// 玻璃灯罩质感(左上角高光 + 白描边)+ 三种动效:常亮 / 呼吸 / 闪烁。
// 用 AnimationController 逐帧驱动;常亮(solid)与熄灭(isOn=false)不跑动画,省电。
//
// 亮度 level(0~1)统一映射各视觉量:
//   fill   = color @ (0.14 + 0.86 * level)         熄灭也留底色,像没通电的灯罩
//   高光   = white @ (0.55 * level) → clear         center(0.35,0.30) endRadius d*0.55
//   描边   = white @ (0.05 + 0.28 * level)          width 1.5
//   scale  = 0.95 + 0.06 * level                    呼吸时轻微胀缩
//   shadow = color @ (0.9 * level)  blur d*0.4*level

import 'dart:math' as math;

import 'package:flutter/widgets.dart';

/// 灯泡动效,与固件/原生三种动画一一对应:
/// [breathe] 推理=呼吸,[blink] 等待=快闪,[solid] 空闲=常亮。
enum BulbMotion { breathe, blink, solid }

class TrafficBulb extends StatefulWidget {
  const TrafficBulb({
    super.key,
    required this.color,
    required this.isOn,
    required this.motion,
    this.period = 1.0, // 呼吸/闪烁周期秒数(越小越急促)
    this.dimTo = 1.0, // 呼吸最暗时亮度(0~1,越小起伏越明显);闪烁/常亮不用
    this.diameter = 56, // 主灯 30,agent 灯 16
  });

  /// 点亮色;熄灭时仍以此色 @14% 作底。
  final Color color;

  /// 是否通电点亮;false 时 level 恒 0(留 0.14 底色)。
  final bool isOn;

  final BulbMotion motion;
  final double period;
  final double dimTo;
  final double diameter;

  @override
  State<TrafficBulb> createState() => _TrafficBulbState();
}

class _TrafficBulbState extends State<TrafficBulb> with SingleTickerProviderStateMixin {
  /// 闪烁亮态占比,和固件/原生一致。
  static const double _blinkDuty = 0.6;

  late final AnimationController _controller;

  /// 仅在点亮且非常亮时才需要逐帧驱动。
  bool get _animating => widget.isOn && widget.motion != BulbMotion.solid;

  @override
  void initState() {
    super.initState();
    // 用固定 1s 周期持续跑,真正的相位由 period 在 _level 里换算——
    // 这样切换 motion/period 不必重建 controller。
    _controller = AnimationController(
      vsync: this,
      duration: const Duration(seconds: 1),
    );
    if (_animating) _controller.repeat();
  }

  @override
  void didUpdateWidget(TrafficBulb oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (_animating && !_controller.isAnimating) {
      _controller.repeat();
    } else if (!_animating && _controller.isAnimating) {
      _controller.stop();
    }
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  /// 当前亮度:熄灭=0,常亮=1,呼吸=dimTo~1 正弦,闪烁=0/1 方波。
  /// t 为累计秒数(controller 每秒 +1,这里乘出真实时间)。
  double _level(double tSeconds) {
    if (!widget.isOn) return 0;
    switch (widget.motion) {
      case BulbMotion.solid:
        return 1;
      case BulbMotion.breathe:
        final double s = (math.sin(2 * math.pi * tSeconds / widget.period) + 1) / 2;
        return widget.dimTo + (1 - widget.dimTo) * s;
      case BulbMotion.blink:
        final double phase = (tSeconds / widget.period) % 1;
        return phase < _blinkDuty ? 1 : 0;
    }
  }

  @override
  Widget build(BuildContext context) {
    if (!_animating) {
      // 常亮 level=1,熄灭 level=0;静态一帧,不重绘。
      return _bulb(widget.isOn ? 1 : 0);
    }
    return AnimatedBuilder(
      animation: _controller,
      builder: (context, _) {
        // controller.value 0~1 对应每秒;乘大基数得到平滑累计秒数。
        final double tSeconds = _controller.lastElapsedDuration == null
            ? _controller.value
            : _controller.lastElapsedDuration!.inMicroseconds / Duration.microsecondsPerSecond;
        return _bulb(_level(tSeconds));
      },
    );
  }

  Widget _bulb(double level) {
    final double d = widget.diameter;
    final Color c = widget.color;
    // 底色填充 + 阴影 + 描边为一层;玻璃高光作为 overlay 叠在上面
    // (对齐原生 .fill(...).overlay(Circle().fill(RadialGradient)).overlay(stroke))。
    return SizedBox(
      width: d,
      height: d,
      child: Center(
        child: Transform.scale(
          scale: 0.95 + 0.06 * level,
          child: Container(
            width: d,
            height: d,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              color: c.withValues(alpha: 0.14 + 0.86 * level),
              boxShadow: [
                BoxShadow(
                  color: c.withValues(alpha: 0.9 * level),
                  blurRadius: d * 0.4 * level,
                ),
              ],
            ),
            // 高光层:RadialGradient 白→透明,center(0.35,0.30) 即 Alignment(-0.3,-0.4);
            // Flutter radius = fraction * shortestSide(=d),原生 endRadius=d*0.55 → fraction 0.55。
            child: Container(
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                gradient: RadialGradient(
                  center: const Alignment(-0.3, -0.4),
                  radius: 0.55,
                  colors: [
                    const Color(0xFFFFFFFF).withValues(alpha: 0.55 * level),
                    const Color(0x00FFFFFF),
                  ],
                ),
                border: Border.all(
                  color: const Color(0xFFFFFFFF).withValues(alpha: 0.05 + 0.28 * level),
                  width: 1.5,
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

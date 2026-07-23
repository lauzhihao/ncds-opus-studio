import 'dart:async';
import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../../core/auth/auth_controller.dart';
import '../../design/tokens.dart';
import '../../design/typography.dart';

/// 未登录落地页:文案/节奏对齐 web LandingPage 打字机;
/// 视觉落在 app 暖色 token + 统一字体栈(mono 打字 / serif 刊头)。
class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key, required this.auth});

  final AuthController auth;

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  /// 与 web TYPE_LINES 一致。
  static const List<String> _lines = <String>[
    '还在玩「￥36/15秒」的「抽卡」短视频？',
    '快来跟我一起',
    '成大事',
  ];

  int _lineIdx = 0;
  int _charIdx = 0;
  bool _typeDone = false;
  bool _authReady = false;
  Timer? _tick;

  AuthController get auth => widget.auth;

  @override
  void initState() {
    super.initState();
    auth.addListener(_onAuth);
    _scheduleNext();
  }

  @override
  void dispose() {
    _tick?.cancel();
    auth.removeListener(_onAuth);
    super.dispose();
  }

  void _onAuth() {
    if (mounted) setState(() {});
  }

  void _scheduleNext() {
    _tick?.cancel();
    if (_typeDone) return;
    final current = _lines[_lineIdx];
    if (_charIdx < current.length) {
      // 对齐 web:48 + random*36 ms/字
      final ms = 48 + math.Random().nextInt(36);
      _tick = Timer(Duration(milliseconds: ms), () {
        if (!mounted) return;
        setState(() => _charIdx += 1);
        _scheduleNext();
      });
      return;
    }
    // 本行打完
    if (_lineIdx < _lines.length - 1) {
      final pause = _lineIdx == 0 ? 520 : 380;
      _tick = Timer(Duration(milliseconds: pause), () {
        if (!mounted) return;
        setState(() {
          _lineIdx += 1;
          _charIdx = 0;
        });
        _scheduleNext();
      });
      return;
    }
    // 最后一行完成 → 稍顿再露出登录按钮
    setState(() => _typeDone = true);
    _tick = Timer(const Duration(milliseconds: 420), () {
      if (!mounted) return;
      setState(() => _authReady = true);
    });
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.sand,
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: AppSpacing.pageH),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              const SizedBox(height: AppSpacing.pageTop + 8),
              // 左上角品牌:logo + NCDS OPUS STUDIO(对齐 web 落地页,去掉「能成大事」)
              Row(
                children: [
                  ClipRRect(
                    borderRadius: BorderRadius.circular(10),
                    child: Image.asset(
                      'assets/icon/neng.png',
                      width: 40,
                      height: 40,
                      fit: BoxFit.cover,
                      filterQuality: FilterQuality.medium,
                    ),
                  ),
                  const SizedBox(width: 10),
                  Text(
                    'NCDS OPUS STUDIO',
                    style: AppTypography.titleM.copyWith(
                      letterSpacing: -0.3,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                ],
              ),
              const SizedBox(height: AppSpacing.block),
              // 中间主容器:吃满剩余高度,文案在卡内水平+垂直居中
              Expanded(
                child: Container(
                  width: double.infinity,
                  padding: const EdgeInsets.fromLTRB(
                    AppSpacing.cardInner,
                    AppSpacing.block + 8,
                    AppSpacing.cardInner,
                    AppSpacing.cardInner + 4,
                  ),
                  decoration: BoxDecoration(
                    color: AppColors.ivory,
                    borderRadius: BorderRadius.circular(AppRadii.card),
                    boxShadow: [
                      BoxShadow(
                        color: AppColors.cardShadow,
                        blurRadius: 28,
                        offset: const Offset(0, 12),
                      ),
                    ],
                  ),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      Expanded(
                        child: Center(
                          child: _TypewriterBlock(
                            lines: _lines,
                            lineIdx: _lineIdx,
                            charIdx: _charIdx,
                            done: _typeDone,
                          ),
                        ),
                      ),
                      if (auth.error != null) ...[
                        Container(
                          padding: const EdgeInsets.symmetric(
                            horizontal: AppSpacing.m,
                            vertical: AppSpacing.s,
                          ),
                          decoration: BoxDecoration(
                            color: AppColors.statusRed.withValues(alpha: 0.10),
                            borderRadius: BorderRadius.circular(AppRadii.thumb),
                          ),
                          child: Text(
                            auth.error!,
                            style: AppTypography.caption.copyWith(
                              color: AppColors.statusRed,
                            ),
                            textAlign: TextAlign.center,
                          ),
                        ),
                        const SizedBox(height: AppSpacing.m),
                      ],
                      AnimatedOpacity(
                        opacity: _authReady ? 1 : 0,
                        duration: const Duration(milliseconds: 700),
                        curve: Curves.easeOut,
                        child: AnimatedSlide(
                          offset: _authReady
                              ? Offset.zero
                              : const Offset(0, 0.12),
                          duration: const Duration(milliseconds: 700),
                          curve: Curves.easeOut,
                          child: IgnorePointer(
                            ignoring: !_authReady,
                            child: _AuthButtons(auth: auth),
                          ),
                        ),
                      ),
                    ],
                  ),
                ),
              ),
              const SizedBox(height: AppSpacing.m),
              Text(
                '与 web 共用 Google / Apple 账号',
                style: AppTypography.monoXS.copyWith(color: AppColors.inkFaint),
                textAlign: TextAlign.center,
              ),
              const SizedBox(height: AppSpacing.pageBottom),
            ],
          ),
        ),
      ),
    );
  }
}

// —— 打字机文案 ——

class _TypewriterBlock extends StatelessWidget {
  const _TypewriterBlock({
    required this.lines,
    required this.lineIdx,
    required this.charIdx,
    required this.done,
  });

  final List<String> lines;
  final int lineIdx;
  final int charIdx;
  final bool done;

  @override
  Widget build(BuildContext context) {
    // 整块文案在容器内水平居中;各行自身也居中。
    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.center,
      children: [
        for (int i = 0; i <= lineIdx && i < lines.length; i++) ...[
          if (i > 0) const SizedBox(height: AppSpacing.m),
          _TypeLine(
            full: lines[i],
            visible: i < lineIdx
                ? lines[i]
                : lines[i].substring(0, charIdx.clamp(0, lines[i].length)),
            isHero: i == lines.length - 1,
            isActive: i == lineIdx && !done,
            showCaret: i == lineIdx && !done,
          ),
        ],
      ],
    );
  }
}

class _TypeLine extends StatelessWidget {
  const _TypeLine({
    required this.full,
    required this.visible,
    required this.isHero,
    required this.isActive,
    required this.showCaret,
  });

  final String full;
  final String visible;
  final bool isHero;
  final bool isActive;
  final bool showCaret;

  /// 引导句:等宽打字机感,字族统一 GeistMono。
  static final TextStyle _leadStyle = AppTypography.mono.copyWith(
    fontSize: 17,
    height: 1.55,
    letterSpacing: 0.4,
    color: AppColors.ink.withValues(alpha: 0.62),
  );

  /// 刊头「成大事」:衬线展示,与首页 display 一致。
  static final TextStyle _heroStyle = AppTypography.displayTitle.copyWith(
    fontSize: 36,
    height: 1.15,
    letterSpacing: 2,
    color: AppColors.ink,
  );

  @override
  Widget build(BuildContext context) {
    if (isHero) {
      return Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.center,
        children: [
          Text.rich(
            textAlign: TextAlign.center,
            TextSpan(
              children: [
                for (int i = 0; i < visible.length; i++)
                  TextSpan(text: visible[i], style: _heroStyle),
                if (showCaret)
                  WidgetSpan(
                    alignment: PlaceholderAlignment.middle,
                    child: _Caret(color: AppColors.orange, height: 28),
                  ),
              ],
            ),
          ),
          // 打完后:accent 提线(对齐 web landing-hero-mark::after)
          AnimatedContainer(
            duration: const Duration(milliseconds: 650),
            curve: Curves.easeOut,
            margin: const EdgeInsets.only(top: 8),
            height: 1.5,
            width: doneWidth,
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(1),
              gradient: LinearGradient(
                colors: [
                  AppColors.orange.withValues(alpha: 0),
                  AppColors.orange.withValues(alpha: 0.85),
                  AppColors.orange.withValues(alpha: 0),
                ],
              ),
            ),
          ),
        ],
      );
    }

    return Text.rich(
      textAlign: TextAlign.center,
      TextSpan(
        style: _leadStyle.copyWith(
          color: isActive
              ? AppColors.ink.withValues(alpha: 0.78)
              : AppColors.ink.withValues(alpha: 0.55),
        ),
        children: [
          ..._quoteSpans(visible),
          if (showCaret)
            WidgetSpan(
              alignment: PlaceholderAlignment.middle,
              child: _Caret(
                color: AppColors.ink.withValues(alpha: 0.55),
                height: 16,
              ),
            ),
        ],
      ),
    );
  }

  double get doneWidth {
    if (visible.length < full.length) return 0;
    return math.min(120, 28.0 * full.length);
  }

  /// 「」内文字品牌红高亮(打字中未闭合片段同样着色)。
  static List<InlineSpan> _quoteSpans(String text) {
    final spans = <InlineSpan>[];
    var i = 0;
    while (i < text.length) {
      if (text[i] == '「') {
        final end = text.indexOf('」', i + 1);
        if (end == -1) {
          spans.add(
            TextSpan(
              text: text.substring(i),
              style: TextStyle(
                color: AppColors.accentWolong,
                fontWeight: FontWeight.w600,
              ),
            ),
          );
          break;
        }
        spans.add(
          TextSpan(
            text: text.substring(i, end + 1),
            style: TextStyle(
              color: AppColors.accentWolong,
              fontWeight: FontWeight.w600,
            ),
          ),
        );
        i = end + 1;
      } else {
        final next = text.indexOf('「', i);
        final slice = next == -1 ? text.substring(i) : text.substring(i, next);
        spans.add(TextSpan(text: slice));
        i = next == -1 ? text.length : next;
      }
    }
    return spans;
  }
}

class _Caret extends StatefulWidget {
  const _Caret({required this.color, required this.height});

  final Color color;
  final double height;

  @override
  State<_Caret> createState() => _CaretState();
}

class _CaretState extends State<_Caret> with SingleTickerProviderStateMixin {
  late final AnimationController _c = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 900),
  )..repeat();

  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: _c,
      builder: (context, child) {
        // step-end blink
        final on = _c.value < 0.5;
        return Opacity(
          opacity: on ? 1 : 0,
          child: Container(
            width: 1.5,
            height: widget.height,
            margin: const EdgeInsets.only(left: 3),
            color: widget.color,
          ),
        );
      },
    );
  }
}

// —— 登录按钮 ——

class _AuthButtons extends StatelessWidget {
  const _AuthButtons({required this.auth});

  final AuthController auth;

  @override
  Widget build(BuildContext context) {
    if (!auth.showGoogleButton && !auth.showAppleButton) {
      return Text(
        '服务端未开放可用登录方式',
        style: AppTypography.caption.copyWith(color: AppColors.inkMuted),
        textAlign: TextAlign.center,
      );
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (auth.showGoogleButton)
          _AuthButton(
            label: 'Continue with Google',
            background: Colors.white,
            foreground: AppColors.ink,
            border: true,
            busy: auth.busy,
            onPressed: auth.busy ? null : auth.signInWithGoogle,
            leading: const _GoogleMark(),
          ),
        if (auth.showGoogleButton && auth.showAppleButton)
          const SizedBox(height: AppSpacing.m),
        if (auth.showAppleButton)
          _AuthButton(
            label: 'Continue with Apple',
            background: AppColors.housing,
            foreground: AppColors.ivory,
            busy: auth.busy,
            onPressed: auth.busy ? null : auth.signInWithApple,
            leading: const _AppleMark(),
          ),
      ],
    );
  }
}

class _AuthButton extends StatelessWidget {
  const _AuthButton({
    required this.label,
    required this.background,
    required this.foreground,
    required this.busy,
    required this.onPressed,
    required this.leading,
    this.border = false,
  });

  final String label;
  final Color background;
  final Color foreground;
  final bool busy;
  final VoidCallback? onPressed;
  final Widget leading;
  final bool border;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      height: 52,
      child: Material(
        color: background,
        borderRadius: BorderRadius.circular(AppRadii.button),
        child: InkWell(
          borderRadius: BorderRadius.circular(AppRadii.button),
          onTap: onPressed,
          child: DecoratedBox(
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(AppRadii.button),
              border: border
                  ? Border.all(color: AppColors.ink.withValues(alpha: 0.12))
                  : null,
            ),
            child: Row(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                if (busy)
                  SizedBox(
                    width: 18,
                    height: 18,
                    child: CircularProgressIndicator(
                      strokeWidth: 2,
                      color: foreground,
                    ),
                  )
                else ...[
                  leading,
                  const SizedBox(width: 10),
                  Text(
                    label,
                    style: AppTypography.subhead.copyWith(
                      color: foreground,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                ],
              ],
            ),
          ),
        ),
      ),
    );
  }
}

/// Google 四色 G(CustomPaint)——四段色本身即渐变分段。
class _GoogleMark extends StatelessWidget {
  const _GoogleMark();

  @override
  Widget build(BuildContext context) {
    return const CustomPaint(size: Size(20, 20), painter: _GoogleGPainter());
  }
}

class _GoogleGPainter extends CustomPainter {
  const _GoogleGPainter();

  @override
  void paint(Canvas canvas, Size size) {
    // 简化四色 G:圆环分段 + 横条
    final cx = size.width / 2;
    final cy = size.height / 2;
    final r = size.width * 0.42;
    final stroke = size.width * 0.18;
    final rect = Rect.fromCircle(center: Offset(cx, cy), radius: r);
    final paint = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = stroke
      ..strokeCap = StrokeCap.butt;

    // 蓝 右上
    paint.color = const Color(0xFF4285F4);
    canvas.drawArc(rect, -math.pi * 0.15, math.pi * 0.55, false, paint);
    // 绿 右下
    paint.color = const Color(0xFF34A853);
    canvas.drawArc(rect, math.pi * 0.4, math.pi * 0.55, false, paint);
    // 黄 左下
    paint.color = const Color(0xFFFBBC05);
    canvas.drawArc(rect, math.pi * 0.95, math.pi * 0.45, false, paint);
    // 红 左上
    paint.color = const Color(0xFFEA4335);
    canvas.drawArc(rect, math.pi * 1.4, math.pi * 0.45, false, paint);

    // 蓝色横臂
    final bar = Paint()
      ..color = const Color(0xFF4285F4)
      ..style = PaintingStyle.fill;
    canvas.drawRRect(
      RRect.fromRectAndRadius(
        Rect.fromLTWH(
          cx - stroke * 0.1,
          cy - stroke * 0.45,
          r + stroke * 0.35,
          stroke * 0.9,
        ),
        const Radius.circular(1),
      ),
      bar,
    );
  }

  @override
  bool shouldRepaint(covariant CustomPainter oldDelegate) => false;
}

/// Apple 标志:银白金属渐变 + 缓慢扫光。
class _AppleMark extends StatefulWidget {
  const _AppleMark();

  @override
  State<_AppleMark> createState() => _AppleMarkState();
}

class _AppleMarkState extends State<_AppleMark>
    with SingleTickerProviderStateMixin {
  late final AnimationController _c = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 2400),
  )..repeat();

  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: _c,
      builder: (context, child) {
        final t = _c.value;
        return ShaderMask(
          blendMode: BlendMode.srcIn,
          shaderCallback: (rect) {
            return LinearGradient(
              begin: Alignment(-1.2 + t * 2.4, -1),
              end: Alignment(-0.2 + t * 2.4, 1),
              colors: const [
                Color(0xFFB8B8B8),
                Color(0xFFFFFFFF),
                Color(0xFFE8E8E8),
                Color(0xFFA0A0A0),
                Color(0xFFF5F5F5),
              ],
              stops: const [0.0, 0.28, 0.5, 0.72, 1.0],
            ).createShader(rect);
          },
          child: const Icon(Icons.apple, size: 22, color: Colors.white),
        );
      },
    );
  }
}

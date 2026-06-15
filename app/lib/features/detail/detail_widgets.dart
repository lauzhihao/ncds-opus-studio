// 详情页各 agent 结果面板共用的小组件:内联音频播放器、可折叠分组、内容工具条(复制)。
// 沈括面板自带了私有版本(保持不动);这里抽出公共版给鬼谷子/吴道子/伯牙/卧龙等新面板复用。

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:just_audio/just_audio.dart';

import '../../design/tokens.dart';
import '../../design/typography.dart';

/// 内联迷你音频播放器(播放/暂停 + 进度条)。对齐 iOS InlineAudioPlayer。
class InlineAudio extends StatefulWidget {
  const InlineAudio({
    super.key,
    required this.url,
    required this.title,
    required this.accent,
    this.subtitle,
  });

  final String? url;
  final String title;
  final String? subtitle;
  final Color accent;

  @override
  State<InlineAudio> createState() => _InlineAudioState();
}

class _InlineAudioState extends State<InlineAudio> {
  final AudioPlayer _player = AudioPlayer();
  bool _ready = false;
  bool _error = false;

  @override
  void initState() {
    super.initState();
    _setup();
  }

  Future<void> _setup() async {
    final url = widget.url;
    if (url == null || url.isEmpty) {
      setState(() => _error = true);
      return;
    }
    try {
      await _player.setUrl(url);
      if (mounted) setState(() => _ready = true);
    } catch (_) {
      if (mounted) setState(() => _error = true);
    }
  }

  @override
  void dispose() {
    _player.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final disabled = widget.url == null || widget.url!.isEmpty;
    return Row(
      children: [
        StreamBuilder<PlayerState>(
          stream: _player.playerStateStream,
          builder: (context, snap) {
            final st = snap.data;
            final completed = st?.processingState == ProcessingState.completed;
            final playing = (st?.playing ?? false) && !completed;
            return IconButton(
              iconSize: 44,
              color: disabled ? AppColors.inkFaint : widget.accent,
              padding: EdgeInsets.zero,
              constraints: const BoxConstraints(),
              icon: Icon(playing ? Icons.pause_circle_filled : Icons.play_circle_fill),
              onPressed: !_ready
                  ? null
                  : () async {
                      if (playing) {
                        await _player.pause();
                      } else {
                        if (completed) await _player.seek(Duration.zero);
                        await _player.play();
                      }
                    },
            );
          },
        ),
        const SizedBox(width: AppSpacing.m),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Flexible(
                    child: Text(widget.title,
                        style: AppTypography.subhead, maxLines: 1, overflow: TextOverflow.ellipsis),
                  ),
                  if (widget.subtitle != null && widget.subtitle!.isNotEmpty) ...[
                    const SizedBox(width: AppSpacing.s),
                    Text(widget.subtitle!,
                        style: AppTypography.caption.copyWith(color: AppColors.inkMuted)),
                  ],
                ],
              ),
              if (_error)
                Text('音频加载失败', style: AppTypography.caption2.copyWith(color: AppColors.statusRed))
              else
                _AudioBar(player: _player, accent: widget.accent),
            ],
          ),
        ),
        const SizedBox(width: AppSpacing.s),
        Icon(Icons.graphic_eq, size: 20, color: widget.accent.withValues(alpha: 0.35)),
      ],
    );
  }
}

class _AudioBar extends StatelessWidget {
  const _AudioBar({required this.player, required this.accent});
  final AudioPlayer player;
  final Color accent;

  @override
  Widget build(BuildContext context) {
    return StreamBuilder<Duration?>(
      stream: player.durationStream,
      builder: (context, durSnap) {
        final duration = durSnap.data ?? Duration.zero;
        return StreamBuilder<Duration>(
          stream: player.positionStream,
          builder: (context, posSnap) {
            var pos = posSnap.data ?? Duration.zero;
            if (pos > duration) pos = duration;
            final maxMs = duration.inMilliseconds;
            return SliderTheme(
              data: SliderTheme.of(context).copyWith(
                trackHeight: 2,
                activeTrackColor: accent,
                inactiveTrackColor: AppColors.ink.withValues(alpha: 0.12),
                thumbColor: accent,
                overlayColor: accent.withValues(alpha: 0.18),
                thumbShape: const RoundSliderThumbShape(enabledThumbRadius: 5),
              ),
              child: Slider(
                min: 0,
                max: maxMs <= 0 ? 1 : maxMs.toDouble(),
                value: maxMs <= 0 ? 0 : pos.inMilliseconds.clamp(0, maxMs).toDouble(),
                onChanged: maxMs <= 0 ? null : (v) => player.seek(Duration(milliseconds: v.round())),
              ),
            );
          },
        );
      },
    );
  }
}

/// 折叠导航标题:hero 滚出视野后,把 [avatar] + [title] 淡入到磨砂顶栏(对齐 iOS 大标题折叠)。
/// 跟 [scroll] 偏移联动:offset 24→72 区间内 opacity 0→1,并伴随轻微上移收尾。
class CollapsingNavTitle extends StatelessWidget {
  const CollapsingNavTitle({super.key, required this.scroll, required this.title, this.avatar});

  final ScrollController scroll;
  final String title;
  final Widget? avatar;

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: scroll,
      builder: (context, _) {
        final double t = scroll.hasClients ? ((scroll.offset - 24) / 48).clamp(0.0, 1.0) : 0.0;
        if (t == 0) return const SizedBox.shrink();
        return Opacity(
          opacity: t,
          child: Transform.translate(
            offset: Offset(0, (1 - t) * 6),
            child: Row(
              children: [
                if (avatar != null) ...[avatar!, const SizedBox(width: AppSpacing.s)],
                Expanded(
                  child: Text(title,
                      style: AppTypography.navTitle, maxLines: 1, overflow: TextOverflow.ellipsis),
                ),
              ],
            ),
          ),
        );
      },
    );
  }
}

/// 可折叠分组(对齐 iOS DisclosureGroup):标题行点开/收起,内容渐隐展开。
class DetailDisclosure extends StatefulWidget {
  const DetailDisclosure({
    super.key,
    required this.label,
    required this.child,
    this.icon,
    this.labelStyle,
    this.initiallyOpen = false,
  });

  final String label;
  final Widget child;
  final IconData? icon;
  final TextStyle? labelStyle;
  final bool initiallyOpen;

  @override
  State<DetailDisclosure> createState() => _DetailDisclosureState();
}

class _DetailDisclosureState extends State<DetailDisclosure> {
  late bool _open = widget.initiallyOpen;

  @override
  Widget build(BuildContext context) {
    final style = widget.labelStyle ??
        AppTypography.caption.copyWith(fontWeight: FontWeight.w500, color: AppColors.inkMuted);
    final tint = style.color ?? AppColors.inkMuted;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        GestureDetector(
          behavior: HitTestBehavior.opaque,
          onTap: () => setState(() => _open = !_open),
          child: Row(
            children: [
              if (widget.icon != null) ...[
                Icon(widget.icon, size: 14, color: tint),
                const SizedBox(width: 5),
              ],
              // Expanded 占满中间 → 箭头铁定贴右(避免 Flexible+Spacer 的不左不右)。
              Expanded(child: Text(widget.label, style: style)),
              Icon(_open ? Icons.expand_less : Icons.expand_more, size: 20, color: tint),
            ],
          ),
        ),
        if (_open) ...[
          const SizedBox(height: AppSpacing.m),
          widget.child,
        ],
      ],
    );
  }
}

/// 内容工具条:把纯文本「带走」(复制到剪贴板)。对齐 iOS ContentToolbelt 的复制能力
/// (iOS 还有系统分享;Flutter 暂未引入 share_plus,先只做复制)。
class ContentToolbelt extends StatelessWidget {
  const ContentToolbelt({super.key, required this.text, required this.accent, this.meta});

  final String text;
  final Color accent;
  final String? meta;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        if (meta != null)
          Expanded(
            child: Text(meta!,
                style: AppTypography.caption2.copyWith(color: AppColors.inkFaint),
                maxLines: 1,
                overflow: TextOverflow.ellipsis),
          )
        else
          const Spacer(),
        const SizedBox(width: AppSpacing.s),
        GestureDetector(
          onTap: () {
            Clipboard.setData(ClipboardData(text: text));
            ScaffoldMessenger.of(context).showSnackBar(
              const SnackBar(content: Text('已复制到剪贴板'), duration: Duration(seconds: 1)),
            );
          },
          child: Container(
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
            decoration: ShapeDecoration(color: accent.withValues(alpha: 0.14), shape: const StadiumBorder()),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Icon(Icons.copy_rounded, size: 13, color: accent),
                const SizedBox(width: 4),
                Text('复制',
                    style: AppTypography.caption.copyWith(fontWeight: FontWeight.w600, color: accent)),
              ],
            ),
          ),
        ),
      ],
    );
  }
}

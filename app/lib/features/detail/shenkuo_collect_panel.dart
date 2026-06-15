import 'package:flutter/material.dart';
import 'package:just_audio/just_audio.dart';

import '../../core/net/factory_client.dart';
import '../../core/net/models.dart';
import '../../design/components/app_card.dart';
import '../../design/components/tag_chip.dart';
import '../../design/components/traffic_bulb.dart';
import '../../design/tokens.dart';
import '../../design/typography.dart';
import '../home/agent_catalog.dart';

// 沈括采集结果面板 —— 1:1 复刻 iOS ShenkuoCollectPanel(AgentResultPanels.swift)。
// 封面 + 标题 + 话题 + 工序状态 + 提取文案 + 声音素材 + 高赞评论 + 抠图素材;不放原视频。
// 灯态/状态词显示在卡片右上角(hero 不再重复)。文件走 /artifacts/files/。

/// 任务灯态:颜色 + 动效 + 状态词。详情页 hero 与采集卡共用(对齐 iOS AgentStatus 灯)。
class TaskLight {
  const TaskLight(this.color, this.motion, this.period, this.dim, this.word);

  final Color color;
  final BulbMotion motion;
  final double period;
  final double dim;
  final String word;

  TaskLight withWord(String w) => TaskLight(color, motion, period, dim, w);

  static const idle = TaskLight(AppColors.statusGreen, BulbMotion.solid, 1.0, 1.0, '');
  static const working = TaskLight(AppColors.statusYellow, BulbMotion.breathe, 3.0, 0.4, '工作中…');
  static const asking = TaskLight(AppColors.statusRed, BulbMotion.blink, 0.7, 1.0, '待验收');
}

/// 任务灯泡(复用 TrafficBulb,固定点亮)。
class TaskBulb extends StatelessWidget {
  const TaskBulb({super.key, required this.light, this.diameter = 11});

  final TaskLight light;
  final double diameter;

  @override
  Widget build(BuildContext context) => TrafficBulb(
        color: light.color,
        isOn: true,
        motion: light.motion,
        period: light.period,
        dimTo: light.dim,
        diameter: diameter,
      );
}

/// 抖音口径数字:<1w 原样,<1亿 用 w,≥1亿 用 亿。
String shenkuoCount(int n) {
  if (n >= 100000000) return '${(n / 100000000).toStringAsFixed(1)}亿';
  if (n >= 10000) return '${(n / 10000).toStringAsFixed(1)}w';
  return '$n';
}

/// 赞/评/转/藏数据行。hero 顶部与多条采集卡内共用。
class ShenkuoStatsLine extends StatelessWidget {
  const ShenkuoStatsLine({super.key, required this.entry});

  final ShenkuoEntry entry;

  @override
  Widget build(BuildContext context) {
    final s = entry.stats ?? const <String, int>{};
    final items = <Widget>[];
    final digg = s['digg'] ?? entry.digg;
    if (digg != null) items.add(_item(Icons.favorite, digg, AppColors.statusRed));
    if (s['comment'] != null) items.add(_item(Icons.mode_comment, s['comment']!, AppColors.inkMuted));
    if (s['share'] != null) items.add(_item(Icons.reply, s['share']!, AppColors.inkMuted));
    if (s['collect'] != null) items.add(_item(Icons.star, s['collect']!, AppColors.amber));
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        for (int i = 0; i < items.length; i++) ...[
          if (i > 0) const SizedBox(width: 8),
          items[i],
        ],
      ],
    );
  }

  Widget _item(IconData icon, int n, Color color) => Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, size: 10, color: color),
          const SizedBox(width: 2),
          Text(
            shenkuoCount(n),
            style: AppTypography.caption.copyWith(fontWeight: FontWeight.w600, color: color),
          ),
        ],
      );
}

class ShenkuoCollectPanel extends StatefulWidget {
  const ShenkuoCollectPanel({
    super.key,
    required this.result,
    required this.agent,
    required this.client,
    this.light,
  });

  final ShenkuoResult result;
  final AgentInfo agent;
  final FactoryClient client;

  /// 任务灯态/状态词:显示在「采集结果」卡右上角(对齐 iOS)。
  final TaskLight? light;

  @override
  State<ShenkuoCollectPanel> createState() => _ShenkuoCollectPanelState();
}

class _ShenkuoCollectPanelState extends State<ShenkuoCollectPanel> {
  final Set<String> _expandedTexts = <String>{}; // 展开全文的 entry(aweme_id)

  List<ShenkuoEntry> get _entries => widget.result.collected ?? const <ShenkuoEntry>[];

  @override
  Widget build(BuildContext context) {
    // 文件服务基址解析一次(resolver 已缓存,基本同步);拿到后同步拼各文件 URL。
    return FutureBuilder<Uri?>(
      future: widget.client.absoluteUrl('/artifacts/files/'),
      builder: (context, snap) {
        final filesBase = snap.data?.toString();
        return AppCard(child: _body(filesBase));
      },
    );
  }

  String? _fileUrl(String? filesBase, String? rel) {
    if (filesBase == null || rel == null || rel.isEmpty) return null;
    return '$filesBase$rel';
  }

  Widget _body(String? filesBase) {
    final r = widget.result;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Text('采集结果', style: AppTypography.titleM),
            const Spacer(),
            if (r.allPosts != null)
              TagChip(label: '拉取 ${r.allPosts} 条', icon: Icons.download, tint: widget.agent.accent),
            if (r.snapshots != null) ...[
              const SizedBox(width: AppSpacing.xs),
              TagChip(label: '快照 ${r.snapshots}', icon: Icons.show_chart, tint: AppColors.orange),
            ],
            if (widget.light != null) ...[
              const SizedBox(width: AppSpacing.s),
              TaskBulb(light: widget.light!, diameter: 10),
              if (widget.light!.word.isNotEmpty) ...[
                const SizedBox(width: 5),
                Text(widget.light!.word,
                    style: AppTypography.caption.copyWith(
                        fontWeight: FontWeight.w500, color: AppColors.inkMuted)),
              ],
            ],
          ],
        ),
        const SizedBox(height: AppSpacing.m),
        if (_entries.isEmpty)
          Text(
            r.snapshots != null ? '本次仅刷新作品指标，未深采' : '没有采到内容',
            style: AppTypography.caption.copyWith(color: AppColors.inkMuted),
          )
        else
          for (int i = 0; i < _entries.length; i++) ...[
            if (i > 0) ...[
              const SizedBox(height: AppSpacing.m),
              Divider(color: AppColors.ink.withValues(alpha: 0.08), height: 1),
              const SizedBox(height: AppSpacing.m),
            ],
            _entryCard(_entries[i], filesBase),
          ],
      ],
    );
  }

  Widget _entryCard(ShenkuoEntry e, String? filesBase) {
    final coverUrl = _fileUrl(filesBase, e.cover);
    final hashtags = e.hashtags ?? const <String>[];
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        // 封面 3:4 竖版,右栏标题贴上沿、话题贴下沿。
        Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            if (coverUrl != null) ...[
              _Cover(url: coverUrl),
              const SizedBox(width: AppSpacing.m),
            ],
            Expanded(
              child: SizedBox(
                height: 120,
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      _cleanTitle(e),
                      style: AppTypography.titleM,
                      maxLines: 3,
                      overflow: TextOverflow.ellipsis,
                    ),
                    const Spacer(),
                    if (hashtags.isNotEmpty)
                      Wrap(
                        spacing: AppSpacing.s,
                        runSpacing: AppSpacing.xs,
                        children: [
                          for (final t in hashtags) TagChip(label: '#$t', tint: widget.agent.accent),
                        ],
                      ),
                    if (_entries.length > 1) ...[
                      const SizedBox(height: AppSpacing.xs),
                      ShenkuoStatsLine(entry: e),
                    ],
                  ],
                ),
              ),
            ),
          ],
        ),
        const SizedBox(height: AppSpacing.m),
        // 工序状态点。
        Wrap(
          spacing: AppSpacing.m,
          runSpacing: AppSpacing.xs,
          children: [
            _stageChip('下载', e.status?['download']),
            _stageChip('转写', e.status?['transcribe']),
            _stageChip('声音', e.status?['audio']),
            _stageChip('抠图', e.status?['cutout']),
            _stageChip('评论', e.status?['comments']),
          ],
        ),
        _transcriptSection(e),
        _audioSection(e, filesBase),
        _commentsSection(e),
        _imageStrip('抠图素材', e.cutouts, filesBase),
      ],
    );
  }

  /// 作品标题:剥掉 desc 内嵌的 #话题(下面有专门 chips,不重复)。
  String _cleanTitle(ShenkuoEntry e) {
    var t = e.desc ?? '';
    for (final tag in e.hashtags ?? const <String>[]) {
      t = t.replaceAll('#$tag', '');
    }
    final cleaned = t.split(' ').where((s) => s.isNotEmpty).join(' ').trim();
    if (cleaned.isNotEmpty) return cleaned;
    return (e.desc?.isNotEmpty ?? false) ? e.desc! : (e.awemeId ?? '?');
  }

  // —— 提取文案:默认露 6 行,可展开全文分段 ——
  Widget _transcriptSection(ShenkuoEntry e) {
    final t = e.text;
    if (t == null || t.isEmpty) return const SizedBox.shrink();
    final key = e.awemeId ?? t;
    final expanded = _expandedTexts.contains(key);
    return Padding(
      padding: const EdgeInsets.only(top: AppSpacing.m),
      child: Container(
        width: double.infinity,
        padding: const EdgeInsets.all(14),
        decoration: BoxDecoration(
          color: AppColors.sand.withValues(alpha: 0.45),
          borderRadius: BorderRadius.circular(AppRadii.capture),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(Icons.format_quote, size: 13, color: AppColors.inkMuted),
                const SizedBox(width: 4),
                Text('提取文案 · ${t.runes.length} 字',
                    style: AppTypography.footnote.copyWith(
                        fontSize: 12, fontWeight: FontWeight.w600, color: AppColors.inkMuted)),
                const Spacer(),
                GestureDetector(
                  onTap: () => setState(() =>
                      expanded ? _expandedTexts.remove(key) : _expandedTexts.add(key)),
                  child: Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Text(expanded ? '收起' : '展开全文',
                          style: AppTypography.footnote.copyWith(
                              fontSize: 12, fontWeight: FontWeight.w600, color: widget.agent.accent)),
                      Icon(expanded ? Icons.expand_less : Icons.expand_more,
                          size: 14, color: widget.agent.accent),
                    ],
                  ),
                ),
              ],
            ),
            const SizedBox(height: AppSpacing.s),
            if (expanded)
              for (final p in _paragraphs(t))
                Padding(
                  padding: const EdgeInsets.only(bottom: AppSpacing.s),
                  child: Text(p,
                      style: AppTypography.bodySerif.copyWith(
                          height: 1.7, color: AppColors.ink.withValues(alpha: 0.88))),
                )
            else
              Text(t,
                  maxLines: 6,
                  overflow: TextOverflow.ellipsis,
                  style: AppTypography.bodySerif.copyWith(
                      height: 1.55, color: AppColors.ink.withValues(alpha: 0.85))),
          ],
        ),
      ),
    );
  }

  /// 转写按句切开、两句一段,排出呼吸感。
  List<String> _paragraphs(String t) {
    final sentences = <String>[];
    var cur = '';
    for (final ch in t.split('')) {
      cur += ch;
      if ('。！？!?'.contains(ch)) {
        final s = cur.trim();
        if (s.isNotEmpty) sentences.add(s);
        cur = '';
      }
    }
    final rest = cur.trim();
    if (rest.isNotEmpty) sentences.add(rest);
    final out = <String>[];
    for (var i = 0; i < sentences.length; i += 2) {
      out.add(sentences.sublist(i, (i + 2).clamp(0, sentences.length)).join());
    }
    return out.isEmpty ? [t] : out;
  }

  // —— 声音素材:原声/人声/伴奏,可折叠 ——
  Widget _audioSection(ShenkuoEntry e, String? filesBase) {
    final a = e.audio;
    if (a == null || a.isEmpty) return const SizedBox.shrink();
    final rows = <Widget>[];
    void add(String title, String note, String? rel) {
      final url = _fileUrl(filesBase, rel);
      if (url != null) rows.add(_InlineAudio(url: url, title: title, subtitle: note, accent: widget.agent.accent));
    }
    add('原声', '视频完整音轨', a['original']);
    add('人声 · 口播', 'Demucs 分离', a['vocals']);
    add('伴奏 · BGM/音效', 'Demucs 分离', a['bgm']);
    if (rows.isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(top: AppSpacing.m),
      child: _Disclosure(
        icon: Icons.graphic_eq,
        label: '声音素材 · ${rows.length} 轨',
        child: Column(
          children: [
            for (int i = 0; i < rows.length; i++) ...[
              if (i > 0) const SizedBox(height: AppSpacing.m),
              rows[i],
            ],
          ],
        ),
      ),
    );
  }

  // —— 高赞评论,可折叠 ——
  Widget _commentsSection(ShenkuoEntry e) {
    final comments = e.topComments;
    if (comments == null || comments.isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(top: AppSpacing.m),
      child: _Disclosure(
        icon: Icons.forum_outlined,
        label: '高赞评论 · Top ${comments.length}',
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            for (int i = 0; i < comments.length; i++) ...[
              if (i > 0) const SizedBox(height: AppSpacing.s),
              _commentRow(i, comments[i]),
            ],
          ],
        ),
      ),
    );
  }

  Widget _commentRow(int i, ShenkuoComment c) {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        SizedBox(
          width: 18,
          child: Text('${i + 1}',
              textAlign: TextAlign.right,
              style: AppTypography.caption2.copyWith(
                  fontWeight: FontWeight.w700,
                  color: widget.agent.accent.withValues(alpha: i < 3 ? 1 : 0.45))),
        ),
        const SizedBox(width: AppSpacing.s),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(c.text ?? '',
                  style: AppTypography.bodySerifS.copyWith(color: AppColors.ink.withValues(alpha: 0.85))),
              const SizedBox(height: 2),
              Row(
                children: [
                  if (c.nickname?.isNotEmpty ?? false)
                    Text(c.nickname!, style: AppTypography.caption2.copyWith(color: AppColors.inkMuted)),
                  if (c.ip?.isNotEmpty ?? false) ...[
                    const SizedBox(width: AppSpacing.s),
                    Text(c.ip!, style: AppTypography.caption2.copyWith(color: AppColors.inkFaint)),
                  ],
                  const Spacer(),
                  if (c.digg != null) ...[
                    Icon(Icons.favorite, size: 11, color: AppColors.statusRed),
                    const SizedBox(width: 2),
                    Text(shenkuoCount(c.digg!),
                        style: AppTypography.caption2.copyWith(
                            fontWeight: FontWeight.w600, color: AppColors.statusRed)),
                  ],
                ],
              ),
            ],
          ),
        ),
      ],
    );
  }

  // —— 图片横滑带(抠图素材),点开全屏相册 ——
  Widget _imageStrip(String title, List<String>? rels, String? filesBase) {
    final urls = (rels ?? const <String>[])
        .map((r) => _fileUrl(filesBase, r))
        .whereType<String>()
        .toList();
    if (urls.isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(top: AppSpacing.m),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('$title · ${urls.length}',
              style: AppTypography.caption.copyWith(fontWeight: FontWeight.w500, color: AppColors.inkMuted)),
          const SizedBox(height: AppSpacing.s),
          SizedBox(
            height: 54,
            child: ListView.separated(
              scrollDirection: Axis.horizontal,
              itemCount: urls.length,
              separatorBuilder: (_, _) => const SizedBox(width: AppSpacing.s),
              itemBuilder: (context, i) => GestureDetector(
                onTap: () => _openPager(urls, i),
                child: ClipRRect(
                  borderRadius: BorderRadius.circular(AppRadii.thumb),
                  child: Image.network(
                    urls[i],
                    width: 96,
                    height: 54,
                    fit: BoxFit.cover,
                    errorBuilder: (_, _, _) => Container(
                      width: 96,
                      height: 54,
                      color: AppColors.sand.withValues(alpha: 0.5),
                    ),
                  ),
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }

  void _openPager(List<String> urls, int start) {
    Navigator.of(context).push(MaterialPageRoute<void>(
      fullscreenDialog: true,
      builder: (_) => _ImagePager(urls: urls, start: start),
    ));
  }

  Widget _stageChip(String label, String? status) {
    final (Color color, IconData icon) = switch (status) {
      null => (AppColors.inkFaint, Icons.remove_circle_outline),
      'ok' || 'cached' => (AppColors.statusGreen, Icons.check_circle),
      _ => (AppColors.statusRed, Icons.cancel),
    };
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(icon, size: 11, color: color),
        const SizedBox(width: 2),
        Text(label, style: AppTypography.caption2.copyWith(color: color)),
      ],
    );
  }
}

/// 封面缩略(90x120,3:4 竖版)。
class _Cover extends StatelessWidget {
  const _Cover({required this.url});
  final String url;

  @override
  Widget build(BuildContext context) {
    return ClipRRect(
      borderRadius: BorderRadius.circular(AppRadii.image),
      child: Image.network(
        url,
        width: 90,
        height: 120,
        fit: BoxFit.cover,
        loadingBuilder: (context, child, progress) =>
            progress == null ? child : _coverPlaceholder(),
        errorBuilder: (_, _, _) => _coverPlaceholder(),
      ),
    );
  }

  Widget _coverPlaceholder() => Container(
        width: 90,
        height: 120,
        color: AppColors.housing.withValues(alpha: 0.12),
        child: Icon(Icons.photo, size: 16, color: AppColors.inkFaint),
      );
}

/// 可折叠分组(声音素材 / 高赞评论)——对齐 iOS DisclosureGroup。
class _Disclosure extends StatefulWidget {
  const _Disclosure({required this.icon, required this.label, required this.child});

  final IconData icon;
  final String label;
  final Widget child;

  @override
  State<_Disclosure> createState() => _DisclosureState();
}

class _DisclosureState extends State<_Disclosure> {
  bool _open = false;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        GestureDetector(
          behavior: HitTestBehavior.opaque,
          onTap: () => setState(() => _open = !_open),
          child: Row(
            children: [
              Icon(widget.icon, size: 13, color: AppColors.inkMuted),
              const SizedBox(width: 4),
              Text(widget.label,
                  style: AppTypography.caption.copyWith(
                      fontWeight: FontWeight.w500, color: AppColors.inkMuted)),
              const Spacer(),
              Icon(_open ? Icons.expand_less : Icons.expand_more, size: 18, color: AppColors.inkMuted),
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

/// 内联迷你音频播放器(播放/暂停 + 进度条)。
class _InlineAudio extends StatefulWidget {
  const _InlineAudio({required this.url, required this.title, required this.subtitle, required this.accent});

  final String url;
  final String title;
  final String subtitle;
  final Color accent;

  @override
  State<_InlineAudio> createState() => _InlineAudioState();
}

class _InlineAudioState extends State<_InlineAudio> {
  final AudioPlayer _player = AudioPlayer();
  bool _ready = false;
  bool _error = false;

  @override
  void initState() {
    super.initState();
    _setup();
  }

  Future<void> _setup() async {
    try {
      await _player.setUrl(widget.url);
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
    return Row(
      children: [
        StreamBuilder<PlayerState>(
          stream: _player.playerStateStream,
          builder: (context, snap) {
            final st = snap.data;
            final completed = st?.processingState == ProcessingState.completed;
            final playing = (st?.playing ?? false) && !completed;
            return IconButton(
              iconSize: 34,
              color: widget.accent,
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
        const SizedBox(width: AppSpacing.s),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Text(widget.title, style: AppTypography.label),
                  const SizedBox(width: AppSpacing.s),
                  Text(widget.subtitle, style: AppTypography.caption2.copyWith(color: AppColors.inkFaint)),
                ],
              ),
              if (_error)
                Text('音频加载失败', style: AppTypography.caption2.copyWith(color: AppColors.statusRed))
              else
                _AudioBar(player: _player),
            ],
          ),
        ),
      ],
    );
  }
}

class _AudioBar extends StatelessWidget {
  const _AudioBar({required this.player});
  final AudioPlayer player;

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
                activeTrackColor: AppColors.orange,
                inactiveTrackColor: AppColors.ink.withValues(alpha: 0.12),
                thumbColor: AppColors.orange,
                overlayColor: AppColors.orange.withValues(alpha: 0.18),
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

/// 全屏图片相册:左右滑动 + 双指缩放。
class _ImagePager extends StatefulWidget {
  const _ImagePager({required this.urls, required this.start});
  final List<String> urls;
  final int start;

  @override
  State<_ImagePager> createState() => _ImagePagerState();
}

class _ImagePagerState extends State<_ImagePager> {
  late final PageController _controller = PageController(initialPage: widget.start);

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.housing,
      body: SafeArea(
        child: Stack(
          children: [
            PageView.builder(
              controller: _controller,
              itemCount: widget.urls.length,
              itemBuilder: (context, i) => InteractiveViewer(
                minScale: 1,
                maxScale: 5,
                child: Center(
                  child: Image.network(
                    widget.urls[i],
                    fit: BoxFit.contain,
                    errorBuilder: (_, _, _) =>
                        Text('图片加载失败', style: AppTypography.body.copyWith(color: AppColors.ivory)),
                  ),
                ),
              ),
            ),
            Positioned(
              top: AppSpacing.s,
              right: AppSpacing.s,
              child: IconButton(
                icon: const Icon(Icons.close),
                color: AppColors.ivory,
                onPressed: () => Navigator.of(context).pop(),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

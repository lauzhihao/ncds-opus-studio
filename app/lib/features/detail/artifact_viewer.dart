import 'package:flutter/material.dart';
import 'package:just_audio/just_audio.dart';
import 'package:video_player/video_player.dart';

import '../../core/net/factory_client.dart';
import '../../core/net/models.dart';
import '../../design/components/app_card.dart';
import '../../design/components/tag_chip.dart';
import '../../design/tokens.dart';
import '../../design/typography.dart';

// 产物审看组件:按 NofArtifact.kind 分流渲染。
// image -> 内联缩略 + 点击全屏可缩放;video -> video_player 内联播放器;
// audio -> just_audio 迷你播放器;text/script/data -> 行内卡 + 点开看地址;
// dir/file -> 仅一行展示,不播放。
// url 解析:absoluteUrl 是 Future,本组件接 FactoryClient + NofArtifact 自行解析
// (需要绝对地址的 image/video/audio 用 FutureBuilder 预解析后再喂播放器/Image)。

/// 产物 kind -> 图标 + 中文名(与详情页 _kindStyle 一致,避免跨文件依赖私有符号)。
({IconData icon, String name}) artifactKindStyle(String kind) {
  switch (kind) {
    case 'script':
      return (icon: Icons.description_outlined, name: '脚本');
    case 'text':
      return (icon: Icons.notes_outlined, name: '文本');
    case 'audio':
      return (icon: Icons.graphic_eq, name: '音频');
    case 'video':
      return (icon: Icons.play_circle_outline, name: '视频');
    case 'image':
      return (icon: Icons.image_outlined, name: '图片');
    case 'data':
      return (icon: Icons.dataset_outlined, name: '数据');
    case 'dir':
      return (icon: Icons.folder_outlined, name: '目录');
    default:
      return (icon: Icons.insert_drive_file_outlined, name: '文件');
  }
}

/// 产物审看入口:根据 kind 决定内联渲染方式;无可访问地址时降级为纯展示行。
class ArtifactViewer extends StatelessWidget {
  const ArtifactViewer({super.key, required this.client, required this.artifact});

  final FactoryClient client;
  final NofArtifact artifact;

  bool get _hasUrl => artifact.url.isNotEmpty;

  @override
  Widget build(BuildContext context) {
    switch (artifact.kind) {
      case 'image':
        return _hasUrl
            ? _ImageArtifact(client: client, artifact: artifact)
            : _PlainArtifactRow(artifact: artifact);
      case 'video':
        return _hasUrl
            ? _VideoArtifact(client: client, artifact: artifact)
            : _PlainArtifactRow(artifact: artifact);
      case 'audio':
        return _hasUrl
            ? _AudioArtifact(client: client, artifact: artifact)
            : _PlainArtifactRow(artifact: artifact);
      case 'text':
      case 'script':
      case 'data':
        return _TextArtifactRow(artifact: artifact);
      default:
        // dir / file:仅一行展示,不播放。
        return _PlainArtifactRow(artifact: artifact);
    }
  }
}

// —— 公共:卡头(图标 + 标题 + kind 标签) ——

class _ArtifactHeader extends StatelessWidget {
  const _ArtifactHeader({required this.artifact, this.trailing});

  final NofArtifact artifact;
  final Widget? trailing;

  @override
  Widget build(BuildContext context) {
    final ks = artifactKindStyle(artifact.kind);
    return Row(
      crossAxisAlignment: CrossAxisAlignment.center,
      children: [
        Icon(ks.icon, size: 22, color: AppColors.orange),
        const SizedBox(width: AppSpacing.m),
        Expanded(
          child: Text(
            artifact.label.isEmpty ? ks.name : artifact.label,
            style: AppTypography.subhead,
            maxLines: 2,
            overflow: TextOverflow.ellipsis,
          ),
        ),
        const SizedBox(width: AppSpacing.s),
        if (trailing != null) trailing! else TagChip(label: ks.name, tint: AppColors.accentTopic),
      ],
    );
  }
}

/// 解析失败/加载占位:统一灰底提示。
class _MediaHint extends StatelessWidget {
  const _MediaHint({required this.text, this.isError = false});

  final String text;
  final bool isError;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.symmetric(vertical: AppSpacing.block),
      decoration: BoxDecoration(
        color: AppColors.ink.withValues(alpha: 0.04),
        borderRadius: BorderRadius.circular(AppRadii.image),
      ),
      child: Center(
        child: Text(
          text,
          style: AppTypography.caption.copyWith(
            color: isError ? AppColors.statusRed : AppColors.inkFaint,
          ),
        ),
      ),
    );
  }
}

// —— 图片:内联缩略,点击进全屏可缩放 ——

class _ImageArtifact extends StatelessWidget {
  const _ImageArtifact({required this.client, required this.artifact});

  final FactoryClient client;
  final NofArtifact artifact;

  @override
  Widget build(BuildContext context) {
    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          _ArtifactHeader(artifact: artifact),
          const SizedBox(height: AppSpacing.m),
          FutureBuilder<Uri?>(
            future: client.absoluteUrl(artifact.url),
            builder: (context, snap) {
              if (snap.connectionState == ConnectionState.waiting) {
                return const _MediaHint(text: '解析地址中…');
              }
              final uri = snap.data;
              if (uri == null) {
                return const _MediaHint(text: '地址无法解析', isError: true);
              }
              final url = uri.toString();
              return ClipRRect(
                borderRadius: BorderRadius.circular(AppRadii.image),
                child: GestureDetector(
                  onTap: () => _openFullscreen(context, url),
                  child: Image.network(
                    url,
                    fit: BoxFit.cover,
                    width: double.infinity,
                    loadingBuilder: (context, child, progress) {
                      if (progress == null) return child;
                      return const _MediaHint(text: '加载图片中…');
                    },
                    errorBuilder: (context, error, stack) =>
                        const _MediaHint(text: '图片加载失败', isError: true),
                  ),
                ),
              );
            },
          ),
        ],
      ),
    );
  }

  void _openFullscreen(BuildContext context, String url) {
    Navigator.of(context).push(
      MaterialPageRoute<void>(
        fullscreenDialog: true,
        builder: (_) => _FullscreenImage(url: url),
      ),
    );
  }
}

/// 全屏图片:深底 + InteractiveViewer 双指缩放/拖动,右上角关闭。
class _FullscreenImage extends StatelessWidget {
  const _FullscreenImage({required this.url});

  final String url;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.housing,
      body: SafeArea(
        child: Stack(
          children: [
            Positioned.fill(
              child: InteractiveViewer(
                minScale: 1,
                maxScale: 5,
                child: Center(
                  child: Image.network(
                    url,
                    fit: BoxFit.contain,
                    errorBuilder: (context, error, stack) => Text(
                      '图片加载失败',
                      style: AppTypography.body.copyWith(color: AppColors.ivory),
                    ),
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

// —— 视频:video_player 内联播放器(播放/暂停 + 进度条) ——

class _VideoArtifact extends StatefulWidget {
  const _VideoArtifact({required this.client, required this.artifact});

  final FactoryClient client;
  final NofArtifact artifact;

  @override
  State<_VideoArtifact> createState() => _VideoArtifactState();
}

class _VideoArtifactState extends State<_VideoArtifact> {
  VideoPlayerController? _controller;
  String? _error;

  @override
  void initState() {
    super.initState();
    _setup();
  }

  Future<void> _setup() async {
    try {
      final uri = await widget.client.absoluteUrl(widget.artifact.url);
      if (!mounted) return;
      if (uri == null) {
        setState(() => _error = '地址无法解析');
        return;
      }
      final c = VideoPlayerController.networkUrl(uri);
      await c.initialize();
      if (!mounted) {
        await c.dispose(); // 初始化期间已离场:别泄漏
        return;
      }
      setState(() => _controller = c);
    } catch (e) {
      if (mounted) setState(() => _error = '视频加载失败');
    }
  }

  Future<void> _toggle() async {
    final c = _controller;
    if (c == null) return;
    if (c.value.isPlaying) {
      await c.pause();
    } else {
      await c.play();
    }
    if (mounted) setState(() {}); // 刷新覆盖层(播放/暂停图标)
  }

  @override
  void dispose() {
    _controller?.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          _ArtifactHeader(artifact: widget.artifact),
          const SizedBox(height: AppSpacing.m),
          _body(),
        ],
      ),
    );
  }

  Widget _body() {
    if (_error != null) return _MediaHint(text: _error!, isError: true);
    final c = _controller;
    if (c == null || !c.value.isInitialized) {
      return const _MediaHint(text: '加载视频中…');
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        ClipRRect(
          borderRadius: BorderRadius.circular(AppRadii.image),
          child: AspectRatio(
            aspectRatio: c.value.aspectRatio == 0 ? 16 / 9 : c.value.aspectRatio,
            child: Stack(
              alignment: Alignment.center,
              children: [
                VideoPlayer(c),
                GestureDetector(
                  onTap: _toggle,
                  child: AnimatedOpacity(
                    opacity: c.value.isPlaying ? 0 : 1,
                    duration: const Duration(milliseconds: 200),
                    child: Container(
                      color: AppColors.housing.withValues(alpha: 0.25),
                      alignment: Alignment.center,
                      child: Icon(
                        Icons.play_circle_fill,
                        size: 56,
                        color: AppColors.ivory.withValues(alpha: 0.92),
                      ),
                    ),
                  ),
                ),
              ],
            ),
          ),
        ),
        const SizedBox(height: AppSpacing.s),
        VideoProgressIndicator(
          c,
          allowScrubbing: true,
          colors: VideoProgressColors(
            playedColor: AppColors.orange,
            bufferedColor: AppColors.ink.withValues(alpha: 0.20),
            backgroundColor: AppColors.ink.withValues(alpha: 0.08),
          ),
        ),
      ],
    );
  }
}

// —— 音频:just_audio 迷你播放器(播放/暂停 + 进度) ——

class _AudioArtifact extends StatefulWidget {
  const _AudioArtifact({required this.client, required this.artifact});

  final FactoryClient client;
  final NofArtifact artifact;

  @override
  State<_AudioArtifact> createState() => _AudioArtifactState();
}

class _AudioArtifactState extends State<_AudioArtifact> {
  final AudioPlayer _player = AudioPlayer();
  String? _error;
  bool _ready = false;

  @override
  void initState() {
    super.initState();
    _setup();
  }

  Future<void> _setup() async {
    try {
      final uri = await widget.client.absoluteUrl(widget.artifact.url);
      if (!mounted) return;
      if (uri == null) {
        setState(() => _error = '地址无法解析');
        return;
      }
      await _player.setUrl(uri.toString());
      if (!mounted) return;
      setState(() => _ready = true);
    } catch (e) {
      if (mounted) setState(() => _error = '音频加载失败');
    }
  }

  @override
  void dispose() {
    _player.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          _ArtifactHeader(artifact: widget.artifact),
          const SizedBox(height: AppSpacing.m),
          _body(),
        ],
      ),
    );
  }

  Widget _body() {
    if (_error != null) return _MediaHint(text: _error!, isError: true);
    if (!_ready) return const _MediaHint(text: '加载音频中…');
    return Row(
      children: [
        // 播放/暂停:订阅 playerState 拿播放状态。
        StreamBuilder<PlayerState>(
          stream: _player.playerStateStream,
          builder: (context, snap) {
            final state = snap.data;
            final completed = state?.processingState == ProcessingState.completed;
            final playing = (state?.playing ?? false) && !completed;
            return IconButton(
              iconSize: 40,
              color: AppColors.orange,
              icon: Icon(playing ? Icons.pause_circle_filled : Icons.play_circle_fill),
              onPressed: () async {
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
        Expanded(child: _AudioProgress(player: _player)),
      ],
    );
  }
}

/// 音频进度条 + 时间。position/duration 各自订阅,duration 可能为 null(流式)。
class _AudioProgress extends StatelessWidget {
  const _AudioProgress({required this.player});

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
            var position = posSnap.data ?? Duration.zero;
            if (position > duration) position = duration;
            final maxMs = duration.inMilliseconds;
            return Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                SliderTheme(
                  data: SliderTheme.of(context).copyWith(
                    trackHeight: 3,
                    activeTrackColor: AppColors.orange,
                    inactiveTrackColor: AppColors.ink.withValues(alpha: 0.12),
                    thumbColor: AppColors.orange,
                    overlayColor: AppColors.orange.withValues(alpha: 0.18),
                    thumbShape: const RoundSliderThumbShape(enabledThumbRadius: 6),
                  ),
                  child: Slider(
                    min: 0,
                    max: maxMs <= 0 ? 1 : maxMs.toDouble(),
                    value: maxMs <= 0
                        ? 0
                        : position.inMilliseconds.clamp(0, maxMs).toDouble(),
                    onChanged: maxMs <= 0
                        ? null
                        : (v) => player.seek(Duration(milliseconds: v.round())),
                  ),
                ),
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: AppSpacing.s),
                  child: Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      Text(_fmt(position),
                          style: AppTypography.monoXS.copyWith(color: AppColors.inkMuted)),
                      Text(_fmt(duration),
                          style: AppTypography.monoXS.copyWith(color: AppColors.inkMuted)),
                    ],
                  ),
                ),
              ],
            );
          },
        );
      },
    );
  }

  static String _fmt(Duration d) {
    final m = d.inMinutes.remainder(60).toString().padLeft(2, '0');
    final s = d.inSeconds.remainder(60).toString().padLeft(2, '0');
    return '$m:$s';
  }
}

// —— 文本类(text/script/data):行内卡,点开展开 url 文本 ——

class _TextArtifactRow extends StatefulWidget {
  const _TextArtifactRow({required this.artifact});

  final NofArtifact artifact;

  @override
  State<_TextArtifactRow> createState() => _TextArtifactRowState();
}

class _TextArtifactRowState extends State<_TextArtifactRow> {
  bool _expanded = false;

  @override
  Widget build(BuildContext context) {
    final art = widget.artifact;
    final hasUrl = art.url.isNotEmpty;
    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          InkWell(
            onTap: hasUrl ? () => setState(() => _expanded = !_expanded) : null,
            child: _ArtifactHeader(
              artifact: art,
              trailing: hasUrl
                  ? Icon(
                      _expanded ? Icons.expand_less : Icons.expand_more,
                      size: 20,
                      color: AppColors.inkMuted,
                    )
                  : null,
            ),
          ),
          if (_expanded && hasUrl) ...[
            const SizedBox(height: AppSpacing.s),
            Container(
              width: double.infinity,
              padding: const EdgeInsets.all(AppSpacing.m),
              decoration: BoxDecoration(
                color: AppColors.ink.withValues(alpha: 0.04),
                borderRadius: BorderRadius.circular(AppRadii.thumb),
              ),
              child: SelectableText(
                art.url,
                style: AppTypography.monoXS.copyWith(color: AppColors.inkMuted),
              ),
            ),
          ],
          if (!hasUrl) ...[
            const SizedBox(height: AppSpacing.xs),
            Text('暂无可访问地址',
                style: AppTypography.caption.copyWith(color: AppColors.inkFaint)),
          ],
        ],
      ),
    );
  }
}

// —— 纯展示行(dir/file,或媒体类缺地址降级):不播放 ——

class _PlainArtifactRow extends StatelessWidget {
  const _PlainArtifactRow({required this.artifact});

  final NofArtifact artifact;

  @override
  Widget build(BuildContext context) {
    final hasUrl = artifact.url.isNotEmpty;
    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          _ArtifactHeader(artifact: artifact),
          const SizedBox(height: AppSpacing.xs),
          if (hasUrl)
            Text(
              artifact.url,
              style: AppTypography.monoXS.copyWith(color: AppColors.inkMuted),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
            )
          else
            Text('暂无可访问地址',
                style: AppTypography.caption.copyWith(color: AppColors.inkFaint)),
        ],
      ),
    );
  }
}

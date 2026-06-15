// 吴道子分镜面板 —— 复刻 iOS WudaoziStoryboardPanel(AgentResultPanels.swift)。
// 不丢句质检(覆盖率)+ 节奏一览(拍型色条)+ 胶片式分镜卡(配图拍=缩略图 / 纯字幕拍=深色字幕卡)。
// 渲染管线代号(拍型 kind / 运镜 motion)展示前翻成人话;配图缩略走详情页传入的 figureBase。

import 'package:flutter/material.dart';

import '../../core/net/models.dart';
import '../../design/components/app_card.dart';
import '../../design/components/tag_chip.dart';
import '../../design/tokens.dart';
import '../../design/typography.dart';
import '../compose/compose_screen.dart';
import '../home/agent_catalog.dart';
import 'detail_widgets.dart';

class WudaoziStoryboardPanel extends StatelessWidget {
  const WudaoziStoryboardPanel({
    super.key,
    required this.result,
    required this.agent,
    this.figureBase,
  });

  final WudaoziResult result;
  final AgentInfo agent;
  final String? figureBase; // 分镜实例目录的文件服务基址(配图缩略由此拼出)

  List<WudaoziBeat> get _beats => result.beats ?? const <WudaoziBeat>[];

  @override
  Widget build(BuildContext context) {
    final beats = _beats;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (result.qc != null) _qcCard(context),
        if (beats.isNotEmpty) ...[
          if (result.qc != null) const SizedBox(height: AppSpacing.cardInner),
          _rhythmCard(),
          for (int i = 0; i < beats.length; i++) ...[
            const SizedBox(height: AppSpacing.cardInner),
            _beatCard(i, beats[i]),
          ],
        ],
      ],
    );
  }

  // —— 质检卡(尾行带「去渲染成片」入口)——
  Widget _qcCard(BuildContext context) {
    final qc = result.qc!;
    final pass = qc.verdict == 'pass';
    final warnings = qc.warnings ?? const <String>[];
    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Text('分镜质检', style: AppTypography.titleM),
              const Spacer(),
              TagChip(
                label: pass ? '不丢句 通过' : '不丢句 未过',
                icon: pass ? Icons.verified : Icons.warning_rounded,
                tint: pass ? AppColors.statusAdopted : AppColors.statusRed,
              ),
            ],
          ),
          if (qc.ratio != null) ...[
            const SizedBox(height: AppSpacing.s),
            _ratioGauge(qc.ratio!.clamp(0, 1)),
          ],
          for (final w in warnings) ...[
            const SizedBox(height: AppSpacing.xs),
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Icon(Icons.error_outline, size: 13, color: AppColors.amber),
                const SizedBox(width: 4),
                Expanded(child: Text(w, style: AppTypography.caption.copyWith(color: AppColors.amber))),
              ],
            ),
          ],
          const SizedBox(height: AppSpacing.m),
          Row(
            children: [
              Expanded(
                child: Text('吴道子只出分镜;成片需渲染合成',
                    style: AppTypography.caption2.copyWith(color: AppColors.ink.withValues(alpha: 0.35))),
              ),
              GestureDetector(
                onTap: () => Navigator.of(context).push(
                  MaterialPageRoute<void>(builder: (_) => const ComposeScreen()),
                ),
                child: Container(
                  padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
                  decoration: ShapeDecoration(
                      color: AppColors.orange.withValues(alpha: 0.14), shape: const StadiumBorder()),
                  child: Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Icon(Icons.movie_outlined, size: 13, color: AppColors.orange),
                      const SizedBox(width: 4),
                      Text('去渲染成片',
                          style: AppTypography.caption.copyWith(
                              fontWeight: FontWeight.w600, color: AppColors.orange)),
                    ],
                  ),
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }

  Widget _ratioGauge(double r) {
    final color = r >= 0.98 ? AppColors.statusAdopted : (r >= 0.9 ? AppColors.amber : AppColors.statusRed);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Text('原文覆盖率',
                style: AppTypography.caption2.copyWith(color: AppColors.ink.withValues(alpha: 0.5))),
            const Spacer(),
            Text('${(r * 100).toStringAsFixed(1)}%',
                style: AppTypography.caption2.copyWith(fontWeight: FontWeight.w700, color: color)),
          ],
        ),
        const SizedBox(height: 4),
        ClipRRect(
          borderRadius: BorderRadius.circular(3),
          child: LinearProgressIndicator(
            value: r.toDouble(),
            minHeight: 5,
            backgroundColor: AppColors.ink.withValues(alpha: 0.1),
            valueColor: AlwaysStoppedAnimation<Color>(color),
          ),
        ),
      ],
    );
  }

  // —— 节奏一览(拍型分布色条)——
  Widget _rhythmCard() {
    final beats = _beats;
    final subs = beats.where((b) => b.figure == null).length;
    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Text('节奏一览', style: AppTypography.titleM),
              const Spacer(),
              Text('${beats.length} 拍${subs > 0 ? ' · 纯字幕 $subs' : ''}',
                  style: AppTypography.caption.copyWith(color: AppColors.ink.withValues(alpha: 0.45))),
            ],
          ),
          const SizedBox(height: AppSpacing.s),
          Row(
            children: [
              for (final b in beats)
                Expanded(
                  child: Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 1.5),
                    child: Container(
                      height: 14,
                      decoration: BoxDecoration(
                        color: _kindColor(b.kind).withValues(alpha: b.figure == null ? 0.4 : 1),
                        borderRadius: BorderRadius.circular(3),
                      ),
                    ),
                  ),
                ),
            ],
          ),
          const SizedBox(height: AppSpacing.s),
          Row(
            children: [
              _legendDot('钩子', _kindColor('hook')),
              const SizedBox(width: AppSpacing.m),
              _legendDot('正文', _kindColor('talk')),
              const SizedBox(width: AppSpacing.m),
              _legendDot('金句', _kindColor('punch')),
              const SizedBox(width: AppSpacing.m),
              _legendDot('结尾', _kindColor('ending')),
              const Spacer(),
              Text('浅色 = 纯字幕拍',
                  style: AppTypography.caption2.copyWith(color: AppColors.ink.withValues(alpha: 0.4))),
            ],
          ),
          const SizedBox(height: AppSpacing.m),
          Divider(color: AppColors.ink.withValues(alpha: 0.08), height: 1),
          const SizedBox(height: AppSpacing.m),
          ContentToolbelt(
            text: beats.map((b) => b.zh ?? '').where((s) => s.isNotEmpty).join('\n'),
            accent: agent.accent,
          ),
        ],
      ),
    );
  }

  Widget _legendDot(String label, Color color) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(
          width: 8,
          height: 8,
          decoration: BoxDecoration(color: color, borderRadius: BorderRadius.circular(2)),
        ),
        const SizedBox(width: 3),
        Text(label, style: AppTypography.caption2.copyWith(color: AppColors.ink.withValues(alpha: 0.55))),
      ],
    );
  }

  // —— 分镜卡 ——
  Widget _beatCard(int i, WudaoziBeat b) {
    return b.figure == null ? _subtitleBeatCard(i, b) : _figureBeatCard(i, b);
  }

  Widget _figureBeatCard(int i, WudaoziBeat b) {
    final iconCount = b.icons?.length ?? 0;
    final extras = (b.title?.isNotEmpty ?? false) || (b.tag?.isNotEmpty ?? false) || iconCount > 0;
    final card = AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          _beatHeader(i, b, dark: false),
          const SizedBox(height: AppSpacing.s),
          _thumb(b),
          const SizedBox(height: AppSpacing.s),
          Text(b.zh ?? '', style: AppTypography.bodySerif.copyWith(height: 1.6)),
          if (extras) ...[
            const SizedBox(height: AppSpacing.s),
            Row(
              children: [
                if (b.title?.isNotEmpty ?? false)
                  Text('字卡 · ${b.title}',
                      style: AppTypography.caption2.copyWith(
                          fontWeight: FontWeight.w500, color: _kindColor(b.kind))),
                if (b.tag?.isNotEmpty ?? false) ...[
                  const SizedBox(width: AppSpacing.s),
                  TagChip(label: b.tag!, tint: agent.accent),
                ],
                const Spacer(),
                if (iconCount > 0)
                  Text('+$iconCount 配饰',
                      style: AppTypography.caption2.copyWith(color: AppColors.ink.withValues(alpha: 0.35))),
              ],
            ),
          ],
        ],
      ),
    );
    if (b.kind == 'punch') {
      return Stack(
        children: [
          card,
          Positioned.fill(
            child: IgnorePointer(
              child: Container(
                decoration: BoxDecoration(
                  borderRadius: const BorderRadius.all(AppRadii.cardR),
                  border: Border.all(color: _kindColor('punch').withValues(alpha: 0.45), width: 1.5),
                ),
              ),
            ),
          ),
        ],
      );
    }
    return card;
  }

  Widget _subtitleBeatCard(int i, WudaoziBeat b) {
    return AppCard(
      dark: true,
      child: Column(
        children: [
          _beatHeader(i, b, dark: true),
          const SizedBox(height: AppSpacing.s),
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 4),
            child: Text(
              b.zh ?? '',
              textAlign: TextAlign.center,
              style: AppTypography.bodySerif.copyWith(fontWeight: FontWeight.w700, color: AppColors.ivory),
            ),
          ),
          const SizedBox(height: AppSpacing.s),
          Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Icon(Icons.closed_caption_outlined, size: 13, color: AppColors.sand.withValues(alpha: 0.5)),
              const SizedBox(width: 4),
              Text('纯字幕拍 · 成片中无配图',
                  style: AppTypography.caption2.copyWith(color: AppColors.sand.withValues(alpha: 0.5))),
            ],
          ),
        ],
      ),
    );
  }

  Widget _beatHeader(int i, WudaoziBeat b, {required bool dark}) {
    final kindC = _kindColor(b.kind);
    final badgeColor = dark ? AppColors.sand : kindC;
    final m = b.motion;
    return Row(
      children: [
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
          decoration: ShapeDecoration(
              color: badgeColor.withValues(alpha: 0.15), shape: const StadiumBorder()),
          child: Text('第 ${i + 1} 拍 · ${_kindLabel(b.kind)}',
              style: AppTypography.caption2.copyWith(
                  fontWeight: FontWeight.w600,
                  color: dark ? AppColors.sand.withValues(alpha: 0.85) : kindC)),
        ),
        const Spacer(),
        if (m != null && m != 'subtitle_only')
          Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(_motionIcon(m),
                  size: 12,
                  color: dark ? AppColors.sand.withValues(alpha: 0.7) : AppColors.ink.withValues(alpha: 0.5)),
              const SizedBox(width: 3),
              Text(_motionLabel(m),
                  style: AppTypography.caption2.copyWith(
                      color: dark
                          ? AppColors.sand.withValues(alpha: 0.7)
                          : AppColors.ink.withValues(alpha: 0.5))),
            ],
          ),
      ],
    );
  }

  /// 配图缩略:产物目录里的真图;拿不到就深色占位 + 图名,优雅降级。
  Widget _thumb(WudaoziBeat b) {
    final f = b.figure;
    if (f == null) return const SizedBox.shrink();
    final url = (figureBase != null && figureBase!.isNotEmpty)
        ? '${figureBase!}${figureBase!.endsWith('/') ? '' : '/'}$f'
        : null;
    return ClipRRect(
      borderRadius: BorderRadius.circular(AppRadii.image),
      child: Container(
        height: 110,
        width: double.infinity,
        color: AppColors.housing.withValues(alpha: 0.92),
        child: url == null
            ? _thumbPlaceholder(f)
            : Image.network(
                url,
                fit: BoxFit.cover,
                errorBuilder: (_, _, _) => _thumbPlaceholder(f),
              ),
      ),
    );
  }

  Widget _thumbPlaceholder(String path) {
    final name = path.split('/').last.split('.').first;
    return Column(
      mainAxisAlignment: MainAxisAlignment.center,
      children: [
        Icon(Icons.photo, size: 22, color: AppColors.sand.withValues(alpha: 0.5)),
        const SizedBox(height: 4),
        Text(name, style: AppTypography.caption2.copyWith(color: AppColors.sand.withValues(alpha: 0.45))),
      ],
    );
  }

  // —— 渲染管线代号 → 人话 ——
  String _kindLabel(String? k) {
    switch (k) {
      case 'hook':
        return '钩子';
      case 'punch':
        return '金句';
      case 'ending':
        return '结尾';
      default:
        return '正文';
    }
  }

  Color _kindColor(String? k) {
    switch (k) {
      case 'hook':
        return AppColors.orange;
      case 'punch':
        return const Color(0xFFA32E2E); // 深红(对齐 iOS 0.64/0.18/0.18)
      case 'ending':
        return const Color(0xFF477A33); // 墨绿(对齐 iOS 0.28/0.48/0.20)
      default:
        return AppColors.ink.withValues(alpha: 0.45);
    }
  }

  String _motionLabel(String m) {
    switch (m) {
      case 'zoom_in':
        return '推近';
      case 'zoom_out':
        return '拉远';
      case 'slide_left':
        return '左滑';
      case 'slide_right':
        return '右滑';
      case 'slide_up':
        return '上滑';
      case 'slide_down':
        return '下滑';
      case 'hold':
        return '定格';
      case 'stamp':
        return '钉章';
      case 'list_pop':
        return '逐条弹出';
      default:
        return m;
    }
  }

  IconData _motionIcon(String m) {
    switch (m) {
      case 'zoom_in':
        return Icons.zoom_in;
      case 'zoom_out':
        return Icons.zoom_out;
      case 'slide_left':
        return Icons.arrow_back;
      case 'slide_right':
        return Icons.arrow_forward;
      case 'slide_up':
        return Icons.arrow_upward;
      case 'slide_down':
        return Icons.arrow_downward;
      case 'hold':
        return Icons.pause_circle_outline;
      case 'stamp':
        return Icons.approval;
      case 'list_pop':
        return Icons.format_list_bulleted;
      default:
        return Icons.air;
    }
  }
}

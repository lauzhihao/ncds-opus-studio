// 鬼谷子选题库面板 —— 复刻 iOS GuiguziTopicsPanel(AgentResultPanels.swift)。
// 排名 + 潜力分 + 母题/理由/对标,带一个真实的生产链动作:选题一键「交给柳永」写稿。

import 'package:flutter/material.dart';

import '../../core/net/factory_client.dart';
import '../../core/net/models.dart';
import '../../design/components/app_card.dart';
import '../../design/tokens.dart';
import '../../design/typography.dart';
import '../home/agent_catalog.dart';
import 'detail_widgets.dart';

class GuiguziTopicsPanel extends StatefulWidget {
  const GuiguziTopicsPanel({
    super.key,
    required this.result,
    required this.agent,
    required this.client,
  });

  final GuiguziResult result;
  final AgentInfo agent;
  final FactoryClient client;

  @override
  State<GuiguziTopicsPanel> createState() => _GuiguziTopicsPanelState();
}

class _GuiguziTopicsPanelState extends State<GuiguziTopicsPanel> {
  final Set<int> _sent = <int>{};
  final Set<int> _sending = <int>{};
  String? _err;

  List<GuiguziTopic> get _topics => widget.result.topics ?? const <GuiguziTopic>[];

  /// 下游编剧(柳永),从内置目录取(找不到就用本 agent,不至于崩样式)。名字/主题色都从这取,
  /// agent 改名后按钮文案自动跟着变。
  AgentInfo get _liuyong =>
      agentCatalog.firstWhere((a) => a.cmd == 'liuyong', orElse: () => widget.agent);
  Color get _liuyongAccent => _liuyong.accent;

  @override
  Widget build(BuildContext context) {
    final topics = _topics;
    if (topics.isEmpty) return const SizedBox.shrink();
    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Text('选题库', style: AppTypography.titleM),
              const Spacer(),
              Text('${topics.length} 条',
                  style: AppTypography.caption.copyWith(color: AppColors.ink.withValues(alpha: 0.45))),
            ],
          ),
          for (int i = 0; i < topics.length; i++) ...[
            if (i > 0) ...[
              const SizedBox(height: AppSpacing.s),
              Divider(color: AppColors.ink.withValues(alpha: 0.08), height: 1),
              const SizedBox(height: AppSpacing.s),
            ] else
              const SizedBox(height: AppSpacing.m),
            _topicRow(i, topics[i]),
          ],
          if (_err != null) ...[
            const SizedBox(height: AppSpacing.s),
            Text(_err!, style: AppTypography.caption.copyWith(color: AppColors.statusRed)),
          ],
          const SizedBox(height: AppSpacing.m),
          Divider(color: AppColors.ink.withValues(alpha: 0.08), height: 1),
          const SizedBox(height: AppSpacing.m),
          ContentToolbelt(text: _topicsText, accent: widget.agent.accent, meta: '${topics.length} 条选题清单'),
        ],
      ),
    );
  }

  Widget _topicRow(int i, GuiguziTopic t) {
    final accent = widget.agent.accent;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Container(
              width: 20,
              height: 20,
              alignment: Alignment.center,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                color: accent.withValues(alpha: i < 3 ? 1 : 0.45),
              ),
              child: Text('${i + 1}',
                  style: AppTypography.caption.copyWith(
                      fontWeight: FontWeight.w700, color: const Color(0xFFFFFFFF))),
            ),
            const SizedBox(width: AppSpacing.s),
            Expanded(
              child: Text(t.title ?? '(无标题)',
                  style: AppTypography.subhead.copyWith(fontWeight: FontWeight.w700)),
            ),
            if (t.potential != null) ...[
              const SizedBox(width: AppSpacing.s),
              _potentialChip(t.potential!),
            ],
          ],
        ),
        if (t.motif != null && t.motif!.isNotEmpty) ...[
          const SizedBox(height: AppSpacing.s),
          Text('母题 · ${t.motif}',
              style: AppTypography.caption.copyWith(color: AppColors.ink.withValues(alpha: 0.65))),
        ],
        if (t.why != null && t.why!.isNotEmpty) ...[
          const SizedBox(height: AppSpacing.xs),
          Text(t.why!, style: AppTypography.caption.copyWith(color: accent.withValues(alpha: 0.9))),
        ],
        const SizedBox(height: AppSpacing.s),
        Row(
          children: [
            if (t.source != null && t.source!.isNotEmpty)
              Flexible(
                child: Text('对标 · ${t.source}',
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: AppTypography.caption2.copyWith(color: AppColors.ink.withValues(alpha: 0.4))),
              ),
            const Spacer(),
            _handoffButton(i, t),
          ],
        ),
      ],
    );
  }

  Widget _potentialChip(double p) {
    final c = _potentialColor(p);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 3),
      decoration: ShapeDecoration(color: c.withValues(alpha: 0.15), shape: const StadiumBorder()),
      child: Text('潜力 ${p.toInt()}',
          style: AppTypography.caption2.copyWith(fontWeight: FontWeight.w700, color: c)),
    );
  }

  /// 生产链下一站:把选题直接投给柳永写稿。
  Widget _handoffButton(int i, GuiguziTopic t) {
    if (_sent.contains(i)) {
      return Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(Icons.check, size: 13, color: AppColors.statusAdopted),
          const SizedBox(width: 3),
          Text('已交${_liuyong.name}',
              style: AppTypography.caption2.copyWith(
                  fontWeight: FontWeight.w600, color: AppColors.statusAdopted)),
        ],
      );
    }
    final disabled = _sending.contains(i) || (t.title ?? '').isEmpty;
    return Opacity(
      opacity: _sending.contains(i) ? 0.5 : 1,
      child: GestureDetector(
        onTap: disabled ? null : () => _handOff(i, t),
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 4),
          decoration: ShapeDecoration(
              color: _liuyongAccent.withValues(alpha: 0.14), shape: const StadiumBorder()),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(Icons.send, size: 12, color: _liuyongAccent),
              const SizedBox(width: 4),
              Text('交给${_liuyong.name}',
                  style: AppTypography.caption2.copyWith(
                      fontWeight: FontWeight.w600, color: _liuyongAccent)),
            ],
          ),
        ),
      ),
    );
  }

  Color _potentialColor(double p) {
    if (p >= 8) return AppColors.statusAdopted;
    if (p >= 6) return AppColors.amber;
    return AppColors.ink.withValues(alpha: 0.5);
  }

  /// 选题清单纯文本版:复制出去还能看。
  String get _topicsText {
    final lines = <String>[];
    for (int i = 0; i < _topics.length; i++) {
      final t = _topics[i];
      var line = '${i + 1}. ${t.title ?? ''}';
      if (t.potential != null) line += '(潜力 ${t.potential!.toInt()})';
      if (t.why != null && t.why!.isNotEmpty) line += ' — ${t.why}';
      lines.add(line);
    }
    return lines.join('\n');
  }

  Future<void> _handOff(int i, GuiguziTopic t) async {
    final title = t.title;
    if (title == null || title.isEmpty) return;
    setState(() {
      _sending.add(i);
      _err = null;
    });
    try {
      await widget.client.createTask(cmd: 'liuyong', params: {'topic': title});
      if (mounted) setState(() => _sent.add(i));
    } catch (e) {
      if (mounted) setState(() => _err = '$e');
    } finally {
      if (mounted) setState(() => _sending.remove(i));
    }
  }
}

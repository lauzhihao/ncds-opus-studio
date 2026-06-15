// 卧龙编排战报面板 —— 复刻 iOS WolongReportPanel(AgentResultPanels.swift)。
// 产出条数 + 编排日志末尾(可折叠)+ 进 round 战报页。本 result 只是 legacy 摘要,
// 逐轮产线/战报/止损在 round 战报页(RoundsScreen)。

import 'package:flutter/material.dart';

import '../../core/net/models.dart';
import '../../design/components/app_card.dart';
import '../../design/components/tag_chip.dart';
import '../../design/tokens.dart';
import '../../design/typography.dart';
import '../home/agent_catalog.dart';
import '../rounds/rounds_screen.dart';
import 'detail_widgets.dart';

class WolongReportPanel extends StatelessWidget {
  const WolongReportPanel({super.key, required this.result, required this.agent});

  final WolongResult result;
  final AgentInfo agent;

  @override
  Widget build(BuildContext context) {
    final tail = result.tail ?? const <String>[];
    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Text('编排战报', style: AppTypography.titleM),
              const Spacer(),
              TagChip(label: '产出 ${result.count ?? 0} 条', icon: Icons.inventory_2, tint: agent.accent),
            ],
          ),
          const SizedBox(height: AppSpacing.s),
          Text('各工序成果在对应 agent 的收件箱里逐条验收',
              style: AppTypography.caption.copyWith(color: AppColors.inkMuted)),
          if (tail.isNotEmpty) ...[
            const SizedBox(height: AppSpacing.m),
            DetailDisclosure(
              icon: Icons.terminal,
              label: '编排日志末尾',
              labelStyle:
                  AppTypography.subhead.copyWith(fontWeight: FontWeight.w500, color: AppColors.inkMuted),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  for (final line in tail)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 3),
                      child: Text(line,
                          style: AppTypography.monoXS.copyWith(color: AppColors.ink.withValues(alpha: 0.6))),
                    ),
                ],
              ),
            ),
          ],
          const SizedBox(height: AppSpacing.m),
          Divider(color: AppColors.ink.withValues(alpha: 0.08), height: 1),
          const SizedBox(height: AppSpacing.m),
          GestureDetector(
            behavior: HitTestBehavior.opaque,
            onTap: () => Navigator.of(context).push(
              MaterialPageRoute<void>(builder: (_) => const RoundsScreen()),
            ),
            child: Row(
              children: [
                Icon(Icons.flag_outlined, size: 18, color: agent.accent),
                const SizedBox(width: AppSpacing.s),
                Text('round 战报',
                    style: AppTypography.subhead.copyWith(fontWeight: FontWeight.w500, color: agent.accent)),
                const Spacer(),
                Icon(Icons.chevron_right, size: 16, color: AppColors.ink.withValues(alpha: 0.25)),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

// 伯牙声音床面板 —— 复刻 iOS BoyaSoundPanel(AgentResultPanels.swift)。
// master 成品试听(内联播放器)+ 听感质检 + 声音方案(BGM / 音效 cue)。
// master 可播放地址由详情页从产物清单 kind=audio 那条解析后传入。

import 'package:flutter/material.dart';

import '../../core/net/models.dart';
import '../../design/components/app_card.dart';
import '../../design/components/tag_chip.dart';
import '../../design/tokens.dart';
import '../../design/typography.dart';
import '../home/agent_catalog.dart';
import 'detail_widgets.dart';

class BoyaSoundPanel extends StatelessWidget {
  const BoyaSoundPanel({super.key, required this.result, required this.agent, this.masterUrl});

  final BoyaResult result;
  final AgentInfo agent;
  final String? masterUrl;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _masterCard(),
        const SizedBox(height: AppSpacing.block),
        _mixCard(),
      ],
    );
  }

  Widget _masterCard() {
    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Text('成品混音', style: AppTypography.titleM),
              const Spacer(),
              if (result.scene != null && result.scene!.isNotEmpty)
                TagChip(label: result.scene!, icon: Icons.theater_comedy, tint: agent.accent),
            ],
          ),
          const SizedBox(height: AppSpacing.m),
          InlineAudio(url: masterUrl, title: 'master.mp3', subtitle: _voiceLine, accent: agent.accent),
          _auditionRow(),
        ],
      ),
    );
  }

  String get _voiceLine {
    final parts = <String>[];
    final clips = result.voice?.clips;
    final dur = result.voice?.durationS;
    if (clips != null) parts.add('$clips 句人声');
    if (dur != null) parts.add('${dur.toStringAsFixed(0)} 秒');
    return parts.join(' · ');
  }

  Widget _auditionRow() {
    final a = result.audition;
    if (a == null) return const SizedBox.shrink();
    final ok = a.verdict == 'ok';
    final color = ok ? AppColors.statusAdopted : AppColors.amber;
    final notes = a.notes ?? const <String>[];
    return Padding(
      padding: const EdgeInsets.only(top: AppSpacing.m),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(ok ? Icons.verified : Icons.warning_amber_rounded, size: 15, color: color),
              const SizedBox(width: 4),
              Text(ok ? '听感质检 通过' : '听感质检 提醒',
                  style: AppTypography.caption.copyWith(fontWeight: FontWeight.w600, color: color)),
            ],
          ),
          for (final n in notes) ...[
            const SizedBox(height: AppSpacing.xs),
            Text('· $n',
                style: AppTypography.caption2.copyWith(color: AppColors.ink.withValues(alpha: 0.55))),
          ],
        ],
      ),
    );
  }

  Widget _mixCard() {
    final cues = result.sfx ?? const <BoyaSfxCue>[];
    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('声音方案', style: AppTypography.titleM),
          const SizedBox(height: AppSpacing.m),
          _bgmRow(),
          const SizedBox(height: AppSpacing.m),
          if (cues.isEmpty)
            Row(
              children: [
                SizedBox(width: 22, child: Icon(Icons.volume_up, size: 18, color: agent.accent)),
                const SizedBox(width: AppSpacing.s),
                Text('无音效 cue',
                    style: AppTypography.subhead.copyWith(
                        fontWeight: FontWeight.w400, color: AppColors.ink.withValues(alpha: 0.45))),
              ],
            )
          else
            DetailDisclosure(
              label: '音效 · ${cues.length} 处',
              labelStyle: AppTypography.subhead.copyWith(fontWeight: FontWeight.w500),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  for (int i = 0; i < cues.length; i++) ...[
                    if (i > 0) const SizedBox(height: AppSpacing.s),
                    _cueRow(cues[i]),
                  ],
                ],
              ),
            ),
        ],
      ),
    );
  }

  Widget _bgmRow() {
    final bgm = result.bgm;
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        SizedBox(width: 22, child: Icon(Icons.music_note, size: 18, color: agent.accent)),
        const SizedBox(width: AppSpacing.s),
        Expanded(
          child: (bgm != null && bgm.file != null)
              ? Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Flexible(
                          child: Text(bgm.file!.split('/').last,
                              style: AppTypography.subhead.copyWith(fontWeight: FontWeight.w500),
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis),
                        ),
                        if (bgm.volumeDb != null) ...[
                          const SizedBox(width: AppSpacing.s),
                          Text('${bgm.volumeDb!.toStringAsFixed(0)} dB',
                              style: AppTypography.caption2.copyWith(
                                  color: AppColors.ink.withValues(alpha: 0.45))),
                        ],
                      ],
                    ),
                    if (bgm.reason != null && bgm.reason!.isNotEmpty)
                      Text(bgm.reason!,
                          style: AppTypography.caption2.copyWith(
                              color: AppColors.ink.withValues(alpha: 0.45))),
                  ],
                )
              : Text('未配 BGM(库内无可用)',
                  style: AppTypography.subhead.copyWith(
                      fontWeight: FontWeight.w400, color: AppColors.ink.withValues(alpha: 0.45))),
        ),
      ],
    );
  }

  Widget _cueRow(BoyaSfxCue c) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Text('#${c.beat ?? 0}',
                style: AppTypography.caption2.copyWith(fontWeight: FontWeight.w700, color: agent.accent)),
            const SizedBox(width: AppSpacing.s),
            Flexible(
              child: Text(c.cue ?? '',
                  style: AppTypography.caption.copyWith(fontWeight: FontWeight.w500),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis),
            ),
            if (c.timeS != null) ...[
              const SizedBox(width: AppSpacing.s),
              Text('@ ${c.timeS!.toStringAsFixed(1)}s',
                  style: AppTypography.caption2.copyWith(color: AppColors.ink.withValues(alpha: 0.45))),
            ],
          ],
        ),
        if (c.reason != null && c.reason!.isNotEmpty)
          Text(c.reason!,
              style: AppTypography.caption2.copyWith(color: AppColors.ink.withValues(alpha: 0.4))),
      ],
    );
  }
}

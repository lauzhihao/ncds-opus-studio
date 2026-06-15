import 'package:flutter/material.dart';

import '../../core/clipboard/share_link_parser.dart';
import '../../design/components/app_card.dart';
import '../../design/components/decision_button.dart';
import '../../design/components/status_light.dart';
import '../../design/components/tag_chip.dart';
import '../../design/liquid_glass.dart';
import '../../design/tokens.dart';
import '../../design/typography.dart';

/// 设计系统画廊:集中展示 token / 组件 / Liquid Glass(含降级开关),
/// 供设计走查与跨端一致性对比。从收件箱右上角调色板入口进入。
class DesignGallery extends StatefulWidget {
  const DesignGallery({super.key});

  @override
  State<DesignGallery> createState() => _DesignGalleryState();
}

class _DesignGalleryState extends State<DesignGallery> {
  LightColor _light = LightColor.green;
  bool _glass = true;

  static const Map<LightColor, String> _statusWord = <LightColor, String>{
    LightColor.green: '空闲',
    LightColor.amber: '推理中',
    LightColor.red: '等待批准',
  };

  @override
  Widget build(BuildContext context) {
    final ShareLink? sample = ShareLinkParser.detect(
      '看看这个 https://v.douyin.com/abc123/ 和 https://b23.tv/xyz',
    );

    return Scaffold(
      appBar: AppBar(
        backgroundColor: AppColors.sand,
        title: Text('设计系统', style: AppTypography.navTitle),
        foregroundColor: AppColors.ink,
      ),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(
          AppSpacing.pageH, AppSpacing.pageTop, AppSpacing.pageH, AppSpacing.pageBottom,
        ),
        children: [
          Row(
            children: [
              StatusLight(color: _light, breathing: _light != LightColor.green),
              const SizedBox(width: AppSpacing.m),
              Text(_statusWord[_light]!, style: AppTypography.displayTitle),
            ],
          ),
          const SizedBox(height: AppSpacing.s),
          Wrap(
            spacing: AppSpacing.s,
            children: [
              for (final LightColor s in LightColor.values)
                GestureDetector(
                  onTap: () => setState(() => _light = s),
                  child: TagChip(label: _statusWord[s]!, tint: s.color),
                ),
            ],
          ),
          const SizedBox(height: AppSpacing.block),
          AppCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('分镜质检', style: AppTypography.titleM),
                const SizedBox(height: AppSpacing.s),
                Text(
                  '这是一段用 Fraunces 衬线渲染的正文,跨 iOS / Android 由 Flutter 引擎逐像素一致绘制。',
                  style: AppTypography.bodySerif,
                ),
                const SizedBox(height: AppSpacing.m),
                Wrap(
                  spacing: AppSpacing.s,
                  runSpacing: AppSpacing.xs,
                  children: const [
                    TagChip(label: '已采用', icon: Icons.check_circle, tint: AppColors.statusAdopted),
                    TagChip(label: '卧龙', tint: AppColors.accentWolong),
                    TagChip(label: '采集', tint: AppColors.accentCollect),
                  ],
                ),
              ],
            ),
          ),
          const SizedBox(height: AppSpacing.block),
          Row(
            children: [
              Expanded(
                child: DecisionButton(label: '批准', tint: AppColors.statusAdopted, onPressed: () {}),
              ),
              const SizedBox(width: AppSpacing.m),
              Expanded(
                child: DecisionButton(label: '打回', filled: false, tint: AppColors.statusRed, onPressed: () {}),
              ),
            ],
          ),
          const SizedBox(height: AppSpacing.block),
          Stack(
            children: [
              Container(
                height: 120,
                decoration: const BoxDecoration(
                  gradient: LinearGradient(colors: [AppColors.orange, AppColors.accentArt]),
                  borderRadius: BorderRadius.all(AppRadii.cardR),
                ),
              ),
              Positioned.fill(
                child: Padding(
                  padding: const EdgeInsets.all(AppSpacing.m),
                  child: LiquidGlass(
                    enableGlass: _glass,
                    child: Center(
                      child: Text(
                        _glass ? 'Liquid Glass(自绘玻璃)' : '降级态(无玻璃·静态半透明)',
                        style: AppTypography.label,
                      ),
                    ),
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: AppSpacing.s),
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text('玻璃效果', style: AppTypography.subhead),
              Switch(value: _glass, onChanged: (bool v) => setState(() => _glass = v)),
            ],
          ),
          const SizedBox(height: AppSpacing.block),
          AppCard(
            dark: true,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('剪贴板短链探测', style: AppTypography.titleM.copyWith(color: AppColors.ivory)),
                const SizedBox(height: AppSpacing.s),
                Text(
                  sample == null ? '未识别到分享链接' : '识别到「${sample.platform}」:${sample.url}',
                  style: AppTypography.monoS.copyWith(color: AppColors.ivory),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

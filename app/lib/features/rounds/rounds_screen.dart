// 卧龙战报页:GET /rounds 列表 + GET /rounds/{id} 详情 + 终止本轮。
// 视觉对齐收件箱(task_list_screen.dart):sand 底 + 象牙卡 + 中文状态词 + 语义色芯片。
// 列表点卡片 push 详情;详情若 active 提供「止损终止」破坏性按钮(二次确认)。

import 'package:flutter/material.dart';

import '../../core/net/endpoint_resolver.dart';
import '../../core/net/factory_client.dart';
import '../../core/net/models.dart';
import '../../design/components/app_card.dart';
import '../../design/components/decision_button.dart';
import '../../design/tokens.dart';
import '../../design/typography.dart';

const String _devBase = 'http://liuzhihao-mbp.local:8810';

FactoryClient _defaultClient() => FactoryClient(resolver: DirectEndpointResolver(_devBase));

// —— 展示口径(列表与详情共用) ——

// round_id 尾部短码(round_t_xxx / round_20260611_191500_ab12 取末 8 位辨识)。
String _roundShortCode(String id) => '#${id.length <= 8 ? id : id.substring(id.length - 8)}';

// round 文件 line.status 中文化(后端 wolong_rounds.py);未知新值原样显示,不挂 UI。
String _lineStatusLabel(String? status) {
  switch (status) {
    case 'dispatching':
      return '派发中';
    case 'drafting':
      return '写稿中';
    case 'review':
      return '待验收';
    case 'prescreen_pending':
      return '预筛中';
    case 'approved':
      return '已采用';
    case 'killed':
      return '已止损';
    case null:
      return '未知';
    default:
      return status;
  }
}

// 产线状态染色:approved 绿 / killed 红 / review·prescreen_pending 琥珀 / 其他灰。
Color _lineStatusColor(String? status) {
  switch (status) {
    case 'approved':
      return AppColors.statusAdopted;
    case 'killed':
      return AppColors.statusRed;
    case 'review':
    case 'prescreen_pending':
      return AppColors.amber;
    default:
      return AppColors.inkFaint;
  }
}

// round.status 染色 + 中文标签 + 是否呼吸(active 进行中)。
({Color color, String label, bool breathing}) _roundStatusStyle(String? status) {
  switch (status) {
    case 'active':
      return (color: AppColors.amber, label: '进行中', breathing: true);
    case 'done':
      return (color: AppColors.statusAdopted, label: '已收盘', breathing: false);
    case 'terminated':
      return (color: AppColors.inkMuted, label: '已终止', breathing: false);
    default:
      return (color: AppColors.inkMuted, label: status ?? '未知', breathing: false);
  }
}

// ISO 时间串截到「MM-DD HH:mm」;解析不了就原样返回(不炸 UI)。
String _shortTime(String? iso) {
  if (iso == null || iso.isEmpty) return '—';
  final dt = DateTime.tryParse(iso);
  if (dt == null) return iso;
  final l = dt.toLocal();
  String two(int n) => n.toString().padLeft(2, '0');
  return '${two(l.month)}-${two(l.day)} ${two(l.hour)}:${two(l.minute)}';
}

String _pct(double v) => '${(v * 100).round()}%';

// 圆点状态指示(替代 iOS BreathingDot):active 用动效呼吸点,其余静态实心点。
class _StatusDot extends StatefulWidget {
  const _StatusDot({required this.color, this.breathing = false});
  final Color color;
  final bool breathing;

  @override
  State<_StatusDot> createState() => _StatusDotState();
}

class _StatusDotState extends State<_StatusDot> with SingleTickerProviderStateMixin {
  late final AnimationController _controller = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 1400),
  );

  @override
  void initState() {
    super.initState();
    if (widget.breathing) _controller.repeat(reverse: true);
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final dot = Container(
      width: 7,
      height: 7,
      decoration: BoxDecoration(color: widget.color, shape: BoxShape.circle),
    );
    if (!widget.breathing) return dot;
    return FadeTransition(
      opacity: Tween<double>(begin: 0.45, end: 1).animate(
        CurvedAnimation(parent: _controller, curve: Curves.easeInOut),
      ),
      child: dot,
    );
  }
}

// round 状态芯片:呼吸点 / 图标 + 中文标签,Capsule 底 @14%。
class _RoundStatusChip extends StatelessWidget {
  const _RoundStatusChip({required this.status});
  final String? status;

  @override
  Widget build(BuildContext context) {
    final s = _roundStatusStyle(status);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: AppSpacing.chipH + 3, vertical: 4),
      decoration: ShapeDecoration(color: AppColors.chipBg(s.color), shape: const StadiumBorder()),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          if (s.breathing) ...[
            _StatusDot(color: s.color, breathing: true),
            const SizedBox(width: 5),
          ] else if (status == 'done') ...[
            Icon(Icons.verified_outlined, size: 12, color: s.color),
            const SizedBox(width: 4),
          ] else if (status == 'terminated') ...[
            Icon(Icons.do_not_disturb_on_outlined, size: 12, color: s.color),
            const SizedBox(width: 4),
          ],
          Text(s.label, style: AppTypography.label.copyWith(color: s.color)),
        ],
      ),
    );
  }
}

// 单元素可滚动容器,保证加载/错误/空态下 RefreshIndicator 仍可下拉(同收件箱)。
Widget _centered(Widget child) => ListView(
      physics: const AlwaysScrollableScrollPhysics(),
      children: [
        Padding(padding: const EdgeInsets.only(top: 96), child: Center(child: child)),
      ],
    );

class _ErrorState extends StatelessWidget {
  const _ErrorState({required this.title, required this.message, required this.onRetry});
  final String title;
  final String message;
  final Future<void> Function() onRetry;

  @override
  Widget build(BuildContext context) {
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(Icons.cloud_off_outlined, color: AppColors.inkFaint, size: 40),
        const SizedBox(height: AppSpacing.m),
        Text(title, style: AppTypography.titleM),
        const SizedBox(height: AppSpacing.xs),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: AppSpacing.pageH),
          child: Text(
            message,
            style: AppTypography.caption.copyWith(color: AppColors.inkMuted),
            textAlign: TextAlign.center,
          ),
        ),
        const SizedBox(height: AppSpacing.m),
        TextButton(onPressed: onRetry, child: const Text('重试')),
      ],
    );
  }
}

// —— round 列表 ——

/// 战报列表:GET /rounds。下拉刷新 + 错误/空态;点卡片 push [RoundDetailScreen]。
class RoundsScreen extends StatefulWidget {
  const RoundsScreen({super.key, this.client});

  /// 测试可注入;为 null 时走默认 LAN 直连 [FactoryClient]。
  final FactoryClient? client;

  @override
  State<RoundsScreen> createState() => _RoundsScreenState();
}

class _RoundsScreenState extends State<RoundsScreen> {
  late final FactoryClient _client = widget.client ?? _defaultClient();
  late Future<List<RoundSummary>> _future = _client.rounds();

  Future<void> _refresh() async {
    setState(() => _future = _client.rounds());
    try {
      await _future;
    } catch (_) {/* 错误由 FutureBuilder 呈现 */}
  }

  void _openDetail(RoundSummary r) {
    Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => RoundDetailScreen(roundId: r.roundId, client: _client),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.sand,
      appBar: AppBar(
        backgroundColor: AppColors.sand,
        foregroundColor: AppColors.ink,
        title: Text('round 战报', style: AppTypography.navTitle),
      ),
      body: RefreshIndicator(
        onRefresh: _refresh,
        child: FutureBuilder<List<RoundSummary>>(
          future: _future,
          builder: (context, snap) {
            if (snap.connectionState == ConnectionState.waiting) {
              return _centered(const CircularProgressIndicator());
            }
            if (snap.hasError) {
              return _centered(_ErrorState(
                title: '拉不到战报',
                message: '${snap.error}',
                onRetry: _refresh,
              ));
            }
            final rounds = snap.data ?? const <RoundSummary>[];
            if (rounds.isEmpty) {
              return _centered(
                Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Icon(Icons.flag_outlined, color: AppColors.inkFaint, size: 40),
                    const SizedBox(height: AppSpacing.m),
                    Text('还没有 round', style: AppTypography.titleM),
                    const SizedBox(height: AppSpacing.xs),
                    Text(
                      '卧龙开盘后,每轮生产的战报在这里看',
                      style: AppTypography.caption.copyWith(color: AppColors.inkMuted),
                    ),
                  ],
                ),
              );
            }
            return ListView.separated(
              physics: const AlwaysScrollableScrollPhysics(),
              padding: const EdgeInsets.fromLTRB(
                AppSpacing.pageH,
                AppSpacing.m,
                AppSpacing.pageH,
                AppSpacing.pageBottom,
              ),
              itemCount: rounds.length,
              separatorBuilder: (_, _) => const SizedBox(height: AppSpacing.m),
              itemBuilder: (context, i) => _RoundCard(
                round: rounds[i],
                onTap: () => _openDetail(rounds[i]),
              ),
            );
          },
        ),
      ),
    );
  }
}

// 一张 round 卡:短码 + 状态芯片 + 时间/目标 + 产线迷你状态条 + 收盘摘要。
class _RoundCard extends StatelessWidget {
  const _RoundCard({required this.round, required this.onTap});
  final RoundSummary round;
  final VoidCallback onTap;

  // 仅收盘(done/terminated)且有战报时给摘要;active 不显示半截数据。
  String? get _closedSummary {
    if (round.status == 'active' || round.report == null) return null;
    return '✓ ${round.report!.approved ?? 0} · ✗ ${round.report!.killed ?? 0}';
  }

  @override
  Widget build(BuildContext context) {
    final lines = round.lines ?? const <RoundLineSummary>[];
    final summary = _closedSummary;
    return GestureDetector(
      onTap: onTap,
      behavior: HitTestBehavior.opaque,
      child: AppCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(
                    _roundShortCode(round.roundId),
                    style: AppTypography.mono.copyWith(fontWeight: FontWeight.w700),
                  ),
                ),
                const SizedBox(width: AppSpacing.s),
                _RoundStatusChip(status: round.status),
              ],
            ),
            const SizedBox(height: AppSpacing.s),
            Row(
              children: [
                Icon(Icons.schedule, size: 13, color: AppColors.inkFaint),
                const SizedBox(width: AppSpacing.xs),
                Text(_shortTime(round.createdAt),
                    style: AppTypography.caption.copyWith(color: AppColors.inkMuted)),
                if (round.goalCount != null) ...[
                  const SizedBox(width: AppSpacing.m),
                  Icon(Icons.adjust, size: 13, color: AppColors.inkFaint),
                  const SizedBox(width: AppSpacing.xs),
                  Text('目标 ${round.goalCount} 条',
                      style: AppTypography.caption.copyWith(color: AppColors.inkMuted)),
                ],
              ],
            ),
            if (lines.isNotEmpty || summary != null) ...[
              const SizedBox(height: AppSpacing.m),
              Row(
                children: [
                  // 产线多时换行,避免固定宽度状态条撑爆卡片
                  Expanded(
                    child: Wrap(
                      spacing: 4,
                      runSpacing: 4,
                      children: [
                        for (final ln in lines)
                          Container(
                            width: 18,
                            height: 8,
                            decoration: BoxDecoration(
                              color: _lineStatusColor(ln.status),
                              borderRadius: BorderRadius.circular(3),
                            ),
                          ),
                      ],
                    ),
                  ),
                  if (summary != null) ...[
                    const SizedBox(width: AppSpacing.s),
                    Text(
                      summary,
                      style: AppTypography.label.copyWith(color: AppColors.inkMuted),
                    ),
                  ],
                ],
              ),
            ],
          ],
        ),
      ),
    );
  }
}

// —— round 详情 ——

/// 战报详情:GET /rounds/{id}。展示开盘目标/产线/战报;active 时提供止损终止按钮。
class RoundDetailScreen extends StatefulWidget {
  const RoundDetailScreen({super.key, required this.roundId, this.client});

  final String roundId;

  /// 测试可注入;为 null 时走默认 LAN 直连 [FactoryClient]。
  final FactoryClient? client;

  @override
  State<RoundDetailScreen> createState() => _RoundDetailScreenState();
}

class _RoundDetailScreenState extends State<RoundDetailScreen> {
  late final FactoryClient _client = widget.client ?? _defaultClient();
  late Future<RoundDetail> _future = _client.round(widget.roundId);

  bool _terminating = false;
  String? _terminateError;

  Future<void> _reload() async {
    setState(() => _future = _client.round(widget.roundId));
    try {
      await _future;
    } catch (_) {/* 错误由 FutureBuilder 呈现 */}
  }

  Future<void> _confirmTerminate() async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: AppColors.ivory,
        title: Text('终止本轮?', style: AppTypography.titleM),
        content: Text(
          '止损不可撤销:本轮产线全部落定,在途任务一并取消。',
          style: AppTypography.body.copyWith(color: AppColors.inkMuted),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(false),
            child: Text('再想想', style: AppTypography.body.copyWith(color: AppColors.inkMuted)),
          ),
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(true),
            child: Text('终止本轮', style: AppTypography.body.copyWith(color: AppColors.statusRed)),
          ),
        ],
      ),
    );
    if (ok == true) await _terminate();
  }

  Future<void> _terminate() async {
    setState(() {
      _terminating = true;
      _terminateError = null;
    });
    try {
      await _client.terminateRound(widget.roundId);
      await _reload();
    } catch (e) {
      if (mounted) setState(() => _terminateError = '$e');
    } finally {
      if (mounted) setState(() => _terminating = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.sand,
      appBar: AppBar(
        backgroundColor: AppColors.sand,
        foregroundColor: AppColors.ink,
        title: Text(_roundShortCode(widget.roundId), style: AppTypography.navTitle),
      ),
      body: RefreshIndicator(
        onRefresh: _reload,
        child: FutureBuilder<RoundDetail>(
          future: _future,
          builder: (context, snap) {
            if (snap.connectionState == ConnectionState.waiting) {
              return _centered(const CircularProgressIndicator());
            }
            if (snap.hasError) {
              return _centered(_ErrorState(
                title: '拉不到 round',
                message: '${snap.error}',
                onRetry: _reload,
              ));
            }
            final d = snap.data!;
            return ListView(
              physics: const AlwaysScrollableScrollPhysics(),
              padding: const EdgeInsets.fromLTRB(
                AppSpacing.pageH,
                AppSpacing.m,
                AppSpacing.pageH,
                AppSpacing.pageBottom,
              ),
              children: [
                _header(d),
                if (d.goal != null) ...[
                  const SizedBox(height: AppSpacing.m),
                  _GoalCard(goal: d.goal!),
                ],
                if (d.lines != null && d.lines!.isNotEmpty) ...[
                  const SizedBox(height: AppSpacing.m),
                  _LinesCard(lines: d.lines!),
                ],
                if (d.report != null) ...[
                  const SizedBox(height: AppSpacing.m),
                  _ReportCard(report: d.report!),
                ],
                if (d.status == 'active') ...[
                  const SizedBox(height: AppSpacing.block),
                  _terminateSection(),
                ],
              ],
            );
          },
        ),
      ),
    );
  }

  Widget _header(RoundDetail d) {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                _roundShortCode(d.roundId),
                style: AppTypography.mono.copyWith(fontSize: 16, fontWeight: FontWeight.w700),
              ),
              const SizedBox(height: AppSpacing.xs),
              Text(
                '开盘 ${_shortTime(d.createdAt)}',
                style: AppTypography.caption.copyWith(color: AppColors.inkMuted),
              ),
            ],
          ),
        ),
        const SizedBox(width: AppSpacing.s),
        _RoundStatusChip(status: d.status),
      ],
    );
  }

  Widget _terminateSection() {
    return Column(
      children: [
        DecisionButton(
          label: _terminating ? '正在终止…' : '止损终止本轮',
          filled: false,
          tint: AppColors.statusRed,
          onPressed: _terminating ? () {} : _confirmTerminate,
        ),
        if (_terminateError != null) ...[
          const SizedBox(height: AppSpacing.s),
          Text(
            _terminateError!,
            style: AppTypography.caption.copyWith(color: AppColors.statusRed),
            textAlign: TextAlign.center,
          ),
        ],
        const SizedBox(height: AppSpacing.s),
        Text(
          '止损:未落定产线计止损,在途任务取消',
          style: AppTypography.caption2.copyWith(color: AppColors.inkFaint),
          textAlign: TextAlign.center,
        ),
      ],
    );
  }
}

// 一个 token 灰底小药丸(图标 + 文本),复刻 iOS statPill。
class _StatPill extends StatelessWidget {
  const _StatPill({required this.icon, required this.text});
  final IconData icon;
  final String text;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 4),
      decoration: ShapeDecoration(
        color: AppColors.ink.withValues(alpha: 0.06),
        shape: const StadiumBorder(),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, size: 12, color: AppColors.inkMuted),
          const SizedBox(width: AppSpacing.xs),
          Flexible(
            child: Text(
              text,
              style: AppTypography.label.copyWith(color: AppColors.inkMuted),
              overflow: TextOverflow.ellipsis,
            ),
          ),
        ],
      ),
    );
  }
}

// 开盘目标卡:目标条数 / rubric 版本 / 对标路径父目录(空=选题库存直开)。
class _GoalCard extends StatelessWidget {
  const _GoalCard({required this.goal});
  final RoundGoal goal;

  // 对标文件统一叫 all_posts.json,有辨识度的是父目录(author_xxx)。
  String _benchmarkLabel(String path) {
    final parts = path.split('/').where((p) => p.isNotEmpty).toList();
    if (parts.length >= 2) return parts[parts.length - 2];
    return parts.isNotEmpty ? parts.last : path;
  }

  @override
  Widget build(BuildContext context) {
    final hasBenchmark = goal.benchmarkPath != null && goal.benchmarkPath!.isNotEmpty;
    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('开盘目标', style: AppTypography.titleM),
          const SizedBox(height: AppSpacing.s),
          Wrap(
            spacing: AppSpacing.s,
            runSpacing: AppSpacing.s,
            children: [
              if (goal.count != null) _StatPill(icon: Icons.adjust, text: '目标 ${goal.count} 条'),
              if (goal.rubricVersion != null)
                _StatPill(icon: Icons.menu_book_outlined, text: 'rubric v${goal.rubricVersion}'),
              if (hasBenchmark)
                _StatPill(icon: Icons.show_chart, text: '对标 ${_benchmarkLabel(goal.benchmarkPath!)}'),
            ],
          ),
          if (!hasBenchmark) ...[
            const SizedBox(height: AppSpacing.s),
            Text(
              '选题库存直开(本轮未用对标数据)',
              style: AppTypography.caption.copyWith(color: AppColors.inkMuted),
            ),
          ],
          if (goal.avoid != null && goal.avoid!.isNotEmpty) ...[
            const SizedBox(height: AppSpacing.s),
            Text(
              '规避:${goal.avoid}',
              style: AppTypography.caption.copyWith(color: AppColors.inkMuted),
            ),
          ],
        ],
      ),
    );
  }
}

// 产线卡:每条 slot 圆点 + 选题标题 + 状态 + 返工数 + 历次打回意见(最多 3 条)。
class _LinesCard extends StatelessWidget {
  const _LinesCard({required this.lines});
  final List<RoundDetailLine> lines;

  String _topicTitle(RoundDetailLine ln) {
    final t = ln.topic?['title'];
    if (t is String && t.isNotEmpty) return t;
    return '(无选题)';
  }

  @override
  Widget build(BuildContext context) {
    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Text('产线', style: AppTypography.titleM),
              const Spacer(),
              Text('${lines.length} 条', style: AppTypography.caption.copyWith(color: AppColors.inkMuted)),
            ],
          ),
          for (var i = 0; i < lines.length; i++) ...[
            if (i == 0) const SizedBox(height: AppSpacing.s) else _divider(),
            _lineRow(lines[i]),
          ],
        ],
      ),
    );
  }

  Widget _divider() => Padding(
        padding: const EdgeInsets.symmetric(vertical: AppSpacing.s),
        child: Divider(height: 1, thickness: 1, color: AppColors.ink.withValues(alpha: 0.06)),
      );

  Widget _lineRow(RoundDetailLine ln) {
    final color = _lineStatusColor(ln.status);
    final notes = ln.notes ?? const <String>[];
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Container(
          width: 20,
          height: 20,
          alignment: Alignment.center,
          decoration: BoxDecoration(color: color, shape: BoxShape.circle),
          child: Text(
            '${ln.slot ?? 0}',
            style: AppTypography.caption2.copyWith(color: AppColors.ivory, fontWeight: FontWeight.w700),
          ),
        ),
        const SizedBox(width: AppSpacing.m),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                _topicTitle(ln),
                style: AppTypography.subhead,
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
              ),
              const SizedBox(height: AppSpacing.xs),
              Row(
                children: [
                  Text(
                    _lineStatusLabel(ln.status),
                    style: AppTypography.caption2.copyWith(color: color, fontWeight: FontWeight.w600),
                  ),
                  if (ln.rework != null && ln.rework! > 0) ...[
                    const SizedBox(width: AppSpacing.s),
                    Text('返工 ${ln.rework} 次',
                        style: AppTypography.caption2.copyWith(color: AppColors.amber)),
                  ],
                ],
              ),
              if (notes.isNotEmpty) ...[
                const SizedBox(height: AppSpacing.xs),
                for (final n in notes.take(3))
                  Padding(
                    padding: const EdgeInsets.only(bottom: 2),
                    child: Text(
                      '↩ $n',
                      style: AppTypography.caption2.copyWith(color: AppColors.inkMuted),
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
                if (notes.length > 3)
                  Text(
                    '…共 ${notes.length} 条打回意见',
                    style: AppTypography.caption2.copyWith(color: AppColors.inkFaint),
                  ),
              ],
            ],
          ),
        ),
      ],
    );
  }
}

// 三连数字格(采用/止损/返工);值缺省显示「—」不显示 0 冒充。
class _StatBox extends StatelessWidget {
  const _StatBox({required this.label, required this.value, required this.color});
  final String label;
  final int? value;
  final Color color;

  @override
  Widget build(BuildContext context) {
    final has = value != null;
    return Expanded(
      child: Container(
        padding: const EdgeInsets.symmetric(vertical: AppSpacing.s + 2),
        decoration: BoxDecoration(
          color: color.withValues(alpha: 0.08),
          borderRadius: BorderRadius.circular(AppRadii.capture),
        ),
        child: Column(
          children: [
            Text(
              has ? '$value' : '—',
              style: AppTypography.titleL.copyWith(color: has ? color : AppColors.inkFaint),
            ),
            const SizedBox(height: 2),
            Text(label, style: AppTypography.caption2.copyWith(color: AppColors.inkMuted)),
          ],
        ),
      ),
    );
  }
}

// 战报卡:终止形态有 reason(红);正常收盘有比率/预筛三数。全字段缺省容错。
class _ReportCard extends StatelessWidget {
  const _ReportCard({required this.report});
  final RoundReport report;

  @override
  Widget build(BuildContext context) {
    final rep = report;
    final pills = <Widget>[
      if (rep.rejectRate != null)
        _StatPill(icon: Icons.undo, text: '打回率 ${_pct(rep.rejectRate!)}'),
      if (rep.reworkRate != null)
        _StatPill(icon: Icons.autorenew, text: '返工率 ${_pct(rep.reworkRate!)}'),
    ];
    final ps = rep.prescreen;
    final summaryLines = rep.summaryLines ?? const <String>[];
    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Text('战报', style: AppTypography.titleM),
              const Spacer(),
              if (rep.finishedAt != null)
                Text('收盘 ${_shortTime(rep.finishedAt)}',
                    style: AppTypography.caption.copyWith(color: AppColors.inkMuted)),
            ],
          ),
          if (rep.reason != null && rep.reason!.isNotEmpty) ...[
            const SizedBox(height: AppSpacing.s),
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Icon(Icons.do_not_disturb_on, size: 14, color: AppColors.statusRed),
                const SizedBox(width: AppSpacing.xs),
                Expanded(
                  child: Text(
                    rep.reason!,
                    style: AppTypography.caption.copyWith(
                        color: AppColors.statusRed, fontWeight: FontWeight.w600),
                  ),
                ),
              ],
            ),
          ],
          const SizedBox(height: AppSpacing.m),
          Row(
            children: [
              _StatBox(label: '采用', value: rep.approved, color: AppColors.statusAdopted),
              const SizedBox(width: AppSpacing.s),
              _StatBox(label: '止损', value: rep.killed, color: AppColors.statusRed),
              const SizedBox(width: AppSpacing.s),
              _StatBox(label: '返工', value: rep.reworkTotal, color: AppColors.amber),
            ],
          ),
          if (pills.isNotEmpty) ...[
            const SizedBox(height: AppSpacing.m),
            Wrap(spacing: AppSpacing.s, runSpacing: AppSpacing.s, children: pills),
          ],
          if (ps != null) ...[
            const SizedBox(height: AppSpacing.s),
            _StatPill(
              icon: Icons.shield_outlined,
              text: '预筛 拦截 ${ps.intercepted ?? 0} · 探索 ${ps.explore ?? 0} · 假阴性 ${ps.falseNegatives ?? 0}',
            ),
          ],
          if (rep.rubricVersion != null) ...[
            const SizedBox(height: AppSpacing.s),
            _StatPill(icon: Icons.menu_book_outlined, text: 'rubric v${rep.rubricVersion}'),
          ],
          if (summaryLines.isNotEmpty) ...[
            const SizedBox(height: AppSpacing.s),
            Divider(height: 1, thickness: 1, color: AppColors.ink.withValues(alpha: 0.06)),
            const SizedBox(height: AppSpacing.s),
            for (final line in summaryLines)
              Padding(
                padding: const EdgeInsets.only(bottom: 3),
                child: Text(line, style: AppTypography.monoXS.copyWith(color: AppColors.inkMuted)),
              ),
          ],
        ],
      ),
    );
  }
}

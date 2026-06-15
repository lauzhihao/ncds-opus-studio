// agent 详情入口 = 该 agent 的任务收件箱。1:1 复刻原生 AgentTaskListView.swift:
// GET /tasks 筛 cmd,按状态分桶(运行中/待验收/已失败/未开始/已归档),暖色药丸切桶;
// 卡片右下角状态芯片带图标(对齐 iOS statusChip);卧龙同 round 的续跑段折叠进主卡。
// 点卡片进通用任务详情(详情页已按 cmd 自渲染专属面板)。

import 'dart:async';
import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../../core/net/endpoint_resolver.dart';
import '../../core/net/factory_client.dart';
import '../../core/net/models.dart';
import '../../design/tokens.dart';
import '../../design/typography.dart';
import '../compose/compose_screen.dart';
import '../detail/liuyong_task_detail_screen.dart';
import '../detail/task_detail_screen.dart';
import '../home/agent_catalog.dart';
import '../rounds/rounds_screen.dart';

/// 任务分桶(对齐 iOS TaskBucket,声明顺序即 selector 顺序)。
/// 优先级:已决策→归档;否则按 status。失败独立成桶;completed 未决进待验收;
/// cancelled(用户取消)进已归档,详情页可恢复。
enum TaskBucket {
  running('运行中'),
  review('待验收'),
  failed('已失败'),
  queued('未开始'),
  archived('已归档');

  const TaskBucket(this.label);
  final String label;

  static TaskBucket of(TaskMeta t) {
    if (t.decision != null) return TaskBucket.archived;
    switch (t.status) {
      case 'running':
        return TaskBucket.running;
      case 'pending':
        return TaskBucket.queued;
      case 'failed':
        return TaskBucket.failed;
      case 'cancelled':
        return TaskBucket.archived;
      default:
        return TaskBucket.review; // completed(未决)
    }
  }

  String get emptyTitle {
    switch (this) {
      case TaskBucket.running:
        return '暂无运行中任务';
      case TaskBucket.review:
        return '没有待验收的任务';
      case TaskBucket.failed:
        return '没有失败的任务';
      case TaskBucket.queued:
        return '没有排队的任务';
      case TaskBucket.archived:
        return '归档为空';
    }
  }

  IconData get emptyIcon {
    switch (this) {
      case TaskBucket.running:
        return Icons.bolt_outlined; // bolt.horizontal.circle
      case TaskBucket.review:
        return Icons.inbox_outlined; // tray
      case TaskBucket.failed:
        return Icons.warning_amber_rounded; // exclamationmark.triangle
      case TaskBucket.queued:
        return Icons.schedule; // clock
      case TaskBucket.archived:
        return Icons.archive_outlined; // archivebox
    }
  }
}

/// ISO 时间串 → "MM-dd HH:mm"(对齐 iOS shortTime,不依赖严格解析)。
String shortTime(String? iso) {
  if (iso == null || iso.isEmpty) return '';
  final noFrac = iso.split('.').first;
  final parts = noFrac.replaceAll('T', ' ').split(' ');
  if (parts.length != 2) return noFrac;
  final date = parts[0].length >= 10 ? parts[0].substring(5) : parts[0]; // 去掉 "yyyy-"
  final time = parts[1].length >= 5 ? parts[1].substring(0, 5) : parts[1];
  return '$date $time';
}

/// 某个 agent 的任务收件箱。进入即拉全量 /tasks 按 cmd 过滤;前台 30s 轮询 + 下拉刷新。
class AgentTaskListScreen extends StatefulWidget {
  const AgentTaskListScreen({super.key, required this.agent, this.client});

  final AgentInfo agent;

  /// 测试可注入假客户端;为 null 时走 LAN 直连工厂后端。
  final FactoryClient? client;

  @override
  State<AgentTaskListScreen> createState() => _AgentTaskListScreenState();
}

class _AgentTaskListScreenState extends State<AgentTaskListScreen> with WidgetsBindingObserver {
  static const String _devBase = 'http://liuzhihao-mbp.local:8810';

  late final FactoryClient _client =
      widget.client ?? FactoryClient(resolver: DirectEndpointResolver(_devBase));

  List<TaskMeta> _tasks = const <TaskMeta>[];
  TaskBucket _bucket = TaskBucket.review;
  bool _loading = true;
  String? _loadError;
  Timer? _timer;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    // 首拉延到 push 转场结束:数据若在右滑动画中途到达,构建任务卡会丢帧、肉眼抖动。
    WidgetsBinding.instance.addPostFrameCallback((_) => _kickoff());
  }

  /// 等本页进场转场动画跑完再发首个请求(局域网很快,否则会在滑动中卡顿)。
  void _kickoff() {
    if (!mounted) return;
    final Animation<double>? anim = ModalRoute.of(context)?.animation;
    if (anim == null || anim.isCompleted) {
      _start();
    } else {
      void listener(AnimationStatus status) {
        if (status == AnimationStatus.completed) {
          anim.removeStatusListener(listener);
          if (mounted) _start();
        }
      }
      anim.addStatusListener(listener);
    }
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _timer?.cancel();
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    // 回前台立即刷新并重启轮询;退后台只歇不刷(对齐原生 scenePhase 行为)。
    if (state == AppLifecycleState.resumed) {
      _start();
    } else {
      _timer?.cancel();
    }
  }

  void _start() {
    _timer?.cancel();
    _refresh();
    _timer = Timer.periodic(const Duration(seconds: 30), (_) => _refresh());
  }

  Future<void> _refresh() async {
    try {
      final list = await _client.listTasks();
      if (!mounted) return;
      setState(() {
        _tasks = list;
        _loadError = null;
        _loading = false;
      });
    } catch (e) {
      // 退出/轮询重启取消在途请求不算故障;已有数据时静默保留旧列表(抖动不整页顶替),
      // 空数据才上错误占位。
      if (isCancellation(e)) return;
      if (!mounted) return;
      setState(() {
        if (_tasks.isEmpty) _loadError = '$e';
        _loading = false;
      });
    }
  }

  List<TaskMeta> get _mine => _tasks.where((t) => t.cmd == widget.agent.cmd).toList();
  List<TaskMeta> get _shown => _mine.where((t) => TaskBucket.of(t) == _bucket).toList();

  /// 续跑卡折叠:仅 cmd=wolong 且带 round_id 的任务按 round_id 分组——入口任务(非 resume)为主卡,
  /// resume 段折叠其下;无 round_id/非 wolong 照旧平铺。分组不跨桶(_shown 已按当前桶过滤)。
  List<_TaskRow> get _rows {
    final shown = _shown;
    final groups = <String, List<TaskMeta>>{};
    for (final t in shown) {
      if (t.cmd == 'wolong' && t.roundId != null) {
        groups.putIfAbsent(t.roundId!, () => <TaskMeta>[]).add(t);
      }
    }
    final consumed = <String>{};
    final out = <_TaskRow>[];
    for (final t in shown) {
      if (consumed.contains(t.taskId)) continue;
      final group = (t.cmd == 'wolong' && t.roundId != null) ? groups[t.roundId!] : null;
      if (group == null || group.length <= 1) {
        out.add(_TaskRow(main: t, segments: const <TaskMeta>[]));
        continue;
      }
      // created_at 是本机 ISO 串,字典序=时间序。主卡取最新的非续跑段,无则取最新段。
      final entries = group.where((g) => !g.isResumeSegment).toList();
      entries.sort((a, b) => (a.createdAt ?? '').compareTo(b.createdAt ?? ''));
      final main = entries.isNotEmpty
          ? entries.last
          : (group.toList()..sort((a, b) => (a.createdAt ?? '').compareTo(b.createdAt ?? ''))).last;
      final segments = group.where((g) => g.taskId != main.taskId).toList()
        ..sort((a, b) => (b.createdAt ?? '').compareTo(a.createdAt ?? ''));
      consumed.addAll(group.map((g) => g.taskId));
      out.add(_TaskRow(main: main, segments: segments));
    }
    return out;
  }

  @override
  Widget build(BuildContext context) {
    final agent = widget.agent;
    return Scaffold(
      backgroundColor: AppColors.sand,
      appBar: AppBar(
        backgroundColor: AppColors.sand,
        foregroundColor: AppColors.ink,
        elevation: 0,
        scrolledUnderElevation: 0,
        surfaceTintColor: Colors.transparent,
        actions: [
          // 卧龙专属:round 战报入口。
          if (agent.cmd == 'wolong')
            TextButton.icon(
              onPressed: () => Navigator.of(context).push(
                MaterialPageRoute<void>(builder: (_) => const RoundsScreen()),
              ),
              icon: const Icon(Icons.flag_outlined, size: 18),
              label: Text('战报', style: AppTypography.label.copyWith(color: AppColors.orange)),
              style: TextButton.styleFrom(foregroundColor: AppColors.orange),
            ),
          IconButton(
            tooltip: '发起任务',
            icon: const Icon(Icons.add),
            color: AppColors.orange,
            onPressed: () async {
              await Navigator.of(context).push<String>(
                MaterialPageRoute<String>(builder: (_) => const ComposeScreen()),
              );
              _refresh(); // 新建回来刷新
            },
          ),
        ],
      ),
      body: SafeArea(
        top: false,
        child: Column(
          children: [
            const SizedBox(height: AppSpacing.s),
            _Header(agent: agent),
            const SizedBox(height: 14),
            _BucketSelector(
              agent: agent,
              current: _bucket,
              onSelect: (b) => setState(() => _bucket = b),
            ),
            const SizedBox(height: 14),
            Expanded(
              child: RefreshIndicator(
                onRefresh: _refresh,
                color: agent.accent,
                backgroundColor: AppColors.ivory,
                child: _content(),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _content() {
    if (_loading && _tasks.isEmpty) {
      return _centered(const CircularProgressIndicator());
    }
    if (_loadError != null && _tasks.isEmpty) {
      return _centered(_ErrorState(message: _loadError!, onRetry: _refresh));
    }
    final rows = _rows;
    if (rows.isEmpty) {
      return _centered(_EmptyState(bucket: _bucket));
    }
    return ListView.builder(
      physics: const AlwaysScrollableScrollPhysics(),
      padding: const EdgeInsets.fromLTRB(
        AppSpacing.pageH, AppSpacing.xs, AppSpacing.pageH, AppSpacing.pageBottom,
      ),
      itemCount: rows.length,
      itemBuilder: (context, i) {
        final row = rows[i];
        return Padding(
          padding: EdgeInsets.only(bottom: i == rows.length - 1 ? 0 : AppSpacing.m),
          child: Column(
            children: [
              _TaskCard(task: row.main, onTap: () => _open(row.main)),
              if (row.segments.isNotEmpty)
                _SegmentsFold(segments: row.segments, onOpen: _open),
            ],
          ),
        );
      },
    );
  }

  Future<void> _open(TaskMeta t) async {
    // 柳永是整页手工定制(双稿对比/质检/语音打回),走专属详情;其余走通用详情。
    final WidgetBuilder builder = widget.agent.cmd == 'liuyong'
        ? (_) => LiuyongTaskDetailScreen(
              taskId: t.taskId, agent: widget.agent, topic: t.titleGuess)
        : (_) => TaskDetailScreen(taskId: t.taskId);
    await Navigator.of(context).push<void>(MaterialPageRoute<void>(builder: builder));
    _refresh(); // 详情可能改了决策,回来刷新
  }

  /// 单元素可滚动容器,保证加载/错误/空态下 RefreshIndicator 仍可下拉。
  Widget _centered(Widget child) => ListView(
        physics: const AlwaysScrollableScrollPhysics(),
        children: [
          Padding(padding: const EdgeInsets.only(top: 80), child: Center(child: child)),
        ],
      );
}

/// 一行列表项:普通任务平铺;wolong 同 round 的续跑段折叠进主卡。
class _TaskRow {
  const _TaskRow({required this.main, required this.segments});
  final TaskMeta main;
  final List<TaskMeta> segments;
}

// —— 头像 + 名字 + 雅号(顶起,当页面标题用) ——

class _Header extends StatelessWidget {
  const _Header({required this.agent});
  final AgentInfo agent;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: AppSpacing.pageH),
      child: Row(
        children: [
          _Avatar(agent: agent, size: 48),
          const SizedBox(width: AppSpacing.m),
          Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(
                agent.name,
                style: const TextStyle(
                  fontFamily: AppFonts.sans,
                  fontFamilyFallback: AppFonts.sansFallback,
                  fontSize: 20,
                  fontWeight: FontWeight.w700,
                  color: AppColors.ink,
                ),
              ),
              const SizedBox(height: 2),
              Text(agent.role, style: AppTypography.subhead.copyWith(color: agent.accent)),
            ],
          ),
          const Spacer(),
        ],
      ),
    );
  }
}

/// 圆形渐变字号头像(对齐首页/详情页 AgentAvatar)。
class _Avatar extends StatelessWidget {
  const _Avatar({required this.agent, required this.size});
  final AgentInfo agent;
  final double size;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: size,
      height: size,
      alignment: Alignment.center,
      decoration: BoxDecoration(
        shape: BoxShape.circle,
        gradient: LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [agent.accent, agent.accent.withValues(alpha: 0.75)],
        ),
        border: Border.all(color: const Color(0xFFFFFFFF).withValues(alpha: 0.18), width: 1),
        boxShadow: [
          BoxShadow(
            color: agent.accent.withValues(alpha: 0.35),
            blurRadius: 5,
            offset: const Offset(0, 3),
          ),
        ],
      ),
      child: Text(
        agent.surname,
        style: TextStyle(
          fontFamily: AppFonts.sans,
          fontFamilyFallback: AppFonts.sansFallback,
          fontSize: size * 0.46,
          fontWeight: FontWeight.w700,
          color: const Color(0xFFFFFFFF),
        ),
      ),
    );
  }
}

// —— 暖色药丸分段(替掉系统 segmented 的灰底) ——

class _BucketSelector extends StatelessWidget {
  const _BucketSelector({required this.agent, required this.current, required this.onSelect});

  final AgentInfo agent;
  final TaskBucket current;
  final ValueChanged<TaskBucket> onSelect;

  @override
  Widget build(BuildContext context) {
    return SingleChildScrollView(
      scrollDirection: Axis.horizontal,
      padding: const EdgeInsets.symmetric(horizontal: AppSpacing.pageH),
      child: Row(
        children: [
          for (final b in TaskBucket.values) ...[
            _BucketPill(
              label: b.label,
              selected: b == current,
              accent: agent.accent,
              onTap: () => onSelect(b),
            ),
            if (b != TaskBucket.values.last) const SizedBox(width: 8),
          ],
        ],
      ),
    );
  }
}

class _BucketPill extends StatelessWidget {
  const _BucketPill({
    required this.label,
    required this.selected,
    required this.accent,
    required this.onTap,
  });

  final String label;
  final bool selected;
  final Color accent;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      behavior: HitTestBehavior.opaque,
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 180),
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 7),
        decoration: ShapeDecoration(
          color: selected ? accent : AppColors.ivory,
          shape: const StadiumBorder(),
          shadows: selected
              ? [BoxShadow(color: accent.withValues(alpha: 0.3), blurRadius: 5, offset: const Offset(0, 2))]
              : null,
        ),
        child: Text(
          label,
          style: AppTypography.subhead.copyWith(
            color: selected ? const Color(0xFFFFFFFF) : AppColors.ink.withValues(alpha: 0.65),
          ),
        ),
      ),
    );
  }
}

// —— 任务卡 ——

class _TaskCard extends StatefulWidget {
  const _TaskCard({required this.task, required this.onTap});
  final TaskMeta task;
  final VoidCallback onTap;

  @override
  State<_TaskCard> createState() => _TaskCardState();
}

class _TaskCardState extends State<_TaskCard> {
  bool _pressed = false;

  @override
  Widget build(BuildContext context) {
    final t = widget.task;
    final badge = t.sourceBadgeLabel;
    return GestureDetector(
      onTap: widget.onTap,
      onTapDown: (_) => setState(() => _pressed = true),
      onTapUp: (_) => setState(() => _pressed = false),
      onTapCancel: () => setState(() => _pressed = false),
      behavior: HitTestBehavior.opaque,
      child: AnimatedScale(
        scale: _pressed ? 0.97 : 1.0,
        duration: const Duration(milliseconds: 180),
        curve: Curves.easeOut,
        child: Container(
          padding: const EdgeInsets.all(AppSpacing.cardInner),
          decoration: BoxDecoration(
            color: AppColors.ivory,
            borderRadius: const BorderRadius.all(AppRadii.cardR),
            boxShadow: [
              BoxShadow(color: AppColors.cardShadow, blurRadius: 6, offset: const Offset(0, 3)),
            ],
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                t.titleGuess,
                style: AppTypography.titleM,
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
              ),
              if (t.subtitleText != null) ...[
                const SizedBox(height: 3),
                Text(
                  t.subtitleText!,
                  style: AppTypography.caption.copyWith(color: AppColors.ink.withValues(alpha: 0.5)),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
              ],
              const SizedBox(height: AppSpacing.m),
              Row(
                children: [
                  Icon(Icons.schedule, size: 12, color: AppColors.ink.withValues(alpha: 0.45)),
                  const SizedBox(width: 4),
                  Text(
                    shortTime(t.createdAt),
                    style: AppTypography.caption.copyWith(color: AppColors.ink.withValues(alpha: 0.45)),
                  ),
                  const Spacer(),
                  if (badge != null) ...[
                    _SourceBadge(text: badge),
                    const SizedBox(width: AppSpacing.s),
                  ],
                  _StatusChip(task: t),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// 来源角标胶囊:低饱和,弱于状态 chip——溯源信息不抢决策视线。
class _SourceBadge extends StatelessWidget {
  const _SourceBadge({required this.text});
  final String text;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
      decoration: ShapeDecoration(
        color: AppColors.ink.withValues(alpha: 0.06),
        shape: const StadiumBorder(),
      ),
      child: Text(
        text,
        style: AppTypography.caption2.copyWith(
          fontWeight: FontWeight.w500,
          color: AppColors.ink.withValues(alpha: 0.5),
        ),
      ),
    );
  }
}

// —— 状态芯片(带图标;运行中走呼吸点)对齐 iOS TaskCardView.statusChip ——

class _StatusChip extends StatelessWidget {
  const _StatusChip({required this.task});
  final TaskMeta task;

  @override
  Widget build(BuildContext context) {
    final d = task.decision;
    if (d != null) {
      final shenkuo = task.cmd == 'shenkuo';
      if (d == 'approved') {
        return _chip('已采用', AppColors.statusGreen, icon: Icons.verified);
      }
      // 沈括 rejected 语义是「弃用」(中性灰),不是「打回重做」(红)。
      return shenkuo
          ? _chip('已弃用', AppColors.ink.withValues(alpha: 0.55), icon: Icons.archive_outlined)
          : _chip('已打回', AppColors.statusRed, icon: Icons.undo);
    }
    switch (task.status) {
      case 'running':
        return _chip('运行中', AppColors.amber, breathing: true);
      case 'pending':
        return _chip('排队中', AppColors.ink.withValues(alpha: 0.5), icon: Icons.hourglass_empty);
      case 'failed':
        return _chip('已失败', AppColors.statusRed, icon: Icons.warning_rounded);
      case 'cancelled':
        return _chip('已取消', AppColors.ink.withValues(alpha: 0.5), icon: Icons.block);
      default:
        return _chip('待验收', AppColors.orange, icon: Icons.inbox);
    }
  }

  Widget _chip(String text, Color c, {IconData? icon, bool breathing = false}) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
      decoration: ShapeDecoration(color: c.withValues(alpha: 0.15), shape: const StadiumBorder()),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          if (breathing) ...[
            _BreathingDot(color: c),
            const SizedBox(width: 5),
          ] else if (icon != null) ...[
            Icon(icon, size: 11, color: c),
            const SizedBox(width: 5),
          ],
          Text(text, style: AppTypography.caption.copyWith(fontWeight: FontWeight.w600, color: c)),
        ],
      ),
    );
  }
}

/// 呼吸点:运行中任务的活感指示,正弦缩放 + 亮度起伏(和主灯一脉相承)。
class _BreathingDot extends StatefulWidget {
  const _BreathingDot({required this.color});
  final Color color;

  @override
  State<_BreathingDot> createState() => _BreathingDotState();
}

class _BreathingDotState extends State<_BreathingDot> with SingleTickerProviderStateMixin {
  late final AnimationController _controller = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 2200),
  )..repeat();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: _controller,
      builder: (context, _) {
        final s = (math.sin(2 * math.pi * _controller.value) + 1) / 2;
        // 缩放 0.8 + 0.35*s、亮度 0.55 + 0.45*s(对齐 iOS BreathingDot)。
        return Transform.scale(
          scale: 0.8 + 0.35 * s,
          child: Container(
            width: 8,
            height: 8,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              color: widget.color.withValues(alpha: 0.55 + 0.45 * s),
              boxShadow: [
                BoxShadow(color: widget.color.withValues(alpha: 0.7 * s), blurRadius: 4 * s),
              ],
            ),
          ),
        );
      },
    );
  }
}

// —— 卧龙续跑段折叠区:默认收起只显条数,展开后逐段一行可点进详情 ——

class _SegmentsFold extends StatefulWidget {
  const _SegmentsFold({required this.segments, required this.onOpen});
  final List<TaskMeta> segments;
  final ValueChanged<TaskMeta> onOpen;

  @override
  State<_SegmentsFold> createState() => _SegmentsFoldState();
}

class _SegmentsFoldState extends State<_SegmentsFold> {
  bool _open = false;

  @override
  Widget build(BuildContext context) {
    final faint = AppColors.ink.withValues(alpha: 0.55);
    return Container(
      margin: const EdgeInsets.fromLTRB(10, AppSpacing.s, 10, 0),
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
      decoration: BoxDecoration(
        color: AppColors.ivory.withValues(alpha: 0.72),
        borderRadius: BorderRadius.circular(14),
      ),
      child: Column(
        children: [
          GestureDetector(
            onTap: () => setState(() => _open = !_open),
            behavior: HitTestBehavior.opaque,
            child: Row(
              children: [
                Icon(Icons.call_split, size: 13, color: faint),
                const SizedBox(width: 5),
                Text(
                  '${widget.segments.length} 个续跑段',
                  style: AppTypography.caption.copyWith(fontWeight: FontWeight.w600, color: faint),
                ),
                const Spacer(),
                Icon(
                  _open ? Icons.expand_less : Icons.expand_more,
                  size: 18,
                  color: AppColors.ink.withValues(alpha: 0.45),
                ),
              ],
            ),
          ),
          if (_open) ...[
            const SizedBox(height: 2),
            for (int i = 0; i < widget.segments.length; i++) ...[
              _ResumeSegmentRow(task: widget.segments[i], onTap: () => widget.onOpen(widget.segments[i])),
              if (i != widget.segments.length - 1)
                Divider(height: 1, color: AppColors.ink.withValues(alpha: 0.08)),
            ],
          ],
        ],
      ),
    );
  }
}

/// 折叠区里的一行续跑段(紧凑:标题 + 时间 + 状态词)。
class _ResumeSegmentRow extends StatelessWidget {
  const _ResumeSegmentRow({required this.task, required this.onTap});
  final TaskMeta task;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final word = _statusWord(task);
    return GestureDetector(
      onTap: onTap,
      behavior: HitTestBehavior.opaque,
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 8),
        child: Row(
          children: [
            Icon(Icons.sync, size: 13, color: AppColors.ink.withValues(alpha: 0.4)),
            const SizedBox(width: 8),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(
                    task.titleGuess,
                    style: AppTypography.subhead.copyWith(fontWeight: FontWeight.w400),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
                  const SizedBox(height: 2),
                  Text(
                    shortTime(task.createdAt),
                    style: AppTypography.caption2.copyWith(color: AppColors.ink.withValues(alpha: 0.4)),
                  ),
                ],
              ),
            ),
            const SizedBox(width: AppSpacing.s),
            Text(
              word.$1,
              style: AppTypography.caption2.copyWith(fontWeight: FontWeight.w600, color: word.$2),
            ),
            const SizedBox(width: 4),
            Icon(Icons.chevron_right, size: 13, color: AppColors.ink.withValues(alpha: 0.25)),
          ],
        ),
      ),
    );
  }

  (String, Color) _statusWord(TaskMeta t) {
    final d = t.decision;
    if (d != null) {
      return d == 'approved' ? ('已采用', AppColors.statusGreen) : ('已打回', AppColors.statusRed);
    }
    switch (t.status) {
      case 'running':
        return ('运行中', AppColors.amber);
      case 'pending':
        return ('排队中', AppColors.ink.withValues(alpha: 0.5));
      case 'failed':
        return ('已失败', AppColors.statusRed);
      default:
        return ('待验收', AppColors.orange);
    }
  }
}

// —— 空态 / 错误态 ——

class _EmptyState extends StatelessWidget {
  const _EmptyState({required this.bucket});
  final TaskBucket bucket;

  @override
  Widget build(BuildContext context) {
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(bucket.emptyIcon, size: 44, color: AppColors.inkFaint),
        const SizedBox(height: AppSpacing.m),
        Text(bucket.emptyTitle, style: AppTypography.body.copyWith(color: AppColors.inkMuted)),
      ],
    );
  }
}

class _ErrorState extends StatelessWidget {
  const _ErrorState({required this.message, required this.onRetry});
  final String message;
  final Future<void> Function() onRetry;

  @override
  Widget build(BuildContext context) {
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(Icons.wifi_off_rounded, color: AppColors.inkFaint, size: 40),
        const SizedBox(height: AppSpacing.m),
        Text('连不上工厂后端', style: AppTypography.titleM),
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

import 'package:flutter/material.dart';

import '../../core/net/endpoint_resolver.dart';
import '../../core/net/factory_client.dart';
import '../../core/net/models.dart';
import '../../design/components/app_card.dart';
import '../../design/components/tag_chip.dart';
import '../../design/tokens.dart';
import '../../design/typography.dart';
import '../compose/compose_screen.dart';
import '../demo/design_gallery.dart';
import '../detail/task_detail_screen.dart';

/// 收件箱:GET /tasks 任务列表(Phase 2 首个接后端的真实页面)。
/// 用可注入的 [tasksLoader] 便于测试;默认走 LAN 直连工厂后端。
class TaskListScreen extends StatefulWidget {
  const TaskListScreen({super.key, this.tasksLoader});

  /// 测试可注入假数据;为 null 时走真实 [FactoryClient]。
  final Future<List<TaskMeta>> Function()? tasksLoader;

  @override
  State<TaskListScreen> createState() => _TaskListScreenState();
}

class _TaskListScreenState extends State<TaskListScreen> {
  static const String _devBase = 'http://liuzhihao-mbp.local:8810';

  late final FactoryClient _client = FactoryClient(resolver: DirectEndpointResolver(_devBase));
  late final Future<List<TaskMeta>> Function() _load = widget.tasksLoader ?? _client.listTasks;
  late Future<List<TaskMeta>> _future = _load();

  Future<void> _refresh() async {
    setState(() => _future = _load());
    try {
      await _future;
    } catch (_) {/* 错误由 FutureBuilder 呈现 */}
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        backgroundColor: AppColors.sand,
        foregroundColor: AppColors.ink,
        title: Text('收件箱', style: AppTypography.navTitle),
        actions: [
          IconButton(
            tooltip: '发起任务',
            icon: const Icon(Icons.add),
            onPressed: () async {
              final created = await Navigator.of(context).push<String>(
                MaterialPageRoute<String>(builder: (_) => const ComposeScreen()),
              );
              if (created != null) _refresh();
            },
          ),
          IconButton(
            tooltip: '设计系统',
            icon: const Icon(Icons.palette_outlined),
            onPressed: () => Navigator.of(context).push(
              MaterialPageRoute<void>(builder: (_) => const DesignGallery()),
            ),
          ),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: _refresh,
        child: FutureBuilder<List<TaskMeta>>(
          future: _future,
          builder: (context, snap) {
            if (snap.connectionState == ConnectionState.waiting) {
              return _centered(const CircularProgressIndicator());
            }
            if (snap.hasError) {
              return _centered(_ErrorState(message: '${snap.error}', onRetry: _refresh));
            }
            final tasks = snap.data ?? const <TaskMeta>[];
            if (tasks.isEmpty) {
              return _centered(Text('收件箱空空如也', style: AppTypography.body.copyWith(color: AppColors.inkMuted)));
            }
            return ListView.separated(
              physics: const AlwaysScrollableScrollPhysics(),
              padding: const EdgeInsets.fromLTRB(
                AppSpacing.pageH, AppSpacing.m, AppSpacing.pageH, AppSpacing.pageBottom,
              ),
              itemCount: tasks.length,
              separatorBuilder: (_, _) => const SizedBox(height: AppSpacing.m),
              itemBuilder: (context, i) {
                final t = tasks[i];
                return _TaskCard(
                  task: t,
                  onTap: () async {
                    await Navigator.of(context).push<void>(
                      MaterialPageRoute<void>(builder: (_) => TaskDetailScreen(taskId: t.taskId)),
                    );
                    _refresh(); // 详情可能改了 review,回来刷新
                  },
                );
              },
            );
          },
        ),
      ),
    );
  }

  /// 单元素可滚动容器,保证加载/错误/空态下 RefreshIndicator 仍可下拉。
  Widget _centered(Widget child) => ListView(
        physics: const AlwaysScrollableScrollPhysics(),
        children: [
          Padding(padding: const EdgeInsets.only(top: 96), child: Center(child: child)),
        ],
      );
}

/// 任务状态 → 语义色 + 中文标签。
({Color color, String label}) _statusStyle(TaskMeta t) {
  switch (t.decision) {
    case 'approved':
      return (color: AppColors.statusAdopted, label: '已采用');
    case 'rejected':
      return (color: AppColors.statusRed, label: '已打回');
  }
  switch (t.status) {
    case 'running':
      return (color: AppColors.amber, label: '生成中');
    case 'queued':
    case 'pending':
      return (color: AppColors.inkFaint, label: '排队中');
    case 'done':
    case 'completed':
      return (color: AppColors.orange, label: '待验收');
    case 'failed':
    case 'error':
      return (color: AppColors.statusRed, label: '失败');
    case 'cancelled':
      return (color: AppColors.inkMuted, label: '已取消');
    default:
      return (color: AppColors.inkMuted, label: t.status);
  }
}

class _TaskCard extends StatelessWidget {
  const _TaskCard({required this.task, this.onTap});
  final TaskMeta task;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final style = _statusStyle(task);
    final badge = task.sourceBadgeLabel;
    return GestureDetector(
      onTap: onTap,
      behavior: HitTestBehavior.opaque,
      child: AppCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Expanded(
                  child: Text(task.titleGuess, style: AppTypography.titleM, maxLines: 2, overflow: TextOverflow.ellipsis),
                ),
                const SizedBox(width: AppSpacing.s),
                TagChip(label: style.label, tint: style.color),
              ],
            ),
            if (task.subtitleText != null) ...[
              const SizedBox(height: AppSpacing.xs),
              Text(task.subtitleText!, style: AppTypography.caption.copyWith(color: AppColors.inkMuted)),
            ],
            const SizedBox(height: AppSpacing.s),
            Row(
              children: [
                Text(task.cmd, style: AppTypography.monoS.copyWith(color: AppColors.inkMuted)),
                if (badge != null) ...[
                  const SizedBox(width: AppSpacing.s),
                  TagChip(label: badge, tint: AppColors.accentTopic),
                ],
              ],
            ),
          ],
        ),
      ),
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
        Icon(Icons.cloud_off_outlined, color: AppColors.inkFaint, size: 40),
        const SizedBox(height: AppSpacing.m),
        Text('连不上工厂后端', style: AppTypography.titleM),
        const SizedBox(height: AppSpacing.xs),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: AppSpacing.pageH),
          child: Text(message, style: AppTypography.caption.copyWith(color: AppColors.inkMuted), textAlign: TextAlign.center),
        ),
        const SizedBox(height: AppSpacing.m),
        TextButton(onPressed: onRetry, child: const Text('重试')),
      ],
    );
  }
}

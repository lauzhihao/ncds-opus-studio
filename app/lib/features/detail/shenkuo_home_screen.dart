// 沈存中(沈括)主页 —— app 决策视角入口。
// 与 web 同源数据、不同查询视角:
//   · 决策:GET /tasks 筛 cmd=shenkuo,分桶验收(通过/弃用)
//   · 对标号:GET /subscriptions(+ authors_repo 水合名片)→ 点进 GET /accounts/.../posts
// 不接 /jobs 画布;派采集仍走 POST /tasks。

import 'dart:async';

import 'package:flutter/material.dart';

import '../../core/labels/platform_domain.dart';
import '../../core/net/endpoint_resolver.dart';
import '../../core/net/factory_client.dart';
import '../../core/net/models.dart';
import '../../design/components/tag_chip.dart';
import '../../design/tokens.dart';
import '../../design/typography.dart';
import '../home/agent_catalog.dart';
import '../inbox/agent_task_list_screen.dart' show TaskBucket, shortTime;
import '../subscriptions/subscriptions_screen.dart';
import 'account_posts_screen.dart';
import 'shenkuo_collect_panel.dart' show shenkuoCount;
import 'task_detail_screen.dart';

/// 顶层视角:决策(任务闸门) vs 对标号(资料库)。
enum _ShenkuoPane { decision, authors }

/// 沈存中专属首页。
class ShenkuoHomeScreen extends StatefulWidget {
  const ShenkuoHomeScreen({super.key, required this.agent, this.client});

  final AgentInfo agent;
  final FactoryClient? client;

  @override
  State<ShenkuoHomeScreen> createState() => _ShenkuoHomeScreenState();
}

class _ShenkuoHomeScreenState extends State<ShenkuoHomeScreen> with WidgetsBindingObserver {
  static const String _devBase = 'http://liuzhihao-mbp.local:8810';

  late final FactoryClient _client =
      widget.client ?? FactoryClient(resolver: DirectEndpointResolver(_devBase));

  _ShenkuoPane _pane = _ShenkuoPane.decision;
  TaskBucket _bucket = TaskBucket.review;

  List<TaskMeta> _tasks = const <TaskMeta>[];
  List<SubscriptionAuthor> _authors = const <SubscriptionAuthor>[];
  bool _loadingTasks = true;
  bool _loadingAuthors = true;
  String? _taskError;
  String? _authorError;
  Timer? _timer;

  AgentInfo get _agent => widget.agent;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    WidgetsBinding.instance.addPostFrameCallback((_) => _kickoff());
  }

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
    if (state == AppLifecycleState.resumed) {
      _start();
    } else {
      _timer?.cancel();
    }
  }

  void _start() {
    _timer?.cancel();
    _refreshAll();
    _timer = Timer.periodic(const Duration(seconds: 30), (_) => _refreshAll());
  }

  Future<void> _refreshAll() async {
    await Future.wait<void>([_refreshTasks(), _refreshAuthors()]);
  }

  Future<void> _refreshTasks() async {
    try {
      final list = await _client.listTasks();
      if (!mounted) return;
      setState(() {
        _tasks = list;
        _taskError = null;
        _loadingTasks = false;
      });
    } catch (e) {
      if (isCancellation(e)) return;
      if (!mounted) return;
      setState(() {
        if (_tasks.isEmpty) _taskError = '$e';
        _loadingTasks = false;
      });
    }
  }

  Future<void> _refreshAuthors() async {
    try {
      final cfg = await _client.subscriptions();
      if (!mounted) return;
      setState(() {
        _authors = List<SubscriptionAuthor>.from(cfg.authors ?? const <SubscriptionAuthor>[]);
        _authorError = null;
        _loadingAuthors = false;
      });
    } catch (e) {
      if (isCancellation(e)) return;
      if (!mounted) return;
      setState(() {
        if (_authors.isEmpty) _authorError = '$e';
        _loadingAuthors = false;
      });
    }
  }

  List<TaskMeta> get _mine => _tasks.where((t) => t.cmd == 'shenkuo').toList();

  List<TaskMeta> get _shown => _mine.where((t) => TaskBucket.of(t) == _bucket).toList()
    ..sort((a, b) => (b.createdAt ?? '').compareTo(a.createdAt ?? ''));

  int get _reviewCount =>
      _mine.where((t) => t.status == 'completed' && t.decision == null).length;

  @override
  Widget build(BuildContext context) {
    final accent = _agent.accent;
    return Scaffold(
      backgroundColor: AppColors.sand,
      appBar: AppBar(
        backgroundColor: AppColors.sand,
        foregroundColor: AppColors.ink,
        elevation: 0,
        scrolledUnderElevation: 0,
        surfaceTintColor: Colors.transparent,
        actions: [
          // 采集入口「+」已移到 App 首页齿轮右侧(剪贴板直读);此处只留订阅配置。
          IconButton(
            tooltip: '订阅管理',
            icon: const Icon(Icons.settings_outlined, size: 20),
            onPressed: () async {
              await Navigator.of(context).push<void>(
                MaterialPageRoute<void>(builder: (_) => const SubscriptionsScreen()),
              );
              _refreshAuthors();
            },
          ),
        ],
      ),
      body: SafeArea(
        top: false,
        child: Column(
          children: [
            const SizedBox(height: AppSpacing.s),
            _Header(agent: _agent, reviewCount: _reviewCount, authorCount: _authors.length),
            const SizedBox(height: 14),
            _PaneSelector(
              accent: accent,
              current: _pane,
              reviewCount: _reviewCount,
              authorCount: _authors.length,
              onSelect: (p) => setState(() => _pane = p),
            ),
            const SizedBox(height: 12),
            if (_pane == _ShenkuoPane.decision) ...[
              _BucketSelector(
                accent: accent,
                current: _bucket,
                onSelect: (b) => setState(() => _bucket = b),
              ),
              const SizedBox(height: 12),
            ],
            Expanded(
              child: RefreshIndicator(
                onRefresh: _refreshAll,
                color: accent,
                backgroundColor: AppColors.ivory,
                child: _pane == _ShenkuoPane.decision ? _decisionBody() : _authorsBody(),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _decisionBody() {
    if (_loadingTasks && _tasks.isEmpty) {
      return _centered(const CircularProgressIndicator());
    }
    if (_taskError != null && _tasks.isEmpty) {
      return _centered(_ErrorBlock(message: _taskError!, onRetry: _refreshTasks));
    }
    final shown = _shown;
    if (shown.isEmpty) {
      return _centered(_EmptyBlock(
        icon: _bucket.emptyIcon,
        title: _bucket.emptyTitle,
        hint: '采集任务与 web 共用同一 TaskStore;验收只在 app 决策面完成。',
      ));
    }
    return ListView.builder(
      physics: const AlwaysScrollableScrollPhysics(),
      padding: const EdgeInsets.fromLTRB(
        AppSpacing.pageH,
        AppSpacing.xs,
        AppSpacing.pageH,
        AppSpacing.pageBottom,
      ),
      itemCount: shown.length,
      itemBuilder: (context, i) {
        final t = shown[i];
        return Padding(
          padding: EdgeInsets.only(bottom: i == shown.length - 1 ? 0 : AppSpacing.m),
          child: _TaskCard(task: t, onTap: () => _openTask(t)),
        );
      },
    );
  }

  Widget _authorsBody() {
    if (_loadingAuthors && _authors.isEmpty) {
      return _centered(const CircularProgressIndicator());
    }
    if (_authorError != null && _authors.isEmpty) {
      return _centered(_ErrorBlock(message: _authorError!, onRetry: _refreshAuthors));
    }
    if (_authors.isEmpty) {
      return _centered(
        _EmptyBlock(
          icon: Icons.person_search_outlined,
          title: '还没有对标号',
          hint: '点右上角齿轮进订阅管理添加;与 web 首页「长期任务」同源 /subscriptions。',
          actionLabel: '去订阅管理',
          onAction: () async {
            await Navigator.of(context).push<void>(
              MaterialPageRoute<void>(builder: (_) => const SubscriptionsScreen()),
            );
            _refreshAuthors();
          },
        ),
      );
    }
    return ListView.builder(
      physics: const AlwaysScrollableScrollPhysics(),
      padding: const EdgeInsets.fromLTRB(
        AppSpacing.pageH,
        AppSpacing.xs,
        AppSpacing.pageH,
        AppSpacing.pageBottom,
      ),
      itemCount: _authors.length,
      itemBuilder: (context, i) {
        final a = _authors[i];
        return Padding(
          padding: EdgeInsets.only(bottom: i == _authors.length - 1 ? 0 : AppSpacing.m),
          child: _AuthorCard(
            author: a,
            onTap: () async {
              await Navigator.of(context).push<void>(
                MaterialPageRoute<void>(
                  builder: (_) => AccountPostsScreen(author: a, client: _client),
                ),
              );
              // 回来可能刚派了采集任务
              _refreshTasks();
            },
          ),
        );
      },
    );
  }

  Future<void> _openTask(TaskMeta t) async {
    await Navigator.of(context).push<void>(
      MaterialPageRoute<void>(builder: (_) => TaskDetailScreen(taskId: t.taskId)),
    );
    _refreshTasks();
  }

  Widget _centered(Widget child) => ListView(
        physics: const AlwaysScrollableScrollPhysics(),
        children: [
          Padding(padding: const EdgeInsets.only(top: 80), child: Center(child: child)),
        ],
      );
}

// —— 顶栏头像 + 名字 ——

class _Header extends StatelessWidget {
  const _Header({
    required this.agent,
    required this.reviewCount,
    required this.authorCount,
  });

  final AgentInfo agent;
  final int reviewCount;
  final int authorCount;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: AppSpacing.pageH),
      child: Row(
        children: [
          _Avatar(agent: agent, size: 48),
          const SizedBox(width: AppSpacing.m),
          Expanded(
            child: Column(
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
                const SizedBox(height: 2),
                Text(
                  '待验收 $reviewCount · 对标 $authorCount',
                  style: AppTypography.caption.copyWith(color: AppColors.inkMuted),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

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

// —— 视角切换 ——

class _PaneSelector extends StatelessWidget {
  const _PaneSelector({
    required this.accent,
    required this.current,
    required this.reviewCount,
    required this.authorCount,
    required this.onSelect,
  });

  final Color accent;
  final _ShenkuoPane current;
  final int reviewCount;
  final int authorCount;
  final ValueChanged<_ShenkuoPane> onSelect;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: AppSpacing.pageH),
      child: Row(
        children: [
          Expanded(
            child: _PanePill(
              label: reviewCount > 0 ? '决策 · $reviewCount' : '决策',
              selected: current == _ShenkuoPane.decision,
              accent: accent,
              onTap: () => onSelect(_ShenkuoPane.decision),
            ),
          ),
          const SizedBox(width: 10),
          Expanded(
            child: _PanePill(
              label: authorCount > 0 ? '对标号 · $authorCount' : '对标号',
              selected: current == _ShenkuoPane.authors,
              accent: accent,
              onTap: () => onSelect(_ShenkuoPane.authors),
            ),
          ),
        ],
      ),
    );
  }
}

class _PanePill extends StatelessWidget {
  const _PanePill({
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
        padding: const EdgeInsets.symmetric(vertical: 10),
        alignment: Alignment.center,
        decoration: ShapeDecoration(
          color: selected ? accent : AppColors.ivory,
          shape: const StadiumBorder(),
          shadows: selected
              ? [BoxShadow(color: accent.withValues(alpha: 0.28), blurRadius: 6, offset: const Offset(0, 2))]
              : null,
        ),
        child: Text(
          label,
          style: AppTypography.subhead.copyWith(
            fontWeight: FontWeight.w600,
            color: selected ? const Color(0xFFFFFFFF) : AppColors.ink.withValues(alpha: 0.65),
          ),
        ),
      ),
    );
  }
}

// —— 决策分桶 ——

class _BucketSelector extends StatelessWidget {
  const _BucketSelector({required this.accent, required this.current, required this.onSelect});

  final Color accent;
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
              accent: accent,
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
          color: selected ? accent.withValues(alpha: 0.18) : AppColors.ivory,
          shape: StadiumBorder(
            side: BorderSide(color: selected ? accent : AppColors.ink.withValues(alpha: 0.08)),
          ),
        ),
        child: Text(
          label,
          style: AppTypography.subhead.copyWith(
            color: selected ? accent : AppColors.ink.withValues(alpha: 0.65),
            fontWeight: selected ? FontWeight.w600 : FontWeight.w500,
          ),
        ),
      ),
    );
  }
}

// —— 任务卡 / 作者卡 ——

class _TaskCard extends StatelessWidget {
  const _TaskCard({required this.task, required this.onTap});
  final TaskMeta task;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final badge = task.sourceBadgeLabel;
    return Material(
      color: Colors.transparent,
      child: InkWell(
        onTap: onTap,
        borderRadius: const BorderRadius.all(AppRadii.cardR),
        child: Ink(
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
                task.titleGuess,
                style: AppTypography.titleM,
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
              ),
              if (task.subtitleText != null) ...[
                const SizedBox(height: 3),
                Text(
                  task.subtitleText!,
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
                    shortTime(task.createdAt),
                    style: AppTypography.caption.copyWith(color: AppColors.ink.withValues(alpha: 0.45)),
                  ),
                  const Spacer(),
                  if (badge != null) ...[
                    TagChip(label: badge, tint: AppColors.inkFaint),
                    const SizedBox(width: AppSpacing.s),
                  ],
                  _statusChip(task),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _statusChip(TaskMeta task) {
    final d = task.decision;
    if (d != null) {
      if (d == 'approved') {
        return TagChip(label: '已采用', tint: AppColors.statusGreen);
      }
      return TagChip(label: '已弃用', tint: AppColors.ink.withValues(alpha: 0.55));
    }
    switch (task.status) {
      case 'running':
        return TagChip(label: '运行中', tint: AppColors.amber);
      case 'pending':
        return TagChip(label: '排队中', tint: AppColors.ink.withValues(alpha: 0.5));
      case 'failed':
        return TagChip(label: '已失败', tint: AppColors.statusRed);
      case 'cancelled':
        return TagChip(label: '已取消', tint: AppColors.ink.withValues(alpha: 0.5));
      default:
        return TagChip(label: '待验收', tint: AppColors.orange);
    }
  }
}

class _AuthorCard extends StatelessWidget {
  const _AuthorCard({required this.author, required this.onTap});
  final SubscriptionAuthor author;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final a = author;
    final handle = (a.uniqueId != null && a.uniqueId!.isNotEmpty) ? '@${a.uniqueId}' : null;
    return Material(
      color: Colors.transparent,
      child: InkWell(
        onTap: onTap,
        borderRadius: const BorderRadius.all(AppRadii.cardR),
        child: Ink(
          padding: const EdgeInsets.all(AppSpacing.cardInner),
          decoration: BoxDecoration(
            color: AppColors.ivory,
            borderRadius: const BorderRadius.all(AppRadii.cardR),
            boxShadow: [
              BoxShadow(color: AppColors.cardShadow, blurRadius: 6, offset: const Offset(0, 3)),
            ],
          ),
          child: Row(
            children: [
              _AuthorAvatar(url: a.avatar, name: a.displayName),
              const SizedBox(width: AppSpacing.m),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      a.displayName,
                      style: AppTypography.titleM,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),
                    if (handle != null) ...[
                      const SizedBox(height: 2),
                      Text(
                        handle,
                        style: AppTypography.caption.copyWith(color: AppColors.inkMuted),
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                      ),
                    ],
                    const SizedBox(height: AppSpacing.s),
                    Wrap(
                      spacing: 8,
                      runSpacing: 4,
                      children: [
                        TagChip(
                          // 存储 key → 中文/短标（对齐 web platformDisplayName）
                          label: platformDisplayName(a.platformOrDouyin),
                          tint: AppColors.inkFaint,
                        ),
                        TagChip(
                          label: a.isEnabled ? '巡查中' : '已停用',
                          tint: a.isEnabled ? AppColors.statusAdopted : AppColors.inkFaint,
                        ),
                        // 赛道 key → 财经/情感（对齐 web domainByKey）
                        if (domainDisplayName(a.domain) != null)
                          TagChip(
                            label: domainDisplayName(a.domain)!,
                            tint: AppColors.accentCollect,
                          ),
                      ],
                    ),
                    if (a.followerCount != null || a.worksCount != null) ...[
                      const SizedBox(height: AppSpacing.s),
                      Text(
                        [
                          if (a.followerCount != null) '粉丝 ${shenkuoCount(a.followerCount!)}',
                          if (a.worksCount != null) '作品 ${shenkuoCount(a.worksCount!)}',
                        ].join(' · '),
                        style: AppTypography.caption.copyWith(color: AppColors.inkMuted),
                      ),
                    ],
                  ],
                ),
              ),
              Icon(Icons.chevron_right, color: AppColors.inkFaint),
            ],
          ),
        ),
      ),
    );
  }
}

class _AuthorAvatar extends StatelessWidget {
  const _AuthorAvatar({this.url, required this.name});
  final String? url;
  final String name;

  @override
  Widget build(BuildContext context) {
    final has = url != null && url!.isNotEmpty;
    return ClipOval(
      child: SizedBox(
        width: 48,
        height: 48,
        child: has
            ? Image.network(
                url!,
                fit: BoxFit.cover,
                errorBuilder: (_, error, stackTrace) => _fallback(),
              )
            : _fallback(),
      ),
    );
  }

  Widget _fallback() {
    final ch = name.isNotEmpty ? name.characters.first : '沈';
    return Container(
      color: AppColors.accentCollect.withValues(alpha: 0.18),
      alignment: Alignment.center,
      child: Text(
        ch,
        style: AppTypography.titleM.copyWith(color: AppColors.accentCollect),
      ),
    );
  }
}

class _ErrorBlock extends StatelessWidget {
  const _ErrorBlock({required this.message, required this.onRetry});
  final String message;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        Text(message, style: AppTypography.body.copyWith(color: AppColors.statusRed), textAlign: TextAlign.center),
        const SizedBox(height: AppSpacing.m),
        TextButton(onPressed: onRetry, child: const Text('重试')),
      ],
    );
  }
}

class _EmptyBlock extends StatelessWidget {
  const _EmptyBlock({
    required this.icon,
    required this.title,
    required this.hint,
    this.actionLabel,
    this.onAction,
  });

  final IconData icon;
  final String title;
  final String hint;
  final String? actionLabel;
  final VoidCallback? onAction;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: AppSpacing.pageH),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, size: 36, color: AppColors.inkFaint),
          const SizedBox(height: AppSpacing.m),
          Text(title, style: AppTypography.titleM),
          const SizedBox(height: AppSpacing.s),
          Text(
            hint,
            style: AppTypography.caption.copyWith(color: AppColors.inkMuted),
            textAlign: TextAlign.center,
          ),
          if (actionLabel != null && onAction != null) ...[
            const SizedBox(height: AppSpacing.m),
            TextButton(onPressed: onAction, child: Text(actionLabel!)),
          ],
        ],
      ),
    );
  }
}

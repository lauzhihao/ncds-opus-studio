import 'package:flutter/material.dart';

import '../../core/net/endpoint_resolver.dart';
import '../../core/net/factory_client.dart';
import '../../core/net/models.dart';
import '../../design/components/app_card.dart';
import '../../design/components/decision_button.dart';
import '../../design/liquid_glass.dart';
import '../../design/tokens.dart';
import '../../design/typography.dart';
import '../home/agent_catalog.dart';
import 'artifact_viewer.dart';
import 'boya_sound_panel.dart';
import 'guiguzi_topics_panel.dart';
import 'reject_voice_sheet.dart';
import 'shenkuo_collect_panel.dart';
import 'wolong_report_panel.dart';
import 'wudaozi_storyboard_panel.dart';

// 任务详情:GET /tasks/{id} 拿终态产物/验收;运行中用 SSE 实时追加进度日志。
// 信息架构对齐 iOS AgentTaskDetailView:hero(头像+标题+灯态) → agent 专属成果面板
// → 产物审看 → 决策(通过/打回,沈括是 通过/弃用) → 失败/取消处置 → 进度日志。
// agent 身份由 detail.cmd 自查 agentCatalog 得到(无需上游传参)。

/// 详情页可审看的产物 kind(对齐 iOS ArtifactsCard.humanKinds);dir/data 等工程中间产物不展示。
const Set<String> _reviewableKinds = {'video', 'audio', 'script', 'image', 'text'};

/// 任务详情页。进入即拉详情;运行中(running/queued/pending)挂 SSE 进度流。
/// [client] 可注入便于测试;为 null 时走 LAN 直连工厂后端(mDNS 自动解析 .local)。
class TaskDetailScreen extends StatefulWidget {
  const TaskDetailScreen({super.key, required this.taskId, this.client});

  final String taskId;
  final FactoryClient? client;

  @override
  State<TaskDetailScreen> createState() => _TaskDetailScreenState();
}

class _TaskDetailScreenState extends State<TaskDetailScreen> {
  static const String _devBase = 'http://liuzhihao-mbp.local:8810';

  late final FactoryClient _client =
      widget.client ?? FactoryClient(resolver: DirectEndpointResolver(_devBase));

  // 固定 hero 磨砂头:detail 加载后回填头像/标题/灯态/数据,钉在顶部磨砂条里。
  static const double _heroBarHeight = 60;
  String _navTitle = '';
  AgentInfo? _navAgent;
  TaskLight _navLight = TaskLight.idle;
  ShenkuoEntry? _navStats; // 沈括单条:赞评转藏数据行展示在头部

  Future<TaskDetail>? _future;

  // 进度日志(SSE 累积)。运行中才挂流;终态不展示工程噪音。
  final List<String> _logs = <String>[];
  String? _streamError;
  bool _streaming = false;

  // 决策/取消/恢复在途时禁用按钮,避免重复提交。
  bool _reviewing = false;
  String? _reviewError;

  // 改判:已决策卡重开决策区(只覆盖 review 标注,不回卷流程)。
  bool _rejudging = false;

  @override
  void initState() {
    super.initState();
    // 首拉延到 push 转场结束:数据若在右滑动画中途到达,构建重内容会丢帧、肉眼抖动。
    WidgetsBinding.instance.addPostFrameCallback((_) => _kickoff());
  }

  /// 等本页进场转场动画跑完再发首个请求(局域网很快,否则会在滑动中卡顿)。
  void _kickoff() {
    if (!mounted) return;
    final Animation<double>? anim = ModalRoute.of(context)?.animation;
    if (anim == null || anim.isCompleted) {
      setState(() => _future = _loadDetail());
    } else {
      void listener(AnimationStatus status) {
        if (status == AnimationStatus.completed) {
          anim.removeStatusListener(listener);
          if (mounted) setState(() => _future = _loadDetail());
        }
      }
      anim.addStatusListener(listener);
    }
  }

  Future<TaskDetail> _loadDetail() async {
    final detail = await _client.task(widget.taskId);
    if (_isLive(detail.status) && !_streaming) _startStream();
    if (mounted) {
      // 回填固定 hero 数据(头像/标题/灯态/沈括数据行)。
      final agent = _agentFor(detail.cmd);
      setState(() {
        _navTitle = detail.titleGuess;
        _navAgent = agent;
        _navLight = _lightFor(detail, agent);
        _navStats =
            detail.shenkuo?.collected?.length == 1 ? detail.shenkuo!.collected!.first : null;
      });
    }
    return detail;
  }

  /// 是否为「跑着/排队」的活动态(需要实时进度流)。
  bool _isLive(String status) =>
      status == 'running' || status == 'queued' || status == 'pending';

  void _startStream() {
    _streaming = true;
    () async {
      try {
        await for (final ev in _client.events(widget.taskId)) {
          if (!mounted) return;
          if (ev.type == 'progress' && ev.text != null && ev.text!.isNotEmpty) {
            setState(() => _logs.add(ev.text!));
          } else if (ev.type == 'error') {
            setState(() => _streamError = ev.error);
          }
        }
      } catch (e) {
        if (isCancellation(e)) return; // 退出/重启导致的取消不算故障
        if (mounted) setState(() => _streamError = '$e');
      }
      // 收流(done/[DONE]/error):刷新详情拿终态产物与验收。
      if (mounted) {
        setState(() {
          _streaming = false;
          _future = _loadDetail();
        });
      }
    }();
  }

  Future<void> _refresh() async {
    setState(() => _future = _loadDetail());
    try {
      await _future;
    } catch (_) {/* 错误由 FutureBuilder 呈现 */}
  }

  // —— 决策/处置行为 ——

  Future<void> _approve() => _runReview(() => _client.review(widget.taskId, decision: 'approved'));

  /// 通用打回:带可选语音/文字意见。沈括不走这条(用 _discard)。
  Future<void> _reject(String note) =>
      _runReview(() => _client.review(widget.taskId, decision: 'rejected', note: note));

  /// 沈括专用:弃用素材(rejected,不需要意见备注)。
  Future<void> _discard() => _runReview(() => _client.review(widget.taskId, decision: 'rejected'));

  /// 撤销决策 / 沈括拉回待验收。
  Future<void> _revoke() => _runReview(() => _client.revokeReview(widget.taskId));

  Future<void> _cancel() => _runReview(() => _client.cancelTask(widget.taskId));

  /// 包装决策类请求:置位 → 调用 → 重载详情 → 收起改判;失败回填 errorText。
  Future<void> _runReview(Future<void> Function() action) async {
    setState(() {
      _reviewing = true;
      _reviewError = null;
    });
    try {
      await action();
      if (!mounted) return;
      setState(() {
        _rejudging = false;
        _future = _loadDetail();
      });
    } catch (e) {
      if (mounted) setState(() => _reviewError = '$e');
    } finally {
      if (mounted) setState(() => _reviewing = false);
    }
  }

  /// 失败重投 / 已取消恢复:成功后退回上一页(任务回到运行中)。
  Future<void> _resubmit(Future<void> Function() action) async {
    setState(() {
      _reviewing = true;
      _reviewError = null;
    });
    try {
      await action();
      if (mounted) Navigator.of(context).pop();
    } catch (e) {
      if (mounted) {
        setState(() {
          _reviewError = '$e';
          _reviewing = false;
        });
      }
    }
  }

  Future<void> _retry(TaskDetail d) =>
      _resubmit(() => _client.createTask(cmd: d.cmd, params: d.params ?? const <String, dynamic>{}));

  Future<void> _restore() => _resubmit(() => _client.restoreTask(widget.taskId));

  /// 打回:语音口述意见(对齐 iOS RejectVoiceOverlay)→ 提交 rejected。沈括不走这条(用 _discard)。
  Future<void> _promptReject() async {
    final note = await showRejectVoiceSheet(context, client: _client);
    if (note == null) return; // 取消
    await _reject(note);
  }

  @override
  Widget build(BuildContext context) {
    final double topInset = MediaQuery.of(context).padding.top + _heroBarHeight;
    return Scaffold(
      // 固定 hero 磨砂头:头像 + 标题 + 灯态/数据钉在顶部磨砂条,内容从下方滚过。
      extendBodyBehindAppBar: true,
      appBar: AppBar(
        backgroundColor: Colors.transparent,
        foregroundColor: AppColors.ink,
        elevation: 0,
        scrolledUnderElevation: 0,
        surfaceTintColor: Colors.transparent,
        toolbarHeight: _heroBarHeight,
        flexibleSpace: const FrostedHeader(),
        centerTitle: false,
        titleSpacing: 0,
        title: _heroBar(),
      ),
      backgroundColor: AppColors.sand,
      body: RefreshIndicator(
        onRefresh: _refresh,
        edgeOffset: topInset,
        child: FutureBuilder<TaskDetail>(
          future: _future,
          builder: (context, snap) {
            if (_future == null || snap.connectionState == ConnectionState.waiting) {
              return _centered(const CircularProgressIndicator());
            }
            if (snap.hasError) {
              return _centered(_ErrorState(message: '${snap.error}', onRetry: _refresh));
            }
            final detail = snap.data;
            if (detail == null) {
              return _centered(Text('没有这条任务', style: AppTypography.body.copyWith(color: AppColors.inkMuted)));
            }
            return _content(detail);
          },
        ),
      ),
    );
  }

  Widget _content(TaskDetail detail) {
    final agent = _agentFor(detail.cmd);
    final shenkuo = detail.shenkuo;
    final guiguzi = detail.guiguzi;
    final wudaozi = detail.wudaozi;
    final boya = detail.boya;
    final wolong = detail.wolong;
    final light = _lightFor(detail, agent);
    final live = _isLive(detail.status);
    final artifacts = (detail.artifacts ?? const <NofArtifact>[])
        .where((a) => _reviewableKinds.contains(a.kind))
        .toList();

    final double topInset = MediaQuery.of(context).padding.top + _heroBarHeight;
    return ListView(
      physics: const AlwaysScrollableScrollPhysics(),
      padding: EdgeInsets.fromLTRB(
        AppSpacing.pageH, topInset + AppSpacing.m, AppSpacing.pageH, AppSpacing.pageBottom,
      ),
      children: [
        // hero(头像/标题/灯态/数据)已钉在顶部磨砂条,内容从成果面板开始。
        // 各 agent 专属成果面板(同一任务只命中一个;其余降级为通用骨架)。
        if (shenkuo != null && agent != null) ...[
          ShenkuoCollectPanel(result: shenkuo, agent: agent, client: _client, light: light),
          const SizedBox(height: AppSpacing.block),
        ],
        if (guiguzi != null && agent != null) ...[
          GuiguziTopicsPanel(result: guiguzi, agent: agent, client: _client),
          const SizedBox(height: AppSpacing.block),
        ],
        if (wudaozi != null && agent != null) ...[
          // 配图缩略基址:dir 产物 url 换成文件服务前缀后解析绝对地址。
          _ResolvedUrl(
            client: _client,
            relativeUrl: detail.figureDirFilesUrl,
            builder: (base) => WudaoziStoryboardPanel(result: wudaozi, agent: agent, figureBase: base),
          ),
          const SizedBox(height: AppSpacing.block),
        ],
        if (boya != null && agent != null) ...[
          // master 可播放地址:产物清单 kind=audio 那条解析绝对地址。
          _ResolvedUrl(
            client: _client,
            relativeUrl: detail.audioArtifactUrl,
            builder: (url) => BoyaSoundPanel(result: boya, agent: agent, masterUrl: url),
          ),
          const SizedBox(height: AppSpacing.block),
        ],
        if (wolong != null && agent != null) ...[
          WolongReportPanel(result: wolong, agent: agent),
          const SizedBox(height: AppSpacing.block),
        ],

        // 产物审看(只展示可审成品)。
        if (artifacts.isNotEmpty) ...[
          _SectionTitle('产物 · 审看'),
          const SizedBox(height: AppSpacing.m),
          for (final art in artifacts) ...[
            ArtifactViewer(client: _client, artifact: art),
            const SizedBox(height: AppSpacing.s),
          ],
          const SizedBox(height: AppSpacing.block - AppSpacing.s),
        ],

        // 决策 / 已决策 / 失败 / 取消处置。
        ..._reviewArea(detail, agent),

        // 进度日志(完成态不展示工程噪音;其余按需)。
        if (_showProgress(detail, live)) _progressSection(detail, live),
      ],
    );
  }

  AgentInfo? _agentFor(String cmd) {
    for (final a in agentCatalog) {
      if (a.cmd == cmd) return a;
    }
    return null;
  }

  bool _isShenkuo(TaskDetail d) => d.cmd == 'shenkuo';

  /// 灯态 + 状态词(对齐 iOS lightStatus / phaseWord)。
  TaskLight _lightFor(TaskDetail d, AgentInfo? agent) {
    final review = d.review;
    if (review != null) {
      final approved = review.decision == 'approved';
      final word = _isShenkuo(d)
          ? (approved ? '已通过' : '已弃用')
          : (approved ? '已验收' : '已打回');
      return TaskLight.idle.withWord(word);
    }
    switch (d.status) {
      case 'running':
        return TaskLight.working.withWord('工作中…');
      case 'queued':
      case 'pending':
        return TaskLight.working.withWord('排队中 · 尚未开始');
      case 'failed':
      case 'error':
        return TaskLight.asking.withWord('已失败 · 待处理');
      case 'completed':
      case 'done':
        return TaskLight.asking.withWord('待验收');
      case 'cancelled':
        return TaskLight.idle.withWord('已取消 · 可恢复');
      default:
        return TaskLight.idle.withWord(agent != null ? '${agent.role}…' : '');
    }
  }

  // —— 决策区:依据 status / review 分支(沈括措辞另走) ——

  List<Widget> _reviewArea(TaskDetail detail, AgentInfo? agent) {
    final review = detail.review;
    final status = detail.status;
    final shenkuo = _isShenkuo(detail);
    final accent = agent?.accent ?? AppColors.orange;
    final out = <Widget>[];

    // 失败:无产出可审。round 任务重投会丢溯源,失败处置归卧龙。
    if (review == null && (status == 'failed' || status == 'error')) {
      out.add(_FailedCard(
        error: detail.error,
        roundOrphan: detail.roundId != null,
        reviewing: _reviewing,
        errorText: _reviewError,
        accent: accent,
        onRetry: () => _retry(detail),
      ));
      out.add(const SizedBox(height: AppSpacing.block));
      return out;
    }

    // 待验收:终态完成且未决策。
    if (review == null && (status == 'completed' || status == 'done')) {
      out.add(_DecisionCard(
        title: '决策',
        shenkuo: shenkuo,
        reviewing: _reviewing,
        errorText: _reviewError,
        onApprove: _approve,
        onReject: shenkuo ? _discard : _promptReject,
      ));
      out.add(const SizedBox(height: AppSpacing.block));
      return out;
    }

    // 已决策:结果徽标 + 改判 + (沈括)拉回待验收 / (其余)撤销。
    if (review != null) {
      out.add(_DecidedCard(
        review: review,
        shenkuo: shenkuo,
        accent: accent,
        decisionFinalized: detail.decisionFinalized,
        rejudging: _rejudging,
        reviewing: _reviewing,
        errorText: _reviewError,
        onRevoke: _revoke,
        onRejudgeToggle: () => setState(() => _rejudging = !_rejudging),
        onApprove: _approve,
        onReject: shenkuo ? _discard : _promptReject,
      ));
      out.add(const SizedBox(height: AppSpacing.block));
      return out;
    }

    // 已取消:归档 + 一键恢复重新入队。
    if (status == 'cancelled') {
      out.add(_RestoreCard(
        reviewing: _reviewing,
        errorText: _reviewError,
        accent: accent,
        onRestore: _restore,
      ));
      out.add(const SizedBox(height: AppSpacing.block));
    }

    return out;
  }

  /// 进度区可见性:运行中始终显示(含取消入口);否则仅在有流错误或日志、且未到完成终态时显示。
  bool _showProgress(TaskDetail detail, bool live) {
    if (live) return true;
    final completed = detail.status == 'completed' || detail.status == 'done';
    if (completed) return false;
    return _streamError != null || _logs.isNotEmpty;
  }

  bool _cancellable(String status) =>
      status == 'pending' || status == 'running' || status == 'queued';

  Widget _progressSection(TaskDetail detail, bool live) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            _SectionTitle('工作进度'),
            const SizedBox(width: AppSpacing.s),
            if (live) const _LiveDot(),
            const Spacer(),
            if (_cancellable(detail.status))
              _CancelChip(onTap: _reviewing ? null : _cancel),
          ],
        ),
        const SizedBox(height: AppSpacing.m),
        AppCard(
          dark: true,
          child: _ProgressLog(logs: _logs, streaming: _streaming, streamError: _streamError),
        ),
      ],
    );
  }

  /// 单元素可滚动容器,保证加载/错误/空态下 RefreshIndicator 仍可下拉。
  Widget _centered(Widget child) => ListView(
        physics: const AlwaysScrollableScrollPhysics(),
        children: [
          Padding(
            padding: EdgeInsets.only(top: MediaQuery.of(context).padding.top + _heroBarHeight + 48),
            child: Center(child: child),
          ),
        ],
      );

  // —— 固定 hero 磨砂头:头像 + 标题 + 灯态/沈括数据(钉在 AppBar 磨砂条里) ——
  Widget _heroBar() {
    if (_navAgent == null && _navTitle.isEmpty) return const SizedBox.shrink();
    return Row(
      children: [
        if (_navAgent != null) ...[
          _HeroAvatar(agent: _navAgent!, size: 38),
          const SizedBox(width: 10),
        ],
        Expanded(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(_navTitle, style: AppTypography.titleM, maxLines: 1, overflow: TextOverflow.ellipsis),
              const SizedBox(height: 2),
              _navSubtitle(),
            ],
          ),
        ),
        const SizedBox(width: AppSpacing.pageH),
      ],
    );
  }

  Widget _navSubtitle() {
    // 沈括单条:赞评转藏数据;其余:灯 + 状态词。
    if (_navStats != null) return ShenkuoStatsLine(entry: _navStats!);
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        TaskBulb(light: _navLight, diameter: 9),
        if (_navLight.word.isNotEmpty) ...[
          const SizedBox(width: 6),
          Flexible(
            child: Text(_navLight.word,
                style: AppTypography.caption.copyWith(color: AppColors.inkMuted),
                maxLines: 1,
                overflow: TextOverflow.ellipsis),
          ),
        ],
      ],
    );
  }
}

// —— 产物相对路径 → 绝对地址解析后再渲染面板(伯牙 master / 吴道子配图基址) ——

class _ResolvedUrl extends StatelessWidget {
  const _ResolvedUrl({required this.client, required this.relativeUrl, required this.builder});

  final FactoryClient client;
  final String? relativeUrl;
  final Widget Function(String? absoluteUrl) builder;

  @override
  Widget build(BuildContext context) {
    if (relativeUrl == null || relativeUrl!.isEmpty) return builder(null);
    return FutureBuilder<Uri?>(
      future: client.absoluteUrl(relativeUrl!),
      builder: (context, snap) => builder(snap.data?.toString()),
    );
  }
}

/// 圆形渐变字号头像(对齐首页 AgentAvatar,姓字白色粗体)。
class _HeroAvatar extends StatelessWidget {
  const _HeroAvatar({required this.agent, required this.size});

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

class _SectionTitle extends StatelessWidget {
  const _SectionTitle(this.text);
  final String text;

  @override
  Widget build(BuildContext context) => Text(text, style: AppTypography.titleM);
}

/// 运行中的呼吸小圆点(给「工作进度」标题作活动指示)。
class _LiveDot extends StatefulWidget {
  const _LiveDot();

  @override
  State<_LiveDot> createState() => _LiveDotState();
}

class _LiveDotState extends State<_LiveDot> with SingleTickerProviderStateMixin {
  late final AnimationController _controller = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 1100),
  )..repeat(reverse: true);

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
        final t = Curves.easeInOut.transform(_controller.value);
        return Container(
          width: 8,
          height: 8,
          decoration: BoxDecoration(
            shape: BoxShape.circle,
            color: AppColors.amber.withValues(alpha: 0.5 + 0.5 * t),
          ),
        );
      },
    );
  }
}

/// 取消任务胶囊按钮(进度区右上)。
class _CancelChip extends StatelessWidget {
  const _CancelChip({required this.onTap});
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
        decoration: ShapeDecoration(
          color: AppColors.statusRed.withValues(alpha: 0.12),
          shape: const StadiumBorder(),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.block, size: 13, color: AppColors.statusRed),
            const SizedBox(width: 4),
            Text('取消任务',
                style: AppTypography.caption.copyWith(
                    fontWeight: FontWeight.w600, color: AppColors.statusRed)),
          ],
        ),
      ),
    );
  }
}

// —— 待验收决策卡(首判与改判共用决策按钮) ——

class _DecisionButtons extends StatelessWidget {
  const _DecisionButtons({
    required this.shenkuo,
    required this.reviewing,
    required this.onApprove,
    required this.onReject,
  });

  final bool shenkuo;
  final bool reviewing;
  final VoidCallback onApprove;
  final VoidCallback onReject;

  @override
  Widget build(BuildContext context) {
    // 沈括是素材采集:没有「打回重做」,只有 弃用/通过;其余 agent 打回/验收通过。
    return Row(
      children: [
        Expanded(
          child: DecisionButton(
            label: shenkuo ? '弃用' : '打回',
            // 沈括 archivebox / 其余 arrow.uturn.backward(对齐 iOS Label)。
            icon: shenkuo ? Icons.archive_outlined : Icons.undo,
            filled: false,
            tint: shenkuo ? AppColors.inkMuted : AppColors.statusRed,
            onPressed: reviewing ? () {} : onReject,
          ),
        ),
        const SizedBox(width: AppSpacing.m),
        Expanded(
          child: DecisionButton(
            label: shenkuo ? '通过' : '验收通过',
            icon: Icons.verified, // checkmark.seal.fill
            tint: AppColors.statusAdopted,
            onPressed: reviewing ? () {} : onApprove,
          ),
        ),
      ],
    );
  }
}

class _DecisionCard extends StatelessWidget {
  const _DecisionCard({
    required this.title,
    required this.shenkuo,
    required this.reviewing,
    required this.errorText,
    required this.onApprove,
    required this.onReject,
  });

  final String title;
  final bool shenkuo;
  final bool reviewing;
  final String? errorText;
  final VoidCallback onApprove;
  final VoidCallback onReject;

  @override
  Widget build(BuildContext context) {
    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(title, style: AppTypography.titleM),
          const SizedBox(height: AppSpacing.m),
          _DecisionButtons(
            shenkuo: shenkuo,
            reviewing: reviewing,
            onApprove: onApprove,
            onReject: onReject,
          ),
          if (errorText != null) ...[
            const SizedBox(height: AppSpacing.s),
            Text(errorText!, style: AppTypography.caption.copyWith(color: AppColors.statusRed)),
          ],
        ],
      ),
    );
  }
}

// —— 已决策卡 ——

class _DecidedCard extends StatelessWidget {
  const _DecidedCard({
    required this.review,
    required this.shenkuo,
    required this.accent,
    required this.decisionFinalized,
    required this.rejudging,
    required this.reviewing,
    required this.errorText,
    required this.onRevoke,
    required this.onRejudgeToggle,
    required this.onApprove,
    required this.onReject,
  });

  final NofReview review;
  final bool shenkuo;
  final Color accent;
  final bool decisionFinalized;
  final bool rejudging;
  final bool reviewing;
  final String? errorText;
  final VoidCallback onRevoke;
  final VoidCallback onRejudgeToggle;
  final VoidCallback onApprove;
  final VoidCallback onReject;

  @override
  Widget build(BuildContext context) {
    final approved = review.decision == 'approved';
    final color = approved
        ? AppColors.statusAdopted
        : (shenkuo ? AppColors.inkMuted : AppColors.statusRed);
    final label = approved ? (shenkuo ? '已通过' : '已验收') : (shenkuo ? '已弃用' : '已打回');
    final icon = approved
        ? Icons.verified_outlined
        : (shenkuo ? Icons.archive_outlined : Icons.undo_outlined);

    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Icon(icon, size: 20, color: color),
              const SizedBox(width: AppSpacing.s),
              Expanded(child: Text(label, style: AppTypography.subhead.copyWith(color: color))),
              if (!rejudging)
                TextButton(
                  onPressed: reviewing ? null : onRejudgeToggle,
                  child: Text('改判',
                      style: AppTypography.label.copyWith(
                          color: reviewing ? AppColors.inkFaint : AppColors.orange)),
                ),
            ],
          ),
          if (review.note != null && review.note!.isNotEmpty) ...[
            const SizedBox(height: AppSpacing.xs),
            Text(review.note!, style: AppTypography.caption.copyWith(color: AppColors.inkMuted)),
          ],
          if (review.reviewedAt != null && review.reviewedAt!.isNotEmpty) ...[
            const SizedBox(height: AppSpacing.xs),
            Text(review.reviewedAt!, style: AppTypography.monoXS.copyWith(color: AppColors.inkFaint)),
          ],

          // 沈括:弃用提示 + 拉回待验收;其余:撤销决策。
          if (shenkuo) ...[
            if (!approved) ...[
              const SizedBox(height: AppSpacing.xs),
              Text('弃用素材超过保留期后由服务端自动清除',
                  style: AppTypography.caption2.copyWith(color: AppColors.inkFaint)),
            ],
            const SizedBox(height: AppSpacing.s),
            _PullBackChip(accent: accent, onTap: reviewing ? null : onRevoke),
          ] else ...[
            const SizedBox(height: AppSpacing.xs),
            Align(
              alignment: Alignment.centerLeft,
              child: TextButton(
                onPressed: reviewing ? null : onRevoke,
                style: TextButton.styleFrom(padding: EdgeInsets.zero, minimumSize: Size.zero),
                child: Text('撤销决策',
                    style: AppTypography.label.copyWith(
                        color: reviewing ? AppColors.inkFaint : AppColors.orange)),
              ),
            ),
          ],

          // 改判区:重开决策按钮(覆盖标注,不回卷流程)。
          if (rejudging) ...[
            const Divider(height: AppSpacing.block),
            Row(
              children: [
                Text('改判', style: AppTypography.subhead),
                const Spacer(),
                TextButton(
                  onPressed: reviewing ? null : onRejudgeToggle,
                  child: Text('收起',
                      style: AppTypography.label.copyWith(color: AppColors.inkMuted)),
                ),
              ],
            ),
            if (decisionFinalized)
              Padding(
                padding: const EdgeInsets.only(bottom: AppSpacing.s),
                child: Text('已定案:改判只影响标注,不回卷流程',
                    style: AppTypography.caption.copyWith(color: AppColors.inkMuted)),
              ),
            _DecisionButtons(
              shenkuo: shenkuo,
              reviewing: reviewing,
              onApprove: onApprove,
              onReject: onReject,
            ),
          ],

          if (errorText != null) ...[
            const SizedBox(height: AppSpacing.s),
            Text(errorText!, style: AppTypography.caption.copyWith(color: AppColors.statusRed)),
          ],
        ],
      ),
    );
  }
}

/// 沈括拉回待验收胶囊。
class _PullBackChip extends StatelessWidget {
  const _PullBackChip({required this.accent, required this.onTap});
  final Color accent;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
        decoration: ShapeDecoration(color: accent.withValues(alpha: 0.14), shape: const StadiumBorder()),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.unarchive_outlined, size: 13, color: accent),
            const SizedBox(width: 4),
            Text('拉回待验收',
                style: AppTypography.caption.copyWith(fontWeight: FontWeight.w600, color: accent)),
          ],
        ),
      ),
    );
  }
}

// —— 失败卡 ——

class _FailedCard extends StatelessWidget {
  const _FailedCard({
    required this.error,
    required this.roundOrphan,
    required this.reviewing,
    required this.errorText,
    required this.accent,
    required this.onRetry,
  });

  final String? error;
  final bool roundOrphan; // round 任务重投会丢溯源链,失败处置归卧龙
  final bool reviewing;
  final String? errorText;
  final Color accent;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(Icons.error_outline, size: 20, color: AppColors.statusRed),
              const SizedBox(width: AppSpacing.s),
              Text('这条没跑成', style: AppTypography.titleM.copyWith(color: AppColors.statusRed)),
            ],
          ),
          if (error != null && error!.isNotEmpty) ...[
            const SizedBox(height: AppSpacing.s),
            Text(error!, style: AppTypography.caption.copyWith(color: AppColors.inkMuted)),
          ],
          const SizedBox(height: AppSpacing.m),
          if (roundOrphan)
            Text('卧龙会自动安排返工或止损',
                style: AppTypography.caption.copyWith(color: AppColors.inkMuted))
          else
            DecisionButton(
              label: '重新发起',
              icon: Icons.refresh, // arrow.clockwise
              tint: accent,
              onPressed: reviewing ? () {} : onRetry,
            ),
          if (errorText != null) ...[
            const SizedBox(height: AppSpacing.s),
            Text(errorText!, style: AppTypography.caption.copyWith(color: AppColors.statusRed)),
          ],
        ],
      ),
    );
  }
}

// —— 已取消恢复卡 ——

class _RestoreCard extends StatelessWidget {
  const _RestoreCard({
    required this.reviewing,
    required this.errorText,
    required this.accent,
    required this.onRestore,
  });

  final bool reviewing;
  final String? errorText;
  final Color accent;
  final VoidCallback onRestore;

  @override
  Widget build(BuildContext context) {
    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(Icons.do_not_disturb_on_outlined, size: 20, color: AppColors.inkMuted),
              const SizedBox(width: AppSpacing.s),
              Text('任务已取消', style: AppTypography.subhead.copyWith(color: AppColors.inkMuted)),
            ],
          ),
          const SizedBox(height: AppSpacing.xs),
          Text('已归入归档;可恢复并沿用原参数重新入队',
              style: AppTypography.caption.copyWith(color: AppColors.inkFaint)),
          const SizedBox(height: AppSpacing.m),
          DecisionButton(
            label: '恢复并重新入队',
            icon: Icons.restart_alt, // arrow.clockwise.circle
            tint: accent,
            onPressed: reviewing ? () {} : onRestore,
          ),
          if (errorText != null) ...[
            const SizedBox(height: AppSpacing.s),
            Text(errorText!, style: AppTypography.caption.copyWith(color: AppColors.statusRed)),
          ],
        ],
      ),
    );
  }
}

// —— 进度日志(等宽 mono,深底卡内) ——

class _ProgressLog extends StatelessWidget {
  const _ProgressLog({required this.logs, required this.streaming, required this.streamError});

  final List<String> logs;
  final bool streaming;
  final String? streamError;

  @override
  Widget build(BuildContext context) {
    final mono = AppTypography.monoS.copyWith(color: AppColors.ivory.withValues(alpha: 0.85));
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (logs.isEmpty && streaming)
          Row(
            children: [
              SizedBox(
                width: 14,
                height: 14,
                child: CircularProgressIndicator(
                  strokeWidth: 2,
                  valueColor: AlwaysStoppedAnimation<Color>(AppColors.amber),
                ),
              ),
              const SizedBox(width: AppSpacing.s),
              Text('等待 agent 输出…', style: mono.copyWith(color: AppColors.ivory.withValues(alpha: 0.6))),
            ],
          ),
        if (logs.isEmpty && !streaming && streamError == null)
          Text('暂无进度', style: mono.copyWith(color: AppColors.ivory.withValues(alpha: 0.6))),
        for (final line in logs)
          Padding(
            padding: const EdgeInsets.only(bottom: AppSpacing.xs),
            child: SelectableText(line, style: mono),
          ),
        if (streamError != null) ...[
          if (logs.isNotEmpty) const SizedBox(height: AppSpacing.xs),
          Text(streamError!, style: mono.copyWith(color: AppColors.statusRed)),
        ],
      ],
    );
  }
}

// —— 错误态 ——

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

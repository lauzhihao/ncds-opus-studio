// 柳永单任务详情 —— 复刻 iOS LiuyongTaskDetailView。
// SSE 进度(运行中实时 / 终态回放)→ 双稿对比 → 质检报告 → 采用 / 打回(打回带意见,当新要求重投)。
// 与通用详情不同:柳永是整页定制,故单独成屏(AgentTaskListScreen 对 cmd=liuyong 路由到这里)。
// 注:iOS 的打回遮罩是「语音口述」;Flutter 暂无 speech_to_text 依赖,这里先做「文字 + 快捷标签」版。

import 'dart:async';

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
import 'detail_widgets.dart';
import 'reject_voice_sheet.dart';
import 'shenkuo_collect_panel.dart' show TaskLight, TaskBulb;

const List<String> _rubricDims = ['节奏', '真实性', '精炼度', '直接性', '信任度'];

class LiuyongTaskDetailScreen extends StatefulWidget {
  const LiuyongTaskDetailScreen({
    super.key,
    required this.taskId,
    required this.agent,
    required this.topic,
    this.client,
  });

  final String taskId;
  final AgentInfo agent;
  final String topic;
  final FactoryClient? client;

  @override
  State<LiuyongTaskDetailScreen> createState() => _LiuyongTaskDetailScreenState();
}

class _LiuyongTaskDetailScreenState extends State<LiuyongTaskDetailScreen> {
  static const String _devBase = 'http://liuzhihao-mbp.local:8810';

  late final FactoryClient _client =
      widget.client ?? FactoryClient(resolver: DirectEndpointResolver(_devBase));

  static const double _heroBarHeight = 60;
  final List<String> _logs = <String>[];
  TaskDetail? _detail;
  bool _streamEnded = false;
  bool _streaming = false;
  int _selected = 0;
  bool _reviewing = false;
  String? _errText;
  String? _pendingRejectNote; // 打回失败暂存口述意见,重试不必重输
  bool _resubmitted = false;
  bool _rejudging = false;

  @override
  void initState() {
    super.initState();
    // 首拉延到 push 转场结束,避免数据在右滑动画中途到达触发重建、肉眼抖动。
    WidgetsBinding.instance.addPostFrameCallback((_) => _kickoff());
  }

  void _kickoff() {
    if (!mounted) return;
    final Animation<double>? anim = ModalRoute.of(context)?.animation;
    if (anim == null || anim.isCompleted) {
      _load();
    } else {
      void listener(AnimationStatus status) {
        if (status == AnimationStatus.completed) {
          anim.removeStatusListener(listener);
          if (mounted) _load();
        }
      }
      anim.addStatusListener(listener);
    }
  }

  List<LiuyongDraft> get _drafts => _detail?.liuyong?.drafts ?? const <LiuyongDraft>[];
  LiuyongDraft? get _current =>
      (_selected >= 0 && _selected < _drafts.length) ? _drafts[_selected] : null;
  bool get _terminal => _detail?.status == 'completed' || _detail?.status == 'failed';

  bool _isLive(String? s) => s == 'running' || s == 'pending' || s == 'queued';

  Future<void> _load() async {
    try {
      final d = await _client.task(widget.taskId);
      if (!mounted) return;
      setState(() => _detail = d);
      if (_isLive(d.status) && !_streaming) _startStream();
    } catch (e) {
      if (isCancellation(e)) return;
      if (mounted) setState(() => _errText = '$e');
    }
  }

  void _startStream() {
    _streaming = true;
    () async {
      try {
        await for (final ev in _client.events(widget.taskId)) {
          if (!mounted) return;
          if (ev.type == 'progress' && ev.text != null && ev.text!.isNotEmpty) {
            setState(() => _logs.add(ev.text!));
          } else if (ev.type == 'error') {
            setState(() => _errText = ev.error);
          }
        }
      } catch (e) {
        if (!isCancellation(e) && mounted) setState(() => _errText = '$e');
      }
      if (mounted) {
        setState(() {
          _streaming = false;
          _streamEnded = true;
        });
        await _load(); // 收流拿终态(drafts/review)
      }
    }();
  }

  // —— 灯态 / 状态词(对齐 iOS lightStatus / phaseWord)——
  TaskLight get _light {
    final d = _detail;
    if (d == null) return TaskLight.working.withWord(_streamEnded ? '处理中' : '创作中…');
    final review = d.review;
    if (review != null) {
      return TaskLight.idle.withWord(review.decision == 'approved' ? '已采用' : '已打回');
    }
    switch (d.status) {
      case 'running':
        return TaskLight.working.withWord('创作中…');
      case 'pending':
      case 'queued':
        return TaskLight.working.withWord('排队中 · 尚未开始创作');
      case 'failed':
        return TaskLight.asking.withWord('已失败 · 待处理');
      case 'completed':
        return TaskLight.asking.withWord('待验收');
      case 'cancelled':
        return TaskLight.idle.withWord('已取消 · 可恢复');
      default:
        return TaskLight.idle.withWord('');
    }
  }

  bool get _cancellable {
    final d = _detail;
    if (d == null) return !_streamEnded;
    return d.status == 'pending' || d.status == 'running' || d.status == 'queued';
  }

  @override
  Widget build(BuildContext context) {
    final detail = _detail;
    final drafts = _drafts;
    final review = detail?.review;
    final double topInset = MediaQuery.of(context).padding.top + _heroBarHeight;
    return Scaffold(
      backgroundColor: AppColors.sand,
      // 固定 hero 磨砂头:头像 + 选题 + 灯态钉在顶部磨砂条,双稿/质检从下方滚过。
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
      body: ListView(
        padding: EdgeInsets.fromLTRB(
            AppSpacing.pageH, topInset + AppSpacing.xs, AppSpacing.pageH, AppSpacing.pageBottom),
        children: [
          if (drafts.isNotEmpty) ...[
            const SizedBox(height: AppSpacing.cardInner),
            _draftsSection(),
          ],
          if (_current != null) ...[
            const SizedBox(height: AppSpacing.cardInner),
            _QCReport(draft: _current!),
          ],
          if (_terminal && review == null && !_resubmitted) ...[
            const SizedBox(height: AppSpacing.cardInner),
            drafts.isEmpty ? _failedCard() : _decisionCard(),
          ],
          if (review != null) ...[
            const SizedBox(height: AppSpacing.cardInner),
            _decidedBadge(review),
          ],
          if (detail?.status == 'cancelled') ...[
            const SizedBox(height: AppSpacing.cardInner),
            _restoreCard(),
          ],
          if (detail?.status != 'completed') ...[
            const SizedBox(height: AppSpacing.cardInner),
            _progressSection(),
          ],
        ],
      ),
    );
  }

  // —— 固定 hero 磨砂头:头像 + 选题 + 灯态(钉在 AppBar 磨砂条里) ——
  Widget _heroBar() {
    final light = _light;
    return Row(
      children: [
        _Avatar(agent: widget.agent, size: 38),
        const SizedBox(width: 10),
        Expanded(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(widget.topic, style: AppTypography.titleM, maxLines: 1, overflow: TextOverflow.ellipsis),
              const SizedBox(height: 2),
              Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  TaskBulb(light: light, diameter: 9),
                  const SizedBox(width: 6),
                  Flexible(
                    child: Text(light.word,
                        style: AppTypography.caption.copyWith(color: AppColors.inkMuted),
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis),
                  ),
                ],
              ),
            ],
          ),
        ),
        const SizedBox(width: AppSpacing.pageH),
      ],
    );
  }

  // —— 双稿(药丸切换 + 稿件卡)——
  Widget _draftsSection() {
    final drafts = _drafts;
    final d = _current;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (drafts.length > 1)
          Padding(
            padding: const EdgeInsets.only(bottom: 10),
            child: Row(
              children: [
                for (int i = 0; i < drafts.length; i++) ...[
                  if (i > 0) const SizedBox(width: 8),
                  _DraftPill(
                    label: drafts[i].model ?? '稿${i + 1}',
                    selected: _selected == i,
                    accent: widget.agent.accent,
                    onTap: () => setState(() => _selected = i),
                  ),
                ],
              ],
            ),
          ),
        if (d != null) _draftCard(d),
      ],
    );
  }

  /// 稿件卡:质检标签居左(已去掉模型名——上方药丸已显示);正文 + 复制。
  Widget _draftCard(LiuyongDraft d) {
    final badges =
        [_verdictBadge(d.qc?.verdict), _rubricChip(d.qcRubric)].whereType<Widget>().toList();
    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (badges.isNotEmpty) ...[
            Row(
              children: [
                for (int i = 0; i < badges.length; i++) ...[
                  if (i > 0) const SizedBox(width: AppSpacing.s),
                  badges[i],
                ],
              ],
            ),
            const SizedBox(height: AppSpacing.s),
            Divider(color: AppColors.ink.withValues(alpha: 0.08), height: 1),
            const SizedBox(height: AppSpacing.s),
          ],
          SelectableText(d.text ?? '(空稿)', style: AppTypography.bodySerif.copyWith(height: 1.6)),
          const SizedBox(height: AppSpacing.s),
          Divider(color: AppColors.ink.withValues(alpha: 0.08), height: 1),
          const SizedBox(height: AppSpacing.s),
          ContentToolbelt(text: d.text ?? '', accent: widget.agent.accent),
        ],
      ),
    );
  }

  Widget? _verdictBadge(String? v) {
    if (v == null) return null;
    final pass = v == 'pass';
    final c = pass ? AppColors.statusAdopted : AppColors.statusRed;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
      decoration: ShapeDecoration(color: c.withValues(alpha: 0.16), shape: const StadiumBorder()),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(pass ? Icons.verified : Icons.warning_rounded, size: 13, color: c),
          const SizedBox(width: 4),
          Text(pass ? 'AI味 通过' : 'AI味 打回',
              style: AppTypography.caption.copyWith(fontWeight: FontWeight.w600, color: c)),
        ],
      ),
    );
  }

  Widget? _rubricChip(RubricQC? r) {
    if (r == null || r.available != true || r.total == null) return null;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 4),
      decoration: ShapeDecoration(
          color: AppColors.orange.withValues(alpha: 0.16), shape: const StadiumBorder()),
      child: Text('${r.total}/50 · ${r.grade ?? ''}',
          style: AppTypography.caption.copyWith(fontWeight: FontWeight.w700, color: AppColors.orange)),
    );
  }

  // —— 决策 ——
  Widget _decisionCard() {
    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('决策', style: AppTypography.titleM),
          const SizedBox(height: AppSpacing.m),
          _decisionButtons(isRejudge: false),
          if (_errText != null) ...[
            const SizedBox(height: AppSpacing.s),
            Text(_errText!, style: AppTypography.caption.copyWith(color: AppColors.statusRed)),
          ],
        ],
      ),
    );
  }

  Widget _decisionButtons({required bool isRejudge}) {
    final pending = _pendingRejectNote;
    return Row(
      children: [
        Expanded(
          child: DecisionButton(
            label: pending != null ? '重试打回' : '打回重写',
            icon: pending != null ? Icons.refresh : Icons.undo,
            filled: false,
            tint: AppColors.statusRed,
            onPressed: _reviewing
                ? () {}
                : () {
                    if (pending != null) {
                      _confirmReject(pending, isRejudge: isRejudge);
                    } else {
                      _openRejectSheet(isRejudge: isRejudge);
                    }
                  },
          ),
        ),
        const SizedBox(width: AppSpacing.m),
        Expanded(
          child: DecisionButton(
            label: '采用此稿',
            icon: Icons.verified,
            tint: AppColors.statusAdopted,
            onPressed: _reviewing ? () {} : _approve,
          ),
        ),
      ],
    );
  }

  Widget _decidedBadge(NofReview r) {
    final approved = r.decision == 'approved';
    final c = approved ? AppColors.statusAdopted : AppColors.statusRed;
    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Icon(approved ? Icons.verified : Icons.undo, size: 20, color: c),
              const SizedBox(width: AppSpacing.s),
              Expanded(
                child: Text(approved ? '已采用' : '已打回',
                    style: AppTypography.subhead.copyWith(color: c)),
              ),
              // iOS .bordered:橙色描边填充胶囊,明确是可点按钮(与左侧无边的状态 Label 区分)。
              if (!_rejudging)
                GestureDetector(
                  onTap: _reviewing ? null : () => setState(() => _rejudging = true),
                  child: Container(
                    padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
                    decoration: ShapeDecoration(
                      color: AppColors.orange.withValues(alpha: _reviewing ? 0.06 : 0.14),
                      shape: StadiumBorder(
                          side: BorderSide(
                              color: AppColors.orange.withValues(alpha: _reviewing ? 0.25 : 0.5))),
                    ),
                    child: Text('改判',
                        style: AppTypography.label.copyWith(
                            fontWeight: FontWeight.w600,
                            color: _reviewing ? AppColors.inkFaint : AppColors.orange)),
                  ),
                ),
            ],
          ),
          if (r.note != null && r.note!.isNotEmpty) ...[
            const SizedBox(height: AppSpacing.xs),
            Text(r.note!, style: AppTypography.caption.copyWith(color: AppColors.inkMuted)),
          ],
          if (_rejudging) ...[
            const Divider(height: AppSpacing.block),
            Row(
              children: [
                Text('改判', style: AppTypography.subhead),
                const Spacer(),
                TextButton(
                  onPressed: _reviewing ? null : () => setState(() => _rejudging = false),
                  child: Text('收起', style: AppTypography.label.copyWith(color: AppColors.inkMuted)),
                ),
              ],
            ),
            if (_detail?.decisionFinalized == true)
              Padding(
                padding: const EdgeInsets.only(bottom: AppSpacing.s),
                child: Text('已定案:改判只影响标注,不回卷流程',
                    style: AppTypography.caption.copyWith(color: AppColors.inkMuted)),
              ),
            _decisionButtons(isRejudge: true),
            if (_errText != null) ...[
              const SizedBox(height: AppSpacing.s),
              Text(_errText!, style: AppTypography.caption.copyWith(color: AppColors.statusRed)),
            ],
          ],
        ],
      ),
    );
  }

  Widget _failedCard() {
    final roundOrphan = _detail?.roundId != null;
    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(Icons.warning_rounded, size: 20, color: AppColors.statusRed),
              const SizedBox(width: AppSpacing.s),
              Text('这条没跑成', style: AppTypography.titleM.copyWith(color: AppColors.statusRed)),
            ],
          ),
          if (_detail?.error != null && _detail!.error!.isNotEmpty) ...[
            const SizedBox(height: AppSpacing.s),
            Text(_detail!.error!, style: AppTypography.caption.copyWith(color: AppColors.inkMuted)),
          ],
          const SizedBox(height: AppSpacing.m),
          if (roundOrphan)
            Text('卧龙会自动安排返工或止损',
                style: AppTypography.caption.copyWith(color: AppColors.inkMuted))
          else
            DecisionButton(
              label: '重新发起',
              icon: Icons.refresh,
              tint: widget.agent.accent,
              onPressed: _reviewing ? () {} : _retry,
            ),
          if (_errText != null) ...[
            const SizedBox(height: AppSpacing.s),
            Text(_errText!, style: AppTypography.caption.copyWith(color: AppColors.statusRed)),
          ],
        ],
      ),
    );
  }

  Widget _restoreCard() {
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
            icon: Icons.restart_alt,
            tint: widget.agent.accent,
            onPressed: _reviewing ? () {} : _restore,
          ),
          if (_errText != null) ...[
            const SizedBox(height: AppSpacing.s),
            Text(_errText!, style: AppTypography.caption.copyWith(color: AppColors.statusRed)),
          ],
        ],
      ),
    );
  }

  // —— 进度 ——
  Widget _progressSection() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Text('创作进度', style: AppTypography.titleM),
            const Spacer(),
            if (_cancellable)
              GestureDetector(
                onTap: _reviewing ? null : _cancel,
                child: Container(
                  padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
                  decoration: ShapeDecoration(
                      color: AppColors.statusRed.withValues(alpha: 0.12), shape: const StadiumBorder()),
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
              ),
          ],
        ),
        const SizedBox(height: AppSpacing.m),
        if (_logs.isEmpty && !_streamEnded)
          Row(
            children: [
              const SizedBox(
                  width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2)),
              const SizedBox(width: AppSpacing.s),
              Text('等待${widget.agent.name}输出…',
                  style: AppTypography.caption.copyWith(color: AppColors.inkMuted)),
            ],
          ),
        for (final line in _logs)
          Padding(
            padding: const EdgeInsets.only(bottom: AppSpacing.s),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Icon(_lineIcon(line), size: 13, color: widget.agent.accent),
                const SizedBox(width: AppSpacing.s),
                Expanded(
                  child: Text(line,
                      style: AppTypography.caption.copyWith(color: AppColors.ink.withValues(alpha: 0.8))),
                ),
              ],
            ),
          ),
        if (_errText != null && (_detail?.status == 'failed' || _detail == null))
          Text(_errText!, style: AppTypography.caption.copyWith(color: AppColors.statusRed)),
      ],
    );
  }

  IconData _lineIcon(String line) {
    if (line.contains('打回') || line.contains('重写')) return Icons.sync;
    if (line.contains('质检')) return Icons.verified_user_outlined;
    if (line.contains('完成')) return Icons.flag_outlined;
    if (line.contains('启动')) return Icons.play_circle_outline;
    return Icons.circle;
  }

  // —— 行为 ——
  Future<void> _openRejectSheet({required bool isRejudge}) async {
    final note = await showRejectVoiceSheet(context, client: _client); // 语音口述打回意见(共享弹层)
    if (note == null) return; // 取消
    await _confirmReject(note, isRejudge: isRejudge);
  }

  Future<void> _approve() async {
    setState(() {
      _reviewing = true;
      _errText = null;
    });
    final model = _current?.model ?? '';
    try {
      await _client.review(widget.taskId,
          decision: 'approved', note: '采用 $model 稿', noteOrigin: 'machine');
      await _load();
      if (mounted) setState(() => _rejudging = false);
    } catch (e) {
      if (mounted) setState(() => _errText = '$e');
    } finally {
      if (mounted) setState(() => _reviewing = false);
    }
  }

  Future<void> _retry() async {
    setState(() {
      _reviewing = true;
      _errText = null;
    });
    try {
      await _client.createTask(cmd: widget.agent.cmd, params: {'topic': widget.topic});
      if (mounted) {
        _resubmitted = true;
        Navigator.of(context).pop();
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _errText = '$e';
          _reviewing = false;
        });
      }
    }
  }

  /// 打回:写 review(rejected)。首判且非 round 任务时把意见当新要求重投;改判只改标注不重投。
  Future<void> _confirmReject(String text, {required bool isRejudge}) async {
    setState(() {
      _pendingRejectNote = text;
      _reviewing = true;
      _errText = null;
    });
    try {
      await _client.review(widget.taskId, decision: 'rejected', note: text);
      if (!isRejudge && _detail?.roundId == null) {
        final extra = text.isEmpty ? '' : '【打回意见】$text';
        await _client.createTask(
          cmd: widget.agent.cmd,
          params: {'topic': widget.topic, 'user_requirements': extra},
        );
        _resubmitted = true;
      }
      if (!mounted) return;
      _pendingRejectNote = null;
      if (isRejudge) {
        await _load();
        if (mounted) {
          setState(() {
            _reviewing = false;
            _rejudging = false;
          });
        }
      } else {
        Navigator.of(context).pop();
      }
    } catch (e) {
      // 半程失败(review 已写、重投未发):同步服务器决策态,避免在已打回的稿上误点采用
      await _load();
      if (mounted) {
        setState(() {
          _errText = '$e';
          _reviewing = false;
        });
      }
    }
  }

  Future<void> _cancel() async {
    setState(() {
      _reviewing = true;
      _errText = null;
    });
    try {
      await _client.cancelTask(widget.taskId);
    } catch (e) {
      if (mounted) setState(() => _errText = '$e');
    } finally {
      if (mounted) setState(() => _reviewing = false);
    }
  }

  Future<void> _restore() async {
    setState(() {
      _reviewing = true;
      _errText = null;
    });
    try {
      await _client.restoreTask(widget.taskId);
      if (mounted) Navigator.of(context).pop();
    } catch (e) {
      if (mounted) {
        setState(() {
          _errText = '$e';
          _reviewing = false;
        });
      }
    }
  }
}

// —— 头像(对齐其余页)——
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
              color: agent.accent.withValues(alpha: 0.35), blurRadius: 5, offset: const Offset(0, 3)),
        ],
      ),
      child: Text(agent.surname,
          style: TextStyle(
            fontFamily: AppFonts.sans,
            fontFamilyFallback: AppFonts.sansFallback,
            fontSize: size * 0.46,
            fontWeight: FontWeight.w700,
            color: const Color(0xFFFFFFFF),
          )),
    );
  }
}

class _DraftPill extends StatelessWidget {
  const _DraftPill({required this.label, required this.selected, required this.accent, required this.onTap});
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
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 6),
        decoration: ShapeDecoration(
          color: selected ? accent : AppColors.ivory,
          shape: const StadiumBorder(),
          shadows: selected
              ? [BoxShadow(color: accent.withValues(alpha: 0.3), blurRadius: 4, offset: const Offset(0, 2))]
              : null,
        ),
        child: Text(label,
            style: AppTypography.subhead.copyWith(
                color: selected ? const Color(0xFFFFFFFF) : AppColors.ink.withValues(alpha: 0.6))),
      ),
    );
  }
}

// —— 质检报告(AI 味命中 + rubric 五维)——
class _QCReport extends StatelessWidget {
  const _QCReport({required this.draft});
  final LiuyongDraft draft;

  @override
  Widget build(BuildContext context) {
    return AppCard(
      child: DetailDisclosure(
        icon: Icons.verified_user_outlined,
        label: '质检报告',
        labelStyle: AppTypography.subhead.copyWith(fontWeight: FontWeight.w600),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            _aiTaste(),
            if (draft.qc != null && draft.qcRubric != null) const SizedBox(height: 14),
            _rubric(),
          ],
        ),
      ),
    );
  }

  Widget _aiTaste() {
    final qc = draft.qc;
    if (qc == null) return const SizedBox.shrink();
    final hits = <QCHit>[...(qc.density ?? const []), ...(qc.hard ?? const [])];
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text('AI 味扫描', style: AppTypography.subhead.copyWith(fontWeight: FontWeight.w700)),
        if (qc.summary != null && qc.summary!.isNotEmpty) ...[
          const SizedBox(height: AppSpacing.xs),
          Text(qc.summary!,
              style: AppTypography.body.copyWith(height: 1.4, color: AppColors.inkMuted)),
        ],
        const SizedBox(height: AppSpacing.s),
        if (hits.isEmpty)
          Row(
            children: [
              Icon(Icons.check, size: 16, color: AppColors.statusAdopted),
              const SizedBox(width: 4),
              Text('无命中句式', style: AppTypography.body.copyWith(color: AppColors.statusAdopted)),
            ],
          )
        else
          for (final h in hits)
            Padding(
              padding: const EdgeInsets.only(bottom: AppSpacing.s),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('• ${h.rule ?? '?'} ×${h.count ?? 0}',
                      style: AppTypography.body.copyWith(fontWeight: FontWeight.w500)),
                  if (h.samples != null && h.samples!.isNotEmpty)
                    Text(h.samples!.join(' / '),
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                        style: AppTypography.label.copyWith(color: AppColors.ink.withValues(alpha: 0.5))),
                ],
              ),
            ),
      ],
    );
  }

  Widget _rubric() {
    final r = draft.qcRubric;
    if (r == null) return const SizedBox.shrink();
    if (r.available != true || r.dims == null) {
      return Text('rubric 跳过(${r.skipped ?? '无可用的 judge 模型'})',
          style: AppTypography.body.copyWith(color: AppColors.ink.withValues(alpha: 0.55)));
    }
    final dims = r.dims!;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text('质量评分(${r.judgeModel ?? 'opus'} rubric)', style: AppTypography.subhead.copyWith(fontWeight: FontWeight.w700)),
        const SizedBox(height: AppSpacing.m),
        for (final dim in _rubricDims) ...[
          _dimBar(dim, dims[dim] ?? 0),
          const SizedBox(height: AppSpacing.m),
        ],
        Row(
          children: [
            Text('总分', style: AppTypography.subhead.copyWith(fontWeight: FontWeight.w700)),
            const Spacer(),
            Text('${r.total ?? 0}/50 · ${r.grade ?? ''}',
                style: AppTypography.subhead.copyWith(fontWeight: FontWeight.w700, color: AppColors.orange)),
          ],
        ),
        if (r.issues != null && r.issues!.isNotEmpty)
          for (final s in r.issues!)
            Padding(
              padding: const EdgeInsets.only(top: AppSpacing.xs),
              child: Text('· $s', style: AppTypography.label.copyWith(color: AppColors.inkMuted)),
            ),
        const SizedBox(height: AppSpacing.s),
        Text('校准期,分数仅供参考',
            style: AppTypography.caption.copyWith(color: AppColors.ink.withValues(alpha: 0.4))),
      ],
    );
  }

  Widget _dimBar(String label, int v) {
    final color = v >= 8 ? AppColors.statusAdopted : (v >= 6 ? AppColors.amber : AppColors.statusRed);
    return Row(
      children: [
        SizedBox(width: 64, child: Text(label, style: AppTypography.label)),
        const SizedBox(width: AppSpacing.s),
        Expanded(
          child: ClipRRect(
            borderRadius: BorderRadius.circular(4),
            child: LinearProgressIndicator(
              value: (v / 10).clamp(0.0, 1.0),
              minHeight: 7,
              backgroundColor: AppColors.ink.withValues(alpha: 0.1),
              valueColor: AlwaysStoppedAnimation<Color>(color),
            ),
          ),
        ),
        const SizedBox(width: AppSpacing.s),
        SizedBox(
          width: 24,
          child: Text('$v',
              textAlign: TextAlign.right,
              style: AppTypography.subhead.copyWith(fontWeight: FontWeight.w700, color: color)),
        ),
      ],
    );
  }
}


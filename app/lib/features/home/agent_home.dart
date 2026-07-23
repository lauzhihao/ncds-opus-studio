// 首页 —— 1:1 复刻原生 ContentView.swift 英雄区 + AgentHome.swift 智能体卡片墙。
// 主灯/灵动岛 = 中继 /v1/state 上报的「真实 Claude 状态」(RelayClient,Mac agent 上报);
//   中继不可达时回落到 6 个 agent 灯态聚合(任一 asking→Asking / working→Thinking / 否则 Idle)。
//   agent 卡片墙仍来自工厂后端 /tasks。
//
// 灯色/动效语义对齐实物灯与主灯:
//   Idle    = 绿(statusGreen)常亮      period 1.0
//   Thinking= 琥珀(statusYellow)呼吸   period 3.0  dim 0.45(主灯)/ 0.40(agent 灯)
//   Asking  = 红(statusRed)快闪        period 0.7
//
// 数据:FactoryClient().listTasks() 按 cmd 聚合——含 running→working;
//   含(failed 或 completed&未决)→asking;否则 idle;reviewCount=count(completed&未决)。
//   进前台拉一次 + 30s Timer 轮询;单次失败保留上次灯态(抖动不灭),连续 2 次才灭。

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../core/clipboard/share_link_parser.dart';
import '../../core/net/endpoint_resolver.dart';
import '../../core/net/factory_client.dart';
import '../../core/net/models.dart';
import '../../core/net/relay_client.dart';
import '../../core/native/live_activity_service.dart';
import '../../design/components/decision_button.dart';
import '../../design/components/tag_chip.dart';
import '../../design/components/traffic_bulb.dart';
import '../../design/components/wave_text.dart';
import '../../design/tokens.dart';
import '../../design/typography.dart';
import '../auth/auth_gate.dart';
import '../detail/shenkuo_add_sheet.dart';
import '../detail/shenkuo_collect_panel.dart' show shenkuoCount;
import '../detail/shenkuo_home_screen.dart';
import '../inbox/agent_task_list_screen.dart';
import '../subscriptions/subscriptions_screen.dart';
import 'agent_catalog.dart';

/// 工厂后端 LAN 直连地址(与 TaskListScreen/SubscriptionsScreen 一致)。
const String _devBase = 'http://liuzhihao-mbp.local:8810';

/// agent 当前灯态。idle=绿常亮 / working=琥珀呼吸 / asking=红闪 / offline=灭灰。
enum AgentStatus { offline, idle, working, asking }

/// 派生主灯状态码(对齐原生 state 字符串语义:G/R/Y)。
enum MainState { idle, thinking, asking }

class AgentHome extends StatefulWidget {
  const AgentHome({super.key, this.client});

  /// 测试可注入假客户端;为 null 时走真实 LAN 直连工厂后端。
  final FactoryClient? client;

  @override
  State<AgentHome> createState() => _AgentHomeState();
}

class _AgentHomeState extends State<AgentHome> with WidgetsBindingObserver {
  late final FactoryClient _client =
      widget.client ??
      FactoryClient(resolver: DirectEndpointResolver(_devBase));

  Map<String, AgentStatus> _statusMap = const <String, AgentStatus>{};
  Map<String, int> _reviewCounts = const <String, int>{};
  int _pollFailures = 0; // 连续轮询失败数:单次抖动不灭灯
  bool _booted = false; // 首次加载前主灯走 connecting 占位

  Timer? _timer;

  // 灵动岛/Live Activity:派生主灯态变化时本地 upsert 驱动;非 iOS 自动 no-op。
  final LiveActivityService _liveActivity = MethodChannelLiveActivityService();
  MainState? _lastSynced;

  // 中继上报的真实 Claude 状态(主灯/灵动岛用;与工厂 agent 任务态分开)。
  final RelayClient _relay = RelayClient();
  ClaudeStatus? _relayStatus;

  /// 首页「+」剪贴板命中后的采集预览卡;剪贴板空/无支持链接则改弹手动添加 sheet。
  _ClipboardCollect? _clipCollect;
  bool _clipSubmitting = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _start();
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
    _refreshStatus();
    _timer = Timer.periodic(
      const Duration(seconds: 30),
      (_) => _refreshStatus(),
    );
  }

  /// 拉 /tasks 按 cmd 聚合灯态 + 待验收数;同一份全量数据,不另发请求。
  Future<void> _refreshStatus() async {
    // 中继是远程源(apn.vooice.tech,可能慢/不可达),独立更新、不阻塞主灯就绪——
    // 否则手机连不上中继时,Connecting 会一直挂到它(connect)超时,首屏卡很久。
    _fetchRelay();
    try {
      final List<TaskMeta> tasks = await _client.listTasks();
      final Map<String, AgentStatus> map = <String, AgentStatus>{};
      final Map<String, int> counts = <String, int>{};
      for (final AgentInfo agent in agentCatalog) {
        final List<TaskMeta> mine = tasks
            .where((t) => t.cmd == agent.cmd)
            .toList();
        if (mine.any((t) => t.status == 'running')) {
          map[agent.cmd] = AgentStatus.working;
        } else if (mine.any(
          (t) =>
              t.status == 'failed' ||
              (t.status == 'completed' && t.decision == null),
        )) {
          map[agent.cmd] = AgentStatus.asking;
        } else {
          map[agent.cmd] = AgentStatus.idle;
        }
        counts[agent.cmd] = mine
            .where((t) => t.status == 'completed' && t.decision == null)
            .length;
      }
      if (!mounted) return;
      setState(() {
        _statusMap = map;
        _reviewCounts = counts;
        _pollFailures = 0;
        _booted = true; // 工厂后端(局域网)一返回就退出 Connecting,不等远程中继
      });
      _syncLiveActivity();
    } catch (error) {
      // 取消(轮询重启/退出导航)不算失败;单次抖动保留上次灯态,
      // 连续两次(≥60s)才视为离线灭灯。
      if (isCancellation(error)) return;
      if (!mounted) return;
      setState(() {
        _pollFailures += 1;
        if (_pollFailures >= 2) {
          _statusMap = const <String, AgentStatus>{};
          _reviewCounts = const <String, int>{};
        }
        _booted = true;
      });
      _syncLiveActivity();
    }
  }

  /// 异步拉中继的真实 Claude 状态:拿到就更新主灯/灵动岛,拿不到/超时就静默保留旧态。
  /// 与工厂后端各跑各的,绝不阻塞首屏 Connecting 的退出。
  void _fetchRelay() {
    _relay.fetchState().then((relay) {
      if (relay != null && mounted) {
        setState(() => _relayStatus = relay);
        _syncLiveActivity();
      }
    });
  }

  /// 把派生主灯状态同步给灵动岛(本地 upsert;去抖,仅状态变化时调)。
  void _syncLiveActivity() {
    if (!_liveActivity.isSupported) return;
    final MainState s = _mainState;
    if (s == _lastSynced) return;
    _lastSynced = s;
    _liveActivity.start(status: _claudeStatus(s));
  }

  ClaudeStatus _claudeStatus(MainState s) {
    switch (s) {
      case MainState.idle:
        return ClaudeStatus.idle;
      case MainState.thinking:
        return ClaudeStatus.thinking;
      case MainState.asking:
        return ClaudeStatus.asking;
    }
  }

  /// 主灯/灵动岛状态:优先用中继上报的真实 Claude 状态;中继不可达时回落到
  /// agent 聚合(任一 asking→Asking / 任一 working→Thinking / 否则 Idle)。
  MainState get _mainState {
    final ClaudeStatus? r = _relayStatus;
    if (r != null) {
      return switch (r) {
        ClaudeStatus.idle => MainState.idle,
        ClaudeStatus.thinking => MainState.thinking,
        ClaudeStatus.asking => MainState.asking,
      };
    }
    final Iterable<AgentStatus> all = _statusMap.values;
    if (all.contains(AgentStatus.asking)) return MainState.asking;
    if (all.contains(AgentStatus.working)) return MainState.thinking;
    return MainState.idle;
  }

  /// 首页右上角「+」:先读剪贴板。
  /// - 命中抖音/TK/YouTube → 解析 → 首页卡片确认采集
  /// - 空 / 无支持链接 → 弹 [showShenkuoAddSheet] 手动加作者或作品
  Future<void> _onClipboardAdd() async {
    if (_clipSubmitting) return;
    setState(() {
      _clipCollect = const _ClipboardCollect.loading();
    });
    try {
      final data = await Clipboard.getData(Clipboard.kTextPlain);
      final text = data?.text?.trim() ?? '';
      if (text.isEmpty) {
        if (!mounted) return;
        setState(() => _clipCollect = null);
        await _openManualAddSheet();
        return;
      }
      final link = ShareLinkParser.detectFactorySupported(text);
      if (link == null) {
        if (!mounted) return;
        setState(() => _clipCollect = null);
        await _openManualAddSheet();
        return;
      }
      await _resolveClipboardLink(link, sourceText: text);
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _clipCollect = _ClipboardCollect.error('读取剪贴板失败:$e');
      });
    }
  }

  /// 手动粘贴/输入:作者对标 或 单作品采集(复用沈存中添加底卡)。
  Future<void> _openManualAddSheet() async {
    List<SubscriptionAuthor> existing = const <SubscriptionAuthor>[];
    try {
      final cfg = await _client.subscriptions();
      existing = List<SubscriptionAuthor>.from(
        cfg.authors ?? const <SubscriptionAuthor>[],
      );
    } catch (_) {
      // 拉订阅失败仍允许打开 sheet;提交时再校验。
    }
    if (!mounted) return;
    final changed = await showShenkuoAddSheet(
      context: context,
      client: _client,
      existingAuthors: existing,
    );
    if (changed == true && mounted) {
      _toast('已添加');
      unawaited(_refreshStatus());
    }
  }

  Future<void> _resolveClipboardLink(
    ShareLink link, {
    required String sourceText,
  }) async {
    try {
      final preferWork = ShareLinkParser.looksLikeWork(sourceText);
      if (preferWork) {
        try {
          final work = await _client.resolveWork(link.url);
          if (!mounted) return;
          setState(() {
            _clipCollect = _ClipboardCollect.work(link: link, work: work);
          });
          return;
        } catch (_) {
          // 作品解析失败再试账号
        }
        final account = await _client.resolveAccount(link.url);
        if (!mounted) return;
        setState(() {
          _clipCollect = _ClipboardCollect.account(
            link: link,
            account: account,
          );
        });
        return;
      }
      try {
        final account = await _client.resolveAccount(link.url);
        if (!mounted) return;
        setState(() {
          _clipCollect = _ClipboardCollect.account(
            link: link,
            account: account,
          );
        });
        return;
      } catch (_) {
        // 主页失败再试作品
      }
      final work = await _client.resolveWork(link.url);
      if (!mounted) return;
      setState(() {
        _clipCollect = _ClipboardCollect.work(link: link, work: work);
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _clipCollect = _ClipboardCollect.error('解析失败:请确认是抖音/TK/YouTube 链接');
      });
    }
  }

  Future<void> _confirmCollect() async {
    final pick = _clipCollect;
    if (pick == null || _clipSubmitting) return;
    setState(() => _clipSubmitting = true);
    try {
      if (pick.work != null) {
        final w = pick.work!;
        final url = (w.shareUrl ?? pick.link?.url ?? '').trim();
        if (url.isEmpty) throw FactoryError('没有可用分享链接');
        await _client.createTask(
          cmd: 'shenkuo',
          params: <String, dynamic>{
            'aweme': url,
            'platform': w.platform.isNotEmpty
                ? w.platform
                : (pick.link?.backendPlatform ?? 'douyin'),
          },
        );
      } else if (pick.account != null) {
        final a = pick.account!;
        await _client.createTask(
          cmd: 'shenkuo',
          params: <String, dynamic>{
            'author': a.secUid,
            'platform': a.platform.isNotEmpty
                ? a.platform
                : (pick.link?.backendPlatform ?? 'douyin'),
          },
        );
      } else {
        return;
      }
      if (!mounted) return;
      setState(() {
        _clipCollect = null;
        _clipSubmitting = false;
      });
      _toast('已派给沈存中采集');
      unawaited(_refreshStatus());
    } catch (e) {
      if (!mounted) return;
      setState(() => _clipSubmitting = false);
      _toast('派采集失败:$e');
    }
  }

  void _dismissClip() {
    if (_clipSubmitting) return;
    setState(() => _clipCollect = null);
  }

  void _toast(String msg) {
    final messenger = ScaffoldMessenger.of(context);
    messenger.clearSnackBars();
    messenger.showSnackBar(
      SnackBar(
        content: Text(
          msg,
          style: AppTypography.body.copyWith(color: AppColors.ivory),
        ),
        backgroundColor: AppColors.ink.withValues(alpha: 0.92),
        behavior: SnackBarBehavior.floating,
        duration: const Duration(milliseconds: 2600),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.sand,
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.fromLTRB(
            AppSpacing.pageH, // 水平 20
            AppSpacing.pageTop, // 顶 12
            AppSpacing.pageH,
            AppSpacing.pageBottom, // 底 28
          ),
          children: [
            _HeroArea(state: _mainState, booting: !_booted),
            const SizedBox(height: AppSpacing.block), // 英雄区与卡片墙间距 22
            _AgentBoard(
              reviewCounts: _reviewCounts,
              clipCollect: _clipCollect,
              clipSubmitting: _clipSubmitting,
              onOpenSettings: () => _openSettingsSheet(context),
              onClipboardAdd: _onClipboardAdd,
              onConfirmCollect: _confirmCollect,
              onDismissClip: _dismissClip,
            ),
          ],
        ),
      ),
    );
  }

  /// 齿轮:订阅管理 + 退出登录(底部),便于回登录页调样式。
  void _openSettingsSheet(BuildContext context) {
    final auth = AuthScope.maybeOf(context);
    final user = auth?.user;
    showModalBottomSheet<void>(
      context: context,
      backgroundColor: AppColors.ivory,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(
          top: Radius.circular(AppRadii.card),
        ),
      ),
      builder: (sheetCtx) {
        return SafeArea(
          child: Padding(
            padding: const EdgeInsets.fromLTRB(
              AppSpacing.pageH,
              AppSpacing.m,
              AppSpacing.pageH,
              AppSpacing.m,
            ),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Center(
                  child: Container(
                    width: 36,
                    height: 4,
                    decoration: BoxDecoration(
                      color: AppColors.ink.withValues(alpha: 0.15),
                      borderRadius: BorderRadius.circular(2),
                    ),
                  ),
                ),
                const SizedBox(height: AppSpacing.m),
                if (user != null) ...[
                  Text(
                    user.displayLabel,
                    style: AppTypography.subhead,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
                  if (user.email.isNotEmpty)
                    Text(
                      user.email,
                      style: AppTypography.caption.copyWith(
                        color: AppColors.inkMuted,
                      ),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),
                  const SizedBox(height: AppSpacing.m),
                ],
                ListTile(
                  contentPadding: EdgeInsets.zero,
                  leading: Icon(
                    Icons.notifications_outlined,
                    color: AppColors.ink.withValues(alpha: 0.7),
                  ),
                  title: Text('订阅管理', style: AppTypography.body),
                  onTap: () {
                    Navigator.of(sheetCtx).pop();
                    Navigator.of(context).push(
                      MaterialPageRoute<void>(
                        builder: (_) => const SubscriptionsScreen(),
                      ),
                    );
                  },
                ),
                const Divider(height: 1),
                ListTile(
                  contentPadding: EdgeInsets.zero,
                  leading: const Icon(Icons.logout, color: AppColors.statusRed),
                  title: Text(
                    '退出登录',
                    style: AppTypography.body.copyWith(
                      color: AppColors.statusRed,
                    ),
                  ),
                  onTap: () async {
                    Navigator.of(sheetCtx).pop();
                    await auth?.logout();
                  },
                ),
              ],
            ),
          ),
        );
      },
    );
  }
}

/// 首页「+」剪贴板采集预览状态。
class _ClipboardCollect {
  const _ClipboardCollect._({
    required this.phase,
    this.link,
    this.work,
    this.account,
    this.error,
  });

  const _ClipboardCollect.loading() : this._(phase: _ClipPhase.loading);
  const _ClipboardCollect.error(String message)
    : this._(phase: _ClipPhase.error, error: message);
  const _ClipboardCollect.work({
    required ShareLink link,
    required WorkResolveResult work,
  }) : this._(phase: _ClipPhase.ready, link: link, work: work);
  const _ClipboardCollect.account({
    required ShareLink link,
    required AccountResolveResult account,
  }) : this._(phase: _ClipPhase.ready, link: link, account: account);

  final _ClipPhase phase;
  final ShareLink? link;
  final WorkResolveResult? work;
  final AccountResolveResult? account;
  final String? error;

  bool get isReady =>
      phase == _ClipPhase.ready && (work != null || account != null);
}

enum _ClipPhase { loading, ready, error }

// MARK: - 英雄区:左上主灯(30pt) + "Agent" + WaveText 状态词

class _HeroArea extends StatelessWidget {
  const _HeroArea({required this.state, required this.booting});

  final MainState state;
  final bool booting;

  @override
  Widget build(BuildContext context) {
    // Fraunces 24 bold,与灵动岛/锁屏一致。
    final TextStyle heroStyle = AppTypography.displayTitle.copyWith(
      fontWeight: FontWeight.w700,
    );
    return Padding(
      // 灯左缘对齐下方 agent 头像左缘
      padding: const EdgeInsets.only(left: 14, top: 4),
      child: Row(
        children: [
          // 首次加载未拿到状态:灯先灭(灰底),配合「Connecting」文案
          // (简化原生自检 RYG 闪烁;拿到聚合状态后切真实灯态)。
          TrafficBulb(
            color: booting ? AppColors.inkFaint : _bulbColor,
            isOn: !booting,
            motion: _bulbMotion,
            period: _bulbPeriod,
            dimTo: _bulbDim,
            diameter: 30,
          ),
          const SizedBox(width: AppSpacing.m), // HStack spacing 12
          // "Agent" + 状态词,spacing 6
          Text('Agent', style: heroStyle.copyWith(color: AppColors.ink)),
          const SizedBox(width: AppSpacing.s),
          if (booting)
            Text(
              'Connecting',
              style: heroStyle.copyWith(
                color: AppColors.ink.withValues(alpha: 0.45),
              ),
            )
          else
            WaveText(
              text: _word,
              style: heroStyle.copyWith(color: _wordColor),
              animated: state != MainState.idle,
              // Asking 更急促,Thinking 平稳(对齐原生 period)。
              period: state == MainState.asking ? 1.0 : 1.6,
            ),
        ],
      ),
    );
  }

  String get _word {
    switch (state) {
      case MainState.idle:
        return 'Idle';
      case MainState.thinking:
        return 'Thinking';
      case MainState.asking:
        return 'Asking';
    }
  }

  // 文案/灯色对齐实物灯:推理=琥珀(R),等待=红(Y),空闲=绿(G)。
  Color get _wordColor {
    switch (state) {
      case MainState.idle:
        return AppColors.statusGreen;
      case MainState.thinking:
        return AppColors.statusYellow;
      case MainState.asking:
        return AppColors.statusRed;
    }
  }

  Color get _bulbColor => _wordColor;

  BulbMotion get _bulbMotion {
    switch (state) {
      case MainState.idle:
        return BulbMotion.solid;
      case MainState.thinking:
        return BulbMotion.breathe;
      case MainState.asking:
        return BulbMotion.blink;
    }
  }

  double get _bulbPeriod {
    switch (state) {
      case MainState.asking:
        return 0.7;
      case MainState.thinking:
        return 3.0;
      case MainState.idle:
        return 1.0;
    }
  }

  // 主灯呼吸最暗亮度(对齐原生 MainStatusLight.stateDim:R=0.45)。
  double get _bulbDim => state == MainState.thinking ? 0.45 : 1.0;
}

// MARK: - 智能体卡片墙

class _AgentBoard extends StatelessWidget {
  const _AgentBoard({
    required this.reviewCounts,
    required this.onOpenSettings,
    required this.onClipboardAdd,
    required this.onConfirmCollect,
    required this.onDismissClip,
    this.clipCollect,
    this.clipSubmitting = false,
  });

  final Map<String, int> reviewCounts;
  final VoidCallback onOpenSettings;
  final VoidCallback onClipboardAdd;
  final VoidCallback onConfirmCollect;
  final VoidCallback onDismissClip;
  final _ClipboardCollect? clipCollect;
  final bool clipSubmitting;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        // 顶部右对齐:齿轮(设置菜单) + 加号(读剪贴板采集,沈存中入口动作)。
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: AppSpacing.xs),
          child: Row(
            children: [
              const Spacer(),
              IconButton(
                tooltip: '设置',
                visualDensity: VisualDensity.compact,
                icon: Icon(
                  Icons.settings,
                  color: AppColors.ink.withValues(alpha: 0.55),
                ),
                onPressed: onOpenSettings,
              ),
              IconButton(
                tooltip: '采集(剪贴板 / 手动添加)',
                visualDensity: VisualDensity.compact,
                icon: Icon(Icons.add, color: AppColors.orange),
                onPressed: clipSubmitting ? null : onClipboardAdd,
              ),
            ],
          ),
        ),
        if (clipCollect != null) ...[
          const SizedBox(height: AppSpacing.s),
          _ClipboardCollectCard(
            pick: clipCollect!,
            submitting: clipSubmitting,
            onConfirm: onConfirmCollect,
            onDismiss: onDismissClip,
          ),
          const SizedBox(height: AppSpacing.m),
        ],
        const SizedBox(height: AppSpacing.m), // VStack spacing 12
        for (int i = 0; i < agentCatalog.length; i++) ...[
          if (i > 0) const SizedBox(height: AppSpacing.m),
          _AgentCard(
            agent: agentCatalog[i],
            reviewCount: reviewCounts[agentCatalog[i].cmd] ?? 0,
            onTap: () => _openInbox(context, agentCatalog[i]),
          ),
        ],
      ],
    );
  }

  /// agent 卡点击 → 该 agent 的主题化收件箱。
  /// 沈存中走专属页(决策任务 + 对标号资料库,与 web 同源数据);其余 agent 仍是任务分桶收件箱。
  void _openInbox(BuildContext context, AgentInfo agent) {
    final Widget page = agent.cmd == 'shenkuo'
        ? ShenkuoHomeScreen(agent: agent)
        : AgentTaskListScreen(agent: agent);
    Navigator.of(context).push(MaterialPageRoute<void>(builder: (_) => page));
  }
}

// MARK: - 单张 agent 卡片

class _AgentCard extends StatelessWidget {
  const _AgentCard({
    required this.agent,
    required this.reviewCount,
    this.onTap,
  });

  final AgentInfo agent;
  final int reviewCount; // 待验收数(completed 未决);0 不显示角标
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      behavior: HitTestBehavior.opaque,
      child: Container(
        padding: const EdgeInsets.all(14),
        decoration: BoxDecoration(
          color: AppColors.ivory,
          borderRadius: const BorderRadius.all(AppRadii.cardR),
          boxShadow: [
            BoxShadow(
              color: AppColors.ink.withValues(alpha: 0.10),
              blurRadius: 8,
              offset: const Offset(0, 4),
            ),
          ],
        ),
        child: Row(
          children: [
            _AgentAvatar(agent: agent),
            const SizedBox(width: 14), // HStack spacing 14
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisSize: MainAxisSize.min,
                children: [
                  _JustifiedName(text: agent.name),
                  const SizedBox(height: 3), // VStack spacing 3
                  Text(
                    agent.role,
                    style: AppTypography.caption.copyWith(
                      fontWeight: FontWeight.w600,
                      color: agent.accent,
                    ),
                  ),
                  const SizedBox(height: 3),
                  Text(
                    agent.blurb,
                    style: AppTypography.footnote.copyWith(
                      color: AppColors.ink.withValues(alpha: 0.55),
                    ),
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                  ),
                ],
              ),
            ),
            const SizedBox(width: 8), // Spacer(minLength: 8)
            // 待验收角标:橙色胶囊(对齐 iOS ctOrange,即"土黄"那种)。单位数为正圆
            // (minWidth=height),多位数自然撑长 —— 1 / 22 / 99+ 都正确渲染。
            // 去掉每张卡原本的红绿灯:6 张卡都跳动太花,改用数字标识待验收数,主灯只留顶部一盏。
            if (reviewCount > 0) ...[
              Container(
                constraints: const BoxConstraints(minWidth: 22),
                height: 22,
                alignment: Alignment.center,
                padding: const EdgeInsets.symmetric(horizontal: 7),
                decoration: const ShapeDecoration(
                  color: AppColors.orange,
                  shape: StadiumBorder(),
                ),
                child: Text(
                  reviewCount > 99 ? '99+' : '$reviewCount',
                  style: AppTypography.caption2.copyWith(
                    fontWeight: FontWeight.w700,
                    color: const Color(0xFFFFFFFF),
                  ),
                ),
              ),
              const SizedBox(width: AppSpacing.s),
            ],
            Icon(
              Icons.chevron_right,
              size: 16,
              color: AppColors.ink.withValues(alpha: 0.25),
            ),
          ],
        ),
      ),
    );
  }
}

/// 圆形渐变字号头像:52pt 圆,姓字白色粗体(字号 ≈ 52*0.46≈24)。
class _AgentAvatar extends StatelessWidget {
  const _AgentAvatar({required this.agent});

  final AgentInfo agent;

  @override
  Widget build(BuildContext context) {
    const double size = 52;
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
        border: Border.all(
          color: const Color(0xFFFFFFFF).withValues(alpha: 0.18),
          width: 1,
        ),
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
        style: const TextStyle(
          fontFamily: AppFonts.sans,
          fontFamilyFallback: AppFonts.sansFallback,
          fontSize: size * 0.46,
          fontWeight: FontWeight.w700,
          color: Color(0xFFFFFFFF),
        ),
      ),
    );
  }
}

/// 两端对齐的中文名:二字名(卧龙)撑到与三字名(鬼谷子)同宽,
/// 列表里所有名字首/末字两条竖线对齐。
class _JustifiedName extends StatelessWidget {
  const _JustifiedName({required this.text});

  final String text;

  /// 3 个 18pt 汉字实测约 54.7pt(含宋体回退),留余量取 58——
  /// 否则三字名(鬼谷子/吴道子)的 spaceBetween 行会右溢 ~2.7px(RenderFlex overflow)。
  static const double _width = 58;

  @override
  Widget build(BuildContext context) {
    final TextStyle style = AppTypography.titleL; // Fraunces 18 bold ink
    final List<String> chars = text.split('');
    if (chars.length < 2) {
      return SizedBox(
        width: _width,
        child: Text(text, style: style),
      );
    }
    return SizedBox(
      width: _width,
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [for (final String c in chars) Text(c, style: style)],
      ),
    );
  }
}

// 注:agent 卡原本每张一盏灯(_AgentStatusLight)已移除——6 张卡都跳动太花,
// 改用橙色待验收数字角标;实时灯态只保留顶部主灯一盏。

// MARK: - 首页剪贴板采集预览卡

class _ClipboardCollectCard extends StatelessWidget {
  const _ClipboardCollectCard({
    required this.pick,
    required this.submitting,
    required this.onConfirm,
    required this.onDismiss,
  });

  final _ClipboardCollect pick;
  final bool submitting;
  final VoidCallback onConfirm;
  final VoidCallback onDismiss;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(AppSpacing.cardInner),
      decoration: BoxDecoration(
        color: AppColors.ivory,
        borderRadius: const BorderRadius.all(AppRadii.cardR),
        boxShadow: [
          BoxShadow(
            color: AppColors.cardShadow,
            blurRadius: 6,
            offset: const Offset(0, 3),
          ),
        ],
        border: Border.all(
          color: AppColors.accentCollect.withValues(alpha: 0.22),
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(
                Icons.content_paste_search,
                size: 16,
                color: AppColors.accentCollect,
              ),
              const SizedBox(width: 6),
              Text(
                '剪贴板 · 沈存中',
                style: AppTypography.subhead.copyWith(
                  color: AppColors.accentCollect,
                  fontWeight: FontWeight.w600,
                ),
              ),
              const Spacer(),
              if (!submitting && pick.phase != _ClipPhase.loading)
                IconButton(
                  tooltip: '关闭',
                  visualDensity: VisualDensity.compact,
                  padding: EdgeInsets.zero,
                  constraints: const BoxConstraints(
                    minWidth: 28,
                    minHeight: 28,
                  ),
                  icon: Icon(Icons.close, size: 18, color: AppColors.inkFaint),
                  onPressed: onDismiss,
                ),
            ],
          ),
          const SizedBox(height: AppSpacing.s),
          if (pick.phase == _ClipPhase.loading)
            Row(
              children: [
                const SizedBox(
                  width: 18,
                  height: 18,
                  child: CircularProgressIndicator(strokeWidth: 2),
                ),
                const SizedBox(width: AppSpacing.m),
                Text(
                  '正在解析剪贴板链接…',
                  style: AppTypography.body.copyWith(color: AppColors.inkMuted),
                ),
              ],
            )
          else if (pick.phase == _ClipPhase.error)
            Text(
              pick.error ?? '解析失败',
              style: AppTypography.body.copyWith(color: AppColors.statusRed),
            )
          else if (pick.work != null)
            _workBody(pick.work!, pick.link)
          else if (pick.account != null)
            _accountBody(pick.account!, pick.link),
          if (pick.isReady) ...[
            const SizedBox(height: AppSpacing.m),
            Text(
              pick.work != null ? '是否立即开始采集这条作品？' : '是否立即开始采集该作者？',
              style: AppTypography.caption.copyWith(color: AppColors.inkMuted),
            ),
            const SizedBox(height: AppSpacing.s),
            Row(
              children: [
                Expanded(
                  child: DecisionButton(
                    label: '稍后',
                    filled: false,
                    tint: AppColors.inkMuted,
                    onPressed: submitting ? () {} : onDismiss,
                  ),
                ),
                const SizedBox(width: AppSpacing.m),
                Expanded(
                  child: DecisionButton(
                    label: submitting ? '派发中…' : '立即开始采集',
                    tint: AppColors.accentCollect,
                    onPressed: submitting ? () {} : onConfirm,
                  ),
                ),
              ],
            ),
          ],
        ],
      ),
    );
  }

  Widget _workBody(WorkResolveResult w, ShareLink? link) {
    final title = (w.title ?? '').trim();
    final author = w.author?['nickname'] as String?;
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        ClipRRect(
          borderRadius: BorderRadius.circular(10),
          child: SizedBox(
            width: 56,
            height: 74,
            child: (w.coverUrl != null && w.coverUrl!.isNotEmpty)
                ? Image.network(
                    w.coverUrl!,
                    fit: BoxFit.cover,
                    errorBuilder: (_, error, stackTrace) => _placeholder(),
                  )
                : _placeholder(),
          ),
        ),
        const SizedBox(width: AppSpacing.m),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              if (link != null)
                TagChip(label: link.platform, tint: AppColors.accentCollect),
              const SizedBox(height: 4),
              Text(
                title.isEmpty ? '作品 ${w.awemeId}' : title,
                style: AppTypography.titleM,
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
              ),
              if (author != null && author.isNotEmpty) ...[
                const SizedBox(height: 2),
                Text(
                  author,
                  style: AppTypography.caption.copyWith(
                    color: AppColors.inkMuted,
                  ),
                ),
              ],
              const SizedBox(height: 4),
              Wrap(
                spacing: 8,
                children: [
                  if (w.digg != null)
                    _stat(Icons.favorite, w.digg!, AppColors.statusRed),
                  if (w.comment != null)
                    _stat(Icons.mode_comment, w.comment!, AppColors.inkMuted),
                ],
              ),
            ],
          ),
        ),
      ],
    );
  }

  Widget _accountBody(AccountResolveResult a, ShareLink? link) {
    final name = (a.nickname != null && a.nickname!.isNotEmpty)
        ? a.nickname!
        : '已解析账号';
    return Row(
      children: [
        ClipOval(
          child: SizedBox(
            width: 48,
            height: 48,
            child: (a.avatar != null && a.avatar!.isNotEmpty)
                ? Image.network(
                    a.avatar!,
                    fit: BoxFit.cover,
                    errorBuilder: (_, error, stackTrace) =>
                        _avatarFallback(name),
                  )
                : _avatarFallback(name),
          ),
        ),
        const SizedBox(width: AppSpacing.m),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  if (link != null) ...[
                    TagChip(
                      label: link.platform,
                      tint: AppColors.accentCollect,
                    ),
                    const SizedBox(width: 6),
                  ],
                  Expanded(
                    child: Text(
                      name,
                      style: AppTypography.titleM,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
                ],
              ),
              if (a.uniqueId != null && a.uniqueId!.isNotEmpty) ...[
                const SizedBox(height: 2),
                Text(
                  '@${a.uniqueId}',
                  style: AppTypography.caption.copyWith(
                    color: AppColors.inkMuted,
                  ),
                ),
              ],
              if (a.followerCount != null) ...[
                const SizedBox(height: 2),
                Text(
                  '粉丝 ${shenkuoCount(a.followerCount!)}',
                  style: AppTypography.caption.copyWith(
                    color: AppColors.inkMuted,
                  ),
                ),
              ],
            ],
          ),
        ),
      ],
    );
  }

  Widget _stat(IconData icon, int n, Color color) => Row(
    mainAxisSize: MainAxisSize.min,
    children: [
      Icon(icon, size: 11, color: color),
      const SizedBox(width: 2),
      Text(
        shenkuoCount(n),
        style: AppTypography.caption.copyWith(
          fontWeight: FontWeight.w600,
          color: color,
        ),
      ),
    ],
  );

  Widget _placeholder() => Container(
    color: AppColors.ink.withValues(alpha: 0.06),
    alignment: Alignment.center,
    child: Icon(Icons.movie_outlined, size: 20, color: AppColors.inkFaint),
  );

  Widget _avatarFallback(String name) {
    final ch = name.isNotEmpty ? name.characters.first : '作';
    return Container(
      color: AppColors.accentCollect.withValues(alpha: 0.16),
      alignment: Alignment.center,
      child: Text(
        ch,
        style: AppTypography.titleM.copyWith(color: AppColors.accentCollect),
      ),
    );
  }
}

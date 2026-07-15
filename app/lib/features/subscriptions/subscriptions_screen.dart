// 订阅管理页:沈括订阅传感器的配置面(GET/PUT /subscriptions + POST /subscriptions/tick)。
// PUT 是整体覆盖写——页面先 GET 全量,本地编辑后整体回写,绝不用局部构造覆盖
// (见 FactoryClient.putSubscriptions 注释):authors 为 null 会被后端清空,故保存时
// 始终传当前本地全量(空表 = 用户显式删光的语义)。
//
// 接入:从首页/设置 push MaterialPageRoute(builder: (_) => const SubscriptionsScreen())。

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../core/labels/platform_domain.dart';
import '../../core/net/endpoint_resolver.dart';
import '../../core/net/factory_client.dart';
import '../../core/net/models.dart';
import '../../design/components/app_card.dart';
import '../../design/components/decision_button.dart';
import '../../design/components/tag_chip.dart';
import '../../design/tokens.dart';
import '../../design/typography.dart';

/// 订阅管理:对标作者列表 + 巡查间隔 + 手动派发一轮。
/// 测试可注入 [client];为 null 时走 LAN 直连工厂后端。
class SubscriptionsScreen extends StatefulWidget {
  const SubscriptionsScreen({super.key, this.client});

  /// 测试可注入假客户端;为 null 时走真实 [FactoryClient]。
  final FactoryClient? client;

  @override
  State<SubscriptionsScreen> createState() => _SubscriptionsScreenState();
}

class _SubscriptionsScreenState extends State<SubscriptionsScreen> {
  static const String _devBase = 'http://liuzhihao-mbp.local:8810';

  late final FactoryClient _client =
      widget.client ?? FactoryClient(resolver: DirectEndpointResolver(_devBase));

  bool _loading = true;
  String? _loadError;

  // 「GET 回来就地修改」的本地全量:保存时整体回写。authors 永不为 null,
  // 改成空表才是清空——避免误清。
  double _intervalHours = 2.0;
  List<SubscriptionAuthor> _authors = const <SubscriptionAuthor>[];

  bool _saving = false;
  bool _ticking = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _loadError = null;
    });
    try {
      final cfg = await _client.subscriptions();
      if (!mounted) return;
      setState(() {
        _intervalHours = cfg.intervalHours ?? 2.0;
        _authors = List<SubscriptionAuthor>.from(cfg.authors ?? const <SubscriptionAuthor>[]);
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loadError = '$e';
        _loading = false;
      });
    }
  }

  Future<void> _save() async {
    if (_saving) return;
    if (_intervalHours <= 0) {
      _toast('巡查间隔要是正数(小时)');
      return;
    }
    setState(() => _saving = true);
    try {
      // 整体覆盖写:authors 传当前编辑后的全量(空表 = 清空订阅,这是用户显式删完的语义)。
      final cfg = SubscriptionsConfig(intervalHours: _intervalHours, authors: _authors);
      final saved = await _client.putSubscriptions(cfg);
      if (!mounted) return;
      setState(() {
        _intervalHours = saved.intervalHours ?? _intervalHours;
        _authors = List<SubscriptionAuthor>.from(saved.authors ?? const <SubscriptionAuthor>[]);
        _saving = false;
      });
      _toast('已保存:${_authors.length} 个作者,每 ${_fmtHours(_intervalHours)} 小时巡查');
    } catch (e) {
      if (!mounted) return;
      setState(() => _saving = false);
      _toast('保存失败:$e');
    }
  }

  Future<void> _tick() async {
    if (_ticking) return;
    setState(() => _ticking = true);
    try {
      final n = await _client.tickSubscriptions();
      if (!mounted) return;
      _toast(n > 0 ? '已派发 $n 个巡查任务' : '本轮没有要巡查的作者');
    } catch (e) {
      if (!mounted) return;
      _toast('巡查失败:$e');
    } finally {
      if (mounted) setState(() => _ticking = false);
    }
  }

  Future<void> _editInterval() async {
    final result = await showModalBottomSheet<double>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (_) => _IntervalEditSheet(initialHours: _intervalHours),
    );
    if (result != null && mounted) {
      setState(() => _intervalHours = result);
    }
  }

  Future<void> _editAuthor({int? index}) async {
    final existingUids = <String>[
      for (var i = 0; i < _authors.length; i++)
        if (i != index) _authors[i].secUid,
    ];
    final result = await showModalBottomSheet<SubscriptionAuthor>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (_) => _AuthorEditSheet(
        author: index == null ? null : _authors[index],
        existingUids: existingUids,
      ),
    );
    if (result == null || !mounted) return;
    setState(() {
      final next = List<SubscriptionAuthor>.from(_authors);
      if (index != null && index >= 0 && index < next.length) {
        next[index] = result;
      } else {
        next.add(result);
      }
      _authors = next;
    });
  }

  void _toggleAuthor(int index, bool on) {
    setState(() {
      final next = List<SubscriptionAuthor>.from(_authors);
      next[index] = next[index].copyWith(enabled: on);
      _authors = next;
    });
  }

  void _removeAuthor(int index) {
    setState(() {
      final next = List<SubscriptionAuthor>.from(_authors);
      next.removeAt(index);
      _authors = next;
    });
  }

  void _toast(String msg) {
    final messenger = ScaffoldMessenger.of(context);
    messenger.clearSnackBars();
    messenger.showSnackBar(
      SnackBar(
        content: Text(msg, style: AppTypography.body.copyWith(color: AppColors.ivory)),
        backgroundColor: AppColors.ink.withValues(alpha: 0.92),
        behavior: SnackBarBehavior.floating,
        duration: const Duration(milliseconds: 2500),
      ),
    );
  }

  static String _fmtHours(double h) {
    if (h == h.roundToDouble()) return h.toInt().toString();
    return h.toString();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        backgroundColor: AppColors.sand,
        foregroundColor: AppColors.ink,
        title: Text('订阅管理', style: AppTypography.navTitle),
      ),
      body: _buildBody(),
    );
  }

  Widget _buildBody() {
    if (_loading) {
      return const Center(child: CircularProgressIndicator());
    }
    if (_loadError != null) {
      return _centered(_ErrorState(message: _loadError!, onRetry: _load));
    }
    return _buildForm();
  }

  Widget _centered(Widget child) => ListView(
        physics: const AlwaysScrollableScrollPhysics(),
        children: [
          Padding(padding: const EdgeInsets.only(top: 96), child: Center(child: child)),
        ],
      );

  Widget _buildForm() {
    // 保存是「先 GET 后整体 PUT」的覆盖写:在途时锁住整个表单的编辑入口,
    // 否则保存期间的编辑会被 PUT 返回的快照悄悄回滚。
    final bool locked = _saving;
    return ListView(
      physics: const AlwaysScrollableScrollPhysics(),
      padding: const EdgeInsets.fromLTRB(
        AppSpacing.pageH,
        AppSpacing.m,
        AppSpacing.pageH,
        AppSpacing.pageBottom,
      ),
      children: [
        _SectionHeader(title: '巡查周期', hint: '沈存中每隔这么久刷一遍启用中的作者,发现新作品自动采集。'),
        const SizedBox(height: AppSpacing.s),
        _intervalCard(locked),
        const SizedBox(height: AppSpacing.block),
        _SectionHeader(title: '对标作者', hint: '左滑删除。改完记得点「保存配置」——配置是整体回写。'),
        const SizedBox(height: AppSpacing.s),
        _authorsSection(locked),
        const SizedBox(height: AppSpacing.block),
        DecisionButton(
          label: _saving ? '保存中…' : '保存配置',
          onPressed: locked ? () {} : _save,
        ),
        const SizedBox(height: AppSpacing.m),
        DecisionButton(
          label: _ticking ? '巡查中…' : '立即巡查',
          filled: false,
          onPressed: (_ticking || locked) ? () {} : _tick,
        ),
        const SizedBox(height: AppSpacing.s),
        Text(
          '「立即巡查」用服务器上已保存的配置跑一轮,不等下个周期。',
          style: AppTypography.caption.copyWith(color: AppColors.inkMuted),
        ),
      ],
    );
  }

  Widget _intervalCard(bool locked) {
    return AppCard(
      child: InkWell(
        onTap: locked ? null : _editInterval,
        borderRadius: const BorderRadius.all(AppRadii.cardR),
        child: Row(
          children: [
            Icon(Icons.timelapse_outlined, size: 20, color: AppColors.inkMuted),
            const SizedBox(width: AppSpacing.m),
            Expanded(child: Text('巡查间隔', style: AppTypography.body)),
            Text('${_fmtHours(_intervalHours)} 小时', style: AppTypography.titleM),
            const SizedBox(width: AppSpacing.s),
            Icon(Icons.chevron_right, size: 20, color: AppColors.inkFaint),
          ],
        ),
      ),
    );
  }

  Widget _authorsSection(bool locked) {
    return Column(
      children: [
        if (_authors.isEmpty)
          AppCard(
            child: Text(
              '配置对标作者后,沈存中会按周期自动巡查新作品。',
              style: AppTypography.body.copyWith(color: AppColors.inkMuted),
            ),
          )
        else
          for (var i = 0; i < _authors.length; i++) ...[
            _AuthorCard(
              author: _authors[i],
              locked: locked,
              onTap: () => _editAuthor(index: i),
              onToggle: (on) => _toggleAuthor(i, on),
              onDelete: () => _removeAuthor(i),
            ),
            const SizedBox(height: AppSpacing.m),
          ],
        DecisionButton(
          label: '添加作者',
          filled: false,
          onPressed: locked ? () {} : () => _editAuthor(),
        ),
      ],
    );
  }
}

/// 区块标题 + 说明,复刻 iOS Form 的 section header/footer。
class _SectionHeader extends StatelessWidget {
  const _SectionHeader({required this.title, required this.hint});
  final String title;
  final String hint;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(title, style: AppTypography.subhead.copyWith(color: AppColors.inkMuted)),
        const SizedBox(height: AppSpacing.xs),
        Text(hint, style: AppTypography.caption.copyWith(color: AppColors.inkMuted)),
      ],
    );
  }
}

/// 作者卡片:点卡进编辑;启用开关直接改本地数组(仍需保存生效);右上删除。
/// 标题优先展示与 web 同源的名片(nickname),备注/sec_uid 作副文。
class _AuthorCard extends StatelessWidget {
  const _AuthorCard({
    required this.author,
    required this.locked,
    required this.onTap,
    required this.onToggle,
    required this.onDelete,
  });

  final SubscriptionAuthor author;
  final bool locked;
  final VoidCallback onTap;
  final ValueChanged<bool> onToggle;
  final VoidCallback onDelete;

  @override
  Widget build(BuildContext context) {
    final a = author;
    final subtitle = (a.note?.isNotEmpty ?? false)
        ? a.note!
        : ((a.uniqueId?.isNotEmpty ?? false) ? '@${a.uniqueId}' : a.secUid);
    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              _Avatar(url: a.avatar, label: a.displayName),
              const SizedBox(width: AppSpacing.m),
              Expanded(
                child: InkWell(
                  onTap: locked ? null : onTap,
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(a.displayName, style: AppTypography.titleM, maxLines: 2, overflow: TextOverflow.ellipsis),
                      const SizedBox(height: AppSpacing.xs),
                      Text(
                        subtitle,
                        style: AppTypography.caption.copyWith(color: AppColors.inkMuted),
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                      ),
                      if (a.secUid.isNotEmpty && subtitle != a.secUid) ...[
                        const SizedBox(height: 2),
                        Text(
                          a.secUid,
                          style: AppTypography.monoS.copyWith(color: AppColors.inkFaint),
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                        ),
                      ],
                    ],
                  ),
                ),
              ),
              const SizedBox(width: AppSpacing.s),
              Switch(
                value: a.isEnabled,
                onChanged: locked ? null : onToggle,
                activeTrackColor: AppColors.orange,
              ),
            ],
          ),
          const SizedBox(height: AppSpacing.s),
          Row(
            children: [
              TagChip(
                label: platformDisplayName(a.platformOrDouyin),
                tint: AppColors.inkFaint,
              ),
              const SizedBox(width: 6),
              TagChip(
                label: a.isEnabled ? '巡查中' : '已停用',
                tint: a.isEnabled ? AppColors.statusAdopted : AppColors.inkFaint,
              ),
              if (domainDisplayName(a.domain) != null) ...[
                const SizedBox(width: 6),
                TagChip(
                  label: domainDisplayName(a.domain)!,
                  tint: AppColors.accentCollect,
                ),
              ],
              const Spacer(),
              TextButton.icon(
                onPressed: locked ? null : onDelete,
                icon: Icon(Icons.delete_outline, size: 18, color: AppColors.statusRed),
                label: Text('删除', style: AppTypography.label.copyWith(color: AppColors.statusRed)),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

class _Avatar extends StatelessWidget {
  const _Avatar({this.url, required this.label});
  final String? url;
  final String label;

  @override
  Widget build(BuildContext context) {
    final has = url != null && url!.isNotEmpty;
    final ch = label.isNotEmpty ? label.characters.first : '作';
    return ClipOval(
      child: SizedBox(
        width: 40,
        height: 40,
        child: has
            ? Image.network(
                url!,
                fit: BoxFit.cover,
                errorBuilder: (_, error, stackTrace) => _fallback(ch),
              )
            : _fallback(ch),
      ),
    );
  }

  Widget _fallback(String ch) => Container(
        color: AppColors.accentCollect.withValues(alpha: 0.16),
        alignment: Alignment.center,
        child: Text(ch, style: AppTypography.subhead.copyWith(color: AppColors.accentCollect)),
      );
}

/// 巡查间隔编辑底卡:十进制小时数,正数校验。
class _IntervalEditSheet extends StatefulWidget {
  const _IntervalEditSheet({required this.initialHours});
  final double initialHours;

  @override
  State<_IntervalEditSheet> createState() => _IntervalEditSheetState();
}

class _IntervalEditSheetState extends State<_IntervalEditSheet> {
  late final TextEditingController _controller = TextEditingController(
    text: _SubscriptionsScreenState._fmtHours(widget.initialHours),
  );
  String? _error;

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  void _confirm() {
    final raw = _controller.text.trim();
    final hours = double.tryParse(raw);
    if (hours == null || hours <= 0) {
      setState(() => _error = '请输入正数(小时)');
      return;
    }
    Navigator.of(context).pop(hours);
  }

  @override
  Widget build(BuildContext context) {
    return _SheetScaffold(
      title: '巡查间隔',
      onCancel: () => Navigator.of(context).pop(),
      onConfirm: _confirm,
      confirmEnabled: true,
      children: [
        TextField(
          controller: _controller,
          keyboardType: const TextInputType.numberWithOptions(decimal: true),
          inputFormatters: [FilteringTextInputFormatter.allow(RegExp(r'[0-9.]'))],
          style: AppTypography.bodyInput,
          decoration: _inputDecoration(hint: '2', suffixText: '小时'),
        ),
        const SizedBox(height: AppSpacing.s),
        Text(
          '沈存中每隔这么久刷一遍启用中的作者。',
          style: AppTypography.caption.copyWith(color: AppColors.inkMuted),
        ),
        if (_error != null) ...[
          const SizedBox(height: AppSpacing.s),
          Text(_error!, style: AppTypography.caption.copyWith(color: AppColors.statusRed)),
        ],
      ],
    );
  }
}

/// 作者编辑底卡(新增/修改共用)。author 为 null 即新增;existingUids 本地查重。
class _AuthorEditSheet extends StatefulWidget {
  const _AuthorEditSheet({required this.author, required this.existingUids});
  final SubscriptionAuthor? author;
  final List<String> existingUids;

  @override
  State<_AuthorEditSheet> createState() => _AuthorEditSheetState();
}

class _AuthorEditSheetState extends State<_AuthorEditSheet> {
  late final TextEditingController _secUid = TextEditingController(text: widget.author?.secUid ?? '');
  late final TextEditingController _note = TextEditingController(text: widget.author?.note ?? '');
  late bool _enabled = widget.author?.isEnabled ?? true;
  String? _error;

  bool get _isNew => widget.author == null;

  @override
  void dispose() {
    _secUid.dispose();
    _note.dispose();
    super.dispose();
  }

  Future<void> _paste() async {
    final data = await Clipboard.getData(Clipboard.kTextPlain);
    final text = data?.text?.trim();
    if (text != null && text.isNotEmpty) {
      _secUid.text = text;
      _secUid.selection = TextSelection.collapsed(offset: text.length);
    }
  }

  void _confirm() {
    final uid = _secUid.text.trim();
    if (uid.isEmpty) {
      setState(() => _error = '请填写 sec_uid');
      return;
    }
    if (widget.existingUids.contains(uid)) {
      setState(() => _error = '这个 sec_uid 已在订阅列表里');
      return;
    }
    final note = _note.text.trim();
    // 编辑时保留名片/平台/赛道等字段,避免 PUT 整体覆盖时把 web 写入的快照冲掉。
    final base = widget.author;
    Navigator.of(context).pop(SubscriptionAuthor(
      secUid: uid,
      note: note.isEmpty ? null : note,
      enabled: _enabled,
      platform: base?.platform,
      domain: base?.domain,
      intervalHours: base?.intervalHours,
      nickname: base?.nickname,
      avatar: base?.avatar,
      uniqueId: base?.uniqueId,
      followerCount: base?.followerCount,
      likeCount: base?.likeCount,
      worksCount: base?.worksCount,
      refreshedAt: base?.refreshedAt,
    ));
  }

  @override
  Widget build(BuildContext context) {
    return _SheetScaffold(
      title: _isNew ? '添加作者' : '编辑作者',
      onCancel: () => Navigator.of(context).pop(),
      onConfirm: _confirm,
      confirmEnabled: true,
      children: [
        Text('sec_uid', style: AppTypography.subhead.copyWith(color: AppColors.inkMuted)),
        const SizedBox(height: AppSpacing.xs),
        TextField(
          controller: _secUid,
          minLines: 2,
          maxLines: 4,
          autocorrect: false,
          enableSuggestions: false,
          style: AppTypography.mono,
          decoration: _inputDecoration(hint: 'MS4wLjABAAAA…'),
        ),
        const SizedBox(height: AppSpacing.xs),
        Align(
          alignment: Alignment.centerLeft,
          child: TextButton.icon(
            onPressed: _paste,
            icon: Icon(Icons.content_paste, size: 16, color: AppColors.orange),
            label: Text('粘贴', style: AppTypography.label.copyWith(color: AppColors.orange)),
          ),
        ),
        Text(
          '抖音作者主页链接里的 sec_uid(MS4wLjABAAAA 开头)。',
          style: AppTypography.caption.copyWith(color: AppColors.inkMuted),
        ),
        const SizedBox(height: AppSpacing.m),
        Text('备注', style: AppTypography.subhead.copyWith(color: AppColors.inkMuted)),
        const SizedBox(height: AppSpacing.xs),
        TextField(
          controller: _note,
          style: AppTypography.bodyInput,
          decoration: _inputDecoration(hint: '如:对标号A'),
        ),
        const SizedBox(height: AppSpacing.m),
        Row(
          children: [
            Expanded(child: Text('启用巡查', style: AppTypography.body)),
            Switch(
              value: _enabled,
              onChanged: (v) => setState(() => _enabled = v),
              activeTrackColor: AppColors.orange,
            ),
          ],
        ),
        if (_error != null) ...[
          const SizedBox(height: AppSpacing.s),
          Text(_error!, style: AppTypography.caption.copyWith(color: AppColors.statusRed)),
        ],
      ],
    );
  }
}

/// 底卡通用脚手架:顶部 取消/标题/完成 + 内容区,随键盘抬升。
class _SheetScaffold extends StatelessWidget {
  const _SheetScaffold({
    required this.title,
    required this.onCancel,
    required this.onConfirm,
    required this.confirmEnabled,
    required this.children,
  });

  final String title;
  final VoidCallback onCancel;
  final VoidCallback onConfirm;
  final bool confirmEnabled;
  final List<Widget> children;

  @override
  Widget build(BuildContext context) {
    final bottomInset = MediaQuery.of(context).viewInsets.bottom;
    return Padding(
      padding: EdgeInsets.only(bottom: bottomInset),
      child: Container(
        decoration: const BoxDecoration(
          color: AppColors.ivory,
          borderRadius: BorderRadius.vertical(top: AppRadii.cardR),
        ),
        child: SafeArea(
          top: false,
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
                Row(
                  children: [
                    TextButton(
                      onPressed: onCancel,
                      child: Text('取消', style: AppTypography.body.copyWith(color: AppColors.inkMuted)),
                    ),
                    Expanded(child: Center(child: Text(title, style: AppTypography.titleM))),
                    TextButton(
                      onPressed: confirmEnabled ? onConfirm : null,
                      child: Text(
                        '完成',
                        style: AppTypography.subhead.copyWith(
                          color: confirmEnabled ? AppColors.orange : AppColors.inkFaint,
                        ),
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: AppSpacing.s),
                ...children,
                const SizedBox(height: AppSpacing.s),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

/// 统一输入框装饰:象牙底上一层柔和描边,聚焦时橙色高亮。
InputDecoration _inputDecoration({String? hint, String? suffixText}) {
  OutlineInputBorder border(Color c) => OutlineInputBorder(
        borderRadius: const BorderRadius.all(Radius.circular(AppRadii.capture)),
        borderSide: BorderSide(color: c, width: 1.2),
      );
  return InputDecoration(
    isDense: true,
    hintText: hint,
    hintStyle: AppTypography.bodyInput.copyWith(color: AppColors.inkFaint),
    suffixText: suffixText,
    suffixStyle: AppTypography.body.copyWith(color: AppColors.inkMuted),
    filled: true,
    fillColor: AppColors.sand.withValues(alpha: 0.35),
    contentPadding: const EdgeInsets.symmetric(horizontal: AppSpacing.m, vertical: AppSpacing.m),
    enabledBorder: border(AppColors.inkFaint),
    focusedBorder: border(AppColors.orange),
  );
}

/// 错误态,复刻收件箱 _ErrorState。
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
        Text('拉不到订阅配置', style: AppTypography.titleM),
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

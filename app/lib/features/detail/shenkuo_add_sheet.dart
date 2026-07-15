// 沈存中「+」：对齐 web 首页新增能力（关注作者 / 作品分享解析）。
// 不再进 ComposeScreen 命令列表；粘贴链接 → resolve → 确认写入订阅或派采集任务。

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../core/labels/platform_domain.dart';
import '../../core/net/factory_client.dart';
import '../../core/net/models.dart';
import '../../design/components/decision_button.dart';
import '../../design/components/tag_chip.dart';
import '../../design/tokens.dart';
import '../../design/typography.dart';
import 'shenkuo_collect_panel.dart' show shenkuoCount;

const String _defaultDomain = 'finance';

/// 必须含 URL 才触发解析（对齐 web HomePage URL_RE）。
final RegExp _urlRe = RegExp(
  r'https?:\/\/[^\s]+|(?:www\.)?(?:douyin|tiktok|youtube)\.com\/[^\s]+|youtu\.be\/[^\s]+',
  caseSensitive: false,
);

bool _containsUrl(String s) => _urlRe.hasMatch(s);

/// 粗判更像作品还是主页（决定先打哪个 resolve；失败再试另一路）。
bool _looksLikeWork(String s) {
  final t = s.toLowerCase();
  return t.contains('/video/') ||
      t.contains('aweme') ||
      t.contains('modal_id') ||
      t.contains('v.douyin.com') ||
      t.contains('vm.tiktok.com') ||
      t.contains('youtu.be/') ||
      t.contains('watch?v=');
}

/// 添加模式：关注对标账号 / 单作品采集。
enum ShenkuoAddKind { account, work }

/// 弹出沈存中添加底卡。返回 true 表示订阅或任务有变更，调用方应刷新。
Future<bool?> showShenkuoAddSheet({
  required BuildContext context,
  required FactoryClient client,
  required List<SubscriptionAuthor> existingAuthors,
  ShenkuoAddKind initialKind = ShenkuoAddKind.account,
}) {
  return showModalBottomSheet<bool>(
    context: context,
    isScrollControlled: true,
    backgroundColor: Colors.transparent,
    builder: (_) => _ShenkuoAddSheet(
      client: client,
      existingAuthors: existingAuthors,
      initialKind: initialKind,
    ),
  );
}

class _ShenkuoAddSheet extends StatefulWidget {
  const _ShenkuoAddSheet({
    required this.client,
    required this.existingAuthors,
    required this.initialKind,
  });

  final FactoryClient client;
  final List<SubscriptionAuthor> existingAuthors;
  final ShenkuoAddKind initialKind;

  @override
  State<_ShenkuoAddSheet> createState() => _ShenkuoAddSheetState();
}

class _ShenkuoAddSheetState extends State<_ShenkuoAddSheet> {
  late ShenkuoAddKind _kind = widget.initialKind;
  final TextEditingController _text = TextEditingController();
  Timer? _debounce;

  bool _resolving = false;
  bool _submitting = false;
  String? _error;

  AccountResolveResult? _account;
  WorkResolveResult? _work;
  String _domain = _defaultDomain;

  @override
  void dispose() {
    _debounce?.cancel();
    _text.dispose();
    super.dispose();
  }

  void _onTextChanged(String _) {
    _debounce?.cancel();
    final raw = _text.text.trim();
    if (raw.isEmpty || !_containsUrl(raw)) return;
    _debounce = Timer(const Duration(milliseconds: 350), () => _resolve(raw));
  }

  Future<void> _paste() async {
    final data = await Clipboard.getData(Clipboard.kTextPlain);
    final t = data?.text?.trim();
    if (t == null || t.isEmpty) return;
    _text.text = t;
    _text.selection = TextSelection.collapsed(offset: t.length);
    _onTextChanged(t);
  }

  Future<void> _resolve(String raw) async {
    if (_resolving || _submitting) return;
    setState(() {
      _resolving = true;
      _error = null;
      _account = null;
      _work = null;
    });
    try {
      if (_kind == ShenkuoAddKind.account) {
        await _resolveAccount(raw);
      } else {
        await _resolveWork(raw);
      }
      if (mounted) _text.clear();
    } finally {
      if (mounted) setState(() => _resolving = false);
    }
  }

  Future<void> _resolveAccount(String raw) async {
    try {
      final r = await widget.client.resolveAccount(raw);
      if (!mounted) return;
      final dup = widget.existingAuthors.any((a) => a.secUid == r.secUid);
      if (dup) {
        setState(() => _error = '「${r.nickname ?? r.secUid}」已在对标列表');
        return;
      }
      setState(() {
        _account = r;
        _work = null;
        _error = null;
      });
    } on FactoryError catch (e) {
      // 主页解析失败时若像作品链接，自动切到作品模式再试
      if (_looksLikeWork(raw)) {
        setState(() => _kind = ShenkuoAddKind.work);
        await _resolveWork(raw);
        return;
      }
      if (!mounted) return;
      setState(() => _error = '解析失败：请贴抖音/TK 主页链接（${e.message}）');
    } catch (e) {
      if (!mounted) return;
      setState(() => _error = '解析失败：请贴抖音/TK 主页链接');
    }
  }

  Future<void> _resolveWork(String raw) async {
    try {
      final r = await widget.client.resolveWork(raw);
      if (!mounted) return;
      final d = r.domain;
      setState(() {
        _work = r;
        _account = null;
        if (d != null && kDomainOptions.any((x) => x.key == d)) _domain = d;
        _error = null;
      });
    } on FactoryError catch (e) {
      if (!_looksLikeWork(raw)) {
        setState(() => _kind = ShenkuoAddKind.account);
        await _resolveAccount(raw);
        return;
      }
      if (!mounted) return;
      setState(() => _error = '解析失败：请贴作品分享链接（${e.message}）');
    } catch (e) {
      if (!mounted) return;
      setState(() => _error = '解析失败：请贴作品分享链接');
    }
  }

  Future<void> _submit() async {
    if (_submitting) return;
    if (_kind == ShenkuoAddKind.account) {
      await _submitAccount();
    } else {
      await _submitWork();
    }
  }

  Future<void> _submitAccount() async {
    final p = _account;
    if (p == null) return;
    setState(() => _submitting = true);
    try {
      final cfg = await widget.client.subscriptions();
      final authors = List<SubscriptionAuthor>.from(cfg.authors ?? const <SubscriptionAuthor>[]);
      if (authors.any((a) => a.secUid == p.secUid)) {
        if (!mounted) return;
        setState(() {
          _error = '已在对标列表';
          _submitting = false;
        });
        return;
      }
      authors.add(p.toSubscriptionAuthor(
        note: p.nickname,
        domain: _domain,
      ));
      await widget.client.putSubscriptions(
        SubscriptionsConfig(intervalHours: cfg.intervalHours, authors: authors),
      );
      // 与 web 一致：新关注后立即 tick 做首次采集（fire-and-forget）
      unawaited(widget.client.tickSubscriptions());
      if (!mounted) return;
      Navigator.of(context).pop(true);
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = '添加失败：$e';
        _submitting = false;
      });
    }
  }

  Future<void> _submitWork() async {
    final w = _work;
    if (w == null) return;
    final url = (w.shareUrl ?? '').trim();
    if (url.isEmpty) {
      setState(() => _error = '该作品没有可用分享链接');
      return;
    }
    setState(() => _submitting = true);
    try {
      await widget.client.createTask(
        cmd: 'shenkuo',
        params: <String, dynamic>{
          'aweme': url,
          'platform': w.platform,
          if (w.awemeId.isNotEmpty) 'source_url': url,
        },
      );
      if (!mounted) return;
      Navigator.of(context).pop(true);
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = '派采集失败：$e';
        _submitting = false;
      });
    }
  }

  bool get _canSubmit =>
      !_resolving &&
      !_submitting &&
      ((_kind == ShenkuoAddKind.account && _account != null) ||
          (_kind == ShenkuoAddKind.work && _work != null));

  @override
  Widget build(BuildContext context) {
    final bottom = MediaQuery.viewInsetsOf(context).bottom;
    return Padding(
      padding: EdgeInsets.only(bottom: bottom),
      child: Container(
        constraints: BoxConstraints(
          maxHeight: MediaQuery.sizeOf(context).height * 0.88,
        ),
        decoration: const BoxDecoration(
          color: AppColors.sand,
          borderRadius: BorderRadius.vertical(top: Radius.circular(16)),
        ),
        child: SafeArea(
          top: false,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const SizedBox(height: 10),
              Container(
                width: 36,
                height: 4,
                decoration: BoxDecoration(
                  color: AppColors.ink.withValues(alpha: 0.15),
                  borderRadius: BorderRadius.circular(2),
                ),
              ),
              Padding(
                padding: const EdgeInsets.fromLTRB(AppSpacing.pageH, 14, AppSpacing.pageH, 8),
                child: Row(
                  children: [
                    Text('添加原料', style: AppTypography.navTitle),
                    const Spacer(),
                    IconButton(
                      tooltip: '关闭',
                      onPressed: () => Navigator.of(context).pop(false),
                      icon: const Icon(Icons.close, size: 20),
                    ),
                  ],
                ),
              ),
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: AppSpacing.pageH),
                child: _KindTabs(
                  kind: _kind,
                  onSelect: (k) {
                    if (k == _kind) return;
                    setState(() {
                      _kind = k;
                      _account = null;
                      _work = null;
                      _error = null;
                    });
                    final raw = _text.text.trim();
                    if (raw.isNotEmpty && _containsUrl(raw)) {
                      _resolve(raw);
                    }
                  },
                ),
              ),
              Flexible(
                child: ListView(
                  shrinkWrap: true,
                  padding: const EdgeInsets.fromLTRB(
                    AppSpacing.pageH,
                    14,
                    AppSpacing.pageH,
                    AppSpacing.pageBottom,
                  ),
                  children: [
                    Text(
                      _kind == ShenkuoAddKind.account
                          ? '粘贴抖音/TK 创作者主页链接或口令，自动解析后加入对标。'
                          : '粘贴作品分享链接或整段分享文本，解析后派沈存中单链采集。',
                      style: AppTypography.caption.copyWith(color: AppColors.inkMuted),
                    ),
                    const SizedBox(height: AppSpacing.m),
                    TextField(
                      controller: _text,
                      minLines: 2,
                      maxLines: 4,
                      autofocus: true,
                      autocorrect: false,
                      enableSuggestions: false,
                      onChanged: _onTextChanged,
                      style: AppTypography.bodyInput,
                      decoration: InputDecoration(
                        hintText: _kind == ShenkuoAddKind.account
                            ? 'https://www.douyin.com/user/…'
                            : '作品分享链接 / 口令',
                        hintStyle: AppTypography.body.copyWith(color: AppColors.inkFaint),
                        filled: true,
                        fillColor: AppColors.ivory,
                        border: OutlineInputBorder(
                          borderRadius: BorderRadius.circular(12),
                          borderSide: BorderSide(color: AppColors.ink.withValues(alpha: 0.08)),
                        ),
                        enabledBorder: OutlineInputBorder(
                          borderRadius: BorderRadius.circular(12),
                          borderSide: BorderSide(color: AppColors.ink.withValues(alpha: 0.08)),
                        ),
                        focusedBorder: OutlineInputBorder(
                          borderRadius: BorderRadius.circular(12),
                          borderSide: const BorderSide(color: AppColors.accentCollect),
                        ),
                        contentPadding: const EdgeInsets.all(14),
                      ),
                    ),
                    Align(
                      alignment: Alignment.centerLeft,
                      child: TextButton.icon(
                        onPressed: _paste,
                        icon: Icon(Icons.content_paste, size: 16, color: AppColors.accentCollect),
                        label: Text(
                          '从剪贴板粘贴',
                          style: AppTypography.label.copyWith(color: AppColors.accentCollect),
                        ),
                      ),
                    ),
                    if (_resolving) ...[
                      const SizedBox(height: AppSpacing.s),
                      const Center(
                        child: SizedBox(
                          width: 22,
                          height: 22,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        ),
                      ),
                    ],
                    if (_error != null) ...[
                      const SizedBox(height: AppSpacing.s),
                      Text(_error!, style: AppTypography.caption.copyWith(color: AppColors.statusRed)),
                    ],
                    if (_account != null) ...[
                      const SizedBox(height: AppSpacing.m),
                      _AccountPreview(profile: _account!),
                      const SizedBox(height: AppSpacing.m),
                      Text('赛道', style: AppTypography.subhead.copyWith(color: AppColors.inkMuted)),
                      const SizedBox(height: AppSpacing.s),
                      Wrap(
                        spacing: 8,
                        children: [
                          for (final d in kDomainOptions)
                            ChoiceChip(
                              label: Text(d.label),
                              selected: _domain == d.key,
                              onSelected: (_) => setState(() => _domain = d.key),
                              selectedColor: AppColors.accentCollect.withValues(alpha: 0.2),
                              labelStyle: AppTypography.subhead.copyWith(
                                color: _domain == d.key ? AppColors.accentCollect : AppColors.ink,
                              ),
                            ),
                        ],
                      ),
                    ],
                    if (_work != null) ...[
                      const SizedBox(height: AppSpacing.m),
                      _WorkPreview(work: _work!),
                    ],
                    const SizedBox(height: AppSpacing.block),
                    DecisionButton(
                      label: _submitting
                          ? '处理中…'
                          : _kind == ShenkuoAddKind.account
                              ? '添加关注'
                              : '派采集任务',
                      tint: AppColors.accentCollect,
                      onPressed: _canSubmit ? _submit : () {},
                    ),
                    if (!_canSubmit && !_resolving && _account == null && _work == null) ...[
                      const SizedBox(height: AppSpacing.s),
                      Text(
                        '粘贴含链接的内容后会自动解析。',
                        style: AppTypography.caption.copyWith(color: AppColors.inkMuted),
                        textAlign: TextAlign.center,
                      ),
                    ],
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _KindTabs extends StatelessWidget {
  const _KindTabs({required this.kind, required this.onSelect});
  final ShenkuoAddKind kind;
  final ValueChanged<ShenkuoAddKind> onSelect;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        Expanded(
          child: _tab(
            label: '关注作者',
            selected: kind == ShenkuoAddKind.account,
            onTap: () => onSelect(ShenkuoAddKind.account),
          ),
        ),
        const SizedBox(width: 10),
        Expanded(
          child: _tab(
            label: '采集作品',
            selected: kind == ShenkuoAddKind.work,
            onTap: () => onSelect(ShenkuoAddKind.work),
          ),
        ),
      ],
    );
  }

  Widget _tab({required String label, required bool selected, required VoidCallback onTap}) {
    return GestureDetector(
      onTap: onTap,
      behavior: HitTestBehavior.opaque,
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 160),
        padding: const EdgeInsets.symmetric(vertical: 10),
        alignment: Alignment.center,
        decoration: ShapeDecoration(
          color: selected ? AppColors.accentCollect : AppColors.ivory,
          shape: const StadiumBorder(),
        ),
        child: Text(
          label,
          style: AppTypography.subhead.copyWith(
            fontWeight: FontWeight.w600,
            color: selected ? Colors.white : AppColors.ink.withValues(alpha: 0.65),
          ),
        ),
      ),
    );
  }
}

class _AccountPreview extends StatelessWidget {
  const _AccountPreview({required this.profile});
  final AccountResolveResult profile;

  @override
  Widget build(BuildContext context) {
    final p = profile;
    final handle = (p.uniqueId != null && p.uniqueId!.isNotEmpty) ? '@${p.uniqueId}' : null;
    return Container(
      padding: const EdgeInsets.all(AppSpacing.cardInner),
      decoration: BoxDecoration(
        color: AppColors.ivory,
        borderRadius: const BorderRadius.all(AppRadii.cardR),
        boxShadow: [BoxShadow(color: AppColors.cardShadow, blurRadius: 6, offset: const Offset(0, 3))],
      ),
      child: Row(
        children: [
          ClipOval(
            child: SizedBox(
              width: 52,
              height: 52,
              child: (p.avatar != null && p.avatar!.isNotEmpty)
                  ? Image.network(
                      p.avatar!,
                      fit: BoxFit.cover,
                      errorBuilder: (_, error, stackTrace) => _avatarFallback(p.nickname),
                    )
                  : _avatarFallback(p.nickname),
            ),
          ),
          const SizedBox(width: AppSpacing.m),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    TagChip(label: platformDisplayName(p.platform), tint: AppColors.inkFaint),
                    const SizedBox(width: 6),
                    Expanded(
                      child: Text(
                        p.nickname?.isNotEmpty == true ? p.nickname! : '已解析账号',
                        style: AppTypography.titleM,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                  ],
                ),
                if (handle != null) ...[
                  const SizedBox(height: 2),
                  Text(handle, style: AppTypography.caption.copyWith(color: AppColors.inkMuted)),
                ],
                if (p.followerCount != null || p.worksCount != null) ...[
                  const SizedBox(height: 4),
                  Text(
                    [
                      if (p.followerCount != null) '粉丝 ${shenkuoCount(p.followerCount!)}',
                      if (p.worksCount != null) '作品 ${shenkuoCount(p.worksCount!)}',
                    ].join(' · '),
                    style: AppTypography.caption.copyWith(color: AppColors.inkMuted),
                  ),
                ],
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _avatarFallback(String? name) {
    final ch = (name != null && name.isNotEmpty) ? name.characters.first : '作';
    return Container(
      color: AppColors.accentCollect.withValues(alpha: 0.16),
      alignment: Alignment.center,
      child: Text(ch, style: AppTypography.titleM.copyWith(color: AppColors.accentCollect)),
    );
  }
}

class _WorkPreview extends StatelessWidget {
  const _WorkPreview({required this.work});
  final WorkResolveResult work;

  @override
  Widget build(BuildContext context) {
    final w = work;
    final title = (w.title ?? '').trim();
    final author = w.author?['nickname'] as String?;
    return Container(
      padding: const EdgeInsets.all(AppSpacing.cardInner),
      decoration: BoxDecoration(
        color: AppColors.ivory,
        borderRadius: const BorderRadius.all(AppRadii.cardR),
        boxShadow: [BoxShadow(color: AppColors.cardShadow, blurRadius: 6, offset: const Offset(0, 3))],
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          ClipRRect(
            borderRadius: BorderRadius.circular(10),
            child: SizedBox(
              width: 64,
              height: 86,
              child: (w.coverUrl != null && w.coverUrl!.isNotEmpty)
                  ? Image.network(
                      w.coverUrl!,
                      fit: BoxFit.cover,
                      errorBuilder: (_, error, stackTrace) => _coverFallback(),
                    )
                  : _coverFallback(),
            ),
          ),
          const SizedBox(width: AppSpacing.m),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  title.isEmpty ? '作品 ${w.awemeId}' : title,
                  style: AppTypography.titleM,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                ),
                if (author != null && author.isNotEmpty) ...[
                  const SizedBox(height: 4),
                  Text(author, style: AppTypography.caption.copyWith(color: AppColors.inkMuted)),
                ],
                const SizedBox(height: 6),
                Wrap(
                  spacing: 8,
                  runSpacing: 4,
                  children: [
                    if (w.digg != null) _stat(Icons.favorite, w.digg!, AppColors.statusRed),
                    if (w.comment != null) _stat(Icons.mode_comment, w.comment!, AppColors.inkMuted),
                    if (w.collect != null) _stat(Icons.star, w.collect!, AppColors.amber),
                    TagChip(label: platformDisplayName(w.platform), tint: AppColors.inkFaint),
                    if (w.cached == true) TagChip(label: '缓存', tint: AppColors.statusAdopted),
                  ],
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _stat(IconData icon, int n, Color color) => Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, size: 11, color: color),
          const SizedBox(width: 2),
          Text(
            shenkuoCount(n),
            style: AppTypography.caption.copyWith(fontWeight: FontWeight.w600, color: color),
          ),
        ],
      );

  Widget _coverFallback() => Container(
        color: AppColors.ink.withValues(alpha: 0.06),
        alignment: Alignment.center,
        child: Icon(Icons.movie_outlined, color: AppColors.inkFaint),
      );
}

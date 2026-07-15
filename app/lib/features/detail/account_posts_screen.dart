// 对标账号作品列表 —— 与 web AccountWorksPage 同源:
// GET /accounts/{sec_uid}/posts 读 state/benchmark 已拉取作品 + collected 深采标记。
// app 决策视角:只读资料库,不建 final_preview 画布;可选「派采集」走 POST /tasks shenkuo。

import 'package:flutter/material.dart';

import '../../core/net/endpoint_resolver.dart';
import '../../core/net/factory_client.dart';
import '../../core/net/models.dart';
import '../../design/components/app_card.dart';
import '../../design/components/tag_chip.dart';
import '../../design/tokens.dart';
import '../../design/typography.dart';
import 'shenkuo_collect_panel.dart' show shenkuoCount;

/// 某对标号的作品列表(资料库视角)。
class AccountPostsScreen extends StatefulWidget {
  const AccountPostsScreen({
    super.key,
    required this.author,
    this.client,
  });

  final SubscriptionAuthor author;
  final FactoryClient? client;

  @override
  State<AccountPostsScreen> createState() => _AccountPostsScreenState();
}

class _AccountPostsScreenState extends State<AccountPostsScreen> {
  static const String _devBase = 'http://liuzhihao-mbp.local:8810';

  late final FactoryClient _client =
      widget.client ?? FactoryClient(resolver: DirectEndpointResolver(_devBase));

  List<AccountPost> _posts = const <AccountPost>[];
  bool _loading = true;
  String? _error;
  String? _dispatchingId; // 正在派采集的 aweme_id

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final a = widget.author;
      final list = await _client.accountPosts(
        a.secUid,
        platform: a.platformOrDouyin,
        uniqueId: a.uniqueId,
      );
      if (!mounted) return;
      setState(() {
        _posts = list;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = '$e';
        _loading = false;
      });
    }
  }

  /// 对未深采作品派一次 shenkuo 单链采集(决策视角入口,不进 web 画布)。
  Future<void> _dispatchCollect(AccountPost post) async {
    final url = post.shareUrl?.trim();
    if (url == null || url.isEmpty) {
      _toast('该作品没有可用分享链接');
      return;
    }
    if (_dispatchingId != null) return;
    setState(() => _dispatchingId = post.awemeId);
    try {
      await _client.createTask(
        cmd: 'shenkuo',
        params: <String, dynamic>{
          'aweme': url,
          'platform': widget.author.platformOrDouyin,
        },
      );
      if (!mounted) return;
      _toast('已派采集任务,回「决策」页验收');
    } catch (e) {
      if (!mounted) return;
      _toast('派发失败:$e');
    } finally {
      if (mounted) setState(() => _dispatchingId = null);
    }
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

  @override
  Widget build(BuildContext context) {
    final a = widget.author;
    return Scaffold(
      backgroundColor: AppColors.sand,
      appBar: AppBar(
        backgroundColor: AppColors.sand,
        foregroundColor: AppColors.ink,
        elevation: 0,
        scrolledUnderElevation: 0,
        surfaceTintColor: Colors.transparent,
        title: Text(a.displayName, style: AppTypography.navTitle),
        actions: [
          IconButton(
            tooltip: '刷新',
            icon: const Icon(Icons.refresh),
            onPressed: _loading ? null : _load,
          ),
        ],
      ),
      body: _body(),
    );
  }

  Widget _body() {
    if (_loading && _posts.isEmpty) {
      return const Center(child: CircularProgressIndicator());
    }
    if (_error != null && _posts.isEmpty) {
      return _centered(
        Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(_error!, style: AppTypography.body.copyWith(color: AppColors.statusRed), textAlign: TextAlign.center),
            const SizedBox(height: AppSpacing.m),
            TextButton(onPressed: _load, child: const Text('重试')),
          ],
        ),
      );
    }
    if (_posts.isEmpty) {
      return _centered(
        Text(
          '该账号还没有已拉取的作品。\n等沈存中巡查/深采后会列出(与 web 同源 benchmark)。',
          style: AppTypography.body.copyWith(color: AppColors.inkMuted),
          textAlign: TextAlign.center,
        ),
      );
    }
    return RefreshIndicator(
      onRefresh: _load,
      color: AppColors.accentCollect,
      backgroundColor: AppColors.ivory,
      child: ListView.builder(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.fromLTRB(
          AppSpacing.pageH,
          AppSpacing.s,
          AppSpacing.pageH,
          AppSpacing.pageBottom,
        ),
        itemCount: _posts.length + 1,
        itemBuilder: (context, i) {
          if (i == 0) {
            final collected = _posts.where((p) => p.collected).length;
            return Padding(
              padding: const EdgeInsets.only(bottom: AppSpacing.m),
              child: Text(
                '共 ${_posts.length} 条 · 已深采 $collected',
                style: AppTypography.caption.copyWith(color: AppColors.inkMuted),
              ),
            );
          }
          final post = _posts[i - 1];
          return Padding(
            padding: const EdgeInsets.only(bottom: AppSpacing.m),
            child: _PostCard(
              post: post,
              busy: _dispatchingId == post.awemeId,
              onCollect: post.collected ? null : () => _dispatchCollect(post),
            ),
          );
        },
      ),
    );
  }

  Widget _centered(Widget child) => ListView(
        physics: const AlwaysScrollableScrollPhysics(),
        children: [
          Padding(padding: const EdgeInsets.only(top: 96), child: Center(child: child)),
        ],
      );
}

class _PostCard extends StatelessWidget {
  const _PostCard({required this.post, required this.busy, this.onCollect});

  final AccountPost post;
  final bool busy;
  final VoidCallback? onCollect;

  @override
  Widget build(BuildContext context) {
    final desc = (post.desc ?? '').trim();
    return AppCard(
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          _Cover(url: post.coverUrl, duration: post.duration),
          const SizedBox(width: AppSpacing.m),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  desc.isEmpty ? '无标题 · ${post.awemeId}' : desc,
                  style: AppTypography.titleM,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                ),
                const SizedBox(height: AppSpacing.s),
                Wrap(
                  spacing: 8,
                  runSpacing: 4,
                  children: [
                    if (post.digg != null) _stat(Icons.favorite, post.digg!, AppColors.statusRed),
                    if (post.comment != null) _stat(Icons.mode_comment, post.comment!, AppColors.inkMuted),
                    if (post.share != null) _stat(Icons.reply, post.share!, AppColors.inkMuted),
                    if (post.collect != null) _stat(Icons.star, post.collect!, AppColors.amber),
                  ],
                ),
                const SizedBox(height: AppSpacing.s),
                Row(
                  children: [
                    TagChip(
                      label: post.collected ? '已深采' : '仅列表',
                      tint: post.collected ? AppColors.statusAdopted : AppColors.inkFaint,
                    ),
                    const Spacer(),
                    if (onCollect != null)
                      TextButton(
                        onPressed: busy ? null : onCollect,
                        child: Text(
                          busy ? '派发中…' : '派采集',
                          style: AppTypography.label.copyWith(color: AppColors.accentCollect),
                        ),
                      ),
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
}

class _Cover extends StatelessWidget {
  const _Cover({this.url, this.duration});
  final String? url;
  final int? duration;

  @override
  Widget build(BuildContext context) {
    final hasUrl = url != null && url!.isNotEmpty;
    return ClipRRect(
      borderRadius: BorderRadius.circular(10),
      child: SizedBox(
        width: 72,
        height: 96,
        child: Stack(
          fit: StackFit.expand,
          children: [
            if (hasUrl)
              Image.network(
                url!,
                fit: BoxFit.cover,
                errorBuilder: (_, error, stackTrace) => _placeholder(),
              )
            else
              _placeholder(),
            if (duration != null && duration! > 0)
              Positioned(
                right: 4,
                bottom: 4,
                child: Container(
                  padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 2),
                  decoration: BoxDecoration(
                    color: Colors.black.withValues(alpha: 0.55),
                    borderRadius: BorderRadius.circular(4),
                  ),
                  child: Text(
                    _fmtDur(duration!),
                    style: AppTypography.caption2.copyWith(color: Colors.white, fontWeight: FontWeight.w600),
                  ),
                ),
              ),
          ],
        ),
      ),
    );
  }

  Widget _placeholder() => Container(
        color: AppColors.ink.withValues(alpha: 0.06),
        alignment: Alignment.center,
        child: Icon(Icons.movie_outlined, size: 22, color: AppColors.inkFaint),
      );

  String _fmtDur(int sec) {
    final m = sec ~/ 60;
    final s = sec % 60;
    return '$m:${s.toString().padLeft(2, '0')}';
  }
}

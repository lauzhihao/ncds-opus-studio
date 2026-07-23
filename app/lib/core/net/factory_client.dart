import 'dart:async';
import 'dart:convert';

import 'package:dio/dio.dart';

import '../auth/session_store.dart';
import 'endpoint_resolver.dart';
import 'models.dart';

/// 工厂后端错误(对应 Swift NofError):HTTP 非 2xx / 解码 / 网络。
class FactoryError implements Exception {
  FactoryError(this.message, {this.statusCode});
  final String message;
  final int? statusCode;
  @override
  String toString() => message;
}

/// 取消(轮询重启/退出导航)不是故障,不该当「连不上」呈现。
bool isCancellation(Object error) =>
    error is DioException && error.type == DioExceptionType.cancel;

/// 工厂后端(ncds-opus-studio)客户端,从 iOS NofClient 移植。
/// LAN 直连;base URL 经 [EndpointResolver] 取得(默认直连已知别名,无 Bonjour)。
/// 鉴权:从 [SessionStore] 注入 `Authorization: Bearer <session>`(web 用 cookie)。
class FactoryClient {
  FactoryClient({required this.resolver, Dio? dio, SessionStore? session})
    // 连接超时:换网络后旧 IP 失活时快速失败,触发重解析自愈,而非无限挂起。
    // (不影响 SSE——长流用的是 receiveTimeout,见 events()。)
    : _dio =
          dio ?? (Dio()..options.connectTimeout = const Duration(seconds: 5)),
      _session = session ?? SessionStore.instance {
    // 每个 client 实例都挂一次,避免漏掉业务页自行 new 的路径。
    _dio.interceptors.add(
      InterceptorsWrapper(
        onRequest: (options, handler) {
          final token = _session.token;
          if (token != null && token.isNotEmpty) {
            options.headers['Authorization'] = 'Bearer $token';
          }
          handler.next(options);
        },
      ),
    );
  }

  final EndpointResolver resolver;
  final Dio _dio;
  final SessionStore _session;

  Future<String> _base() async =>
      (await resolver.resolveBase()).toString().replaceAll(RegExp(r'/+$'), '');

  Options get _json => Options(
    responseType: ResponseType.json,
    validateStatus: (_) => true, // 自己判 2xx,拿到 body 拼错误信息
  );

  Never _throwHttp(Response<dynamic> r) => throw FactoryError(
    'HTTP ${r.statusCode}: ${r.data}',
    statusCode: r.statusCode,
  );

  bool _ok(int? code) => code != null && code >= 200 && code < 300;

  // —— Auth(对齐 web /api/auth/*;mobile 走 Bearer 而非 cookie)——

  Future<AuthMeResponse> authMe() async {
    final r = await _get('/api/auth/me');
    return AuthMeResponse.fromJson((r.data as Map).cast<String, dynamic>());
  }

  /// POST /api/auth/mobile:{provider,id_token} → sessionToken + user。
  Future<MobileLoginResponse> mobileLogin({
    required String provider,
    required String idToken,
  }) async {
    final r = await _post(
      '/api/auth/mobile',
      body: <String, dynamic>{'provider': provider, 'id_token': idToken},
    );
    return MobileLoginResponse.fromJson(
      (r.data as Map).cast<String, dynamic>(),
    );
  }

  Future<void> logout() async {
    await _post('/api/auth/logout');
  }

  Future<Response<dynamic>> _get(String path) async {
    final base = await _base();
    try {
      final r = await _dio.get<dynamic>('$base$path', options: _json);
      if (!_ok(r.statusCode)) _throwHttp(r);
      return r;
    } on DioException catch (e) {
      if (!isCancellation(e)) {
        resolver.invalidate(); // 连不上→作废解析缓存,下次重解析(IP 变了时自愈)
      }
      throw FactoryError('网络错误: ${e.message ?? e.type.name}');
    }
  }

  Future<Response<dynamic>> _post(String path, {Object? body}) async {
    final base = await _base();
    try {
      final r = await _dio.post<dynamic>(
        '$base$path',
        data: body,
        options: _json.copyWith(
          contentType: body != null ? Headers.jsonContentType : null,
        ),
      );
      if (!_ok(r.statusCode)) _throwHttp(r);
      return r;
    } on DioException catch (e) {
      if (!isCancellation(e)) {
        resolver.invalidate(); // 连不上→作废解析缓存,下次重解析(IP 变了时自愈)
      }
      throw FactoryError('网络错误: ${e.message ?? e.type.name}');
    }
  }

  Future<Response<dynamic>> _put(String path, {Object? body}) async {
    final base = await _base();
    try {
      final r = await _dio.put<dynamic>(
        '$base$path',
        data: body,
        options: _json.copyWith(
          contentType: body != null ? Headers.jsonContentType : null,
        ),
      );
      if (!_ok(r.statusCode)) _throwHttp(r);
      return r;
    } on DioException catch (e) {
      if (!isCancellation(e)) {
        resolver.invalidate(); // 连不上→作废解析缓存,下次重解析(IP 变了时自愈)
      }
      throw FactoryError('网络错误: ${e.message ?? e.type.name}');
    }
  }

  // —— 短语音 ASR(安卓系统听写不可用时的兜底)——

  /// POST /asr:上传 WAV(16kHz / 16-bit / 单声道 PCM),服务端调阿里云「一句话识别」,
  /// 同步返回识别文字。契约:multipart 字段 audio = WAV 字节;响应 { "text": "..." }。
  Future<String> transcribe(List<int> wavBytes) async {
    final base = await _base();
    try {
      final form = FormData.fromMap(<String, dynamic>{
        'audio': MultipartFile.fromBytes(wavBytes, filename: 'reject.wav'),
      });
      final r = await _dio.post<dynamic>(
        '$base/asr',
        data: form,
        options: Options(
          responseType: ResponseType.json,
          validateStatus: (_) => true,
          sendTimeout: const Duration(seconds: 20),
          receiveTimeout: const Duration(seconds: 20),
        ),
      );
      if (!_ok(r.statusCode)) _throwHttp(r);
      final data = r.data;
      return (data is Map ? data['text'] as String? : null) ?? '';
    } on DioException catch (e) {
      if (!isCancellation(e)) resolver.invalidate();
      throw FactoryError('转写失败: ${e.message ?? e.type.name}');
    }
  }

  // —— Tasks ——

  /// GET /tasks。逐条容错:坏记录丢弃,不放大成整页报错;全损则报解码错误(非空列表)。
  Future<List<TaskMeta>> listTasks() async {
    final r = await _get('/tasks');
    final list = (r.data as List?) ?? const <dynamic>[];
    final ok = <TaskMeta>[];
    for (final e in list) {
      try {
        ok.add(TaskMeta.fromJson((e as Map).cast<String, dynamic>()));
      } catch (_) {
        /* 丢弃脏数据 */
      }
    }
    if (list.isNotEmpty && ok.isEmpty) {
      throw FactoryError('/tasks 共 ${list.length} 条全部解码失败');
    }
    return ok;
  }

  Future<TaskDetail> task(String id) async {
    final r = await _get('/tasks/$id');
    return TaskDetail.fromJson((r.data as Map).cast<String, dynamic>());
  }

  Future<String> createTask({
    required String cmd,
    required Map<String, dynamic> params,
  }) async {
    final r = await _post(
      '/tasks',
      body: <String, dynamic>{'cmd': cmd, 'params': params},
    );
    return TaskCreateResponse.fromJson(
      (r.data as Map).cast<String, dynamic>(),
    ).taskId;
  }

  Future<void> cancelTask(String id) => _post('/tasks/$id/cancel');

  Future<void> restoreTask(String id) => _post('/tasks/$id/restore');

  /// POST /tasks/{id}/review。note_origin=machine 时标注模板生成,不冒充人工语料。
  Future<NofReview> review(
    String id, {
    required String decision,
    String? note,
    String? noteOrigin,
  }) async {
    final body = <String, dynamic>{'decision': decision};
    if (note != null && note.isNotEmpty) body['note'] = note;
    if (noteOrigin != null) body['note_origin'] = noteOrigin;
    final r = await _post('/tasks/$id/review', body: body);
    return NofReview.fromJson((r.data as Map).cast<String, dynamic>());
  }

  /// DELETE /tasks/{id}/review:撤销决策(幂等)。
  Future<void> revokeReview(String id) async {
    final base = await _base();
    try {
      final r = await _dio.delete<dynamic>(
        '$base/tasks/$id/review',
        options: _json,
      );
      if (!_ok(r.statusCode)) _throwHttp(r);
    } on DioException catch (e) {
      if (!isCancellation(e)) {
        resolver.invalidate(); // 连不上→作废解析缓存,下次重解析(IP 变了时自愈)
      }
      throw FactoryError('网络错误: ${e.message ?? e.type.name}');
    }
  }

  /// 产物相对 URL(/artifacts/...)绝对化,供图片/播放器直用。
  Future<Uri?> absoluteUrl(String relative) async {
    if (relative.startsWith('http')) return Uri.tryParse(relative);
    return Uri.tryParse('${await _base()}$relative');
  }

  // —— Commands / Schema ——

  /// GET /commands。响应是 { commands: [...] },取 commands 字段;逐条容错丢脏数据。
  Future<List<NofCommand>> listCommands() async {
    final r = await _get('/commands');
    final list = ((r.data as Map?)?['commands'] as List?) ?? const <dynamic>[];
    final ok = <NofCommand>[];
    for (final e in list) {
      try {
        ok.add(NofCommand.fromJson((e as Map).cast<String, dynamic>()));
      } catch (_) {
        /* 丢弃脏数据 */
      }
    }
    return ok;
  }

  /// GET /commands/{cmd}/schema:某 agent 的表单字段定义。
  Future<CommandSchema> schema(String cmd) async {
    final r = await _get('/commands/$cmd/schema');
    return CommandSchema.fromJson((r.data as Map).cast<String, dynamic>());
  }

  // —— Rounds(卧龙战报页)——

  /// GET /rounds。逐条容错 + 全损报错(同 listTasks)。后端按文件名倒序返回,
  /// 这里统一按 created_at 降序重排——别依赖文件名排序这种实现细节。
  Future<List<RoundSummary>> rounds() async {
    final r = await _get('/rounds');
    final list = (r.data as List?) ?? const <dynamic>[];
    final ok = <RoundSummary>[];
    for (final e in list) {
      try {
        ok.add(RoundSummary.fromJson((e as Map).cast<String, dynamic>()));
      } catch (_) {
        /* 丢弃脏数据 */
      }
    }
    if (list.isNotEmpty && ok.isEmpty) {
      throw FactoryError('/rounds 共 ${list.length} 条全部解码失败');
    }
    ok.sort((a, b) => (b.createdAt ?? '').compareTo(a.createdAt ?? ''));
    return ok;
  }

  /// GET /rounds/{id}:round 文件全量(战报/产线;events 本期不建模)。
  Future<RoundDetail> round(String id) async {
    final r = await _get('/rounds/$id');
    return RoundDetail.fromJson((r.data as Map).cast<String, dynamic>());
  }

  /// POST /rounds/{id}/terminate:显式止损,终止本轮并清场(在途任务取消)。
  Future<void> terminateRound(String id) => _post('/rounds/$id/terminate');

  // —— Subscriptions(订阅管理页)——

  /// GET /subscriptions:订阅配置全量。
  Future<SubscriptionsConfig> subscriptions() async {
    final r = await _get('/subscriptions');
    return SubscriptionsConfig.fromJson(
      (r.data as Map).cast<String, dynamic>(),
    );
  }

  /// PUT /subscriptions:整体覆盖写。务必传「GET 拿回后就地修改」的全量配置——
  /// authors 为 null/缺省会被后端当成空表,把订阅清光。返回回写后的全量配置。
  Future<SubscriptionsConfig> putSubscriptions(SubscriptionsConfig cfg) async {
    final r = await _put('/subscriptions', body: cfg.toJson());
    return SubscriptionsConfig.fromJson(
      (r.data as Map).cast<String, dynamic>(),
    );
  }

  /// POST /subscriptions/tick:手动触发一轮派发(调试/演示)。返回实际派发任务数。
  Future<int> tickSubscriptions() async {
    final r = await _post('/subscriptions/tick');
    if (r.data is! Map) return 0;
    return SubscriptionsTickResponse.fromJson(
          (r.data as Map).cast<String, dynamic>(),
        ).submitted ??
        0;
  }

  // —— Accounts / Works(与 web 同源:作者库 + benchmark + 作品仓库)——
  // 沈存中「对标号」资料库视角读这些;决策验收仍走 /tasks。

  /// GET /subscriptions?domain= 可选赛道过滤(与 web 首页 domain tab 同源)。
  Future<SubscriptionsConfig> subscriptionsFiltered({String? domain}) async {
    final qs = (domain != null && domain.isNotEmpty)
        ? '?domain=${Uri.encodeQueryComponent(domain)}'
        : '';
    final r = await _get('/subscriptions$qs');
    return SubscriptionsConfig.fromJson(
      (r.data as Map).cast<String, dynamic>(),
    );
  }

  /// GET /accounts/{sec_uid}/posts:对标号作品列表(benchmark + collected 标记)。
  Future<List<AccountPost>> accountPosts(
    String secUid, {
    String platform = 'douyin',
    String? uniqueId,
  }) async {
    final q = <String, String>{'platform': platform};
    if (uniqueId != null && uniqueId.isNotEmpty) q['unique_id'] = uniqueId;
    final qs = q.entries
        .map((e) => '${e.key}=${Uri.encodeQueryComponent(e.value)}')
        .join('&');
    final path = '/accounts/${Uri.encodeComponent(secUid)}/posts?$qs';
    final r = await _get(path);
    final list = ((r.data as Map?)?['posts'] as List?) ?? const <dynamic>[];
    final ok = <AccountPost>[];
    for (final e in list) {
      try {
        ok.add(AccountPost.fromJson((e as Map).cast<String, dynamic>()));
      } catch (_) {
        /* 丢弃脏数据 */
      }
    }
    return ok;
  }

  /// POST /accounts/resolve:主页链接/口令 → 账号档案(作者库缓存)。
  Future<AccountResolveResult> resolveAccount(String text) async {
    final r = await _post(
      '/accounts/resolve',
      body: <String, dynamic>{'text': text},
    );
    return AccountResolveResult.fromJson(
      (r.data as Map).cast<String, dynamic>(),
    );
  }

  /// POST /works/resolve:作品分享链接 → 作品卡(作品仓库缓存)。
  Future<WorkResolveResult> resolveWork(String text) async {
    final r = await _post(
      '/works/resolve',
      body: <String, dynamic>{'text': text},
    );
    return WorkResolveResult.fromJson((r.data as Map).cast<String, dynamic>());
  }

  // —— SSE 进度流 ——
  // 按行读 text/event-stream,取 data: 行解析 TaskEvent;收到 [DONE] 结束。对应 Swift events()。

  Stream<TaskEvent> events(String id) async* {
    final base = await _base();
    final r = await _dio.get<ResponseBody>(
      '$base/tasks/$id/events',
      options: Options(
        responseType: ResponseType.stream,
        headers: <String, dynamic>{
          'Accept': 'text/event-stream',
          // interceptor 也会补 Bearer;显式再写一次防个别平台丢自定义 header。
          ..._session.authHeaders,
        },
        receiveTimeout: const Duration(hours: 1), // 长任务进度间隔可能 >60s
      ),
    );
    final lines = r.data!.stream
        .cast<List<int>>()
        .transform(utf8.decoder)
        .transform(const LineSplitter());
    await for (final line in lines) {
      if (!line.startsWith('data:')) continue;
      final payload = line.substring(5).trim();
      if (payload == '[DONE]') break;
      try {
        yield TaskEvent.fromJson(jsonDecode(payload) as Map<String, dynamic>);
      } catch (_) {
        /* 跳过坏行 */
      }
    }
  }
}

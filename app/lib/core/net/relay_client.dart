import 'package:dio/dio.dart';

import '../native/live_activity_service.dart' show ClaudeStatus;
import 'relay_config.dart';

/// 读取中继上的「真实 Claude 状态」(Mac agent 上报 → 中继 /v1/state)。
/// 这是主灯/灵动岛该显示的状态;与工厂后端 /tasks(各 agent 任务态)是两回事。
class RelayClient {
  // connectTimeout 必须显式设:默认不限时,手机连不上中继时 connect 会挂到系统级
  // 超时(几十秒),拖住调用方;5s 内连不上就放弃,保留上次状态。
  RelayClient({Dio? dio})
      : _dio = dio ?? (Dio()..options.connectTimeout = const Duration(seconds: 5));

  final Dio _dio;

  /// GET /v1/state(Bearer 鉴权)→ {"state":"R"/"Y"/"G","updatedAt":...}。
  /// 失败/无法解析返回 null(调用方保留上次状态,单次抖动不灭灯)。
  Future<ClaudeStatus?> fetchState() async {
    try {
      final r = await _dio.get<dynamic>(
        '${RelayConfig.url}/v1/state',
        options: Options(
          responseType: ResponseType.json,
          headers: <String, dynamic>{'Authorization': 'Bearer ${RelayConfig.apiToken}'},
          sendTimeout: const Duration(seconds: 6),
          receiveTimeout: const Duration(seconds: 6),
          validateStatus: (c) => c != null && c >= 200 && c < 300,
        ),
      );
      final data = r.data;
      final state = data is Map ? data['state'] as String? : null;
      return state == null ? null : ClaudeStatus.fromWire(state);
    } catch (_) {
      return null;
    }
  }
}

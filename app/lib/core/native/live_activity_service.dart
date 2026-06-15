// iOS 专有原生能力(灵动岛 / Live Activity)的「可换接口」抽象。
//
// 原生 UI(锁屏/灵动岛/Approve-Deny)保留在 iOS 工程的 Swift 里单独维护,
// 见 ClaudeLiveActivity.swift / AppIntents.swift / ClaudeAttributes.swift。
// Dart 侧只通过 MethodChannel 驱动它:start / update / end。
//
// 风格对齐 lib/core/net/ 的 EndpointResolver——抽象接口 + 默认实现 + 可替换,
// 上层业务不感知具体通道。Android / 其它平台无此能力,默认实现自动降级为 no-op。

import 'dart:io' show Platform;

import 'package:flutter/services.dart';

/// Claude 状态,对齐 iOS `ClaudeAttributes.ContentState.state` 的单字母 wire code。
/// 按「语义」命名,避免「灯色 vs 状态」混淆(原生灯位与字母并不直觉):
/// - [idle]     → `"G"` → 空闲,绿灯常亮(Idle)
/// - [thinking] → `"R"` → 推理中,琥珀灯呼吸(Thinking)
/// - [asking]   → `"Y"` → 等待批准,红灯闪烁(Asking)
enum ClaudeStatus {
  idle('G'),
  thinking('R'),
  asking('Y');

  const ClaudeStatus(this.wireCode);

  /// 发给 iOS ActivityKit 的状态字符串(`G` / `R` / `Y`)。
  final String wireCode;

  /// 从 wire code 反解;未知值回落 [idle](最保守)。
  static ClaudeStatus fromWire(String? code) {
    for (final s in ClaudeStatus.values) {
      if (s.wireCode == code) return s;
    }
    return ClaudeStatus.idle;
  }
}

/// Live Activity / 灵动岛驱动接口(可换实现)。
///
/// 默认用 [MethodChannelLiveActivityService];非 iOS 平台或原生未接线时,
/// 调用都安静降级(不抛异常),让跨端代码无需到处写平台分支。
/// 测试 / 其它平台可注入 [NoopLiveActivityService]。
abstract class LiveActivityService {
  /// 当前平台是否可能支持 Live Activity。仅 iOS 返回 true;
  /// 真正能否开启还取决于系统设置与 iOS 版本,以原生侧 `areActivitiesEnabled` 为准。
  bool get isSupported;

  /// 开启一个 Live Activity(灵动岛 + 锁屏胶囊)。
  ///
  /// - [status] 初始红绿灯状态。
  /// - [text] 可选附加文案(预留:当前原生 UI 由 status 推导固定文案,
  ///   传入值原样转交,未来原生可改用)。
  /// - [requestId] 可选待批准请求 id;有值时原生在「等待」态展示 Approve/Deny。
  ///
  /// 重复调用语义由原生决定(通常已存在 Activity 时改走 update)。
  Future<void> start({
    required ClaudeStatus status,
    String? text,
    String? requestId,
  });

  /// 更新已开启 Live Activity 的内容。无活动时由原生静默忽略。
  Future<void> update({
    required ClaudeStatus status,
    String? text,
    String? requestId,
  });

  /// 结束 Live Activity。无活动时静默忽略。
  Future<void> end();
}

/// 默认实现:经 [MethodChannel] 把调用转交给 iOS 原生 ActivityKit。
///
/// 通道名 `claude_traffic_light/live_activity`,原生侧在 AppDelegate(或自定义 plugin)
/// 注册同名 handler,把 start/update/end 映射到 `Activity.request` / `.update` / `.end`。
/// 接线步骤见 docs/IOS_NATIVE_BRIDGE.md。
class MethodChannelLiveActivityService implements LiveActivityService {
  MethodChannelLiveActivityService({MethodChannel? channel})
      : _channel = channel ?? const MethodChannel(channelName);

  /// 与原生约定的通道名;改这里要同步改 Swift 侧。
  static const String channelName = 'claude_traffic_light/live_activity';

  final MethodChannel _channel;

  @override
  bool get isSupported => Platform.isIOS;

  @override
  Future<void> start({
    required ClaudeStatus status,
    String? text,
    String? requestId,
  }) =>
      _invoke('start', status: status, text: text, requestId: requestId);

  @override
  Future<void> update({
    required ClaudeStatus status,
    String? text,
    String? requestId,
  }) =>
      _invoke('update', status: status, text: text, requestId: requestId);

  @override
  Future<void> end() => _invoke('end');

  /// 统一收口:非 iOS 直接 no-op;原生未注册 handler(MissingPluginException)
  /// 或调用失败(PlatformException)都静默降级,不让灵动岛缺失拖垮主流程。
  Future<void> _invoke(
    String method, {
    ClaudeStatus? status,
    String? text,
    String? requestId,
  }) async {
    if (!isSupported) return;
    final args = <String, dynamic>{
      if (status != null) 'status': status.wireCode,
      'text': ?text,
      'requestId': ?requestId,
    };
    try {
      await _channel.invokeMethod<void>(method, args.isEmpty ? null : args);
    } on MissingPluginException {
      // 原生侧还没接线(Widget Extension / handler 未配),静默忽略。
    } on PlatformException {
      // 原生执行失败(系统未授权 Live Activity 等),不上抛到业务层。
    }
  }
}

/// 空实现:任何平台都安静成功。用于测试、Android,或显式关闭该能力。
class NoopLiveActivityService implements LiveActivityService {
  const NoopLiveActivityService();

  @override
  bool get isSupported => false;

  @override
  Future<void> start({
    required ClaudeStatus status,
    String? text,
    String? requestId,
  }) async {}

  @override
  Future<void> update({
    required ClaudeStatus status,
    String? text,
    String? requestId,
  }) async {}

  @override
  Future<void> end() async {}
}

/// 推送 token 注册可换接口。
///
/// 平台差异藏在实现里:iOS = APNs deviceToken,Android = FCM token,鸿蒙 = Push Kit。
/// 上层只调 [register];后端按平台分发各自的下发链路。当前远程推送基础设施已配、未启用,
/// 默认用 [NoopPushRegistrar] 占位。
abstract class PushRegistrar {
  Future<void> register();
}

class NoopPushRegistrar implements PushRegistrar {
  const NoopPushRegistrar();

  @override
  Future<void> register() async {
    // 暂未启用远程推送。
  }
}

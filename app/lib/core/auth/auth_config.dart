/// OAuth 客户端公开配置(client id 本身不是 secret)。
///
/// 与后端 `.env` 对齐:
/// - [googleWebClientId] = GOOGLE_CLIENT_ID(用于 id_token aud / serverClientId)
/// - [googleIosClientId] = GOOGLE_IOS_CLIENT_ID(可选;未配时 iOS 隐藏 Google 按钮)
/// - [appleBundleId] = APPLE_BUNDLE_ID
class AuthConfig {
  AuthConfig._();

  /// 后端 LAN 默认地址(与各页面 DirectEndpointResolver 一致)。
  static const String factoryBaseUrl = 'http://liuzhihao-mbp.local:8810';

  /// Web OAuth client —— 同时作 mobile id_token 的 server audience。
  static const String googleWebClientId =
      '514836945888-6lnp93jgm4f2t1unle7h48kt1beojsbh.apps.googleusercontent.com';

  /// iOS native OAuth client(Google Cloud 控制台「iOS」类型)。
  /// 空字符串 = 未配置;AuthController 在 iOS 上隐藏 Google 按钮。
  static const String googleIosClientId = '';

  static const String appleBundleId = 'com.claudelight.claudeTrafficLight';
}

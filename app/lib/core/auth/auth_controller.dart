import 'dart:io' show Platform;

import 'package:flutter/foundation.dart';
import 'package:google_sign_in/google_sign_in.dart';
import 'package:sign_in_with_apple/sign_in_with_apple.dart';

import '../net/endpoint_resolver.dart';
import '../net/factory_client.dart';
import 'auth_config.dart';
import 'session_store.dart';

/// 登录门闸状态:对齐 web AuthGate 的 authRequired / authenticated。
class AuthController extends ChangeNotifier {
  AuthController({FactoryClient? client})
    : _client =
          client ??
          FactoryClient(
            resolver: DirectEndpointResolver(AuthConfig.factoryBaseUrl),
          );

  final FactoryClient _client;
  final SessionStore _session = SessionStore.instance;

  bool _booting = true;
  bool _busy = false;
  bool authRequired = false;
  bool googleEnabled = false;
  bool appleEnabled = false;
  String? error;
  bool _googleInitialized = false;

  bool get booting => _booting;
  bool get busy => _busy;
  AuthUser? get user => _session.user;
  bool get authenticated => _session.isSignedIn;
  FactoryClient get client => _client;

  /// iOS 上 Google 需要独立 iOS client id;未配时不展示 Google 按钮。
  bool get showGoogleButton {
    if (!googleEnabled) return false;
    if (Platform.isIOS || Platform.isMacOS) {
      return AuthConfig.googleIosClientId.isNotEmpty;
    }
    return AuthConfig.googleWebClientId.isNotEmpty;
  }

  bool get showAppleButton {
    if (!appleEnabled) return false;
    // Apple 原生登录只在 Apple 平台可靠。
    return Platform.isIOS || Platform.isMacOS;
  }

  Future<void> bootstrap() async {
    _booting = true;
    error = null;
    notifyListeners();
    try {
      await _session.load();
      final status = await _client.authMe();
      authRequired = status.authRequired;
      googleEnabled = status.providers.google;
      appleEnabled = status.providers.apple;

      if (!authRequired) {
        // 本地未开鉴权:直接放行(与 web 一致)。
        return;
      }

      if (status.authenticated && status.user != null) {
        // /me 用 Bearer 校验通过;同步服务端用户信息。
        final token = _session.token;
        if (token != null) {
          await _session.save(token: token, user: status.user!);
        }
        return;
      }

      // token 失效或不存在。
      if (_session.isSignedIn) {
        await _session.clear();
      }
    } on FactoryError catch (e) {
      // 后端不可达时:若本地有 session 先放行进首页,业务请求会再失败并自愈。
      // 完全无 session 且能确认 authRequired 时才挡登录。
      if (!_session.isSignedIn) {
        // /me 也 401 不该发生(白名单);网络错误则保守不挡死。
        if (e.statusCode == null) {
          authRequired = false;
        } else {
          authRequired = true;
        }
      }
      error = e.message;
    } catch (e) {
      if (!_session.isSignedIn) authRequired = false;
      error = '$e';
    } finally {
      _booting = false;
      notifyListeners();
    }
  }

  Future<void> signInWithApple() async {
    await _runLogin(() async {
      final credential = await SignInWithApple.getAppleIDCredential(
        scopes: [
          AppleIDAuthorizationScopes.email,
          AppleIDAuthorizationScopes.fullName,
        ],
      );
      final idToken = credential.identityToken;
      if (idToken == null || idToken.isEmpty) {
        throw FactoryError('Apple 未返回 identityToken');
      }
      await _exchange(provider: 'apple', idToken: idToken);
    });
  }

  Future<void> signInWithGoogle() async {
    await _runLogin(() async {
      await _ensureGoogleInitialized();
      final account = await GoogleSignIn.instance.authenticate();
      final idToken = account.authentication.idToken;
      if (idToken == null || idToken.isEmpty) {
        throw FactoryError(
          'Google 未返回 idToken(请确认 serverClientId = GOOGLE_CLIENT_ID)',
        );
      }
      await _exchange(provider: 'google', idToken: idToken);
    });
  }

  Future<void> logout() async {
    _busy = true;
    notifyListeners();
    try {
      try {
        await _client.logout();
      } catch (_) {
        // 服务端 session 已失效也清本地
      }
      await _session.clear();
      try {
        if (_googleInitialized) await GoogleSignIn.instance.signOut();
      } catch (_) {}
      // 强制回登录门闸(即便 bootstrap 时 authRequired 被误判)。
      authRequired = true;
      error = null;
    } finally {
      _busy = false;
      notifyListeners();
    }
  }

  /// 业务 API 401 时:清 session 并回到登录门闸。
  Future<void> onUnauthorized() async {
    await _session.clear();
    authRequired = true;
    notifyListeners();
  }

  Future<void> _exchange({
    required String provider,
    required String idToken,
  }) async {
    final res = await _client.mobileLogin(provider: provider, idToken: idToken);
    await _session.save(token: res.sessionToken, user: res.user);
  }

  Future<void> _ensureGoogleInitialized() async {
    if (_googleInitialized) return;
    final iosId = AuthConfig.googleIosClientId.trim();
    await GoogleSignIn.instance.initialize(
      clientId: iosId.isEmpty ? null : iosId,
      serverClientId: AuthConfig.googleWebClientId,
    );
    _googleInitialized = true;
  }

  Future<void> _runLogin(Future<void> Function() body) async {
    _busy = true;
    error = null;
    notifyListeners();
    try {
      await body();
    } on SignInWithAppleAuthorizationException catch (e) {
      if (e.code != AuthorizationErrorCode.canceled) {
        error = 'Apple 登录失败: ${e.message}';
      }
    } on GoogleSignInException catch (e) {
      if (e.code != GoogleSignInExceptionCode.canceled) {
        error = 'Google 登录失败: ${e.description ?? e.code.name}';
      }
    } on FactoryError catch (e) {
      error = e.message;
    } catch (e) {
      error = '$e';
    } finally {
      _busy = false;
      notifyListeners();
    }
  }
}

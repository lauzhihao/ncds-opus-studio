import 'dart:convert';

import 'package:shared_preferences/shared_preferences.dart';

/// 本地持久化的工厂 session(对应服务端 nof_session / Bearer token)。
class AuthUser {
  const AuthUser({
    required this.id,
    required this.provider,
    required this.email,
    this.name,
    this.pictureUrl,
  });

  final int id;
  final String provider;
  final String email;
  final String? name;
  final String? pictureUrl;

  factory AuthUser.fromJson(Map<String, dynamic> j) => AuthUser(
    id: (j['id'] as num?)?.toInt() ?? 0,
    provider: (j['provider'] as String?) ?? '',
    email: (j['email'] as String?) ?? '',
    name: j['name'] as String?,
    pictureUrl: (j['pictureUrl'] as String?) ?? j['picture_url'] as String?,
  );

  Map<String, dynamic> toJson() => <String, dynamic>{
    'id': id,
    'provider': provider,
    'email': email,
    if (name != null) 'name': name,
    if (pictureUrl != null) 'pictureUrl': pictureUrl,
  };

  String get displayLabel {
    final n = name?.trim();
    if (n != null && n.isNotEmpty) return n;
    return email;
  }
}

/// GET /api/auth/me
class AuthMeResponse {
  const AuthMeResponse({
    required this.authRequired,
    required this.authenticated,
    this.user,
    this.providers = const AuthProviders(),
  });

  final bool authRequired;
  final bool authenticated;
  final AuthUser? user;
  final AuthProviders providers;

  factory AuthMeResponse.fromJson(Map<String, dynamic> j) {
    final p = j['providers'];
    return AuthMeResponse(
      authRequired: j['authRequired'] as bool? ?? false,
      authenticated: j['authenticated'] as bool? ?? false,
      user: j['user'] is Map
          ? AuthUser.fromJson((j['user'] as Map).cast<String, dynamic>())
          : null,
      providers: p is Map
          ? AuthProviders.fromJson(p.cast<String, dynamic>())
          : const AuthProviders(),
    );
  }
}

class AuthProviders {
  const AuthProviders({this.google = false, this.apple = false});

  final bool google;
  final bool apple;

  factory AuthProviders.fromJson(Map<String, dynamic> j) => AuthProviders(
    google: j['google'] as bool? ?? false,
    apple: j['apple'] as bool? ?? false,
  );
}

/// POST /api/auth/mobile
class MobileLoginResponse {
  const MobileLoginResponse({
    required this.sessionToken,
    required this.user,
    this.expiresInDays,
  });

  final String sessionToken;
  final AuthUser user;
  final int? expiresInDays;

  factory MobileLoginResponse.fromJson(Map<String, dynamic> j) {
    final token = (j['sessionToken'] as String?) ?? '';
    final userMap = j['user'];
    if (token.isEmpty || userMap is! Map) {
      throw FormatException('mobile login response missing sessionToken/user');
    }
    return MobileLoginResponse(
      sessionToken: token,
      user: AuthUser.fromJson(userMap.cast<String, dynamic>()),
      expiresInDays: (j['expiresInDays'] as num?)?.toInt(),
    );
  }
}

/// 单例:所有 [FactoryClient] 实例共享同一 token,经 interceptor 注入 Bearer。
class SessionStore {
  SessionStore._();
  static final SessionStore instance = SessionStore._();

  static const _kToken = 'nof_session_token';
  static const _kUser = 'nof_session_user';

  String? _token;
  AuthUser? _user;
  bool _loaded = false;

  String? get token => _token;
  AuthUser? get user => _user;
  bool get isSignedIn => _token != null && _token!.isNotEmpty;

  /// Image.network / VideoPlayer 等原生加载器需要的 header。
  Map<String, String> get authHeaders {
    final t = _token;
    if (t == null || t.isEmpty) return const <String, String>{};
    return <String, String>{'Authorization': 'Bearer $t'};
  }

  Future<void> load() async {
    if (_loaded) return;
    final prefs = await SharedPreferences.getInstance();
    _token = prefs.getString(_kToken);
    final raw = prefs.getString(_kUser);
    if (raw != null && raw.isNotEmpty) {
      try {
        _user = AuthUser.fromJson(jsonDecode(raw) as Map<String, dynamic>);
      } catch (_) {
        _user = null;
      }
    }
    _loaded = true;
  }

  Future<void> save({required String token, required AuthUser user}) async {
    _token = token;
    _user = user;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_kToken, token);
    await prefs.setString(_kUser, jsonEncode(user.toJson()));
  }

  Future<void> clear() async {
    _token = null;
    _user = null;
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove(_kToken);
    await prefs.remove(_kUser);
  }
}

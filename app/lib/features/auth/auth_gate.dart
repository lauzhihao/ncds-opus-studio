import 'package:flutter/material.dart';

import '../../core/auth/auth_controller.dart';
import '../../design/tokens.dart';
import 'login_screen.dart';

/// 向下暴露 [AuthController],供首页齿轮菜单等调用 logout。
class AuthScope extends InheritedNotifier<AuthController> {
  const AuthScope({
    super.key,
    required AuthController auth,
    required super.child,
  }) : super(notifier: auth);

  static AuthController of(BuildContext context) {
    final scope = context.dependOnInheritedWidgetOfExactType<AuthScope>();
    assert(scope != null, 'AuthScope not found');
    return scope!.notifier!;
  }

  static AuthController? maybeOf(BuildContext context) {
    return context.dependOnInheritedWidgetOfExactType<AuthScope>()?.notifier;
  }
}

/// 根门闸:鉴权开启且未登录时只显示 [LoginScreen];否则渲染 [child]。
class AuthGate extends StatefulWidget {
  const AuthGate({super.key, required this.child, this.auth});

  final Widget child;
  final AuthController? auth;

  @override
  State<AuthGate> createState() => _AuthGateState();
}

class _AuthGateState extends State<AuthGate> {
  late final AuthController _auth = widget.auth ?? AuthController();

  @override
  void initState() {
    super.initState();
    _auth.addListener(_onAuth);
    _auth.bootstrap();
  }

  @override
  void dispose() {
    _auth.removeListener(_onAuth);
    // 仅在本 gate 自建 controller 时 dispose。
    if (widget.auth == null) {
      _auth.dispose();
    }
    super.dispose();
  }

  void _onAuth() {
    if (mounted) setState(() {});
  }

  @override
  Widget build(BuildContext context) {
    Widget body;
    if (_auth.booting) {
      body = const Scaffold(
        backgroundColor: AppColors.sand,
        body: Center(
          child: SizedBox(
            width: 28,
            height: 28,
            child: CircularProgressIndicator(
              strokeWidth: 2.4,
              color: AppColors.orange,
            ),
          ),
        ),
      );
    } else if (_auth.authRequired && !_auth.authenticated) {
      body = LoginScreen(auth: _auth);
    } else {
      body = widget.child;
    }
    return AuthScope(auth: _auth, child: body);
  }
}

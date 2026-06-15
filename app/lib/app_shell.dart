import 'package:flutter/material.dart';

import 'design/tokens.dart';
import 'features/inbox/task_list_screen.dart';
import 'features/rounds/rounds_screen.dart';
import 'features/subscriptions/subscriptions_screen.dart';

/// 底部导航外壳:收件箱 / 战报 / 订阅。各页自带 AppBar(嵌套 Scaffold),
/// 用 IndexedStack 保留各 tab 的滚动与加载状态。
class AppShell extends StatefulWidget {
  const AppShell({super.key});

  @override
  State<AppShell> createState() => _AppShellState();
}

class _AppShellState extends State<AppShell> {
  int _tab = 0;

  static const List<Widget> _pages = <Widget>[
    TaskListScreen(),
    RoundsScreen(),
    SubscriptionsScreen(),
  ];

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: IndexedStack(index: _tab, children: _pages),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _tab,
        onDestinationSelected: (i) => setState(() => _tab = i),
        backgroundColor: AppColors.ivory,
        destinations: const <NavigationDestination>[
          NavigationDestination(icon: Icon(Icons.inbox_outlined), selectedIcon: Icon(Icons.inbox), label: '收件箱'),
          NavigationDestination(icon: Icon(Icons.assessment_outlined), selectedIcon: Icon(Icons.assessment), label: '战报'),
          NavigationDestination(icon: Icon(Icons.notifications_outlined), selectedIcon: Icon(Icons.notifications), label: '订阅'),
        ],
      ),
    );
  }
}

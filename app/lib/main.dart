import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'design/app_theme.dart';
import 'features/home/agent_home.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  // 锁竖屏:全 UI 按竖屏单列设计(英雄区 + 卡片墙 + 详情),横屏会被拉伸变形。
  // 安卓默认随传感器旋转,且系统旋转锁定对未声明朝向的 App 不可靠——这里显式钉死竖屏。
  await SystemChrome.setPreferredOrientations(const [DeviceOrientation.portraitUp]);
  runApp(const ClaudeTrafficLightApp());
}

class ClaudeTrafficLightApp extends StatelessWidget {
  const ClaudeTrafficLightApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: '能成大事',
      debugShowCheckedModeBanner: false,
      theme: AppTheme.light,
      themeMode: ThemeMode.light,
      home: const AgentHome(),
    );
  }
}

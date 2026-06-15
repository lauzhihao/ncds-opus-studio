import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:claude_traffic_light/core/net/models.dart';
import 'package:claude_traffic_light/design/app_theme.dart';
import 'package:claude_traffic_light/features/inbox/task_list_screen.dart';

void main() {
  testWidgets('收件箱:空态渲染(注入空数据,免网络)', (WidgetTester tester) async {
    await tester.pumpWidget(MaterialApp(
      theme: AppTheme.light,
      home: TaskListScreen(tasksLoader: () async => <TaskMeta>[]),
    ));
    await tester.pumpAndSettle();
    expect(find.text('收件箱'), findsOneWidget);
    expect(find.text('收件箱空空如也'), findsOneWidget);
  });

  testWidgets('收件箱:渲染任务卡与状态标签', (WidgetTester tester) async {
    final tasks = <TaskMeta>[
      const TaskMeta(taskId: 't1', cmd: 'liuyong', status: 'done', title: '一条测试文案', decision: 'approved'),
      const TaskMeta(taskId: 't2', cmd: 'wolong', status: 'running', source: 'cron'),
    ];
    await tester.pumpWidget(MaterialApp(
      theme: AppTheme.light,
      home: TaskListScreen(tasksLoader: () async => tasks),
    ));
    await tester.pumpAndSettle();
    expect(find.text('一条测试文案'), findsOneWidget);
    expect(find.text('已采用'), findsOneWidget);
    expect(find.text('生成中'), findsOneWidget);
    expect(find.text('自动排产'), findsOneWidget); // source 角标
  });
}

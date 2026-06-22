# 能成大事 Flutter App

这是 `ncds-opus-studio` 的移动决策控制台，不是 `/studio` web 画布的替代品。

当前定位：

- 首页展示 Claude 主灯 + 6 个中国风 agent 卡片墙（卧龙 / 沈括 / 鬼谷子 / 柳永 / 吴道子 / 伯牙）。
- app 通过工厂后端 `/tasks` 查看任务收件箱、提交 agent / primitive 任务、订阅 SSE 进度、审看产物并提交同意/拒绝。
- 主灯 / 灵动岛的真实 Claude 状态走 relay；agent 卡片灯态从 `/tasks` 聚合。
- `/instances` 生产引擎 API 已存在于后端，但 app 目前还没有切过去。

## 本地运行

先补两个 gitignored 配置文件，否则编译会失败：

```bash
cp lib/core/net/relay_config.example.dart lib/core/net/relay_config.dart
cp ios/Shared/RelayConfig.example.swift ios/Shared/RelayConfig.swift
```

启动：

```bash
flutter devices
flutter run -d <device>
```

交付前检查：

```bash
flutter analyze
flutter test
```

默认开发后端是 `http://liuzhihao-mbp.local:8810`。手机和 Mac 需要在同一局域网；换网络后 mDNS / endpoint resolver 会尝试自愈。

## 代码入口

- `lib/main.dart`：app 入口，当前 home 是 `AgentHome`。
- `lib/core/net/factory_client.dart`：工厂后端客户端，当前主路径是 `/tasks`、`/commands`、`/artifacts`、`/rounds`、`/subscriptions`。
- `lib/core/net/relay_client.dart`：Claude 状态 relay 客户端，与工厂任务态分开。
- `lib/features/home/`：主灯与 agent 卡片墙。
- `lib/features/inbox/`：按 agent 分桶的任务收件箱。
- `lib/features/detail/`：任务详情、产物审看、review 操作。
- `lib/features/rounds/`、`subscriptions/`：卧龙战报与订阅管理。

更细的工程约定见 [app/CLAUDE.md](CLAUDE.md)。

# CLAUDE.md

本文件指导 Claude Code 在本仓库工作。约定与事实以本文件为准。

## 项目是什么

Flutter App「能成大事」—— 一个内容工厂(`ncds-opus-studio` 后端)的移动控制台。

- 首页 = Claude 主灯(红绿灯语义:Idle 绿常亮 / Thinking 琥珀呼吸 / Asking 红快闪)+ 6 个中国风 agent 卡片墙(卧龙 / 沈括 / 鬼谷子 / 柳永 / 吴道子 / 伯牙)。每个 agent 进去是它的任务收件箱 → 任务详情。
- 灵动岛 / 锁屏 Live Activity 走 iOS Widget Extension(MethodChannel 驱动)。
- 仍在把原生 iOS App「ClaudeTrafficLight」迁到 Flutter。**iOS 参考代码在 `../ClaudeTrafficLight`**(SwiftUI)——迁页面 / 对样式时务必对照它,数值与动效尽量 1:1 复刻。

## 沟通与注释(沿用现有风格)

- 用**简体中文**交流、分析、解释方案;技术术语(`Widget`、`State`、`BackdropFilter`、`SSE`、`mDNS` 等)保留英文。
- 代码注释用中文,解释**为什么**这么写,而不是复述代码做了什么。本仓库既有注释都是这个风格,保持一致。
- 控制台 / 日志输出避免 emoji 与特殊 Unicode。

## 怎么跑

- `flutter devices` 看设备;`flutter run -d <device>` 起 App。交付前 `flutter analyze`(目标零 warning)+ `flutter test` 必须通过。
- dev 后端 LAN 直连 `http://liuzhihao-mbp.local:8810`(mDNS 机器名,换 WiFi / 换地方自愈;手机与 Mac 需同网段)。
- **新拉代码 / 新建 worktree 后,必须先补两个被 gitignore 的密钥配置**,否则编译失败:
  - `cp lib/core/net/relay_config.example.dart lib/core/net/relay_config.dart`
  - `cp ios/Shared/RelayConfig.example.swift ios/Shared/RelayConfig.swift`
- 装真机(release):
  - Android(release 用 debug keystore 签名,可直接装):`flutter build apk --release && flutter install --release -d <id>`。小米等机型需在手机「开发者选项」打开「USB 安装」,否则报 `INSTALL_FAILED_USER_RESTRICTED`。
  - iOS(自动签名,team `Z3LULFMC72`):`flutter build ios --release && flutter install --release -d <id>`。

## 代码结构（lib/）

- `core/` —— 非 UI。`net/`(`FactoryClient` 工厂后端 / `RelayClient` 中继真实 Claude 状态 / `lan_host_resolver` 安卓 mDNS 自解析 / `models.dart` 数据模型)、`native/`(灵动岛 MethodChannel)、`push/`、`clipboard/`。
- `design/` —— 设计系统。`tokens.dart`(`AppColors` / `AppSpacing` / `AppRadii`)、`typography.dart`(`AppTypography`)、`components/`(`AppCard` / `TagChip` / `DecisionButton` / `TrafficBulb` / `StatusLight` 等)、`liquid_glass.dart`(自绘玻璃 `LiquidGlass`,已造好但尚未接入业务页面)。
- `features/` —— 按页面分。`home/`(主灯 + agent 卡片墙 + `agent_catalog`)、`inbox/`(任务列表,含 agent 主题化的 `AgentTaskListScreen`)、`detail/`(任务详情 + 各 agent 专属结果面板)、`compose/`、`rounds/`、`subscriptions/`、`demo/`(设计系统展示页)。
- 入口 `main.dart` → `home: AgentHome()`,全局锁竖屏。

## 约定（务必遵守）

- **不硬编码视觉值**:颜色用 `AppColors.*`、间距 `AppSpacing.*`、圆角 `AppRadii.*`、字体 `AppTypography.*`。需要新值时先往 token 里加,别就地写 `Color(0x...)` 或魔数。
- **忠实复刻 iOS**:迁移页面 / 组件时对照 `../ClaudeTrafficLight` 的 SwiftUI,padding / 字号 / 动效 period 等尽量 1:1,并在行尾注释标注对应关系(如 `// 对齐 iOS BreathingDot`)。
- **agent 主题色**:6 个 agent 各有 accent(`AppColors.accentWolong` 等),其卡片 / 分桶 / 按钮 / 头像统一用各自 accent。
- **后端模型容错**:网络模型字段名跟后端 JSON(snake_case),解码字段全部可空、缺键不炸 UI(参考 `models.dart` 现有写法)。
- 详情页 agent 身份由 `detail.cmd` 自查 `agentCatalog` 得到,不靠上游传参。

## 派发子任务的模型选择（成本优化）

- **haiku**:文件操作、执行明确命令、格式化、简单搜索。
- **sonnet**:代码分析、调试、需要推理的中等任务。
- **opus**:深度推理、架构设计、创造性工作。

默认优先低档,必要时再升级。

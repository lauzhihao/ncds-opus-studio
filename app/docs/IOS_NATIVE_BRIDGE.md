# iOS 专有原生能力接入(灵动岛 / Live Activity / 主屏 Widget / App Intent)

本文档是 **手动接入交接说明**。Dart 侧的可换接口已经写好(`lib/core/native/live_activity_service.dart`),
但下面这些步骤必须在 **Xcode 里手工完成**(新增 target、配置 capability、签名 entitlements),
纯文本/脚本无法可靠生成,所以这里把每一步写清楚交给做 iOS 的人。

> 设计原则:**原生 UI(锁屏/灵动岛/Approve-Deny)是 iOS-only,单独用 Swift 维护。**
> Dart 只通过 MethodChannel `claude_traffic_light/live_activity` 驱动它(start/update/end)。
> 跨端主体走 Flutter,这块不进 Flutter 的渲染管线。

---

## 0. 现状与目标

- Flutter 工程:`/Users/liuzhihao/Documents/claude_traffic_light_flutter`,iOS 壳在 `ios/`。
- 现有 iOS 原生工程(待迁入的 Swift 源):`ClaudeTrafficLight`(主 App)+ `ClaudeTrafficLightWidget`(Widget Extension)。
- 需要迁入 Flutter `ios/` 的 4 个 Swift 文件:
  | 文件 | 作用 | 归属 target |
  | --- | --- | --- |
  | `ClaudeAttributes.swift` | Live Activity 的 `ActivityAttributes` 状态模型(`R`/`Y`/`G` + quota + pending) | **主 App + Widget Extension 两个 target** |
  | `ClaudeLiveActivity.swift` | 灵动岛 / 锁屏 UI | Widget Extension |
  | `StatusIconWidget.swift` | 主屏小组件(自拉中继 `GET /v1/state`) | Widget Extension |
  | `AppIntents.swift` | Approve / Deny 的 `LiveActivityIntent`(不解锁后台执行) | **主 App + Widget Extension 两个 target** |

  另外这些 Swift 文件依赖 `RelayConfig`(中继地址 + apiToken)与 `Fonts/`(Fraunces 字体),迁移时一并带上(见 §4、§7)。

> ⚠️ `ClaudeAttributes.swift` 与 `AppIntents.swift` 必须 **同时** 属于主 App 与扩展两个 target 的
> Target Membership,否则编译期 `Activity<ClaudeAttributes>` / `ApproveIntent` 在某一侧找不到符号。

---

## 1. Bundle ID 对齐(动手前先做,否则 App Group / 推送全错)

| | 现 iOS 原生工程 | 现 Flutter 工程(`ios/Runner.xcodeproj`) |
| --- | --- | --- |
| 主 App bundle id | `com.liuzhihao.claudetrafficlight` | `com.claudelight.claudeTrafficLight`(占位 org `com.claudelight`) |

二者不一致。接 App Group / APNs 前 **必须先定一个**。两条路任选:

- **路线 A(推荐,沿用既有推送/中继注册):** 把 Flutter 工程改成 `com.liuzhihao.claudetrafficlight`。
  - 在 Xcode 选 `Runner` target → Signing & Capabilities / Build Settings,把 `PRODUCT_BUNDLE_IDENTIFIER`
    从 `com.claudelight.claudeTrafficLight` 改为 `com.liuzhihao.claudetrafficlight`。
  - 对应改 `ios/Runner.xcodeproj/project.pbxproj` 里 Debug/Release/Profile 三处(测试 target 的
    `...RunnerTests` 后缀也跟着改前缀)。
- **路线 B:** 维持 `com.claudelight.claudeTrafficLight`,则需要在 Apple Developer 后台新建 App ID、
  重配 APNs key/证书、并把中继侧(`/v1/register`)对应关系一起迁。成本更高。

**Widget Extension 的 bundle id 必须是主 App 的子级**,例如:

```
主 App:           com.liuzhihao.claudetrafficlight
Widget Extension: com.liuzhihao.claudetrafficlight.ClaudeTrafficLightWidget
```

> 选定后,本文后续示例统一用 `com.liuzhihao.claudetrafficlight`(路线 A)。走路线 B 请整篇替换。

---

## 2. 新增 Widget Extension target

1. Xcode 打开 `ios/Runner.xcworkspace`(**不是** `.xcodeproj`,Flutter 用 CocoaPods 必须开 workspace)。
2. 菜单 `File → New → Target…` → 选 **Widget Extension**。
   - Product Name:`ClaudeTrafficLightWidget`(与原生工程同名,迁文件最省事)。
   - **勾选** “Include Live Activity”。
   - **不要** 勾 “Include Configuration App Intent”(我们用自己的 `LiveActivityIntent`)。
   - Embed in Application:选 `Runner`。
3. Xcode 会自动生成模板文件(`ClaudeTrafficLightWidget.swift`、`*Bundle.swift`、`Info.plist`、`Assets`)。
   把模板里的示例 widget/attributes 删掉,改用迁入的真实文件(下一步)。
4. 在 `Runner` target → Build Phases → `Embed App Extensions` 里确认扩展已被嵌入(步骤 2 通常自动加好)。

---

## 3. 迁入 4 个 Swift 文件并设 Target Membership

1. 把 §0 表里的 4 个 `.swift` 拖进 Xcode 项目导航器(放在 `Runner/` 或新建分组皆可)。
   拖入时弹窗 **不要** 勾 “Copy items if needed” 时要确认路径——建议复制进 `ios/` 目录树内,避免引用工程外文件。
2. 选中每个文件,右侧 File Inspector → **Target Membership** 按下表勾选:

   | 文件 | Runner(主 App) | ClaudeTrafficLightWidget(扩展) |
   | --- | :---: | :---: |
   | `ClaudeAttributes.swift` | ✅ | ✅ |
   | `AppIntents.swift` | ✅ | ✅ |
   | `ClaudeLiveActivity.swift` | ⬜ | ✅ |
   | `StatusIconWidget.swift` | ⬜ | ✅ |
   | `RelayConfig.swift`(见 §7) | ✅ | ✅ |

3. 扩展的 `*Bundle.swift`(`@main struct ...Bundle: WidgetBundle`)里登记 widget:

   ```swift
   @main
   struct ClaudeTrafficLightWidgetBundle: WidgetBundle {
       var body: some Widget {
           ClaudeLiveActivity()   // 灵动岛 / 锁屏
           StatusIconWidget()     // 主屏小组件
       }
   }
   ```

> `RelayConfig.example.swift` 是模板,**不要** 加入任何 target(否则与 `RelayConfig.swift` enum 重名编译报错)。

---

## 4. Widget Extension 的 Info.plist(Live Activity + 字体)

1. 主 App 的 `ios/Runner/Info.plist` 增加:

   ```xml
   <key>NSSupportsLiveActivities</key>
   <true/>
   ```

   (可选)若要允许频繁更新,再加 `NSSupportsLiveActivitiesFrequentUpdates` = `true`。

2. `ClaudeLiveActivity.swift` / `StatusIconWidget.swift` 用了 Fraunces 字体。把 `Fonts/Fraunces-Bold.ttf`
   `Fraunces-Regular.ttf` 加进 **扩展 target**,并在 **扩展的 Info.plist** 注册:

   ```xml
   <key>UIAppFonts</key>
   <array>
     <string>Fraunces-Bold.ttf</string>
     <string>Fraunces-Regular.ttf</string>
   </array>
   ```

   > Flutter 工程已在 `pubspec.yaml` 用 `assets/fonts/Fraunces-*.ttf` 注册了同款字体,但那只对
   > Flutter 渲染层有效;Widget Extension 是独立进程,必须单独把 ttf 打进扩展并在扩展 Info.plist 注册。

3. `StatusIconWidget.swift` 用到名为 `IconBase` 的图片(主屏组件底图),把它加进 **扩展的 Assets.xcassets**。

---

## 5. App Group(主 App ↔ 扩展共享数据)

主 App 与扩展是两个进程,要共享状态(例如把 Dart 推来的 status 落地给 Widget 读,或反向回传)需 App Group。

1. Apple Developer 后台 → Identifiers → App Groups,新建一个,例如:
   ```
   group.com.liuzhihao.claudetrafficlight
   ```
2. 在 Xcode 给 **Runner** 和 **ClaudeTrafficLightWidget** 两个 target 都加 capability：
   Signing & Capabilities → `+ Capability` → **App Groups** → 勾上同一个 group。
3. 这会在各自的 `.entitlements` 写入:

   ```xml
   <key>com.apple.security.application-groups</key>
   <array>
     <string>group.com.liuzhihao.claudetrafficlight</string>
   </array>
   ```

   - `Runner.entitlements`(可能需新建)和扩展的 `.entitlements` 各一份,group 名要一致。
4. 代码里读写共享数据用 `UserDefaults(suiteName: "group.com.liuzhihao.claudetrafficlight")` 或
   `FileManager.containerURL(forSecurityApplicationGroupIdentifier:)`。

> 当前 `StatusIconWidget` 是自己拉中继 `GET /v1/state` 取状态,**不依赖** App Group;
> 但 Live Activity 的 push token 注册、或想让主屏组件复用 App 已知状态,就需要 App Group。先配好备用。

---

## 6. 推送能力(ActivityKit push,需真机)

Live Activity 走 `pushType: .token`(见原生 `ContentView.start()`),由中继下发 APNs 更新内容。

1. 给 **Runner** target 加 capability：Signing & Capabilities → `+ Capability` → **Push Notifications**。
   会写入 `Runner.entitlements`：

   ```xml
   <key>aps-environment</key>
   <string>development</string>   <!-- 上架前改 production -->
   ```

   (现 iOS 原生工程的 `ClaudeTrafficLight.entitlements` 就是这一条,照搬即可。)
2. Apple Developer 后台为该 App ID 开启 Push Notifications,配 APNs Auth Key / 证书,交给中继侧用于
   `liveactivity` topic（`<bundleId>.push-type.liveactivity`)。
3. push token 由原生 `Activity.pushTokenUpdates` 拿到后 `POST /v1/register`(Bearer 鉴权)上报中继——
   这套逻辑已在原生 `ContentView`，迁移时一并带上。

---

## 7. RelayConfig(中继地址 + apiToken)

`AppIntents.swift`(Approve/Deny 回传)和 `StatusIconWidget.swift`(拉状态)都引用 `RelayConfig`。

1. 复制模板:`cp RelayConfig.example.swift RelayConfig.swift`，填入真实 `apiToken`、中继 `urls`。
2. `RelayConfig.swift` 加入 **Runner + 扩展两个 target**;`RelayConfig.example.swift` 不加任何 target。
3. `RelayConfig.swift` 含真实 token,确认在 `.gitignore`(原生工程已忽略)。
4. 中继是 HTTPS 正规证书(`apn.vooice.tech` / `/v1` API),无需 ATS 例外。

---

## 8. 注册 MethodChannel handler(把 Dart 的 start/update/end 接到 ActivityKit)

Dart 侧通道名 = `MethodChannelLiveActivityService.channelName` = `claude_traffic_light/live_activity`。
在 `ios/Runner/AppDelegate.swift` 注册同名 handler。注意现在的 `AppDelegate` 用的是新版隐式引擎
(`FlutterImplicitEngineDelegate` / `didInitializeImplicitFlutterEngine`),拿 BinaryMessenger 要从那里取。

参数约定(与 Dart 一致):
- `method`：`"start"` / `"update"` / `"end"`。
- `arguments`：`{"status": "R"|"Y"|"G", "text": String?, "requestId": String?}`(`end` 无参)。
- `status` 是 wire code,直接喂给 `ClaudeAttributes.ContentState.state`。

```swift
import Flutter
import UIKit
import ActivityKit

@main
@objc class AppDelegate: FlutterAppDelegate, FlutterImplicitEngineDelegate {
  override func application(
    _ application: UIApplication,
    didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?
  ) -> Bool {
    return super.application(application, didFinishLaunchingWithOptions: launchOptions)
  }

  func didInitializeImplicitFlutterEngine(_ engineBridge: FlutterImplicitEngineBridge) {
    GeneratedPluginRegistrant.register(with: engineBridge.pluginRegistry)

    // 取隐式引擎的 BinaryMessenger 注册 Live Activity 通道。
    let messenger = engineBridge.applicationFlutterEngine.binaryMessenger
    let channel = FlutterMethodChannel(
      name: "claude_traffic_light/live_activity",
      binaryMessenger: messenger
    )
    channel.setMethodCallHandler { call, result in
      Task { @MainActor in
        await self.handleLiveActivity(call, result: result)
      }
    }
  }

  // MARK: - Live Activity 桥接(对齐 lib/core/native/live_activity_service.dart)

  // 已开启的活动句柄。重复 start 时改走 update,end 后置空。
  private var activity: Activity<ClaudeAttributes>?

  @MainActor
  private func handleLiveActivity(_ call: FlutterMethodCall, result: @escaping FlutterResult) async {
    let args = call.arguments as? [String: Any]
    let stateCode = (args?["status"] as? String) ?? "G"

    switch call.method {
    case "start":
      guard ActivityAuthorizationInfo().areActivitiesEnabled else {
        // 系统未授权 Live Activity:抛 PlatformException,Dart 侧已 catch 静默降级。
        result(FlutterError(code: "unavailable",
                            message: "Live Activities disabled in Settings", details: nil))
        return
      }
      let content = ClaudeAttributes.ContentState(state: stateCode, updatedAt: .now)
      // 已存在则更新,避免重复开
      if let act = activity ?? Activity<ClaudeAttributes>.activities.first {
        activity = act
        await act.update(.init(state: content, staleDate: nil))
      } else {
        do {
          activity = try Activity.request(
            attributes: ClaudeAttributes(name: "Claude Code"),
            content: .init(state: content, staleDate: nil),
            pushType: .token   // 推送驱动更新;token 走 §6 上报中继
          )
        } catch {
          result(FlutterError(code: "start_failed", message: "\(error)", details: nil))
          return
        }
      }
      result(nil)

    case "update":
      let content = ClaudeAttributes.ContentState(state: stateCode, updatedAt: .now)
      let act = activity ?? Activity<ClaudeAttributes>.activities.first
      await act?.update(.init(state: content, staleDate: nil))
      result(nil)   // 无活动也回成功:Dart 侧把 update 当幂等

    case "end":
      let act = activity ?? Activity<ClaudeAttributes>.activities.first
      await act?.end(nil, dismissalPolicy: .immediate)
      activity = nil
      result(nil)

    default:
      result(FlutterMethodNotImplemented)   // Dart 侧映射为 MissingPluginException → 静默
    }
  }
}
```

> - `text` / `requestId` 参数当前原生 UI 由 `state` 推导固定文案,这里未用;
>   若要在「等待」态(`Y`)显示 Approve/Deny,就用 `requestId` 构造
>   `ClaudeAttributes.ContentState.Pending(id:tool:preview:)` 一起塞进 ContentState。
> - **Approve / Deny 保留原生 `LiveActivityIntent`(`AppIntents.swift`),不要走 MethodChannel。**
>   它要在锁屏/灵动岛上不解锁后台执行(直接 `POST /v1/command` 回中继),Flutter 引擎那时可能没在跑,
>   MethodChannel 不可靠;按钮内嵌在 SwiftUI 里由系统直接触发 Intent。

---

## 9. Dart 侧用法(已就绪,无需改动)

```dart
import 'package:claude_traffic_light/core/native/live_activity_service.dart';

final LiveActivityService liveActivity = MethodChannelLiveActivityService();

await liveActivity.start(status: ClaudeStatus.green);          // 开
await liveActivity.update(status: ClaudeStatus.amber,          // 等待批准
                          requestId: 'req-123');
await liveActivity.end();                                      // 结束
```

- 非 iOS / 原生未接线 → 全部安静 no-op(`isSupported == false` 或捕获 `MissingPluginException`)。
- 测试或显式关闭:注入 `const NoopLiveActivityService()`。

---

## 10. 重要提醒(交接给主控 / iOS 维护者)

- **模拟器支持有限**:灵动岛仅 iPhone 15 Pro 系列模拟器可见;**ActivityKit push（`pushType: .token`)需真机**。
  CI / 纯文本环境无法验证 UI,这块按 iOS-only 单独验收。
- **不要把原生 UI 搬进 Flutter 渲染层**:`ClaudeLiveActivity` 是系统快照渲染(跑不了呼吸/循环动画),
  和主 App 的 Flutter UI 是两套,各自维护。
- **bundle id / App Group / APNs 三者绑死**:§1 选定 bundle id 之前不要配 §5、§6,否则要返工。
- **wire code 别搞反**:`R`=推理(显示黄灯)、`Y`=等待(显示红灯)、`G`=空闲(绿灯)。
  Dart 枚举名按语义(`red`/`amber`/`green`),`wireCode` 才是发给原生的字符串,两侧都以
  `ClaudeAttributes.ContentState.state` 为准。
- 本次改动只新增了 Flutter 工程的 `lib/core/native/live_activity_service.dart` 与本文档,
  **未触碰 iOS 工程**;上面所有 Xcode 步骤都还没做,需要在真机 + Apple 账号环境下手工执行。

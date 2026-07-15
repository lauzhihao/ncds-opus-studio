import ActivityKit
import Flutter
import Foundation

/// Dart ⇄ ActivityKit 桥:start / update / end 灵动岛 Live Activity。
/// 频道名与 Dart 侧 LiveActivityService 对齐。本期走「本地驱动」(主 App 通过
/// MethodChannel 直接 update),不接 APNs 推送(无需 push 能力/entitlement)。
/// Approve/Deny 由扩展进程内的 LiveActivityIntent 直发中继,不经此频道。
enum LiveActivityChannel {
  static let channelName = "claude_traffic_light/live_activity"

  static func register(messenger: FlutterBinaryMessenger) {
    let channel = FlutterMethodChannel(name: channelName, binaryMessenger: messenger)
    channel.setMethodCallHandler { call, result in
      guard #available(iOS 16.2, *) else {
        result(FlutterError(code: "unsupported", message: "Live Activities 需 iOS 16.2+", details: nil))
        return
      }
      let args = call.arguments as? [String: Any] ?? [:]
      switch call.method {
      case "start": Self.start(args, result)
      case "update": Self.update(args, result)
      case "end": Self.end(result)
      default: result(FlutterMethodNotImplemented)
      }
    }
  }

  @available(iOS 16.2, *)
  private static func contentState(from args: [String: Any]) -> ClaudeAttributes.ContentState {
    let state = args["status"] as? String ?? "G"
    var quota: ClaudeAttributes.ContentState.Quota?
    if let q = args["quota"] as? [String: Any] {
      quota = .init(
        tokens5h: q["tokens5h"] as? Int ?? 0,
        tokens7d: q["tokens7d"] as? Int ?? 0,
        updatedAt: q["updatedAt"] as? Int)
    }
    var pending: ClaudeAttributes.ContentState.Pending?
    if let p = args["pending"] as? [String: Any],
       let id = p["id"] as? String, let tool = p["tool"] as? String {
      pending = .init(id: id, tool: tool, preview: p["preview"] as? String)
    }
    return .init(state: state, updatedAt: Date(), quota: quota, pending: pending)
  }

  /// 是否已在观察 push token(每进程一次,避免重复注册)。
  private static var observingToken = false

  @available(iOS 16.2, *)
  private static func start(_ args: [String: Any], _ result: @escaping FlutterResult) {
    guard ActivityAuthorizationInfo().areActivitiesEnabled else {
      result(FlutterError(code: "disabled",
                          message: "实时活动未开启(设置 → 能成大事 → 实时活动)", details: nil))
      return
    }
    let cs = contentState(from: args)
    Task {
      do {
        let activity: Activity<ClaudeAttributes>
        if let existing = Activity<ClaudeAttributes>.activities.first {
          // 已有则复用并更新,不重复创建。
          await existing.update(ActivityContent(state: cs, staleDate: nil))
          activity = existing
        } else {
          // pushType: .token —— 拿 APNs push token,让中继(持 .p8)能在后台直接推状态,
          // App 关掉灵动岛也实时。对应原生 ContentView.start。
          activity = try Activity.request(
            attributes: ClaudeAttributes(name: "Agent"),
            content: ActivityContent(state: cs, staleDate: nil),
            pushType: .token)
        }
        observePushToken(activity)
        result(activity.id)
      } catch {
        result(FlutterError(code: "start_failed", message: error.localizedDescription, details: nil))
      }
    }
  }

  /// 观察 Live Activity 的 push token,拿到即注册到中继。对应原生 ContentView.observe。
  @available(iOS 16.2, *)
  private static func observePushToken(_ activity: Activity<ClaudeAttributes>) {
    guard !observingToken else { return }
    observingToken = true
    Task {
      for await tokenData in activity.pushTokenUpdates {
        let token = tokenData.map { String(format: "%02x", $0) }.joined()
        await registerToken(token)
      }
    }
  }

  /// POST /v1/register(Bearer)把 LA push token 注册到中继。对应原生 ContentView.register。
  private static func registerToken(_ token: String) async {
    for base in RelayConfig.urls {
      guard let url = URL(string: "\(base)/v1/register") else { continue }
      var req = URLRequest(url: url)
      req.httpMethod = "POST"
      req.timeoutInterval = 6
      req.setValue("application/json", forHTTPHeaderField: "Content-Type")
      req.setValue("Bearer \(RelayConfig.apiToken)", forHTTPHeaderField: "Authorization")
      req.httpBody = try? JSONSerialization.data(withJSONObject: ["deviceToken": token])
      if let (_, resp) = try? await URLSession.shared.data(for: req),
         (resp as? HTTPURLResponse)?.statusCode == 200 {
        return
      }
    }
  }

  @available(iOS 16.2, *)
  private static func update(_ args: [String: Any], _ result: @escaping FlutterResult) {
    let cs = contentState(from: args)
    Task {
      for activity in Activity<ClaudeAttributes>.activities {
        await activity.update(ActivityContent(state: cs, staleDate: nil))
      }
      result(nil)
    }
  }

  @available(iOS 16.2, *)
  private static func end(_ result: @escaping FlutterResult) {
    Task {
      for activity in Activity<ClaudeAttributes>.activities {
        await activity.end(nil, dismissalPolicy: .immediate)
      }
      result(nil)
    }
  }
}

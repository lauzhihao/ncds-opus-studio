import ActivityKit
import WidgetKit
import SwiftUI

/// Fraunces 展示衬线(与主 App / studio 同款),状态词全英文无需中文级联。
/// 字体文件在 Fonts/,经本扩展 Info.plist UIAppFonts 注册。
func frauncesFont(_ size: CGFloat, bold: Bool = true) -> Font {
    .custom(bold ? "Fraunces-Bold" : "Fraunces-Regular", size: size)
}

struct ClaudeLiveActivity: Widget {
    var body: some WidgetConfiguration {
        ActivityConfiguration(for: ClaudeAttributes.self) { context in
            LockScreenView(state: context.state)
                .activityBackgroundTint(Color(red: 0.13, green: 0.07, blue: 0.05).opacity(0.92))
                .activitySystemActionForegroundColor(.white)
        } dynamicIsland: { context in
            DynamicIsland {
                DynamicIslandExpandedRegion(.leading) {
                    Bulb(color: stateColor(context.state.state), on: true, size: 36)
                }
                DynamicIslandExpandedRegion(.center) {
                    StatusText(state: context.state.state)
                        .font(frauncesFont(15))
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                DynamicIslandExpandedRegion(.trailing) {
                    TrafficStack(state: context.state.state, compact: true)
                }
                DynamicIslandExpandedRegion(.bottom) {
                    BottomView(state: context.state)
                }
            } compactLeading: {
                Circle()
                    .fill(stateColor(context.state.state))
                    .frame(width: 14, height: 14)
            } compactTrailing: {
                Text(stateLabel(context.state.state))
                    .font(frauncesFont(11))
                    .foregroundStyle(stateColor(context.state.state))
            } minimal: {
                Image(systemName: "circle.fill")
                    .foregroundStyle(stateColor(context.state.state))
            }
            .keylineTint(stateColor(context.state.state))
        }
    }
}

// 灵动岛展开后底部区：有配额则显示配额,否则空。
// (Approve/Deny 按钮已按产品决定移除——不在端上做工具调用批准。)
struct BottomView: View {
    let state: ClaudeAttributes.ContentState

    var body: some View {
        if let quota = state.quota {
            QuotaRow(quota: quota)
        } else {
            EmptyView()
        }
    }
}

struct QuotaRow: View {
    let quota: ClaudeAttributes.ContentState.Quota

    var body: some View {
        HStack {
            Label(fmt(quota.tokens5h), systemImage: "clock")
                .font(.caption2)
            Spacer()
            Label(fmt(quota.tokens7d), systemImage: "calendar")
                .font(.caption2)
        }
        .foregroundStyle(.white.opacity(0.85))
    }

    func fmt(_ n: Int) -> String {
        if n >= 1_000_000 { return String(format: "%.1fM", Double(n) / 1_000_000) }
        if n >= 1_000 { return String(format: "%.1fK", Double(n) / 1_000) }
        return "\(n)"
    }
}

struct TrafficStack: View {
    let state: String
    var compact: Bool = false

    var body: some View {
        VStack(spacing: compact ? 3 : 6) {
            // 经典红绿灯三色位置不变,点亮逻辑对齐实物灯:红=等待(Y) 黄=推理(R) 绿=空闲(G)
            Bulb(color: .red, on: state == "Y", size: compact ? 10 : 18)
            Bulb(color: Color(red: 1.0, green: 0.5, blue: 0.0), on: state == "R", size: compact ? 10 : 18)
            Bulb(color: .green, on: state == "G", size: compact ? 10 : 18)
        }
        .padding(compact ? 4 : 8)
        .background(Color.black.opacity(0.6))
        .clipShape(RoundedRectangle(cornerRadius: compact ? 6 : 10))
    }
}

// 玻璃质感灯泡:点亮态带高光 + 发光 + 白边,与主 App 的 TrafficBulb 一致。
// 注:Live Activity 由系统快照渲染,跑不了呼吸/循环动画,这里只做静态点亮态。
struct Bulb: View {
    let color: Color
    let on: Bool
    let size: CGFloat

    var body: some View {
        let level: Double = on ? 1 : 0
        Circle()
            .fill(color.opacity(0.16 + 0.84 * level))     // 熄灭也留淡底色,像没通电的灯罩
            .overlay(
                // 左上角高光,做出玻璃灯罩的立体反光
                Circle().fill(
                    RadialGradient(
                        colors: [.white.opacity(0.5 * level), .clear],
                        center: UnitPoint(x: 0.35, y: 0.30),
                        startRadius: 0,
                        endRadius: size * 0.55
                    )
                )
            )
            .overlay(Circle().stroke(.white.opacity(0.06 + 0.28 * level), lineWidth: max(1, size * 0.06)))
            .frame(width: size, height: size)
            .shadow(color: color.opacity(0.9 * level), radius: on ? size / 2 : 0)
    }
}

struct LockScreenView: View {
    let state: ClaudeAttributes.ContentState

    var body: some View {
        // 横版红绿灯居中:左红=等待(Y) 中黄=推理(R) 右绿=空闲(G),样式对齐 App 首页。
        HStack(spacing: 16) {
            Bulb(color: .red,                                   on: state.state == "Y", size: 30)
            Bulb(color: Color(red: 1.0, green: 0.5, blue: 0.0), on: state.state == "R", size: 30)
            Bulb(color: .green,                                 on: state.state == "G", size: 30)
        }
        .padding(.horizontal, 24)
        .padding(.vertical, 14)
        .background(Color(red: 0.16, green: 0.13, blue: 0.11))   // 灯壳深色,同首页 ctHousing
        .clipShape(Capsule())
        .frame(maxWidth: .infinity)   // 居中铺满,胶囊水平居中
        .padding()
        // 状态标签:右上角状态色胶囊(Thinking/Asking/Idle)
        .overlay(alignment: .topTrailing) {
            Text(stateLabel(state.state))
                .font(frauncesFont(12))
                .foregroundStyle(stateColor(state.state))
                .padding(.horizontal, 10)
                .padding(.vertical, 4)
                .background(stateColor(state.state).opacity(0.15), in: Capsule())
                .padding(10)
        }
    }
}

// 状态文案:Agent + 状态词(状态词上状态色)。灵动岛展开态 / 锁屏复用。
struct StatusText: View {
    let state: String
    var body: some View {
        HStack(spacing: 5) {
            Text("Agent").foregroundStyle(.white)
            Text(stateLabel(state)).foregroundStyle(stateColor(state))
        }
        .lineLimit(1)
        .minimumScaleFactor(0.7)
    }
}

// 颜色对齐实物灯:推理=黄(琥珀),等待=红,空闲=绿
func stateColor(_ s: String) -> Color {
    switch s {
    case "R": return Color(red: 1.0, green: 0.5, blue: 0.0)  // 琥珀
    case "Y": return .red
    case "G": return .green
    default: return .gray
    }
}

func stateLabel(_ s: String) -> String {
    switch s {
    case "R": return "Thinking"
    case "Y": return "Asking"
    case "G": return "Idle"
    default: return "—"
    }
}

// 注:原生的两个 #Preview(Live Activity 画布预览)已移除——其 result builder 需 iOS 17+,
// 而本扩展部署目标为 16.2;预览仅 Xcode 画布用,不影响运行时。如需预览可临时把扩展目标提到 17。

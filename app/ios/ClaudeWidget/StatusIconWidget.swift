import WidgetKit
import SwiftUI

// 主屏「图标样式」小组件:底图(珊瑚星芒)+ 右下角状态圆点(R/Y/G)。
// 数据:Widget 自己定时拉中继 GET /v1/state(Bearer 鉴权,App 没开也能刷)。
// ⚠️ iOS 给 Widget 的刷新次数有每日配额,所以是「准实时」——可能滞后几分钟,
//    要真·实时看状态请用灵动岛/锁屏的 Live Activity。

struct StatusEntry: TimelineEntry {
    let date: Date
    let state: String   // R / Y / G / 0
}

struct StatusProvider: TimelineProvider {
    func placeholder(in context: Context) -> StatusEntry {
        StatusEntry(date: .now, state: "G")
    }

    func getSnapshot(in context: Context, completion: @escaping (StatusEntry) -> Void) {
        completion(StatusEntry(date: .now, state: "G"))
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<StatusEntry>) -> Void) {
        Task {
            let state = await fetchState()
            let next = Calendar.current.date(byAdding: .minute, value: 15, to: .now) ?? .now.addingTimeInterval(900)
            completion(Timeline(entries: [StatusEntry(date: .now, state: state)], policy: .after(next)))
        }
    }

    private func fetchState() async -> String {
        guard let url = URL(string: "\(RelayConfig.url)/v1/state") else { return "0" }
        var req = URLRequest(url: url)
        req.setValue("Bearer \(RelayConfig.apiToken)", forHTTPHeaderField: "Authorization")
        guard
            let (data, _) = try? await URLSession.shared.data(for: req),
            let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
            let s = obj["state"] as? String
        else { return "0" }
        return s
    }
}

struct StatusIconView: View {
    let state: String

    var body: some View {
        GeometryReader { geo in
            let s = min(geo.size.width, geo.size.height)
            ZStack(alignment: .bottomTrailing) {
                Color.clear
                Circle()
                    .fill(dotColor(state))
                    .frame(width: s * 0.30, height: s * 0.30)
                    .overlay(Circle().strokeBorder(.white, lineWidth: s * 0.035))
                    .shadow(color: dotColor(state).opacity(0.85), radius: s * 0.06)
                    .padding(s * 0.05)
            }
        }
        .containerBackground(for: .widget) {
            Image("IconBase")
                .resizable()
                .scaledToFill()
        }
    }

    func dotColor(_ s: String) -> Color {
        switch s {
        case "R": return .red
        case "Y": return .yellow
        case "G": return .green
        default:  return Color(white: 0.6)   // 0 / 未知:灰
        }
    }
}

struct StatusIconWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "ClaudeStatusIcon", provider: StatusProvider()) { entry in
            StatusIconView(state: entry.state)
        }
        .configurationDisplayName("Agent")
        .description("Agent traffic light on your Home Screen; the bottom-right dot shows the current state (updates are throttled by iOS, so it may lag a few minutes).")
        .supportedFamilies([.systemSmall])
    }
}

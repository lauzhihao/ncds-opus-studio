import WidgetKit
import SwiftUI

@main
struct ClaudeTrafficLightWidgetBundle: WidgetBundle {
    var body: some Widget {
        ClaudeLiveActivity()
        StatusIconWidget()
    }
}

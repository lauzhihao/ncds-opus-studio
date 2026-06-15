import ActivityKit
import Foundation

// 主 App 和 Widget Extension 共用。在 Xcode 里把这个文件加入两个 target 的 Membership。
public struct ClaudeAttributes: ActivityAttributes {
    public struct ContentState: Codable, Hashable {
        public var state: String   // "R" / "Y" / "G"
        public var updatedAt: Date
        public var quota: Quota?
        public var pending: Pending?

        public init(state: String, updatedAt: Date, quota: Quota? = nil, pending: Pending? = nil) {
            self.state = state
            self.updatedAt = updatedAt
            self.quota = quota
            self.pending = pending
        }

        public struct Quota: Codable, Hashable {
            public var tokens5h: Int
            public var tokens7d: Int
            public var updatedAt: Int?

            public init(tokens5h: Int, tokens7d: Int, updatedAt: Int? = nil) {
                self.tokens5h = tokens5h
                self.tokens7d = tokens7d
                self.updatedAt = updatedAt
            }
        }

        public struct Pending: Codable, Hashable {
            public var id: String
            public var tool: String
            public var preview: String?

            public init(id: String, tool: String, preview: String? = nil) {
                self.id = id
                self.tool = tool
                self.preview = preview
            }
        }
    }

    public var name: String

    public init(name: String) {
        self.name = name
    }
}

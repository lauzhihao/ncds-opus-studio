import Foundation

// 中继连接配置模板——复制为 RelayConfig.swift 填入你自己的值再编译:
//
//   cp RelayConfig.example.swift RelayConfig.swift
//
// RelayConfig.swift 含真实 apiToken,已被 .gitignore。
// ⚠️ Xcode 里只把 RelayConfig.swift 加入工程(主 App + Widget 两个 target),
//    本模板文件不要加,否则 enum 重名编译报错。
//
// 公开版:状态推送走自托管中继(见 relay/),按用户 apiToken 鉴权。App 用 apiToken
// 把本机注册到中继(/v1/register),Mac agent 用同一个 apiToken 上报状态(/v1/state)。
// 中继是 HTTPS 正规证书,无需 ATS 例外。
enum RelayConfig {
    static let urls = ["https://apn.example.com"]   // 你的中继域名
    static let url = urls[0]

    // 每用户 API token:在中继上 `POST /v1/admin/users`(X-Admin-Secret)领取。
    // App 与 Mac agent 必须用同一个,中继才能把「这台手机」和「这个人的状态」对上。
    static let apiToken = "clt_REPLACE_WITH_YOUR_API_TOKEN"

    // 旧字段(本机 agent 时代),中继版不用,留空免旧引用编译报错。
    static let registerSecret = ""
    static let commandSecret = "UNUSED"
}

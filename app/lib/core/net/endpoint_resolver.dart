import 'lan_host_resolver.dart';

/// 网络「发现 → 解析 IP → HTTP」可换接口。
///
/// 现状:App 直连已知别名(`.local` / Tailscale MagicDNS),并非主动 Bonjour 服务发现。
/// 默认实现 [DirectEndpointResolver] 返回配置的 base URL;若 host 是 `.local` 别名,
/// 在 Android 上经 [LanHostResolver] 用 mDNS 解析成局域网 IP(iOS/macOS 系统原生解析,
/// 无需介入)。上层业务无需改动——这正是当初留这层抽象的目的。
abstract class EndpointResolver {
  Future<Uri> resolveBase();

  /// 上次解析到的地址连不上时调用:让下次请求重新解析(换网络/IP 变了时自愈)。默认无操作。
  void invalidate() {}
}

class DirectEndpointResolver implements EndpointResolver {
  DirectEndpointResolver(this.baseUrl);

  /// 例:`http://liuzhihao-mbp.local:8810` 或 `https://xxx.tail009bf5.ts.net`。
  final String baseUrl;

  @override
  Future<Uri> resolveBase() => LanHostResolver.resolve(Uri.parse(baseUrl));

  @override
  void invalidate() => LanHostResolver.invalidate(Uri.parse(baseUrl).host);
}

import 'dart:async';
import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:multicast_dns/multicast_dns.dart';

/// 把 `.local`(mDNS/Bonjour 别名)解析成局域网 IP。
///
/// iOS/macOS 系统自带 Bonjour,`http://xxx.local` 在 socket 层就能直接解析;但 Android
/// 的标准 DNS 不带 mDNS responder,`*.local` 一律解析失败——这正是「安卓连不上工厂后端」
/// 的根因。本类只在 Android 上用 mDNS 主动查询 A 记录,把机器名换成当前局域网 IP,
/// 于是换地方、换 Wi-Fi(IP 变了)也能按机器名找到 Mac,无需手填 IP。
///
/// 设计要点:
/// - 解析结果按 host 静态缓存(带 TTL),避免每个 HTTP 请求都打一次 mDNS;
/// - 同一 host 的并发解析合并为一次在途查询;
/// - 解析失败时回退到上一次成功的 IP(哪怕已过期——IP 多半还有效),再不行才保留原别名;
/// - Android 上接收 mDNS 多播应答需要持有 `WifiManager.MulticastLock`(经 MethodChannel
///   `app/multicast_lock` 向原生申请),否则系统会过滤多播包,查询永远超时。
class LanHostResolver {
  LanHostResolver._();

  static const MethodChannel _lockChannel = MethodChannel('app/multicast_lock');

  static final Map<String, _CacheEntry> _cache = <String, _CacheEntry>{};
  static final Map<String, Future<String?>> _inflight = <String, Future<String?>>{};

  /// 缓存有效期。换网络后旧 IP 会失活,届时一次失败的请求触发重解析即可。
  static const Duration _ttl = Duration(minutes: 5);

  /// 单轮 mDNS 查询超时。应答通常百毫秒级到;一轮没收到就重发(多播易丢包)。
  static const Duration _queryTimeout = Duration(milliseconds: 1500);

  /// 重发轮数:Wi-Fi 多播丢包率不低,重发几轮显著提高冷启动首解命中率。
  static const int _queryRounds = 3;

  /// 仅 Android 且 host 以 `.local` 结尾时才需要 mDNS;其它平台/host 原样返回
  /// (iOS/macOS 原生解析 `.local`,Tailscale/公网域名走系统 DNS)。
  static bool _needsMdns(String host) => Platform.isAndroid && host.endsWith('.local');

  /// 把 [uri] 的 host(若是 .local)换成解析到的 IP;无需解析或解析失败则返回可用的原 uri。
  static Future<Uri> resolve(Uri uri) async {
    final host = uri.host;
    if (!_needsMdns(host)) return uri;

    final fresh = _cache[host];
    if (fresh != null && DateTime.now().difference(fresh.at) < _ttl) {
      return uri.replace(host: fresh.ip);
    }

    final ip = await (_inflight[host] ??=
        _lookup(host).whenComplete(() => _inflight.remove(host)));
    if (ip != null) {
      if (kDebugMode) debugPrint('[mDNS] $host -> $ip');
      return uri.replace(host: ip);
    }

    // 解析失败:有旧缓存就先顶着用,否则保留原别名(Android 上仍会失败,但错误可读)。
    final stale = _cache[host];
    if (kDebugMode) {
      debugPrint('[mDNS] $host 解析失败,'
          '${stale != null ? '回退缓存 ${stale.ip}' : '保留别名(Android 上将连不上)'}');
    }
    return stale != null ? uri.replace(host: stale.ip) : uri;
  }

  /// 让某 host 的缓存失效——请求失败时调用,下次访问立即重解析。
  /// 换地方/换 Wi-Fi 导致 Mac 的局域网 IP 变了时,无需等 TTL 过期就能自愈。
  static void invalidate(String host) => _cache.remove(host);

  static Future<String?> _lookup(String host) async {
    await _acquireLock();
    try {
      // 每轮用全新 client(重新 bind + 清空内部缓存)再查一次:既扛多播丢包,
      // 也扛 start()/bind 偶发失败(某些机型 5353 端口/多播 join 首次会抖)。
      for (var round = 0; round < _queryRounds; round++) {
        final ip = await _queryOnce(host);
        if (ip != null) {
          _cache[host] = _CacheEntry(ip, DateTime.now());
          return ip;
        }
      }
    } finally {
      await _releaseLock();
    }
    return null;
  }

  /// 单轮:新建 mDNS client → start → 查一次 A 记录,返回首个可路由地址。失败返回 null。
  static Future<String?> _queryOnce(String host) async {
    final client = MDnsClient(rawDatagramSocketFactory: _bindNoReusePort);
    try {
      await client.start();
      await for (final IPAddressResourceRecord rec in client.lookup<IPAddressResourceRecord>(
        ResourceRecordQuery.addressIPv4(host),
        timeout: _queryTimeout,
      )) {
        // 同一别名常有多条 A 记录(Wi-Fi / 以太 / 回环 / 链路本地),到达顺序不固定。
        // 跳过连不通的:回环 127.x、链路本地 169.254.x、未指定 0.0.0.0、多播——
        // 只认可路由的局域网地址,否则会连到错误地址而超时/被拒。
        final addr = rec.address;
        if (addr.isLoopback || addr.isLinkLocal || addr.isMulticast || addr.address == '0.0.0.0') {
          continue;
        }
        return addr.address; // 取第一条可路由的 A 记录
      }
    } catch (_) {
      // 绑定/多播失败:本轮放弃,外层换新 client 重试。
    } finally {
      try {
        client.stop();
      } catch (_) {/* start 中途失败时 stop 可能抛 StateError,忽略 */}
    }
    return null;
  }

  /// Android 上 SO_REUSEPORT 常不被支持(默认工厂用 reusePort:true 会 bind 抛错),强制关掉。
  static Future<RawDatagramSocket> _bindNoReusePort(
    dynamic host,
    int port, {
    bool reuseAddress = true,
    bool reusePort = true,
    int ttl = 255,
  }) =>
      RawDatagramSocket.bind(host, port, reuseAddress: reuseAddress, reusePort: false, ttl: ttl);

  static Future<void> _acquireLock() async {
    try {
      await _lockChannel.invokeMethod<void>('acquire');
    } catch (_) {/* 没有原生实现也不致命:部分设备多播无锁也能收到 */}
  }

  static Future<void> _releaseLock() async {
    try {
      await _lockChannel.invokeMethod<void>('release');
    } catch (_) {/* 同上 */}
  }
}

class _CacheEntry {
  _CacheEntry(this.ip, this.at);
  final String ip;
  final DateTime at;
}

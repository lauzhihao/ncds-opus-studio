// 打回意见语音口述弹层 —— 复刻 iOS RejectVoiceOverlay,双模式:
// - 端上识别(iOS:SFSpeechRecognizer / 安卓有 Google 语音服务时):speech_to_text 实时转写。
// - 兜底(安卓系统听写被阉割/不可用):录音 WAV → 上传服务端 /asr(阿里云一句话识别)→ 回文字。
// 端上识别初始化失败或运行报错时自动切到服务端转写。

import 'dart:io';

import 'package:flutter/material.dart';
import 'package:path_provider/path_provider.dart';
import 'package:record/record.dart';
import 'package:speech_to_text/speech_to_text.dart';

import '../../core/net/factory_client.dart';
import '../../design/components/decision_button.dart';
import '../../design/liquid_glass.dart';
import '../../design/tokens.dart';
import '../../design/typography.dart';

/// 透明路由弹出,确认返回 composed 文案(口述正文 + 【标签】xx),取消返回 null。
class RejectVoiceSheet extends StatefulWidget {
  const RejectVoiceSheet({super.key, required this.client});

  /// 服务端转写兜底用(POST /asr)。
  final FactoryClient client;

  @override
  State<RejectVoiceSheet> createState() => _RejectVoiceSheetState();
}

class _RejectVoiceSheetState extends State<RejectVoiceSheet> {
  static const List<String> _quickTags = ['开头太绕', '钩子弱', 'AI味重', '太啰嗦', '选题不对'];
  // 偏暖红(对齐 iOS RejectVoiceOverlay tint)。
  static const Color _tint = Color(0xFFCC4238);

  final SpeechToText _speech = SpeechToText();
  final AudioRecorder _recorder = AudioRecorder();
  final Set<String> _tags = <String>{};

  bool _serverMode = false; // true=端上识别不可用,改录音→服务端转写
  bool _listening = false; // 端上识别中
  bool _recording = false; // 服务端模式录音中
  bool _transcribing = false; // 上传/等待服务端转写中
  String _text = ''; // ASR 结果(直接展示)
  String _base = ''; // 开录前的既有文本,新转写叠在其后
  String? _err;

  @override
  void initState() {
    super.initState();
    _initSpeech();
  }

  Future<void> _initSpeech() async {
    try {
      final ok = await _speech.initialize(
        onError: (e) {
          // 端上识别报错(如系统听写不可用)→ 切到服务端转写兜底。
          if (!mounted) return;
          setState(() {
            _listening = false;
            if (!_serverMode) {
              _serverMode = true;
              _err = '端上识别不可用,已切换联网转写';
            }
          });
        },
        onStatus: (status) {
          // notListening/done:安卓停顿后自动停;把当前文本设为续录基底。
          if ((status == 'notListening' || status == 'done') && mounted && _listening) {
            setState(() {
              _listening = false;
              _base = _text;
            });
          }
        },
      );
      if (mounted) setState(() => _serverMode = !ok);
    } catch (_) {
      if (mounted) setState(() => _serverMode = true);
    }
  }

  @override
  void dispose() {
    _speech.cancel();
    _recorder.dispose();
    super.dispose();
  }

  // —— 麦克风:按模式分派 ——
  Future<void> _toggleMic() async {
    if (_transcribing) return; // 转写在途不响应
    if (_serverMode) {
      await _toggleRecord();
      return;
    }
    // 端上识别模式
    if (_listening) {
      await _speech.stop();
      if (mounted) {
        setState(() {
          _listening = false;
          _base = _text;
        });
      }
      return;
    }
    _base = _text;
    setState(() {
      _err = null;
      _listening = true;
    });
    await _speech.listen(
      onResult: (result) {
        if (!mounted) return;
        setState(() {
          _text = _base.isEmpty ? result.recognizedWords : '$_base${result.recognizedWords}';
        });
      },
      listenOptions: SpeechListenOptions(
        partialResults: true,
        listenMode: ListenMode.dictation,
        localeId: 'zh_CN',
        cancelOnError: true,
      ),
    );
  }

  // —— 服务端转写兜底:录音 → 上传 /asr ——
  Future<void> _toggleRecord() async {
    if (_recording) {
      String? path;
      try {
        path = await _recorder.stop();
      } catch (_) {}
      if (!mounted) return;
      setState(() => _recording = false);
      if (path != null) await _uploadAndTranscribe(path);
      return;
    }
    try {
      if (!await _recorder.hasPermission()) {
        if (mounted) setState(() => _err = '需要麦克风权限');
        return;
      }
      final dir = await getTemporaryDirectory();
      final path = '${dir.path}/reject_${DateTime.now().millisecondsSinceEpoch}.wav';
      await _recorder.start(
        const RecordConfig(encoder: AudioEncoder.wav, sampleRate: 16000, numChannels: 1),
        path: path,
      );
      _base = _text;
      if (mounted) {
        setState(() {
          _err = null;
          _recording = true;
        });
      }
    } catch (_) {
      if (mounted) {
        setState(() {
          _recording = false;
          _err = '录音失败';
        });
      }
    }
  }

  Future<void> _uploadAndTranscribe(String path) async {
    setState(() {
      _transcribing = true;
      _err = null;
    });
    try {
      final bytes = await File(path).readAsBytes();
      final text = await widget.client.transcribe(bytes);
      if (!mounted) return;
      setState(() {
        if (text.trim().isNotEmpty) {
          _text = _base.isEmpty ? text : '$_base$text';
          _base = _text;
        }
        _transcribing = false;
      });
    } catch (_) {
      if (mounted) {
        setState(() {
          _transcribing = false;
          _err = '转写失败,请重试';
        });
      }
    } finally {
      try {
        await File(path).delete();
      } catch (_) {}
    }
  }

  /// 提交文案:口述正文 + 「【标签】xx、yy」。纯标签不放行(由确认键禁用兜住)。
  String get _composed {
    final body = _text.trim();
    if (_tags.isEmpty) return body;
    final tags = _quickTags.where(_tags.contains).join('、');
    final sep =
        (body.endsWith('。') || body.endsWith('!') || body.endsWith('?') || body.endsWith('.'))
            ? ''
            : '。';
    return '$body$sep【标签】$tags';
  }

  String get _hint {
    if (_transcribing) return '转写中…';
    if (_serverMode) return _recording ? '录音中… 点击停止' : '点麦克风说话(联网转写)';
    if (_listening) return '正在聆听… 点击麦克风停止';
    return _text.trim().isEmpty ? '点击麦克风,说说哪里要改' : '可继续口述,点确认打回';
  }

  String get _placeholder {
    if (_transcribing) return '转写中…';
    if (_listening || _recording) return '在听了,说吧…';
    return '点下方麦克风,口述打回意见';
  }

  @override
  Widget build(BuildContext context) {
    final hasText = _text.trim().isNotEmpty;
    return Scaffold(
      backgroundColor: Colors.transparent,
      body: Stack(
        children: [
          // 毛玻璃遮罩:模糊压暗下方稿件。
          const Positioned.fill(
            child: GlassBar(tint: Color(0xFF0A0A0A), glassOpacity: 0.62, blurSigma: 22),
          ),
          Positioned.fill(
            child: SafeArea(
              child: Padding(
                padding: const EdgeInsets.fromLTRB(
                    AppSpacing.pageH, AppSpacing.s, AppSpacing.pageH, AppSpacing.pageBottom),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Align(
                      alignment: Alignment.centerRight,
                      child: IconButton(
                        icon: const Icon(Icons.close, color: Colors.white70),
                        onPressed: () {
                          _speech.cancel();
                          Navigator.of(context).pop();
                        },
                      ),
                    ),
                    Text('说说哪里要改?',
                        style: AppTypography.displayTitle
                            .copyWith(fontSize: 32, height: 1.15, color: Colors.white)),
                    const SizedBox(height: AppSpacing.block),
                    Expanded(
                      child: SingleChildScrollView(
                        child: Text(
                          _text.isEmpty ? _placeholder : _text,
                          style: AppTypography.bodySerif.copyWith(
                            fontSize: 22,
                            height: 1.6,
                            color: _text.isEmpty ? Colors.white38 : Colors.white,
                          ),
                        ),
                      ),
                    ),
                    Wrap(
                      spacing: 8,
                      runSpacing: 8,
                      children: [
                        for (final tag in _quickTags)
                          GestureDetector(
                            onTap: () => setState(
                                () => _tags.contains(tag) ? _tags.remove(tag) : _tags.add(tag)),
                            child: Container(
                              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 7),
                              decoration: ShapeDecoration(
                                color: _tags.contains(tag)
                                    ? _tint
                                    : Colors.white.withValues(alpha: 0.1),
                                shape: const StadiumBorder(),
                              ),
                              child: Text(tag,
                                  style: AppTypography.caption.copyWith(
                                      fontWeight: FontWeight.w600,
                                      color: _tags.contains(tag) ? Colors.white : Colors.white70)),
                            ),
                          ),
                      ],
                    ),
                    const SizedBox(height: AppSpacing.block),
                    Center(
                      child: _MicButton(
                        active: _listening || _recording,
                        busy: _transcribing,
                        tint: _tint,
                        onTap: _toggleMic,
                      ),
                    ),
                    const SizedBox(height: AppSpacing.s),
                    Text(_err ?? _hint,
                        textAlign: TextAlign.center,
                        style: AppTypography.caption
                            .copyWith(color: _err != null ? _tint : Colors.white70)),
                    const SizedBox(height: AppSpacing.m),
                    Opacity(
                      opacity: hasText ? 1 : 0.5,
                      child: DecisionButton(
                        label: '确认打回',
                        icon: Icons.undo,
                        tint: _tint,
                        onPressed: hasText
                            ? () {
                                _speech.cancel();
                                Navigator.of(context).pop(_composed);
                              }
                            : () {},
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

/// 大麦克风按钮:录音/识别时渐变实心 + 外圈呼吸光环 + 波形图标;转写中显示转圈。
class _MicButton extends StatefulWidget {
  const _MicButton({required this.active, required this.busy, required this.tint, required this.onTap});
  final bool active;
  final bool busy;
  final Color tint;
  final VoidCallback onTap;

  @override
  State<_MicButton> createState() => _MicButtonState();
}

class _MicButtonState extends State<_MicButton> with SingleTickerProviderStateMixin {
  late final AnimationController _ring = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 1600),
  );

  @override
  void initState() {
    super.initState();
    if (widget.active) _ring.repeat();
  }

  @override
  void didUpdateWidget(_MicButton old) {
    super.didUpdateWidget(old);
    if (widget.active && !_ring.isAnimating) {
      _ring.repeat();
    } else if (!widget.active && _ring.isAnimating) {
      _ring.stop();
      _ring.value = 0;
    }
  }

  @override
  void dispose() {
    _ring.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: widget.onTap,
      behavior: HitTestBehavior.opaque,
      child: SizedBox(
        width: 120,
        height: 120,
        child: Stack(
          alignment: Alignment.center,
          children: [
            if (widget.active)
              AnimatedBuilder(
                animation: _ring,
                builder: (context, _) {
                  final s = _ring.value;
                  return Container(
                    width: 84 + 28 * s,
                    height: 84 + 28 * s,
                    decoration: BoxDecoration(
                      shape: BoxShape.circle,
                      border:
                          Border.all(color: widget.tint.withValues(alpha: 0.6 * (1 - s)), width: 3),
                    ),
                  );
                },
              ),
            Container(
              width: 84,
              height: 84,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                gradient: LinearGradient(
                  begin: Alignment.topCenter,
                  end: Alignment.bottomCenter,
                  colors: [widget.tint, widget.tint.withValues(alpha: 0.8)],
                ),
                boxShadow: [
                  BoxShadow(
                      color: widget.tint.withValues(alpha: 0.5),
                      blurRadius: 14,
                      offset: const Offset(0, 6)),
                ],
              ),
              child: widget.busy
                  ? const Padding(
                      padding: EdgeInsets.all(26),
                      child: CircularProgressIndicator(
                          strokeWidth: 3, valueColor: AlwaysStoppedAnimation<Color>(Colors.white)),
                    )
                  : Icon(widget.active ? Icons.graphic_eq : Icons.mic, size: 32, color: Colors.white),
            ),
          ],
        ),
      ),
    );
  }
}

/// 透明路由弹出语音打回弹层,返回 composed 文案或 null(取消)。
Future<String?> showRejectVoiceSheet(BuildContext context, {required FactoryClient client}) {
  return Navigator.of(context).push<String>(
    PageRouteBuilder<String>(
      opaque: false,
      barrierColor: Colors.transparent,
      transitionDuration: const Duration(milliseconds: 220),
      pageBuilder: (_, _, _) => RejectVoiceSheet(client: client),
      transitionsBuilder: (_, anim, _, child) => FadeTransition(opacity: anim, child: child),
    ),
  );
}

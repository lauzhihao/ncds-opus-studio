// 工厂后端(ncds-opus-studio)数据模型,从 iOS NofModels.swift 移植。
// 字段名与后端 JSON 一致(snake_case);params 用 Map String,dynamic。

class NofCommand {
  const NofCommand({required this.name, required this.label, required this.group, required this.summary});

  final String name;
  final String label;
  final String group;
  final String summary;

  factory NofCommand.fromJson(Map<String, dynamic> j) => NofCommand(
        name: j['name'] as String? ?? '',
        label: j['label'] as String? ?? '',
        group: j['group'] as String? ?? '',
        summary: j['summary'] as String? ?? '',
      );
}

class NofReview {
  const NofReview({required this.decision, this.note, this.reviewedAt});

  final String decision; // approved / rejected
  final String? note;
  final String? reviewedAt;

  factory NofReview.fromJson(Map<String, dynamic> j) => NofReview(
        decision: j['decision'] as String? ?? '',
        note: j['note'] as String?,
        reviewedAt: j['reviewed_at'] as String?,
      );
}

class NofArtifact {
  const NofArtifact({required this.label, required this.kind, required this.url, this.path});

  final String label;
  final String kind; // script/audio/video/image/data/dir/text/file
  final String url;
  final String? path;

  factory NofArtifact.fromJson(Map<String, dynamic> j) => NofArtifact(
        label: j['label'] as String? ?? '',
        kind: j['kind'] as String? ?? '',
        url: j['url'] as String? ?? '',
        path: j['path'] as String?,
      );
}

/// GET /tasks 列表项(收件箱)。decision 未决为 null;params 原样带回做标题。
class TaskMeta {
  const TaskMeta({
    required this.taskId,
    required this.cmd,
    required this.status,
    this.createdAt,
    this.decision,
    this.params,
    this.title,
    this.subtitle,
    this.source,
    this.parentTaskId,
    this.roundId,
  });

  final String taskId;
  final String cmd;
  final String status;
  final String? createdAt;
  final String? decision;
  final Map<String, dynamic>? params;
  final String? title;
  final String? subtitle;
  final String? source;
  final String? parentTaskId;
  final String? roundId;

  factory TaskMeta.fromJson(Map<String, dynamic> j) => TaskMeta(
        taskId: j['task_id'] as String? ?? '',
        cmd: j['cmd'] as String? ?? '',
        status: j['status'] as String? ?? '',
        createdAt: j['created_at'] as String?,
        decision: j['decision'] as String?,
        params: (j['params'] as Map?)?.cast<String, dynamic>(),
        title: j['title'] as String?,
        subtitle: j['subtitle'] as String?,
        source: j['source'] as String?,
        parentTaskId: j['parent_task_id'] as String?,
        roundId: j['round_id'] as String?,
      );

  bool get isResumeSegment => cmd == 'wolong' && params?['resume'] == true;

  /// 卡片标题:后端 title 最优先;续跑段写死;再按各 agent 主参数猜;都没有兜底。
  String get titleGuess => nofTitleGuess(cmd: cmd, params: params, title: title);

  String? get subtitleText {
    if (title != null && title!.isNotEmpty && subtitle != null && subtitle!.isNotEmpty) return subtitle;
    return null;
  }

  String? get sourceBadgeLabel => nofSourceBadgeLabel(source);
}

/// 任务标题推断(列表项与详情页共用):后端 title 最优先;卧龙续跑段写死;
/// 再按各 agent 主参数猜;都没有兜底「(无参数)」。
String nofTitleGuess({required String cmd, Map<String, dynamic>? params, String? title}) {
  if (title != null && title.isNotEmpty) return title;
  if (cmd == 'wolong' && params?['resume'] == true) return '卧龙·续跑段';
  const keys = ['topic', 'author', 'aweme', 'benchmark_path', 'script_path', 'job_dir', 'html_url', 'prompt'];
  for (final key in keys) {
    final v = _nofDisplay(params?[key]);
    if (v == null || v.isEmpty) continue;
    if (key == 'aweme' && v.startsWith('http')) return '分享链接采集';
    if (key.endsWith('_path') || key.endsWith('_dir')) return v.split('/').last;
    return v;
  }
  final c = _nofDisplay(params?['count']);
  if (c != null && c.isNotEmpty) return '本轮产出 $c 条';
  if (params != null && params.isNotEmpty) {
    final p = _nofDisplay(params.values.first);
    if (p != null && p.isNotEmpty) return p;
  }
  return '(无参数)';
}

String? _nofDisplay(dynamic v) {
  if (v == null) return null;
  if (v is String) return v;
  if (v is bool) return v ? 'true' : 'false';
  return v.toString();
}

/// 来源角标:user/null(手发)不挂角标;未知新值返回 null(不炸 UI)。
String? nofSourceBadgeLabel(String? source) {
  switch (source) {
    case 'wolong':
      return '卧龙派发';
    case 'gate':
      return '卧龙续跑';
    case 'cron':
      return '自动排产';
    case 'retro':
      return '复盘';
    default:
      return null;
  }
}

/// GET /tasks/{id} 详情。公共字段(status/error/artifacts/review)按 agent 渲染专属面板;
/// result 原样带回(各 agent 形态不同),沈括等强类型面板按 cmd 自行解码。
class TaskDetail {
  const TaskDetail({
    required this.taskId,
    required this.cmd,
    required this.status,
    this.error,
    this.artifacts,
    this.review,
    this.params,
    this.source,
    this.result,
    this.decisionFinalized = false,
    this.roundId,
  });

  final String taskId;
  final String cmd;
  final String status;
  final String? error;
  final List<NofArtifact>? artifacts;
  final NofReview? review;
  final Map<String, dynamic>? params;
  final String? source;
  final Map<String, dynamic>? result; // agent 专属成果,原样带回
  final bool decisionFinalized; // 已定案:改判只影响标注,不回卷流程(旧后端解 nil 当 false)
  final String? roundId; // round 归属:失败处置是否归卧龙

  /// 各 agent 专属成果(cmd 对得上且 result 在时解码;否则 null,降级为通用骨架)。
  ShenkuoResult? get shenkuo =>
      cmd == 'shenkuo' && result != null ? ShenkuoResult.fromJson(result!) : null;
  GuiguziResult? get guiguzi =>
      cmd == 'guiguzi' && result != null ? GuiguziResult.fromJson(result!) : null;
  WudaoziResult? get wudaozi =>
      cmd == 'wudaozi' && result != null ? WudaoziResult.fromJson(result!) : null;
  BoyaResult? get boya => cmd == 'boya' && result != null ? BoyaResult.fromJson(result!) : null;
  WolongResult? get wolong =>
      cmd == 'wolong' && result != null ? WolongResult.fromJson(result!) : null;
  LiuyongResult? get liuyong =>
      cmd == 'liuyong' && result != null ? LiuyongResult.fromJson(result!) : null;

  /// 产物里 kind=audio 的可播放绝对路径用(伯牙 master.mp3);返回相对 url,由调用方解析绝对地址。
  String? get audioArtifactUrl {
    for (final a in artifacts ?? const <NofArtifact>[]) {
      if (a.kind == 'audio') return a.url;
    }
    return null;
  }

  /// 吴道子配图缩略基址:分镜实例目录(dir 产物)的 url 换成文件服务前缀;调用方再解析绝对地址。
  String? get figureDirFilesUrl {
    for (final a in artifacts ?? const <NofArtifact>[]) {
      if (a.kind == 'dir') {
        return a.url.replaceFirst('/artifacts/dir/', '/artifacts/files/');
      }
    }
    return null;
  }

  String? get sourceBadgeLabel => nofSourceBadgeLabel(source);

  /// hero 标题:沈括用成果回填的 task_title,其余按参数猜(对齐 iOS task.titleGuess)。
  String get titleGuess => nofTitleGuess(cmd: cmd, params: params, title: shenkuo?.taskTitle);

  factory TaskDetail.fromJson(Map<String, dynamic> j) => TaskDetail(
        taskId: j['task_id'] as String? ?? '',
        cmd: j['cmd'] as String? ?? '',
        status: j['status'] as String? ?? '',
        error: j['error'] as String?,
        artifacts: (j['artifacts'] as List?)
            ?.map((e) => NofArtifact.fromJson((e as Map).cast<String, dynamic>()))
            .toList(),
        review: j['review'] == null ? null : NofReview.fromJson((j['review'] as Map).cast<String, dynamic>()),
        params: (j['params'] as Map?)?.cast<String, dynamic>(),
        source: j['source'] as String?,
        result: (j['result'] as Map?)?.cast<String, dynamic>(),
        decisionFinalized: j['decision_finalized'] as bool? ?? false,
        roundId: j['round_id'] as String?,
      );
}

// —— 沈括:采集成果(作者目录 + 逐条采集清单),从 iOS NofModels.swift 移植 ——

/// 沈括采集结果。单条模式没有 all_posts;refresh-only 模式只回 snapshots。
class ShenkuoResult {
  const ShenkuoResult({
    this.authorDir,
    this.allPosts,
    this.collected,
    this.snapshots,
    this.taskTitle,
    this.taskSubtitle,
  });

  final String? authorDir;
  final int? allPosts;
  final List<ShenkuoEntry>? collected;
  final int? snapshots;
  final String? taskTitle; // "#话题"
  final String? taskSubtitle; // "@作者"

  factory ShenkuoResult.fromJson(Map<String, dynamic> j) => ShenkuoResult(
        authorDir: j['author_dir'] as String?,
        allPosts: (j['all_posts'] as num?)?.toInt(),
        collected: (j['collected'] as List?)
            ?.map((e) => ShenkuoEntry.fromJson((e as Map).cast<String, dynamic>()))
            .toList(),
        snapshots: (j['snapshots'] as num?)?.toInt(),
        taskTitle: j['task_title'] as String?,
        taskSubtitle: j['task_subtitle'] as String?,
      );
}

/// 一条采集结果。status 是各工序状态字(download/transcribe/cutout/comments → ok/cached/error:*)。
/// 文件字段(cover/cutouts/audio)是仓库根相对路径,经 /artifacts/files/ 取。
class ShenkuoEntry {
  const ShenkuoEntry({
    this.awemeId,
    this.desc,
    this.digg,
    this.status,
    this.frames,
    this.cutouts,
    this.author,
    this.hashtags,
    this.stats,
    this.cover,
    this.text,
    this.topComments,
    this.audio,
  });

  final String? awemeId;
  final String? desc;
  final int? digg;
  final Map<String, String>? status;
  final List<String>? frames;
  final List<String>? cutouts;
  final String? author;
  final List<String>? hashtags;
  final Map<String, int>? stats; // digg/comment/share/collect
  final String? cover;
  final String? text; // 转写文字(已嵌入)
  final List<ShenkuoComment>? topComments; // 高赞评论(已按赞数排好)
  final Map<String, String>? audio; // original/vocals/bgm → 相对路径

  factory ShenkuoEntry.fromJson(Map<String, dynamic> j) => ShenkuoEntry(
        awemeId: j['aweme_id'] as String?,
        desc: j['desc'] as String?,
        digg: (j['digg'] as num?)?.toInt(),
        status: (j['status'] as Map?)?.map((k, v) => MapEntry(k.toString(), v.toString())),
        frames: (j['frames'] as List?)?.map((e) => e.toString()).toList(),
        cutouts: (j['cutouts'] as List?)?.map((e) => e.toString()).toList(),
        author: j['author'] as String?,
        hashtags: (j['hashtags'] as List?)?.map((e) => e.toString()).toList(),
        stats: (j['stats'] as Map?)?.map((k, v) => MapEntry(k.toString(), v is num ? v.toInt() : 0)),
        cover: j['cover'] as String?,
        text: j['text'] as String?,
        topComments: (j['top_comments'] as List?)
            ?.map((e) => ShenkuoComment.fromJson((e as Map).cast<String, dynamic>()))
            .toList(),
        audio: (j['audio'] as Map?)?.map((k, v) => MapEntry(k.toString(), v.toString())),
      );
}

/// 一条高赞评论(受众反馈)。
class ShenkuoComment {
  const ShenkuoComment({this.nickname, this.text, this.digg, this.ip});

  final String? nickname;
  final String? text;
  final int? digg;
  final String? ip;

  factory ShenkuoComment.fromJson(Map<String, dynamic> j) => ShenkuoComment(
        nickname: j['nickname'] as String?,
        text: j['text'] as String?,
        digg: (j['digg'] as num?)?.toInt(),
        ip: j['ip'] as String?,
      );
}

// —— 鬼谷子:选题库(result.topics) ——

class GuiguziResult {
  const GuiguziResult({this.topics});
  final List<GuiguziTopic>? topics;

  factory GuiguziResult.fromJson(Map<String, dynamic> j) => GuiguziResult(
        topics: (j['topics'] as List?)
            ?.map((e) => GuiguziTopic.fromJson((e as Map).cast<String, dynamic>()))
            .toList(),
      );
}

/// 一条选题。potential 是 1-10 爆款潜力分(模型生成,用 double 容错小数)。
class GuiguziTopic {
  const GuiguziTopic({this.title, this.motif, this.source, this.why, this.angle, this.potential});

  final String? title;
  final String? motif;
  final String? source;
  final String? why;
  final String? angle;
  final double? potential;

  factory GuiguziTopic.fromJson(Map<String, dynamic> j) => GuiguziTopic(
        title: j['title'] as String?,
        motif: j['motif'] as String?,
        source: j['source'] as String?,
        why: j['why'] as String?,
        angle: j['angle'] as String?,
        potential: (j['potential'] as num?)?.toDouble(),
      );
}

// —— 吴道子:分镜 beats + 不丢句质检 ——

class WudaoziResult {
  const WudaoziResult({this.jobId, this.outDir, this.beats, this.storyboardPath, this.qc});

  final String? jobId;
  final String? outDir;
  final List<WudaoziBeat>? beats;
  final String? storyboardPath;
  final WudaoziQC? qc;

  factory WudaoziResult.fromJson(Map<String, dynamic> j) => WudaoziResult(
        jobId: j['job_id'] as String?,
        outDir: j['out_dir'] as String?,
        beats: (j['beats'] as List?)
            ?.map((e) => WudaoziBeat.fromJson((e as Map).cast<String, dynamic>()))
            .toList(),
        storyboardPath: j['storyboard_path'] as String?,
        qc: j['qc'] == null ? null : WudaoziQC.fromJson((j['qc'] as Map).cast<String, dynamic>()),
      );
}

/// 一句分镜:zh 台词必有;figure/icons/motion 是视觉选用。
class WudaoziBeat {
  const WudaoziBeat({this.zh, this.figure, this.icons, this.motion, this.title, this.tag, this.kind});

  final String? zh;
  final String? figure;
  final List<String>? icons;
  final String? motion;
  final String? title;
  final String? tag;
  final String? kind;

  factory WudaoziBeat.fromJson(Map<String, dynamic> j) => WudaoziBeat(
        zh: j['zh'] as String?,
        figure: j['figure'] as String?,
        icons: (j['icons'] as List?)?.map((e) => e.toString()).toList(),
        motion: j['motion'] as String?,
        title: j['title'] as String?,
        tag: j['tag'] as String?,
        kind: j['kind'] as String?,
      );
}

/// 不丢句硬校验(ratio=字符覆盖率)+ 软质检 warnings。
class WudaoziQC {
  const WudaoziQC({this.verdict, this.ratio, this.warnings});

  final String? verdict;
  final double? ratio;
  final List<String>? warnings;

  factory WudaoziQC.fromJson(Map<String, dynamic> j) => WudaoziQC(
        verdict: j['verdict'] as String?,
        ratio: (j['ratio'] as num?)?.toDouble(),
        warnings: (j['warnings'] as List?)?.map((e) => e.toString()).toList(),
      );
}

// —— 伯牙:声音床方案(result = audio_plan) ——

class BoyaResult {
  const BoyaResult({this.job, this.scene, this.voice, this.bgm, this.sfx, this.audition});

  final String? job;
  final String? scene;
  final BoyaVoice? voice;
  final BoyaBGM? bgm; // 库内无可用 BGM 时为 null
  final List<BoyaSfxCue>? sfx;
  final BoyaAudition? audition;

  factory BoyaResult.fromJson(Map<String, dynamic> j) => BoyaResult(
        job: j['job'] as String?,
        scene: j['scene'] as String?,
        voice: j['voice'] == null ? null : BoyaVoice.fromJson((j['voice'] as Map).cast<String, dynamic>()),
        bgm: j['bgm'] == null ? null : BoyaBGM.fromJson((j['bgm'] as Map).cast<String, dynamic>()),
        sfx: (j['sfx'] as List?)
            ?.map((e) => BoyaSfxCue.fromJson((e as Map).cast<String, dynamic>()))
            .toList(),
        audition: j['audition'] == null
            ? null
            : BoyaAudition.fromJson((j['audition'] as Map).cast<String, dynamic>()),
      );
}

class BoyaVoice {
  const BoyaVoice({this.clips, this.durationS});
  final int? clips;
  final double? durationS;

  factory BoyaVoice.fromJson(Map<String, dynamic> j) => BoyaVoice(
        clips: (j['clips'] as num?)?.toInt(),
        durationS: (j['duration_s'] as num?)?.toDouble(),
      );
}

class BoyaBGM {
  const BoyaBGM({this.file, this.volumeDb, this.reason});
  final String? file;
  final double? volumeDb;
  final String? reason;

  factory BoyaBGM.fromJson(Map<String, dynamic> j) => BoyaBGM(
        file: j['file'] as String?,
        volumeDb: (j['volume_db'] as num?)?.toDouble(),
        reason: j['reason'] as String?,
      );
}

class BoyaSfxCue {
  const BoyaSfxCue({this.beat, this.kind, this.cue, this.timeS, this.file, this.reason});
  final int? beat;
  final String? kind;
  final String? cue;
  final double? timeS;
  final String? file;
  final String? reason;

  factory BoyaSfxCue.fromJson(Map<String, dynamic> j) => BoyaSfxCue(
        beat: (j['beat'] as num?)?.toInt(),
        kind: j['kind'] as String?,
        cue: j['cue'] as String?,
        timeS: (j['time_s'] as num?)?.toDouble(),
        file: j['file'] as String?,
        reason: j['reason'] as String?,
      );
}

/// 听感质检:verdict ∈ ok/warn,notes 是逐条提醒(语速过快/过慢等)。
class BoyaAudition {
  const BoyaAudition({this.verdict, this.voiceTotalS, this.notes});
  final String? verdict;
  final double? voiceTotalS;
  final List<String>? notes;

  factory BoyaAudition.fromJson(Map<String, dynamic> j) => BoyaAudition(
        verdict: j['verdict'] as String?,
        voiceTotalS: (j['voice_total_s'] as num?)?.toDouble(),
        notes: (j['notes'] as List?)?.map((e) => e.toString()).toList(),
      );
}

// —— 卧龙:编排战报(legacy 摘要;逐轮产线看 round 战报页) ——

class WolongResult {
  const WolongResult({this.count, this.reviewDir, this.tail});
  final int? count;
  final String? reviewDir;
  final List<String>? tail; // 编排日志末 20 行

  factory WolongResult.fromJson(Map<String, dynamic> j) => WolongResult(
        count: (j['count'] as num?)?.toInt(),
        reviewDir: j['review_dir'] as String?,
        tail: (j['tail'] as List?)?.map((e) => e.toString()).toList(),
      );
}

// —— 柳永:双稿(drafts)+ AI 味扫描 + opus rubric 五维打分 ——

class LiuyongResult {
  const LiuyongResult({this.drafts, this.jobId, this.deliverablesDir});
  final List<LiuyongDraft>? drafts;
  final String? jobId;
  final String? deliverablesDir;

  factory LiuyongResult.fromJson(Map<String, dynamic> j) => LiuyongResult(
        drafts: (j['drafts'] as List?)
            ?.map((e) => LiuyongDraft.fromJson((e as Map).cast<String, dynamic>()))
            .toList(),
        jobId: j['job_id'] as String?,
        deliverablesDir: j['deliverables_dir'] as String?,
      );
}

class LiuyongDraft {
  const LiuyongDraft({this.model, this.text, this.qc, this.qcRubric});
  final String? model;
  final String? text;
  final AiTasteQC? qc; // AI 味扫描
  final RubricQC? qcRubric; // opus rubric 打分

  factory LiuyongDraft.fromJson(Map<String, dynamic> j) => LiuyongDraft(
        model: j['model'] as String?,
        text: j['text'] as String?,
        qc: j['qc'] == null ? null : AiTasteQC.fromJson((j['qc'] as Map).cast<String, dynamic>()),
        qcRubric: j['qc_rubric'] == null
            ? null
            : RubricQC.fromJson((j['qc_rubric'] as Map).cast<String, dynamic>()),
      );
}

/// AI 味扫描:verdict=fail 表示曾被打回重写;density/hard 是命中的句式。
class AiTasteQC {
  const AiTasteQC({this.verdict, this.summary, this.density, this.hard});
  final String? verdict;
  final String? summary;
  final List<QCHit>? density;
  final List<QCHit>? hard;

  factory AiTasteQC.fromJson(Map<String, dynamic> j) => AiTasteQC(
        verdict: j['verdict'] as String?,
        summary: j['summary'] as String?,
        density: (j['density'] as List?)
            ?.map((e) => QCHit.fromJson((e as Map).cast<String, dynamic>()))
            .toList(),
        hard: (j['hard'] as List?)
            ?.map((e) => QCHit.fromJson((e as Map).cast<String, dynamic>()))
            .toList(),
      );
}

class QCHit {
  const QCHit({this.rule, this.count, this.threshold, this.severity, this.samples});
  final String? rule;
  final int? count;
  final int? threshold;
  final String? severity;
  final List<String>? samples;

  factory QCHit.fromJson(Map<String, dynamic> j) => QCHit(
        rule: j['rule'] as String?,
        count: (j['count'] as num?)?.toInt(),
        threshold: (j['threshold'] as num?)?.toInt(),
        severity: j['severity'] as String?,
        samples: (j['samples'] as List?)?.map((e) => e.toString()).toList(),
      );
}

/// opus rubric:5 维各 /10,total /50,grade=优秀/良好/需重修;不可用时 available=false。
class RubricQC {
  const RubricQC({this.available, this.dims, this.total, this.grade, this.issues, this.skipped});
  final bool? available;
  final Map<String, int>? dims;
  final int? total;
  final String? grade;
  final List<String>? issues;
  final String? skipped;

  factory RubricQC.fromJson(Map<String, dynamic> j) => RubricQC(
        available: j['available'] as bool?,
        dims: (j['dims'] as Map?)?.map((k, v) => MapEntry(k.toString(), v is num ? v.toInt() : 0)),
        total: (j['total'] as num?)?.toInt(),
        grade: j['grade'] as String?,
        issues: (j['issues'] as List?)?.map((e) => e.toString()).toList(),
        skipped: j['skipped'] as String?,
      );
}

class TaskCreateResponse {
  const TaskCreateResponse({required this.taskId, required this.status});

  final String taskId;
  final String status;

  factory TaskCreateResponse.fromJson(Map<String, dynamic> j) => TaskCreateResponse(
        taskId: j['task_id'] as String? ?? '',
        status: j['status'] as String? ?? '',
      );
}

/// SSE 进度事件。type ∈ progress/done/error。
class TaskEvent {
  const TaskEvent({required this.type, this.ts, this.text, this.error});

  final String type;
  final int? ts;
  final String? text;
  final String? error;

  factory TaskEvent.fromJson(Map<String, dynamic> j) => TaskEvent(
        type: j['type'] as String? ?? '',
        ts: (j['ts'] as num?)?.toInt(),
        text: j['text'] as String?,
        error: j['error'] as String?,
      );
}

// —— Commands / Schema:GET /commands/{cmd}/schema 表单建模 ——

/// 一个表单字段。type ∈ string/text/int/float/bool/string[]/enum。
/// defaultValue 用 dynamic(后端可能是 string/int/float/bool),取展示文本即可。
class NofField {
  const NofField({
    required this.name,
    required this.label,
    required this.type,
    this.required,
    this.defaultValue,
    this.enumValues,
    this.help,
  });

  final String name;
  final String label;
  final String type;
  final bool? required; // 后端键名 required
  final dynamic defaultValue; // 后端键名 default(JSONValue)
  final List<String>? enumValues; // 后端键名 enum
  final String? help;

  bool get isRequired => required ?? false;

  factory NofField.fromJson(Map<String, dynamic> j) => NofField(
        name: j['name'] as String? ?? '',
        label: j['label'] as String? ?? '',
        type: j['type'] as String? ?? '',
        required: j['required'] as bool?,
        defaultValue: j['default'],
        enumValues: (j['enum'] as List?)?.map((e) => e.toString()).toList(),
        help: j['help'] as String?,
      );
}

/// GET /commands/{cmd}/schema 表单定义。fields 缺省解空表(不炸表单页)。
class CommandSchema {
  const CommandSchema({
    this.cmd,
    this.label,
    this.group,
    this.summary,
    required this.fields,
  });

  final String? cmd;
  final String? label;
  final String? group;
  final String? summary;
  final List<NofField> fields;

  factory CommandSchema.fromJson(Map<String, dynamic> j) => CommandSchema(
        cmd: j['cmd'] as String?,
        label: j['label'] as String?,
        group: j['group'] as String?,
        summary: j['summary'] as String?,
        fields: (j['fields'] as List?)
                ?.map((e) => NofField.fromJson((e as Map).cast<String, dynamic>()))
                .toList() ??
            const <NofField>[],
      );
}

// —— Round 战报页:GET /rounds(摘要列表) 与 GET /rounds/{id}(round 文件全量) ——
// report 两种形态合一(正常收盘 / 终止),字段全可空——共用一个模型,缺键解 null。

/// 列表项里的产线摘要(title 是后端截断到 60 字的选题标题)。
class RoundLineSummary {
  const RoundLineSummary({this.slot, this.status, this.title, this.rework});

  final int? slot;
  final String? status;
  final String? title;
  final int? rework;

  factory RoundLineSummary.fromJson(Map<String, dynamic> j) => RoundLineSummary(
        slot: (j['slot'] as num?)?.toInt(),
        status: j['status'] as String?,
        title: j['title'] as String?,
        rework: (j['rework'] as num?)?.toInt(),
      );
}

/// 预筛战绩(仅正常收盘且启用预筛的 round 有)。
class RoundPrescreenStats {
  const RoundPrescreenStats({this.intercepted, this.explore, this.falseNegatives});

  final int? intercepted;
  final int? explore;
  final int? falseNegatives; // 后端键名 false_negatives

  factory RoundPrescreenStats.fromJson(Map<String, dynamic> j) => RoundPrescreenStats(
        intercepted: (j['intercepted'] as num?)?.toInt(),
        explore: (j['explore'] as num?)?.toInt(),
        falseNegatives: (j['false_negatives'] as num?)?.toInt(),
      );
}

/// round 战报。正常收盘与终止两种形态合一,字段全可空。
class RoundReport {
  const RoundReport({
    this.approved,
    this.killed,
    this.reworkTotal,
    this.approvedTasks,
    this.rubricVersion,
    this.rejectRate,
    this.reworkRate,
    this.prescreen,
    this.reason,
    this.summaryLines,
    this.finishedAt,
  });

  final int? approved;
  final int? killed;
  final int? reworkTotal; // rework_total
  final List<String>? approvedTasks; // approved_tasks
  final int? rubricVersion; // rubric_version
  final double? rejectRate; // reject_rate
  final double? reworkRate; // rework_rate
  final RoundPrescreenStats? prescreen;
  final String? reason; // 仅终止形态:止损原因
  final List<String>? summaryLines; // summary_lines
  final String? finishedAt; // finished_at

  factory RoundReport.fromJson(Map<String, dynamic> j) => RoundReport(
        approved: (j['approved'] as num?)?.toInt(),
        killed: (j['killed'] as num?)?.toInt(),
        reworkTotal: (j['rework_total'] as num?)?.toInt(),
        approvedTasks: (j['approved_tasks'] as List?)?.map((e) => e.toString()).toList(),
        rubricVersion: (j['rubric_version'] as num?)?.toInt(),
        rejectRate: (j['reject_rate'] as num?)?.toDouble(),
        reworkRate: (j['rework_rate'] as num?)?.toDouble(),
        prescreen: j['prescreen'] == null
            ? null
            : RoundPrescreenStats.fromJson((j['prescreen'] as Map).cast<String, dynamic>()),
        reason: j['reason'] as String?,
        summaryLines: (j['summary_lines'] as List?)?.map((e) => e.toString()).toList(),
        finishedAt: j['finished_at'] as String?,
      );
}

/// GET /rounds 列表项。status ∈ active/done/terminated,stage ∈ topics/scripts/done。
class RoundSummary {
  const RoundSummary({
    required this.roundId,
    this.status,
    this.stage,
    this.createdAt,
    this.updatedAt,
    this.goalCount,
    this.lines,
    this.report,
  });

  final String roundId; // round_id
  final String? status;
  final String? stage;
  final String? createdAt; // created_at
  final String? updatedAt; // updated_at
  final int? goalCount; // goal_count
  final List<RoundLineSummary>? lines;
  final RoundReport? report;

  factory RoundSummary.fromJson(Map<String, dynamic> j) => RoundSummary(
        roundId: j['round_id'] as String? ?? '',
        status: j['status'] as String?,
        stage: j['stage'] as String?,
        createdAt: j['created_at'] as String?,
        updatedAt: j['updated_at'] as String?,
        goalCount: (j['goal_count'] as num?)?.toInt(),
        lines: (j['lines'] as List?)
            ?.map((e) => RoundLineSummary.fromJson((e as Map).cast<String, dynamic>()))
            .toList(),
        report: j['report'] == null
            ? null
            : RoundReport.fromJson((j['report'] as Map).cast<String, dynamic>()),
      );
}

/// round 开盘目标(round 文件 goal 节)。
class RoundGoal {
  const RoundGoal({this.count, this.benchmarkPath, this.avoid, this.rubricVersion});

  final int? count;
  final String? benchmarkPath; // benchmark_path
  final String? avoid;
  final int? rubricVersion; // rubric_version

  factory RoundGoal.fromJson(Map<String, dynamic> j) => RoundGoal(
        count: (j['count'] as num?)?.toInt(),
        benchmarkPath: j['benchmark_path'] as String?,
        avoid: j['avoid'] as String?,
        rubricVersion: (j['rubric_version'] as num?)?.toInt(),
      );
}

/// round 文件里的一条产线。notes 是历次打回意见(续跑返工的需求注入来源)。
/// topic 用 Map 原样带回(对齐鬼谷子选题字段,本层不强解)。
class RoundDetailLine {
  const RoundDetailLine({
    this.slot,
    this.status,
    this.taskId,
    this.rework,
    this.topic,
    this.notes,
  });

  final int? slot;
  final String? status;
  final String? taskId; // task_id
  final int? rework;
  final Map<String, dynamic>? topic; // 鬼谷子选题(title/motif/...),原样带回
  final List<String>? notes;

  factory RoundDetailLine.fromJson(Map<String, dynamic> j) => RoundDetailLine(
        slot: (j['slot'] as num?)?.toInt(),
        status: j['status'] as String?,
        taskId: j['task_id'] as String?,
        rework: (j['rework'] as num?)?.toInt(),
        topic: (j['topic'] as Map?)?.cast<String, dynamic>(),
        notes: (j['notes'] as List?)?.map((e) => e.toString()).toList(),
      );
}

/// GET /rounds/{id} round 文件全量。intents/events 本期不建模,解码时忽略。
class RoundDetail {
  const RoundDetail({
    required this.roundId,
    this.status,
    this.stage,
    this.createdAt,
    this.updatedAt,
    this.goal,
    this.lines,
    this.report,
  });

  final String roundId; // round_id
  final String? status;
  final String? stage;
  final String? createdAt; // created_at
  final String? updatedAt; // updated_at
  final RoundGoal? goal;
  final List<RoundDetailLine>? lines;
  final RoundReport? report;

  factory RoundDetail.fromJson(Map<String, dynamic> j) => RoundDetail(
        roundId: j['round_id'] as String? ?? '',
        status: j['status'] as String?,
        stage: j['stage'] as String?,
        createdAt: j['created_at'] as String?,
        updatedAt: j['updated_at'] as String?,
        goal: j['goal'] == null ? null : RoundGoal.fromJson((j['goal'] as Map).cast<String, dynamic>()),
        lines: (j['lines'] as List?)
            ?.map((e) => RoundDetailLine.fromJson((e as Map).cast<String, dynamic>()))
            .toList(),
        report: j['report'] == null
            ? null
            : RoundReport.fromJson((j['report'] as Map).cast<String, dynamic>()),
      );
}

// —— 订阅管理:GET/PUT /subscriptions ——
// PUT 是整体覆盖写:页面应先 GET 拿全量,改完原对象 toJson 回写——别用局部构造的配置覆盖。

/// 一个订阅作者。enabled 后端缺省 true(null 视为 true);note 是人读备注。
class SubscriptionAuthor {
  const SubscriptionAuthor({required this.secUid, this.note, this.enabled});

  final String secUid; // sec_uid
  final String? note;
  final bool? enabled;

  bool get isEnabled => enabled ?? true;

  factory SubscriptionAuthor.fromJson(Map<String, dynamic> j) => SubscriptionAuthor(
        secUid: j['sec_uid'] as String? ?? '',
        note: j['note'] as String?,
        enabled: j['enabled'] as bool?,
      );

  Map<String, dynamic> toJson() => <String, dynamic>{
        'sec_uid': secUid,
        if (note != null) 'note': note,
        if (enabled != null) 'enabled': enabled,
      };

  SubscriptionAuthor copyWith({String? secUid, String? note, bool? enabled}) => SubscriptionAuthor(
        secUid: secUid ?? this.secUid,
        note: note ?? this.note,
        enabled: enabled ?? this.enabled,
      );
}

/// 订阅配置全量。intervalHours 后端缺省 2.0(PUT 缺键时由后端补默认)。
class SubscriptionsConfig {
  const SubscriptionsConfig({this.intervalHours, this.authors});

  final double? intervalHours; // interval_hours
  final List<SubscriptionAuthor>? authors;

  factory SubscriptionsConfig.fromJson(Map<String, dynamic> j) => SubscriptionsConfig(
        intervalHours: (j['interval_hours'] as num?)?.toDouble(),
        authors: (j['authors'] as List?)
            ?.map((e) => SubscriptionAuthor.fromJson((e as Map).cast<String, dynamic>()))
            .toList(),
      );

  /// PUT 整体覆盖:authors 为 null/缺省会被后端当成空表清光,故只在非 null 时带键。
  Map<String, dynamic> toJson() => <String, dynamic>{
        if (intervalHours != null) 'interval_hours': intervalHours,
        if (authors != null) 'authors': authors!.map((a) => a.toJson()).toList(),
      };

  SubscriptionsConfig copyWith({double? intervalHours, List<SubscriptionAuthor>? authors}) =>
      SubscriptionsConfig(
        intervalHours: intervalHours ?? this.intervalHours,
        authors: authors ?? this.authors,
      );
}

/// POST /subscriptions/tick 响应:本轮实际派发的刷新任务数。
class SubscriptionsTickResponse {
  const SubscriptionsTickResponse({this.submitted});

  final int? submitted;

  factory SubscriptionsTickResponse.fromJson(Map<String, dynamic> j) =>
      SubscriptionsTickResponse(submitted: (j['submitted'] as num?)?.toInt());
}

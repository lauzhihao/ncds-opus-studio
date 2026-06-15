// 发起任务页:按 GET /commands 列命令、GET /commands/{cmd}/schema 动态渲染表单。
// 移植自 iOS AgentComposeSheet —— 字段说明书运行时取,前端不硬编码各命令字段,
// 新 agent / 字段变更零代码适配。两步走:先选命令(按 group 分组),再填表单提交。
// 提交成功后 Navigator.pop(context, taskId)。

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../core/net/endpoint_resolver.dart';
import '../../core/net/factory_client.dart';
import '../../core/net/models.dart';
import '../../design/components/app_card.dart';
import '../../design/components/decision_button.dart';
import '../../design/components/tag_chip.dart';
import '../../design/tokens.dart';
import '../../design/typography.dart';

// 命令 group → 主题色(对齐 tokens 里的 agent 主题色;未知 group 兜底 orange)。
Color _groupTint(String group) {
  switch (group) {
    case 'wolong':
    case '操盘':
      return AppColors.accentWolong;
    case 'collect':
    case '采集':
      return AppColors.accentCollect;
    case 'topic':
    case '选题':
      return AppColors.accentTopic;
    case 'script':
    case '编剧':
      return AppColors.accentScript;
    case 'art':
    case '美术':
      return AppColors.accentArt;
    case 'sound':
    case '声音':
      return AppColors.accentSound;
    default:
      return AppColors.orange;
  }
}

/// 发起任务页。先选命令,再按命令 schema 动态渲染表单并提交。
/// [client] 可注入便于测试;为 null 时走 LAN 直连工厂后端。
class ComposeScreen extends StatefulWidget {
  const ComposeScreen({super.key, this.client});

  final FactoryClient? client;

  @override
  State<ComposeScreen> createState() => _ComposeScreenState();
}

class _ComposeScreenState extends State<ComposeScreen> {
  static const String _devBase = 'http://liuzhihao-mbp.local:8810';

  late final FactoryClient _client =
      widget.client ?? FactoryClient(resolver: DirectEndpointResolver(_devBase));

  late Future<List<NofCommand>> _commandsFuture = _client.listCommands();

  // 选中的命令;为 null 时停在「选命令」一步。
  NofCommand? _selected;

  void _refreshCommands() {
    setState(() => _commandsFuture = _client.listCommands());
  }

  void _pick(NofCommand cmd) => setState(() => _selected = cmd);

  void _backToPicker() => setState(() => _selected = null);

  @override
  Widget build(BuildContext context) {
    final selected = _selected;
    return Scaffold(
      backgroundColor: AppColors.sand,
      appBar: AppBar(
        backgroundColor: AppColors.sand,
        foregroundColor: AppColors.ink,
        leading: selected != null
            ? IconButton(
                tooltip: '重选命令',
                icon: const Icon(Icons.arrow_back),
                onPressed: _backToPicker,
              )
            : null,
        title: Text(
          selected == null ? '发起任务' : '交给${selected.label.isEmpty ? selected.name : selected.label}',
          style: AppTypography.navTitle,
        ),
      ),
      body: selected == null
          ? _CommandPicker(
              future: _commandsFuture,
              onRetry: _refreshCommands,
              onPick: _pick,
            )
          : _SchemaForm(
              key: ValueKey<String>(selected.name),
              client: _client,
              command: selected,
            ),
    );
  }
}

// —— 第一步:选命令(按 group 分组) ——

class _CommandPicker extends StatelessWidget {
  const _CommandPicker({required this.future, required this.onRetry, required this.onPick});

  final Future<List<NofCommand>> future;
  final VoidCallback onRetry;
  final void Function(NofCommand) onPick;

  @override
  Widget build(BuildContext context) {
    return FutureBuilder<List<NofCommand>>(
      future: future,
      builder: (context, snap) {
        if (snap.connectionState == ConnectionState.waiting) {
          return const _Centered(child: CircularProgressIndicator());
        }
        if (snap.hasError) {
          return _Centered(child: _ErrorState(message: '${snap.error}', onRetry: onRetry));
        }
        final commands = snap.data ?? const <NofCommand>[];
        if (commands.isEmpty) {
          return _Centered(
            child: Text('暂无可用命令', style: AppTypography.body.copyWith(color: AppColors.inkMuted)),
          );
        }

        // 按 group 分组,保留首次出现顺序。
        final groups = <String, List<NofCommand>>{};
        for (final c in commands) {
          groups.putIfAbsent(c.group, () => <NofCommand>[]).add(c);
        }

        final children = <Widget>[];
        groups.forEach((group, cmds) {
          children.add(_GroupHeader(group: group));
          for (final c in cmds) {
            children.add(Padding(
              padding: const EdgeInsets.only(bottom: AppSpacing.m),
              child: _CommandCard(command: c, onTap: () => onPick(c)),
            ));
          }
          children.add(const SizedBox(height: AppSpacing.s));
        });

        return ListView(
          physics: const AlwaysScrollableScrollPhysics(),
          padding: const EdgeInsets.fromLTRB(
            AppSpacing.pageH, AppSpacing.m, AppSpacing.pageH, AppSpacing.pageBottom,
          ),
          children: children,
        );
      },
    );
  }
}

class _GroupHeader extends StatelessWidget {
  const _GroupHeader({required this.group});
  final String group;

  @override
  Widget build(BuildContext context) {
    final label = group.isEmpty ? '其他' : group;
    return Padding(
      padding: const EdgeInsets.only(bottom: AppSpacing.s, top: AppSpacing.xs),
      child: Row(
        children: [
          Container(
            width: 3,
            height: 14,
            decoration: BoxDecoration(
              color: _groupTint(group),
              borderRadius: BorderRadius.circular(2),
            ),
          ),
          const SizedBox(width: AppSpacing.s),
          Text(label, style: AppTypography.subhead.copyWith(color: AppColors.inkMuted)),
        ],
      ),
    );
  }
}

class _CommandCard extends StatelessWidget {
  const _CommandCard({required this.command, required this.onTap});
  final NofCommand command;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final tint = _groupTint(command.group);
    final title = command.label.isEmpty ? command.name : command.label;
    return GestureDetector(
      onTap: onTap,
      behavior: HitTestBehavior.opaque,
      child: AppCard(
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Expanded(
                        child: Text(title, style: AppTypography.titleM, maxLines: 1, overflow: TextOverflow.ellipsis),
                      ),
                      const SizedBox(width: AppSpacing.s),
                      TagChip(label: command.name, tint: tint),
                    ],
                  ),
                  if (command.summary.isNotEmpty) ...[
                    const SizedBox(height: AppSpacing.xs),
                    Text(
                      command.summary,
                      style: AppTypography.caption.copyWith(color: AppColors.inkMuted),
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ],
                ],
              ),
            ),
            const SizedBox(width: AppSpacing.s),
            Icon(Icons.chevron_right, color: AppColors.inkFaint, size: 22),
          ],
        ),
      ),
    );
  }
}

// —— 第二步:动态表单 ——

class _SchemaForm extends StatefulWidget {
  const _SchemaForm({super.key, required this.client, required this.command});

  final FactoryClient client;
  final NofCommand command;

  @override
  State<_SchemaForm> createState() => _SchemaFormState();
}

class _SchemaFormState extends State<_SchemaForm> {
  late Future<CommandSchema> _schemaFuture = widget.client.schema(widget.command.name);

  // 文本类字段(string/text/int/float/enum/string[])的输入暂存。
  final Map<String, TextEditingController> _controllers = <String, TextEditingController>{};
  // bool 字段。
  final Map<String, bool> _flags = <String, bool>{};
  // enum 字段当前选中值(下拉)。
  final Map<String, String?> _enums = <String, String?>{};

  CommandSchema? _schema;
  bool _submitting = false;
  String? _submitError;
  // 提交时触发的必填校验:命中后高亮缺失字段。
  bool _showErrors = false;

  void _reloadSchema() {
    setState(() {
      _schema = null;
      _schemaFuture = widget.client.schema(widget.command.name);
    });
  }

  // 拿到 schema 后初始化各字段默认值(对齐 iOS:enum 无默认选第一项,bool 取 default)。
  void _bind(CommandSchema schema) {
    if (_schema != null) return; // 已绑定过,FutureBuilder 重建不重复绑
    _schema = schema;
    for (final f in schema.fields) {
      switch (f.type) {
        case 'bool':
          _flags[f.name] = _asBool(f.defaultValue);
          break;
        case 'enum':
          final def = _display(f.defaultValue);
          final options = f.enumValues ?? const <String>[];
          _enums[f.name] = (def != null && def.isNotEmpty && options.contains(def))
              ? def
              : (options.isNotEmpty ? options.first : null);
          break;
        default:
          final def = _display(f.defaultValue);
          _controllers[f.name] = TextEditingController(text: def ?? '');
      }
    }
  }

  TextEditingController _controllerFor(String name) =>
      _controllers.putIfAbsent(name, () => TextEditingController());

  @override
  void dispose() {
    for (final c in _controllers.values) {
      c.dispose();
    }
    super.dispose();
  }

  static bool _asBool(dynamic v) {
    if (v is bool) return v;
    if (v is String) return v.toLowerCase() == 'true';
    return false;
  }

  static String? _display(dynamic v) {
    if (v == null) return null;
    if (v is String) return v;
    if (v is bool) return v ? 'true' : 'false';
    return v.toString();
  }

  // 必填校验:bool 永远满足;enum 需有选中;其余需非空文本。
  bool _isFilled(NofField f) {
    if (!f.isRequired) return true;
    switch (f.type) {
      case 'bool':
        return true;
      case 'enum':
        return (_enums[f.name] ?? '').isNotEmpty;
      default:
        return _controllerFor(f.name).text.trim().isNotEmpty;
    }
  }

  bool get _canSubmit {
    final schema = _schema;
    if (schema == null || _submitting) return false;
    return schema.fields.every(_isFilled);
  }

  // 按 type 把输入转回原生类型组装 params;空值不发,让后端用默认值。
  Map<String, dynamic> _buildParams(CommandSchema schema) {
    final params = <String, dynamic>{};
    for (final f in schema.fields) {
      switch (f.type) {
        case 'bool':
          params[f.name] = _flags[f.name] ?? false;
          break;
        case 'enum':
          final v = _enums[f.name];
          if (v != null && v.isNotEmpty) params[f.name] = v;
          break;
        case 'string[]':
          final raw = _controllerFor(f.name).text.trim();
          if (raw.isEmpty) break;
          final list = raw
              .split(',')
              .map((e) => e.trim())
              .where((e) => e.isNotEmpty)
              .toList();
          if (list.isNotEmpty) params[f.name] = list;
          break;
        case 'int':
          final raw = _controllerFor(f.name).text.trim();
          if (raw.isEmpty) break;
          params[f.name] = int.tryParse(raw) ?? raw;
          break;
        case 'float':
          final raw = _controllerFor(f.name).text.trim();
          if (raw.isEmpty) break;
          params[f.name] = double.tryParse(raw) ?? raw;
          break;
        default: // string / text
          final raw = _controllerFor(f.name).text.trim();
          if (raw.isNotEmpty) params[f.name] = raw;
      }
    }
    return params;
  }

  Future<void> _submit(CommandSchema schema) async {
    if (!_canSubmit) {
      setState(() => _showErrors = true);
      return;
    }
    setState(() {
      _submitting = true;
      _submitError = null;
    });
    try {
      final taskId = await widget.client.createTask(
        cmd: widget.command.name,
        params: _buildParams(schema),
      );
      if (!mounted) return;
      Navigator.pop(context, taskId);
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _submitError = '$e';
        _submitting = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return FutureBuilder<CommandSchema>(
      future: _schemaFuture,
      builder: (context, snap) {
        if (snap.connectionState == ConnectionState.waiting) {
          return const _Centered(child: CircularProgressIndicator());
        }
        if (snap.hasError) {
          return _Centered(child: _ErrorState(message: '${snap.error}', onRetry: _reloadSchema));
        }
        final schema = snap.data;
        if (schema == null) {
          return _Centered(
            child: Text('拿不到表单', style: AppTypography.body.copyWith(color: AppColors.inkMuted)),
          );
        }
        _bind(schema);
        return _buildForm(schema);
      },
    );
  }

  Widget _buildForm(CommandSchema schema) {
    final tint = _groupTint(widget.command.group);
    final required = schema.fields.where((f) => f.isRequired).toList();
    final optional = schema.fields.where((f) => !f.isRequired).toList();
    final summary = schema.summary;

    return Column(
      children: [
        Expanded(
          child: ListView(
            physics: const AlwaysScrollableScrollPhysics(),
            padding: const EdgeInsets.fromLTRB(
              AppSpacing.pageH, AppSpacing.m, AppSpacing.pageH, AppSpacing.m,
            ),
            children: [
              if (summary != null && summary.isNotEmpty) ...[
                Text(summary, style: AppTypography.caption.copyWith(color: AppColors.inkMuted)),
                const SizedBox(height: AppSpacing.m),
              ],
              if (schema.fields.isEmpty)
                AppCard(
                  child: Text(
                    '该命令无需参数,可直接发起。',
                    style: AppTypography.body.copyWith(color: AppColors.inkMuted),
                  ),
                ),
              if (required.isNotEmpty) ...[
                _SectionLabel(label: '必填', tint: tint),
                const SizedBox(height: AppSpacing.s),
                AppCard(child: _fieldColumn(required)),
              ],
              if (optional.isNotEmpty) ...[
                const SizedBox(height: AppSpacing.block),
                _SectionLabel(label: '选填', tint: AppColors.inkMuted),
                const SizedBox(height: AppSpacing.s),
                AppCard(child: _fieldColumn(optional)),
              ],
              if (_submitError != null) ...[
                const SizedBox(height: AppSpacing.m),
                Text(
                  _submitError!,
                  style: AppTypography.caption.copyWith(color: AppColors.statusRed),
                ),
              ],
            ],
          ),
        ),
        // 底部提交栏。
        Container(
          padding: const EdgeInsets.fromLTRB(
            AppSpacing.pageH, AppSpacing.m, AppSpacing.pageH, AppSpacing.pageBottom,
          ),
          decoration: BoxDecoration(
            color: AppColors.sand,
            boxShadow: [
              BoxShadow(
                color: AppColors.cardShadow,
                blurRadius: 8,
                offset: const Offset(0, -2),
              ),
            ],
          ),
          child: DecisionButton(
            label: _submitting ? '发起中…' : '发起任务',
            tint: tint,
            onPressed: _submitting ? () {} : () => _submit(schema),
          ),
        ),
      ],
    );
  }

  Widget _fieldColumn(List<NofField> fields) {
    final children = <Widget>[];
    for (var i = 0; i < fields.length; i++) {
      if (i > 0) children.add(const SizedBox(height: AppSpacing.block));
      children.add(_fieldRow(fields[i]));
    }
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: children);
  }

  Widget _fieldRow(NofField f) {
    final missing = _showErrors && !_isFilled(f);
    switch (f.type) {
      case 'bool':
        return _BoolField(
          field: f,
          value: _flags[f.name] ?? false,
          tint: _groupTint(widget.command.group),
          onChanged: (v) => setState(() => _flags[f.name] = v),
        );
      case 'enum':
        return _EnumField(
          field: f,
          value: _enums[f.name],
          missing: missing,
          onChanged: (v) => setState(() => _enums[f.name] = v),
        );
      default:
        return _TextField(
          field: f,
          controller: _controllerFor(f.name),
          missing: missing,
          onChanged: () {
            if (_showErrors) setState(() {});
          },
        );
    }
  }
}

// —— 字段控件 ——

// 字段标签 + required 星标 + help 说明,文本/枚举类字段共用的表头。
class _FieldLabel extends StatelessWidget {
  const _FieldLabel({required this.field, this.missing = false});
  final NofField field;
  final bool missing;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Flexible(
              child: Text(
                field.label.isEmpty ? field.name : field.label,
                style: AppTypography.subhead.copyWith(
                  color: missing ? AppColors.statusRed : AppColors.ink,
                ),
              ),
            ),
            if (field.isRequired) ...[
              const SizedBox(width: 3),
              Text('*', style: AppTypography.subhead.copyWith(color: AppColors.statusRed)),
            ],
          ],
        ),
        if (field.help != null && field.help!.isNotEmpty) ...[
          const SizedBox(height: 2),
          Text(field.help!, style: AppTypography.caption.copyWith(color: AppColors.inkMuted)),
        ],
      ],
    );
  }
}

class _TextField extends StatelessWidget {
  const _TextField({
    required this.field,
    required this.controller,
    required this.missing,
    required this.onChanged,
  });

  final NofField field;
  final TextEditingController controller;
  final bool missing;
  final VoidCallback onChanged;

  @override
  Widget build(BuildContext context) {
    final isMultiline = field.type == 'text';
    final isInt = field.type == 'int';
    final isFloat = field.type == 'float';
    final keyboard = isInt
        ? TextInputType.number
        : isFloat
            ? const TextInputType.numberWithOptions(decimal: true)
            : (isMultiline ? TextInputType.multiline : TextInputType.text);
    final inputFormatters = isInt
        ? <TextInputFormatter>[FilteringTextInputFormatter.allow(RegExp(r'[0-9-]'))]
        : isFloat
            ? <TextInputFormatter>[FilteringTextInputFormatter.allow(RegExp(r'[0-9.\-]'))]
            : const <TextInputFormatter>[];

    final hint = field.type == 'string[]'
        ? (field.help != null && field.help!.isNotEmpty ? '${field.help}(逗号分隔)' : '逗号分隔多个值')
        : '';

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _FieldLabel(field: field, missing: missing),
        const SizedBox(height: AppSpacing.s),
        TextField(
          controller: controller,
          onChanged: (_) => onChanged(),
          keyboardType: keyboard,
          inputFormatters: inputFormatters,
          minLines: isMultiline ? 2 : 1,
          maxLines: isMultiline ? 6 : 1,
          style: AppTypography.bodyInput,
          decoration: InputDecoration(
            isDense: true,
            hintText: hint,
            hintStyle: AppTypography.bodyInput.copyWith(color: AppColors.inkFaint),
            contentPadding: const EdgeInsets.symmetric(
              horizontal: AppSpacing.m, vertical: AppSpacing.s + 2,
            ),
            filled: true,
            fillColor: AppColors.sand.withValues(alpha: 0.45),
            enabledBorder: OutlineInputBorder(
              borderRadius: BorderRadius.circular(AppRadii.capture),
              borderSide: BorderSide(
                color: missing ? AppColors.statusRed : AppColors.ink.withValues(alpha: 0.15),
              ),
            ),
            focusedBorder: OutlineInputBorder(
              borderRadius: BorderRadius.circular(AppRadii.capture),
              borderSide: BorderSide(color: AppColors.orange, width: 1.5),
            ),
          ),
        ),
      ],
    );
  }
}

class _EnumField extends StatelessWidget {
  const _EnumField({
    required this.field,
    required this.value,
    required this.missing,
    required this.onChanged,
  });

  final NofField field;
  final String? value;
  final bool missing;
  final ValueChanged<String?> onChanged;

  @override
  Widget build(BuildContext context) {
    final options = field.enumValues ?? const <String>[];
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _FieldLabel(field: field, missing: missing),
        const SizedBox(height: AppSpacing.s),
        Container(
          padding: const EdgeInsets.symmetric(horizontal: AppSpacing.m),
          decoration: BoxDecoration(
            color: AppColors.sand.withValues(alpha: 0.45),
            borderRadius: BorderRadius.circular(AppRadii.capture),
            border: Border.all(
              color: missing ? AppColors.statusRed : AppColors.ink.withValues(alpha: 0.15),
            ),
          ),
          child: DropdownButtonHideUnderline(
            child: DropdownButton<String>(
              value: value,
              isExpanded: true,
              isDense: true,
              icon: Icon(Icons.arrow_drop_down, color: AppColors.inkMuted),
              style: AppTypography.bodyInput,
              dropdownColor: AppColors.ivory,
              hint: Text('请选择', style: AppTypography.bodyInput.copyWith(color: AppColors.inkFaint)),
              items: [
                for (final o in options)
                  DropdownMenuItem<String>(value: o, child: Text(o, style: AppTypography.bodyInput)),
              ],
              onChanged: onChanged,
            ),
          ),
        ),
      ],
    );
  }
}

class _BoolField extends StatelessWidget {
  const _BoolField({
    required this.field,
    required this.value,
    required this.tint,
    required this.onChanged,
  });

  final NofField field;
  final bool value;
  final Color tint;
  final ValueChanged<bool> onChanged;

  @override
  Widget build(BuildContext context) {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.center,
      children: [
        Expanded(child: _FieldLabel(field: field)),
        const SizedBox(width: AppSpacing.s),
        Switch(
          value: value,
          activeTrackColor: tint,
          onChanged: onChanged,
        ),
      ],
    );
  }
}

class _SectionLabel extends StatelessWidget {
  const _SectionLabel({required this.label, required this.tint});
  final String label;
  final Color tint;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        Container(
          width: 3,
          height: 14,
          decoration: BoxDecoration(color: tint, borderRadius: BorderRadius.circular(2)),
        ),
        const SizedBox(width: AppSpacing.s),
        Text(label, style: AppTypography.subhead.copyWith(color: AppColors.inkMuted)),
      ],
    );
  }
}

// —— 公用:加载/错误态 ——

class _Centered extends StatelessWidget {
  const _Centered({required this.child});
  final Widget child;

  @override
  Widget build(BuildContext context) => ListView(
        physics: const AlwaysScrollableScrollPhysics(),
        children: [
          Padding(padding: const EdgeInsets.only(top: 96), child: Center(child: child)),
        ],
      );
}

class _ErrorState extends StatelessWidget {
  const _ErrorState({required this.message, required this.onRetry});
  final String message;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(Icons.cloud_off_outlined, color: AppColors.inkFaint, size: 40),
        const SizedBox(height: AppSpacing.m),
        Text('连不上工厂后端', style: AppTypography.titleM),
        const SizedBox(height: AppSpacing.xs),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: AppSpacing.pageH),
          child: Text(
            message,
            style: AppTypography.caption.copyWith(color: AppColors.inkMuted),
            textAlign: TextAlign.center,
          ),
        ),
        const SizedBox(height: AppSpacing.m),
        TextButton(onPressed: onRetry, child: const Text('重试')),
      ],
    );
  }
}

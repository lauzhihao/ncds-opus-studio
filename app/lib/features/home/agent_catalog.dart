// 智能体内置目录 —— 1:1 复刻原生 AgentHome.swift 的 agentCatalog（6 个 agent）。
// cmd 对齐 server COMMAND_REGISTRY;离线也可见(灯态/角标才靠后端聚合)。
// 排序按生产链 + 重要度:操盘手在前,再到采集→选题→编剧→美术→声音。
// accent 用设计 token 里的 6 个 agent 主色(对齐原生 Color 字面量)。

import 'package:flutter/widgets.dart';

import '../../design/tokens.dart';

/// 一个 agent 的静态档案。
class AgentInfo {
  const AgentInfo({
    required this.cmd,
    required this.name,
    required this.surname,
    required this.role,
    required this.blurb,
    required this.accent,
  });

  final String cmd; // 对齐 server COMMAND_REGISTRY
  final String name; // 鬼谷子
  final String surname; // 头像字:鬼
  final String role; // 雅号/title:捭阖纵横
  final String blurb; // 一句话说明(成名典故)
  final Color accent; // 头像/主题色

  String get id => cmd;
}

/// 内置目录(顺序与原生一致:wolong/shenkuo/guiguzi/liuyong/wudaozi/boya)。
const List<AgentInfo> agentCatalog = <AgentInfo>[
  AgentInfo(
    cmd: 'wolong',
    name: '卧龙',
    surname: '卧',
    role: '一统千军',
    blurb: '运筹帷幄之中，决胜千里之外',
    accent: AppColors.accentWolong, // 操盘手:opus 编排全厂
  ),
  AgentInfo(
    cmd: 'shenkuo',
    name: '沈存中', // 沈括,字存中
    surname: '沈',
    role: '梦溪笔谈',
    blurb: '遍采百家，笔录天下爆款',
    accent: AppColors.accentCollect, // 采集供料
  ),
  AgentInfo(
    cmd: 'guiguzi',
    name: '鬼谷子',
    surname: '鬼',
    role: '捭阖纵横',
    blurb: '揣情摩意，谋定而后动',
    accent: AppColors.accentTopic, // 选题官
  ),
  AgentInfo(
    cmd: 'liuyong',
    name: '柳三变', // 柳永原名三变,后改名永
    surname: '柳',
    role: '婉约词宗',
    blurb: '凡有井水处，皆能歌柳词',
    accent: AppColors.accentScript, // 编剧 + 质检
  ),
  AgentInfo(
    cmd: 'wudaozi',
    name: '吴道子',
    surname: '吴',
    role: '吴带当风',
    blurb: '落笔成画，分镜传神',
    accent: AppColors.accentArt, // 美术/分镜
  ),
  AgentInfo(
    cmd: 'boya',
    name: '俞伯牙', // 伯牙全名俞伯牙;头像字随名取首字「俞」
    surname: '俞',
    role: '高山流水',
    blurb: '鼓琴和鸣，众声成曲',
    accent: AppColors.accentSound, // 声音
  ),
];

/* sequences.js — 套路 / 连贯动作库
 *
 * 与 motions.js 的区别：motion 是单一循环叠加层；sequence 是一段「连贯动作」——
 * 一串有名字的招式按时序衔接成一条连续动画。rig.js 的 playSequence() 会把每根骨头
 * 在各招式的目标姿态（pose）串成一条带 offset 的 keyframe 轨道，整体播放并循环，
 * 同时对外报出「当前第几招 / 招式名」。
 *
 * pose：{ 骨头名: 'transform 字符串' }，相对预备式（identity）的偏移。
 *   某招没写到的骨头 = 保持上一招的姿态（hold）。
 * 招式只用到二段肢体骨头（肘/膝）+ root/torso/head，所以套路角色需是「武者」这类二段角色。
 *
 * 数据来源：八极拳「八大招」核心技击招法（刚猛暴烈、近身短打）。各招姿态为侧重表意的
 * 剪影化演绎，非招式图谱级精确。
 */
(function () {
  window.RIG_SEQUENCES = {
    baji8: {
      id: 'baji8',
      name: '八极拳 · 八大招',
      desc: '刚猛暴烈、近身短打。八招连贯，循环演练，起落归于跨立预备式。',
      returnDuration: 850,
      moves: [
        {
          name: '阎王三点手',
          desc: '连环进击，手法密集，专打中盘，势如阎王索命。',
          duration: 720, easing: 'ease-out',
          pose: {
            'arm-r-upper': 'rotate(-38deg)', 'arm-r-lower': 'rotate(-44deg)',
            'arm-l-upper': 'rotate(22deg)',  'arm-l-lower': 'rotate(82deg)',
            torso: 'rotate(7deg)', head: 'rotate(4deg)',
          },
        },
        {
          name: '猛虎硬爬山',
          desc: '最具代表性的杀招，全身爆发力贴身靠打，势不可挡。',
          duration: 820, easing: 'ease-out',
          pose: {
            root: 'translate(16px, 2px)',
            'arm-l-upper': 'rotate(34deg)', 'arm-l-lower': 'rotate(30deg)',
            'arm-r-upper': 'rotate(-34deg)', 'arm-r-lower': 'rotate(-30deg)',
            'leg-l-upper': 'rotate(12deg)', 'leg-l-lower': 'rotate(24deg)',
            torso: 'rotate(11deg)', head: 'rotate(6deg)',
          },
        },
        {
          name: '迎门三不顾',
          desc: '不退反进，贴身强攻，不顾防守，专破中门。',
          duration: 700, easing: 'ease-out',
          pose: {
            root: 'translate(4px, -5px)',
            'arm-r-upper': 'rotate(-72deg)', 'arm-r-lower': 'rotate(12deg)',
            'arm-l-upper': 'rotate(10deg)',  'arm-l-lower': 'rotate(16deg)',
            'leg-r-upper': 'rotate(-13deg)', 'leg-r-lower': 'rotate(6deg)',
            torso: 'rotate(5deg)', head: 'rotate(3deg)',
          },
        },
        {
          name: '霸王硬折缰',
          desc: '近身缠抱擒拿，借力发力，重挫对方关节与重心。',
          duration: 860, easing: 'ease-in-out',
          pose: {
            root: 'translate(0px, 8px)',
            'arm-l-upper': 'rotate(-26deg)', 'arm-l-lower': 'rotate(72deg)',
            'arm-r-upper': 'rotate(26deg)',  'arm-r-lower': 'rotate(-72deg)',
            'leg-l-lower': 'rotate(20deg)', 'leg-r-lower': 'rotate(-20deg)',
            torso: 'rotate(-8deg)', head: 'rotate(-4deg)',
          },
        },
        {
          name: '迎风朝阳掌',
          desc: '大开大合，掌法刚猛，以气催力，直击要害。',
          duration: 820, easing: 'ease-out',
          pose: {
            root: 'translate(0px, 0px)',
            'arm-r-upper': 'rotate(-104deg)', 'arm-r-lower': 'rotate(-16deg)',
            'arm-l-upper': 'rotate(64deg)',   'arm-l-lower': 'rotate(-12deg)',
            'leg-r-lower': 'rotate(-8deg)',
            torso: 'rotate(9deg)', head: 'rotate(7deg)',
          },
        },
        {
          name: '左右硬开门',
          desc: '以肩、肘等部位发力，强行破开对方防御，近身抢占中线。',
          duration: 660, easing: 'ease-out',
          pose: {
            'arm-l-upper': 'rotate(56deg)',  'arm-l-lower': 'rotate(96deg)',
            'arm-r-upper': 'rotate(-56deg)', 'arm-r-lower': 'rotate(-96deg)',
            'leg-l-upper': 'rotate(-6deg)', 'leg-r-upper': 'rotate(6deg)',
            torso: 'rotate(0deg)', head: 'rotate(0deg)',
          },
        },
        {
          name: '黄莺双抱爪',
          desc: '双手并发，抓拿与肘击并用，化解并反击对方攻势。',
          duration: 720, easing: 'ease-in-out',
          pose: {
            'arm-l-upper': 'rotate(16deg)',  'arm-l-lower': 'rotate(112deg)',
            'arm-r-upper': 'rotate(-16deg)', 'arm-r-lower': 'rotate(-112deg)',
            torso: 'rotate(3deg)', head: 'rotate(2deg)',
          },
        },
        {
          name: '立地通天炮',
          desc: '发力由脚而起，贯通全身，自下而上重击对手下颌或胸腹。',
          duration: 820, easing: 'ease-out',
          pose: {
            root: 'translate(0px, -10px)',
            'arm-r-upper': 'rotate(-150deg)', 'arm-r-lower': 'rotate(12deg)',
            'arm-l-upper': 'rotate(20deg)',   'arm-l-lower': 'rotate(84deg)',
            'leg-l-lower': 'rotate(0deg)', 'leg-r-lower': 'rotate(0deg)',
            torso: 'rotate(3deg)', head: 'rotate(-7deg)',
          },
        },
      ],
    },
  };

  window.RIG_SEQUENCE_LIST = Object.values(window.RIG_SEQUENCES);
})();

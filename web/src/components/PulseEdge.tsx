// 自定义 edge：底层实线 + 顶层一段亮带沿曲线滑动。
//
// 关键坑：ReactFlow 默认 css `.react-flow__edge.animated path` 给所有 path 强加
// stroke-dasharray:5 + dashdraw 动画。SVG attribute 比 CSS class 弱，所以
// 这里必须用 inline `style`（CSS prop level），并在 global.css 用 !important
// 取消 reactflow 默认动画。

import { getBezierPath, type EdgeProps } from 'reactflow';

export function PulseEdge(props: EdgeProps) {
  const {
    id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition,
    animated, style,
  } = props;

  const [edgePath] = getBezierPath({
    sourceX, sourceY, targetX, targetY,
    sourcePosition, targetPosition,
  });

  const stroke = (style?.stroke as string) || 'url(#opus-gradient)';

  return (
    <>
      {/* 底层：常态线。animated 时实线半透明，idle 时虚线全色。 */}
      <path
        id={id}
        d={edgePath}
        fill="none"
        className="opus-edge-base"
        style={{
          stroke,
          strokeWidth: 2,
          strokeLinecap: 'round',
          strokeOpacity: animated ? 0.28 : 1,
          strokeDasharray: animated ? 'none' : '8 6',
        }}
      />
      {/* 顶层：仅 animated 时显示。同宽 2px 的一段光，dashoffset 动画让它从 source 滑向 target。 */}
      {animated && (
        <path
          d={edgePath}
          className="opus-edge-flow"
          fill="none"
          style={{
            stroke,
            strokeWidth: 2,
            strokeLinecap: 'round',
            strokeDasharray: '36 600',
          }}
        />
      )}
    </>
  );
}

import type { CSSProperties } from 'react';

interface GlobalLoadingProps {
  size?: number | string;
  thickness?: number | string;
  className?: string;
  coreColor?: string;
  label?: string;
  style?: CSSProperties;
}

function cssSize(value: number | string): string {
  return typeof value === 'number' ? `${value}px` : value;
}

export function GlobalLoading({
  size = 24,
  thickness,
  className,
  coreColor,
  label,
  style,
}: GlobalLoadingProps) {
  const cssVars = {
    '--global-loading-size': cssSize(size),
    ...(thickness == null ? {} : { '--global-loading-thickness': cssSize(thickness) }),
    ...(coreColor == null ? {} : { '--global-loading-core': coreColor }),
    ...style,
  } as CSSProperties;

  const accessibility = label
    ? { role: 'status' as const, 'aria-label': label }
    : { 'aria-hidden': true };

  return (
    <span className={['global-loading', className].filter(Boolean).join(' ')} style={cssVars} {...accessibility}>
      <span className="global-loading__ring">
        <span className="global-loading__core" />
      </span>
    </span>
  );
}

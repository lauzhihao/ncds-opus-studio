import {
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type ComponentPropsWithoutRef,
  type ReactNode,
} from 'react';
import { createPortal } from 'react-dom';

const TIP_GAP_PX = 6;
const VIEWPORT_PADDING_PX = 8;

interface TipPosition {
  top: number;
  left: number;
  side: 'top' | 'bottom';
  ready: boolean;
}

export interface QuickTipProps extends ComponentPropsWithoutRef<'span'> {
  tip: ReactNode;
  disabled?: boolean;
}

export function QuickTip({
  tip,
  disabled = false,
  children,
  onBlur,
  onFocus,
  onMouseEnter,
  onMouseLeave,
  ...anchorProps
}: QuickTipProps) {
  const anchorRef = useRef<HTMLSpanElement>(null);
  const tipRef = useRef<HTMLDivElement>(null);
  const tooltipId = useId();
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState<TipPosition>({
    top: 0,
    left: 0,
    side: 'top',
    ready: false,
  });

  function showTip() {
    if (disabled) return;
    setPosition((current) => ({ ...current, ready: false }));
    setOpen(true);
  }

  function hideTip() {
    setOpen(false);
  }

  useLayoutEffect(() => {
    if (!open) return;

    function updatePosition() {
      const anchor = anchorRef.current;
      const tooltip = tipRef.current;
      if (!anchor || !tooltip) return;

      const anchorRect = anchor.getBoundingClientRect();
      const tipRect = tooltip.getBoundingClientRect();
      const roomAbove = anchorRect.top - VIEWPORT_PADDING_PX;
      const roomBelow = window.innerHeight - anchorRect.bottom - VIEWPORT_PADDING_PX;
      const side = roomAbove >= tipRect.height + TIP_GAP_PX || roomAbove >= roomBelow
        ? 'top'
        : 'bottom';
      const unclampedTop = side === 'top'
        ? anchorRect.top - tipRect.height - TIP_GAP_PX
        : anchorRect.bottom + TIP_GAP_PX;
      const top = Math.min(
        Math.max(VIEWPORT_PADDING_PX, unclampedTop),
        Math.max(VIEWPORT_PADDING_PX, window.innerHeight - tipRect.height - VIEWPORT_PADDING_PX),
      );
      const unclampedLeft = anchorRect.left + (anchorRect.width - tipRect.width) / 2;
      const left = Math.min(
        Math.max(VIEWPORT_PADDING_PX, unclampedLeft),
        Math.max(VIEWPORT_PADDING_PX, window.innerWidth - tipRect.width - VIEWPORT_PADDING_PX),
      );

      setPosition((current) => {
        if (
          current.ready
          && current.top === top
          && current.left === left
          && current.side === side
        ) {
          return current;
        }
        return { top, left, side, ready: true };
      });
    }

    updatePosition();
    window.addEventListener('resize', updatePosition);
    document.addEventListener('scroll', updatePosition, true);
    return () => {
      window.removeEventListener('resize', updatePosition);
      document.removeEventListener('scroll', updatePosition, true);
    };
  }, [open]);

  return (
    <>
      <span
        {...anchorProps}
        aria-describedby={open ? tooltipId : undefined}
        ref={anchorRef}
        onBlur={(event) => {
          onBlur?.(event);
          hideTip();
        }}
        onFocus={(event) => {
          onFocus?.(event);
          showTip();
        }}
        onMouseEnter={(event) => {
          onMouseEnter?.(event);
          showTip();
        }}
        onMouseLeave={(event) => {
          onMouseLeave?.(event);
          hideTip();
        }}
      >
        {children}
      </span>
      {open && createPortal(
        <div
          className={`quick-tip quick-tip-${position.side}${position.ready ? ' is-ready' : ''}`}
          id={tooltipId}
          ref={tipRef}
          role="tooltip"
          style={{ left: position.left, top: position.top }}
        >
          {tip}
        </div>,
        document.body,
      )}
    </>
  );
}

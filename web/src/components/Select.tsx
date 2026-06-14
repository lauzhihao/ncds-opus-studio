// 下拉选择：基于 Radix UI（@radix-ui/react-select），用本项目 SCSS token 配色，
// 跟现有自定义按钮/卡片风格一致。Radix 负责无障碍/键盘/portal/碰撞翻转。
// 对外仍是 number 值的简单接口（内部转 string，因 Radix Item value 要求字符串）。

import * as RSelect from '@radix-ui/react-select';
import { Check, ChevronDown } from 'lucide-react';

export interface SelectOption {
  value: number;
  label: string;
}

interface Props {
  value: number;
  options: SelectOption[];
  onChange: (v: number) => void;
  ariaLabel?: string;
}

export function Select({ value, options, onChange, ariaLabel }: Props) {
  return (
    <RSelect.Root value={String(value)} onValueChange={(v) => onChange(Number(v))}>
      <RSelect.Trigger className="ui-select-trigger" aria-label={ariaLabel}>
        <RSelect.Value />
        <RSelect.Icon className="ui-select-caret">
          <ChevronDown size={14} strokeWidth={1.8} />
        </RSelect.Icon>
      </RSelect.Trigger>
      <RSelect.Portal>
        <RSelect.Content className="ui-select-menu" position="popper" sideOffset={4}>
          <RSelect.Viewport>
            {options.map((o) => (
              <RSelect.Item key={o.value} value={String(o.value)} className="ui-select-option">
                <RSelect.ItemText>{o.label}</RSelect.ItemText>
                <RSelect.ItemIndicator className="ui-select-check">
                  <Check size={13} strokeWidth={2.2} />
                </RSelect.ItemIndicator>
              </RSelect.Item>
            ))}
          </RSelect.Viewport>
        </RSelect.Content>
      </RSelect.Portal>
    </RSelect.Root>
  );
}

import React from 'react';
import './Toggle.css';

/**
 * Toggle 组件属性
 */
interface ToggleProps {
  /** 标签文本 */
  label?: string;
  
  /** 是否选中 */
  checked: boolean;
  
  /** 变化回调 */
  onChange: (checked: boolean) => void;
  
  /** 描述文本 */
  description?: string;
  
  /** 是否禁用 */
  disabled?: boolean;
}

/**
 * 开关组件
 * iOS 风格的切换开关
 */
export function Toggle({
  label,
  checked,
  onChange,
  description,
  disabled = false
}: ToggleProps): JSX.Element {
  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    onChange(e.target.checked);
  };

  return (
    <div className="toggle-container">
      <div className="toggle-content">
        <div>
          {label && <div className="toggle-label">{label}</div>}
          {description && <div className="toggle-description">{description}</div>}
        </div>
        
        <label className="toggle-switch">
          <input
            type="checkbox"
            checked={checked}
            onChange={handleChange}
            disabled={disabled}
          />
          <span className="toggle-slider"></span>
        </label>
      </div>
    </div>
  );
}

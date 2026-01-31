import React from 'react';
import './Select.css';

/**
 * Select 选项接口
 */
export interface SelectOption {
  /** 选项值 */
  value: string;
  
  /** 显示标签 */
  label: string;
  
  /** 图标 */
  icon?: string;
  
  /** 描述 */
  description?: string;
}

/**
 * Select 组件属性
 */
interface SelectProps {
  /** 标签文本 */
  label?: string;
  
  /** 当前值 */
  value: string;
  
  /** 选项列表 */
  options: SelectOption[];
  
  /** 变化回调 */
  onChange: (value: string) => void;
  
  /** 占位符 */
  placeholder?: string;
  
  /** 是否禁用 */
  disabled?: boolean;
}

/**
 * 下拉选择组件
 */
export function Select({
  label,
  value,
  options,
  onChange,
  placeholder,
  disabled = false
}: SelectProps): JSX.Element {
  const handleChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    onChange(e.target.value);
  };

  return (
    <div className="select-container">
      {label && <label className="select-label">{label}</label>}
      
      <select
        className="select-field"
        value={value}
        onChange={handleChange}
        disabled={disabled}
      >
        {placeholder && (
          <option value="" disabled>
            {placeholder}
          </option>
        )}
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.icon ? `${option.icon} ${option.label}` : option.label}
          </option>
        ))}
      </select>
    </div>
  );
}

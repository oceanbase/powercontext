import React from 'react';
import './Input.css';

/**
 * Input 组件属性
 */
interface InputProps {
  /** 输入类型 */
  type?: 'text' | 'password' | 'url' | 'number';
  
  /** 标签文本 */
  label?: string;
  
  /** 占位符 */
  placeholder?: string;
  
  /** 当前值 */
  value: string | number;
  
  /** 变化回调 */
  onChange: (value: string | number) => void;
  
  /** 错误信息 */
  error?: string;
  
  /** 帮助文本 */
  helpText?: string;
  
  /** 是否必填 */
  required?: boolean;
  
  /** 是否禁用 */
  disabled?: boolean;
}

/**
 * 输入框组件
 * 统一的表单输入样式
 */
export function Input({
  type = 'text',
  label,
  placeholder,
  value,
  onChange,
  error,
  helpText,
  required = false,
  disabled = false
}: InputProps): JSX.Element {
  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const newValue = type === 'number' ? parseFloat(e.target.value) : e.target.value;
    onChange(newValue);
  };

  return (
    <div className="input-container">
      {label && (
        <label className="input-label">
          {label}
          {required && <span className="input-required">*</span>}
        </label>
      )}
      
      <input
        type={type}
        className={`input-field ${error ? 'input-error' : ''}`}
        placeholder={placeholder}
        value={value}
        onChange={handleChange}
        disabled={disabled}
        required={required}
      />
      
      {error && <div className="input-error-text">{error}</div>}
      {helpText && !error && <div className="input-help-text">{helpText}</div>}
    </div>
  );
}

import React from 'react';
import './Button.css';

/**
 * Button 组件属性
 */
interface ButtonProps {
  /** 按钮文本 */
  children: React.ReactNode;
  
  /** 点击回调 */
  onClick?: () => void;
  
  /** 按钮类型 */
  variant?: 'primary' | 'secondary' | 'danger' | 'ghost';
  
  /** 按钮尺寸 */
  size?: 'small' | 'medium' | 'large';
  
  /** 是否禁用 */
  disabled?: boolean;
  
  /** 是否加载中 */
  loading?: boolean;
  
  /** 图标 */
  icon?: React.ReactNode;
  
  /** 是否全宽 */
  fullWidth?: boolean;
}

/**
 * 按钮组件
 */
export function Button({
  children,
  onClick,
  variant = 'primary',
  size = 'medium',
  disabled = false,
  loading = false,
  icon,
  fullWidth = false
}: ButtonProps): JSX.Element {
  const classNames = [
    'button',
    `button-${variant}`,
    `button-${size}`,
    fullWidth ? 'button-full' : '',
    loading ? 'button-loading' : ''
  ].filter(Boolean).join(' ');

  return (
    <button
      className={classNames}
      onClick={onClick}
      disabled={disabled || loading}
    >
      {loading && <span className="button-spinner"></span>}
      {icon && !loading && <span className="button-icon">{icon}</span>}
      {children}
    </button>
  );
}

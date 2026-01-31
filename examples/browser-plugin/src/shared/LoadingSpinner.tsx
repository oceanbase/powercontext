import React from 'react';
import './LoadingSpinner.css';

/**
 * LoadingSpinner 组件属性
 */
interface LoadingSpinnerProps {
  /** 加载提示文本 */
  text?: string;
  
  /** 尺寸 */
  size?: 'small' | 'medium' | 'large';
}

/**
 * 加载动画组件
 * 显示旋转的加载指示器
 * 
 * @param props - 组件属性
 * @returns React 组件
 */
export function LoadingSpinner({ 
  text, 
  size = 'medium' 
}: LoadingSpinnerProps): JSX.Element {
  return (
    <div className="loading-spinner-container">
      <div className={`loading-spinner loading-spinner-${size}`}>
        <div className="spinner"></div>
      </div>
      {text && <p className="loading-text">{text}</p>}
    </div>
  );
}

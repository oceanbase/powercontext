import React from 'react';
import './ErrorBanner.css';

/**
 * ErrorBanner 组件属性
 */
interface ErrorBannerProps {
  /** 错误消息 */
  message: string;
  
  /** 重试回调 */
  onRetry?: () => void;
  
  /** 关闭回调 */
  onDismiss?: () => void;
}

/**
 * 错误提示横幅组件
 * 显示错误信息和操作按钮
 * 
 * @param props - 组件属性
 * @returns React 组件
 */
export function ErrorBanner({ 
  message, 
  onRetry, 
  onDismiss 
}: ErrorBannerProps): JSX.Element {
  return (
    <div className="error-banner">
      <div className="error-banner-content">
        <span className="error-banner-icon">⚠️</span>
        <span className="error-banner-message">{message}</span>
      </div>
      <div className="error-banner-actions">
        {onRetry && (
          <button 
            className="error-banner-btn error-banner-retry"
            onClick={onRetry}
          >
            重试
          </button>
        )}
        {onDismiss && (
          <button 
            className="error-banner-btn error-banner-dismiss"
            onClick={onDismiss}
            aria-label="关闭"
          >
            ✕
          </button>
        )}
      </div>
    </div>
  );
}

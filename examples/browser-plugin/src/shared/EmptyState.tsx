import React from 'react';
import './EmptyState.css';

/**
 * EmptyState 组件属性
 */
interface EmptyStateProps {
  /** 图标 */
  icon?: string;
  
  /** 标题 */
  title: string;
  
  /** 描述文本 */
  description?: string;
  
  /** 操作按钮 */
  action?: {
    label: string;
    onClick: () => void;
  };
}

/**
 * 空状态组件
 * 当列表为空时显示
 * 
 * @param props - 组件属性
 * @returns React 组件
 */
export function EmptyState({ 
  icon = '📭', 
  title, 
  description, 
  action 
}: EmptyStateProps): JSX.Element {
  return (
    <div className="empty-state">
      <div className="empty-state-icon">{icon}</div>
      <h3 className="empty-state-title">{title}</h3>
      {description && (
        <p className="empty-state-description">{description}</p>
      )}
      {action && (
        <button 
          className="empty-state-action"
          onClick={action.onClick}
        >
          {action.label}
        </button>
      )}
    </div>
  );
}

import React from 'react';
import type { Memory } from '@/types/memory';
import { formatDate, truncateText, getDomain } from '@/utils/helpers';
import './MemoryCard.css';

/**
 * MemoryCard 组件属性
 */
interface MemoryCardProps {
  /** 记忆数据 */
  memory: Memory;
  
  /** 点击回调 */
  onClick?: (memory: Memory) => void;
  
  /** 是否高亮显示 */
  isHighlighted?: boolean;
}

/**
 * 记忆卡片组件
 * 显示单条记忆的摘要信息
 * 
 * @param props - 组件属性
 * @returns React 组件
 */
export function MemoryCard({ 
  memory, 
  onClick, 
  isHighlighted = false 
}: MemoryCardProps): JSX.Element {
  
  /**
   * 获取标题
   */
  const getTitle = (): string => {
    return memory.metadata.title || truncateText(memory.content, 50);
  };
  
  /**
   * 获取预览文本
   */
  const getPreview = (): string => {
    return truncateText(memory.content, 150);
  };
  
  /**
   * 获取域名
   */
  const domain = getDomain(memory.metadata.url);
  
  /**
   * 处理点击
   */
  const handleClick = () => {
    if (onClick) {
      onClick(memory);
    }
  };
  
  return (
    <div 
      className={`memory-card ${isHighlighted ? 'memory-card-highlighted' : ''}`}
      onClick={handleClick}
    >
      <div className="memory-card-header">
        <h3 className="memory-card-title">{getTitle()}</h3>
        <span className="memory-card-type">{memory.metadata.type}</span>
      </div>
      
      <p className="memory-card-preview">{getPreview()}</p>
      
      <div className="memory-card-meta">
        {domain && (
          <span className="memory-card-domain" title={memory.metadata.url}>
            🌐 {domain}
          </span>
        )}
        <span className="memory-card-date">
          {formatDate(memory.metadata.timestamp || memory.created_at || '')}
        </span>
      </div>
    </div>
  );
}

import React, { useEffect, useRef } from 'react';
import type { Memory } from '../../types/memory';
import { MemoryCard } from './MemoryCard';
import { LoadingSpinner } from '../../shared/LoadingSpinner';
import { EmptyState } from '../../shared/EmptyState';
import './MemoryList.css';

/**
 * MemoryList 组件属性
 */
interface MemoryListProps {
  /** 记忆列表 */
  memories: Memory[];
  
  /** 是否为搜索结果模式 */
  isSearchMode?: boolean;
  
  /** 是否正在加载 */
  isLoading?: boolean;
  
  /** 是否有更多数据 */
  hasMore?: boolean;
  
  /** 列表项点击回调 */
  onItemClick?: (memory: Memory) => void;
  
  /** 加载更多回调 */
  onLoadMore?: () => void;
}

/**
 * 记忆列表组件
 * 显示记忆卡片列表，支持滚动加载
 * 
 * @param props - 组件属性
 * @returns React 组件
 */
export function MemoryList({ 
  memories,
  isSearchMode = false,
  isLoading = false,
  hasMore = false,
  onItemClick,
  onLoadMore
}: MemoryListProps): JSX.Element {
  const listRef = useRef<HTMLDivElement>(null);
  
  /**
   * 处理滚动事件
   */
  const handleScroll = (event: React.UIEvent<HTMLDivElement>) => {
    const element = event.currentTarget;
    
    if (isScrolledToBottom(element) && hasMore && !isLoading && onLoadMore) {
      onLoadMore();
    }
  };
  
  /**
   * 判断是否到达底部
   */
  const isScrolledToBottom = (element: HTMLDivElement): boolean => {
    const threshold = 50; // 提前50px触发加载
    return element.scrollHeight - element.scrollTop - element.clientHeight < threshold;
  };
  
  // 显示加载中
  if (isLoading && memories.length === 0) {
    return (
      <div className="memory-list">
        <LoadingSpinner text="加载中..." />
      </div>
    );
  }
  
  // 显示空状态
  if (memories.length === 0) {
    return (
      <div className="memory-list">
        <EmptyState
          icon={isSearchMode ? '🔍' : '📭'}
          title={isSearchMode ? '未找到相关记忆' : '还没有记忆'}
          description={isSearchMode ? '尝试使用其他关键词搜索' : '开始捕获网页内容以创建记忆'}
        />
      </div>
    );
  }
  
  return (
    <div 
      className="memory-list"
      ref={listRef}
      onScroll={handleScroll}
    >
      {memories.map((memory) => (
        <MemoryCard
          key={memory.memory_id}
          memory={memory}
          onClick={onItemClick}
        />
      ))}
      
      {isLoading && (
        <div className="memory-list-loading">
          <LoadingSpinner size="small" text="加载更多..." />
        </div>
      )}
      
      {!hasMore && memories.length > 0 && (
        <div className="memory-list-end">
          已显示所有记忆
        </div>
      )}
    </div>
  );
}

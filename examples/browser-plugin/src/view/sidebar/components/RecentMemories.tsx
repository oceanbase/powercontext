import React, { useEffect } from 'react';
import type { Memory } from '@/types';
import { useMemoryStore } from '@/store/useMemoryStore';
import { LoadingSpinner } from '@/shared/LoadingSpinner';
import { EmptyState } from '@/shared/EmptyState';
import './RecentMemories.css';

/**
 * RecentMemories 组件属性
 */
interface RecentMemoriesProps {
  /** 最大显示数量 */
  limit?: number;
}

/**
 * 最近记忆组件
 * 显示最近添加的记忆
 * 
 * @param props - 组件属性
 * @returns React 组件
 */
export function RecentMemories(props: RecentMemoriesProps): JSX.Element {
  const { limit = 20 } = props;
  const { memories, isLoading } = useMemoryStore();
  
  // 获取最近的记忆（按创建时间排序）
  const recentMemories = memories
    .slice()
    .sort((a, b) => {
      const dateA = a.created_at ? new Date(a.created_at).getTime() : 0;
      const dateB = b.created_at ? new Date(b.created_at).getTime() : 0;
      return dateB - dateA; // 降序
    })
    .slice(0, limit);
  
  if (isLoading) {
    return (
      <div className="recent-memories">
        <h3 className="recent-memories-title">Recent memories</h3>
        <LoadingSpinner />
      </div>
    );
  }
  
  if (recentMemories.length === 0) {
    return (
      <div className="recent-memories">
        <h3 className="recent-memories-title">Recent memories</h3>
        <EmptyState title="No memories yet" description="Start capturing memories by selecting text on any webpage" />
      </div>
    );
  }
  
  return (
    <div className="recent-memories">
      <h3 className="recent-memories-title">Recent memories</h3>
      <div className="memory-list">
        {recentMemories.map(memory => (
          <div key={memory.memory_id} className="memory-card">
            <div className="memory-content">
              {memory.content.substring(0, 150)}
              {memory.content.length > 150 && '...'}
            </div>
            <div className="memory-meta">
              <span className="memory-domain">{memory.metadata?.domain}</span>
              <span className="memory-date">
                {memory.created_at ? new Date(memory.created_at).toLocaleDateString() : ''}
              </span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

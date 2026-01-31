import React, { useEffect, useState } from 'react';
import type { Memory } from '@/types';
import { RecommendationEngine, type PageContext } from '@/services/recommendation/recommendationEngine';
import { useMemoryStore } from '@/store/useMemoryStore';
import { LoadingSpinner } from '@/shared/LoadingSpinner';
import { EmptyState } from '@/shared/EmptyState';
import './RelatedMemories.css';

/**
 * RelatedMemories 组件属性
 */
interface RelatedMemoriesProps {
  /** 当前页面 URL */
  pageUrl: string;
  
  /** 当前页面标题 */
  pageTitle: string;
}

/**
 * 相关记忆组件
 * 显示与当前页面相关的记忆
 * 
 * @param props - 组件属性
 * @returns React 组件
 */
export function RelatedMemories(props: RelatedMemoriesProps): JSX.Element {
  const { pageUrl, pageTitle } = props;
  const { memories, isLoading } = useMemoryStore();
  const [relatedMemories, setRelatedMemories] = useState<Memory[]>([]);
  
  useEffect(() => {
    if (pageUrl && pageTitle && memories.length > 0) {
      // 提取域名
      let domain = '';
      try {
        const url = new URL(pageUrl);
        domain = url.hostname;
      } catch (error) {
        console.error('[RelatedMemories] Invalid URL:', error);
      }
      
      // 获取推荐
      const pageContext: PageContext = { url: pageUrl, title: pageTitle, domain };
      const recommendations = RecommendationEngine.getRecommendations(
        pageContext,
        memories,
        5 // 最多显示 5 个
      );
      
      setRelatedMemories(recommendations.map(r => r.memory));
    }
  }, [pageUrl, pageTitle, memories]);
  
  if (isLoading) {
    return (
      <div className="related-memories">
        <h3 className="related-memories-title">Related to this page</h3>
        <LoadingSpinner />
      </div>
    );
  }
  
  if (relatedMemories.length === 0) {
    return (
      <div className="related-memories">
        <h3 className="related-memories-title">Related to this page</h3>
        <EmptyState title="No related memories found" />
      </div>
    );
  }
  
  return (
    <div className="related-memories">
      <h3 className="related-memories-title">Related to this page</h3>
      <div className="memory-list">
        {relatedMemories.map(memory => (
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

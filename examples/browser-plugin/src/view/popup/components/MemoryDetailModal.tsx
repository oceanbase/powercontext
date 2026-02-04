import React from 'react';
import type { Memory } from '@/types/memory';
import { Modal } from '@/shared/Modal';
import { formatDate, getDomain } from '@/utils/helpers';
import './MemoryDetailModal.css';

/**
 * MemoryDetailModal 组件属性
 */
interface MemoryDetailModalProps {
  /** 要显示的记忆 */
  memory: Memory | null;
  
  /** 是否打开 */
  isOpen: boolean;
  
  /** 关闭回调 */
  onClose: () => void;
  
  /** 编辑回调（暂不实现） */
  onEdit?: (memory: Memory) => void;
  
  /** 删除回调（暂不实现） */
  onDelete?: (memoryId: string) => void;
}

/**
 * 记忆详情弹窗组件
 * 显示记忆的完整内容和元数据
 * 
 * @param props - 组件属性
 * @returns React 组件
 */
export function MemoryDetailModal({ 
  memory, 
  isOpen, 
  onClose 
}: MemoryDetailModalProps): JSX.Element {
  
  if (!memory) {
    return <></>;
  }
  
  /**
   * 在新标签页打开原始页面
   */
  const handleOpenInNewTab = () => {
    if (memory.metadata.url) {
      chrome.tabs.create({ url: memory.metadata.url });
    }
  };
  
  const domain = getDomain(memory.metadata.url);
  
  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="记忆详情"
      size="large"
    >
      <div className="memory-detail">
        <div className="memory-detail-header">
          <h2 className="memory-detail-title">
            {memory.metadata.title || '无标题'}
          </h2>
          <span className="memory-detail-type">{memory.metadata.type}</span>
        </div>
        
        <div className="memory-detail-content">
          <h3 className="memory-detail-section-title">内容</h3>
          <p className="memory-detail-text">{memory.content}</p>
        </div>
        
        <div className="memory-detail-metadata">
          <h3 className="memory-detail-section-title">元数据</h3>
          
          <div className="memory-detail-meta-grid">
            {domain && (
              <div className="memory-detail-meta-item">
                <span className="memory-detail-meta-label">来源域名</span>
                <span className="memory-detail-meta-value">{domain}</span>
              </div>
            )}
            
            <div className="memory-detail-meta-item">
              <span className="memory-detail-meta-label">创建时间</span>
              <span className="memory-detail-meta-value">
                {formatDate(memory.metadata.timestamp || memory.created_at || '')}
              </span>
            </div>
            
            <div className="memory-detail-meta-item">
              <span className="memory-detail-meta-label">记忆 ID</span>
              <span className="memory-detail-meta-value memory-id">{memory.memory_id}</span>
            </div>
            
            <div className="memory-detail-meta-item">
              <span className="memory-detail-meta-label">用户 ID</span>
              <span className="memory-detail-meta-value memory-id">{memory.user_id}</span>
            </div>
          </div>
        </div>
        
        <div className="memory-detail-actions">
          {memory.metadata.url && (
            <button 
              className="memory-detail-btn memory-detail-btn-primary"
              onClick={handleOpenInNewTab}
            >
              🔗 在新标签页打开
            </button>
          )}
          <button 
            className="memory-detail-btn memory-detail-btn-secondary"
            onClick={onClose}
          >
            关闭
          </button>
        </div>
      </div>
    </Modal>
  );
}

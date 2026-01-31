import React, { useState, useEffect } from 'react';
import type { Memory } from '@/types';
import { Modal } from './Modal';
import './MemoryEditor.css';

/**
 * MemoryEditor 组件属性
 */
interface MemoryEditorProps {
  /** 要编辑的记忆 */
  memory: Memory;
  
  /** 是否打开 */
  isOpen: boolean;
  
  /** 关闭回调 */
  onClose: () => void;
  
  /** 保存回调 */
  onSave: (memory: Memory) => Promise<void>;
}

/**
 * 编辑表单状态
 */
interface EditFormState {
  /** 记忆内容 */
  content: string;
  
  /** 标题（metadata.title） */
  title: string;
  
  /** 类别（filters.category） */
  category: string;
  
  /** 自定义标签 */
  tags: string[];
}

/**
 * 记忆编辑器组件
 * 允许用户修改记忆内容和元数据
 * 
 * @param props - 组件属性
 * @returns React 组件
 */
export function MemoryEditor(props: MemoryEditorProps): JSX.Element {
  const { memory, isOpen, onClose, onSave } = props;
  
  // 表单状态
  const [formState, setFormState] = useState<EditFormState>({
    content: '',
    title: '',
    category: '',
    tags: [],
  });
  
  // 新标签输入
  const [newTag, setNewTag] = useState('');
  
  // 保存状态
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  
  // 初始化表单数据
  useEffect(() => {
    if (isOpen && memory) {
      setFormState({
        content: memory.content || '',
        title: memory.metadata?.title || '',
        category: memory.filters?.category || '',
        tags: [], // TODO: 从 metadata 中提取标签
      });
      setError(null);
    }
  }, [isOpen, memory]);
  
  /**
   * 处理字段变化
   */
  const handleFieldChange = (field: keyof EditFormState, value: any) => {
    setFormState(prev => ({
      ...prev,
      [field]: value,
    }));
  };
  
  /**
   * 添加标签
   */
  const handleAddTag = () => {
    const tag = newTag.trim();
    if (tag && !formState.tags.includes(tag)) {
      setFormState(prev => ({
        ...prev,
        tags: [...prev.tags, tag],
      }));
      setNewTag('');
    }
  };
  
  /**
   * 移除标签
   */
  const handleRemoveTag = (tag: string) => {
    setFormState(prev => ({
      ...prev,
      tags: prev.tags.filter(t => t !== tag),
    }));
  };
  
  /**
   * 验证表单
   */
  const validateForm = (): { isValid: boolean; errors: string[] } => {
    const errors: string[] = [];
    
    if (!formState.content.trim()) {
      errors.push('Content cannot be empty');
    }
    
    if (formState.content.length > 10000) {
      errors.push('Content is too long (max 10000 characters)');
    }
    
    return {
      isValid: errors.length === 0,
      errors,
    };
  };
  
  /**
   * 保存修改
   */
  const handleSave = async () => {
    // 验证表单
    const validation = validateForm();
    if (!validation.isValid) {
      setError(validation.errors.join('. '));
      return;
    }
    
    setIsSaving(true);
    setError(null);
    
    try {
      // 构建更新后的记忆对象
      const updatedMemory: Memory = {
        ...memory,
        content: formState.content,
        metadata: {
          ...memory.metadata,
          title: formState.title,
        },
        filters: {
          ...(memory.filters || {}),
          category: formState.category,
        },
      };
      
      // 调用保存回调
      await onSave(updatedMemory);
      
      // 关闭对话框
      onClose();
    } catch (error) {
      console.error('[MemoryEditor] Failed to save:', error);
      setError(error instanceof Error ? error.message : 'Failed to save memory');
    } finally {
      setIsSaving(false);
    }
  };
  
  /**
   * 取消编辑
   */
  const handleCancel = () => {
    if (!isSaving) {
      onClose();
    }
  };
  
  /**
   * 处理键盘事件
   */
  const handleKeyDown = (e: React.KeyboardEvent) => {
    // Ctrl+Enter 保存
    if (e.ctrlKey && e.key === 'Enter') {
      e.preventDefault();
      handleSave();
    }
  };
  
  return (
    <Modal isOpen={isOpen} onClose={handleCancel}>
      <div className="memory-editor" onKeyDown={handleKeyDown}>
        <div className="memory-editor-header">
          <h2 className="memory-editor-title">Edit Memory</h2>
        </div>
        
        <div className="memory-editor-content">
          {/* 错误提示 */}
          {error && (
            <div className="memory-editor-error">
              {error}
            </div>
          )}
          
          {/* 标题字段 */}
          <div className="memory-editor-field">
            <label htmlFor="title" className="memory-editor-label">
              Title
            </label>
            <input
              id="title"
              type="text"
              className="memory-editor-input"
              value={formState.title}
              onChange={(e) => handleFieldChange('title', e.target.value)}
              placeholder="Enter title..."
            />
          </div>
          
          {/* 内容字段 */}
          <div className="memory-editor-field">
            <label htmlFor="content" className="memory-editor-label">
              Content *
            </label>
            <textarea
              id="content"
              className="memory-editor-textarea"
              value={formState.content}
              onChange={(e) => handleFieldChange('content', e.target.value)}
              placeholder="Enter content..."
              rows={8}
            />
            <div className="memory-editor-hint">
              {formState.content.length} / 10000 characters
            </div>
          </div>
          
          {/* 类别字段 */}
          <div className="memory-editor-field">
            <label htmlFor="category" className="memory-editor-label">
              Category
            </label>
            <input
              id="category"
              type="text"
              className="memory-editor-input"
              value={formState.category}
              onChange={(e) => handleFieldChange('category', e.target.value)}
              placeholder="Enter category..."
            />
          </div>
          
          {/* 标签字段 */}
          <div className="memory-editor-field">
            <label className="memory-editor-label">
              Tags
            </label>
            <div className="memory-editor-tags">
              {formState.tags.map(tag => (
                <span key={tag} className="memory-editor-tag">
                  {tag}
                  <button
                    type="button"
                    className="memory-editor-tag-remove"
                    onClick={() => handleRemoveTag(tag)}
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
            <div className="memory-editor-tag-input">
              <input
                type="text"
                className="memory-editor-input"
                value={newTag}
                onChange={(e) => setNewTag(e.target.value)}
                onKeyPress={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault();
                    handleAddTag();
                  }
                }}
                placeholder="Add tag..."
              />
              <button
                type="button"
                className="btn btn-secondary"
                onClick={handleAddTag}
                disabled={!newTag.trim()}
              >
                Add
              </button>
            </div>
          </div>
        </div>
        
        <div className="memory-editor-actions">
          <button
            type="button"
            className="btn btn-secondary"
            onClick={handleCancel}
            disabled={isSaving}
          >
            Cancel
          </button>
          <button
            type="button"
            className="btn btn-primary"
            onClick={handleSave}
            disabled={isSaving}
          >
            {isSaving ? 'Saving...' : 'Save'}
          </button>
        </div>
        
        <div className="memory-editor-hint">
          Tip: Press Ctrl+Enter to save
        </div>
      </div>
    </Modal>
  );
}

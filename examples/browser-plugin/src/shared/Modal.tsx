import React, { useEffect } from 'react';
import './Modal.css';

/**
 * Modal 组件属性
 */
interface ModalProps {
  /** 是否打开 */
  isOpen: boolean;
  
  /** 关闭回调 */
  onClose: () => void;
  
  /** 模态框标题 */
  title?: string;
  
  /** 子组件 */
  children: React.ReactNode;
  
  /** 模态框大小 */
  size?: 'small' | 'medium' | 'large';
}

/**
 * 通用模态框组件
 * 支持遮罩层点击关闭、ESC 键关闭
 * 
 * @param props - 组件属性
 * @returns React 组件
 */
export function Modal({ 
  isOpen, 
  onClose, 
  title, 
  children, 
  size = 'medium' 
}: ModalProps): JSX.Element | null {
  
  // 监听 ESC 键
  useEffect(() => {
    if (!isOpen) return;
    
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
      }
    };
    
    document.addEventListener('keydown', handleEscape);
    
    return () => {
      document.removeEventListener('keydown', handleEscape);
    };
  }, [isOpen, onClose]);
  
  // 禁用背景滚动
  useEffect(() => {
    if (isOpen) {
      document.body.style.overflow = 'hidden';
    } else {
      document.body.style.overflow = '';
    }
    
    return () => {
      document.body.style.overflow = '';
    };
  }, [isOpen]);
  
  if (!isOpen) {
    return null;
  }
  
  return (
    <div className="modal-overlay" onClick={onClose}>
      <div 
        className={`modal-content modal-${size}`}
        onClick={(e) => e.stopPropagation()}
      >
        {title && (
          <div className="modal-header">
            <h2 className="modal-title">{title}</h2>
            <button 
              className="modal-close-btn"
              onClick={onClose}
              aria-label="关闭"
            >
              ✕
            </button>
          </div>
        )}
        
        <div className="modal-body">
          {children}
        </div>
      </div>
    </div>
  );
}

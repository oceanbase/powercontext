import React, { useState } from 'react';
import { Modal } from './Modal';
import './DeleteConfirmDialog.css';

/**
 * DeleteConfirmDialog 组件属性
 */
interface DeleteConfirmDialogProps {
  /** 是否打开 */
  isOpen: boolean;
  
  /** 标题 */
  title?: string;
  
  /** 消息 */
  message: string;
  
  /** 确认回调 */
  onConfirm: () => void;
  
  /** 取消回调 */
  onCancel: () => void;
  
  /** 是否显示"不再提示"选项 */
  showDontAskAgain?: boolean;
}

/**
 * 删除确认对话框组件
 * 
 * @param props - 组件属性
 * @returns React 组件
 */
export function DeleteConfirmDialog(props: DeleteConfirmDialogProps): JSX.Element {
  const {
    isOpen,
    title = 'Confirm Delete',
    message,
    onConfirm,
    onCancel,
    showDontAskAgain = false,
  } = props;
  
  const [dontAskAgain, setDontAskAgain] = useState(false);
  
  /**
   * 处理确认
   */
  const handleConfirm = async () => {
    // 保存"不再提示"设置
    if (dontAskAgain) {
      await chrome.storage.local.set({ skipDeleteConfirm: true });
    }
    
    onConfirm();
  };
  
  /**
   * 处理取消
   */
  const handleCancel = () => {
    setDontAskAgain(false);
    onCancel();
  };
  
  return (
    <Modal isOpen={isOpen} onClose={handleCancel}>
      <div className="delete-confirm-dialog">
        <div className="delete-confirm-header">
          <div className="delete-confirm-icon">⚠️</div>
          <h2 className="delete-confirm-title">{title}</h2>
        </div>
        
        <div className="delete-confirm-content">
          <p className="delete-confirm-message">{message}</p>
          
          {showDontAskAgain && (
            <label className="delete-confirm-checkbox">
              <input
                type="checkbox"
                checked={dontAskAgain}
                onChange={(e) => setDontAskAgain(e.target.checked)}
              />
              <span>Don't ask again</span>
            </label>
          )}
        </div>
        
        <div className="delete-confirm-actions">
          <button
            type="button"
            className="btn btn-secondary"
            onClick={handleCancel}
          >
            Cancel
          </button>
          <button
            type="button"
            className="btn btn-danger"
            onClick={handleConfirm}
          >
            Delete
          </button>
        </div>
      </div>
    </Modal>
  );
}

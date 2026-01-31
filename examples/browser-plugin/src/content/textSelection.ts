import { PageContextExtractor } from './pageContext';
import type { Message, MessageResponse } from '@/types';

/**
 * 文本选择处理器
 * 监听用户文本选择并显示保存按钮
 */
export class TextSelectionHandler {
  private minLength: number;
  private saveButton: HTMLElement | null = null;
  private boundHandleSelection: ((event: MouseEvent) => void) | null = null;
  private boundHideButton: (() => void) | null = null;

  /**
   * 构造函数
   * @param minLength - 最小选择长度（默认 10）
   */
  constructor(minLength: number = 10) {
    this.minLength = minLength;
  }

  /**
   * 初始化监听器
   * 绑定 mouseup 事件
   */
  init(): void {
    if (!PageContextExtractor.isValidWebPage()) {
      console.log('[TextSelectionHandler] Skipping invalid page');
      return;
    }

    this.boundHandleSelection = this.handleSelection.bind(this);
    this.boundHideButton = this.hideSaveButton.bind(this);

    document.addEventListener('mouseup', this.boundHandleSelection);
    document.addEventListener('mousedown', this.boundHideButton);
    
    console.log('[TextSelectionHandler] Initialized');
  }

  /**
   * 处理文本选择事件
   * @param event - 鼠标事件
   */
  private handleSelection(event: MouseEvent): void {
    // 延迟执行，确保选择已完成
    setTimeout(() => {
      const selection = window.getSelection();
      const selectedText = selection?.toString().trim() || '';

      // 检查文本长度
      if (selectedText.length < this.minLength) {
        this.hideSaveButton();
        return;
      }

      // 获取选择位置
      const range = selection?.getRangeAt(0);
      if (!range) return;

      const rect = range.getBoundingClientRect();
      const position = {
        x: rect.right + window.scrollX,
        y: rect.top + window.scrollY,
      };

      this.showSaveButton(position, selectedText);
    }, 10);
  }

  /**
   * 显示保存按钮
   * @param position - 按钮位置
   * @param selectedText - 选中的文本
   */
  private showSaveButton(position: { x: number; y: number }, selectedText: string): void {
    // 移除旧按钮
    this.hideSaveButton();

    // 创建新按钮
    this.saveButton = this.createButton();
    this.saveButton.style.left = `${position.x + 5}px`;
    this.saveButton.style.top = `${position.y}px`;

    // 点击事件
    this.saveButton.addEventListener('click', async (e) => {
      e.stopPropagation();
      e.preventDefault();
      await this.saveToMemory(selectedText);
    });

    document.body.appendChild(this.saveButton);
  }

  /**
   * 隐藏保存按钮
   */
  private hideSaveButton(): void {
    if (this.saveButton) {
      this.saveButton.remove();
      this.saveButton = null;
    }
  }

  /**
   * 创建保存按钮 DOM 元素
   * @returns 按钮元素
   */
  private createButton(): HTMLElement {
    const button = document.createElement('div');
    button.id = 'powermem-save-button';
    button.innerHTML = `
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path d="M19 21H5C4.46957 21 3.96086 20.7893 3.58579 20.4142C3.21071 20.0391 3 19.5304 3 19V5C3 4.46957 3.21071 3.96086 3.58579 3.58579C3.96086 3.21071 4.46957 3 5 3H16L21 8V19C21 19.5304 20.7893 20.0391 20.4142 20.4142C20.0391 20.7893 19.5304 21 19 21Z" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
        <path d="M17 21V13H7V21" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
        <path d="M7 3V8H15" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
      </svg>
      <span>Save</span>
    `;

    // 样式
    Object.assign(button.style, {
      position: 'absolute',
      zIndex: '999999',
      display: 'flex',
      alignItems: 'center',
      gap: '6px',
      padding: '6px 12px',
      background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
      color: 'white',
      border: 'none',
      borderRadius: '6px',
      cursor: 'pointer',
      fontSize: '13px',
      fontFamily: 'system-ui, -apple-system, sans-serif',
      fontWeight: '500',
      boxShadow: '0 2px 8px rgba(0, 0, 0, 0.15)',
      transition: 'all 0.2s ease',
    });

    // 悬停效果
    button.addEventListener('mouseenter', () => {
      button.style.transform = 'translateY(-2px)';
      button.style.boxShadow = '0 4px 12px rgba(0, 0, 0, 0.2)';
    });

    button.addEventListener('mouseleave', () => {
      button.style.transform = 'translateY(0)';
      button.style.boxShadow = '0 2px 8px rgba(0, 0, 0, 0.15)';
    });

    return button;
  }

  /**
   * 发送保存请求到 Background
   * @param text - 要保存的文本
   */
  private async saveToMemory(text: string): Promise<void> {
    try {
      // 获取页面上下文
      const pageContext = PageContextExtractor.getFullContext();
      const selectionContext = PageContextExtractor.getSelectionContext(text);

      // 发送消息到 background
      const message: Message = {
        type: 'SAVE_MEMORY',
        payload: {
          content: text,
          pageContext,
          selectionContext,
        },
      };

      const response: MessageResponse = await chrome.runtime.sendMessage(message);

      if (response.success) {
        this.showSuccessToast();
      } else {
        this.showErrorToast(response.error || '保存失败');
      }

      // 隐藏按钮
      this.hideSaveButton();
      
      // 清除选择
      window.getSelection()?.removeAllRanges();
    } catch (error) {
      console.error('[TextSelectionHandler] Save failed:', error);
      this.showErrorToast('保存失败，请检查扩展设置');
    }
  }

  /**
   * 显示保存成功提示
   */
  private showSuccessToast(): void {
    this.showToast('✓ 保存成功！', '#10b981');
  }

  /**
   * 显示保存失败提示
   * @param error - 错误信息
   */
  private showErrorToast(error: string): void {
    this.showToast(`✗ ${error}`, '#ef4444');
  }

  /**
   * 显示 Toast 提示
   * @param message - 提示消息
   * @param backgroundColor - 背景色
   */
  private showToast(message: string, backgroundColor: string): void {
    const toast = document.createElement('div');
    toast.textContent = message;

    Object.assign(toast.style, {
      position: 'fixed',
      top: '20px',
      right: '20px',
      zIndex: '1000000',
      padding: '12px 20px',
      background: backgroundColor,
      color: 'white',
      borderRadius: '8px',
      fontSize: '14px',
      fontFamily: 'system-ui, -apple-system, sans-serif',
      fontWeight: '500',
      boxShadow: '0 4px 12px rgba(0, 0, 0, 0.15)',
      animation: 'powermem-toast-in 0.3s ease',
    });

    // 添加动画样式
    const style = document.createElement('style');
    style.textContent = `
      @keyframes powermem-toast-in {
        from {
          opacity: 0;
          transform: translateY(-20px);
        }
        to {
          opacity: 1;
          transform: translateY(0);
        }
      }
      @keyframes powermem-toast-out {
        from {
          opacity: 1;
          transform: translateY(0);
        }
        to {
          opacity: 0;
          transform: translateY(-20px);
        }
      }
    `;
    document.head.appendChild(style);

    document.body.appendChild(toast);

    // 3 秒后移除
    setTimeout(() => {
      toast.style.animation = 'powermem-toast-out 0.3s ease';
      setTimeout(() => {
        toast.remove();
        style.remove();
      }, 300);
    }, 3000);
  }

  /**
   * 销毁处理器
   * 移除事件监听和 DOM 元素
   */
  destroy(): void {
    if (this.boundHandleSelection) {
      document.removeEventListener('mouseup', this.boundHandleSelection);
    }
    
    if (this.boundHideButton) {
      document.removeEventListener('mousedown', this.boundHideButton);
    }

    this.hideSaveButton();
    
    console.log('[TextSelectionHandler] Destroyed');
  }
}

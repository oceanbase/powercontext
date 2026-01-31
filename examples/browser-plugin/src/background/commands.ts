import { MessageHandler } from './messageHandler';
import { NotificationService } from '@/services/notification/notificationService';
import type { Message } from '@/types';

/**
 * 快捷键命令处理器
 * 处理扩展定义的快捷键
 */
export class CommandHandler {
  private static messageHandler: MessageHandler;

  /**
   * 初始化快捷键监听
   */
  static init(messageHandler: MessageHandler): void {
    this.messageHandler = messageHandler;
    
    chrome.commands.onCommand.addListener((command, tab) => {
      this.handleCommand(command, tab);
    });
    
    console.log('[CommandHandler] Initialized');
  }

  /**
   * 处理快捷键命令
   * @param command - 命令名称
   * @param tab - 当前标签页
   */
  private static async handleCommand(
    command: string,
    tab?: chrome.tabs.Tab
  ): Promise<void> {
    console.log('[CommandHandler] Command received:', command);

    switch (command) {
      case 'save-selection':
        if (tab?.id) {
          await this.handleSaveSelection(tab);
        }
        break;
      
      default:
        console.warn('[CommandHandler] Unknown command:', command);
    }
  }

  /**
   * 处理保存选择快捷键 (Ctrl+Shift+S)
   * @param tab - 当前标签页
   */
  private static async handleSaveSelection(tab: chrome.tabs.Tab): Promise<void> {
    try {
      if (!tab.id) return;

      // 从标签页获取选中文本
      const selectedText = await this.getSelectedText(tab.id);
      
      if (!selectedText || selectedText.trim().length < 10) {
        NotificationService.showInfo('请先选择至少 10 个字符的文本');
        return;
      }

      // 发送保存消息
      const message: Message = {
        type: 'SAVE_MEMORY',
        payload: {
          content: selectedText,
          pageContext: {
            url: tab.url || '',
            title: tab.title || 'Untitled',
            domain: tab.url ? new URL(tab.url).hostname : '',
          },
        },
      };

      const response = await this.messageHandler.handle(
        message,
        { tab } as chrome.runtime.MessageSender
      );

      if (response.success) {
        NotificationService.showSuccess('记忆保存成功！');
      } else {
        NotificationService.showError(response.error || '保存失败');
      }
    } catch (error) {
      console.error('[CommandHandler] Save failed:', error);
      NotificationService.showError('保存失败，请检查设置');
    }
  }

  /**
   * 从标签页获取选中文本
   * @param tabId - 标签页 ID
   * @returns 选中的文本
   */
  private static async getSelectedText(tabId: number): Promise<string> {
    try {
      const results = await chrome.scripting.executeScript({
        target: { tabId },
        func: () => window.getSelection()?.toString() || '',
      });

      return results[0]?.result || '';
    } catch (error) {
      console.error('[CommandHandler] Failed to get selected text:', error);
      return '';
    }
  }
}

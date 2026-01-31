import { MessageHandler } from './messageHandler';
import { NotificationService } from '@/services/notification/notificationService';

/**
 * 右键菜单管理器
 * 创建和管理扩展的右键菜单项
 */
export class ContextMenuManager {
  private static messageHandler: MessageHandler;

  /**
   * 初始化右键菜单
   * 在扩展安装时调用
   */
  static init(messageHandler: MessageHandler): void {
    this.messageHandler = messageHandler;
    this.createMainMenu();
    
    // 监听点击事件
    chrome.contextMenus.onClicked.addListener((info, tab) => {
      this.handleClick(info, tab);
    });
    
    console.log('[ContextMenuManager] Initialized');
  }

  /**
   * 创建主菜单项
   */
  private static createMainMenu(): void {
    chrome.contextMenus.create({
      id: 'save-to-powermem',
      title: 'Save to PowerMem',
      contexts: ['selection'],
    });
  }

  /**
   * 处理菜单点击事件
   * @param info - 点击信息
   * @param tab - 当前标签页
   */
  static handleClick(
    info: chrome.contextMenus.OnClickData,
    tab?: chrome.tabs.Tab
  ): void {
    if (info.menuItemId === 'save-to-powermem' && info.selectionText) {
      this.saveFromSelection(
        info.selectionText,
        info.pageUrl || '',
        tab?.title || 'Untitled'
      );
    }
  }

  /**
   * 从选中文本保存记忆
   * @param selectionText - 选中的文本
   * @param pageUrl - 页面 URL
   * @param pageTitle - 页面标题
   */
  private static async saveFromSelection(
    selectionText: string,
    pageUrl: string,
    pageTitle: string
  ): Promise<void> {
    try {
      const url = new URL(pageUrl);
      
      const response = await this.messageHandler.handle(
        {
          type: 'SAVE_MEMORY',
          payload: {
            content: selectionText,
            pageContext: {
              url: pageUrl,
              title: pageTitle,
              domain: url.hostname,
            },
          },
        },
        {} as chrome.runtime.MessageSender
      );

      if (response.success) {
        NotificationService.showSuccess('记忆保存成功！');
      } else {
        NotificationService.showError(response.error || '保存失败');
      }
    } catch (error) {
      console.error('[ContextMenuManager] Save failed:', error);
      NotificationService.showError('保存失败，请检查设置');
    }
  }
}

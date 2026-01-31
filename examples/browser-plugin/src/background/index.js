/**
 * Background Service Worker 入口
 * 负责扩展后台逻辑、消息处理、API 通信
 */
import { StorageService } from '@/services/storage/storageService';
import { MessageHandler } from './messageHandler';
import { ContextMenuManager } from './contextMenus';
import { CommandHandler } from './commands';
console.log('[PowerMem Background] Service Worker starting...');
// 初始化消息处理器
const messageHandler = new MessageHandler();
/**
 * 扩展安装事件
 * 初始化设置和上下文菜单
 */
chrome.runtime.onInstalled.addListener(async (details) => {
    console.log('[PowerMem Background] Extension installed:', details.reason);
    if (details.reason === 'install') {
        // 首次安装，初始化设置
        await StorageService.initializeSettings();
        console.log('[PowerMem Background] Default settings initialized');
    }
    // 初始化右键菜单
    ContextMenuManager.init(messageHandler);
});
// 初始化快捷键处理器
CommandHandler.init(messageHandler);
/**
 * 消息监听器
 * 处理来自 Content Script 和 Popup 的消息
 */
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    console.log('[PowerMem Background] Message received:', message.type);
    // 异步处理消息
    messageHandler.handle(message, sender)
        .then((response) => {
        sendResponse(response);
    })
        .catch((error) => {
        console.error('[PowerMem Background] Message handling error:', error);
        sendResponse({
            success: false,
            error: error.message || 'Unknown error',
        });
    });
    // 返回 true 表示异步响应
    return true;
});
console.log('[PowerMem Background] Service Worker ready');

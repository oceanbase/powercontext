import type { Message, MessageResponse } from '../types/message';

/**
 * 发送消息到 Background
 * @param message - 消息对象
 * @returns 消息响应
 * @throws {Error} 当消息发送失败时
 */
export async function sendMessage<T = any>(
  message: Message
): Promise<MessageResponse<T>> {
  try {
    const response = await chrome.runtime.sendMessage(message);
    return response as MessageResponse<T>;
  } catch (error) {
    console.error('Failed to send message:', error);
    throw new Error(
      error instanceof Error ? error.message : '消息发送失败'
    );
  }
}

/**
 * 发送消息到指定标签页
 * @param tabId - 标签页 ID
 * @param message - 消息对象
 * @returns 消息响应
 */
export async function sendMessageToTab<T = any>(
  tabId: number,
  message: Message
): Promise<MessageResponse<T>> {
  try {
    const response = await chrome.tabs.sendMessage(tabId, message);
    return response as MessageResponse<T>;
  } catch (error) {
    console.error('Failed to send message to tab:', error);
    throw new Error(
      error instanceof Error ? error.message : '消息发送失败'
    );
  }
}

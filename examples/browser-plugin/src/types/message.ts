/**
 * 消息类型枚举
 */
export type MessageType = 
  | 'SAVE_MEMORY'
  | 'SEARCH_MEMORIES'
  | 'GET_MEMORIES'
  | 'GET_MEMORY'
  | 'UPDATE_MEMORY'
  | 'DELETE_MEMORY'
  | 'GET_SETTINGS'
  | 'UPDATE_SETTINGS'
  | 'TEST_CONNECTION'
  | 'GET_PAGE_CONTEXT';

/**
 * 消息结构
 */
export interface Message<T = any> {
  /** 消息类型 */
  type: MessageType;
  
  /** 消息负载 */
  payload?: T;
  
  /** 标签页 ID (可选) */
  tabId?: number;
}

/**
 * 消息响应结构
 */
export interface MessageResponse<T = any> {
  /** 是否成功 */
  success: boolean;
  
  /** 响应数据 */
  data?: T;
  
  /** 错误信息 */
  error?: string;
}

/**
 * 通知选项
 */
export interface NotificationOptions {
  /** 通知标题 */
  title: string;
  
  /** 通知消息 */
  message: string;
  
  /** 通知类型 */
  type?: 'basic' | 'image' | 'list' | 'progress';
  
  /** 图标 URL */
  iconUrl?: string;
  
  /** 优先级 */
  priority?: number;
}

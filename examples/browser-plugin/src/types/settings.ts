/**
 * 扩展设置配置
 */
export interface ExtensionSettings {
  /** 服务器配置 */
  server: {
    /** PowerMem 服务器地址 */
    url: string;
    
    /** API 密钥 */
    apiKey: string;
    
    /** 请求超时时间(ms) */
    timeout: number;
  };
  
  /** 用户配置 */
  user: {
    /** 用户唯一标识 */
    userId: string;
    
    /** 默认智能体 ID */
    defaultAgentId: string;
  };
  
  /** 捕获配置 */
  capture: {
    /** 是否自动保存选中文本 */
    autoSave: boolean;
    
    /** 最小选择长度(字符数) */
    minSelectionLength: number;
    
    /** 是否包含页面上下文 */
    includePageContext: boolean;
  };
  
  /** UI 配置 */
  ui: {
    /** 主题 */
    theme: 'light' | 'dark' | 'auto';
    
    /** 语言 */
    language: 'en' | 'zh';
  };
  
  /** 高级配置 */
  advanced: {
    /** 启用上下文建议 */
    enableContextualSuggestions: boolean;
    
    /** 最大最近记忆数 */
    maxRecentMemories: number;
  };
}

/**
 * 设置存储键
 */
export const STORAGE_KEYS = {
  SETTINGS: 'powermem_settings',
  CACHE: 'powermem_cache',
} as const;

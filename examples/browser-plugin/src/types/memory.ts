/**
 * PowerMem 记忆数据结构
 */
export interface Memory {
  /** 记忆唯一标识 */
  memory_id: string;
  
  /** 记忆内容 */
  content: string;
  
  /** 用户 ID */
  user_id: string;
  
  /** 智能体 ID (上下文标识) */
  agent_id: string;
  
  /** 运行 ID (可选) */
  run_id?: string;
  
  /** 元数据 */
  metadata: {
    /** 来源标识 */
    source: 'browser_extension';
    
    /** 类型 */
    type: 'web_selection' | 'bookmark' | 'full_page' | 'user_profile';
    
    /** 原始 URL */
    url: string;
    
    /** 页面标题 */
    title: string;
    
    /** 域名 */
    domain: string;
    
    /** 时间戳 */
    timestamp: string;
    
    /** 扩展字段 */
    [key: string]: any;
  };
  
  /** 过滤器 */
  filters?: {
    category: string;
    [key: string]: string;
  };
  
  /** 创建时间 */
  created_at?: string;
  
  /** 更新时间 */
  updated_at?: string;
}

/**
 * 记忆列表响应
 */
export interface MemoryListResponse {
  memories: Memory[];
  total: number;
}

/**
 * 记忆搜索响应
 */
export interface MemorySearchResponse {
  results: Memory[];
  total: number;
  query: string;
}

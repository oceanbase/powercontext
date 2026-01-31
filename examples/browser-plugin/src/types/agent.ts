/**
 * Agent（上下文空间）类型定义
 */

/**
 * Agent（上下文空间）定义
 */
export interface Agent {
  /** Agent ID */
  id: string;
  
  /** Agent 名称 */
  name: string;
  
  /** 描述 */
  description?: string;
  
  /** 图标 */
  icon?: string;
  
  /** 颜色标识 */
  color?: string;
  
  /** 是否为系统预定义 */
  isPredefined: boolean;
  
  /** 创建时间 */
  createdAt: string;
  
  /** 记忆数量 */
  memoryCount?: number;
}

/**
 * 预定义 Agent 列表
 */
export const PREDEFINED_AGENTS: Agent[] = [
  {
    id: 'work',
    name: 'Work',
    description: 'Work-related memories and notes',
    icon: '💼',
    color: '#4F46E5',
    isPredefined: true,
    createdAt: new Date().toISOString(),
  },
  {
    id: 'personal',
    name: 'Personal',
    description: 'Personal memories and thoughts',
    icon: '🏠',
    color: '#10B981',
    isPredefined: true,
    createdAt: new Date().toISOString(),
  },
  {
    id: 'research',
    name: 'Research',
    description: 'Research and learning materials',
    icon: '📚',
    color: '#F59E0B',
    isPredefined: true,
    createdAt: new Date().toISOString(),
  },
];

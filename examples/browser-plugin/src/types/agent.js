/**
 * Agent（上下文空间）类型定义
 */
/**
 * 预定义 Agent 列表
 */
export const PREDEFINED_AGENTS = [
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

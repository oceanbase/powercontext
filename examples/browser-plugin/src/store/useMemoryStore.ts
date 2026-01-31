import { create } from 'zustand';
import type { Memory } from '../types/memory';

/**
 * 记忆状态接口
 */
interface MemoryState {
  // ========== 状态 ==========
  
  /** 记忆列表 */
  memories: Memory[];
  
  /** 搜索结果列表 */
  searchResults: Memory[];
  
  /** 当前选中的记忆 */
  selectedMemory: Memory | null;
  
  /** 搜索关键词 */
  searchQuery: string;
  
  /** 是否正在加载 */
  isLoading: boolean;
  
  /** 错误信息 */
  error: string | null;
  
  /** 是否有更多数据 */
  hasMore: boolean;
  
  /** 当前页偏移量 */
  offset: number;
  
  // ========== 操作 ==========
  
  /**
   * 设置记忆列表
   * @param memories - 记忆数组
   */
  setMemories: (memories: Memory[]) => void;
  
  /**
   * 添加记忆到列表
   * @param memories - 要添加的记忆数组
   */
  appendMemories: (memories: Memory[]) => void;
  
  /**
   * 设置搜索结果
   * @param results - 搜索结果数组
   */
  setSearchResults: (results: Memory[]) => void;
  
  /**
   * 设置选中的记忆
   * @param memory - 记忆对象
   */
  setSelectedMemory: (memory: Memory | null) => void;
  
  /**
   * 设置搜索关键词
   * @param query - 搜索关键词
   */
  setSearchQuery: (query: string) => void;
  
  /**
   * 设置加载状态
   * @param loading - 是否加载中
   */
  setLoading: (loading: boolean) => void;
  
  /**
   * 设置错误信息
   * @param error - 错误消息
   */
  setError: (error: string | null) => void;
  
  /**
   * 设置是否有更多数据
   * @param hasMore - 是否有更多
   */
  setHasMore: (hasMore: boolean) => void;
  
  /**
   * 设置偏移量
   * @param offset - 偏移量
   */
  setOffset: (offset: number) => void;
  
  /**
   * 清除搜索状态
   */
  clearSearch: () => void;
  
  /**
   * 重置所有状态
   */
  reset: () => void;
}

/**
 * 使用记忆状态 Hook
 */
export const useMemoryStore = create<MemoryState>((set) => ({
  // 初始状态
  memories: [],
  searchResults: [],
  selectedMemory: null,
  searchQuery: '',
  isLoading: false,
  error: null,
  hasMore: true,
  offset: 0,
  
  // 操作实现
  setMemories: (memories) => set({ memories }),
  
  appendMemories: (memories) => set((state) => ({
    memories: [...state.memories, ...memories]
  })),
  
  setSearchResults: (results) => set({ searchResults: results }),
  
  setSelectedMemory: (memory) => set({ selectedMemory: memory }),
  
  setSearchQuery: (query) => set({ searchQuery: query }),
  
  setLoading: (loading) => set({ isLoading: loading }),
  
  setError: (error) => set({ error }),
  
  setHasMore: (hasMore) => set({ hasMore }),
  
  setOffset: (offset) => set({ offset }),
  
  clearSearch: () => set({
    searchQuery: '',
    searchResults: [],
    error: null
  }),
  
  reset: () => set({
    memories: [],
    searchResults: [],
    selectedMemory: null,
    searchQuery: '',
    isLoading: false,
    error: null,
    hasMore: true,
    offset: 0
  })
}));

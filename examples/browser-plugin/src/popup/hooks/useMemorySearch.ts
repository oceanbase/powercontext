import { useState, useCallback } from 'react';
import { useMemoryStore } from '../../store/useMemoryStore';
import { sendMessage } from '../../utils/messaging';
import type { MemorySearchResponse } from '../../types/memory';

/**
 * 搜索状态
 */
interface SearchState {
  /** 是否正在搜索 */
  isSearching: boolean;
  
  /** 搜索错误 */
  error: string | null;
}

/**
 * 搜索 Hook 返回值
 */
interface UseMemorySearchReturn extends SearchState {
  /**
   * 执行搜索
   * @param query - 搜索关键词
   */
  search: (query: string) => Promise<void>;
  
  /**
   * 清除搜索结果
   */
  clearSearch: () => void;
}

/**
 * 记忆搜索 Hook
 * 封装搜索逻辑和状态管理
 * 
 * @returns 搜索功能和状态
 */
export function useMemorySearch(): UseMemorySearchReturn {
  const [isSearching, setIsSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  
  const { setSearchResults, setSearchQuery, clearSearch: clearStoreSearch } = useMemoryStore();
  
  const search = useCallback(async (query: string) => {
    if (!query.trim()) {
      clearStoreSearch();
      return;
    }
    
    setIsSearching(true);
    setError(null);
    setSearchQuery(query);
    
    try {
      const response = await sendMessage<MemorySearchResponse>({
        type: 'SEARCH_MEMORIES',
        payload: { query }
      });
      
      if (response.success && response.data) {
        setSearchResults(response.data.results);
      } else {
        throw new Error(response.error || '搜索失败');
      }
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : '搜索失败';
      setError(errorMessage);
      setSearchResults([]);
    } finally {
      setIsSearching(false);
    }
  }, [setSearchResults, setSearchQuery, clearStoreSearch]);
  
  const clearSearch = useCallback(() => {
    clearStoreSearch();
    setError(null);
  }, [clearStoreSearch]);
  
  return {
    isSearching,
    error,
    search,
    clearSearch
  };
}

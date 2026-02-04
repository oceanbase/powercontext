import { useState, useCallback } from 'react';
import { useMemoryStore } from '@/store/useMemoryStore';
import { sendMessage } from '@/utils/messaging';
/**
 * 记忆搜索 Hook
 * 封装搜索逻辑和状态管理
 *
 * @returns 搜索功能和状态
 */
export function useMemorySearch() {
    const [isSearching, setIsSearching] = useState(false);
    const [error, setError] = useState(null);
    const { setSearchResults, setSearchQuery, clearSearch: clearStoreSearch } = useMemoryStore();
    const search = useCallback(async (query) => {
        if (!query.trim()) {
            clearStoreSearch();
            return;
        }
        setIsSearching(true);
        setError(null);
        setSearchQuery(query);
        try {
            const response = await sendMessage({
                type: 'SEARCH_MEMORIES',
                payload: { query }
            });
            if (response.success && response.data) {
                const results = response.data.results || [];
                setSearchResults(results);
            }
            else {
                throw new Error(response.error || '搜索失败');
            }
        }
        catch (err) {
            const errorMessage = err instanceof Error ? err.message : '搜索失败';
            setError(errorMessage);
            setSearchResults([]);
        }
        finally {
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

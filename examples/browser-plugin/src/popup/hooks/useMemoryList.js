import { useState, useCallback } from 'react';
import { useMemoryStore } from '../../store/useMemoryStore';
import { sendMessage } from '../../utils/messaging';
const PAGE_SIZE = 20;
/**
 * 记忆列表 Hook
 * 封装列表加载和分页逻辑
 *
 * @returns 列表功能和状态
 */
export function useMemoryList() {
    const [isLoading, setIsLoading] = useState(false);
    const [hasMore, setHasMore] = useState(true);
    const [error, setError] = useState(null);
    const { setMemories, appendMemories, memories, setOffset, offset } = useMemoryStore();
    const loadInitial = useCallback(async () => {
        setIsLoading(true);
        setError(null);
        setOffset(0);
        try {
            const response = await sendMessage({
                type: 'GET_MEMORIES',
                payload: {
                    limit: PAGE_SIZE,
                    offset: 0
                }
            });
            if (response.success && response.data) {
                setMemories(response.data.memories);
                setHasMore(response.data.memories.length >= PAGE_SIZE);
            }
            else {
                throw new Error(response.error || '加载失败');
            }
        }
        catch (err) {
            const errorMessage = err instanceof Error ? err.message : '加载失败';
            setError(errorMessage);
            setMemories([]);
        }
        finally {
            setIsLoading(false);
        }
    }, [setMemories, setOffset]);
    const loadMore = useCallback(async () => {
        if (isLoading || !hasMore)
            return;
        setIsLoading(true);
        setError(null);
        const newOffset = offset + PAGE_SIZE;
        try {
            const response = await sendMessage({
                type: 'GET_MEMORIES',
                payload: {
                    limit: PAGE_SIZE,
                    offset: newOffset
                }
            });
            if (response.success && response.data) {
                appendMemories(response.data.memories);
                setOffset(newOffset);
                setHasMore(response.data.memories.length >= PAGE_SIZE);
            }
            else {
                throw new Error(response.error || '加载更多失败');
            }
        }
        catch (err) {
            const errorMessage = err instanceof Error ? err.message : '加载更多失败';
            setError(errorMessage);
        }
        finally {
            setIsLoading(false);
        }
    }, [isLoading, hasMore, offset, appendMemories, setOffset]);
    const refresh = useCallback(async () => {
        await loadInitial();
    }, [loadInitial]);
    return {
        isLoading,
        hasMore,
        error,
        loadInitial,
        loadMore,
        refresh
    };
}

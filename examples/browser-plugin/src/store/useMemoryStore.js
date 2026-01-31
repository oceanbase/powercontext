import { create } from 'zustand';
/**
 * 使用记忆状态 Hook
 */
export const useMemoryStore = create((set) => ({
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

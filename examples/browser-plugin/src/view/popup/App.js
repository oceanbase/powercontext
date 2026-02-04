import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useState } from 'react';
import { SearchBar } from './components/SearchBar';
import { MemoryList } from './components/MemoryList';
import { MemoryDetailModal } from './components/MemoryDetailModal';
import { ErrorBanner } from '@/shared/ErrorBanner';
import { useMemoryStore } from '@/store/useMemoryStore';
import { useMemorySearch } from './hooks/useMemorySearch';
import { useMemoryList } from './hooks/useMemoryList';
import './styles/variables.css';
import './styles/components.css';
/**
 * Popup 主组件
 * 显示搜索界面和最近记忆
 */
function App() {
    const [selectedMemory, setSelectedMemory] = useState(null);
    const [isModalOpen, setIsModalOpen] = useState(false);
    const { memories, searchResults, searchQuery } = useMemoryStore();
    const { search, clearSearch, isSearching, error: searchError } = useMemorySearch();
    const { loadInitial, loadMore, isLoading, hasMore, error: listError } = useMemoryList();
    // 初始加载记忆列表
    useEffect(() => {
        loadInitial();
    }, []);
    /**
     * 处理搜索
     */
    const handleSearch = (query) => {
        search(query);
    };
    /**
     * 处理清除搜索
     */
    const handleClearSearch = () => {
        clearSearch();
    };
    /**
     * 处理记忆卡片点击
     */
    const handleMemoryClick = (memory) => {
        setSelectedMemory(memory);
        setIsModalOpen(true);
    };
    /**
     * 关闭详情弹窗
     */
    const handleCloseModal = () => {
        setIsModalOpen(false);
        setSelectedMemory(null);
    };
    /**
     * 获取显示的记忆列表
     */
    const displayMemories = searchQuery ? searchResults : memories;
    const isSearchMode = !!searchQuery;
    const currentError = searchError || listError;
    return (_jsxs("div", { className: "popup-container", children: [_jsxs("div", { className: "popup-header", children: [_jsxs("div", { className: "popup-logo", children: [_jsx("span", { className: "popup-logo-icon", children: "\uD83E\uDDE0" }), _jsx("span", { children: "PowerMem" })] }), _jsx("button", { className: "popup-settings-btn", onClick: () => chrome.runtime.openOptionsPage(), title: "\u8BBE\u7F6E", children: "\u2699\uFE0F" })] }), _jsx(SearchBar, { onSearch: handleSearch, onClear: handleClearSearch }), currentError && (_jsx(ErrorBanner, { message: currentError, onRetry: isSearchMode ? () => search(searchQuery) : loadInitial })), _jsx(MemoryList, { memories: displayMemories, isSearchMode: isSearchMode, isLoading: isSearchMode ? isSearching : isLoading, hasMore: !isSearchMode && hasMore, onItemClick: handleMemoryClick, onLoadMore: loadMore }), _jsx(MemoryDetailModal, { memory: selectedMemory, isOpen: isModalOpen, onClose: handleCloseModal })] }));
}
export default App;

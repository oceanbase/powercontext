import React, { useEffect, useState } from 'react';
import { SearchBar } from './components/SearchBar';
import { MemoryList } from './components/MemoryList';
import { MemoryDetailModal } from './components/MemoryDetailModal';
import { ErrorBanner } from '@/shared/ErrorBanner';
import { useMemoryStore } from '@/store/useMemoryStore';
import { useMemorySearch } from './hooks/useMemorySearch';
import { useMemoryList } from './hooks/useMemoryList';
import type { Memory } from '@/types/memory';
import './styles/variables.css';
import './styles/components.css';

/**
 * Popup 主组件
 * 显示搜索界面和最近记忆
 */
function App() {
  const [selectedMemory, setSelectedMemory] = useState<Memory | null>(null);
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
  const handleSearch = (query: string) => {
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
  const handleMemoryClick = (memory: Memory) => {
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
  
  return (
    <div className="popup-container">
      {/* 头部 */}
      <div className="popup-header">
        <div className="popup-logo">
          <span className="popup-logo-icon">🧠</span>
          <span>PowerMem</span>
        </div>
        <button 
          className="popup-settings-btn"
          onClick={() => chrome.runtime.openOptionsPage()}
          title="设置"
        >
          ⚙️
        </button>
      </div>
      
      {/* 搜索栏 */}
      <SearchBar 
        onSearch={handleSearch}
        onClear={handleClearSearch}
      />
      
      {/* 错误提示 */}
      {currentError && (
        <ErrorBanner 
          message={currentError}
          onRetry={isSearchMode ? () => search(searchQuery) : loadInitial}
        />
      )}
      
      {/* 记忆列表 */}
      <MemoryList
        memories={displayMemories}
        isSearchMode={isSearchMode}
        isLoading={isSearchMode ? isSearching : isLoading}
        hasMore={!isSearchMode && hasMore}
        onItemClick={handleMemoryClick}
        onLoadMore={loadMore}
      />
      
      {/* 记忆详情弹窗 */}
      <MemoryDetailModal
        memory={selectedMemory}
        isOpen={isModalOpen}
        onClose={handleCloseModal}
      />
    </div>
  );
}

export default App;

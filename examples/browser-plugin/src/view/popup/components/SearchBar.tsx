import React, { useState, useCallback, useEffect } from 'react';
import { debounce } from '@/utils/helpers';
import './SearchBar.css';

/**
 * SearchBar 组件属性
 */
interface SearchBarProps {
  /** 搜索提示文本 */
  placeholder?: string;
  
  /** 是否自动聚焦 */
  autoFocus?: boolean;
  
  /** 搜索回调 */
  onSearch?: (query: string) => void;
  
  /** 清除回调 */
  onClear?: () => void;
}

/**
 * 搜索栏组件
 * 提供搜索输入和实时搜索功能
 * 
 * @param props - 组件属性
 * @returns React 组件
 */
export function SearchBar({ 
  placeholder = '搜索记忆...', 
  autoFocus = false,
  onSearch,
  onClear
}: SearchBarProps): JSX.Element {
  const [inputValue, setInputValue] = useState('');
  
  // 创建防抖搜索函数
  const debouncedSearch = useCallback(
    debounce((query: string) => {
      if (onSearch) {
        onSearch(query);
      }
    }, 500),
    [onSearch]
  );
  
  /**
   * 处理输入变化
   */
  const handleInputChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    const value = event.target.value;
    setInputValue(value);
    debouncedSearch(value);
  };
  
  /**
   * 处理表单提交
   */
  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    if (onSearch) {
      onSearch(inputValue);
    }
  };
  
  /**
   * 清除搜索
   */
  const handleClear = () => {
    setInputValue('');
    if (onClear) {
      onClear();
    }
    if (onSearch) {
      onSearch('');
    }
  };
  
  return (
    <form className="search-bar" onSubmit={handleSubmit}>
      <div className="search-input-wrapper">
        <span className="search-icon">🔍</span>
        <input
          type="text"
          className="search-input"
          placeholder={placeholder}
          value={inputValue}
          onChange={handleInputChange}
          autoFocus={autoFocus}
        />
        {inputValue && (
          <button
            type="button"
            className="search-clear-btn"
            onClick={handleClear}
            aria-label="清除"
          >
            ✕
          </button>
        )}
      </div>
    </form>
  );
}

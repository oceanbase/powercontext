import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useRef } from 'react';
import { MemoryCard } from './MemoryCard';
import { LoadingSpinner } from '../../shared/LoadingSpinner';
import { EmptyState } from '../../shared/EmptyState';
import './MemoryList.css';
/**
 * 记忆列表组件
 * 显示记忆卡片列表，支持滚动加载
 *
 * @param props - 组件属性
 * @returns React 组件
 */
export function MemoryList({ memories, isSearchMode = false, isLoading = false, hasMore = false, onItemClick, onLoadMore }) {
    const listRef = useRef(null);
    /**
     * 处理滚动事件
     */
    const handleScroll = (event) => {
        const element = event.currentTarget;
        if (isScrolledToBottom(element) && hasMore && !isLoading && onLoadMore) {
            onLoadMore();
        }
    };
    /**
     * 判断是否到达底部
     */
    const isScrolledToBottom = (element) => {
        const threshold = 50; // 提前50px触发加载
        return element.scrollHeight - element.scrollTop - element.clientHeight < threshold;
    };
    // 显示加载中
    if (isLoading && memories.length === 0) {
        return (_jsx("div", { className: "memory-list", children: _jsx(LoadingSpinner, { text: "\u52A0\u8F7D\u4E2D..." }) }));
    }
    // 显示空状态
    if (memories.length === 0) {
        return (_jsx("div", { className: "memory-list", children: _jsx(EmptyState, { icon: isSearchMode ? '🔍' : '📭', title: isSearchMode ? '未找到相关记忆' : '还没有记忆', description: isSearchMode ? '尝试使用其他关键词搜索' : '开始捕获网页内容以创建记忆' }) }));
    }
    return (_jsxs("div", { className: "memory-list", ref: listRef, onScroll: handleScroll, children: [memories.map((memory) => (_jsx(MemoryCard, { memory: memory, onClick: onItemClick }, memory.memory_id))), isLoading && (_jsx("div", { className: "memory-list-loading", children: _jsx(LoadingSpinner, { size: "small", text: "\u52A0\u8F7D\u66F4\u591A..." }) })), !hasMore && memories.length > 0 && (_jsx("div", { className: "memory-list-end", children: "\u5DF2\u663E\u793A\u6240\u6709\u8BB0\u5FC6" }))] }));
}

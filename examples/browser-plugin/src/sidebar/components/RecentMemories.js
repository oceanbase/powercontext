import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useMemoryStore } from '@/store/useMemoryStore';
import { LoadingSpinner } from '@/shared/LoadingSpinner';
import { EmptyState } from '@/shared/EmptyState';
import './RecentMemories.css';
/**
 * 最近记忆组件
 * 显示最近添加的记忆
 *
 * @param props - 组件属性
 * @returns React 组件
 */
export function RecentMemories(props) {
    const { limit = 20 } = props;
    const { memories, isLoading } = useMemoryStore();
    // 获取最近的记忆（按创建时间排序）
    const recentMemories = memories
        .slice()
        .sort((a, b) => {
        const dateA = a.created_at ? new Date(a.created_at).getTime() : 0;
        const dateB = b.created_at ? new Date(b.created_at).getTime() : 0;
        return dateB - dateA; // 降序
    })
        .slice(0, limit);
    if (isLoading) {
        return (_jsxs("div", { className: "recent-memories", children: [_jsx("h3", { className: "recent-memories-title", children: "Recent memories" }), _jsx(LoadingSpinner, {})] }));
    }
    if (recentMemories.length === 0) {
        return (_jsxs("div", { className: "recent-memories", children: [_jsx("h3", { className: "recent-memories-title", children: "Recent memories" }), _jsx(EmptyState, { title: "No memories yet", description: "Start capturing memories by selecting text on any webpage" })] }));
    }
    return (_jsxs("div", { className: "recent-memories", children: [_jsx("h3", { className: "recent-memories-title", children: "Recent memories" }), _jsx("div", { className: "memory-list", children: recentMemories.map(memory => (_jsxs("div", { className: "memory-card", children: [_jsxs("div", { className: "memory-content", children: [memory.content.substring(0, 150), memory.content.length > 150 && '...'] }), _jsxs("div", { className: "memory-meta", children: [_jsx("span", { className: "memory-domain", children: memory.metadata?.domain }), _jsx("span", { className: "memory-date", children: memory.created_at ? new Date(memory.created_at).toLocaleDateString() : '' })] })] }, memory.memory_id))) })] }));
}

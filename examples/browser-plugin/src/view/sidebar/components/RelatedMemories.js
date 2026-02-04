import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useState } from 'react';
import { RecommendationEngine } from '@/services/recommendation/recommendationEngine';
import { useMemoryStore } from '@/store/useMemoryStore';
import { LoadingSpinner } from '@/shared/LoadingSpinner';
import { EmptyState } from '@/shared/EmptyState';
import './RelatedMemories.css';
/**
 * 相关记忆组件
 * 显示与当前页面相关的记忆
 *
 * @param props - 组件属性
 * @returns React 组件
 */
export function RelatedMemories(props) {
    const { pageUrl, pageTitle } = props;
    const { memories, isLoading } = useMemoryStore();
    const [relatedMemories, setRelatedMemories] = useState([]);
    useEffect(() => {
        if (pageUrl && pageTitle && memories.length > 0) {
            // 提取域名
            let domain = '';
            try {
                const url = new URL(pageUrl);
                domain = url.hostname;
            }
            catch (error) {
                console.error('[RelatedMemories] Invalid URL:', error);
            }
            // 获取推荐
            const pageContext = { url: pageUrl, title: pageTitle, domain };
            const recommendations = RecommendationEngine.getRecommendations(pageContext, memories, 5 // 最多显示 5 个
            );
            setRelatedMemories(recommendations.map(r => r.memory));
        }
    }, [pageUrl, pageTitle, memories]);
    if (isLoading) {
        return (_jsxs("div", { className: "related-memories", children: [_jsx("h3", { className: "related-memories-title", children: "Related to this page" }), _jsx(LoadingSpinner, {})] }));
    }
    if (relatedMemories.length === 0) {
        return (_jsxs("div", { className: "related-memories", children: [_jsx("h3", { className: "related-memories-title", children: "Related to this page" }), _jsx(EmptyState, { title: "No related memories found" })] }));
    }
    return (_jsxs("div", { className: "related-memories", children: [_jsx("h3", { className: "related-memories-title", children: "Related to this page" }), _jsx("div", { className: "memory-list", children: relatedMemories.map(memory => (_jsxs("div", { className: "memory-card", children: [_jsxs("div", { className: "memory-content", children: [memory.content.substring(0, 150), memory.content.length > 150 && '...'] }), _jsxs("div", { className: "memory-meta", children: [_jsx("span", { className: "memory-domain", children: memory.metadata?.domain }), _jsx("span", { className: "memory-date", children: memory.created_at ? new Date(memory.created_at).toLocaleDateString() : '' })] })] }, memory.memory_id))) })] }));
}

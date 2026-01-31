import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { formatDate, truncateText, getDomain } from '../../utils/helpers';
import './MemoryCard.css';
/**
 * 记忆卡片组件
 * 显示单条记忆的摘要信息
 *
 * @param props - 组件属性
 * @returns React 组件
 */
export function MemoryCard({ memory, onClick, isHighlighted = false }) {
    /**
     * 获取标题
     */
    const getTitle = () => {
        return memory.metadata.title || truncateText(memory.content, 50);
    };
    /**
     * 获取预览文本
     */
    const getPreview = () => {
        return truncateText(memory.content, 150);
    };
    /**
     * 获取域名
     */
    const domain = getDomain(memory.metadata.url);
    /**
     * 处理点击
     */
    const handleClick = () => {
        if (onClick) {
            onClick(memory);
        }
    };
    return (_jsxs("div", { className: `memory-card ${isHighlighted ? 'memory-card-highlighted' : ''}`, onClick: handleClick, children: [_jsxs("div", { className: "memory-card-header", children: [_jsx("h3", { className: "memory-card-title", children: getTitle() }), _jsx("span", { className: "memory-card-type", children: memory.metadata.type })] }), _jsx("p", { className: "memory-card-preview", children: getPreview() }), _jsxs("div", { className: "memory-card-meta", children: [domain && (_jsxs("span", { className: "memory-card-domain", title: memory.metadata.url, children: ["\uD83C\uDF10 ", domain] })), _jsx("span", { className: "memory-card-date", children: formatDate(memory.metadata.timestamp || memory.created_at || '') })] })] }));
}

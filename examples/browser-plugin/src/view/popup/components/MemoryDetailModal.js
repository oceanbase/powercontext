import { Fragment as _Fragment, jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { Modal } from '@/shared/Modal';
import { formatDate, getDomain } from '@/utils/helpers';
import './MemoryDetailModal.css';
/**
 * 记忆详情弹窗组件
 * 显示记忆的完整内容和元数据
 *
 * @param props - 组件属性
 * @returns React 组件
 */
export function MemoryDetailModal({ memory, isOpen, onClose }) {
    if (!memory) {
        return _jsx(_Fragment, {});
    }
    /**
     * 在新标签页打开原始页面
     */
    const handleOpenInNewTab = () => {
        if (memory.metadata.url) {
            chrome.tabs.create({ url: memory.metadata.url });
        }
    };
    const domain = getDomain(memory.metadata.url);
    return (_jsx(Modal, { isOpen: isOpen, onClose: onClose, title: "\u8BB0\u5FC6\u8BE6\u60C5", size: "large", children: _jsxs("div", { className: "memory-detail", children: [_jsxs("div", { className: "memory-detail-header", children: [_jsx("h2", { className: "memory-detail-title", children: memory.metadata.title || '无标题' }), _jsx("span", { className: "memory-detail-type", children: memory.metadata.type })] }), _jsxs("div", { className: "memory-detail-content", children: [_jsx("h3", { className: "memory-detail-section-title", children: "\u5185\u5BB9" }), _jsx("p", { className: "memory-detail-text", children: memory.content })] }), _jsxs("div", { className: "memory-detail-metadata", children: [_jsx("h3", { className: "memory-detail-section-title", children: "\u5143\u6570\u636E" }), _jsxs("div", { className: "memory-detail-meta-grid", children: [domain && (_jsxs("div", { className: "memory-detail-meta-item", children: [_jsx("span", { className: "memory-detail-meta-label", children: "\u6765\u6E90\u57DF\u540D" }), _jsx("span", { className: "memory-detail-meta-value", children: domain })] })), _jsxs("div", { className: "memory-detail-meta-item", children: [_jsx("span", { className: "memory-detail-meta-label", children: "\u521B\u5EFA\u65F6\u95F4" }), _jsx("span", { className: "memory-detail-meta-value", children: formatDate(memory.metadata.timestamp || memory.created_at || '') })] }), _jsxs("div", { className: "memory-detail-meta-item", children: [_jsx("span", { className: "memory-detail-meta-label", children: "\u8BB0\u5FC6 ID" }), _jsx("span", { className: "memory-detail-meta-value memory-id", children: memory.memory_id })] }), _jsxs("div", { className: "memory-detail-meta-item", children: [_jsx("span", { className: "memory-detail-meta-label", children: "\u7528\u6237 ID" }), _jsx("span", { className: "memory-detail-meta-value memory-id", children: memory.user_id })] })] })] }), _jsxs("div", { className: "memory-detail-actions", children: [memory.metadata.url && (_jsx("button", { className: "memory-detail-btn memory-detail-btn-primary", onClick: handleOpenInNewTab, children: "\uD83D\uDD17 \u5728\u65B0\u6807\u7B7E\u9875\u6253\u5F00" })), _jsx("button", { className: "memory-detail-btn memory-detail-btn-secondary", onClick: onClose, children: "\u5173\u95ED" })] })] }) }));
}

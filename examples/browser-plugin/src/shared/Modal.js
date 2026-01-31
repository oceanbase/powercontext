import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect } from 'react';
import './Modal.css';
/**
 * 通用模态框组件
 * 支持遮罩层点击关闭、ESC 键关闭
 *
 * @param props - 组件属性
 * @returns React 组件
 */
export function Modal({ isOpen, onClose, title, children, size = 'medium' }) {
    // 监听 ESC 键
    useEffect(() => {
        if (!isOpen)
            return;
        const handleEscape = (e) => {
            if (e.key === 'Escape') {
                onClose();
            }
        };
        document.addEventListener('keydown', handleEscape);
        return () => {
            document.removeEventListener('keydown', handleEscape);
        };
    }, [isOpen, onClose]);
    // 禁用背景滚动
    useEffect(() => {
        if (isOpen) {
            document.body.style.overflow = 'hidden';
        }
        else {
            document.body.style.overflow = '';
        }
        return () => {
            document.body.style.overflow = '';
        };
    }, [isOpen]);
    if (!isOpen) {
        return null;
    }
    return (_jsx("div", { className: "modal-overlay", onClick: onClose, children: _jsxs("div", { className: `modal-content modal-${size}`, onClick: (e) => e.stopPropagation(), children: [title && (_jsxs("div", { className: "modal-header", children: [_jsx("h2", { className: "modal-title", children: title }), _jsx("button", { className: "modal-close-btn", onClick: onClose, "aria-label": "\u5173\u95ED", children: "\u2715" })] })), _jsx("div", { className: "modal-body", children: children })] }) }));
}

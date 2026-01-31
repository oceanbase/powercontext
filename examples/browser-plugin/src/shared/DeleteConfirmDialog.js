import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useState } from 'react';
import { Modal } from './Modal';
import './DeleteConfirmDialog.css';
/**
 * 删除确认对话框组件
 *
 * @param props - 组件属性
 * @returns React 组件
 */
export function DeleteConfirmDialog(props) {
    const { isOpen, title = 'Confirm Delete', message, onConfirm, onCancel, showDontAskAgain = false, } = props;
    const [dontAskAgain, setDontAskAgain] = useState(false);
    /**
     * 处理确认
     */
    const handleConfirm = async () => {
        // 保存"不再提示"设置
        if (dontAskAgain) {
            await chrome.storage.local.set({ skipDeleteConfirm: true });
        }
        onConfirm();
    };
    /**
     * 处理取消
     */
    const handleCancel = () => {
        setDontAskAgain(false);
        onCancel();
    };
    return (_jsx(Modal, { isOpen: isOpen, onClose: handleCancel, children: _jsxs("div", { className: "delete-confirm-dialog", children: [_jsxs("div", { className: "delete-confirm-header", children: [_jsx("div", { className: "delete-confirm-icon", children: "\u26A0\uFE0F" }), _jsx("h2", { className: "delete-confirm-title", children: title })] }), _jsxs("div", { className: "delete-confirm-content", children: [_jsx("p", { className: "delete-confirm-message", children: message }), showDontAskAgain && (_jsxs("label", { className: "delete-confirm-checkbox", children: [_jsx("input", { type: "checkbox", checked: dontAskAgain, onChange: (e) => setDontAskAgain(e.target.checked) }), _jsx("span", { children: "Don't ask again" })] }))] }), _jsxs("div", { className: "delete-confirm-actions", children: [_jsx("button", { type: "button", className: "btn btn-secondary", onClick: handleCancel, children: "Cancel" }), _jsx("button", { type: "button", className: "btn btn-danger", onClick: handleConfirm, children: "Delete" })] })] }) }));
}

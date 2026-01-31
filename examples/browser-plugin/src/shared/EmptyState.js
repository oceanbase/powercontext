import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import './EmptyState.css';
/**
 * 空状态组件
 * 当列表为空时显示
 *
 * @param props - 组件属性
 * @returns React 组件
 */
export function EmptyState({ icon = '📭', title, description, action }) {
    return (_jsxs("div", { className: "empty-state", children: [_jsx("div", { className: "empty-state-icon", children: icon }), _jsx("h3", { className: "empty-state-title", children: title }), description && (_jsx("p", { className: "empty-state-description", children: description })), action && (_jsx("button", { className: "empty-state-action", onClick: action.onClick, children: action.label }))] }));
}

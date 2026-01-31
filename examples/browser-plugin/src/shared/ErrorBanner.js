import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import './ErrorBanner.css';
/**
 * 错误提示横幅组件
 * 显示错误信息和操作按钮
 *
 * @param props - 组件属性
 * @returns React 组件
 */
export function ErrorBanner({ message, onRetry, onDismiss }) {
    return (_jsxs("div", { className: "error-banner", children: [_jsxs("div", { className: "error-banner-content", children: [_jsx("span", { className: "error-banner-icon", children: "\u26A0\uFE0F" }), _jsx("span", { className: "error-banner-message", children: message })] }), _jsxs("div", { className: "error-banner-actions", children: [onRetry && (_jsx("button", { className: "error-banner-btn error-banner-retry", onClick: onRetry, children: "\u91CD\u8BD5" })), onDismiss && (_jsx("button", { className: "error-banner-btn error-banner-dismiss", onClick: onDismiss, "aria-label": "\u5173\u95ED", children: "\u2715" }))] })] }));
}

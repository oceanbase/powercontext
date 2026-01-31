import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import './LoadingSpinner.css';
/**
 * 加载动画组件
 * 显示旋转的加载指示器
 *
 * @param props - 组件属性
 * @returns React 组件
 */
export function LoadingSpinner({ text, size = 'medium' }) {
    return (_jsxs("div", { className: "loading-spinner-container", children: [_jsx("div", { className: `loading-spinner loading-spinner-${size}`, children: _jsx("div", { className: "spinner" }) }), text && _jsx("p", { className: "loading-text", children: text })] }));
}

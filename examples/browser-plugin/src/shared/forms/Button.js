import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import './Button.css';
/**
 * 按钮组件
 */
export function Button({ children, onClick, variant = 'primary', size = 'medium', disabled = false, loading = false, icon, fullWidth = false }) {
    const classNames = [
        'button',
        `button-${variant}`,
        `button-${size}`,
        fullWidth ? 'button-full' : '',
        loading ? 'button-loading' : ''
    ].filter(Boolean).join(' ');
    return (_jsxs("button", { className: classNames, onClick: onClick, disabled: disabled || loading, children: [loading && _jsx("span", { className: "button-spinner" }), icon && !loading && _jsx("span", { className: "button-icon", children: icon }), children] }));
}

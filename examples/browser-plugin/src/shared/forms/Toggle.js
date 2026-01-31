import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import './Toggle.css';
/**
 * 开关组件
 * iOS 风格的切换开关
 */
export function Toggle({ label, checked, onChange, description, disabled = false }) {
    const handleChange = (e) => {
        onChange(e.target.checked);
    };
    return (_jsx("div", { className: "toggle-container", children: _jsxs("div", { className: "toggle-content", children: [_jsxs("div", { children: [label && _jsx("div", { className: "toggle-label", children: label }), description && _jsx("div", { className: "toggle-description", children: description })] }), _jsxs("label", { className: "toggle-switch", children: [_jsx("input", { type: "checkbox", checked: checked, onChange: handleChange, disabled: disabled }), _jsx("span", { className: "toggle-slider" })] })] }) }));
}

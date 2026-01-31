import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import './Input.css';
/**
 * 输入框组件
 * 统一的表单输入样式
 */
export function Input({ type = 'text', label, placeholder, value, onChange, error, helpText, required = false, disabled = false }) {
    const handleChange = (e) => {
        const newValue = type === 'number' ? parseFloat(e.target.value) : e.target.value;
        onChange(newValue);
    };
    return (_jsxs("div", { className: "input-container", children: [label && (_jsxs("label", { className: "input-label", children: [label, required && _jsx("span", { className: "input-required", children: "*" })] })), _jsx("input", { type: type, className: `input-field ${error ? 'input-error' : ''}`, placeholder: placeholder, value: value, onChange: handleChange, disabled: disabled, required: required }), error && _jsx("div", { className: "input-error-text", children: error }), helpText && !error && _jsx("div", { className: "input-help-text", children: helpText })] }));
}

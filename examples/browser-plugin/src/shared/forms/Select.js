import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import './Select.css';
/**
 * 下拉选择组件
 */
export function Select({ label, value, options, onChange, placeholder, disabled = false }) {
    const handleChange = (e) => {
        onChange(e.target.value);
    };
    return (_jsxs("div", { className: "select-container", children: [label && _jsx("label", { className: "select-label", children: label }), _jsxs("select", { className: "select-field", value: value, onChange: handleChange, disabled: disabled, children: [placeholder && (_jsx("option", { value: "", disabled: true, children: placeholder })), options.map((option) => (_jsx("option", { value: option.value, children: option.icon ? `${option.icon} ${option.label}` : option.label }, option.value)))] })] }));
}

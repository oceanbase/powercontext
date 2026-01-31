import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import './Slider.css';
/**
 * 滑块组件
 * 数值范围选择器
 */
export function Slider({ label, value, min, max, step = 1, onChange, formatValue, helpText }) {
    const handleChange = (e) => {
        onChange(parseFloat(e.target.value));
    };
    const displayValue = formatValue ? formatValue(value) : value.toString();
    return (_jsxs("div", { className: "slider-container", children: [label && (_jsxs("div", { className: "slider-header", children: [_jsx("label", { className: "slider-label", children: label }), _jsx("span", { className: "slider-value", children: displayValue })] })), _jsx("input", { type: "range", className: "slider-input", min: min, max: max, step: step, value: value, onChange: handleChange }), _jsxs("div", { className: "slider-range", children: [_jsx("span", { children: min }), _jsx("span", { children: max })] }), helpText && _jsx("div", { className: "slider-help-text", children: helpText })] }));
}

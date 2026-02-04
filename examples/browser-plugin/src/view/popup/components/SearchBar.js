import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useState, useCallback } from 'react';
import { debounce } from '@/utils/helpers';
import './SearchBar.css';
/**
 * 搜索栏组件
 * 提供搜索输入和实时搜索功能
 *
 * @param props - 组件属性
 * @returns React 组件
 */
export function SearchBar({ placeholder = '搜索记忆...', autoFocus = false, onSearch, onClear }) {
    const [inputValue, setInputValue] = useState('');
    // 创建防抖搜索函数
    const debouncedSearch = useCallback(debounce((query) => {
        if (onSearch) {
            onSearch(query);
        }
    }, 500), [onSearch]);
    /**
     * 处理输入变化
     */
    const handleInputChange = (event) => {
        const value = event.target.value;
        setInputValue(value);
        debouncedSearch(value);
    };
    /**
     * 处理表单提交
     */
    const handleSubmit = (event) => {
        event.preventDefault();
        if (onSearch) {
            onSearch(inputValue);
        }
    };
    /**
     * 清除搜索
     */
    const handleClear = () => {
        setInputValue('');
        if (onClear) {
            onClear();
        }
        if (onSearch) {
            onSearch('');
        }
    };
    return (_jsx("form", { className: "search-bar", onSubmit: handleSubmit, children: _jsxs("div", { className: "search-input-wrapper", children: [_jsx("span", { className: "search-icon", children: "\uD83D\uDD0D" }), _jsx("input", { type: "text", className: "search-input", placeholder: placeholder, value: inputValue, onChange: handleInputChange, autoFocus: autoFocus }), inputValue && (_jsx("button", { type: "button", className: "search-clear-btn", onClick: handleClear, "aria-label": "\u6E05\u9664", children: "\u2715" }))] }) }));
}

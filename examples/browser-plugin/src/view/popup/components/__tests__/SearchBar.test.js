import { jsx as _jsx } from "react/jsx-runtime";
import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { SearchBar } from '../SearchBar';
/**
 * SearchBar 组件测试套件
 */
describe('SearchBar Component', () => {
    let onSearchMock;
    let onClearMock;
    beforeEach(() => {
        onSearchMock = vi.fn();
        onClearMock = vi.fn();
    });
    /**
     * 测试：渲染
     */
    describe('Rendering', () => {
        test('should render search input with default placeholder', () => {
            render(_jsx(SearchBar, {}));
            const input = screen.getByPlaceholderText('搜索记忆...');
            expect(input).toBeInTheDocument();
        });
        test('should render with custom placeholder', () => {
            render(_jsx(SearchBar, { placeholder: "Search memories" }));
            const input = screen.getByPlaceholderText('Search memories');
            expect(input).toBeInTheDocument();
        });
        test('should autofocus when autoFocus is true', () => {
            render(_jsx(SearchBar, { autoFocus: true }));
            const input = screen.getByPlaceholderText('搜索记忆...');
            expect(input).toHaveFocus();
        });
        test('should not show clear button initially', () => {
            render(_jsx(SearchBar, {}));
            const clearButton = screen.queryByLabelText('清除');
            expect(clearButton).not.toBeInTheDocument();
        });
    });
    /**
     * 测试：输入交互
     */
    describe('Input Interaction', () => {
        test('should update input value on change', async () => {
            const user = userEvent.setup();
            render(_jsx(SearchBar, {}));
            const input = screen.getByPlaceholderText('搜索记忆...');
            await user.type(input, 'test query');
            expect(input.value).toBe('test query');
        });
        test('should show clear button when input has value', async () => {
            const user = userEvent.setup();
            render(_jsx(SearchBar, {}));
            const input = screen.getByPlaceholderText('搜索记忆...');
            await user.type(input, 'test');
            const clearButton = screen.getByLabelText('清除');
            expect(clearButton).toBeInTheDocument();
        });
        test('should trigger debounced search on input', async () => {
            const user = userEvent.setup();
            render(_jsx(SearchBar, { onSearch: onSearchMock }));
            const input = screen.getByPlaceholderText('搜索记忆...');
            await user.type(input, 'test query');
            // 等待防抖时间（500ms）
            await waitFor(() => {
                expect(onSearchMock).toHaveBeenCalledWith('test query');
            }, { timeout: 600 });
        });
        test('should not call onSearch if not provided', async () => {
            const user = userEvent.setup();
            render(_jsx(SearchBar, {}));
            const input = screen.getByPlaceholderText('搜索记忆...');
            // 不应该抛出错误
            await expect(user.type(input, 'test')).resolves.not.toThrow();
        });
    });
    /**
     * 测试：清除功能
     */
    describe('Clear Functionality', () => {
        test('should clear input on clear button click', async () => {
            const user = userEvent.setup();
            render(_jsx(SearchBar, { onClear: onClearMock, onSearch: onSearchMock }));
            const input = screen.getByPlaceholderText('搜索记忆...');
            await user.type(input, 'test');
            expect(input.value).toBe('test');
            const clearButton = screen.getByLabelText('清除');
            await user.click(clearButton);
            expect(input.value).toBe('');
        });
        test('should call onClear callback when clearing', async () => {
            const user = userEvent.setup();
            render(_jsx(SearchBar, { onClear: onClearMock }));
            const input = screen.getByPlaceholderText('搜索记忆...');
            await user.type(input, 'test');
            const clearButton = screen.getByLabelText('清除');
            await user.click(clearButton);
            expect(onClearMock).toHaveBeenCalled();
        });
        test('should call onSearch with empty string when clearing', async () => {
            const user = userEvent.setup();
            render(_jsx(SearchBar, { onSearch: onSearchMock }));
            const input = screen.getByPlaceholderText('搜索记忆...');
            await user.type(input, 'test');
            const clearButton = screen.getByLabelText('清除');
            await user.click(clearButton);
            expect(onSearchMock).toHaveBeenCalledWith('');
        });
        test('should hide clear button after clearing', async () => {
            const user = userEvent.setup();
            render(_jsx(SearchBar, {}));
            const input = screen.getByPlaceholderText('搜索记忆...');
            await user.type(input, 'test');
            let clearButton = screen.getByLabelText('清除');
            expect(clearButton).toBeInTheDocument();
            await user.click(clearButton);
            clearButton = screen.queryByLabelText('清除');
            expect(clearButton).not.toBeInTheDocument();
        });
    });
    /**
     * 测试：表单提交
     */
    describe('Form Submission', () => {
        test('should call onSearch on form submit', async () => {
            const user = userEvent.setup();
            render(_jsx(SearchBar, { onSearch: onSearchMock }));
            const input = screen.getByPlaceholderText('搜索记忆...');
            await user.type(input, 'test query');
            const form = input.closest('form');
            fireEvent.submit(form);
            expect(onSearchMock).toHaveBeenCalledWith('test query');
        });
        test('should prevent default form submission', async () => {
            const user = userEvent.setup();
            render(_jsx(SearchBar, { onSearch: onSearchMock }));
            const input = screen.getByPlaceholderText('搜索记忆...');
            await user.type(input, 'test');
            const form = input.closest('form');
            const submitEvent = new Event('submit', { bubbles: true, cancelable: true });
            const preventDefaultSpy = vi.spyOn(submitEvent, 'preventDefault');
            form.dispatchEvent(submitEvent);
            expect(preventDefaultSpy).toHaveBeenCalled();
        });
        test('should not throw error if onSearch not provided on submit', async () => {
            const user = userEvent.setup();
            render(_jsx(SearchBar, {}));
            const input = screen.getByPlaceholderText('搜索记忆...');
            await user.type(input, 'test');
            const form = input.closest('form');
            expect(() => fireEvent.submit(form)).not.toThrow();
        });
    });
    /**
     * 测试：可访问性
     */
    describe('Accessibility', () => {
        test('should have proper aria-label on clear button', async () => {
            const user = userEvent.setup();
            render(_jsx(SearchBar, {}));
            const input = screen.getByPlaceholderText('搜索记忆...');
            await user.type(input, 'test');
            const clearButton = screen.getByLabelText('清除');
            expect(clearButton).toHaveAttribute('aria-label', '清除');
        });
        test('should have proper input type', () => {
            render(_jsx(SearchBar, {}));
            const input = screen.getByPlaceholderText('搜索记忆...');
            expect(input).toHaveAttribute('type', 'text');
        });
    });
});

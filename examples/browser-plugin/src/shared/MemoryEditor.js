import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useState, useEffect } from 'react';
import { Modal } from './Modal';
import './MemoryEditor.css';
/**
 * 记忆编辑器组件
 * 允许用户修改记忆内容和元数据
 *
 * @param props - 组件属性
 * @returns React 组件
 */
export function MemoryEditor(props) {
    const { memory, isOpen, onClose, onSave } = props;
    // 表单状态
    const [formState, setFormState] = useState({
        content: '',
        title: '',
        category: '',
        tags: [],
    });
    // 新标签输入
    const [newTag, setNewTag] = useState('');
    // 保存状态
    const [isSaving, setIsSaving] = useState(false);
    const [error, setError] = useState(null);
    // 初始化表单数据
    useEffect(() => {
        if (isOpen && memory) {
            setFormState({
                content: memory.content || '',
                title: memory.metadata?.title || '',
                category: memory.filters?.category || '',
                tags: [], // TODO: 从 metadata 中提取标签
            });
            setError(null);
        }
    }, [isOpen, memory]);
    /**
     * 处理字段变化
     */
    const handleFieldChange = (field, value) => {
        setFormState(prev => ({
            ...prev,
            [field]: value,
        }));
    };
    /**
     * 添加标签
     */
    const handleAddTag = () => {
        const tag = newTag.trim();
        if (tag && !formState.tags.includes(tag)) {
            setFormState(prev => ({
                ...prev,
                tags: [...prev.tags, tag],
            }));
            setNewTag('');
        }
    };
    /**
     * 移除标签
     */
    const handleRemoveTag = (tag) => {
        setFormState(prev => ({
            ...prev,
            tags: prev.tags.filter(t => t !== tag),
        }));
    };
    /**
     * 验证表单
     */
    const validateForm = () => {
        const errors = [];
        if (!formState.content.trim()) {
            errors.push('Content cannot be empty');
        }
        if (formState.content.length > 10000) {
            errors.push('Content is too long (max 10000 characters)');
        }
        return {
            isValid: errors.length === 0,
            errors,
        };
    };
    /**
     * 保存修改
     */
    const handleSave = async () => {
        // 验证表单
        const validation = validateForm();
        if (!validation.isValid) {
            setError(validation.errors.join('. '));
            return;
        }
        setIsSaving(true);
        setError(null);
        try {
            // 构建更新后的记忆对象
            const updatedMemory = {
                ...memory,
                content: formState.content,
                metadata: {
                    ...memory.metadata,
                    title: formState.title,
                },
                filters: {
                    ...(memory.filters || {}),
                    category: formState.category,
                },
            };
            // 调用保存回调
            await onSave(updatedMemory);
            // 关闭对话框
            onClose();
        }
        catch (error) {
            console.error('[MemoryEditor] Failed to save:', error);
            setError(error instanceof Error ? error.message : 'Failed to save memory');
        }
        finally {
            setIsSaving(false);
        }
    };
    /**
     * 取消编辑
     */
    const handleCancel = () => {
        if (!isSaving) {
            onClose();
        }
    };
    /**
     * 处理键盘事件
     */
    const handleKeyDown = (e) => {
        // Ctrl+Enter 保存
        if (e.ctrlKey && e.key === 'Enter') {
            e.preventDefault();
            handleSave();
        }
    };
    return (_jsx(Modal, { isOpen: isOpen, onClose: handleCancel, children: _jsxs("div", { className: "memory-editor", onKeyDown: handleKeyDown, children: [_jsx("div", { className: "memory-editor-header", children: _jsx("h2", { className: "memory-editor-title", children: "Edit Memory" }) }), _jsxs("div", { className: "memory-editor-content", children: [error && (_jsx("div", { className: "memory-editor-error", children: error })), _jsxs("div", { className: "memory-editor-field", children: [_jsx("label", { htmlFor: "title", className: "memory-editor-label", children: "Title" }), _jsx("input", { id: "title", type: "text", className: "memory-editor-input", value: formState.title, onChange: (e) => handleFieldChange('title', e.target.value), placeholder: "Enter title..." })] }), _jsxs("div", { className: "memory-editor-field", children: [_jsx("label", { htmlFor: "content", className: "memory-editor-label", children: "Content *" }), _jsx("textarea", { id: "content", className: "memory-editor-textarea", value: formState.content, onChange: (e) => handleFieldChange('content', e.target.value), placeholder: "Enter content...", rows: 8 }), _jsxs("div", { className: "memory-editor-hint", children: [formState.content.length, " / 10000 characters"] })] }), _jsxs("div", { className: "memory-editor-field", children: [_jsx("label", { htmlFor: "category", className: "memory-editor-label", children: "Category" }), _jsx("input", { id: "category", type: "text", className: "memory-editor-input", value: formState.category, onChange: (e) => handleFieldChange('category', e.target.value), placeholder: "Enter category..." })] }), _jsxs("div", { className: "memory-editor-field", children: [_jsx("label", { className: "memory-editor-label", children: "Tags" }), _jsx("div", { className: "memory-editor-tags", children: formState.tags.map(tag => (_jsxs("span", { className: "memory-editor-tag", children: [tag, _jsx("button", { type: "button", className: "memory-editor-tag-remove", onClick: () => handleRemoveTag(tag), children: "\u00D7" })] }, tag))) }), _jsxs("div", { className: "memory-editor-tag-input", children: [_jsx("input", { type: "text", className: "memory-editor-input", value: newTag, onChange: (e) => setNewTag(e.target.value), onKeyPress: (e) => {
                                                if (e.key === 'Enter') {
                                                    e.preventDefault();
                                                    handleAddTag();
                                                }
                                            }, placeholder: "Add tag..." }), _jsx("button", { type: "button", className: "btn btn-secondary", onClick: handleAddTag, disabled: !newTag.trim(), children: "Add" })] })] })] }), _jsxs("div", { className: "memory-editor-actions", children: [_jsx("button", { type: "button", className: "btn btn-secondary", onClick: handleCancel, disabled: isSaving, children: "Cancel" }), _jsx("button", { type: "button", className: "btn btn-primary", onClick: handleSave, disabled: isSaving, children: isSaving ? 'Saving...' : 'Save' })] }), _jsx("div", { className: "memory-editor-hint", children: "Tip: Press Ctrl+Enter to save" })] }) }));
}

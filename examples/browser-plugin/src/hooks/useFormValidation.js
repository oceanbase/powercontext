import { useState, useCallback } from 'react';
/**
 * 表单验证 Hook
 * @param initialValues - 初始值
 * @param validationRules - 验证规则
 * @returns 表单状态和方法
 */
export function useFormValidation(initialValues, validationRules) {
    const [values, setValues] = useState(initialValues);
    const [errors, setErrors] = useState({});
    const [isSubmitting, setIsSubmitting] = useState(false);
    const [touched, setTouched] = useState({});
    /**
     * 设置字段值
     */
    const setFieldValue = useCallback((field, value) => {
        setValues(prev => ({ ...prev, [field]: value }));
        // 清除该字段的错误
        setErrors(prev => {
            const newErrors = { ...prev };
            delete newErrors[field];
            return newErrors;
        });
    }, []);
    /**
     * 验证字段
     */
    const validateField = useCallback((field) => {
        const rule = validationRules[field];
        if (!rule)
            return true;
        const result = rule(values[field]);
        if (!result.isValid && result.error) {
            setErrors(prev => ({ ...prev, [field]: result.error }));
            return false;
        }
        return true;
    }, [values, validationRules]);
    /**
     * 验证所有字段
     */
    const validateAll = useCallback(() => {
        const newErrors = {};
        let isValid = true;
        for (const field in validationRules) {
            const rule = validationRules[field];
            if (rule) {
                const result = rule(values[field]);
                if (!result.isValid && result.error) {
                    newErrors[field] = result.error;
                    isValid = false;
                }
            }
        }
        setErrors(newErrors);
        return isValid;
    }, [values, validationRules]);
    /**
     * 重置表单
     */
    const reset = useCallback(() => {
        setValues(initialValues);
        setErrors({});
        setTouched({});
        setIsSubmitting(false);
    }, [initialValues]);
    /**
     * 处理提交
     */
    const handleSubmit = useCallback(async (onSubmit) => {
        setIsSubmitting(true);
        try {
            const isValid = validateAll();
            if (!isValid) {
                setIsSubmitting(false);
                return;
            }
            await onSubmit(values);
        }
        catch (error) {
            console.error('Form submission error:', error);
        }
        finally {
            setIsSubmitting(false);
        }
    }, [values, validateAll]);
    /**
     * 标记字段为已触摸
     */
    const setFieldTouched = useCallback((field) => {
        setTouched(prev => ({ ...prev, [field]: true }));
    }, []);
    /**
     * 判断表单是否有效
     */
    const isValid = Object.keys(errors).length === 0;
    return {
        values,
        errors,
        isSubmitting,
        isValid,
        touched,
        setFieldValue,
        validateField,
        validateAll,
        reset,
        handleSubmit,
        setFieldTouched
    };
}

import { jsx as _jsx } from "react/jsx-runtime";
import React, { Suspense } from 'react';
import { LoadingSpinner } from '@/shared/LoadingSpinner';
/**
 * 懒加载组件 HOC
 * 用于优化打包体积，按需加载组件
 *
 * @param importFunc - 动态导入函数
 * @param fallback - 加载时的占位组件
 * @returns 懒加载包装后的组件
 *
 * @example
 * ```typescript
 * const MemoryEditor = lazyLoadComponent(
 *   () => import('@/shared/MemoryEditor')
 * );
 * ```
 */
export function lazyLoadComponent(importFunc, fallback) {
    const LazyComponent = React.lazy(importFunc);
    return (props) => (_jsx(Suspense, { fallback: fallback || _jsx(LoadingSpinner, {}), children: _jsx(LazyComponent, { ...props }) }));
}
/**
 * 预加载组件
 * 在用户可能需要之前提前加载组件
 *
 * @param importFunc - 动态导入函数
 *
 * @example
 * ```typescript
 * // 在鼠标悬停时预加载
 * onMouseEnter={() => preloadComponent(() => import('@/shared/MemoryEditor'))}
 * ```
 */
export function preloadComponent(importFunc) {
    importFunc();
}
/**
 * 延迟加载辅助函数
 * 添加最小延迟时间，避免闪烁
 *
 * @param importFunc - 动态导入函数
 * @param minDelay - 最小延迟时间（毫秒）
 * @returns 延迟后的导入函数
 */
export function withMinDelay(importFunc, minDelay = 200) {
    return async () => {
        const [component] = await Promise.all([
            importFunc(),
            new Promise(resolve => setTimeout(resolve, minDelay)),
        ]);
        return component;
    };
}

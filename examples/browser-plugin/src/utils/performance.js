/**
 * 防抖函数
 * @param fn - 目标函数
 * @param delay - 延迟时间(ms)
 * @returns 防抖后的函数
 */
export function debounce(fn, delay) {
    let timeoutId = null;
    return function (...args) {
        if (timeoutId) {
            clearTimeout(timeoutId);
        }
        timeoutId = setTimeout(() => {
            fn.apply(this, args);
            timeoutId = null;
        }, delay);
    };
}
/**
 * 节流函数
 * @param fn - 目标函数
 * @param interval - 时间间隔(ms)
 * @returns 节流后的函数
 */
export function throttle(fn, interval) {
    let lastTime = 0;
    let timeoutId = null;
    return function (...args) {
        const now = Date.now();
        if (now - lastTime >= interval) {
            lastTime = now;
            fn.apply(this, args);
        }
        else {
            if (timeoutId) {
                clearTimeout(timeoutId);
            }
            timeoutId = setTimeout(() => {
                lastTime = Date.now();
                fn.apply(this, args);
                timeoutId = null;
            }, interval - (now - lastTime));
        }
    };
}

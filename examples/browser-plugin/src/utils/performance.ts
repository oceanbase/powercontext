/**
 * 防抖函数
 * @param fn - 目标函数
 * @param delay - 延迟时间(ms)
 * @returns 防抖后的函数
 */
export function debounce<T extends (...args: any[]) => any>(
  fn: T,
  delay: number
): (...args: Parameters<T>) => void {
  let timeoutId: ReturnType<typeof setTimeout> | null = null;

  return function (this: any, ...args: Parameters<T>) {
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
export function throttle<T extends (...args: any[]) => any>(
  fn: T,
  interval: number
): (...args: Parameters<T>) => void {
  let lastTime = 0;
  let timeoutId: ReturnType<typeof setTimeout> | null = null;

  return function (this: any, ...args: Parameters<T>) {
    const now = Date.now();

    if (now - lastTime >= interval) {
      lastTime = now;
      fn.apply(this, args);
    } else {
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

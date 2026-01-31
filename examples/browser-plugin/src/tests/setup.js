import { expect, afterEach, vi, beforeAll } from 'vitest';
import { cleanup } from '@testing-library/react';
import '@testing-library/jest-dom';
/**
 * 全局测试设置
 * 配置测试环境和 Mock Chrome API
 */
// 每个测试后清理 DOM
afterEach(() => {
    cleanup();
});
// 测试开始前设置全局 Mocks
beforeAll(() => {
    // Mock Chrome API
    globalThis.chrome = {
        runtime: {
            sendMessage: vi.fn(),
            onMessage: {
                addListener: vi.fn(),
                removeListener: vi.fn(),
                hasListener: vi.fn(),
            },
            getURL: vi.fn((path) => `chrome-extension://test-id/${path}`),
            id: 'test-extension-id',
            lastError: undefined,
        },
        storage: {
            local: {
                get: vi.fn((keys, callback) => {
                    if (callback)
                        callback({});
                    return Promise.resolve({});
                }),
                set: vi.fn((items, callback) => {
                    if (callback)
                        callback();
                    return Promise.resolve();
                }),
                remove: vi.fn((keys, callback) => {
                    if (callback)
                        callback();
                    return Promise.resolve();
                }),
                clear: vi.fn((callback) => {
                    if (callback)
                        callback();
                    return Promise.resolve();
                }),
            },
            sync: {
                get: vi.fn((keys, callback) => {
                    if (callback)
                        callback({});
                    return Promise.resolve({});
                }),
                set: vi.fn((items, callback) => {
                    if (callback)
                        callback();
                    return Promise.resolve();
                }),
                remove: vi.fn((keys, callback) => {
                    if (callback)
                        callback();
                    return Promise.resolve();
                }),
                clear: vi.fn((callback) => {
                    if (callback)
                        callback();
                    return Promise.resolve();
                }),
            },
            onChanged: {
                addListener: vi.fn(),
                removeListener: vi.fn(),
                hasListener: vi.fn(),
            },
        },
        tabs: {
            query: vi.fn((queryInfo, callback) => {
                const tabs = [
                    {
                        id: 1,
                        url: 'https://example.com',
                        title: 'Example',
                        active: true,
                        windowId: 1,
                    },
                ];
                if (callback)
                    callback(tabs);
                return Promise.resolve(tabs);
            }),
            sendMessage: vi.fn((tabId, message, callback) => {
                if (callback)
                    callback({});
                return Promise.resolve({});
            }),
            create: vi.fn((createProperties, callback) => {
                const tab = { id: 2, ...createProperties };
                if (callback)
                    callback(tab);
                return Promise.resolve(tab);
            }),
            get: vi.fn((tabId, callback) => {
                const tab = { id: tabId, url: 'https://example.com', title: 'Example' };
                if (callback)
                    callback(tab);
                return Promise.resolve(tab);
            }),
        },
        contextMenus: {
            create: vi.fn(),
            update: vi.fn(),
            remove: vi.fn(),
            removeAll: vi.fn(),
            onClicked: {
                addListener: vi.fn(),
                removeListener: vi.fn(),
                hasListener: vi.fn(),
            },
        },
        commands: {
            onCommand: {
                addListener: vi.fn(),
                removeListener: vi.fn(),
                hasListener: vi.fn(),
            },
        },
        sidePanel: {
            open: vi.fn(),
            close: vi.fn(),
            setOptions: vi.fn(),
            getOptions: vi.fn(),
        },
        notifications: {
            create: vi.fn((notificationId, options, callback) => {
                if (callback)
                    callback(notificationId || 'test-notification-id');
                return Promise.resolve(notificationId || 'test-notification-id');
            }),
            clear: vi.fn((notificationId, callback) => {
                if (callback)
                    callback(true);
                return Promise.resolve(true);
            }),
        },
    };
    // Mock window.matchMedia
    Object.defineProperty(window, 'matchMedia', {
        writable: true,
        value: vi.fn().mockImplementation((query) => ({
            matches: false,
            media: query,
            onchange: null,
            addListener: vi.fn(),
            removeListener: vi.fn(),
            addEventListener: vi.fn(),
            removeEventListener: vi.fn(),
            dispatchEvent: vi.fn(),
        })),
    });
    // Mock IntersectionObserver
    globalThis.IntersectionObserver = class IntersectionObserver {
        constructor() { }
        disconnect() { }
        observe() { }
        takeRecords() {
            return [];
        }
        unobserve() { }
    };
    // Mock ResizeObserver
    globalThis.ResizeObserver = class ResizeObserver {
        constructor() { }
        disconnect() { }
        observe() { }
        unobserve() { }
    };
});
// 自定义匹配器
expect.extend({
    toBeWithinRange(received, floor, ceiling) {
        const pass = received >= floor && received <= ceiling;
        if (pass) {
            return {
                message: () => `expected ${received} not to be within range ${floor} - ${ceiling}`,
                pass: true,
            };
        }
        else {
            return {
                message: () => `expected ${received} to be within range ${floor} - ${ceiling}`,
                pass: false,
            };
        }
    },
});

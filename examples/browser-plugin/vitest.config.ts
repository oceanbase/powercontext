import { defineConfig } from 'vitest/config';
import { resolve } from 'path';

/**
 * Vitest 测试配置
 * 注意：测试环境不需要 React 插件，避免与 vite.config.ts 的插件冲突
 */
export default defineConfig({
  resolve: {
    alias: {
      '@': resolve(__dirname, './src'),
    },
  },
  
  test: {
    // 测试环境
    environment: 'jsdom',
    
    // 全局设置
    globals: true,
    
    // 覆盖率配置
    coverage: {
      provider: 'v8',
      reporter: ['text', 'json', 'html', 'lcov'],
      exclude: [
        'node_modules/',
        'dist/',
        '**/*.test.{ts,tsx}',
        '**/*.spec.{ts,tsx}',
        '**/*.config.{ts,js}',
        '**/tests/**',
        '**/__tests__/**',
      ],
      // 覆盖率阈值
      thresholds: {
        lines: 70,
        functions: 70,
        branches: 70,
        statements: 70,
      },
    },
    
    // Setup 文件
    setupFiles: ['./src/tests/setup.ts'],
    
    // 测试超时
    testTimeout: 10000,
    
    // 包含的测试文件
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
  },
});

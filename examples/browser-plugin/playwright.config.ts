import { defineConfig, devices } from '@playwright/test';

/**
 * Playwright E2E 测试配置
 * 用于浏览器扩展的端到端测试
 */
export default defineConfig({
  testDir: './tests/e2e',
  
  // 超时设置
  timeout: 30 * 1000,
  
  // 期望超时
  expect: {
    timeout: 5000,
  },
  
  // 失败重试策略
  retries: process.env.CI ? 2 : 0,
  
  // 并行worker数
  workers: process.env.CI ? 1 : undefined,
  
  // Reporter配置
  reporter: [
    ['html'],
    ['list'],
  ],
  
  // 共享设置
  use: {
    // 基础URL
    // baseURL: 'http://localhost:5173',
    
    // 失败时截图
    screenshot: 'only-on-failure',
    
    // 失败时录制视频
    video: 'retain-on-failure',
    
    // 追踪
    trace: 'on-first-retry',
  },
  
  // 项目配置
  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        channel: 'chrome',
        viewport: { width: 1280, height: 720 },
        
        // Chrome扩展特定配置
        // 注意：需要先构建扩展到dist目录
        // args: [
        //   `--disable-extensions-except=${process.cwd()}/dist`,
        //   `--load-extension=${process.cwd()}/dist`,
        // ],
      },
    },
  ],
  
  // Web服务器配置（如果需要）
  // webServer: {
  //   command: 'npm run dev',
  //   port: 5173,
  //   reuseExistingServer: !process.env.CI,
  // },
});

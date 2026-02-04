import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import webExtension from 'vite-plugin-web-extension';
import { resolve } from 'path';
import viteCompression from 'vite-plugin-compression';

/**
 * Vite 配置
 * 支持多入口构建和 Chrome 扩展打包
 * 生产环境优化：压缩、代码分割、依赖预构建
 */
export default defineConfig(({ mode }) => ({
  plugins: [
    react(),
    webExtension({
      manifest: resolve(__dirname, 'public/manifest.json'),
      additionalInputs: [
        'src/view/popup/index.html',
        'src/view/sidebar/index.html',
        'src/view/options/index.html',
      ],
    }),
    // 生产环境启用 gzip 压缩
    mode === 'production' && viteCompression({
      algorithm: 'gzip',
      ext: '.gz',
      threshold: 10240, // 只压缩大于 10KB 的文件
    }),
  ].filter(Boolean),
  
  resolve: {
    alias: {
      '@': resolve(__dirname, './src'),
    },
  },
  
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    minify: 'terser',
    
    // Terser 压缩选项
    terserOptions: {
      compress: {
        drop_console: mode === 'production',   // 生产环境移除 console.log
        drop_debugger: true,                    // 移除 debugger
        pure_funcs: mode === 'production' ? ['console.log', 'console.info'] : [],
      },
    },
    
    // Chunk 大小警告阈值（KB）
    chunkSizeWarningLimit: 500,
  },
  
  // 优化依赖预构建
  optimizeDeps: {
    include: ['react', 'react-dom', 'zustand', 'axios'],
  },
}));

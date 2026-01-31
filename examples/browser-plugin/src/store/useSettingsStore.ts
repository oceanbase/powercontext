import { create } from 'zustand';
import type { ExtensionSettings } from '../types/settings';
import { STORAGE_KEYS } from '../types/settings';

/**
 * 默认设置
 */
const DEFAULT_SETTINGS: ExtensionSettings = {
  server: {
    url: 'http://localhost:8000',
    apiKey: '',
    timeout: 30000
  },
  user: {
    userId: '',
    defaultAgentId: 'default-agent'
  },
  capture: {
    autoSave: false,
    minSelectionLength: 10,
    includePageContext: true
  },
  ui: {
    theme: 'auto',
    language: 'zh'
  },
  advanced: {
    enableContextualSuggestions: true,
    maxRecentMemories: 50
  }
};

/**
 * 设置状态接口
 */
interface SettingsState {
  /** 当前设置 */
  settings: ExtensionSettings | null;
  
  /** 是否已加载 */
  isLoaded: boolean;
  
  /** 是否正在加载 */
  isLoading: boolean;
  
  /** 错误信息 */
  error: string | null;
  
  /**
   * 加载设置
   */
  loadSettings: () => Promise<void>;
  
  /**
   * 更新设置
   * @param settings - 部分设置
   */
  updateSettings: (settings: Partial<ExtensionSettings>) => Promise<void>;
  
  /**
   * 重置为默认设置
   */
  resetSettings: () => Promise<void>;
}

/**
 * 使用设置状态 Hook
 */
export const useSettingsStore = create<SettingsState>((set, get) => ({
  settings: null,
  isLoaded: false,
  isLoading: false,
  error: null,
  
  loadSettings: async () => {
    set({ isLoading: true, error: null });
    
    try {
      const result = await chrome.storage.sync.get(STORAGE_KEYS.SETTINGS);
      const settings = result[STORAGE_KEYS.SETTINGS] || DEFAULT_SETTINGS;
      
      set({ 
        settings, 
        isLoaded: true, 
        isLoading: false 
      });
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : '加载设置失败';
      set({ 
        error: errorMessage, 
        isLoading: false 
      });
    }
  },
  
  updateSettings: async (partialSettings) => {
    const currentSettings = get().settings || DEFAULT_SETTINGS;
    
    // 深度合并设置
    const newSettings: ExtensionSettings = {
      server: { ...currentSettings.server, ...partialSettings.server },
      user: { ...currentSettings.user, ...partialSettings.user },
      capture: { ...currentSettings.capture, ...partialSettings.capture },
      ui: { ...currentSettings.ui, ...partialSettings.ui },
      advanced: { ...currentSettings.advanced, ...partialSettings.advanced }
    };
    
    try {
      await chrome.storage.sync.set({
        [STORAGE_KEYS.SETTINGS]: newSettings
      });
      
      set({ settings: newSettings });
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : '更新设置失败';
      set({ error: errorMessage });
      throw error;
    }
  },
  
  resetSettings: async () => {
    try {
      await chrome.storage.sync.set({
        [STORAGE_KEYS.SETTINGS]: DEFAULT_SETTINGS
      });
      
      set({ settings: DEFAULT_SETTINGS });
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : '重置设置失败';
      set({ error: errorMessage });
      throw error;
    }
  }
}));

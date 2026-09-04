import { defineI18n } from 'fumadocs-core/i18n';
import { defineI18nUI } from 'fumadocs-ui/i18n';

export const languages = ['en', 'zh'] as const;
export type Language = (typeof languages)[number];

export const i18n = defineI18n({
  defaultLanguage: 'en',
  languages: [...languages],
  parser: 'dir',
  hideLocale: 'never',
});

export const i18nUI = defineI18nUI(i18n, {
  en: { displayName: 'English' },
  zh: { displayName: '简体中文' },
});

export function isLanguage(value: string): value is Language {
  return languages.includes(value as Language);
}

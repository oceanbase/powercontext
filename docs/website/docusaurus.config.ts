import {themes as prismThemes} from 'prism-react-renderer';
import type {Config} from '@docusaurus/types';
import type * as Preset from '@docusaurus/preset-classic';

const config: Config = {
  title: 'PowerMem',
  tagline: 'Build Persistent Memory for AI Applications',
  favicon: 'img/favicon.svg',

  future: {
    v4: true,
  },

  url: 'https://oceanbase.github.io',
  baseUrl: '/',

  organizationName: 'oceanbase',
  projectName: 'powermem',

  onBrokenLinks: 'throw',
  onBrokenMarkdownLinks: 'warn',

  i18n: {
    defaultLocale: 'en',
    locales: ['en', 'zh'],
    localeConfigs: {
      en: {
        label: 'English',
        direction: 'ltr',
        htmlLang: 'en-US',
      },
      zh: {
        label: '中文',
        direction: 'ltr',
        htmlLang: 'zh-CN',
      },
    },
  },

  plugins: [],

  presets: [
    [
      'classic',
      {
        docs: {
          sidebarPath: './sidebars.ts',
          editUrl: undefined,
          routeBasePath: 'docs',
          sidebarItemsGenerator: require('./plugins/docusaurus-plugin-sort-docs'),
          remarkPlugins: [
            require('./plugins/remark-auto-overview'),
            require('./plugins/remark-normalize-doc-links'),
          ],
        },
        blog: false,
        theme: {
          customCss: './src/css/custom.css',
        },
      } satisfies Preset.Options,
    ],
  ],

  themeConfig: {
    image: 'img/powermem-social-card.jpg',
    colorMode: {
      respectPrefersColorScheme: false,
      defaultMode: 'dark',
      disableSwitch: true,
    },
    navbar: {
      title: 'PowerMem',
      logo: {
        alt: 'PowerMem Logo',
        src: 'img/logo.svg',
      },
      items: [
        {
          type: 'docSidebar',
          sidebarId: 'docsSidebar',
          position: 'left',
          label: 'Docs',
        },
        {
          to: '/features',
          label: 'Features',
          position: 'left',
        },
        {
          to: '/benchmark',
          label: 'Benchmark',
          position: 'left',
        },
        {
          to: '/community',
          label: 'Community',
          position: 'left',
        },
        {
          href: 'https://github.com/oceanbase/powermem',
          label: 'GitHub',
          position: 'right',
        },
        {
          href: 'https://discord.com/invite/74cF8vbNEs',
          label: 'Discord',
          position: 'right',
        },
        {
          href: 'https://x.com/OceanBaseDB',
          label: 'X',
          position: 'right',
        },
      ],
    },
    footer: {
      style: 'dark',
      links: [
        {
          title: 'Docs',
          items: [
            {
              label: 'Getting Started',
              to: '/docs/guides/getting_started',
            },
            {
              label: 'API Reference',
              to: '/docs/api/overview',
            },
            {
              label: 'Examples',
              to: '/docs/examples/overview',
            },
          ],
        },
        {
          title: 'Community',
          items: [
            {
              label: 'GitHub',
              href: 'https://github.com/oceanbase/powermem',
            },
            {
              label: 'Discord',
              href: 'https://discord.com/invite/74cF8vbNEs',
            },
            {
              label: 'X',
              href: 'https://x.com/OceanBaseDB',
            },
          ],
        },
        {
          title: 'More',
          items: [
            {
              label: 'Features',
              to: '/features',
            },
            {
              label: 'Benchmark',
              to: '/benchmark',
            },
            {
              label: 'Community',
              to: '/community',
            },
          ],
        },
      ],
      copyright: `Copyright © ${new Date().getFullYear()} OceanBase.`,
    },
    prism: {
      theme: prismThemes.github,
      darkTheme: prismThemes.dracula,
      additionalLanguages: ['python', 'bash', 'json'],
    },
  } satisfies Preset.ThemeConfig,
};

export default config;

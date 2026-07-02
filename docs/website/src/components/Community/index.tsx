import React from 'react';
import Link from '@docusaurus/Link';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import Heading from '@theme/Heading';
import GitHubIcon from './icons/GitHubIcon';
import DiscordIcon from './icons/DiscordIcon';
import XIcon from './icons/XIcon';
import {localizedPath} from '../../utils/localizedPath';
import styles from './styles.module.css';

const communityLinks = [
  {
    icon: GitHubIcon,
    name: 'GitHub',
    descKey: 'github.desc',
    href: 'https://github.com/oceanbase/powermem',
    color: 'gray',
  },
  {
    icon: DiscordIcon,
    name: 'Discord',
    descKey: 'discord.desc',
    href: 'https://discord.com/invite/74cF8vbNEs',
    color: 'indigo',
  },
  {
    icon: XIcon,
    name: 'X',
    descKey: 'x.desc',
    href: 'https://x.com/OceanBaseDB',
    color: 'sky',
  },
];

const translations: Record<string, Record<string, string>> = {
  en: {
    'community.title': 'Join the Community',
    'community.subtitle': 'Build better AI memory management systems with the developer community',
    'community.contribute': 'We welcome all forms of contributions!',
    'community.learnMore': 'Learn More →',
    'community.github.desc': 'View source code, submit issues and contribute',
    'community.discord.desc': 'Join our Discord server to chat, get help and share experiences',
    'community.x.desc': 'Follow latest updates and product news',
  },
  zh: {
    'community.title': '加入社区',
    'community.subtitle': '与开发者社区一起构建更好的 AI 记忆管理系统',
    'community.contribute': '我们欢迎所有形式的贡献！',
    'community.learnMore': '了解更多 →',
    'community.github.desc': '查看源代码、提交 Issue 和贡献代码',
    'community.discord.desc': '加入我们的 Discord 服务器，聊天、获取帮助和分享经验',
    'community.x.desc': '关注最新动态和产品更新',
  },
};

export default function Community() {
  const { i18n } = useDocusaurusContext();
  const isZh = i18n.currentLocale === 'zh';
  const t = (key: string) => {
    const lang = isZh ? 'zh' : 'en';
    return translations[lang][key] || key;
  };

  return (
    <section className={styles.community}>
      <div className="container">
        <div className={styles.header}>
          <Heading as="h2" className={styles.title}>
            {t('community.title')}
          </Heading>
          <p className={styles.subtitle}>
            {t('community.subtitle')}
          </p>
        </div>

        <div className={styles.grid}>
          {communityLinks.map((link, index) => {
            const Icon = link.icon;
            return (
              <a
                key={link.name}
                href={link.href}
                target="_blank"
                rel="noopener noreferrer"
                className={`${styles.card} ${styles[`card-${link.color}`]} fade-in-delay-${index + 1}`}
              >
                <div className={styles.icon}>
                  <Icon className={styles.iconSvg} />
                </div>
                <Heading as="h3" className={styles.cardTitle}>
                  {link.name}
                </Heading>
                <p className={styles.cardDesc}>{t(`community.${link.descKey}`)}</p>
              </a>
            );
          })}
        </div>

        <div className={styles.footer}>
          <p className={styles.footerText}>
            {t('community.contribute')}
          </p>
          <Link
            to={localizedPath('/community', isZh)}
            className={styles.footerLink}
          >
            {t('community.learnMore')}
          </Link>
        </div>
      </div>
    </section>
  );
}

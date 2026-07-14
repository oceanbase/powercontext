import React from 'react';
import Layout from '@theme/Layout';
import Heading from '@theme/Heading';
import Link from '@docusaurus/Link';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import GitHubIcon from '../components/Community/icons/GitHubIcon';
import DiscordIcon from '../components/Community/icons/DiscordIcon';
import XIcon from '../components/Community/icons/XIcon';
import styles from './community.module.css';

const communityLinks = [
  {
    icon: GitHubIcon,
    name: 'GitHub',
    descKey: 'community.github.desc',
    actionKey: 'communityPage.github.action',
    href: 'https://github.com/oceanbase/powermem',
  },
  {
    icon: DiscordIcon,
    name: 'Discord',
    descKey: 'community.discord.desc',
    actionKey: 'communityPage.discord.action',
    href: 'https://discord.com/invite/74cF8vbNEs',
  },
  {
    icon: XIcon,
    name: 'X',
    descKey: 'community.x.desc',
    actionKey: 'communityPage.x.action',
    href: 'https://x.com/OceanBaseDB',
  },
];

const translations: Record<string, Record<string, string>> = {
  en: {
    'common.back': 'Back',
    'communityPage.title': 'Community',
    'communityPage.subtitle': 'Build better AI memory management systems with the developer community',
    'communityPage.description': 'Join the PowerMem Community',
    'communityPage.github.action': 'Star Us',
    'communityPage.discord.action': 'Join Discord',
    'communityPage.x.action': 'Follow Us',
    'community.github.desc': 'View source code, submit issues and contribute',
    'community.discord.desc': 'Join our Discord server to chat, get help and share experiences',
    'community.x.desc': 'Follow latest updates and product news',
    'communityPage.contributing.title': 'Contributing',
    'communityPage.contributing.desc': "We welcome all forms of contributions! Whether it's code, documentation, issue reports, or feature suggestions, your participation makes PowerMem better.",
    'communityPage.contributing.item1': 'Submit Issues to report problems or suggest new features',
    'communityPage.contributing.item2': 'Submit Pull Requests to contribute code',
    'communityPage.contributing.item3': 'Improve documentation and examples',
    'communityPage.contributing.item4': 'Share usage experiences and best practices',
    'communityPage.contributing.viewGuide': 'View Contribution Guide',
    'communityPage.codeOfConduct.title': 'Code of Conduct',
    'communityPage.codeOfConduct.desc': 'The PowerMem community is committed to providing a friendly, inclusive, and respectful environment for all participants. We expect all community members to follow these principles:',
    'communityPage.codeOfConduct.item1': 'Respect all community members regardless of background',
    'communityPage.codeOfConduct.item2': 'Maintain professional and courteous communication',
    'communityPage.codeOfConduct.item3': 'Welcome different perspectives and experiences',
    'communityPage.codeOfConduct.item4': 'Focus on constructive feedback and discussion',
  },
  zh: {
    'common.back': '返回',
    'communityPage.title': '社区',
    'communityPage.subtitle': '与开发者社区一起构建更好的 AI 记忆管理系统',
    'communityPage.description': '加入 PowerMem 社区',
    'communityPage.github.action': 'Star 我们',
    'communityPage.discord.action': '加入 Discord',
    'communityPage.x.action': '关注我们',
    'community.github.desc': '查看源代码、提交 Issue 和贡献代码',
    'community.discord.desc': '加入我们的 Discord 服务器，聊天、获取帮助和分享经验',
    'community.x.desc': '关注最新动态和产品更新',
    'communityPage.contributing.title': '贡献',
    'communityPage.contributing.desc': '我们欢迎所有形式的贡献！无论是代码、文档、问题报告还是功能建议，您的参与都会让 PowerMem 变得更好。',
    'communityPage.contributing.item1': '提交 Issue 来报告问题或建议新功能',
    'communityPage.contributing.item2': '提交 Pull Request 来贡献代码',
    'communityPage.contributing.item3': '改进文档和示例',
    'communityPage.contributing.item4': '分享使用经验和最佳实践',
    'communityPage.contributing.viewGuide': '查看贡献指南',
    'communityPage.codeOfConduct.title': '行为准则',
    'communityPage.codeOfConduct.desc': 'PowerMem 社区致力于为所有参与者提供友好、包容和尊重的环境。我们期望所有社区成员遵循以下原则：',
    'communityPage.codeOfConduct.item1': '尊重所有社区成员，无论背景如何',
    'communityPage.codeOfConduct.item2': '保持专业和礼貌的沟通',
    'communityPage.codeOfConduct.item3': '欢迎不同的观点和经验',
    'communityPage.codeOfConduct.item4': '专注于建设性的反馈和讨论',
  },
};

export default function CommunityPage() {
  const { i18n } = useDocusaurusContext();
  const isZh = i18n.currentLocale === 'zh';
  const t = (key: string) => translations[isZh ? 'zh' : 'en'][key] || key;
  const newTabLabel = isZh
    ? '（在新标签页中打开）'
    : ' (opens in a new tab)';

  return (
    <Layout
      title={t('communityPage.title')}
      description={t('communityPage.description')}
    >
      <main className={styles.communityPage}>
        <header className={styles.hero}>
          <div
            className={`container ${styles.pageContainer} ${styles.heroGrid}`}
          >
            <Heading as="h1" className={styles.title}>
              {t('communityPage.title')}
            </Heading>
            <p className={styles.subtitle}>{t('communityPage.subtitle')}</p>
          </div>
        </header>

        <nav
          className={styles.channelsSection}
          aria-label={t('communityPage.title')}
        >
          <div className={`container ${styles.pageContainer}`}>
            <div className={styles.communityGrid}>
              {communityLinks.map((link) => {
                const Icon = link.icon;
                return (
                  <a
                    key={link.name}
                    href={link.href}
                    target="_blank"
                    rel="noopener noreferrer"
                    className={styles.communityCard}
                  >
                    <span className={styles.cardIconSlot} aria-hidden="true">
                      <Icon className={styles.cardIcon} />
                    </span>
                    <div className={styles.cardBody}>
                      <Heading as="h2" className={styles.cardTitle}>
                        {link.name}
                      </Heading>
                      <p className={styles.cardDesc}>{t(link.descKey)}</p>
                    </div>
                    <span className={styles.cardAction}>
                      {t(link.actionKey)}
                      <span className={styles.cardArrow} aria-hidden="true">
                        ↗
                      </span>
                      <span className={styles.srOnly}>{newTabLabel}</span>
                    </span>
                  </a>
                );
              })}
            </div>
          </div>
        </nav>

        <section className={styles.contributeSection}>
          <div className={`container ${styles.pageContainer}`}>
            <div className={styles.contributeGrid}>
              <div className={styles.contributeIntro}>
                <Heading as="h2" className={styles.sectionTitle}>
                  {t('communityPage.contributing.title')}
                </Heading>
                <p className={styles.sectionDesc}>
                  {t('communityPage.contributing.desc')}
                </p>
                <Link
                  href="https://github.com/oceanbase/powermem"
                  className={styles.guideLink}
                >
                  <GitHubIcon className={styles.buttonIcon} />
                  <span>{t('communityPage.contributing.viewGuide')}</span>
                  <span className={styles.guideArrow} aria-hidden="true">
                    →
                  </span>
                </Link>
              </div>

              <ul className={styles.detailList}>
                {[1, 2, 3, 4].map((number) => (
                  <li key={number}>
                    <span className={styles.detailMark} aria-hidden="true" />
                    <span>{t(`communityPage.contributing.item${number}`)}</span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </section>

        <section className={styles.conductSection}>
          <div className={`container ${styles.pageContainer}`}>
            <div className={styles.conductGrid}>
              <div className={styles.conductIntro}>
                <Heading as="h2" className={styles.conductTitle}>
                  {t('communityPage.codeOfConduct.title')}
                </Heading>
                <p className={styles.conductDesc}>
                  {t('communityPage.codeOfConduct.desc')}
                </p>
              </div>

              <ul className={styles.detailList}>
                {[1, 2, 3, 4].map((number) => (
                  <li key={number}>
                    <span className={styles.detailMark} aria-hidden="true" />
                    <span>{t(`communityPage.codeOfConduct.item${number}`)}</span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </section>
      </main>
    </Layout>
  );
}

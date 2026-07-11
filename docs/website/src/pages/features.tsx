import React from 'react';
import Layout from '@theme/Layout';
import Heading from '@theme/Heading';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import SyncIcon from './icons/SyncIcon';
import AnalyticsIcon from './icons/AnalyticsIcon';
import ShieldIcon from './icons/ShieldIcon';
import CloudIcon from './icons/CloudIcon';
import NetworkIcon from './icons/NetworkIcon';
import styles from './features.module.css';

type FeatureKey = 'realtime' | 'analytics' | 'security' | 'edge' | 'federated';
type PreviewVariant = 'A' | 'B' | 'C';
type Translate = (key: string) => string;

type FeatureDefinition = {
  icon: React.ComponentType<{ className?: string }>;
  key: FeatureKey;
};

const features: FeatureDefinition[] = [
  {
    icon: SyncIcon,
    key: 'realtime',
  },
  {
    icon: AnalyticsIcon,
    key: 'analytics',
  },
  {
    icon: ShieldIcon,
    key: 'security',
  },
  {
    icon: CloudIcon,
    key: 'edge',
  },
  {
    icon: NetworkIcon,
    key: 'federated',
  },
];

const previewVariants: Array<{
  id: PreviewVariant;
  label: string;
  note?: string;
}> = [
  { id: 'A', label: 'Roadmap ledger' },
  { id: 'B', label: 'Chapter index', note: 'Recommended' },
  { id: 'C', label: 'Capability explorer' },
];

const translations: Record<string, Record<string, string>> = {
  en: {
    'features.title': 'Upcoming Features',
    'features.subtitle': 'Exciting features we\'re planning for PowerMem\'s future',
    'features.more.title': 'And More...',
    'features.more.desc': 'We\'re continuously working on new features and improvements. Stay tuned for more exciting updates!',
    'feature.realtime.title': 'Real-time Sync & Collaboration',
    'feature.realtime.desc': 'Enable seamless memory synchronization across multiple devices and users, with real-time collaboration capabilities for team-based AI applications.',
    'feature.realtime.detail1': 'Multi-device synchronization',
    'feature.realtime.detail2': 'Real-time collaboration',
    'feature.realtime.detail3': 'Conflict resolution',
    'feature.realtime.detail4': 'Offline-first architecture',
    'feature.analytics.title': 'Advanced Analytics & Insights',
    'feature.analytics.desc': 'Comprehensive analytics dashboard for memory usage patterns, performance metrics, and intelligent recommendations for optimization.',
    'feature.analytics.detail1': 'Memory usage analytics',
    'feature.analytics.detail2': 'Performance monitoring',
    'feature.analytics.detail3': 'Intelligent recommendations',
    'feature.analytics.detail4': 'Customizable dashboards',
    'feature.security.title': 'Enterprise Security & Compliance',
    'feature.security.desc': 'Enterprise-grade security features including end-to-end encryption, audit logs, compliance controls, and advanced access management.',
    'feature.security.detail1': 'End-to-end encryption',
    'feature.security.detail2': 'Audit logging',
    'feature.security.detail3': 'Compliance controls',
    'feature.security.detail4': 'Advanced access management',
    'feature.edge.title': 'Edge Computing Support',
    'feature.edge.desc': 'Deploy PowerMem on edge devices with offline capabilities, reduced latency, and optimized resource usage for distributed AI applications.',
    'feature.edge.detail1': 'Edge device deployment',
    'feature.edge.detail2': 'Offline capabilities',
    'feature.edge.detail3': 'Reduced latency',
    'feature.edge.detail4': 'Resource optimization',
    'feature.federated.title': 'Federated Learning Integration',
    'feature.federated.desc': 'Enable privacy-preserving distributed learning across multiple organizations and devices without centralizing sensitive data.',
    'feature.federated.detail1': 'Privacy-preserving learning',
    'feature.federated.detail2': 'Distributed model training',
    'feature.federated.detail3': 'Cross-organization collaboration',
    'feature.federated.detail4': 'Secure aggregation protocols',
  },
  zh: {
    'features.title': '即将推出的特性',
    'features.subtitle': '我们为 PowerMem 未来规划的前沿功能',
    'features.description': 'PowerMem 即将推出的特性',
    'features.more.title': '还有更多...',
    'features.more.desc': '我们正在持续开发新功能和改进。敬请期待更多令人兴奋的更新！',
    'feature.realtime.title': '实时同步与协作',
    'feature.realtime.desc': '支持多设备、多用户的无缝记忆同步，为团队协作的 AI 应用提供实时协作能力。',
    'feature.realtime.detail1': '多设备同步',
    'feature.realtime.detail2': '实时协作',
    'feature.realtime.detail3': '冲突解决',
    'feature.realtime.detail4': '离线优先架构',
    'feature.analytics.title': '高级分析与洞察',
    'feature.analytics.desc': '全面的分析仪表板，提供记忆使用模式、性能指标分析，以及智能优化建议。',
    'feature.analytics.detail1': '记忆使用分析',
    'feature.analytics.detail2': '性能监控',
    'feature.analytics.detail3': '智能推荐',
    'feature.analytics.detail4': '可定制仪表板',
    'feature.security.title': '企业级安全与合规',
    'feature.security.desc': '企业级安全功能，包括端到端加密、审计日志、合规控制和高级访问管理。',
    'feature.security.detail1': '端到端加密',
    'feature.security.detail2': '审计日志',
    'feature.security.detail3': '合规控制',
    'feature.security.detail4': '高级访问管理',
    'feature.edge.title': '边缘计算支持',
    'feature.edge.desc': '在边缘设备上部署 PowerMem，支持离线能力、降低延迟，为分布式 AI 应用优化资源使用。',
    'feature.edge.detail1': '边缘设备部署',
    'feature.edge.detail2': '离线能力',
    'feature.edge.detail3': '降低延迟',
    'feature.edge.detail4': '资源优化',
    'feature.federated.title': '联邦学习集成',
    'feature.federated.desc': '支持跨多个组织和设备的隐私保护分布式学习，无需集中敏感数据。',
    'feature.federated.detail1': '隐私保护学习',
    'feature.federated.detail2': '分布式模型训练',
    'feature.federated.detail3': '跨组织协作',
    'feature.federated.detail4': '安全聚合协议',
  },
};

function FeatureDetails({ featureKey, t, className }: {
  featureKey: FeatureKey;
  t: Translate;
  className?: string;
}) {
  return (
    <ul className={`${styles.details}${className ? ` ${className}` : ''}`}>
      {[1, 2, 3, 4].map((num) => (
        <li key={num}>{t(`feature.${featureKey}.detail${num}`)}</li>
      ))}
    </ul>
  );
}

function MoreStatement({ t, className }: { t: Translate; className?: string }) {
  return (
    <aside className={`${styles.moreStatement}${className ? ` ${className}` : ''}`}>
      <span className={styles.moreMark} aria-hidden="true">•••</span>
      <div>
        <Heading as="h2" className={styles.moreTitle}>
          {t('features.more.title')}
        </Heading>
        <p className={styles.moreDesc}>{t('features.more.desc')}</p>
      </div>
    </aside>
  );
}

function RoadmapLedger({ t }: { t: Translate }) {
  return (
    <div className={styles.ledger}>
      {features.map((feature) => {
        const Icon = feature.icon;
        return (
          <article className={styles.ledgerRow} key={feature.key}>
            <div className={styles.ledgerIdentity}>
              <Icon className={styles.ledgerIcon} />
              <Heading as="h2" className={styles.ledgerTitle}>
                {t(`feature.${feature.key}.title`)}
              </Heading>
            </div>
            <div className={styles.ledgerBody}>
              <p className={styles.featureDesc}>{t(`feature.${feature.key}.desc`)}</p>
              <FeatureDetails featureKey={feature.key} t={t} />
            </div>
          </article>
        );
      })}
      <MoreStatement t={t} className={styles.ledgerMore} />
    </div>
  );
}

function ChapterIndex({ t }: { t: Translate }) {
  return (
    <div className={styles.chapterLayout}>
      <aside className={styles.chapterRail}>
        <nav className={styles.chapterNav} aria-label="Feature chapters">
          {features.map((feature) => (
            <a className={styles.chapterLink} href={`#feature-${feature.key}`} key={feature.key}>
              {t(`feature.${feature.key}.title`)}
            </a>
          ))}
        </nav>
      </aside>

      <div className={styles.chapterStream}>
        {features.map((feature) => {
          const Icon = feature.icon;
          return (
            <article className={styles.chapter} id={`feature-${feature.key}`} key={feature.key}>
              <header className={styles.chapterHeading}>
                <Icon className={styles.chapterIcon} />
                <Heading as="h2" className={styles.chapterTitle}>
                  {t(`feature.${feature.key}.title`)}
                </Heading>
              </header>
              <p className={styles.chapterDesc}>{t(`feature.${feature.key}.desc`)}</p>
              <FeatureDetails featureKey={feature.key} t={t} className={styles.chapterDetails} />
            </article>
          );
        })}
        <MoreStatement t={t} className={styles.chapterMore} />
      </div>
    </div>
  );
}

function CapabilityExplorer({ t }: { t: Translate }) {
  const [selectedFeature, setSelectedFeature] = React.useState<FeatureKey>('realtime');

  return (
    <>
      <div className={styles.explorerDesktop}>
        <div className={styles.explorerSelector} role="group" aria-label="Feature capabilities">
          {features.map((feature) => {
            const Icon = feature.icon;
            const isSelected = selectedFeature === feature.key;
            return (
              <button
                aria-controls={`capability-panel-${feature.key}`}
                aria-pressed={isSelected}
                className={styles.explorerOption}
                id={`capability-tab-${feature.key}`}
                key={feature.key}
                onClick={() => setSelectedFeature(feature.key)}
                type="button"
              >
                <Icon className={styles.explorerOptionIcon} />
                <span>{t(`feature.${feature.key}.title`)}</span>
              </button>
            );
          })}
        </div>

        <div className={styles.explorerPanels}>
          {features.map((feature) => {
            const Icon = feature.icon;
            const isSelected = selectedFeature === feature.key;
            return (
              <article
                aria-labelledby={`capability-tab-${feature.key}`}
                className={styles.explorerPanel}
                hidden={!isSelected}
                id={`capability-panel-${feature.key}`}
                key={feature.key}
                role="region"
                tabIndex={0}
              >
                <div className={styles.explorerPanelHeading}>
                  <Icon className={styles.explorerPanelIcon} />
                </div>
                <Heading as="h2" className={styles.explorerTitle}>
                  {t(`feature.${feature.key}.title`)}
                </Heading>
                <p className={styles.explorerDesc}>{t(`feature.${feature.key}.desc`)}</p>
                <FeatureDetails featureKey={feature.key} t={t} className={styles.explorerDetails} />
              </article>
            );
          })}
        </div>
      </div>

      <div className={styles.explorerMobile}>
        {features.map((feature, index) => {
          const Icon = feature.icon;
          return (
            <details className={styles.explorerDisclosure} key={feature.key} open={index === 0}>
              <summary className={styles.explorerSummary}>
                <Icon className={styles.explorerOptionIcon} />
                <span>{t(`feature.${feature.key}.title`)}</span>
              </summary>
              <div className={styles.explorerDisclosureBody}>
                <p className={styles.featureDesc}>{t(`feature.${feature.key}.desc`)}</p>
                <FeatureDetails featureKey={feature.key} t={t} />
              </div>
            </details>
          );
        })}
      </div>
      <MoreStatement t={t} className={styles.explorerMore} />
    </>
  );
}

export default function FeaturesPage() {
  const { i18n } = useDocusaurusContext();
  const isZh = i18n.currentLocale === 'zh';
  const t: Translate = (key) => translations[isZh ? 'zh' : 'en'][key] || key;
  const [variant, setVariant] = React.useState<PreviewVariant>('B');

  return (
    <Layout title={t('features.title')} description={isZh ? t('features.description') : 'PowerMem Upcoming Features'}>
      <main className={styles.featuresPage}>
        <div className={`container ${styles.pageContainer}`}>
          <section className={styles.previewControl} aria-label="Feature page design preview">
            <span className={styles.previewLabel}>Design preview</span>
            <div className={styles.previewTabs} role="group" aria-label="Choose a feature page layout">
              {previewVariants.map((option) => (
                <button
                  aria-pressed={variant === option.id}
                  className={styles.previewTab}
                  key={option.id}
                  onClick={() => setVariant(option.id)}
                  type="button"
                >
                  <span className={styles.previewLetter}>{option.id}</span>
                  <span className={styles.previewName}>{option.label}</span>
                  {option.note && <span className={styles.previewNote}>{option.note}</span>}
                </button>
              ))}
            </div>
          </section>

          <header className={styles.header}>
            <Heading as="h1" className={styles.title}>
              {t('features.title')}
            </Heading>
            <p className={styles.subtitle}>{t('features.subtitle')}</p>
          </header>

          <div className={styles.variantStage}>
            {variant === 'A' && <RoadmapLedger t={t} />}
            {variant === 'B' && <ChapterIndex t={t} />}
            {variant === 'C' && <CapabilityExplorer t={t} />}
          </div>
        </div>
      </main>
    </Layout>
  );
}

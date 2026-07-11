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
type Translate = (key: string) => string;

type FeatureDefinition = {
  icon: React.ComponentType<{ className?: string }>;
  key: FeatureKey;
};

const features: FeatureDefinition[] = [
  { icon: SyncIcon, key: 'realtime' },
  { icon: AnalyticsIcon, key: 'analytics' },
  { icon: ShieldIcon, key: 'security' },
  { icon: CloudIcon, key: 'edge' },
  { icon: NetworkIcon, key: 'federated' },
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

function SyncDiagram() {
  return (
    <svg className={styles.diagram} viewBox="0 0 560 320" role="presentation">
      <path className={styles.diagramLine} d="M151 95H241M319 95H409M280 133V218" />
      <path className={styles.diagramLineStrong} d="m225 86 16 9-16 9M335 86l-16 9 16 9M271 202l9 16 9-16" />
      <rect className={styles.diagramSurface} x="47" y="58" width="104" height="74" rx="4" />
      <rect className={styles.diagramSurface} x="409" y="58" width="104" height="74" rx="4" />
      <rect className={styles.diagramSurface} x="228" y="218" width="104" height="54" rx="4" />
      <circle className={styles.diagramAccentSoft} cx="280" cy="95" r="39" />
      <path className={styles.diagramAccentLine} d="M260 91a22 22 0 0 1 34-13l5 5M300 99a22 22 0 0 1-34 13l-5-5" />
      <path className={styles.diagramAccent} d="m299 75 1 11-11-1M261 115l-1-11 11 1" />
      <circle className={styles.diagramAccent} cx="99" cy="95" r="5" />
      <circle className={styles.diagramAccent} cx="461" cy="95" r="5" />
      <circle className={styles.diagramAccent} cx="280" cy="245" r="5" />
      <path className={styles.diagramMutedLine} d="M70 148h58M432 148h58M250 288h60" />
    </svg>
  );
}

function AnalyticsDiagram() {
  return (
    <svg className={styles.diagram} viewBox="0 0 560 320" role="presentation">
      <path className={styles.diagramLine} d="M74 56v208h420M74 108h420M74 160h420M74 212h420" />
      <rect className={styles.diagramSurface} x="112" y="183" width="38" height="81" rx="2" />
      <rect className={styles.diagramSurface} x="172" y="139" width="38" height="125" rx="2" />
      <rect className={styles.diagramAccentSoft} x="232" y="100" width="38" height="164" rx="2" />
      <rect className={styles.diagramSurface} x="292" y="155" width="38" height="109" rx="2" />
      <rect className={styles.diagramSurface} x="352" y="123" width="38" height="141" rx="2" />
      <rect className={styles.diagramSurface} x="412" y="76" width="38" height="188" rx="2" />
      <path className={styles.diagramAccentLine} d="m95 198 78-54 75 21 74-69 64 22 83-61" />
      <circle className={styles.diagramAccent} cx="95" cy="198" r="5" />
      <circle className={styles.diagramAccent} cx="173" cy="144" r="5" />
      <circle className={styles.diagramAccent} cx="248" cy="165" r="5" />
      <circle className={styles.diagramAccent} cx="322" cy="96" r="5" />
      <circle className={styles.diagramAccent} cx="386" cy="118" r="5" />
      <circle className={styles.diagramAccent} cx="469" cy="57" r="5" />
    </svg>
  );
}

function SecurityDiagram() {
  return (
    <svg className={styles.diagram} viewBox="0 0 560 320" role="presentation">
      <circle className={styles.diagramLine} cx="280" cy="160" r="118" />
      <circle className={styles.diagramMutedLine} cx="280" cy="160" r="84" strokeDasharray="5 8" />
      <path className={styles.diagramLine} d="M280 42V20M398 160h35M280 278v22M162 160h-35" />
      <circle className={styles.diagramSurface} cx="280" cy="160" r="58" />
      <path className={styles.diagramAccentSoft} d="M280 104l42 18v31c0 31-17 56-42 67-25-11-42-36-42-67v-31l42-18Z" />
      <path className={styles.diagramAccentLine} d="M280 104l42 18v31c0 31-17 56-42 67-25-11-42-36-42-67v-31l42-18Z" />
      <path className={styles.diagramAccentLine} d="m261 159 13 13 27-31" />
      <circle className={styles.diagramAccent} cx="280" cy="20" r="5" />
      <circle className={styles.diagramAccent} cx="433" cy="160" r="5" />
      <circle className={styles.diagramAccent} cx="280" cy="300" r="5" />
      <circle className={styles.diagramAccent} cx="127" cy="160" r="5" />
    </svg>
  );
}

function EdgeDiagram() {
  return (
    <svg className={styles.diagram} viewBox="0 0 560 320" role="presentation">
      <path className={styles.diagramLine} d="M162 78 245 137M398 78l-83 59M162 242l83-59M398 242l-83-59" />
      <rect className={styles.diagramSurface} x="82" y="46" width="80" height="64" rx="4" />
      <rect className={styles.diagramSurface} x="398" y="46" width="80" height="64" rx="4" />
      <rect className={styles.diagramSurface} x="82" y="210" width="80" height="64" rx="4" />
      <rect className={styles.diagramSurface} x="398" y="210" width="80" height="64" rx="4" />
      <rect className={styles.diagramAccentSoft} x="226" y="118" width="108" height="84" rx="6" />
      <path className={styles.diagramAccentLine} d="M244 172h73c12 0 21-9 21-20s-9-20-21-20h-4c-5-13-18-22-33-22-18 0-33 12-36 29h-2c-10 0-18 7-18 17s8 16 20 16Z" />
      <circle className={styles.diagramAccent} cx="122" cy="78" r="5" />
      <circle className={styles.diagramAccent} cx="438" cy="78" r="5" />
      <circle className={styles.diagramAccent} cx="122" cy="242" r="5" />
      <circle className={styles.diagramAccent} cx="438" cy="242" r="5" />
      <path className={styles.diagramMutedLine} d="M101 93h42M417 93h42M101 257h42M417 257h42" />
    </svg>
  );
}

function FederatedDiagram() {
  return (
    <svg className={styles.diagram} viewBox="0 0 560 320" role="presentation">
      <path className={styles.diagramMutedLine} d="M280 160 138 73M280 160l142-87M280 160 111 235M280 160l169 75M280 160v132" strokeDasharray="5 8" />
      <circle className={styles.diagramLine} cx="280" cy="160" r="67" />
      <circle className={styles.diagramAccentSoft} cx="280" cy="160" r="40" />
      <path className={styles.diagramAccentLine} d="m280 124 31 18v36l-31 18-31-18v-36l31-18Z" />
      <circle className={styles.diagramSurface} cx="138" cy="73" r="31" />
      <circle className={styles.diagramSurface} cx="422" cy="73" r="31" />
      <circle className={styles.diagramSurface} cx="111" cy="235" r="31" />
      <circle className={styles.diagramSurface} cx="449" cy="235" r="31" />
      <circle className={styles.diagramSurface} cx="280" cy="292" r="20" />
      <circle className={styles.diagramAccent} cx="138" cy="73" r="5" />
      <circle className={styles.diagramAccent} cx="422" cy="73" r="5" />
      <circle className={styles.diagramAccent} cx="111" cy="235" r="5" />
      <circle className={styles.diagramAccent} cx="449" cy="235" r="5" />
      <circle className={styles.diagramAccent} cx="280" cy="292" r="5" />
      <circle className={styles.diagramAccent} cx="280" cy="160" r="5" />
    </svg>
  );
}

function FeatureDiagram({ featureKey }: { featureKey: FeatureKey }) {
  switch (featureKey) {
    case 'realtime':
      return <SyncDiagram />;
    case 'analytics':
      return <AnalyticsDiagram />;
    case 'security':
      return <SecurityDiagram />;
    case 'edge':
      return <EdgeDiagram />;
    case 'federated':
      return <FederatedDiagram />;
  }
}

function FeatureDetails({ featureKey, t }: { featureKey: FeatureKey; t: Translate }) {
  return (
    <ul className={styles.featureDetails}>
      {[1, 2, 3, 4].map((number) => (
        <li key={number}>{t(`feature.${featureKey}.detail${number}`)}</li>
      ))}
    </ul>
  );
}

export default function FeaturesPage() {
  const { i18n } = useDocusaurusContext();
  const isZh = i18n.currentLocale === 'zh';
  const t: Translate = (key) => translations[isZh ? 'zh' : 'en'][key] || key;

  return (
    <Layout title={t('features.title')} description={isZh ? t('features.description') : 'PowerMem Upcoming Features'}>
      <main className={styles.featuresPage}>
        <header className={styles.hero}>
          <div className={`container ${styles.pageContainer}`}>
            <div className={styles.heroGrid}>
              <Heading as="h1" className={styles.title}>
                {t('features.title')}
              </Heading>
              <p className={styles.subtitle}>{t('features.subtitle')}</p>
            </div>

            <div className={styles.featureMap} aria-hidden="true">
              {features.map((feature) => {
                const Icon = feature.icon;
                return (
                  <span className={styles.mapPoint} key={feature.key}>
                    <Icon className={styles.mapIcon} />
                  </span>
                );
              })}
            </div>
          </div>
        </header>

        <div className={styles.featureAtlas}>
          {features.map((feature, index) => {
            const Icon = feature.icon;
            return (
              <article
                className={`${styles.featureChapter} ${index % 2 === 1 ? styles.featureChapterReverse : ''}`}
                key={feature.key}
              >
                <div className={`container ${styles.pageContainer} ${styles.chapterGrid}`}>
                  <header className={styles.featureCopy}>
                    <span className={styles.featureIconFrame} aria-hidden="true">
                      <Icon className={styles.featureIcon} />
                    </span>
                    <Heading as="h2" className={styles.featureTitle}>
                      {t(`feature.${feature.key}.title`)}
                    </Heading>
                    <p className={styles.featureDesc}>{t(`feature.${feature.key}.desc`)}</p>
                  </header>

                  <div className={styles.diagramFrame} aria-hidden="true">
                    <FeatureDiagram featureKey={feature.key} />
                  </div>

                  <FeatureDetails featureKey={feature.key} t={t} />
                </div>
              </article>
            );
          })}
        </div>

        <aside className={styles.moreSection}>
          <div className={`container ${styles.pageContainer} ${styles.moreGrid}`}>
            <Heading as="h2" className={styles.moreTitle}>
              {t('features.more.title')}
            </Heading>
            <p className={styles.moreDesc}>{t('features.more.desc')}</p>
          </div>
        </aside>
      </main>
    </Layout>
  );
}

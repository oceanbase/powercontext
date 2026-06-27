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

const features = [
  {
    icon: SyncIcon,
    key: 'realtime',
    color: 'blue',
  },
  {
    icon: AnalyticsIcon,
    key: 'analytics',
    color: 'purple',
  },
  {
    icon: ShieldIcon,
    key: 'security',
    color: 'green',
  },
  {
    icon: CloudIcon,
    key: 'edge',
    color: 'orange',
  },
  {
    icon: NetworkIcon,
    key: 'federated',
    color: 'pink',
  },
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

export default function FeaturesPage() {
  const { i18n } = useDocusaurusContext();
  const isZh = i18n.currentLocale === 'zh';
  const t = (key: string) => translations[isZh ? 'zh' : 'en'][key] || key;

  return (
    <Layout title={t('features.title')} description={isZh ? t('features.description') : 'PowerMem Upcoming Features'}>
      <div className={styles.featuresPage}>
        <div className="container margin-vert--lg">
          <div className={styles.header}>
            <Heading as="h1" className={styles.title}>
              {t('features.title')}
            </Heading>
            <p className={styles.subtitle}>
              {t('features.subtitle')}
            </p>
          </div>

          <div className={styles.grid}>
            {features.map((feature, index) => {
              const Icon = feature.icon;
              return (
                <div
                  key={feature.key}
                  className={`${styles.card} ${styles[`card-${feature.color}`]}`}
                >
                  <div className={styles.iconContainer}>
                    <Icon className={styles.icon} />
                  </div>
                  <Heading as="h3" className={styles.cardTitle}>
                    {t(`feature.${feature.key}.title`)}
                  </Heading>
                  <p className={styles.cardDesc}>
                    {t(`feature.${feature.key}.desc`)}
                  </p>
                  <ul className={styles.details}>
                    {[1, 2, 3, 4].map((num) => (
                      <li key={num}>
                        <span className={styles.bullet}>•</span>
                        {t(`feature.${feature.key}.detail${num}`)}
                      </li>
                    ))}
                  </ul>
                </div>
              );
            })}
            {/* More features placeholder */}
            <div className={`${styles.card} ${styles.cardMore}`}>
              <div className={styles.iconContainer}>
                <div className={styles.moreIcon}>...</div>
              </div>
              <Heading as="h3" className={styles.cardTitle}>
                {t('features.more.title')}
              </Heading>
              <p className={styles.cardDesc}>
                {t('features.more.desc')}
              </p>
            </div>
          </div>
        </div>
      </div>
    </Layout>
  );
}

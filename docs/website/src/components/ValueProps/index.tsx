import React, { useState } from 'react';
import Link from '@docusaurus/Link';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import Heading from '@theme/Heading';
import AgileIcon from './icons/AgileIcon';
import AffordableIcon from './icons/AffordableIcon';
import AccurateIcon from './icons/AccurateIcon';
import styles from './styles.module.css';

// Order: accurate, agile, affordable
const valueProps = [
  { icon: AccurateIcon, key: 'accurate' },
  { icon: AgileIcon, key: 'agile' },
  { icon: AffordableIcon, key: 'affordable' },
];

// Comparison data - labels will be translated
const getComparisonData = (isZh: boolean) => ({
  accurate: {
    fullContext: 52.9,
    powermem: 78.7,
    unit: '',
    label: isZh ? 'LLM 评分' : 'LLM Score',
  },
  agile: {
    fullContext: 17.12,
    powermem: 1.44,
    unit: 's',
    label: isZh ? '响应时间' : 'Response Time',
  },
  affordable: {
    fullContext: 26,
    powermem: 0.9,
    unit: 'k',
    label: isZh ? 'Token 使用量' : 'Token Usage',
  },
});

const translations: Record<string, Record<string, string>> = {
  en: {
    'valueProps.title': 'Why Choose PowerMem?',
    'valueProps.subtitle': 'Accurate, Agile, Affordable - The best AI memory management experience',
    'valueProps.benchmarkDesc': 'Real-world performance metrics based on LOCOMO dataset',
    'valueProps.viewBenchmark': 'View Full Benchmark Results',
    'valueProps.agile.title': 'Agile',
    'valueProps.agile.en': 'Faster',
    'valueProps.agile.desc': 'Ultra-fast retrieval response, high-performance async processing, intelligent cache optimization',
    'valueProps.agile.feature1': 'Ultra-fast Retrieval',
    'valueProps.agile.feature2': 'High-Performance Async',
    'valueProps.agile.feature3': 'Intelligent Cache Optimization',
    'valueProps.affordable.title': 'Affordable',
    'valueProps.affordable.en': 'More Economical',
    'valueProps.affordable.desc': 'Reduce storage costs, intelligent memory management, efficient resource utilization',
    'valueProps.affordable.feature1': 'Lower Storage Costs',
    'valueProps.affordable.feature2': 'Intelligent Memory Management',
    'valueProps.affordable.feature3': 'Efficient Resource Usage',
    'valueProps.accurate.title': 'Accurate',
    'valueProps.accurate.en': 'More Accurate',
    'valueProps.accurate.desc': 'Precise memory retrieval, AI-driven importance scoring, context-aware matching',
    'valueProps.accurate.feature1': 'Precise Memory Retrieval',
    'valueProps.accurate.feature2': 'AI-Driven Scoring',
    'valueProps.accurate.feature3': 'Context-Aware Matching',
  },
  zh: {
    'valueProps.title': '为什么选择 PowerMem？',
    'valueProps.subtitle': '更快、更省、更准 - 最佳的 AI 内存管理体验',
    'valueProps.benchmarkDesc': '基于 LOCOMO 数据集的真实性能指标',
    'valueProps.viewBenchmark': '查看完整压测数据',
    'valueProps.agile.title': '更快',
    'valueProps.agile.en': 'Agile',
    'valueProps.agile.desc': '极速检索响应，高性能异步处理，智能缓存优化',
    'valueProps.agile.feature1': '极速检索响应',
    'valueProps.agile.feature2': '高性能异步处理',
    'valueProps.agile.feature3': '智能缓存优化',
    'valueProps.affordable.title': '更省',
    'valueProps.affordable.en': 'Affordable',
    'valueProps.affordable.desc': '降低存储成本，智能内存管理，资源高效利用',
    'valueProps.affordable.feature1': '降低存储成本',
    'valueProps.affordable.feature2': '智能内存管理',
    'valueProps.affordable.feature3': '资源高效利用',
    'valueProps.accurate.title': '更准',
    'valueProps.accurate.en': 'Accurate',
    'valueProps.accurate.desc': '精准记忆检索，AI 驱动的重要性评分，上下文感知匹配',
    'valueProps.accurate.feature1': '精准记忆检索',
    'valueProps.accurate.feature2': 'AI 驱动评分',
    'valueProps.accurate.feature3': '上下文感知匹配',
  },
};

export default function ValueProps() {
  const { i18n } = useDocusaurusContext();
  const isZh = i18n.currentLocale === 'zh';
  const [hoveredKey, setHoveredKey] = useState<string | null>(valueProps[0].key);
  const t = (key: string) => {
    const lang = isZh ? 'zh' : 'en';
    return translations[lang][key] || key;
  };

  const comparisonData = getComparisonData(isZh);
  const activeComparison = hoveredKey ? comparisonData[hoveredKey as keyof typeof comparisonData] : null;

  return (
    <section className={styles.valueProps}>
      <div className="container">
        <div className={styles.header}>
          <Heading as="h2" className={styles.title}>
            {t('valueProps.title')}
          </Heading>
          <p className={styles.subtitle}>
            {t('valueProps.subtitle')}
          </p>
          <p className={styles.benchmarkDesc}>
            {t('valueProps.benchmarkDesc')}
          </p>
        </div>

        <div className={styles.content}>
          {/* Left side: Cards */}
          <div className={styles.cardsContainer}>
            {valueProps.map((prop, index) => {
              const Icon = prop.icon;
              const isActive = hoveredKey === prop.key;
              return (
                <div
                  key={prop.key}
                  className={`${styles.card} ${isActive ? styles.cardActive : ''} fade-in-delay-${index + 1}`}
                  onMouseEnter={() => setHoveredKey(prop.key)}
                  style={{'--index': index} as React.CSSProperties}
                >
                  <div className={styles.cardContent}>
                    <div className={styles.cardHeader}>
                      <div className={styles.icon}>
                        <Icon className={styles.iconSvg} />
                      </div>
                      <div className={styles.cardTitleWrapper}>
                        <Heading as="h3" className={styles.cardTitle}>
                          {t(`valueProps.${prop.key}.title`)}
                        </Heading>
                        <p className={styles.cardEn}>{t(`valueProps.${prop.key}.en`)}</p>
                      </div>
                    </div>
                    <p className={styles.cardDesc}>
                      {t(`valueProps.${prop.key}.desc`)}
                    </p>
                  </div>
                </div>
              );
            })}
          </div>

          {/* Right side: Comparison */}
          <div className={styles.comparisonContainer}>
            {activeComparison && (
              <div className={styles.comparison}>
                <div className={styles.comparisonHeader}>
                  <Heading as="h3" className={styles.comparisonTitle}>
                    {activeComparison.label}
                  </Heading>
                </div>
                <div className={styles.comparisonData}>
                  {/* PowerMem */}
                  <div className={`${styles.comparisonItem} ${styles.comparisonItemPowerMem}`}>
                    <div className={styles.comparisonLabel}>PowerMem</div>
                    <div className={styles.comparisonValue}>
                      {activeComparison.powermem}
                      <span className={styles.comparisonUnit}>{activeComparison.unit}</span>
                    </div>
                    <div className={styles.improvement}>
                      {hoveredKey === 'accurate' && (
                        <span className={styles.improvementText}>
                          +{(activeComparison.powermem - activeComparison.fullContext).toFixed(1)}%
                        </span>
                      )}
                      {hoveredKey === 'agile' && (
                        <span className={styles.improvementText}>
                          {((activeComparison.fullContext / activeComparison.powermem).toFixed(1))}x faster
                        </span>
                      )}
                      {hoveredKey === 'affordable' && (
                        <span className={styles.improvementText}>
                          {((activeComparison.fullContext / activeComparison.powermem).toFixed(1))}x less
                        </span>
                      )}
                    </div>
                  </div>
                  
                  {/* VS */}
                  <div className={styles.vsDivider}>
                    <span className={styles.vsText}>VS</span>
                  </div>

                  {/* Full-Context */}
                  <div className={styles.comparisonItem}>
                    <div className={styles.comparisonLabel}>
                      {isZh ? 'Full-Context' : 'Full-Context'}
                    </div>
                    <div className={styles.comparisonValue}>
                      {activeComparison.fullContext}
                      <span className={styles.comparisonUnit}>{activeComparison.unit}</span>
                    </div>
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>

        <div className={styles.footer}>
          <Link
            to="/benchmark"
            className="button button--secondary"
          >
            {t('valueProps.viewBenchmark')} →
          </Link>
        </div>
      </div>
    </section>
  );
}

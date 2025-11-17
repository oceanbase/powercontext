import React, { useState } from 'react';
import Link from '@docusaurus/Link';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import Heading from '@theme/Heading';
import AgileIcon from '../ValueProps/icons/AgileIcon';
import AffordableIcon from '../ValueProps/icons/AffordableIcon';
import AccurateIcon from '../ValueProps/icons/AccurateIcon';
import styles from './styles.module.css';

const valueProps = [
  { icon: AccurateIcon, key: 'accurate' },
  { icon: AgileIcon, key: 'agile' },
  { icon: AffordableIcon, key: 'affordable' },
];

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
    'valueProps.title': 'Why Choose PowerMem?-5',
    'valueProps.subtitle': 'Accurate, Agile, Affordable - The best AI memory management experience',
    'valueProps.benchmarkDesc': 'Real-world performance metrics based on LOCOMO dataset',
    'valueProps.viewBenchmark': 'View Full Benchmark Results',
    'valueProps.agile.title': 'Agile',
    'valueProps.agile.en': 'Faster',
    'valueProps.agile.desc': 'Ultra-fast retrieval response, high-performance async processing, intelligent cache optimization',
    'valueProps.affordable.title': 'Affordable',
    'valueProps.affordable.en': 'More Economical',
    'valueProps.affordable.desc': 'Reduce storage costs, intelligent memory management, efficient resource utilization',
    'valueProps.accurate.title': 'Accurate',
    'valueProps.accurate.en': 'More Accurate',
    'valueProps.accurate.desc': 'Precise memory retrieval, AI-driven importance scoring, context-aware matching',
  },
  zh: {
    'valueProps.title': '为什么选择 PowerMem？-1',
    'valueProps.subtitle': '更快、更省、更准 - 最佳的 AI 内存管理体验',
    'valueProps.benchmarkDesc': '基于 LOCOMO 数据集的真实性能指标',
    'valueProps.viewBenchmark': '查看完整压测数据',
    'valueProps.agile.title': '更快',
    'valueProps.agile.en': 'Agile',
    'valueProps.agile.desc': '极速检索响应，高性能异步处理，智能缓存优化',
    'valueProps.affordable.title': '更省',
    'valueProps.affordable.en': 'Affordable',
    'valueProps.affordable.desc': '降低存储成本，智能内存管理，资源高效利用',
    'valueProps.accurate.title': '更准',
    'valueProps.accurate.en': 'Accurate',
    'valueProps.accurate.desc': '精准记忆检索，AI 驱动的重要性评分，上下文感知匹配',
  },
};

export default function ValueProps5() {
  const { i18n } = useDocusaurusContext();
  const isZh = i18n.currentLocale === 'zh';
  const [hoveredKey, setHoveredKey] = useState<string | null>(valueProps[0].key);
  const t = (key: string) => {
    const lang = isZh ? 'zh' : 'en';
    return translations[lang][key] || key;
  };

  const comparisonData = getComparisonData(isZh);
  const activeComparison = hoveredKey ? comparisonData[hoveredKey as keyof typeof comparisonData] : null;

  const getCirclePercentage = (value: number, max: number) => {
    return Math.min((value / max) * 100, 100);
  };

  const maxValue = activeComparison ? Math.max(activeComparison.fullContext, activeComparison.powermem) * 1.2 : 100;
  
  const circumference = 2 * Math.PI * 45; // radius = 45

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
          <div className={styles.cardsContainer}>
            {valueProps.map((prop, index) => {
              const Icon = prop.icon;
              const isActive = hoveredKey === prop.key;
              return (
                <div
                  key={prop.key}
                  className={`${styles.card} ${isActive ? styles.cardActive : ''}`}
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

          <div className={styles.comparisonContainer}>
            {activeComparison && (
              <div className={styles.comparison}>
                <div className={styles.comparisonHeader}>
                  <Heading as="h3" className={styles.comparisonTitle}>
                    {activeComparison.label}
                  </Heading>
                </div>
                <div className={styles.comparisonData}>
                  {/* Circular Progress Layout */}
                  <div className={styles.circleRow}>
                    {/* PowerMem Circle */}
                    <div className={styles.circleItem}>
                      <div className={styles.circleLabel}>PowerMem</div>
                      <div className={styles.circleContainer}>
                        <svg className={styles.circleSvg} viewBox="0 0 100 100">
                          <defs>
                            <linearGradient id="circleGradientPowerMem" x1="0%" y1="0%" x2="100%" y2="0%">
                              <stop offset="0%" style={{stopColor: '#3b82f6', stopOpacity: 1}} />
                              <stop offset="100%" style={{stopColor: '#9333ea', stopOpacity: 1}} />
                            </linearGradient>
                          </defs>
                          <circle
                            className={styles.circleBackground}
                            cx="50"
                            cy="50"
                            r="45"
                            fill="none"
                            strokeWidth="8"
                          />
                          <circle
                            className={styles.circleProgressPowerMem}
                            cx="50"
                            cy="50"
                            r="45"
                            fill="none"
                            strokeWidth="8"
                            strokeDasharray={circumference}
                            strokeDashoffset={circumference - (getCirclePercentage(activeComparison.powermem, maxValue) / 100) * circumference}
                            strokeLinecap="round"
                          />
                        </svg>
                        <div className={styles.circleValue}>
                          {activeComparison.powermem}
                          <span className={styles.circleUnit}>{activeComparison.unit}</span>
                        </div>
                      </div>
                      <div className={styles.circleImprovement}>
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

                    {/* VS Divider */}
                    <div className={styles.vsDivider}>
                      <span className={styles.vsText}>VS</span>
                    </div>

                    {/* Full-Context Circle */}
                    <div className={styles.circleItem}>
                      <div className={styles.circleLabel}>Full-Context</div>
                      <div className={styles.circleContainer}>
                        <svg className={styles.circleSvg} viewBox="0 0 100 100">
                          <circle
                            className={styles.circleBackground}
                            cx="50"
                            cy="50"
                            r="45"
                            fill="none"
                            strokeWidth="8"
                          />
                          <circle
                            className={styles.circleProgressFullContext}
                            cx="50"
                            cy="50"
                            r="45"
                            fill="none"
                            strokeWidth="8"
                            strokeDasharray={circumference}
                            strokeDashoffset={circumference - (getCirclePercentage(activeComparison.fullContext, maxValue) / 100) * circumference}
                            strokeLinecap="round"
                          />
                        </svg>
                        <div className={styles.circleValue}>
                          {activeComparison.fullContext}
                          <span className={styles.circleUnit}>{activeComparison.unit}</span>
                        </div>
                      </div>
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


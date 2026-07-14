import React from 'react';
import Link from '@docusaurus/Link';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import Heading from '@theme/Heading';
import {localizedPath} from '../../utils/localizedPath';
import styles from './styles.module.css';

const metrics = [
  {key: 'accurate', value: '87.79%', baseline: '52.9%', change: '+65.9%'},
  {key: 'agile', value: '1.44s', baseline: '17.12s', change: '-91.6%'},
  {key: 'affordable', value: '~0.9k', baseline: '~26k', change: '-96.5%'},
];

const translations: Record<string, Record<string, string>> = {
  en: {
    title: 'Why Choose PowerMem?',
    subtitle: 'Accurate, Agile, Affordable - The best AI memory management experience',
    benchmarkDesc: 'Real-world performance metrics based on LOCOMO dataset',
    view: 'View Full Benchmark Results',
    accurate: 'LLM Score',
    agile: 'Retrieval P95',
    affordable: 'Token Usage',
    baseline: 'Full-Context',
  },
  zh: {
    title: '为什么选择 PowerMem？',
    subtitle: '更快、更省、更准 - 更好的 AI 记忆管理体验',
    benchmarkDesc: '基于 LOCOMO 数据集的真实性能指标',
    view: '查看完整压测数据',
    accurate: 'LLM 评分',
    agile: 'Retrieval P95',
    affordable: 'Token 使用量',
    baseline: 'Full-Context',
  },
};

export default function ValueProps1() {
  const {i18n} = useDocusaurusContext();
  const isZh = i18n.currentLocale === 'zh';
  const t = translations[isZh ? 'zh' : 'en'];

  return (
    <section className={styles.valueProps}>
      <div className={styles.inner}>
        <div className={styles.intro}>
          <Heading as="h2">{t.title}</Heading>
          <p>{t.subtitle}</p>
          <p className={styles.benchmarkDesc}>{t.benchmarkDesc}</p>
          <Link to={localizedPath('/benchmark', isZh)}>{t.view} →</Link>
        </div>
        <div className={styles.metrics}>
          {metrics.map((metric) => (
            <article key={metric.key} className={styles.metric}>
              <span className={styles.metricLabel}>{t[metric.key]}</span>
              <strong className={styles.metricValue}>{metric.value}</strong>
              <span className={styles.metricBaseline}>
                {t.baseline} {metric.baseline}
              </span>
              <span className={styles.metricChange}>{metric.change}</span>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}

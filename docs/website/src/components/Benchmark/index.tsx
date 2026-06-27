import React from 'react';
import Link from '@docusaurus/Link';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import Heading from '@theme/Heading';
import ZapIcon from './icons/ZapIcon';
import {localizedPath} from '../../utils/localizedPath';
import styles from './styles.module.css';

const overallScores = {
  llm_score: 0.8779,
};

const translations: Record<string, Record<string, string>> = {
  en: {
    'benchmark.title': 'Performance Benchmarks',
    'benchmark.subtitle': 'Real-world performance metrics based on LOCOMO dataset',
    'benchmark.bleuscore.desc': 'Text similarity evaluation',
    'benchmark.f1score.desc': 'Precision and recall balance',
    'benchmark.llmscore.desc': 'LLM-based quality assessment',
    'benchmark.viewDetails': 'View Full Benchmark Results',
  },
  zh: {
    'benchmark.title': '性能压测数据',
    'benchmark.subtitle': '基于 LOCOMO 数据集的真实性能指标',
    'benchmark.bleuscore.desc': '文本相似度评估',
    'benchmark.f1score.desc': '精确率和召回率平衡',
    'benchmark.llmscore.desc': '基于 LLM 的质量评估',
    'benchmark.viewDetails': '查看完整压测数据',
  },
};

const formatScore = (num: number) => {
  return (num * 100).toFixed(2) + '%';
};

export default function Benchmark() {
  const { i18n } = useDocusaurusContext();
  const isZh = i18n.currentLocale === 'zh';
  const t = (key: string) => translations[isZh ? 'zh' : 'en'][key] || key;

  const metrics = [
    {
      icon: ZapIcon,
      label: 'LLM Score',
      value: formatScore(overallScores.llm_score),
      color: 'green',
      descKey: 'benchmark.llmscore.desc',
    },
  ];

  return (
    <section className={styles.benchmark}>
      <div className="container">
        <div className={styles.header}>
          <Heading as="h2" className={styles.title}>
            {t('benchmark.title')}
          </Heading>
          <p className={styles.subtitle}>
            {t('benchmark.subtitle')}
          </p>
        </div>

        <div className={styles.grid}>
          {metrics.map((metric, index) => {
            const Icon = metric.icon;
            return (
              <div
                key={metric.label}
                className={`${styles.card} ${styles[`card-${metric.color}`]} fade-in-delay-${index + 1}`}
              >
                <div className={styles.cardHeader}>
                  <div className={styles.icon}>
                    <Icon className={styles.iconSvg} />
                  </div>
                  <div className={styles.cardInfo}>
                    <p className={styles.cardLabel}>{metric.label}</p>
                    <p className={styles.cardValue}>{metric.value}</p>
                  </div>
                </div>
                <p className={styles.cardDesc}>{t(metric.descKey)}</p>
              </div>
            );
          })}
        </div>
      </div>

      <div className={styles.footer}>
        <div className="container">
          <Link
            to={localizedPath('/benchmark', isZh)}
            className="button button--secondary"
          >
            {t('benchmark.viewDetails')} →
          </Link>
        </div>
      </div>
    </section>
  );
}

import React from 'react';
import Layout from '@theme/Layout';
import Heading from '@theme/Heading';
import Link from '@docusaurus/Link';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import styles from './benchmark.module.css';

const translations: Record<string, Record<string, string>> = {
  en: {
    'benchmark.title': 'Performance Benchmarks',
    'benchmark.subtitle': 'Real-world performance metrics based on LOCOMO dataset',
    'benchmark.config': '10 long multi-turn conversations · 1,540 QA · GPT-5.5',
    'benchmark.comparison.title': 'PowerMem vs Full-Context',
    'benchmark.performance.title': 'Performance Metrics',
    'benchmark.performance.totalRequests': 'Total Requests',
    'benchmark.performance.avgTime': 'Average Request Time',
    'benchmark.performance.p95Time': 'P95 Request Time',
    'benchmark.metrics.llmScore': 'LLM Score',
    'benchmark.metrics.accuracy': 'Accuracy',
    'benchmark.metrics.retrievalP95': 'Retrieval P95',
    'benchmark.metrics.latency': 'Latency',
    'benchmark.metrics.tokenUsage': 'Token Usage',
    'benchmark.metrics.consumption': 'Consumption',
    'benchmark.metrics.llmScore.baseline': 'baseline 52.9%, +65.9%',
    'benchmark.metrics.retrievalP95.baseline': 'baseline 17.12s, -91.6%',
    'benchmark.metrics.tokenUsage.baseline': 'baseline ~26k, -96.5%',
    'benchmark.scores.title': 'Mean Scores Per Category',
    'benchmark.scores.bleu': 'BLEU Score',
    'benchmark.scores.f1': 'F1 Score',
    'benchmark.scores.llm': 'LLM Score',
    'benchmark.scores.count': 'Count',
    'benchmark.scores.category': 'Category',
    'benchmark.scores.description': 'Description',
    'benchmark.scores.viewDetails': 'How to Run Benchmark Tests',
    'benchmark.tokens.title': 'Token Usage During Evaluation',
    'benchmark.tokens.prompt': 'Prompt Tokens',
    'benchmark.tokens.completion': 'Completion Tokens',
    'benchmark.tokens.total': 'Total Tokens',
    'benchmark.tokens.cached': 'Cached Tokens',
    'category.1.name': 'Single-Hop',
    'category.1.desc': 'Questions asking for specific facts directly mentioned in a single conversation.',
    'category.2.name': 'Temporal',
    'category.2.desc': 'Questions can be answered through temporal reasoning and capturing time-related data cues within the conversation.',
    'category.3.name': 'Multi-Hop',
    'category.3.desc': 'Questions that require synthesizing information from multiple sessions.',
    'category.4.name': 'Open-Domain',
    'category.4.desc': 'Questions can be answered by integrating a speaker\'s provided information with external knowledge, such as commonsense or world facts.',
    'category.5.name': 'Adversarial',
    'category.5.desc': 'These questions are designed to trick the agent into providing wrong answers, with the expectation that the agent will correctly identify them as unanswerable.',
  },
  zh: {
    'benchmark.title': '性能压测数据',
    'benchmark.subtitle': '基于 LOCOMO 数据集的真实性能指标',
    'benchmark.config': '10 段超长多轮对话 · 1540 个 QA · GPT-5.5',
    'benchmark.comparison.title': 'PowerMem vs Full-Context',
    'benchmark.performance.title': '性能指标',
    'benchmark.performance.totalRequests': '总请求数',
    'benchmark.performance.avgTime': '平均请求时间',
    'benchmark.performance.p95Time': 'P95 请求时间',
    'benchmark.metrics.llmScore': 'LLM Score',
    'benchmark.metrics.accuracy': '准确率',
    'benchmark.metrics.retrievalP95': 'Retrieval P95',
    'benchmark.metrics.latency': '延迟',
    'benchmark.metrics.tokenUsage': 'Token 用量',
    'benchmark.metrics.consumption': '消耗',
    'benchmark.metrics.llmScore.baseline': 'baseline 52.9%, 提升 +65.9%',
    'benchmark.metrics.retrievalP95.baseline': 'baseline 17.12s, 降低 -91.6%',
    'benchmark.metrics.tokenUsage.baseline': 'baseline ~26k, 降低 -96.5%',
    'benchmark.scores.title': '分类平均评分',
    'benchmark.scores.bleu': 'BLEU 评分',
    'benchmark.scores.f1': 'F1 评分',
    'benchmark.scores.llm': 'LLM 评分',
    'benchmark.scores.count': '数量',
    'benchmark.scores.category': '类别',
    'benchmark.scores.description': '描述',
    'benchmark.scores.viewDetails': '了解如何运行压测',
    'benchmark.tokens.title': '评估期间 Token 使用情况',
    'benchmark.tokens.prompt': '提示词 Token',
    'benchmark.tokens.completion': '完成 Token',
    'benchmark.tokens.total': '总 Token',
    'benchmark.tokens.cached': '缓存 Token',
    'category.1.name': '单轮 (Single-Hop)',
    'category.1.desc': '询问单段对话中直接提到的特定事实的问题。',
    'category.2.name': '跨轮 (Temporal)',
    'category.2.desc': '可以通过时间推理和捕获对话中与时间相关的数据线索来回答的问题。',
    'category.3.name': '多跳 (Multi-Hop)',
    'category.3.desc': '需要综合多个会话信息的问题。',
    'category.4.name': '开放 (Open-Domain)',
    'category.4.desc': '可以通过整合说话者提供的信息与外部知识（如常识或世界事实）来回答的问题。',
    'category.5.name': '对抗性',
    'category.5.desc': '这些问题旨在诱使代理提供错误答案，期望代理能正确识别它们为无法回答的问题。',
  },
};

const comparisonMetrics = [
  {
    labelKey: 'benchmark.metrics.llmScore',
    detailKey: 'benchmark.metrics.accuracy',
    value: '87.79%',
    baselineKey: 'benchmark.metrics.llmScore.baseline',
    cardClass: 'metricCardBlue',
  },
  {
    labelKey: 'benchmark.metrics.retrievalP95',
    detailKey: 'benchmark.metrics.latency',
    value: '1.44s',
    baselineKey: 'benchmark.metrics.retrievalP95.baseline',
    cardClass: 'metricCardGreen',
  },
  {
    labelKey: 'benchmark.metrics.tokenUsage',
    detailKey: 'benchmark.metrics.consumption',
    value: '~0.9k',
    baselineKey: 'benchmark.metrics.tokenUsage.baseline',
    cardClass: 'metricCardPurple',
  },
];

const categoryScores = [
  { 
    category: 1, 
    llm_score: 0.9078,
    count: 282,
    nameKey: 'category.1.name',
    descKey: 'category.1.desc',
  },
  { 
    category: 2, 
    llm_score: 0.8349,
    count: 321,
    nameKey: 'category.2.name',
    descKey: 'category.2.desc',
  },
  { 
    category: 3, 
    llm_score: 0.7396,
    count: 96,
    nameKey: 'category.3.name',
    descKey: 'category.3.desc',
  },
  { 
    category: 4, 
    llm_score: 0.9001,
    count: 841,
    nameKey: 'category.4.name',
    descKey: 'category.4.desc',
  },
];

function formatScore(num: number): string {
  return (num * 100).toFixed(2) + '%';
}

export default function BenchmarkPage() {
  const { i18n } = useDocusaurusContext();
  const isZh = i18n.currentLocale === 'zh';
  const t = (key: string) => translations[isZh ? 'zh' : 'en'][key] || key;

  return (
    <Layout title="Benchmark" description="PowerMem Performance Benchmarks">
      <div className={styles.benchmarkPage}>
        <div className="container margin-vert--lg">
          <div className={styles.header}>
            <Heading as="h1" className={styles.title}>
              {t('benchmark.title')}
            </Heading>
            <p className={styles.subtitle}>
              {t('benchmark.subtitle')}
            </p>
            <p className={styles.config}>
              {t('benchmark.config')}
            </p>
          </div>

          {/* PowerMem vs Full-Context */}
          <section className={styles.section}>
            <Heading as="h2" className={styles.sectionTitle}>
              {t('benchmark.comparison.title')}
            </Heading>
            <div className={styles.performanceGrid}>
              {comparisonMetrics.map((metric) => (
                <div
                  key={metric.labelKey}
                  className={`${styles.metricCard} ${styles[metric.cardClass]}`}
                >
                  <div className={styles.metricLabel}>{t(metric.labelKey)} {t(metric.detailKey)}</div>
                  <div className={styles.metricValue}>{metric.value}</div>
                  <div className={styles.metricDetail}>{t(metric.baselineKey)}</div>
                </div>
              ))}
            </div>
          </section>

          {/* Category Scores */}
          <section className={styles.section}>
            <Heading as="h2" className={styles.sectionTitle}>
              {t('benchmark.scores.title')}
            </Heading>
            <div className={styles.tableContainer}>
              <table className={styles.scoresTable}>
                <thead>
                  <tr>
                    <th>{t('benchmark.scores.category')}</th>
                    <th>{t('benchmark.scores.description')}</th>
                    <th>{t('benchmark.scores.llm')}</th>
                    <th>{t('benchmark.scores.count')}</th>
                  </tr>
                </thead>
                <tbody>
                  {categoryScores.map((score) => (
                    <tr key={score.category}>
                      <td className={styles.categoryCell}>
                        <div className={styles.categoryName}>
                          <span className={styles.categoryNumber}>{score.category}</span>
                          <span className={styles.categoryTitle}>{t(score.nameKey)}</span>
                        </div>
                      </td>
                      <td className={styles.descriptionCell}>{t(score.descKey)}</td>
                      <td className={styles.llmCell}>{formatScore(score.llm_score)}</td>
                      <td className={styles.countCell}>{score.count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className={styles.sectionFooter}>
              <Link
                to="/docs/benchmark/overview"
                className={styles.viewDetailsButton}
              >
                {t('benchmark.scores.viewDetails')} →
              </Link>
            </div>
          </section>
        </div>
      </div>
    </Layout>
  );
}

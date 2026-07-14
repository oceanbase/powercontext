import React from 'react';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import Heading from '@theme/Heading';
import DeveloperIcon from './icons/DeveloperIcon';
import MemoryIcon from './icons/MemoryIcon';
import UsersIcon from './icons/UsersIcon';
import MultimodalIcon from './icons/MultimodalIcon';
import DatabaseIcon from './icons/DatabaseIcon';
import styles from './styles.module.css';

const features = [
  {
    icon: DeveloperIcon,
    key: 'developer',
  },
  {
    icon: MemoryIcon,
    key: 'intelligent',
  },
  {
    icon: UsersIcon,
    key: 'multiAgent',
  },
  {
    icon: MultimodalIcon,
    key: 'multimodal',
  },
  {
    icon: DatabaseIcon,
    key: 'storage',
  },
];

const translations: Record<string, Record<string, string>> = {
  en: {
    'features.title': 'Core Features',
    'features.subtitle': 'Complete intelligent memory management solution for AI applications',
    'feature.developer.title': 'Developer Friendly',
    'feature.developer.en': 'Developer Friendly',
    'feature.developer.desc': 'Provides a simple Python SDK, automatically loads configuration from .env files, enabling developers to quickly integrate into existing projects. Also supports MCP Server and HTTP API Server integration methods',
    'feature.intelligent.title': 'Intelligent Memory Management',
    'feature.intelligent.en': 'Intelligent Memory Management',
    'feature.intelligent.desc': 'Automatically extracts key facts from conversations through LLM, intelligently detects duplicates, updates conflicting information, and merges related memories. Based on cognitive science, implements time-decay weighting to prioritize recent and relevant memories.',
    'feature.multiAgent.title': 'Multi-Agent Support',
    'feature.multiAgent.en': 'Multi-Agent Support',
    'feature.multiAgent.desc': 'Provides independent memory spaces for each agent, supports cross-agent memory sharing and collaboration, and enables flexible permission management through scope control.',
    'feature.multimodal.title': 'Multimodal Support',
    'feature.multimodal.en': 'Multimodal Support',
    'feature.multimodal.desc': 'Automatically converts images and audio to text descriptions for storage, supports retrieval of multimodal mixed content (text + image + audio), enabling AI systems to understand richer contextual information',
    'feature.storage.title': 'Deeply Optimized Data Storage',
    'feature.storage.en': 'Deeply Optimized Data Storage',
    'feature.storage.desc': 'Implements data partition management through sub stores with automatic query routing. Combines multi-channel recall capabilities of vector retrieval, full-text search, and graph retrieval for precise retrieval of complex memory relationships.',
  },
  zh: {
    'features.title': '核心特性',
    'features.subtitle': '为 AI 应用提供完整的智能记忆管理解决方案',
    'feature.developer.title': '开发者友好',
    'feature.developer.en': 'Developer Friendly',
    'feature.developer.desc': '提供简单的 Python SDK，自动从 .env 文件加载配置，使开发者能够快速集成到现有项目中。还支持 MCP Server 和 HTTP API Server 两种接入方式',
    'feature.intelligent.title': '智能记忆管理',
    'feature.intelligent.en': 'Intelligent Memory Management',
    'feature.intelligent.desc': '通过 LLM 自动从对话中提取关键事实，智能检测重复、更新冲突信息并合并相关记忆。基于认知科学，实现时间衰减加权，优先考虑最近和相关的记忆。',
    'feature.multiAgent.title': 'Multi-Agent 支持',
    'feature.multiAgent.en': 'Multi-Agent Support',
    'feature.multiAgent.desc': '为每个 Agent 提供独立的记忆空间，支持跨 Agent 记忆共享和协作，并通过范围控制实现灵活的权限管理',
    'feature.multimodal.title': '多模态支持',
    'feature.multimodal.en': 'Multimodal Support',
    'feature.multimodal.desc': '自动将图像和音频转换为文本描述进行存储，支持检索多模态混合内容（文本 + 图像 + 音频），使 AI 系统能够理解更丰富的上下文信息',
    'feature.storage.title': '深度优化的数据存储',
    'feature.storage.en': 'Deeply Optimized Data Storage',
    'feature.storage.desc': '通过子存储实现数据分区管理，支持自动查询路由。结合向量检索、全文搜索和图检索的多通道召回能力，精确检索复杂的记忆关系。',
  },
};

export default function Features() {
  const { i18n } = useDocusaurusContext();
  const isZh = i18n.currentLocale === 'zh';
  const t = (key: string) => {
    const lang = isZh ? 'zh' : 'en';
    return translations[lang][key] || key;
  };

  return (
    <section className={styles.features}>
      <div className="container">
        <div className={styles.header}>
          <Heading as="h2" className={styles.title}>
            {t('features.title')}
          </Heading>
          <p className={styles.subtitle}>
            {t('features.subtitle')}
          </p>
        </div>

        <div className={styles.grid}>
          {features.map((feature) => {
            const Icon = feature.icon;
            return (
              <article
                key={feature.key}
                className={styles.card}
              >
                <div className={`${styles.icon} ${styles[`icon-${feature.key}`]}`}>
                  <Icon className={styles.iconSvg} />
                </div>
                <Heading as="h3" className={styles.cardTitle}>
                  {t(`feature.${feature.key}.title`)}
                </Heading>
                <p className={styles.cardDesc}>
                  {t(`feature.${feature.key}.desc`)}
                </p>
              </article>
            );
          })}
        </div>
      </div>
    </section>
  );
}

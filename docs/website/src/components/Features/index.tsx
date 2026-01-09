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
    details: [
      'detail1',
    ],
    color: 'from-orange-500 to-orange-600',
  },
  {
    icon: MemoryIcon,
    key: 'intelligent',
    details: [
      'detail1',
      'detail2',
      'detail3',
    ],
    color: 'from-blue-500 to-blue-600',
  },
  {
    icon: UsersIcon,
    key: 'multiAgent',
    details: [
      'detail1',
      'detail2',
      'detail3',
      'detail4',
    ],
    color: 'from-purple-500 to-purple-600',
  },
  {
    icon: MultimodalIcon,
    key: 'multimodal',
    details: [
      'detail1',
    ],
    color: 'from-pink-500 to-pink-600',
  },
  {
    icon: DatabaseIcon,
    key: 'storage',
    details: [
      'detail1',
      'detail2',
      'detail3',
    ],
    color: 'from-green-500 to-green-600',
  },
];

const translations: Record<string, Record<string, string>> = {
  en: {
    'features.title': 'Core Features',
    'features.subtitle': 'Complete intelligent memory management solution for AI applications',
    'feature.developer.title': 'Developer Friendly',
    'feature.developer.en': 'Developer Friendly',
    'feature.developer.desc': 'Provides a simple Python SDK, automatically loads configuration from .env files, enabling developers to quickly integrate into existing projects. Also supports MCP Server and HTTP API Server integration methods',
    'feature.developer.detail1': 'Lightweight Integration',
    'feature.intelligent.title': 'Intelligent Memory Management',
    'feature.intelligent.en': 'Intelligent Memory Management',
    'feature.intelligent.desc': 'Automatically extracts key facts from conversations through LLM, intelligently detects duplicates, updates conflicting information, and merges related memories. Based on cognitive science, implements time-decay weighting to prioritize recent and relevant memories.',
    'feature.intelligent.detail1': 'Intelligent Memory Extraction',
    'feature.intelligent.detail2': 'Ebbinghaus Forgetting Curve',
    'feature.intelligent.detail3': 'Automatic Duplicate Detection',
    'feature.multiAgent.title': 'Multi-Agent Support',
    'feature.multiAgent.en': 'Multi-Agent Support',
    'feature.multiAgent.desc': 'Provides independent memory spaces for each agent, supports cross-agent memory sharing and collaboration, and enables flexible permission management through scope control',
    'feature.multiAgent.detail1': 'Agent Shared/Isolated Memory',
    'feature.multiAgent.detail2': 'Cross-Agent Collaboration',
    'feature.multiAgent.detail3': 'Flexible Permission Management',
    'feature.multiAgent.detail4': 'Scope Control',
    'feature.multimodal.title': 'Multimodal Support',
    'feature.multimodal.en': 'Multimodal Support',
    'feature.multimodal.desc': 'Automatically converts images and audio to text descriptions for storage, supports retrieval of multimodal mixed content (text + image + audio), enabling AI systems to understand richer contextual information',
    'feature.multimodal.detail1': 'Text, Image, and Audio Memory',
    'feature.storage.title': 'Deeply Optimized Data Storage',
    'feature.storage.en': 'Deeply Optimized Data Storage',
    'feature.storage.desc': 'Implements data partition management through sub stores with automatic query routing. Combines multi-channel recall capabilities of vector retrieval, full-text search, and graph retrieval for precise retrieval of complex memory relationships.',
    'feature.storage.detail1': 'Sub Stores Support',
    'feature.storage.detail2': 'Hybrid Retrieval',
    'feature.storage.detail3': 'Multi-Hop Graph Traversal',
  },
  zh: {
    'features.title': '核心特性',
    'features.subtitle': '为 AI 应用提供完整的智能内存管理解决方案',
    'feature.developer.title': '开发者友好',
    'feature.developer.en': 'Developer Friendly',
    'feature.developer.desc': '提供简单的 Python SDK，自动从 .env 文件加载配置，使开发者能够快速集成到现有项目中。还支持 MCP Server 和 HTTP API Server 两种接入方式',
    'feature.developer.detail1': '轻量级集成',
    'feature.intelligent.title': '智能内存管理',
    'feature.intelligent.en': 'Intelligent Memory Management',
    'feature.intelligent.desc': '通过 LLM 自动从对话中提取关键事实，智能检测重复、更新冲突信息并合并相关记忆。基于认知科学，实现时间衰减加权，优先考虑最近和相关的记忆。',
    'feature.intelligent.detail1': '智能记忆提取',
    'feature.intelligent.detail2': '艾宾浩斯遗忘曲线',
    'feature.intelligent.detail3': '自动重复检测',
    'feature.multiAgent.title': '多Agent支持',
    'feature.multiAgent.en': 'Multi-Agent Support',
    'feature.multiAgent.desc': '为每个代理提供独立的记忆空间，支持跨代理记忆共享和协作，并通过范围控制实现灵活的权限管理',
    'feature.multiAgent.detail1': '代理共享/隔离记忆',
    'feature.multiAgent.detail2': '跨代理协作',
    'feature.multiAgent.detail3': '灵活权限管理',
    'feature.multiAgent.detail4': '范围控制',
    'feature.multimodal.title': '多模态支持',
    'feature.multimodal.en': 'Multimodal Support',
    'feature.multimodal.desc': '自动将图像和音频转换为文本描述进行存储，支持检索多模态混合内容（文本 + 图像 + 音频），使 AI 系统能够理解更丰富的上下文信息',
    'feature.multimodal.detail1': '文本、图像和音频记忆',
    'feature.storage.title': '深度优化的数据存储',
    'feature.storage.en': 'Deeply Optimized Data Storage',
    'feature.storage.desc': '通过子存储实现数据分区管理，支持自动查询路由。结合向量检索、全文搜索和图检索的多通道召回能力，精确检索复杂的记忆关系。',
    'feature.storage.detail1': '子存储支持',
    'feature.storage.detail2': '混合检索',
    'feature.storage.detail3': '多跳图遍历',
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
          {features.map((feature, index) => {
            const Icon = feature.icon;
            return (
              <div
                key={feature.key}
                className={`${styles.card} fade-in-delay-${index + 1}`}
              >
                <div className={`${styles.icon} ${styles[`icon-${feature.key}`]}`}>
                  <Icon className={styles.iconSvg} />
                </div>
                <Heading as="h3" className={styles.cardTitle}>
                  {t(`feature.${feature.key}.title`)}
                </Heading>
                <p className={styles.cardEn}>{t(`feature.${feature.key}.en`)}</p>
                <p className={styles.cardDesc}>
                  {t(`feature.${feature.key}.desc`)}
                </p>
                <ul className={styles.details}>
                  {feature.details.map((detailKey) => (
                    <li key={detailKey}>
                      <span className={styles.bullet}>•</span>
                      {t(`feature.${feature.key}.${detailKey}`)}
                    </li>
                  ))}
                </ul>
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}

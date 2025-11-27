import React, { useState } from 'react';
import Link from '@docusaurus/Link';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import Heading from '@theme/Heading';
import {Highlight, themes} from 'prism-react-renderer';
import styles from './styles.module.css';

const codeExamples: Record<string, Record<string, string>> = {
  en: {
    install: 'pip install powermem',
    basicUsage: `from powermem import Memory, auto_config

# Load configuration (auto-loads from .env)
config = auto_config()
memory = Memory(config=config)

# Add memory
memory.add("User likes coffee", user_id="user123")

# Search memories
results = memory.search("user preferences", user_id="user123")`,
    multiAgent: `from powermem import Memory, auto_config

config = auto_config()

# Create memory instances for different agents
support_agent = Memory(config=config, agent_id="support_agent")
sales_agent = Memory(config=config, agent_id="sales_agent")
tech_agent = Memory(config=config, agent_id="tech_agent")

# Add agent-specific memories
support_memory.add("Customer prefers email support", user_id="customer123")`,
  },
  zh: {
    install: 'pip install powermem',
    basicUsage: `from powermem import Memory, auto_config

# 自动从 .env 加载配置
config = auto_config()
memory = Memory(config=config)

# 添加记忆
memory.add("用户喜欢咖啡", user_id="user123")

# 搜索记忆
memories = memory.search("用户偏好", user_id="user123")`,
    multiAgent: `# 为不同代理创建独立的记忆空间
from powermem import Memory, auto_config

config = auto_config()
# Create memory instances for different agents
support_agent = Memory(config=config, agent_id="support_agent")
sales_agent = Memory(config=config, agent_id="sales_agent")
tech_agent = Memory(config=config, agent_id="tech_agent")

# 添加代理特定记忆
support_memory.add("客户偏好邮件支持", user_id="customer123")`,
  },
};

const examples = [
  { key: 'install', titleKey: 'install' },
  { key: 'basicUsage', titleKey: 'basicUsage' },
  { key: 'multiAgent', titleKey: 'multiAgent' },
];

const translations: Record<string, Record<string, string>> = {
  en: {
    'quickStart.title': 'Get Started in Minutes',
    'quickStart.subtitle': 'Simple installation, start building your AI applications immediately',
    'quickStart.viewDocs': 'View Full Documentation',
    'install': 'Install',
    'basicUsage': 'Basic Usage',
    'multiAgent': 'Multi-Agent Scenario',
  },
  zh: {
    'quickStart.title': '几分钟内开始使用',
    'quickStart.subtitle': '简单安装，立即上手构建您的 AI 应用',
    'quickStart.viewDocs': '查看完整文档',
    'install': '安装',
    'basicUsage': '基础使用',
    'multiAgent': '多Agent场景',
  },
};

export default function QuickStart() {
  const { i18n } = useDocusaurusContext();
  const isZh = i18n.currentLocale === 'zh';
  const t = (key: string) => translations[isZh ? 'zh' : 'en'][key] || key;
  const [copiedIndex, setCopiedIndex] = useState<number | null>(null);

  const copyToClipboard = async (text: string, index: number) => {
    try {
      // Try modern clipboard API first
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(text);
        setCopiedIndex(index);
        setTimeout(() => setCopiedIndex(null), 2000);
      } else {
        // Fallback for older browsers or non-HTTPS environments
        const textArea = document.createElement('textarea');
        textArea.value = text;
        textArea.style.position = 'fixed';
        textArea.style.left = '-999999px';
        textArea.style.top = '-999999px';
        document.body.appendChild(textArea);
        textArea.focus();
        textArea.select();
        
        try {
          const successful = document.execCommand('copy');
          if (successful) {
            setCopiedIndex(index);
            setTimeout(() => setCopiedIndex(null), 2000);
          }
        } catch (err) {
          console.error('Fallback copy failed:', err);
        } finally {
          document.body.removeChild(textArea);
        }
      }
    } catch (err) {
      console.error('Copy to clipboard failed:', err);
      // Still show feedback even if copy fails
      setCopiedIndex(index);
      setTimeout(() => setCopiedIndex(null), 2000);
    }
  };

  return (
    <section className={styles.quickStart}>
      <div className="container">
        <div className={styles.header}>
          <Heading as="h2" className={styles.title}>
            {t('quickStart.title')}
          </Heading>
          <p className={styles.subtitle}>
            {t('quickStart.subtitle')}
          </p>
        </div>

        <div className={styles.grid}>
          {examples.map((example, index) => {
            const code = codeExamples[isZh ? 'zh' : 'en'][example.key];
            return (
              <div
                key={example.key}
                className={`${styles.card} fade-in-delay-${index + 1}`}
              >
                <div className={styles.cardHeader}>
                  <Heading as="h3" className={styles.cardTitle}>
                    {t(example.titleKey)}
                  </Heading>
                  <button
                    className={styles.copyButton}
                    onClick={() => copyToClipboard(code, index)}
                    aria-label="Copy code"
                  >
                    {copiedIndex === index ? '✓' : '📋'}
                  </button>
                </div>
                <div className={styles.codeBlock}>
                  <Highlight
                    theme={themes.vsDark}
                    code={code}
                    language={example.key === 'install' ? 'bash' : 'python'}
                  >
                    {({className, style, tokens, getLineProps, getTokenProps}) => (
                      <pre className={`${className} ${styles.codePre}`} style={style}>
                        {tokens.map((line, i) => (
                          <div key={i} {...getLineProps({line})} className={styles.codeLine}>
                            <span className={styles.lineContent}>
                              {line.map((token, key) => (
                                <span key={key} {...getTokenProps({token})} />
                              ))}
                            </span>
                          </div>
                        ))}
                      </pre>
                    )}
                  </Highlight>
                </div>
              </div>
            );
          })}
        </div>

        <div className={styles.footer}>
          <Link
            to="/docs"
            className="button button--primary button--lg"
          >
            {t('quickStart.viewDocs')} →
          </Link>
        </div>
      </div>
    </section>
  );
}

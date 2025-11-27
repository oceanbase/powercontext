import React, { useEffect, useState } from 'react';
import Link from '@docusaurus/Link';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import useBaseUrl from '@docusaurus/useBaseUrl';
import Heading from '@theme/Heading';
import CodeIcon from './icons/CodeIcon';
import {Highlight, themes} from 'prism-react-renderer';
import styles from './styles.module.css';

// GitHub Stars Hook
function useGitHubStars() {
  const [stars, setStars] = useState<number | null>(null);

  useEffect(() => {
    fetch('https://api.github.com/repos/oceanbase/powermem')
      .then((res) => res.json())
      .then((data) => {
        if (data.stargazers_count && data.stargazers_count > 1000) {
          setStars(data.stargazers_count);
        }
      })
      .catch(() => {
        // Silently fail
      });
  }, []);

  return stars;
}

export default function Hero() {
  const { siteConfig, i18n } = useDocusaurusContext();
  const stars = useGitHubStars();
  const isZh = i18n.currentLocale === 'zh';

  const codeExample = isZh
    ? `from powermem import Memory, auto_config

# 自动从 .env 加载配置
config = auto_config()
memory = Memory(config=config)

# 添加记忆
memory.add("用户喜欢咖啡", user_id="user123")

# 搜索记忆
memories = memory.search("用户偏好", user_id="user123")`
    : `from powermem import Memory, auto_config

# Auto-load from .env
config = auto_config()
memory = Memory(config=config)

# Add memory
memory.add("User likes coffee", user_id="user123")

# Search memories
memories = memory.search("user preferences", user_id="user123")`;

  return (
    <section className={styles.hero}>
      {/* Background Gradient */}
      <div className={styles.heroBackground} />

      {/* Grid Background */}
      <div className={styles.gridBackground} />

      {/* Animated Background Blobs */}
      <div className={styles.blobContainer}>
        <div className={`${styles.blob} ${styles.blob1}`} />
        <div className={`${styles.blob} ${styles.blob2}`} />
        <div className={`${styles.blob} ${styles.blob3}`} />
      </div>

      {/* Content */}
      <div className={styles.heroContent}>
        <div className={`${styles.heroText} fade-in`}>
          <Heading as="h1" className={styles.heroTitle}>
            {isZh ? (
              <>
                为 AI 应用构建
                <br />
                <span className={styles.heroTitleHighlight}>
                  持久<span className={styles.heroTitleMemory}>记忆层</span>
                </span>
              </>
            ) : (
              <>
                Build Persistent <span className={styles.heroTitleMemory}>Memory</span>
                <br />
                <span className={styles.heroTitleHighlight}>
                  for AI Applications
                </span>
              </>
            )}
          </Heading>

          <p className={styles.heroSubtitle}>
            {isZh ? '几分钟上手，轻松扩展到百万级' : 'Get started in minutes, scale to millions'}
          </p>

          {/* CTA Buttons */}
          <div className={styles.heroButtons}>
            <Link
              className="button button--primary button--lg"
              to="/docs/guides/getting_started"
            >
              {isZh ? '开始使用' : 'Get Started'}
              <span className={styles.buttonArrow}>→</span>
            </Link>
            <Link
              className="button button--secondary button--lg"
              href="https://github.com/oceanbase/powermem"
            >
              <CodeIcon className={styles.buttonIcon} />
              {isZh ? '查看代码' : 'View Code'}
              {stars !== null && stars > 1000 && (
                <span className={styles.stars}>
                  ⭐ {stars.toLocaleString()}
                </span>
              )}
            </Link>
          </div>
        </div>

        {/* Code Preview */}
        <div className={`${styles.codePreview} fade-in-delay-3`}>
          <div className={styles.codeHeader}>
            <div className={styles.codeDots}>
              <span className={styles.codeDot} />
              <span className={styles.codeDot} />
              <span className={styles.codeDot} />
            </div>
            <span className={styles.codeLanguage}>Python</span>
          </div>
          <div className={styles.codeBlock}>
            <Highlight
              theme={themes.vsDark}
              code={codeExample}
              language="python"
            >
              {({className, style, tokens, getLineProps, getTokenProps}) => (
                <pre className={`${className} ${styles.codePre}`} style={style}>
                  {tokens.map((line, i) => (
                    <div key={i} {...getLineProps({line})} className={styles.codeLine}>
                      <span className={styles.lineNumber}>{i + 1}</span>
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
      </div>
    </section>
  );
}

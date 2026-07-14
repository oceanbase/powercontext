import React from 'react';
import Link from '@docusaurus/Link';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import {useColorMode} from '@docusaurus/theme-common';
import Heading from '@theme/Heading';
import {Highlight, themes} from 'prism-react-renderer';
import CodeIcon from './icons/CodeIcon';
import {localizedPath} from '../../utils/localizedPath';
import styles from './styles.module.css';

export default function Hero() {
  const {i18n} = useDocusaurusContext();
  const {colorMode} = useColorMode();
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
      <div className={styles.heroContent}>
        <div className={styles.heroText}>
          <Heading as="h1" className={styles.heroTitle}>
            {isZh ? (
              <>为 AI 应用构建<span className={styles.heroTitleMemory}>持久记忆层</span></>
            ) : (
              <>Build Persistent <span className={styles.heroTitleMemory}>Memory</span> for AI Applications</>
            )}
          </Heading>
          <p className={styles.heroSubtitle}>
            {isZh ? '几分钟上手，轻松扩展到百万级记忆' : 'Get started in minutes, scale to millions'}
          </p>
          <div className={styles.heroButtons}>
            <Link
              className="button button--primary button--lg"
              to={localizedPath('/docs/guides/getting_started', isZh)}
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
            </Link>
          </div>
        </div>

        <div className={styles.codePreview}>
          <div className={styles.codeHeader}>
            <span>Python</span>
            <span>powermem.py</span>
          </div>
          <div className={styles.codeBlock}>
            <Highlight
              theme={colorMode === 'dark' ? themes.vsDark : themes.github}
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

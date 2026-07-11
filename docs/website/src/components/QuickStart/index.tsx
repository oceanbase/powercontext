import React from 'react';
import Link from '@docusaurus/Link';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import {useColorMode} from '@docusaurus/theme-common';
import Heading from '@theme/Heading';
import {Highlight, themes} from 'prism-react-renderer';
import {localizedPath} from '../../utils/localizedPath';
import styles from './styles.module.css';

export default function QuickStart() {
  const {i18n} = useDocusaurusContext();
  const {colorMode} = useColorMode();
  const isZh = i18n.currentLocale === 'zh';

  const code = isZh
    ? `from powermem import Memory, auto_config

memory = Memory(config=auto_config())
memory.add("用户喜欢咖啡", user_id="u123")

results = memory.search("咖啡", user_id="u123")`
    : `from powermem import Memory, auto_config

memory = Memory(config=auto_config())
memory.add("User likes coffee", user_id="u123")

results = memory.search("coffee", user_id="u123")`;

  return (
    <section className={styles.quickStart}>
      <div className={styles.inner}>
        <div className={styles.copy}>
          <Heading as="h2">{isZh ? '几分钟内开始使用' : 'Start with one command.'}</Heading>
          <p>
            {isZh
              ? '安装 PowerMem，添加第一条记忆，然后按用户范围检索相关上下文。'
              : 'Install PowerMem, write the first memory, and retrieve relevant context by user scope.'}
          </p>
          <div className={styles.install}>
            <span aria-hidden="true">$</span>
            <code>pip install powermem</code>
          </div>
          <Link
            to={localizedPath('/docs/guides/getting_started', isZh)}
            className="button button--primary"
          >
            {isZh ? '查看完整文档' : 'Read the quick start'} →
          </Link>
        </div>

        <div className={styles.example}>
          <div className={styles.exampleHeader}>
            <span>Python</span>
            <span>quickstart.py</span>
          </div>
          <Highlight
            theme={colorMode === 'dark' ? themes.vsDark : themes.github}
            code={code}
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
    </section>
  );
}


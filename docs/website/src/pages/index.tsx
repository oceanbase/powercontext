import React from 'react';
import Layout from '@theme/Layout';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import Hero from '@site/src/components/Hero';
import Features from '@site/src/components/Features';
import ValueProps1 from '@site/src/components/ValueProps1';
import QuickStart from '@site/src/components/QuickStart';
// import AnimatedBackground from '@site/src/components/AnimatedBackground';
import MouseGlow from '@site/src/components/MouseGlow';
import GridBackground from '@site/src/components/GridBackground';

export default function Home(): React.JSX.Element {
  const { siteConfig } = useDocusaurusContext();
  return (
    <Layout
      title={siteConfig.title}
      description={siteConfig.tagline}>
      {/* Background Effects */}
      <GridBackground />
      {/* <AnimatedBackground /> */}
      <MouseGlow />
      <main>
        <Hero />
        <Features />
        <ValueProps1 />
        <QuickStart />
      </main>
    </Layout>
  );
}

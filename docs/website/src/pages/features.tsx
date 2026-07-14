import React from 'react';
import Layout from '@theme/Layout';
import Heading from '@theme/Heading';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import styles from './features.module.css';

type FeatureKey = 'realtime' | 'analytics' | 'security' | 'edge' | 'federated';
type Translate = (key: string) => string;

const features: FeatureKey[] = ['realtime', 'analytics', 'security', 'edge', 'federated'];

const translations: Record<string, Record<string, string>> = {
  en: {
    'features.title': 'Upcoming Features',
    'features.subtitle': 'Exciting features we\'re planning for PowerMem\'s future',
    'feature.realtime.title': 'Real-time Sync & Collaboration',
    'feature.realtime.desc': 'Enable seamless memory synchronization across multiple devices and users, with real-time collaboration capabilities for team-based AI applications.',
    'feature.realtime.detail1': 'Multi-device synchronization',
    'feature.realtime.detail2': 'Real-time collaboration',
    'feature.realtime.detail3': 'Conflict resolution',
    'feature.realtime.detail4': 'Offline-first architecture',
    'feature.analytics.title': 'Advanced Analytics & Insights',
    'feature.analytics.desc': 'Comprehensive analytics dashboard for memory usage patterns, performance metrics, and intelligent recommendations for optimization.',
    'feature.analytics.detail1': 'Memory usage analytics',
    'feature.analytics.detail2': 'Performance monitoring',
    'feature.analytics.detail3': 'Intelligent recommendations',
    'feature.analytics.detail4': 'Customizable dashboards',
    'feature.security.title': 'Enterprise Security & Compliance',
    'feature.security.desc': 'Enterprise-grade security features including end-to-end encryption, audit logs, compliance controls, and advanced access management.',
    'feature.security.detail1': 'End-to-end encryption',
    'feature.security.detail2': 'Audit logging',
    'feature.security.detail3': 'Compliance controls',
    'feature.security.detail4': 'Advanced access management',
    'feature.edge.title': 'Edge Computing Support',
    'feature.edge.desc': 'Deploy PowerMem on edge devices with offline capabilities, reduced latency, and optimized resource usage for distributed AI applications.',
    'feature.edge.detail1': 'Edge device deployment',
    'feature.edge.detail2': 'Offline capabilities',
    'feature.edge.detail3': 'Reduced latency',
    'feature.edge.detail4': 'Resource optimization',
    'feature.federated.title': 'Federated Learning Integration',
    'feature.federated.desc': 'Enable privacy-preserving distributed learning across multiple organizations and devices without centralizing sensitive data.',
    'feature.federated.detail1': 'Privacy-preserving learning',
    'feature.federated.detail2': 'Distributed model training',
    'feature.federated.detail3': 'Cross-organization collaboration',
    'feature.federated.detail4': 'Secure aggregation protocols',
  },
  zh: {
    'features.title': '即将推出的特性',
    'features.subtitle': '我们为 PowerMem 未来规划的前沿功能',
    'features.description': 'PowerMem 即将推出的特性',
    'feature.realtime.title': '实时同步与协作',
    'feature.realtime.desc': '支持多设备、多用户的无缝记忆同步，为团队协作的 AI 应用提供实时协作能力。',
    'feature.realtime.detail1': '多设备同步',
    'feature.realtime.detail2': '实时协作',
    'feature.realtime.detail3': '冲突解决',
    'feature.realtime.detail4': '离线优先架构',
    'feature.analytics.title': '高级分析与洞察',
    'feature.analytics.desc': '全面的分析仪表板，提供记忆使用模式、性能指标分析，以及智能优化建议。',
    'feature.analytics.detail1': '记忆使用分析',
    'feature.analytics.detail2': '性能监控',
    'feature.analytics.detail3': '智能推荐',
    'feature.analytics.detail4': '可定制仪表板',
    'feature.security.title': '企业级安全与合规',
    'feature.security.desc': '企业级安全功能，包括端到端加密、审计日志、合规控制和高级访问管理。',
    'feature.security.detail1': '端到端加密',
    'feature.security.detail2': '审计日志',
    'feature.security.detail3': '合规控制',
    'feature.security.detail4': '高级访问管理',
    'feature.edge.title': '边缘计算支持',
    'feature.edge.desc': '在边缘设备上部署 PowerMem，支持离线能力、降低延迟，为分布式 AI 应用优化资源使用。',
    'feature.edge.detail1': '边缘设备部署',
    'feature.edge.detail2': '离线能力',
    'feature.edge.detail3': '降低延迟',
    'feature.edge.detail4': '资源优化',
    'feature.federated.title': '联邦学习集成',
    'feature.federated.desc': '支持跨多个组织和设备的隐私保护分布式学习，无需集中敏感数据。',
    'feature.federated.detail1': '隐私保护学习',
    'feature.federated.detail2': '分布式模型训练',
    'feature.federated.detail3': '跨组织协作',
    'feature.federated.detail4': '安全聚合协议',
  },
};

function SyncDiagram() {
  return (
    <svg className={styles.diagram} viewBox="0 0 680 380" role="presentation">
      <path className={styles.diagramMutedLine} d="M204 190h64M412 142h45M412 238h45" strokeDasharray="5 7" />
      <rect className={styles.diagramAccent} x="232" y="185" width="9" height="9" rx="2" />
      <rect className={styles.diagramAccent} x="430" y="137" width="9" height="9" rx="2" />
      <rect className={styles.diagramAccent} x="430" y="233" width="9" height="9" rx="2" />

      <rect className={styles.diagramSurfaceStrong} x="34" y="44" width="170" height="292" rx="18" />
      <rect className={styles.diagramSurfaceMuted} x="49" y="71" width="140" height="235" rx="10" />
      <path className={styles.diagramMutedLine} d="M92 58h54" />
      <circle className={styles.diagramAccent} cx="73" cy="104" r="5" />
      <path className={styles.diagramLineStrong} d="M90 100h61M90 110h39" />
      <rect className={styles.diagramSurface} x="64" y="132" width="110" height="46" rx="7" />
      <circle className={styles.diagramAccentSoft} cx="83" cy="155" r="9" />
      <path className={styles.diagramMutedLine} d="M100 149h55M100 160h37" />
      <rect className={styles.diagramSurface} x="64" y="190" width="110" height="46" rx="7" />
      <circle className={styles.diagramAccentSoft} cx="83" cy="213" r="9" />
      <path className={styles.diagramMutedLine} d="M100 207h55M100 218h44" />
      <rect className={styles.diagramSurface} x="64" y="248" width="110" height="42" rx="7" />
      <circle className={styles.diagramAccentSoft} cx="83" cy="269" r="9" />
      <path className={styles.diagramMutedLine} d="M100 263h48M100 274h31" />
      <circle className={styles.diagramLine} cx="119" cy="321" r="5" />

      <rect className={styles.diagramSurfaceStrong} x="268" y="104" width="144" height="172" rx="12" />
      <path className={styles.diagramLine} d="M268 136h144" />
      <circle className={styles.diagramAccent} cx="287" cy="120" r="4" />
      <text className={styles.diagramTechnicalLabel} x="300" y="124">Memory sync</text>
      <rect className={styles.diagramSurfaceMuted} x="286" y="151" width="108" height="32" rx="6" />
      <circle className={styles.diagramAccentSoft} cx="302" cy="167" r="7" />
      <path className={styles.diagramMutedLine} d="M315 162h64M315 172h43" />
      <rect className={styles.diagramSurfaceMuted} x="286" y="192" width="108" height="32" rx="6" />
      <circle className={styles.diagramAccentSoft} cx="302" cy="208" r="7" />
      <path className={styles.diagramMutedLine} d="M315 203h64M315 213h50" />

      <rect className={styles.diagramSurfaceStrong} x="457" y="45" width="189" height="184" rx="12" />
      <path className={styles.diagramLine} d="M457 77h189" />
      <circle className={styles.diagramAccent} cx="478" cy="61" r="4" />
      <circle className={styles.diagramLine} cx="493" cy="61" r="4" />
      <circle className={styles.diagramLine} cx="508" cy="61" r="4" />
      <rect className={styles.diagramAccentSoft} x="477" y="96" width="65" height="48" rx="7" />
      <path className={styles.diagramAccentLine} d="M491 126l12-13 10 8 16-17" />
      <rect className={styles.diagramSurfaceMuted} x="554" y="96" width="72" height="48" rx="7" />
      <path className={styles.diagramMutedLine} d="M568 111h42M568 122h31M568 133h22" />
      <rect className={styles.diagramSurfaceMuted} x="477" y="157" width="149" height="49" rx="7" />
      <circle className={styles.diagramAccent} cx="495" cy="181" r="5" />
      <path className={styles.diagramMutedLine} d="M510 174h91M510 186h65" />
      <path className={styles.diagramLineStrong} d="M526 229v15h51v-15M509 244h85" />

      <rect className={styles.diagramSurfaceStrong} x="470" y="271" width="151" height="72" rx="12" />
      <path className={styles.diagramLine} d="M470 293h151" />
      <circle className={styles.diagramAccent} cx="487" cy="282" r="3.5" />
      <rect className={styles.diagramAccentSoft} x="486" y="306" width="34" height="22" rx="5" />
      <path className={styles.diagramMutedLine} d="M532 310h70M532 321h46" />
    </svg>
  );
}

function AnalyticsDiagram() {
  return (
    <svg className={styles.diagram} viewBox="0 0 680 380" role="presentation">
      <rect className={styles.diagramSurfaceStrong} x="34" y="40" width="612" height="300" rx="14" />
      <path className={styles.diagramLine} d="M34 78h612M146 78v262" />
      <circle className={styles.diagramAccent} cx="55" cy="59" r="4" />
      <circle className={styles.diagramLine} cx="70" cy="59" r="4" />
      <circle className={styles.diagramLine} cx="85" cy="59" r="4" />

      <rect className={styles.diagramAccentSoft} x="56" y="102" width="68" height="34" rx="6" />
      <path className={styles.diagramAccentLine} d="M69 122h14l7-11 9 16 7-9h7" />
      <path className={styles.diagramMutedLine} d="M57 160h66M57 184h49M57 208h58M57 286h66M57 310h45" />
      <circle className={styles.diagramLine} cx="68" cy="249" r="10" />
      <path className={styles.diagramMutedLine} d="M85 245h38M85 254h25" />

      <rect className={styles.diagramSurfaceMuted} x="169" y="101" width="134" height="62" rx="8" />
      <circle className={styles.diagramAccentSoft} cx="191" cy="123" r="9" />
      <path className={styles.diagramAccentLine} d="M187 123h8M191 119v8" />
      <path className={styles.diagramMutedLine} d="M209 116h61M209 128h38" />
      <rect className={styles.diagramAccent} x="209" y="141" width="72" height="5" rx="2.5" />

      <rect className={styles.diagramSurfaceMuted} x="316" y="101" width="134" height="62" rx="8" />
      <path className={styles.diagramMutedLine} d="M337 116h74M337 128h49" />
      <path className={styles.diagramAccentLine} d="m337 145 19-12 17 7 20-18 21 5 18-13" />
      <text className={styles.diagramTechnicalLabel} x="411" y="152">24h</text>

      <rect className={styles.diagramSurfaceMuted} x="463" y="101" width="158" height="62" rx="8" />
      <circle className={styles.diagramAccent} cx="486" cy="126" r="12" />
      <path className={styles.diagramCheck} d="m480 126 4 4 8-9" />
      <path className={styles.diagramMutedLine} d="M506 116h91M506 128h63M506 140h77" />

      <rect className={styles.diagramSurfaceMuted} x="169" y="181" width="282" height="132" rx="9" />
      <path className={styles.diagramMutedLine} d="M192 205h231M192 234h231M192 263h231M192 292h231" />
      <path className={styles.diagramMutedLine} d="M214 203v90M263 203v90M312 203v90M361 203v90M410 203v90" />
      <path className={styles.diagramAccentSoftFill} d="m192 276 44-30 41 13 43-45 42 17 61-34v96H192Z" />
      <path className={styles.diagramAccentLine} d="m192 276 44-30 41 13 43-45 42 17 61-34" />
      <circle className={styles.diagramAccent} cx="236" cy="246" r="4" />
      <circle className={styles.diagramAccent} cx="320" cy="214" r="4" />
      <circle className={styles.diagramAccent} cx="423" cy="197" r="4" />

      <rect className={styles.diagramSurfaceMuted} x="466" y="181" width="155" height="132" rx="9" />
      <path className={styles.diagramMutedLine} d="M486 205h74M486 224h115M486 243h91" />
      <rect className={styles.diagramAccentSoft} x="486" y="265" width="24" height="28" rx="4" />
      <rect className={styles.diagramAccentSoft} x="519" y="250" width="24" height="43" rx="4" />
      <rect className={styles.diagramAccentSoft} x="552" y="231" width="24" height="62" rx="4" />
      <rect className={styles.diagramAccent} x="585" y="213" width="16" height="80" rx="4" />
    </svg>
  );
}

function SecurityDiagram() {
  return (
    <svg className={styles.diagram} viewBox="0 0 680 380" role="presentation">
      <rect className={styles.diagramSurfaceStrong} x="35" y="42" width="610" height="296" rx="14" />
      <path className={styles.diagramLine} d="M35 80h610" />
      <circle className={styles.diagramAccent} cx="56" cy="61" r="4" />
      <circle className={styles.diagramLine} cx="71" cy="61" r="4" />
      <circle className={styles.diagramLine} cx="86" cy="61" r="4" />

      <rect className={styles.diagramSurfaceMuted} x="58" y="102" width="257" height="211" rx="10" />
      <circle className={styles.diagramSurfaceStrong} cx="82" cy="136" r="13" />
      <circle className={styles.diagramSurfaceStrong} cx="82" cy="201" r="13" />
      <circle className={styles.diagramSurfaceStrong} cx="82" cy="266" r="13" />
      <path className={styles.diagramMutedLine} d="M95 136h21M95 201h21M95 266h21" />
      <circle className={styles.diagramAccent} cx="82" cy="136" r="4" />
      <circle className={styles.diagramAccent} cx="82" cy="201" r="4" />
      <circle className={styles.diagramAccent} cx="82" cy="266" r="4" />
      <rect className={styles.diagramSurfaceStrong} x="116" y="119" width="83" height="164" rx="9" />
      <path className={styles.diagramLine} d="M116 153h83" />
      <text className={styles.diagramTechnicalLabelAccent} x="136" y="140">Access</text>
      <circle className={styles.diagramAccent} cx="135" cy="178" r="7" />
      <path className={styles.diagramCheck} d="m131 178 3 3 5-6" />
      <path className={styles.diagramMutedLine} d="M151 174h29M151 183h19" />
      <circle className={styles.diagramAccent} cx="135" cy="219" r="7" />
      <path className={styles.diagramCheck} d="m131 219 3 3 5-6" />
      <path className={styles.diagramMutedLine} d="M151 215h29M151 224h23" />
      <circle className={styles.diagramAccent} cx="135" cy="260" r="7" />
      <path className={styles.diagramCheck} d="m131 260 3 3 5-6" />
      <path className={styles.diagramMutedLine} d="M151 256h29M151 265h16" />
      <path className={styles.diagramAccentLine} d="M199 201h21m-8-7 8 7-8 7" />
      <rect className={styles.diagramAccentSoft} x="220" y="137" width="75" height="128" rx="10" />
      <ellipse className={styles.diagramSurface} cx="257.5" cy="171" rx="24" ry="8" />
      <path className={styles.diagramLineStrong} d="M233.5 171v38c0 5 11 9 24 9s24-4 24-9v-38M233.5 190c0 5 11 9 24 9s24-4 24-9" />
      <rect className={styles.diagramAccent} x="247" y="224" width="21" height="23" rx="5" />
      <path className={styles.diagramSurfaceStroke} d="M252 224v-7a6 6 0 0 1 12 0v7" />
      <text className={styles.diagramTechnicalLabelAccent} x="229" y="258">Encrypted</text>

      <rect className={styles.diagramSurfaceMuted} x="337" y="102" width="285" height="211" rx="10" />
      <path className={styles.diagramLine} d="M337 144h285" />
      <path className={styles.diagramMutedLine} d="M360 122h83M548 122h50" />
      <circle className={styles.diagramAccent} cx="365" cy="169" r="9" />
      <path className={styles.diagramCheck} d="m360 169 3 3 6-7" />
      <path className={styles.diagramLineStrong} d="M385 162h93M385 174h58" />
      <rect className={styles.diagramSurface} x="531" y="158" width="67" height="22" rx="11" />
      <path className={styles.diagramMutedLine} d="M546 169h36" />
      <circle className={styles.diagramAccent} cx="365" cy="214" r="9" />
      <path className={styles.diagramCheck} d="m360 214 3 3 6-7" />
      <path className={styles.diagramLineStrong} d="M385 207h118M385 219h78" />
      <rect className={styles.diagramSurface} x="531" y="203" width="67" height="22" rx="11" />
      <path className={styles.diagramMutedLine} d="M546 214h36" />
      <circle className={styles.diagramAccent} cx="365" cy="259" r="9" />
      <path className={styles.diagramCheck} d="m360 259 3 3 6-7" />
      <path className={styles.diagramLineStrong} d="M385 252h104M385 264h69" />
      <rect className={styles.diagramAccentSoft} x="531" y="248" width="67" height="22" rx="11" />
      <path className={styles.diagramAccentLine} d="M546 259h36" />
      <path className={styles.diagramMutedLine} d="M359 290h239" />
    </svg>
  );
}

function EdgeDiagram() {
  return (
    <svg className={styles.diagram} viewBox="0 0 680 380" role="presentation">
      <path className={styles.diagramMutedLine} d="M207 111 287 166M473 111l-80 55M207 275l80-55M473 275l-80-55" strokeDasharray="6 7" />
      <rect className={styles.diagramAccent} x="243" y="132" width="9" height="9" rx="2" />
      <rect className={styles.diagramAccent} x="428" y="132" width="9" height="9" rx="2" />
      <rect className={styles.diagramAccent} x="243" y="246" width="9" height="9" rx="2" />
      <rect className={styles.diagramAccent} x="428" y="246" width="9" height="9" rx="2" />

      <rect className={styles.diagramAccentSoft} x="45" y="52" width="162" height="120" rx="12" />
      <path className={styles.diagramLine} d="M45 82h162" />
      <circle className={styles.diagramAccent} cx="64" cy="67" r="4" />
      <text className={styles.diagramTechnicalLabel} x="76" y="71">EDGE</text>
      <rect className={styles.diagramSurfaceMuted} x="66" y="101" width="47" height="43" rx="7" />
      <circle className={styles.diagramLineStrong} cx="89.5" cy="122.5" r="10" />
      <circle className={styles.diagramAccent} cx="89.5" cy="122.5" r="3" />
      <path className={styles.diagramMutedLine} d="M128 104h57M128 117h42M128 139h57" />
      <rect className={styles.diagramAccent} x="128" y="135" width="35" height="4" rx="2" />

      <rect className={styles.diagramAccentSoft} x="473" y="52" width="162" height="120" rx="12" />
      <path className={styles.diagramLine} d="M473 82h162" />
      <circle className={styles.diagramAccent} cx="492" cy="67" r="4" />
      <text className={styles.diagramTechnicalLabel} x="504" y="71">EDGE</text>
      <rect className={styles.diagramSurfaceMuted} x="495" y="99" width="44" height="48" rx="6" />
      <path className={styles.diagramLineStrong} d="M506 111h22M506 122h22M506 133h22" />
      <circle className={styles.diagramAccent} cx="504" cy="111" r="2" />
      <circle className={styles.diagramAccent} cx="504" cy="122" r="2" />
      <circle className={styles.diagramAccent} cx="504" cy="133" r="2" />
      <path className={styles.diagramMutedLine} d="M554 104h57M554 117h42M554 139h57" />
      <rect className={styles.diagramAccent} x="554" y="135" width="45" height="4" rx="2" />

      <rect className={styles.diagramSurfaceMuted} x="267" y="123" width="146" height="140" rx="18" />
      <rect className={styles.diagramSurfaceStrong} x="287" y="142" width="106" height="102" rx="12" />
      <path className={styles.diagramLine} d="M287 174h106" />
      <circle className={styles.diagramAccent} cx="304" cy="158" r="4" />
      <text className={styles.diagramTechnicalLabel} x="316" y="162">CLOUD</text>
      <rect className={styles.diagramAccentSoft} x="303" y="187" width="74" height="27" rx="6" />
      <circle className={styles.diagramAccent} cx="317" cy="200.5" r="4" />
      <path className={styles.diagramMutedLine} d="M328 196h35M328 205h24" />
      <text className={styles.diagramTechnicalLabelAccent} x="319" y="232">12ms</text>

      <rect className={styles.diagramAccentSoft} x="45" y="214" width="162" height="120" rx="12" />
      <path className={styles.diagramLine} d="M45 244h162" />
      <circle className={styles.diagramAccent} cx="64" cy="229" r="4" />
      <text className={styles.diagramTechnicalLabel} x="76" y="233">EDGE</text>
      <rect className={styles.diagramSurfaceMuted} x="70" y="263" width="31" height="48" rx="7" />
      <path className={styles.diagramLineStrong} d="M82 270h7M81 302h9" />
      <path className={styles.diagramMutedLine} d="M119 266h66M119 279h44M119 301h66" />
      <rect className={styles.diagramAccent} x="119" y="297" width="53" height="4" rx="2" />

      <rect className={styles.diagramAccentSoft} x="473" y="214" width="162" height="120" rx="12" />
      <path className={styles.diagramLine} d="M473 244h162" />
      <circle className={styles.diagramAccent} cx="492" cy="229" r="4" />
      <text className={styles.diagramTechnicalLabel} x="504" y="233">EDGE</text>
      <circle className={styles.diagramSurfaceMuted} cx="517" cy="284" r="24" />
      <path className={styles.diagramAccentLine} d="M517 270v28M503 284h28" />
      <path className={styles.diagramMutedLine} d="M554 266h57M554 279h42M554 301h57" />
      <rect className={styles.diagramAccent} x="554" y="297" width="39" height="4" rx="2" />
    </svg>
  );
}

function FederatedDiagram() {
  return (
    <svg className={styles.diagram} viewBox="0 0 680 380" role="presentation">
      <path className={styles.diagramMutedLine} d="M208 102 275 153M472 102l-67 51M208 278l67-51M472 278l-67-51" strokeDasharray="5 7" />
      <rect className={styles.diagramAccent} x="240" y="122" width="9" height="9" rx="2" />
      <rect className={styles.diagramAccent} x="431" y="122" width="9" height="9" rx="2" />
      <rect className={styles.diagramAccent} x="240" y="249" width="9" height="9" rx="2" />
      <rect className={styles.diagramAccent} x="431" y="249" width="9" height="9" rx="2" />

      <rect className={styles.diagramSurfaceStrong} x="38" y="47" width="170" height="112" rx="12" />
      <path className={styles.diagramLine} d="M38 78h170" />
      <circle className={styles.diagramAccent} cx="58" cy="62" r="4" />
      <ellipse className={styles.diagramSurfaceMuted} cx="77" cy="111" rx="22" ry="8" />
      <path className={styles.diagramLineStrong} d="M55 111v21c0 5 10 8 22 8s22-3 22-8v-21M55 122c0 5 10 8 22 8s22-3 22-8" />
      <rect className={styles.diagramAccentSoft} x="119" y="95" width="65" height="48" rx="8" />
      <path className={styles.diagramAccentLine} d="M134 108h34M134 119h26M134 130h34" />

      <rect className={styles.diagramSurfaceStrong} x="472" y="47" width="170" height="112" rx="12" />
      <path className={styles.diagramLine} d="M472 78h170" />
      <circle className={styles.diagramAccent} cx="492" cy="62" r="4" />
      <ellipse className={styles.diagramSurfaceMuted} cx="511" cy="111" rx="22" ry="8" />
      <path className={styles.diagramLineStrong} d="M489 111v21c0 5 10 8 22 8s22-3 22-8v-21M489 122c0 5 10 8 22 8s22-3 22-8" />
      <rect className={styles.diagramAccentSoft} x="553" y="95" width="65" height="48" rx="8" />
      <path className={styles.diagramAccentLine} d="M568 108h34M568 119h26M568 130h34" />

      <rect className={styles.diagramSurfaceStrong} x="38" y="221" width="170" height="112" rx="12" />
      <path className={styles.diagramLine} d="M38 252h170" />
      <circle className={styles.diagramAccent} cx="58" cy="236" r="4" />
      <ellipse className={styles.diagramSurfaceMuted} cx="77" cy="285" rx="22" ry="8" />
      <path className={styles.diagramLineStrong} d="M55 285v21c0 5 10 8 22 8s22-3 22-8v-21M55 296c0 5 10 8 22 8s22-3 22-8" />
      <rect className={styles.diagramAccentSoft} x="119" y="269" width="65" height="48" rx="8" />
      <path className={styles.diagramAccentLine} d="M134 282h34M134 293h26M134 304h34" />

      <rect className={styles.diagramSurfaceStrong} x="472" y="221" width="170" height="112" rx="12" />
      <path className={styles.diagramLine} d="M472 252h170" />
      <circle className={styles.diagramAccent} cx="492" cy="236" r="4" />
      <ellipse className={styles.diagramSurfaceMuted} cx="511" cy="285" rx="22" ry="8" />
      <path className={styles.diagramLineStrong} d="M489 285v21c0 5 10 8 22 8s22-3 22-8v-21M489 296c0 5 10 8 22 8s22-3 22-8" />
      <rect className={styles.diagramAccentSoft} x="553" y="269" width="65" height="48" rx="8" />
      <path className={styles.diagramAccentLine} d="M568 282h34M568 293h26M568 304h34" />

      <circle className={styles.diagramAccentSoft} cx="340" cy="190" r="82" />
      <circle className={styles.diagramSurfaceStrong} cx="340" cy="190" r="60" />
      <rect className={styles.diagramSurfaceMuted} x="304" y="151" width="72" height="78" rx="12" />
      <rect className={styles.diagramAccent} x="320" y="168" width="40" height="9" rx="4.5" />
      <rect className={styles.diagramAccentSoft} x="320" y="185" width="40" height="9" rx="4.5" />
      <rect className={styles.diagramAccentSoft} x="320" y="202" width="40" height="9" rx="4.5" />
    </svg>
  );
}

function FeatureDiagram({ featureKey }: { featureKey: FeatureKey }) {
  switch (featureKey) {
    case 'realtime':
      return <SyncDiagram />;
    case 'analytics':
      return <AnalyticsDiagram />;
    case 'security':
      return <SecurityDiagram />;
    case 'edge':
      return <EdgeDiagram />;
    case 'federated':
      return <FederatedDiagram />;
  }
}

function FeatureDetails({ featureKey, t }: { featureKey: FeatureKey; t: Translate }) {
  return (
    <ul className={styles.featureDetails}>
      {[1, 2, 3, 4].map((number) => (
        <li key={number}>{t(`feature.${featureKey}.detail${number}`)}</li>
      ))}
    </ul>
  );
}

export default function FeaturesPage() {
  const { i18n } = useDocusaurusContext();
  const isZh = i18n.currentLocale === 'zh';
  const t: Translate = (key) => translations[isZh ? 'zh' : 'en'][key] || key;

  return (
    <Layout title={t('features.title')} description={isZh ? t('features.description') : 'PowerMem Upcoming Features'}>
      <main className={styles.featuresPage}>
        <header className={styles.hero}>
          <div className={`container ${styles.pageContainer}`}>
            <div className={styles.heroGrid}>
              <Heading as="h1" className={styles.title}>
                {t('features.title')}
              </Heading>
              <p className={styles.subtitle}>{t('features.subtitle')}</p>
            </div>
          </div>
        </header>

        <div className={styles.featureAtlas}>
          {features.map((featureKey, index) => (
            <article
              className={`${styles.featureChapter} ${index % 2 === 1 ? styles.featureChapterReverse : ''}`}
              key={featureKey}
            >
              <div className={`container ${styles.pageContainer} ${styles.chapterGrid}`}>
                <header className={styles.featureCopy}>
                  <Heading as="h2" className={styles.featureTitle}>
                    {t(`feature.${featureKey}.title`)}
                  </Heading>
                  <p className={styles.featureDesc}>{t(`feature.${featureKey}.desc`)}</p>
                  <FeatureDetails featureKey={featureKey} t={t} />
                </header>

                <div className={styles.diagramFrame} aria-hidden="true">
                  <FeatureDiagram featureKey={featureKey} />
                </div>
              </div>
            </article>
          ))}
        </div>
      </main>
    </Layout>
  );
}

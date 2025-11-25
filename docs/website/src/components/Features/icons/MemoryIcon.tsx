import React from 'react';

export default function MemoryIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
    >
      {/* Main memory card */}
      <rect
        x="4"
        y="6"
        width="16"
        height="12"
        rx="2"
        fill="currentColor"
        fillOpacity="0.9"
      />
      {/* Memory layers - showing intelligent organization */}
      <rect
        x="6"
        y="4"
        width="12"
        height="10"
        rx="1.5"
        fill="currentColor"
        fillOpacity="0.6"
      />
      <rect
        x="8"
        y="2"
        width="8"
        height="8"
        rx="1"
        fill="currentColor"
        fillOpacity="0.3"
      />
      {/* Connection lines - showing intelligent linking */}
      <path
        d="M12 8V10M12 12V14M12 16V18"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        fill="none"
        opacity="0.8"
      />
      {/* Intelligence indicator - sparkle/star */}
      <path
        d="M16 10L16.5 11L18 11L16.5 12L17 13L16 12.5L15 13L15.5 12L14 11L15.5 11L16 10Z"
        fill="currentColor"
        fillOpacity="0.9"
      />
    </svg>
  );
}


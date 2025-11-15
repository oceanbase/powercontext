import React from 'react';

export default function MultimodalIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
    >
      <rect
        x="3"
        y="3"
        width="8"
        height="8"
        rx="1"
        fill="currentColor"
        fillOpacity="0.9"
      />
      <rect
        x="13"
        y="3"
        width="8"
        height="8"
        rx="1"
        fill="currentColor"
        fillOpacity="0.7"
      />
      <rect
        x="3"
        y="13"
        width="8"
        height="8"
        rx="1"
        fill="currentColor"
        fillOpacity="0.5"
      />
      <path
        d="M13 13H21V21H13V13Z"
        fill="currentColor"
        fillOpacity="0.3"
      />
      <circle
        cx="17"
        cy="17"
        r="2"
        fill="currentColor"
      />
    </svg>
  );
}


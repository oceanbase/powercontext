import React from 'react';

export default function DeveloperIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
    >
      <path
        d="M8 3C7.44772 3 7 3.44772 7 4V7H4C3.44772 7 3 7.44772 3 8V20C3 20.5523 3.44772 21 4 21H20C20.5523 21 21 20.5523 21 20V8C21 7.44772 20.5523 7 20 7H17V4C17 3.44772 16.5523 3 16 3H8Z"
        fill="currentColor"
        fillOpacity="0.9"
      />
      <path
        d="M9 5V7H15V5H9Z"
        fill="currentColor"
      />
      <path
        d="M7 9H17V19H7V9Z"
        fill="currentColor"
        fillOpacity="0.3"
      />
      <path
        d="M10 12L12 14L14 12"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        fill="none"
      />
    </svg>
  );
}


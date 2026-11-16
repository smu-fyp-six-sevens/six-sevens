import type { ReactNode } from 'react';

// The one icon set: outlined, a 24px grid, a 1.75 stroke with round ends, drawn in the text colour. Every glyph in the
// app (menu items, card headers, buttons, messages) comes from here, so they read as one family.
const PATHS = {
  alert: (
    <>
      <path d="M12 4.2 21 19.5H3z" />
      <path d="M12 10v4" />
      <circle cx="12" cy="16.9" r=".6" fill="currentColor" />
    </>
  ),
  check: <path d="m5 12.5 4.5 4.5L19 7" />,
  x: <path d="M6 6l12 12M18 6 6 18" />,
  info: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 11.2v5" />
      <circle cx="12" cy="8" r=".6" fill="currentColor" />
    </>
  ),
  upload: <path d="M12 16V5m-4.5 4.5L12 5l4.5 4.5M5 19h14" />,
  download: <path d="M12 5v11m-4.5-4.5L12 16l4.5-4.5M5 19h14" />,
  retry: <path d="M20 11a8 8 0 1 0-2.3 5.7M20 5v6h-6" />,
  stop: <rect x="6.5" y="6.5" width="11" height="11" rx="2" />,
  summary: (
    <>
      <rect x="4" y="4" width="16" height="16" rx="2.5" />
      <path d="M10 4v16" />
    </>
  ),
  documents: <path d="M9 7h11M9 12h11M9 17h11M4.5 7h.01M4.5 12h.01M4.5 17h.01" />,
  file: <path d="M6.5 3.5h7l4 4v13h-11zM13.5 3.5v4h4M9.5 12.5h5M9.5 16h5" />,
  approved: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="m8.5 12.2 2.5 2.5 4.5-5" />
    </>
  ),
  rejected: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="m9 9 6 6m0-6-6 6" />
    </>
  ),
  analysis: (
    <>
      <circle cx="11" cy="11" r="6.5" />
      <path d="m16 16 4 4" />
    </>
  ),
  risk: (
    <>
      <path d="M4 16a8 8 0 0 1 16 0" />
      <path d="m12 16 3.5-4.5" />
      <circle cx="12" cy="16" r="1" fill="currentColor" />
    </>
  ),
  additional: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 8.5v7M8.5 12h7" />
    </>
  ),
  notes: <path d="M15 5.5 18.5 9M4.5 19.5l1-4.2L16.3 4.5a2 2 0 0 1 2.8 0l.4.4a2 2 0 0 1 0 2.8L8.7 18.5z" />,
  audit: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 7.5V12l3 2" />
    </>
  ),
  cost: (
    <>
      <path d="M12 4v16" />
      <path d="M16 8.3c-.5-1.2-2-2-4-2-2.3 0-3.9 1-3.9 2.7 0 4 7.9 1.7 7.9 5.7 0 1.7-1.7 2.9-4 2.9-2 0-3.5-.8-4.1-2.1" />
    </>
  ),
  overview: <path d="M4 4h6.5v6.5H4zM13.5 4H20v6.5h-6.5zM4 13.5h6.5V20H4zM13.5 13.5H20V20h-6.5z" />,
  flag: <path d="M5.5 21V4M5.5 4.5h11l-2 3.7 2 3.7h-11" />,
  chevronRight: <path d="M9 6l6 6-6 6" />,
  chevronLeft: <path d="M15 6l-6 6 6 6" />,
  locate: (
    <>
      <circle cx="12" cy="12" r="4.5" />
      <path d="M12 3v3M12 18v3M3 12h3M18 12h3" />
    </>
  ),
} satisfies Record<string, ReactNode>;

export type IconName = keyof typeof PATHS;

export function Icon({ name, size = 16, className }: { name: IconName; size?: number; className?: string }) {
  return (
    <svg
      className={className ? `icon ${className}` : 'icon'}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {PATHS[name]}
    </svg>
  );
}

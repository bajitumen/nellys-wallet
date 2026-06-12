export type IconProps = { className?: string };

const navIconProps = {
  viewBox: "0 0 24 24",
  fill: "none" as const,
  stroke: "currentColor" as const,
  strokeWidth: 1.7,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  "aria-hidden": true as const,
};

const iconProps = {
  viewBox: "0 0 24 24",
  fill: "none" as const,
  stroke: "currentColor" as const,
  strokeWidth: 2,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  "aria-hidden": true as const,
};

export function IconHouse({ className = "nav-icon" }: IconProps = {}) {
  return (
    <svg {...navIconProps} className={className}>
      <path d="M3 11l9-8 9 8v10h-6v-6H9v6H3z" />
    </svg>
  );
}

export function IconBag({ className = "nav-icon" }: IconProps = {}) {
  return (
    <svg {...navIconProps} className={className}>
      <path d="M5 7h14l-1 14H6L5 7z" />
      <path d="M9 7V5a3 3 0 0 1 6 0v2" />
    </svg>
  );
}

export function IconCoin({ className = "nav-icon" }: IconProps = {}) {
  return (
    <svg {...navIconProps} className={className}>
      <circle cx="12" cy="12" r="9" />
      <path d="M14.5 9.5c-.5-1-1.5-1.5-2.5-1.5s-2 .5-2 1.5.6 1.4 2 2c1.5.5 2.5 1 2.5 2 0 1-1 1.5-2.5 1.5s-2-.5-2.5-1.5" />
      <line x1="12" y1="6.5" x2="12" y2="8" />
      <line x1="12" y1="16" x2="12" y2="17.5" />
    </svg>
  );
}

export function IconPie({ className = "nav-icon" }: IconProps = {}) {
  return (
    <svg {...navIconProps} className={className}>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 3v9h9" />
    </svg>
  );
}

export function IconTarget({ className = "nav-icon" }: IconProps = {}) {
  return (
    <svg {...navIconProps} className={className}>
      <circle cx="12" cy="12" r="9" />
      <circle cx="12" cy="12" r="5" />
      <circle cx="12" cy="12" r="1.2" fill="currentColor" stroke="none" />
    </svg>
  );
}

export function IconSliders({ className = "nav-icon" }: IconProps = {}) {
  return (
    <svg {...navIconProps} className={className}>
      <line x1="4" y1="6" x2="20" y2="6" />
      <line x1="4" y1="12" x2="20" y2="12" />
      <line x1="4" y1="18" x2="20" y2="18" />
      <circle cx="9" cy="6" r="2" fill="var(--surface, #fff)" />
      <circle cx="15" cy="12" r="2" fill="var(--surface, #fff)" />
      <circle cx="9" cy="18" r="2" fill="var(--surface, #fff)" />
    </svg>
  );
}

export function IconCaretDown({ className = "icon" }: IconProps = {}) {
  return (
    <svg {...iconProps} className={className}>
      <polyline points="6 9 12 15 18 9" />
    </svg>
  );
}

export function IconPlus({ className = "icon" }: IconProps = {}) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      aria-hidden
      className={className}
    >
      <line x1="12" y1="5" x2="12" y2="19" />
      <line x1="5" y1="12" x2="19" y2="12" />
    </svg>
  );
}

export function IconRefresh({ className = "icon" }: IconProps = {}) {
  return (
    <svg {...iconProps} className={className}>
      <path d="M3 12a9 9 0 0 1 15.5 -6.3 L 21 8" />
      <polyline points="21 3 21 8 16 8" />
      <path d="M21 12a9 9 0 0 1 -15.5 6.3 L 3 16" />
      <polyline points="3 21 3 16 8 16" />
    </svg>
  );
}

export function IconDotsThree({ className = "icon" }: IconProps = {}) {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden className={className}>
      <circle cx="5" cy="12" r="1.6" />
      <circle cx="12" cy="12" r="1.6" />
      <circle cx="19" cy="12" r="1.6" />
    </svg>
  );
}

export function IconPencil({ className = "icon" }: IconProps = {}) {
  return (
    <svg
      viewBox="0 0 256 256"
      fill="none"
      stroke="currentColor"
      strokeWidth={16}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
      className={className}
    >
      <path d="M92 216H48a8 8 0 0 1-8-8v-44a8 8 0 0 1 2.34-5.66l120-120a8 8 0 0 1 11.32 0l44 44a8 8 0 0 1 0 11.32l-120 120A8 8 0 0 1 92 216Z" />
      <line x1="136" y1="64" x2="192" y2="120" />
    </svg>
  );
}

export function IconTrash({ className = "icon" }: IconProps = {}) {
  return (
    <svg
      viewBox="0 0 256 256"
      fill="none"
      stroke="currentColor"
      strokeWidth={16}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
      className={className}
    >
      <line x1="216" y1="56" x2="40" y2="56" />
      <line x1="104" y1="104" x2="104" y2="168" />
      <line x1="152" y1="104" x2="152" y2="168" />
      <path d="M200 56v152a8 8 0 0 1-8 8H64a8 8 0 0 1-8-8V56" />
      <path d="M168 56V40a16 16 0 0 0-16-16h-48a16 16 0 0 0-16 16v16" />
    </svg>
  );
}

export function IconCaretDoubleLeft({ className }: IconProps = {}) {
  return (
    <svg {...iconProps} className={className}>
      <polyline points="17 5 11 12 17 19" />
      <polyline points="11 5 5 12 11 19" />
    </svg>
  );
}

export function IconCaretDoubleRight({ className }: IconProps = {}) {
  return (
    <svg {...iconProps} className={className}>
      <polyline points="7 5 13 12 7 19" />
      <polyline points="13 5 19 12 13 19" />
    </svg>
  );
}

export function IconMoon({ className = "icon icon-moon" }: IconProps = {}) {
  return (
    <svg {...iconProps} className={className}>
      <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
    </svg>
  );
}

export function IconSun({ className = "icon icon-sun" }: IconProps = {}) {
  return (
    <svg {...iconProps} className={className}>
      <circle cx="12" cy="12" r="4" />
      <line x1="12" y1="2" x2="12" y2="4" />
      <line x1="12" y1="20" x2="12" y2="22" />
      <line x1="2" y1="12" x2="4" y2="12" />
      <line x1="20" y1="12" x2="22" y2="12" />
      <line x1="4.93" y1="4.93" x2="6.34" y2="6.34" />
      <line x1="17.66" y1="17.66" x2="19.07" y2="19.07" />
      <line x1="4.93" y1="19.07" x2="6.34" y2="17.66" />
      <line x1="17.66" y1="6.34" x2="19.07" y2="4.93" />
    </svg>
  );
}

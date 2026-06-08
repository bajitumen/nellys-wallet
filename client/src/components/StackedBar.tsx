import { useState } from "react";

export type Segment = {
  key: string;
  label: string;
  value: number;
  color: string;
  active?: boolean;
};

type Props = {
  segments: Segment[];
  onToggle?: (key: string) => void;
  ariaLabel?: string;
};

export function StackedBar({ segments, onToggle, ariaLabel }: Props) {
  const [hover, setHover] = useState<{ text: string; x: number; y: number } | null>(null);
  if (segments.length === 0) return null;

  function format(s: Segment): string {
    const usd = s.value.toLocaleString("en-US", {
      style: "currency",
      currency: "USD",
      maximumFractionDigits: 0,
    });
    return `${s.label}: ${usd}`;
  }

  return (
    <>
      <div className="stacked-bar" role="img" aria-label={ariaLabel}>
        {segments.map((s) => (
          <button
            key={s.key}
            type="button"
            className={`stacked-bar-segment${s.active ? " active" : ""}`}
            style={{ flex: `${s.value} 0 0`, background: s.color }}
            aria-label={s.label}
            onClick={() => onToggle?.(s.key)}
            onMouseEnter={(e) => {
              const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
              setHover({ text: format(s), x: rect.left + rect.width / 2, y: rect.top });
            }}
            onMouseLeave={() => setHover(null)}
          />
        ))}
      </div>
      {hover && (
        <div
          className="bar-tooltip visible"
          style={{ left: `${hover.x}px`, top: `${hover.y}px` }}
        >
          {hover.text}
        </div>
      )}
    </>
  );
}

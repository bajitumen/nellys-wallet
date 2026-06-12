import { useLayoutEffect, useRef, useState } from "react";

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

const REPLAY_MS = 350;

export function StackedBar({ segments, onToggle, ariaLabel }: Props) {
  const [hover, setHover] = useState<{ text: string; x: number; y: number } | null>(null);
  const barRef = useRef<HTMLDivElement | null>(null);
  // Snapshot previous flex weights per key so we can animate from the old
  // distribution to the new one when segments rebalance after a filter change.
  const prevValuesRef = useRef<Record<string, number>>({});

  useLayoutEffect(() => {
    const bar = barRef.current;
    if (!bar) return;
    const buttons = bar.querySelectorAll<HTMLElement>(".stacked-bar-segment");
    buttons.forEach((el) => {
      const key = el.dataset.segKey;
      if (!key) return;
      const target = Number(el.dataset.segValue) || 0;
      const from = prevValuesRef.current[key];
      if (from == null || from === target) return;
      // Two RAFs so the browser commits the "from" weight before transitioning.
      el.style.transition = "none";
      el.style.flexGrow = String(from);
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          el.style.transition = `flex-grow ${REPLAY_MS}ms ease-out`;
          el.style.flexGrow = String(target);
        });
      });
    });
    prevValuesRef.current = Object.fromEntries(segments.map((s) => [s.key, s.value]));
  }, [segments]);

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
      <div className="stacked-bar" role="img" aria-label={ariaLabel} ref={barRef}>
        {segments.map((s) => (
          <button
            key={s.key}
            type="button"
            className={`stacked-bar-segment${s.active ? " active" : ""}`}
            data-seg-key={s.key}
            data-seg-value={s.value}
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

import { useEffect, useMemo, useRef, useState } from "react";
import { InlineDropdown, type DropdownOption } from "./InlineDropdown";

export type ProjectionAccount = {
  id: string;
  label: string;
  balance: number;
  sign: number;
  rateAnnual: number;
  monthlyContribution: number;
};

const HORIZONS: { years: number; label: string }[] = [
  { years: 1, label: "1Y" },
  { years: 5, label: "5Y" },
  { years: 10, label: "10Y" },
  { years: 20, label: "20Y" },
  { years: 30, label: "30Y" },
];

const W = 1000;
const H = 220;
const PAD_X = 8;
const PAD_Y = 14;

function formatUsd(n: number): string {
  const sign = n < 0 ? "-" : "";
  return sign + "$" + Math.abs(n).toLocaleString("en-US", { maximumFractionDigits: 0 });
}

function monthLabel(idx: number): string {
  if (idx === 0) return "Today";
  const d = new Date();
  d.setDate(1);
  d.setMonth(d.getMonth() + idx);
  return d.toLocaleDateString("en-US", { month: "short", year: "numeric" });
}

function projectSeries(start: number, annualPct: number, months: number, contrib: number): number[] {
  const monthly = Math.pow(1 + annualPct / 100, 1 / 12) - 1;
  const out = [start];
  let v = start;
  for (let m = 1; m <= months; m++) {
    v = v * (1 + monthly) + contrib;
    out.push(v);
  }
  return out;
}

function buildSeries(
  target: string,
  accounts: ProjectionAccount[],
  months: number,
  netMonthly: number,
): number[] {
  if (target === "__net__") {
    const series = new Array(months + 1).fill(0);
    let allocated = 0;
    for (const a of accounts) {
      const p = projectSeries(a.balance, a.rateAnnual, months, a.monthlyContribution);
      for (let i = 0; i <= months; i++) series[i] += a.sign * p[i];
      allocated += a.monthlyContribution;
    }
    const remainder = netMonthly - allocated;
    for (let j = 1; j <= months; j++) series[j] += remainder * j;
    return series;
  }
  const acct = accounts.find((a) => a.id === target);
  if (!acct) return [];
  return projectSeries(acct.balance, acct.rateAnnual, months, acct.monthlyContribution);
}

type PathBundle = {
  line: string;
  area: string;
  points: { x: number; y: number; value: number; monthIdx: number }[];
};

function pathFromSeries(series: number[]): PathBundle | null {
  if (series.length < 2) return null;
  const n = series.length;
  const plotW = W - 2 * PAD_X;
  const plotH = H - 2 * PAD_Y;
  let yMin = Math.min(...series);
  let yMax = Math.max(...series);
  const pad = Math.max(1, (yMax - yMin) * 0.05);
  yMin -= pad;
  yMax += pad;
  const ySpan = Math.max(1, yMax - yMin);
  const points = series.map((v, i) => ({
    x: PAD_X + (i / (n - 1)) * plotW,
    y: PAD_Y + ((yMax - v) / ySpan) * plotH,
    value: v,
    monthIdx: i,
  }));
  const baseY = PAD_Y + plotH;
  const line = "M " + points.map((p) => `${p.x.toFixed(2)},${p.y.toFixed(2)}`).join(" L ");
  const area =
    `M ${points[0].x.toFixed(2)},${baseY.toFixed(2)} ` +
    points.map((p) => `L ${p.x.toFixed(2)},${p.y.toFixed(2)}`).join(" ") +
    ` L ${points[n - 1].x.toFixed(2)},${baseY.toFixed(2)} Z`;
  return { line, area, points };
}

type Props = {
  accounts: ProjectionAccount[];
  netMonthlyFlow: number;
};

export function PlanningChart({ accounts, netMonthlyFlow }: Props) {
  const [target, setTarget] = useState<string>("__net__");
  const [years, setYears] = useState<number>(10);
  const svgRef = useRef<SVGSVGElement>(null);
  const [hover, setHover] = useState<{
    x: number; y: number; value: number; monthIdx: number; clientX: number; clientY: number;
  } | null>(null);

  const months = years * 12;
  const series = useMemo(
    () => buildSeries(target, accounts, months, netMonthlyFlow),
    [target, accounts, months, netMonthlyFlow],
  );
  const paths = useMemo(() => pathFromSeries(series), [series]);
  const trend = series.length > 1 && series[series.length - 1] >= series[0] ? "up" : "down";

  const targetOpts: DropdownOption[] = useMemo(
    () => [
      { value: "__net__", label: "Total net worth" },
      ...accounts.map((a) => ({ value: a.id, label: a.label })),
    ],
    [accounts],
  );
  const targetLabel = targetOpts.find((o) => o.value === target)?.label ?? "";
  const summary =
    series.length > 1
      ? `${targetLabel} · today ${formatUsd(series[0])} → ${years}y ${formatUsd(series[series.length - 1])}`
      : "";

  useEffect(() => setHover(null), [target, years]);

  function onMove(e: React.MouseEvent) {
    if (!paths || !svgRef.current) return;
    const rect = svgRef.current.getBoundingClientRect();
    const svgX = ((e.clientX - rect.left) / rect.width) * W;
    let best = paths.points[0];
    let bestDiff = Math.abs(best.x - svgX);
    for (const p of paths.points) {
      const diff = Math.abs(p.x - svgX);
      if (diff < bestDiff) {
        best = p;
        bestDiff = diff;
      }
    }
    setHover({
      x: best.x,
      y: best.y,
      value: best.value,
      monthIdx: best.monthIdx,
      clientX: rect.left + (best.x / W) * rect.width,
      clientY: rect.top + (best.y / H) * rect.height,
    });
  }

  return (
    <div className={`chart-card planning-chart-card trend-${trend}`}>
      <div className="planning-chart-controls">
        <div className="planning-target">
          <span className="planning-target-label">Project</span>
          <InlineDropdown
            triggerClassName="inline-dropdown-trigger planning-target-trigger"
            options={targetOpts}
            value={target}
            onChange={(opt) => setTarget(opt.value)}
            ariaLabel="Projection target"
          />
        </div>
        <div className="planning-horizon-filter">
          {HORIZONS.map((h) => (
            <button
              key={h.years}
              type="button"
              className={`chart-range-btn${years === h.years ? " active" : ""}`}
              onClick={() => setYears(h.years)}
            >
              {h.label}
            </button>
          ))}
        </div>
      </div>
      <svg
        ref={svgRef}
        id="planning-svg"
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        onMouseMove={onMove}
        onMouseLeave={() => setHover(null)}
      >
        {paths && (
          <>
            <path d={paths.area} className={`planning-area planning-trend-${trend}`} />
            <path
              d={paths.line}
              className={`planning-line planning-trend-${trend}`}
              fill="none"
              strokeWidth={2}
              strokeLinecap="round"
              strokeLinejoin="round"
              vectorEffect="non-scaling-stroke"
            />
          </>
        )}
      </svg>
      <div className="planning-summary">{summary}</div>
      {hover && (
        <>
          <div
            className="networth-dot"
            style={{
              left: `${hover.clientX}px`,
              top: `${hover.clientY}px`,
              borderColor: trend === "up" ? "var(--positive)" : "var(--negative)",
            }}
          />
          <div
            className="bar-tooltip visible"
            style={{ left: `${hover.clientX}px`, top: `${hover.clientY}px` }}
          >
            {monthLabel(hover.monthIdx)}: {formatUsd(hover.value)}
          </div>
        </>
      )}
    </div>
  );
}

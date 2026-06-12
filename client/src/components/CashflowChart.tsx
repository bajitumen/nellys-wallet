import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

export type MonthRow = {
  month: string;
  label: string;
  spend: number;
  income: number;
  ts: number;
};

type Range = "30D" | "3M" | "6M" | "YTD" | "All";
const RANGES: Range[] = ["30D", "3M", "6M", "YTD", "All"];

const W = 500;
const H = 180;
const PAD_X = 4;
const PAD_Y = 12;

function rangeStart(range: Range, dataMinTs: number): number {
  const now = new Date();
  const nowTs = Math.floor(now.getTime() / 1000);
  if (range === "All") return dataMinTs;
  if (range === "YTD") {
    return Math.floor(new Date(now.getFullYear(), 0, 1).getTime() / 1000);
  }
  if (range === "30D") return nowTs - 30 * 86400;
  if (range === "3M") {
    const d = new Date(now);
    d.setMonth(d.getMonth() - 3);
    return Math.floor(d.getTime() / 1000);
  }
  const d = new Date(now);
  d.setMonth(d.getMonth() - 6);
  return Math.floor(d.getTime() / 1000);
}

function roundedTopPath(x: number, y: number, w: number, h: number, r: number): string {
  const rr = Math.max(0, Math.min(r, w / 2, h));
  return (
    `M ${x.toFixed(2)},${(y + rr).toFixed(2)}` +
    ` Q ${x.toFixed(2)},${y.toFixed(2)} ${(x + rr).toFixed(2)},${y.toFixed(2)}` +
    ` L ${(x + w - rr).toFixed(2)},${y.toFixed(2)}` +
    ` Q ${(x + w).toFixed(2)},${y.toFixed(2)} ${(x + w).toFixed(2)},${(y + rr).toFixed(2)}` +
    ` L ${(x + w).toFixed(2)},${(y + h).toFixed(2)}` +
    ` L ${x.toFixed(2)},${(y + h).toFixed(2)} Z`
  );
}

function roundedBottomPath(x: number, y: number, w: number, h: number, r: number): string {
  const rr = Math.max(0, Math.min(r, w / 2, h));
  return (
    `M ${x.toFixed(2)},${y.toFixed(2)}` +
    ` L ${(x + w).toFixed(2)},${y.toFixed(2)}` +
    ` L ${(x + w).toFixed(2)},${(y + h - rr).toFixed(2)}` +
    ` Q ${(x + w).toFixed(2)},${(y + h).toFixed(2)} ${(x + w - rr).toFixed(2)},${(y + h).toFixed(2)}` +
    ` L ${(x + rr).toFixed(2)},${(y + h).toFixed(2)}` +
    ` Q ${x.toFixed(2)},${(y + h).toFixed(2)} ${x.toFixed(2)},${(y + h - rr).toFixed(2)} Z`
  );
}

type Bar = {
  path: string;
  kind: "income" | "spend" | "net-positive" | "net-negative";
  label: string;
  month: string;
  amount: number;
};

function buildBars(totals: MonthRow[]): { bars: Bar[]; zeroY: number } {
  if (totals.length === 0) return { bars: [], zeroY: 0 };
  const plotW = W - 2 * PAD_X;
  const plotH = H - 2 * PAD_Y;

  let maxIncome = 0;
  let maxSpend = 0;
  for (const t of totals) {
    if (t.income > maxIncome) maxIncome = t.income;
    if (t.spend > maxSpend) maxSpend = t.spend;
  }
  if (maxIncome === 0 && maxSpend === 0) maxIncome = 1;

  const yMax = maxIncome;
  const yMin = -maxSpend;
  const ySpan = Math.max(1, yMax - yMin);
  const toY = (v: number) => PAD_Y + ((yMax - v) / ySpan) * plotH;
  const zeroY = toY(0);

  const n = totals.length;
  const gap = 8;
  const barW = (plotW - gap * (n - 1)) / n;

  const bars: Bar[] = [];
  totals.forEach((t, i) => {
    const x = PAD_X + i * (barW + gap);
    if (t.income > 0) {
      const topY = toY(t.income);
      bars.push({
        path: roundedTopPath(x, topY, barW, zeroY - topY, 6),
        kind: "income",
        label: t.label,
        month: t.month,
        amount: t.income,
      });
    }
    if (t.spend > 0) {
      const bottomY = toY(-t.spend);
      bars.push({
        path: roundedBottomPath(x, zeroY, barW, bottomY - zeroY, 6),
        kind: "spend",
        label: t.label,
        month: t.month,
        amount: t.spend,
      });
    }
    const net = t.income - t.spend;
    if (net > 0) {
      const topY = toY(net);
      bars.push({
        path: roundedTopPath(x, topY, barW, zeroY - topY, 6),
        kind: "net-positive",
        label: t.label,
        month: t.month,
        amount: net,
      });
    } else if (net < 0) {
      const bottomY = toY(net);
      bars.push({
        path: roundedBottomPath(x, zeroY, barW, bottomY - zeroY, 6),
        kind: "net-negative",
        label: t.label,
        month: t.month,
        amount: -net,
      });
    }
  });
  return { bars, zeroY };
}

function tooltipText(bar: Bar): string {
  let suffix: string;
  let sign = "";
  if (bar.kind === "income") suffix = "Earned";
  else if (bar.kind === "spend") suffix = "Spent";
  else if (bar.kind === "net-positive") {
    suffix = "Net";
    sign = "+";
  } else {
    suffix = "Net";
    sign = "−";
  }
  const amount = sign + "$" + bar.amount.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  return `${bar.label}: ${amount} ${suffix}`;
}

export function CashflowChart({ data }: { data: MonthRow[] }) {
  const nav = useNavigate();
  const [range, setRange] = useState<Range>("6M");
  const [hover, setHover] = useState<{ text: string; x: number; y: number } | null>(null);

  const dataMinTs = useMemo(
    () => (data.length ? Math.min(...data.map((d) => d.ts)) : 0),
    [data],
  );

  const filtered = useMemo(() => {
    const startTs = rangeStart(range, dataMinTs);
    return data.filter((t) => t.ts >= startTs && (t.spend > 0 || t.income > 0));
  }, [data, range, dataMinTs]);

  const geo = useMemo(() => buildBars(filtered), [filtered]);

  function onBarClick(bar: Bar) {
    if (typeof window !== "undefined" && window.matchMedia("(max-width: 720px)").matches) return;
    const page = bar.kind === "income" || bar.kind === "net-positive" ? "/income" : "/spending";
    nav(`${page}?month=${encodeURIComponent(bar.month)}`);
  }

  if (data.length === 0) return null;

  return (
    <div className="chart-card monthly-spend-chart">
      <div className="label">Cash Flow</div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        xmlns="http://www.w3.org/2000/svg"
      >
        {filtered.length > 0 && (
          <line
            className="zero-line"
            x1={0}
            x2={W}
            y1={geo.zeroY}
            y2={geo.zeroY}
            vectorEffect="non-scaling-stroke"
          />
        )}
        {geo.bars.map((bar, i) => (
          <path
            key={i}
            className={`bar bar-${bar.kind}`}
            d={bar.path}
            style={{ cursor: "pointer" }}
            onClick={() => onBarClick(bar)}
            onMouseEnter={(e) => {
              const rect = (e.currentTarget as SVGPathElement).getBoundingClientRect();
              setHover({
                text: tooltipText(bar),
                x: rect.left + rect.width / 2,
                y: rect.top,
              });
            }}
            onMouseLeave={() => setHover(null)}
          />
        ))}
      </svg>
      <div className="chart-range-filter">
        {RANGES.map((r) => (
          <button
            key={r}
            type="button"
            className={`chart-range-btn${r === range ? " active" : ""}`}
            onClick={() => setRange(r)}
          >
            {r}
          </button>
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
    </div>
  );
}

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { InlineDropdown, type DropdownOption } from "./InlineDropdown";
import { formatUsdWhole as formatUsd } from "../lib/format";

export type SeriesPoint = { ts: number; value: number };
export type SeriesOption = {
  key: string;
  label: string;
  menu_label?: string;
  indent?: boolean;
};

const RANGES: { key: "30D" | "3M" | "6M" | "YTD" | "ALL"; label: string; days: number | null }[] = [
  { key: "30D", label: "30D", days: 30 },
  { key: "3M", label: "3M", days: 90 },
  { key: "6M", label: "6M", days: 180 },
  { key: "YTD", label: "YTD", days: -1 },
  { key: "ALL", label: "All", days: null },
];

const WIDTH = 1000;
const HEIGHT = 150;
const PAD_X = 4;
const PAD_Y = 10;


function rangeStartTs(rangeKey: typeof RANGES[number]["key"]): number | null {
  const now = Math.floor(Date.now() / 1000);
  const r = RANGES.find((r) => r.key === rangeKey)!;
  if (r.days === null) return null;
  if (r.days === -1) {
    const jan1 = new Date();
    jan1.setMonth(0, 1);
    jan1.setHours(0, 0, 0, 0);
    return Math.floor(jan1.getTime() / 1000);
  }
  return now - r.days * 86400;
}

type ChartGeometry = {
  realLinePath: string;
  zeroLinePath: string;
  areaPath: string;
  rendered: { x: number; y: number; ts: number; value: number }[];
  firstValue: number;
  lastValue: number;
};

function buildChart(
  series: SeriesPoint[],
  rangeStart: number | null,
  rangeEnd: number,
): ChartGeometry | null {
  if (series.length === 0) return null;
  const DAY = 86400;
  const start = rangeStart ?? series[0].ts;
  const realByDay = new Map<number, number>();
  for (const s of series) {
    realByDay.set(Math.floor(s.ts / DAY) * DAY, s.value);
  }
  // Three kinds of days in the chart range:
  //   - "pre"     : before the first real snapshot     → $0
  //   - "real"    : has a stored snapshot              → that value
  //   - "carried" : after first snapshot, no data      → previous real value
  // Pre days render as a dashed $0 baseline; real + carried form a single
  // continuous line (a step where carried takes over).
  type Day = { ts: number; value: number; kind: "pre" | "real" | "carried" };
  const days: Day[] = [];
  let carried: number | null = null;
  for (let t = Math.floor(start / DAY) * DAY; t <= rangeEnd; t += DAY) {
    const real = realByDay.get(t);
    if (real !== undefined) {
      carried = real;
      days.push({ ts: t, value: real, kind: "real" });
    } else if (carried === null) {
      days.push({ ts: t, value: 0, kind: "pre" });
    } else {
      days.push({ ts: t, value: carried, kind: "carried" });
    }
  }

  const hasPrePrefix = days.some((d) => d.kind === "pre");
  const realValues = series.map((s) => s.value);

  const xMin = days[0].ts;
  const xMax = rangeEnd;
  const xSpan = Math.max(1, xMax - xMin);
  // Anchor y-floor at $0 only when there ARE pre-days to show; otherwise
  // tighten to the real range so $179k-$185k variations stay legible.
  const yMin = hasPrePrefix ? 0 : Math.min(...realValues);
  const yMax = Math.max(...realValues);
  const ySpan = Math.max(1, yMax - yMin);
  const plotW = WIDTH - 2 * PAD_X;
  const plotH = HEIGHT - 2 * PAD_Y;
  const toX = (t: number) => PAD_X + ((t - xMin) / xSpan) * plotW;
  const toY = (v: number) => {
    if (yMax === yMin) return PAD_Y + plotH / 2;
    return PAD_Y + ((yMax - v) / ySpan) * plotH;
  };
  const baselineY = PAD_Y + plotH;

  // One continuous green line across every day in the range: pre days at $0,
  // real days at their value, carried days holding the previous real value.
  let realLinePath = "M ";
  for (let i = 0; i < days.length; i++) {
    const x = toX(days[i].ts).toFixed(2);
    const y = toY(days[i].value).toFixed(2);
    realLinePath += i === 0 ? `${x},${y} ` : `L ${x},${y} `;
  }
  const preLinePath = "";

  // Area under the whole continuous line; pre-days at $0 sit right on the
  // baseline, so the area there has zero height and adds no visual weight.
  const startX = toX(days[0].ts).toFixed(2);
  const endX = toX(days[days.length - 1].ts).toFixed(2);
  let areaPath = `M ${startX},${baselineY.toFixed(2)} `;
  for (const d of days) {
    areaPath += `L ${toX(d.ts).toFixed(2)},${toY(d.value).toFixed(2)} `;
  }
  areaPath += `L ${endX},${baselineY.toFixed(2)} Z`;

  // Every day is hoverable so the user can confirm a $0 prefix or see the
  // value being carried forward.
  const rendered = days.map((d, i) => ({
    x: toX(d.ts),
    y: toY(d.value),
    ts: d.ts,
    value: d.value,
    i,
  }));

  // Trend follows what the eye actually sees. With a $0 pre-prefix the
  // chart clearly climbs from baseline up to the current value, so use the
  // leftmost rendered point (synthetic or real) as the trend anchor.
  const firstValue = days[0].value;
  const lastValue = realValues[realValues.length - 1];
  return {
    realLinePath, zeroLinePath: preLinePath, areaPath, rendered,
    firstValue, lastValue,
  };
}

type Props = {
  seriesData: Record<string, SeriesPoint[]>;
  seriesOptions: SeriesOption[];
};

export function NetWorthChart({ seriesData, seriesOptions }: Props) {
  const [seriesKey, setSeriesKey] = useState<string>("net");
  const [range, setRange] = useState<typeof RANGES[number]["key"]>("30D");
  const svgRef = useRef<SVGSVGElement>(null);
  const [hover, setHover] = useState<{
    x: number; y: number; ts: number; value: number;
    clientX: number; clientY: number;
  } | null>(null);
  const tipRef = useRef<HTMLDivElement | null>(null);
  const [clampedTipLeft, setClampedTipLeft] = useState<number | null>(null);
  useLayoutEffect(() => {
    if (!hover || !tipRef.current) {
      setClampedTipLeft(null);
      return;
    }
    const w = tipRef.current.offsetWidth;
    const margin = 8;
    let left = hover.clientX - w / 2;
    if (left < margin) left = margin;
    if (left + w > window.innerWidth - margin) left = window.innerWidth - w - margin;
    setClampedTipLeft(left);
  }, [hover]);

  const series = seriesData[seriesKey] || [];
  const rangeEnd = useMemo(() => Math.floor(Date.now() / 1000), [seriesKey, range]);
  const rangeStart = useMemo(() => rangeStartTs(range), [range]);
  const filtered = useMemo(
    () =>
      rangeStart === null
        ? series
        : series.filter((s) => s.ts >= rangeStart),
    [series, rangeStart],
  );
  const chart = useMemo(
    () => buildChart(filtered, rangeStart, rangeEnd),
    [filtered, rangeStart, rangeEnd],
  );

  useEffect(() => {
    setHover(null);
  }, [seriesKey, range]);

  function pickClosestByClientX(clientX: number) {
    const svg = svgRef.current;
    if (!svg || !chart || chart.rendered.length === 0) return null;
    const rect = svg.getBoundingClientRect();
    const localX = ((clientX - rect.left) / rect.width) * WIDTH;
    let best = chart.rendered[0];
    let bestDiff = Math.abs(best.x - localX);
    for (const p of chart.rendered) {
      const diff = Math.abs(p.x - localX);
      if (diff < bestDiff) {
        best = p;
        bestDiff = diff;
      }
    }
    return { point: best, rect };
  }
  function handleHoverAtClientX(clientX: number) {
    const result = pickClosestByClientX(clientX);
    if (!result) return;
    const { point, rect } = result;
    const screenX = rect.left + (point.x / WIDTH) * rect.width;
    const screenY = rect.top + (point.y / HEIGHT) * rect.height;
    setHover({
      x: point.x, y: point.y, ts: point.ts, value: point.value,
      clientX: screenX, clientY: screenY,
    });
  }

  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    let touchState: { x0: number; y0: number; lock: "h" | "v" | null } | null = null;
    const LOCK = 8;
    function onStart(e: TouchEvent) {
      if (e.touches.length !== 1) {
        touchState = null;
        return;
      }
      touchState = { x0: e.touches[0].clientX, y0: e.touches[0].clientY, lock: null };
    }
    function onMove(e: TouchEvent) {
      if (!touchState || e.touches.length !== 1) return;
      const t = e.touches[0];
      const dx = t.clientX - touchState.x0;
      const dy = t.clientY - touchState.y0;
      if (touchState.lock === null) {
        if (Math.abs(dx) > LOCK || Math.abs(dy) > LOCK) {
          touchState.lock = Math.abs(dx) > Math.abs(dy) ? "h" : "v";
        }
      }
      if (touchState.lock === "h") {
        handleHoverAtClientX(t.clientX);
        e.preventDefault();
      }
    }
    function onEnd() {
      touchState = null;
      setHover(null);
    }
    svg.addEventListener("touchstart", onStart, { passive: true });
    svg.addEventListener("touchmove", onMove, { passive: false });
    svg.addEventListener("touchend", onEnd);
    svg.addEventListener("touchcancel", onEnd);
    const onScroll = () => setHover(null);
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      svg.removeEventListener("touchstart", onStart);
      svg.removeEventListener("touchmove", onMove);
      svg.removeEventListener("touchend", onEnd);
      svg.removeEventListener("touchcancel", onEnd);
      window.removeEventListener("scroll", onScroll);
    };
  }, [chart]);

  const seriesDropdownOpts: DropdownOption[] = seriesOptions.map((s) => ({
    value: s.key,
    label: s.label,
    menuLabel: s.menu_label,
    indent: s.indent,
  }));

  const lastValue = chart?.lastValue ?? 0;
  const firstValue = chart?.firstValue ?? 0;
  const delta = lastValue - firstValue;
  const trend = delta >= 0 ? "up" : "down";

  const hoverDate = hover ? new Date(hover.ts * 1000) : null;
  return (
    <div className={`chart-card networth-chart trend-${trend}`}>
      <div className="networth-heading">
        <InlineDropdown
          className="inline-dropdown"
          triggerClassName="inline-dropdown-trigger networth-series-trigger"
          options={seriesDropdownOpts}
          value={seriesKey}
          onChange={(opt) => setSeriesKey(opt.value)}
          ariaLabel="Series"
        />
        <span
          className={`networth-delta ${
            chart && delta > 0 ? "delta-up" : chart && delta < 0 ? "delta-down" : ""
          }`}
        >
          {chart ? formatUsd(lastValue) : "—"}
        </span>
      </div>
      {chart ? (
        <svg
          ref={svgRef}
          className="networth-svg"
          viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
          preserveAspectRatio="none"
          onMouseMove={(e) => handleHoverAtClientX(e.clientX)}
          onMouseLeave={() => setHover(null)}
        >
          <path d={chart.areaPath} className="networth-area" />
          {chart.zeroLinePath && (
            <path
              d={chart.zeroLinePath}
              className="networth-line-missing"
              fill="none"
              strokeWidth={1.5}
              strokeDasharray="3 3"
              vectorEffect="non-scaling-stroke"
              strokeLinejoin="round"
              strokeLinecap="round"
            />
          )}
          {chart.realLinePath && (
            <path
              d={chart.realLinePath}
              className="networth-line-real"
              fill="none"
              strokeWidth={2.5}
              vectorEffect="non-scaling-stroke"
              strokeLinejoin="round"
              strokeLinecap="round"
            />
          )}
        </svg>
      ) : (
        <p className="muted" style={{ padding: "1rem 0" }}>
          No snapshots in this range yet.
        </p>
      )}
      {hover && (
        <>
          <div
            className="networth-dot"
            style={{ left: `${hover.clientX}px`, top: `${hover.clientY}px` }}
          />
          <div
            ref={tipRef}
            className="bar-tooltip visible"
            style={{
              left: `${clampedTipLeft ?? hover.clientX}px`,
              top: `${hover.clientY}px`,
              transform: clampedTipLeft != null ? "translateY(-100%)" : undefined,
              visibility: clampedTipLeft != null ? "visible" : "hidden",
            }}
          >
            {hoverDate?.toLocaleDateString("en-US", {
              month: "short", day: "numeric", year: "numeric", timeZone: "UTC",
            })}
            : {formatUsd(hover.value)}
          </div>
        </>
      )}
      <div className="chart-range-filter" data-chart="networth">
        {RANGES.map((r) => (
          <button
            key={r.key}
            type="button"
            className={`chart-range-btn${range === r.key ? " active" : ""}`}
            onClick={() => setRange(r.key)}
          >
            {r.label}
          </button>
        ))}
      </div>
    </div>
  );
}

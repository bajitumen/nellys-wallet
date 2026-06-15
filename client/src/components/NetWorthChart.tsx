import { useEffect, useMemo, useRef, useState } from "react";
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
  linePath: string;
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
  const allDays: { ts: number; value: number; synthetic: boolean }[] = [];
  // Carry back from the first real snapshot so days before it draw a flat
  // line at the earliest known value — no L-spike from baseline zeros.
  let carried = series[0].value;
  for (let t = Math.floor(start / DAY) * DAY; t <= rangeEnd; t += DAY) {
    const real = realByDay.get(t);
    if (real !== undefined) carried = real;
    allDays.push({ ts: t, value: carried, synthetic: real === undefined && t < series[0].ts });
  }
  const pathXs = allDays.map((d) => d.ts);
  const pathYs = allDays.map((d) => d.value);
  const xMin = pathXs[0];
  const xMax = rangeEnd;
  const xSpan = Math.max(1, xMax - xMin);
  const yMin = Math.min(...pathYs);
  const yMax = Math.max(...pathYs);
  const ySpan = Math.max(1, yMax - yMin);
  const plotW = WIDTH - 2 * PAD_X;
  const plotH = HEIGHT - 2 * PAD_Y;
  const toX = (t: number) => PAD_X + ((t - xMin) / xSpan) * plotW;
  const toY = (v: number) => {
    if (yMax === yMin) return PAD_Y + plotH / 2;
    return PAD_Y + ((yMax - v) / ySpan) * plotH;
  };

  const baselineY = PAD_Y + plotH;
  const points = pathXs.map((t, i) => ({ x: toX(t), y: toY(pathYs[i]) }));
  const linePath = "M " + points.map((p) => `${p.x.toFixed(2)},${p.y.toFixed(2)}`).join(" L ");
  const areaPath =
    `M ${points[0].x.toFixed(2)},${baselineY.toFixed(2)} ` +
    points.map((p) => `L ${p.x.toFixed(2)},${p.y.toFixed(2)}`).join(" ") +
    ` L ${points[points.length - 1].x.toFixed(2)},${baselineY.toFixed(2)} Z`;

  // Hover only on real snapshots — never lie about a value the user
  // didn't actually have on a carried-back day.
  const rendered = allDays
    .map((d, i) => ({ synthetic: d.synthetic, i }))
    .filter(({ synthetic }) => !synthetic)
    .map(({ i }) => ({
      x: toX(pathXs[i]),
      y: toY(pathYs[i]),
      ts: pathXs[i],
      value: pathYs[i],
    }));

  const realValues = series.map((s) => s.value);
  const firstRealValue = realValues[0];
  const lastValue = realValues[realValues.length - 1];
  return { linePath, areaPath, rendered, firstValue: firstRealValue, lastValue };
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
          <path
            d={chart.linePath}
            className="networth-line-real"
            fill="none"
            strokeWidth={2.5}
            vectorEffect="non-scaling-stroke"
            strokeLinejoin="round"
            strokeLinecap="round"
          />
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
            className="bar-tooltip visible"
            style={{ left: `${hover.clientX}px`, top: `${hover.clientY}px` }}
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

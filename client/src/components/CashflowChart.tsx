import { useNavigate } from "react-router-dom";

export type MonthRow = {
  month: string;
  label: string;
  spend: number;
  income: number;
  ts: number;
};

function formatUsd(n: number): string {
  return n.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
}

const W = 1000;
const H = 180;
const PAD_TOP = 12;
const PAD_BOTTOM = 28;
const PAD_X = 12;

export function CashflowChart({ data }: { data: MonthRow[] }) {
  const nav = useNavigate();
  if (data.length === 0) return null;
  const maxAbs = Math.max(
    1,
    ...data.flatMap((d) => [d.spend, d.income, Math.abs(d.income - d.spend)]),
  );
  const plotH = H - PAD_TOP - PAD_BOTTOM;
  const halfH = plotH / 2;
  const zeroY = PAD_TOP + halfH;
  const colW = (W - 2 * PAD_X) / data.length;
  const barW = Math.min(18, colW / 4);

  function ySpend(v: number) {
    return zeroY + (v / maxAbs) * halfH;
  }
  function yIncome(v: number) {
    return zeroY - (v / maxAbs) * halfH;
  }

  return (
    <div className="chart-card monthly-spend-chart">
      <h3 className="label">Cash flow</h3>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label="Monthly spend vs. income">
        <line
          className="zero-line"
          x1={PAD_X}
          x2={W - PAD_X}
          y1={zeroY}
          y2={zeroY}
        />
        {data.map((row, i) => {
          const cx = PAD_X + colW * (i + 0.5);
          const net = row.income - row.spend;
          const netH = (Math.abs(net) / maxAbs) * halfH;
          const netY = net >= 0 ? zeroY - netH : zeroY;
          return (
            <g
              key={row.month}
              style={{ cursor: "pointer" }}
              onClick={() => nav(`/spending?month=${row.month}`)}
            >
              <rect
                className="bar bar-spend"
                x={cx - barW * 1.5}
                width={barW}
                y={zeroY}
                height={ySpend(row.spend) - zeroY}
              >
                <title>{`${row.label} spend: ${formatUsd(row.spend)} — click to view`}</title>
              </rect>
              <rect
                className="bar bar-income"
                x={cx - barW / 2}
                width={barW}
                y={yIncome(row.income)}
                height={zeroY - yIncome(row.income)}
              >
                <title>{`${row.label} income: ${formatUsd(row.income)}`}</title>
              </rect>
              <rect
                className={`bar ${net >= 0 ? "bar-net-positive" : "bar-net-negative"}`}
                x={cx + barW / 2}
                width={barW}
                y={netY}
                height={netH}
              >
                <title>{`${row.label} net: ${formatUsd(net)}`}</title>
              </rect>
              <text
                x={cx}
                y={H - 8}
                textAnchor="middle"
                fontSize="10"
                fill="var(--text-mid)"
              >
                {row.label.slice(0, 3)}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

import { useQuery } from "@tanstack/react-query";
import { Page } from "../components/Page";
import { EmptyState } from "../components/EmptyState";
import { InstAvatar } from "../components/InstAvatar";
import { NetWorthChart, type SeriesPoint, type SeriesOption } from "../components/NetWorthChart";
import { CashflowChart, type MonthRow } from "../components/CashflowChart";
import { AnimatedUsd } from "../components/AnimatedNumber";
import { ApiError, getJson } from "../lib/api";
import { formatUsd } from "../lib/format";

type Account = {
  institution: string;
  logo: string | null;
  primary_color: string | null;
  name: string;
  type: string;
  mask: string | null;
  balance: number | null;
  available: number | null;
  plaid_account_id: string;
};
type OverviewData = {
  linked: boolean;
  cash: Account[];
  credit: Account[];
  investment: Account[];
  other: Account[];
  errors: string[];
  cash_total: number;
  credit_total: number;
  investment_total: number;
  net_total: number;
  monthly_cashflow: MonthRow[];
  has_monthly_data: boolean;
  networth_snapshot_count: number;
  networth_series_data: Record<string, SeriesPoint[]>;
  networth_series_options: SeriesOption[];
};

export default function DashboardPage() {
  const q = useQuery<OverviewData, ApiError>({
    queryKey: ["overview"],
    queryFn: () => getJson<OverviewData>("/api/overview"),
    retry: false,
  });

  if (q.isLoading) {
    return (
      <Page heading="Overview">
        <p className="muted">Loading…</p>
      </Page>
    );
  }
  if (q.error?.status === 401) {
    return (
      <Page heading="Overview">
        <EmptyState headline="Sign in to see your overview." />
      </Page>
    );
  }
  if (q.error || !q.data) {
    return (
      <Page heading="Overview">
        <EmptyState headline="Could not load overview." hint={q.error?.message} />
      </Page>
    );
  }
  return <OverviewView data={q.data} />;
}

function OverviewView({ data }: { data: OverviewData }) {
  if (!data.linked) {
    return (
      <Page heading="Overview">
        <EmptyState
          headline="No accounts linked yet."
          hint="Click the + button above to connect an account."
        />
      </Page>
    );
  }
  return (
    <Page heading="Overview">
      <p className="subtitle">Live balances from your connected accounts</p>
      <div className="totals" style={{ gridTemplateColumns: "repeat(4, 1fr)" }}>
        <div className="card net">
          <div className="label">Net Worth</div>
          <div className="value"><AnimatedUsd value={data.net_total} decimals={2} /></div>
        </div>
        <div className="card">
          <div className="label">Cash</div>
          <div className="value"><AnimatedUsd value={data.cash_total} decimals={2} /></div>
        </div>
        <div className="card">
          <div className="label">Investments</div>
          <div className="value"><AnimatedUsd value={data.investment_total} decimals={2} /></div>
        </div>
        <div className="card credit">
          <div className="label">Credit Owed</div>
          <div className="value"><AnimatedUsd value={data.credit_total} decimals={2} /></div>
        </div>
      </div>

      {(data.networth_snapshot_count > 0 || data.has_monthly_data) && (
        <div className="overview-charts">
          {data.networth_snapshot_count > 0 && (
            <NetWorthChart
              seriesData={data.networth_series_data}
              seriesOptions={data.networth_series_options}
            />
          )}
          {data.has_monthly_data && <CashflowChart data={data.monthly_cashflow} />}
        </div>
      )}

      <AccountBucket title="Cash" accounts={data.cash} showAvailable />
      <AccountBucket title="Investments" accounts={data.investment} />
      <AccountBucket title="Credit" accounts={data.credit} negative showAvailable />
      {data.other.length > 0 && <AccountBucket title="Other" accounts={data.other} />}

      {data.errors.length > 0 && (
        <div className="error-list">
          {data.errors.map((e, i) => (
            <p key={i} className="error">
              {e}
            </p>
          ))}
        </div>
      )}
    </Page>
  );
}

function AccountBucket({
  title, accounts, negative = false, showAvailable = false,
}: {
  title: string;
  accounts: Account[];
  negative?: boolean;
  showAvailable?: boolean;
}) {
  if (accounts.length === 0) return null;
  return (
    <section className="account-bucket">
      <h2>{title}</h2>
      <table className="account-table">
        <thead>
          <tr>
            <th>Institution</th>
            <th>Account</th>
            <th className="col-hide-mobile">Type</th>
            <th className="col-hide-mobile">Mask</th>
            {showAvailable && <th className="num col-hide-mobile">Available</th>}
            <th className="num">Balance</th>
          </tr>
        </thead>
        <tbody>
          {accounts.map((a) => (
            <tr key={a.plaid_account_id}>
              <td>
                <InstAvatar
                  name={a.institution}
                  logo={a.logo}
                  primaryColor={a.primary_color}
                />
                {a.institution}
              </td>
              <td>{a.name}</td>
              <td className="col-hide-mobile muted">{a.type}</td>
              <td className="col-hide-mobile muted">
                {a.mask ? `****${a.mask}` : "—"}
              </td>
              {showAvailable && (
                <td className="num col-hide-mobile muted">
                  {a.available == null ? "—" : formatUsd(a.available)}
                </td>
              )}
              <td className="num">
                {a.balance == null
                  ? "—"
                  : negative
                    ? formatUsd(-Math.abs(a.balance))
                    : formatUsd(a.balance)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

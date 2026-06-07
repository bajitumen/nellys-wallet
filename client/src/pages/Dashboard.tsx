import { useQuery } from "@tanstack/react-query";
import { Page } from "../components/Page";
import { EmptyState } from "../components/EmptyState";
import { InstAvatar } from "../components/InstAvatar";
import { ApiError, getJson } from "../lib/api";

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
type MonthRow = {
  month: string;
  label: string;
  spend: number;
  income: number;
  ts: number;
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
};

function formatUsd(n: number): string {
  return n.toLocaleString("en-US", { style: "currency", currency: "USD" });
}

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
      <div className="totals" style={{ gridTemplateColumns: "repeat(4, 1fr)" }}>
        <div className="card">
          <div className="label">Net Worth</div>
          <div className="value">{formatUsd(data.net_total)}</div>
        </div>
        <div className="card">
          <div className="label">Cash</div>
          <div className="value">{formatUsd(data.cash_total)}</div>
        </div>
        <div className="card">
          <div className="label">Investments</div>
          <div className="value">{formatUsd(data.investment_total)}</div>
        </div>
        <div className="card credit">
          <div className="label">Credit</div>
          <div className="value">{formatUsd(data.credit_total)}</div>
        </div>
      </div>

      <AccountBucket title="Cash" accounts={data.cash} />
      <AccountBucket title="Investments" accounts={data.investment} />
      <AccountBucket title="Credit" accounts={data.credit} negative />
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
  title, accounts, negative = false,
}: {
  title: string;
  accounts: Account[];
  negative?: boolean;
}) {
  if (accounts.length === 0) return null;
  return (
    <section className="account-bucket">
      <h2 className="rules-section-heading">{title}</h2>
      <table className="account-table">
        <tbody>
          {accounts.map((a) => (
            <tr key={a.plaid_account_id}>
              <td>
                <InstAvatar
                  name={a.institution}
                  logo={a.logo}
                  primaryColor={a.primary_color}
                />
                <span className="planning-acct-name">
                  {a.institution} · {a.name}
                </span>
                <span className="planning-acct-type muted">{a.type}</span>
              </td>
              <td className="num">
                {a.balance == null ? "—" : (negative ? "-" : "") + formatUsd(a.balance)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

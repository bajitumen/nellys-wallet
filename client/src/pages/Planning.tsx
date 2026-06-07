import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Page } from "../components/Page";
import { EmptyState } from "../components/EmptyState";
import { InstAvatar } from "../components/InstAvatar";
import { ApiError, getJson, postJson } from "../lib/api";

type Account = {
  id: string;
  institution: string;
  logo: string | null;
  primary_color: string | null;
  name: string;
  type: string;
  balance: number;
  bucket: string;
  sign: number;
};

type PlanningData = {
  accounts: Account[];
  rates: Record<string, number>;
  contributions: Record<string, number>;
  monthly_income: number | null;
  monthly_spend: number | null;
  avg_monthly_income: number;
  avg_monthly_spend: number;
};

function formatUsd(n: number): string {
  return n.toLocaleString("en-US", { style: "currency", currency: "USD" });
}

export default function PlanningPage() {
  const q = useQuery<PlanningData, ApiError>({
    queryKey: ["planning"],
    queryFn: () => getJson<PlanningData>("/api/planning"),
    retry: false,
  });

  if (q.isLoading) {
    return (
      <Page heading="Planning">
        <p className="muted">Loading…</p>
      </Page>
    );
  }
  if (q.error?.status === 401) {
    return (
      <Page heading="Planning">
        <EmptyState headline="Sign in to see Planning." />
      </Page>
    );
  }
  if (q.error) {
    return (
      <Page heading="Planning">
        <EmptyState headline="Could not load Planning." hint={q.error.message} />
      </Page>
    );
  }
  if (!q.data) return null;
  return <PlanningView data={q.data} />;
}

function PlanningView({ data }: { data: PlanningData }) {
  return (
    <Page heading="Planning">
      <p className="subtitle">Project balances forward using rates you set per account</p>
      {data.accounts.length === 0 ? (
        <EmptyState
          headline="No accounts with balances."
          hint="Click Refresh to pull current balances from your linked accounts."
        />
      ) : (
        <>
          <CashflowCard data={data} />
          <h2>Rates</h2>
          <RatesTable data={data} />
        </>
      )}
    </Page>
  );
}

function CashflowCard({ data }: { data: PlanningData }) {
  const qc = useQueryClient();
  const [income, setIncome] = useState<string>(
    data.monthly_income == null ? "" : data.monthly_income.toFixed(0),
  );
  const [spend, setSpend] = useState<string>(
    data.monthly_spend == null ? "" : data.monthly_spend.toFixed(0),
  );

  const save = useMutation({
    mutationFn: (vars: { field: "income" | "spend"; value: string }) =>
      postJson<{ ok: boolean; value: number | null }>("/planning/cashflow", vars),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["planning"] }),
  });

  const incomeNum = parseFloat(income) || 0;
  const spendNum = parseFloat(spend) || 0;
  const net = incomeNum - spendNum;

  return (
    <div className="chart-card planning-cashflow-card">
      <h3 className="planning-cashflow-title">Monthly cash flow</h3>
      <p className="planning-cashflow-help muted">
        Added to balances each projected month.
      </p>
      <label className="planning-cashflow-field">
        <span className="planning-cashflow-label">Income</span>
        <span className="planning-cashflow-input-wrap">
          <span className="prefix-left">$</span>
          <input
            type="number"
            className="numeric-input"
            step="1"
            min="0"
            placeholder={data.avg_monthly_income ? data.avg_monthly_income.toFixed(0) : "0"}
            value={income}
            onChange={(e) => setIncome(e.target.value)}
            onBlur={() => save.mutate({ field: "income", value: income })}
          />
        </span>
      </label>
      <label className="planning-cashflow-field">
        <span className="planning-cashflow-label">Spend</span>
        <span className="planning-cashflow-input-wrap">
          <span className="prefix-left">$</span>
          <input
            type="number"
            className="numeric-input"
            step="1"
            min="0"
            placeholder={data.avg_monthly_spend ? data.avg_monthly_spend.toFixed(0) : "0"}
            value={spend}
            onChange={(e) => setSpend(e.target.value)}
            onBlur={() => save.mutate({ field: "spend", value: spend })}
          />
        </span>
      </label>
      <div className="planning-cashflow-net">
        Net: <span>{formatUsd(net)}</span>/mo
      </div>
    </div>
  );
}

function RatesTable({ data }: { data: PlanningData }) {
  return (
    <table className="planning-rates-table">
      <thead>
        <tr>
          <th>Account</th>
          <th className="num">Balance</th>
          <th className="num">Interest rate</th>
          <th className="num">Monthly add</th>
        </tr>
      </thead>
      <tbody>
        {data.accounts.map((a) => (
          <RateRow
            key={a.id}
            account={a}
            initialRate={data.rates[a.id]}
            initialContribution={data.contributions[a.id]}
          />
        ))}
      </tbody>
    </table>
  );
}

function RateRow({
  account,
  initialRate,
  initialContribution,
}: {
  account: Account;
  initialRate: number | undefined;
  initialContribution: number | undefined;
}) {
  const [rate, setRate] = useState<string>(
    initialRate == null ? "" : initialRate.toFixed(2),
  );
  const [contrib, setContrib] = useState<string>(
    initialContribution == null ? "" : initialContribution.toFixed(0),
  );

  const saveRate = useMutation({
    mutationFn: (v: string) =>
      postJson(`/planning/rate/${encodeURIComponent(account.id)}`, { rate: v }),
  });
  const saveContrib = useMutation({
    mutationFn: (v: string) =>
      postJson(`/planning/contribution/${encodeURIComponent(account.id)}`, { value: v }),
  });

  return (
    <tr>
      <td>
        <InstAvatar
          name={account.institution}
          logo={account.logo}
          primaryColor={account.primary_color}
        />
        <span className="planning-acct-name">
          {account.institution} · {account.name}
        </span>
        <span className="planning-acct-type muted">{account.type}</span>
      </td>
      <td className="num">{formatUsd(account.balance)}</td>
      <td className="num">
        <span className="planning-rate-input-wrap">
          <input
            type="number"
            className="planning-rate-input numeric-input"
            step="0.01"
            min="0"
            placeholder="0.00"
            value={rate}
            onChange={(e) => setRate(e.target.value)}
            onBlur={() => saveRate.mutate(rate)}
          />
          <span className="prefix">%</span>
        </span>
      </td>
      <td className="num">
        <span className="planning-contrib-input-wrap">
          <span className="prefix-left">$</span>
          <input
            type="number"
            className="planning-contrib-input numeric-input"
            step="1"
            min="0"
            placeholder="0"
            value={contrib}
            onChange={(e) => setContrib(e.target.value)}
            onBlur={() => saveContrib.mutate(contrib)}
          />
        </span>
      </td>
    </tr>
  );
}

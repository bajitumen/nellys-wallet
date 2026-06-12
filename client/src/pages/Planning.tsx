import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Page } from "../components/Page";
import { EmptyState } from "../components/EmptyState";
import { InstAvatar } from "../components/InstAvatar";
import { PlanningChart, type ProjectionAccount } from "../components/PlanningChart";
import { useToast } from "../components/Toast";
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
  const qc = useQueryClient();
  const toast = useToast();

  const [rates, setRates] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      data.accounts.map((a) => [
        a.id, data.rates[a.id] == null ? "" : data.rates[a.id].toFixed(2),
      ]),
    ),
  );
  const [contribs, setContribs] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      data.accounts.map((a) => [
        a.id, data.contributions[a.id] == null ? "" : data.contributions[a.id].toFixed(0),
      ]),
    ),
  );
  const [income, setIncome] = useState<string>(
    data.monthly_income == null ? "" : data.monthly_income.toFixed(0),
  );
  const [spend, setSpend] = useState<string>(
    data.monthly_spend == null ? "" : data.monthly_spend.toFixed(0),
  );

  const saveRate = useMutation({
    mutationFn: (vars: { id: string; rate: string }) =>
      postJson(`/planning/rate/${encodeURIComponent(vars.id)}`, { rate: vars.rate }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["planning"] }),
    onError: (e: Error) => toast.error(`Could not save rate: ${e.message}`),
  });
  const saveContrib = useMutation({
    mutationFn: (vars: { id: string; value: string }) =>
      postJson(`/planning/contribution/${encodeURIComponent(vars.id)}`, { value: vars.value }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["planning"] }),
    onError: (e: Error) => toast.error(`Could not save contribution: ${e.message}`),
  });
  const saveCashflow = useMutation({
    mutationFn: (vars: { field: "income" | "spend"; value: string }) =>
      postJson("/planning/cashflow", vars),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["planning"] }),
    onError: (e: Error) => toast.error(`Could not save cashflow override: ${e.message}`),
  });

  const projAccounts: ProjectionAccount[] = data.accounts.map((a) => ({
    id: a.id,
    label: `${a.institution} · ${a.name}`,
    balance: a.balance,
    sign: a.sign,
    rateAnnual: parseFloat(rates[a.id]) || 0,
    monthlyContribution: parseFloat(contribs[a.id]) || 0,
  }));
  const netFlow = (parseFloat(income) || 0) - (parseFloat(spend) || 0);

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
          <div className="planning-top-row">
            <PlanningChart accounts={projAccounts} netMonthlyFlow={netFlow} />
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
                    onBlur={() => saveCashflow.mutate({ field: "income", value: income })}
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
                    onBlur={() => saveCashflow.mutate({ field: "spend", value: spend })}
                  />
                </span>
              </label>
              <div className="planning-cashflow-net">
                Net:{" "}
                <span style={{ color: netFlow > 0 ? "var(--positive)" : netFlow < 0 ? "var(--negative)" : undefined }}>
                  {formatUsd(netFlow)}
                </span>
                /mo
              </div>
            </div>
          </div>

          <h2>Rates</h2>
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
                <tr key={a.id}>
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
                  <td className="num">{formatUsd(a.balance)}</td>
                  <td className="num">
                    <span className="planning-rate-input-wrap">
                      <input
                        type="number"
                        className="planning-rate-input numeric-input"
                        step="0.01"
                        min="0"
                        placeholder="0.00"
                        value={rates[a.id] ?? ""}
                        onChange={(e) =>
                          setRates((p) => ({ ...p, [a.id]: e.target.value }))
                        }
                        onBlur={() =>
                          saveRate.mutate({ id: a.id, rate: rates[a.id] ?? "" })
                        }
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
                        value={contribs[a.id] ?? ""}
                        onChange={(e) =>
                          setContribs((p) => ({ ...p, [a.id]: e.target.value }))
                        }
                        onBlur={() =>
                          saveContrib.mutate({ id: a.id, value: contribs[a.id] ?? "" })
                        }
                      />
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </Page>
  );
}

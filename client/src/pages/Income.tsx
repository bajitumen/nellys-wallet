import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { Page } from "../components/Page";
import { EmptyState } from "../components/EmptyState";
import { MonthPickerCard, type MonthOption } from "../components/MonthPicker";
import { KebabMenu, type KebabAction } from "../components/KebabMenu";
import {
  RuleModal, type ExistingRule, type Primary, type RuleMatchOptions,
} from "../components/RuleModal";
import { ApiError, getJson, postJson } from "../lib/api";

type Payer = { name: string; total: number; count: number; color: string };
type Tx = {
  plaid_id: string;
  date: string;
  source: string;
  payer: string;
  name: string;
  amount: number;
  original_amount: number;
  color: string;
  dismissed: boolean;
  category_raw: string;
  detailed_raw: string | null;
  rule_id: number | null;
};
type IncomeData = {
  total: number;
  count: number;
  payers: Payer[];
  transactions: Tx[];
  sources: string[];
  current_source: string | null;
  month_options: MonthOption[];
  current_month: string;
  month_label: string;
  daily_avg: number;
  prev_month_change_pct: number | null;
  primaries: Primary[];
  rule_match_options: RuleMatchOptions;
  rules_by_id: Record<string, ExistingRule>;
};

function formatUsd(n: number): string {
  return n.toLocaleString("en-US", { style: "currency", currency: "USD" });
}
function shortDate(iso: string): string {
  const d = new Date(iso + "T00:00:00");
  return d.toLocaleDateString("en-US", { month: "short", day: "2-digit" });
}

export default function IncomePage() {
  const [searchParams] = useSearchParams();
  const month = searchParams.get("month") || undefined;
  const source = searchParams.get("source") || undefined;

  const q = useQuery<IncomeData, ApiError>({
    queryKey: ["income", month, source],
    queryFn: () => {
      const params = new URLSearchParams();
      if (month) params.set("month", month);
      if (source) params.set("source", source);
      const qs = params.toString();
      return getJson<IncomeData>(`/api/income${qs ? `?${qs}` : ""}`);
    },
    retry: false,
  });

  if (q.isLoading) {
    return (
      <Page heading="Income">
        <p className="muted">Loading…</p>
      </Page>
    );
  }
  if (q.error?.status === 401) {
    return (
      <Page heading="Income">
        <EmptyState headline="Sign in to see Income." />
      </Page>
    );
  }
  if (q.error || !q.data) {
    return (
      <Page heading="Income">
        <EmptyState headline="Could not load Income." hint={q.error?.message} />
      </Page>
    );
  }
  return <IncomeView data={q.data} />;
}

function IncomeView({ data }: { data: IncomeData }) {
  const qc = useQueryClient();
  const [modalTx, setModalTx] = useState<Tx | null>(null);
  const [editingRule, setEditingRule] = useState<ExistingRule | null>(null);

  const applyOverride = useMutation({
    mutationFn: (vars: { txId: string; payload: Record<string, unknown> }) =>
      postJson(`/transactions/${encodeURIComponent(vars.txId)}/override`, vars.payload),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["income"] }),
    onError: (e: Error) => alert(`Override failed: ${e.message}`),
  });

  function onKebab(tx: Tx, id: KebabAction["id"]) {
    if (id === "dismiss") applyOverride.mutate({ txId: tx.plaid_id, payload: { dismiss: true } });
    else if (id === "restore") applyOverride.mutate({ txId: tx.plaid_id, payload: { dismiss: false } });
    else if (id === "reset") applyOverride.mutate({ txId: tx.plaid_id, payload: { clear: true } });
    else if (id === "set-rule") {
      const existing = tx.rule_id ? data.rules_by_id[String(tx.rule_id)] || null : null;
      setEditingRule(existing);
      setModalTx(tx);
    }
  }

  const sourceOpts = useMemo(() => ["", ...data.sources], [data.sources]);

  return (
    <Page heading="Income">
      <div className="totals" style={{ gridTemplateColumns: "repeat(4, 1fr)" }}>
        <MonthPickerCard
          label={data.month_label}
          options={data.month_options}
          currentValue={data.current_month}
        />
        <div className="card">
          <div className="label">Total Income</div>
          <div className="value">{formatUsd(data.total)}</div>
        </div>
        <div className="card">
          <div className="label">Transactions</div>
          <div className="value">{data.count}</div>
        </div>
        <div className="card">
          <div className="label">Daily Avg</div>
          <div className="value">{formatUsd(data.daily_avg)}</div>
        </div>
      </div>

      {data.sources.length > 1 && (
        <p style={{ margin: "1rem 0", color: "var(--text-mid)" }}>
          Source:{" "}
          <SourceSelect current={data.current_source} options={sourceOpts} />
        </p>
      )}

      {data.transactions.length === 0 ? (
        <EmptyState
          headline="No income for this month."
          hint="Click Refresh to sync the latest transactions, or pick a different month."
        />
      ) : (
        <table className="tx-table">
          <thead>
            <tr>
              <th>Date</th>
              <th>Source</th>
              <th>Payer</th>
              <th className="col-hide-mobile">Description</th>
              <th className="num">Amount</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {data.transactions.map((tx) => (
              <tr
                key={tx.plaid_id}
                className={`tx-row${tx.dismissed ? " tx-dismissed" : ""}`}
                data-rule-id={tx.rule_id ?? undefined}
              >
                <td className="muted">{shortDate(tx.date)}</td>
                <td className="muted col-hide-mobile">{tx.source}</td>
                <td>
                  <span className="cat-dot" style={{ background: tx.color }} />
                  {tx.payer}
                </td>
                <td className="muted col-hide-mobile">{tx.name}</td>
                <td className="num">{formatUsd(tx.amount)}</td>
                <td className="tx-actions">
                  <KebabMenu
                    actions={
                      tx.dismissed
                        ? [
                            { id: "restore", label: "Restore" },
                            { id: "set-rule", label: tx.rule_id ? "Edit rule" : "Set rule" },
                          ]
                        : [
                            { id: "dismiss", label: "Dismiss" },
                            { id: "set-rule", label: tx.rule_id ? "Edit rule" : "Set rule" },
                            { id: "reset", label: "Reset to original" },
                          ]
                    }
                    onPick={(id) => onKebab(tx, id)}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <RuleModal
        open={modalTx !== null}
        options={data.rule_match_options}
        primaries={data.primaries}
        pageScope="income"
        editingRule={editingRule}
        rowMerchant={modalTx?.name}
        rowCategoryRaw={modalTx?.category_raw}
        rowDetailedRaw={modalTx?.detailed_raw ?? undefined}
        rowSource={modalTx?.source}
        onClose={() => setModalTx(null)}
        onSaved={() => {
          setModalTx(null);
          setEditingRule(null);
          qc.invalidateQueries({ queryKey: ["income"] });
        }}
      />
    </Page>
  );
}

function SourceSelect({
  current, options,
}: {
  current: string | null;
  options: string[];
}) {
  const [searchParams, setSearchParams] = useSearchParams();
  return (
    <select
      value={current || ""}
      onChange={(e) => {
        const p = new URLSearchParams(searchParams);
        if (e.target.value) p.set("source", e.target.value);
        else p.delete("source");
        setSearchParams(p);
      }}
    >
      {options.map((s) => (
        <option key={s} value={s}>
          {s || "All sources"}
        </option>
      ))}
    </select>
  );
}

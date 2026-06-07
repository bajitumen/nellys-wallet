import { useState } from "react";
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

type Subitem = { code: string; label: string; total: number; count: number };
type Category = {
  code: string; name: string; total: number; count: number;
  color: string; budget: number; subitems: Subitem[];
};
type Tx = {
  plaid_id: string;
  date: string;
  source: string;
  name: string;
  category: string;
  category_raw: string;
  detailed_raw: string | null;
  detailed_label: string | null;
  amount: number;
  original_amount: number;
  split_percentage: number | null;
  dismissed: boolean;
  rule_id: number | null;
};
type SpendingData = {
  total: number;
  count: number;
  categories: Category[];
  transactions: Tx[];
  errors: string[];
  sources: string[];
  current_source: string | null;
  categories_filter: string[];
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

export default function SpendingPage() {
  const [searchParams] = useSearchParams();
  const month = searchParams.get("month") || undefined;
  const source = searchParams.get("source") || undefined;
  const cats = searchParams.getAll("category");

  const q = useQuery<SpendingData, ApiError>({
    queryKey: ["spending", month, source, cats.join(",")],
    queryFn: () => {
      const p = new URLSearchParams();
      if (month) p.set("month", month);
      if (source) p.set("source", source);
      cats.forEach((c) => p.append("category", c));
      const qs = p.toString();
      return getJson<SpendingData>(`/api/spending${qs ? `?${qs}` : ""}`);
    },
    retry: false,
  });

  if (q.isLoading) {
    return (
      <Page heading="Spending">
        <p className="muted">Loading…</p>
      </Page>
    );
  }
  if (q.error?.status === 401) {
    return (
      <Page heading="Spending">
        <EmptyState headline="Sign in to see Spending." />
      </Page>
    );
  }
  if (q.error || !q.data) {
    return (
      <Page heading="Spending">
        <EmptyState headline="Could not load Spending." hint={q.error?.message} />
      </Page>
    );
  }
  return <SpendingView data={q.data} />;
}

function SpendingView({ data }: { data: SpendingData }) {
  const qc = useQueryClient();
  const [modalTx, setModalTx] = useState<Tx | null>(null);
  const [editingRule, setEditingRule] = useState<ExistingRule | null>(null);

  const applyOverride = useMutation({
    mutationFn: (vars: { txId: string; payload: Record<string, unknown> }) =>
      postJson(`/transactions/${encodeURIComponent(vars.txId)}/override`, vars.payload),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["spending"] }),
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

  return (
    <Page heading="Spending">
      <div className="totals" style={{ gridTemplateColumns: "repeat(4, 1fr)" }}>
        <MonthPickerCard
          label={data.month_label}
          options={data.month_options}
          currentValue={data.current_month}
        />
        <div className="card">
          <div className="label">Total Spent</div>
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

      {data.categories.length > 0 && (
        <div className="chart-card budget-summary-card">
          <div className="label">By category</div>
          <div className="stacked-bar" role="img" aria-label="Spending by category">
            {data.categories.map((c) => (
              <div
                key={c.code}
                className="stacked-bar-segment"
                style={{ flex: `${c.total} 0 0`, background: c.color }}
                data-tooltip={`${c.name}: ${formatUsd(c.total)}`}
              />
            ))}
          </div>
        </div>
      )}

      {data.transactions.length === 0 ? (
        <EmptyState
          headline="No spending for this month."
          hint="Click Refresh to sync the latest transactions, or pick a different month."
        />
      ) : (
        <table className="tx-table">
          <thead>
            <tr>
              <th>Date</th>
              <th>Source</th>
              <th>Description</th>
              <th className="col-hide-mobile">Category</th>
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
                <td>{tx.name}</td>
                <td className="muted col-hide-mobile">{tx.category}</td>
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
        pageScope="spending"
        editingRule={editingRule}
        rowMerchant={modalTx?.name}
        rowCategoryRaw={modalTx?.category_raw}
        rowDetailedRaw={modalTx?.detailed_raw ?? undefined}
        rowSource={modalTx?.source}
        onClose={() => setModalTx(null)}
        onSaved={() => {
          setModalTx(null);
          setEditingRule(null);
          qc.invalidateQueries({ queryKey: ["spending"] });
        }}
      />
    </Page>
  );
}

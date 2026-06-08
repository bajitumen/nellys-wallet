import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { Page } from "../components/Page";
import { EmptyState } from "../components/EmptyState";
import { MonthPickerCard, type MonthOption } from "../components/MonthPicker";
import { KebabMenu, type KebabAction } from "../components/KebabMenu";
import { StackedBar } from "../components/StackedBar";
import { SourceFilter } from "../components/SourceFilter";
import { useToast } from "../components/Toast";
import { useSorted } from "../lib/useSorted";
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
  source_logos: Record<string, { logo?: string | null; primary_color?: string | null }>;
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
  const toast = useToast();
  const [modalTx, setModalTx] = useState<Tx | null>(null);
  const [editingRule, setEditingRule] = useState<ExistingRule | null>(null);
  const [payerFilter, setPayerFilter] = useState<string[]>([]);

  function togglePayer(name: string) {
    setPayerFilter((prev) =>
      prev.includes(name) ? prev.filter((p) => p !== name) : [...prev, name],
    );
  }

  const filtered = payerFilter.length === 0
    ? data.transactions
    : data.transactions.filter((tx) => payerFilter.includes(tx.payer));
  type SortKey = "date" | "source" | "payer" | "description" | "amount";
  const { sorted: visibleTransactions, sort, toggle } = useSorted<Tx, SortKey>(
    filtered,
    { key: "date", dir: "desc" },
    {
      date: (tx) => tx.date,
      source: (tx) => tx.source.toLowerCase(),
      payer: (tx) => tx.payer.toLowerCase(),
      description: (tx) => tx.name.toLowerCase(),
      amount: (tx) => tx.amount,
    },
  );
  const sortAttrs = (key: SortKey) => ({
    "data-sort": "true",
    "data-dir": sort.key === key ? sort.dir : undefined,
  });

  const applyOverride = useMutation({
    mutationFn: (vars: { txId: string; payload: Record<string, unknown> }) =>
      postJson(`/transactions/${encodeURIComponent(vars.txId)}/override`, vars.payload),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["income"] }),
    onError: (e: Error) => toast.error(`Override failed: ${e.message}`),
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
    <Page heading="Income">
      <p className="subtitle">Grouped by payer</p>

      {data.sources.length > 1 && (
        <SourceFilter
          sources={data.sources}
          current={data.current_source}
          logos={data.source_logos}
        />
      )}

      <div className="totals" style={{ gridTemplateColumns: "repeat(4, 1fr)" }}>
        <MonthPickerCard
          label={data.month_label}
          options={data.month_options}
          currentValue={data.current_month}
        />
        <div className="card">
          <div className="label">Total Earned</div>
          <div className="value">
            {formatUsd(data.total)}
            {data.prev_month_change_pct != null && (
              <span
                className={`delta ${
                  data.prev_month_change_pct > 0 ? "delta-up" : "delta-down"
                }`}
              >
                {" ("}
                {data.prev_month_change_pct > 0 ? "+" : ""}
                {data.prev_month_change_pct.toFixed(0)}%{")"}
              </span>
            )}
          </div>
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

      {data.payers.length > 0 && (
        <>
          <div className="chart-card budget-summary-card">
            <div className="label">Payers</div>
            <StackedBar
              ariaLabel="Income by payer"
              segments={data.payers.map((p) => ({
                key: p.name,
                label: p.name,
                value: p.total,
                color: p.color,
                active: payerFilter.includes(p.name),
              }))}
              onToggle={togglePayer}
            />
          </div>
          <table className="category-table">
            <thead>
              <tr>
                <th className="cat-dot-col" />
                <th>Payer</th>
                <th className="num col-hide-mobile">Transactions</th>
                <th className="num col-hide-mobile">% of Total</th>
                <th className="num">Earned</th>
              </tr>
            </thead>
            <tbody>
              {data.payers.map((p) => {
                const pct = data.total > 0 ? (p.total / data.total) * 100 : 0;
                return (
                  <tr
                    key={p.name}
                    className={`category-row${payerFilter.includes(p.name) ? " active" : ""}`}
                    style={{ cursor: "pointer" }}
                    onClick={() => togglePayer(p.name)}
                  >
                    <td className="cat-dot-col">
                      <span className="subcat-toggle subcat-toggle-empty" aria-hidden="true" />
                      <span className="cat-dot" style={{ background: p.color }} />
                    </td>
                    <td>{p.name}</td>
                    <td className="num col-hide-mobile muted">{p.count}</td>
                    <td className="num col-hide-mobile muted">{pct.toFixed(0)}%</td>
                    <td className="num">{formatUsd(p.total)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </>
      )}

      <div className="tx-header" id="transactions">
        <h2 className="tx-header-title">Transactions</h2>
      </div>

      {visibleTransactions.length === 0 ? (
        <EmptyState
          headline={`No income in ${data.month_label}.`}
          hint="Click Refresh to sync the latest transactions, or pick a different month."
        />
      ) : (
        <table className="tx-table">
          <thead>
            <tr>
              <th {...sortAttrs("date")} onClick={() => toggle("date")}>Date</th>
              <th {...sortAttrs("source")} onClick={() => toggle("source")}>Source</th>
              <th {...sortAttrs("payer")} onClick={() => toggle("payer")}>Payer</th>
              <th
                className="col-hide-mobile"
                {...sortAttrs("description")}
                onClick={() => toggle("description")}
              >
                Description
              </th>
              <th className="num" {...sortAttrs("amount")} onClick={() => toggle("amount")}>
                Amount
              </th>
              <th />
            </tr>
          </thead>
          <tbody>
            {visibleTransactions.map((tx) => (
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
        key={modalTx?.plaid_id ?? "closed"}
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


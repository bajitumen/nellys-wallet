import { useState } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { Page } from "../components/Page";
import { EmptyState } from "../components/EmptyState";
import { MonthPickerCard, type MonthOption } from "../components/MonthPicker";
import { KebabMenu, type KebabAction } from "../components/KebabMenu";
import { StackedBar } from "../components/StackedBar";
import { SourceFilter } from "../components/SourceFilter";
import { useToast } from "../components/Toast";
import { TxFilters, applyTxFilters, type FilterColumn } from "../components/TxFilters";
import { AnimatedCount, AnimatedUsd } from "../components/AnimatedNumber";
import { SplitDialog } from "../components/SplitDialog";
import { clientCurrentMonth, scrollToTransactions } from "../lib/scrollToTransactions";
import { useSorted } from "../lib/useSorted";
import {
  RuleModal, type ExistingRule, type Primary, type RuleMatchOptions,
} from "../components/RuleModal";
import { ApiError, getJson, postJson } from "../lib/api";
import { formatUsd, shortDate } from "../lib/format";

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

export default function IncomePage() {
  const [searchParams] = useSearchParams();
  const month = searchParams.get("month") || clientCurrentMonth();
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
    placeholderData: keepPreviousData,
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

function PayersBlock({
  data, payerFilter, togglePayer,
}: {
  data: IncomeData;
  payerFilter: string[];
  togglePayer: (name: string) => void;
}) {
  type PayerSortKey = "name" | "count" | "pct" | "total";
  const { sorted, headerProps: payerHeader } = useSorted<Payer, PayerSortKey>(
    data.payers,
    { key: "total", dir: "desc" },
    {
      name: (p) => p.name.toLowerCase(),
      count: (p) => p.count,
      pct: (p) => (data.total > 0 ? p.total / data.total : 0),
      total: (p) => p.total,
    },
  );
  return (
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
            <th {...payerHeader("name")}>Payer</th>
            <th className="num col-hide-mobile" {...payerHeader("count")}>Transactions</th>
            <th className="num col-hide-mobile" {...payerHeader("pct")}>% of Total</th>
            <th className="num" {...payerHeader("total")}>Earned</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((p) => {
            const pct = data.total > 0 ? (p.total / data.total) * 100 : 0;
            return (
              <tr
                key={p.name}
                className={`category-row${payerFilter.includes(p.name) ? " active" : ""}`}
                style={{ cursor: "pointer" }}
                tabIndex={0}
                role="button"
                aria-pressed={payerFilter.includes(p.name)}
                onClick={() => togglePayer(p.name)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    togglePayer(p.name);
                  }
                }}
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
  );
}

function IncomeView({ data }: { data: IncomeData }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [searchParams, setSearchParams] = useSearchParams();
  const [modalTx, setModalTx] = useState<Tx | null>(null);
  const [editingRule, setEditingRule] = useState<ExistingRule | null>(null);
  const [splitTx, setSplitTx] = useState<Tx | null>(null);

  // Multi-column filter mirroring the original tx-filters.js on the income
  // page. Payer + Source + Date + Description, all URL-backed; chips render
  // above the table via TxFilters.
  const filterColumns: FilterColumn<Tx>[] = [
    {
      key: "date", label: "Date", urlParam: "f_date",
      getValue: (tx) => tx.date,
      getLabel: (tx) => shortDate(tx.date),
    },
    {
      key: "source", label: "Source", urlParam: "f_source",
      getValue: (tx) => tx.source,
    },
    {
      key: "payer", label: "Payer", urlParam: "payer",
      getValue: (tx) => tx.payer,
    },
    {
      key: "description", label: "Description", urlParam: "f_description",
      getValue: (tx) => tx.name,
    },
  ];
  const payerFilter = searchParams.getAll("payer");

  function togglePayer(name: string) {
    // replaceFilter semantics: a payer click clears all active chips and
    // sets just this one (or clears it if it was the only filter).
    const p = new URLSearchParams(searchParams);
    const isOnlyActive =
      p.getAll("payer").length === 1
      && p.get("payer") === name
      && filterColumns.every((c) => c.urlParam === "payer" || p.getAll(c.urlParam).length === 0);
    for (const c of filterColumns) p.delete(c.urlParam);
    if (!isOnlyActive) p.append("payer", name);
    setSearchParams(p);
    scrollToTransactions();
  }

  const filtered = applyTxFilters(data.transactions, filterColumns, searchParams);
  // Filtered totals so the cards reflect the active chip set instead of
  // disagreeing with the table below.
  const filteredTotal = filtered.reduce((s, tx) => s + Math.abs(tx.amount), 0);
  const filteredCount = filtered.length;
  const isFiltered = filterColumns.some((c) => searchParams.getAll(c.urlParam).length > 0);
  type SortKey = "date" | "source" | "payer" | "description" | "amount";
  const { sorted: visibleTransactions, headerProps: txHeader } = useSorted<Tx, SortKey>(
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
    else if (id === "split") setSplitTx(tx);
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
          <div className="label">{isFiltered ? "Filtered Earned" : "Total Earned"}</div>
          <div className="value">
            <AnimatedUsd value={isFiltered ? filteredTotal : data.total} decimals={2} />
            {!isFiltered && data.prev_month_change_pct != null && (
              <span
                className={`delta ${
                  data.prev_month_change_pct > 0 ? "delta-good" : "delta-bad"
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
          <div className="value"><AnimatedCount value={isFiltered ? filteredCount : data.count} /></div>
        </div>
        <div className="card">
          <div className="label">Daily Avg</div>
          <div className="value"><AnimatedUsd value={data.daily_avg} decimals={2} /></div>
        </div>
      </div>

      {data.total > 0 && (
        <PayersBlock data={data} payerFilter={payerFilter} togglePayer={togglePayer} />
      )}

      <TxFilters rows={data.transactions} columns={filterColumns} />

      {visibleTransactions.length === 0 ? (
        <EmptyState
          headline={
            filtered.length === 0 && data.transactions.length > 0
              ? "No transactions match the selected filters."
              : `No income in ${data.month_label}.`
          }
          hint={
            filtered.length === 0 && data.transactions.length > 0
              ? "Remove a chip above to broaden the view."
              : "Click Refresh to sync the latest transactions, or pick a different month."
          }
        />
      ) : (
        <table className="tx-table">
          <thead>
            <tr>
              <th {...txHeader("date")}>Date</th>
              <th {...txHeader("source")}>Source</th>
              <th {...txHeader("payer")}>Payer</th>
              <th className="col-hide-mobile" {...txHeader("description")}>Description</th>
              <th className="num" {...txHeader("amount")}>Amount</th>
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
                <td>{tx.payer}</td>
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
                            { id: "split", label: "Split" },
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
          for (const key of ["rules", "spending", "income", "overview", "budget"] as const) {
            qc.invalidateQueries({ queryKey: [key] });
          }
        }}
      />

      <SplitDialog
        open={splitTx !== null}
        amount={splitTx?.original_amount ?? 0}
        onClose={() => setSplitTx(null)}
        onSave={(amount, split_percentage) => {
          if (!splitTx) return;
          applyOverride.mutate({
            txId: splitTx.plaid_id,
            payload: { amount, split_percentage },
          });
          setSplitTx(null);
        }}
      />
    </Page>
  );
}


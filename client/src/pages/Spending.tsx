import { Fragment, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { Page } from "../components/Page";
import { EmptyState } from "../components/EmptyState";
import { MonthPickerCard, type MonthOption } from "../components/MonthPicker";
import { KebabMenu, type KebabAction } from "../components/KebabMenu";
import { StackedBar } from "../components/StackedBar";
import { SourceFilter } from "../components/SourceFilter";
import { IconCaretDown } from "../components/icons";
import { useToast } from "../components/Toast";
import { useSorted } from "../lib/useSorted";
import {
  RuleModal, type ExistingRule, type Primary, type RuleMatchOptions,
} from "../components/RuleModal";
import { InlineDropdown } from "../components/InlineDropdown";
import { ApiError, getJson, postJson } from "../lib/api";

type Subitem = { code: string; name: string; total: number; count: number; budget: number };
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
type Detailed = { code: string; label: string };
type SpendingData = {
  total: number;
  count: number;
  categories: Category[];
  transactions: Tx[];
  errors: string[];
  sources: string[];
  current_source: string | null;
  source_logos: Record<string, { logo?: string | null; primary_color?: string | null }>;
  categories_filter: string[];
  month_options: MonthOption[];
  current_month: string;
  month_label: string;
  daily_avg: number;
  prev_month_change_pct: number | null;
  primaries: Primary[];
  taxonomy: Record<string, Detailed[]>;
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

function CategoryTable({
  data, toggleCategoryFilter,
}: {
  data: SpendingData;
  toggleCategoryFilter: (code: string) => void;
}) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  function toggleExpand(code: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(code)) next.delete(code);
      else next.add(code);
      return next;
    });
  }
  const showBudget = !data.current_source;
  return (
    <table className="category-table">
      <thead>
        <tr>
          <th />
          <th>Category</th>
          <th className="num col-hide-mobile">Transactions</th>
          <th className="num col-hide-mobile">% of Total</th>
          {showBudget && <th className="num col-hide-mobile">Budget</th>}
          <th className="num">Spent</th>
          {showBudget && <th className="num col-hide-mobile">Difference</th>}
        </tr>
      </thead>
      <tbody>
        {data.categories.map((c) => {
          const pct = data.total > 0 ? (c.total / data.total) * 100 : 0;
          const diff = c.budget - c.total;
          const overspent = c.budget > 0 && diff < 0;
          const active = data.categories_filter.includes(c.code);
          const hasSubitems = c.subitems.some((s) => s.total > 0 || s.budget > 0);
          const isExpanded = expanded.has(c.code);
          return (
            <Fragment key={c.code}>
              <tr
                className={`category-row${active ? " active" : ""}`}
                style={{ cursor: "pointer" }}
              >
                <td
                  className="cat-dot-col"
                  onClick={() => {
                    if (hasSubitems) toggleExpand(c.code);
                    else toggleCategoryFilter(c.code);
                  }}
                >
                  {hasSubitems ? (
                    <button
                      type="button"
                      className="subcat-toggle"
                      onClick={(e) => {
                        e.stopPropagation();
                        toggleExpand(c.code);
                      }}
                      aria-label={`Show ${c.name} subcategories`}
                      aria-expanded={isExpanded}
                    >
                      <IconCaretDown />
                    </button>
                  ) : (
                    <span className="subcat-toggle subcat-toggle-empty" aria-hidden="true" />
                  )}
                  <span className="cat-dot" style={{ background: c.color }} />
                </td>
                <td onClick={() => toggleCategoryFilter(c.code)}>{c.name}</td>
                <td className="num col-hide-mobile muted">{c.count}</td>
                <td className="num col-hide-mobile muted">{pct.toFixed(0)}%</td>
                {showBudget && (
                  <td className="num col-hide-mobile muted">
                    {c.budget > 0 ? formatUsd(c.budget) : "—"}
                  </td>
                )}
                <td className="num" onClick={() => toggleCategoryFilter(c.code)}>
                  {formatUsd(c.total)}
                </td>
                {showBudget && (
                  <td
                    className={`num col-hide-mobile${
                      overspent ? " spend-bad" : c.budget > 0 ? " spend-good" : ""
                    }`}
                  >
                    {c.budget > 0
                      ? `${diff >= 0 ? "+" : ""}${formatUsd(diff)}`
                      : "—"}
                  </td>
                )}
              </tr>
              {isExpanded &&
                c.subitems
                  .filter((s) => s.total > 0 || s.budget > 0)
                  .map((s) => {
                    const subPct = data.total > 0 ? (s.total / data.total) * 100 : 0;
                    const subDiff = s.budget - s.total;
                    return (
                      <tr key={s.code} className="subcategory-row">
                        <td />
                        <td className="subcat-label">{s.name}</td>
                        <td className="num col-hide-mobile muted">{s.count}</td>
                        <td className="num col-hide-mobile muted">{subPct.toFixed(0)}%</td>
                        {showBudget && (
                          <td className="num col-hide-mobile muted">
                            {s.budget > 0 ? formatUsd(s.budget) : "—"}
                          </td>
                        )}
                        <td className="num">{s.total > 0 ? formatUsd(s.total) : ""}</td>
                        {showBudget && (
                          <td className="num col-hide-mobile muted">
                            {s.budget > 0 ? formatUsd(subDiff) : "—"}
                          </td>
                        )}
                      </tr>
                    );
                  })}
            </Fragment>
          );
        })}
      </tbody>
    </table>
  );
}

function SpendingView({ data }: { data: SpendingData }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [searchParams, setSearchParams] = useSearchParams();
  const [modalTx, setModalTx] = useState<Tx | null>(null);
  const [editingRule, setEditingRule] = useState<ExistingRule | null>(null);

  type SortKey = "date" | "source" | "name" | "category" | "item" | "amount";
  const { sorted: sortedTransactions, sort, toggle } = useSorted<Tx, SortKey>(
    data.transactions,
    { key: "date", dir: "desc" },
    {
      date: (tx) => tx.date,
      source: (tx) => tx.source.toLowerCase(),
      name: (tx) => tx.name.toLowerCase(),
      category: (tx) => tx.category.toLowerCase(),
      item: (tx) => (tx.detailed_label || "").toLowerCase(),
      amount: (tx) => tx.amount,
    },
  );
  const sortAttrs = (key: SortKey) => ({
    "data-sort": "true",
    "data-dir": sort.key === key ? sort.dir : undefined,
  });

  function toggleCategoryFilter(code: string) {
    const p = new URLSearchParams(searchParams);
    const current = p.getAll("category");
    p.delete("category");
    if (current.includes(code)) {
      current.filter((c) => c !== code).forEach((c) => p.append("category", c));
    } else {
      [...current, code].forEach((c) => p.append("category", c));
    }
    setSearchParams(p);
  }

  const applyOverride = useMutation({
    mutationFn: (vars: { txId: string; payload: Record<string, unknown> }) =>
      postJson(`/transactions/${encodeURIComponent(vars.txId)}/override`, vars.payload),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["spending"] }),
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
    <Page heading="Spending">
      <p className="subtitle">Grouped by category</p>

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
          <div className="label">Total Spent</div>
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

      {data.categories.length > 0 && (
        <div className="chart-card budget-summary-card">
          <div className="label">Breakdown</div>
          <StackedBar
            ariaLabel="Spending by category"
            segments={data.categories.map((c) => ({
              key: c.code,
              label: c.name,
              value: c.total,
              color: c.color,
              active: data.categories_filter.includes(c.code),
            }))}
            onToggle={toggleCategoryFilter}
          />
        </div>
      )}

      {data.categories.length > 0 && (
        <CategoryTable
          data={data}
          toggleCategoryFilter={toggleCategoryFilter}
        />
      )}

      <div className="tx-header" id="transactions">
        <h2 className="tx-header-title">Transactions</h2>
      </div>

      {data.transactions.length === 0 ? (
        <EmptyState
          headline={`No spending in ${data.month_label}.`}
          hint="Click Refresh to sync the latest transactions, or pick a different month."
        />
      ) : (
        <table className="tx-table">
          <thead>
            <tr>
              <th {...sortAttrs("date")} onClick={() => toggle("date")}>Date</th>
              <th {...sortAttrs("source")} onClick={() => toggle("source")}>Source</th>
              <th {...sortAttrs("name")} onClick={() => toggle("name")}>Description</th>
              <th
                className="col-hide-mobile"
                {...sortAttrs("category")}
                onClick={() => toggle("category")}
              >
                Category
              </th>
              <th
                className="col-hide-mobile"
                {...sortAttrs("item")}
                onClick={() => toggle("item")}
              >
                Item
              </th>
              <th className="num" {...sortAttrs("amount")} onClick={() => toggle("amount")}>
                Amount
              </th>
              <th />
            </tr>
          </thead>
          <tbody>
            {sortedTransactions.map((tx) => (
              <tr
                key={tx.plaid_id}
                className={`tx-row${tx.dismissed ? " tx-dismissed" : ""}`}
                data-rule-id={tx.rule_id ?? undefined}
              >
                <td className="muted">{shortDate(tx.date)}</td>
                <td className="muted col-hide-mobile">{tx.source}</td>
                <td>{tx.name}</td>
                <td className="muted col-hide-mobile">
                  <InlineDropdown
                    options={data.primaries.map((p) => ({ value: p.code, label: p.label }))}
                    value={tx.category_raw}
                    onChange={(opt) =>
                      applyOverride.mutate({
                        txId: tx.plaid_id,
                        payload: { category: opt.value, detailed: null },
                      })
                    }
                    ariaLabel="Category"
                  />
                </td>
                <td className="muted col-hide-mobile">
                  <InlineDropdown
                    options={[
                      { value: "", label: "—" },
                      ...(data.taxonomy[tx.category_raw] || []).map((d) => ({
                        value: d.code, label: d.label,
                      })),
                    ]}
                    value={tx.detailed_raw || ""}
                    onChange={(opt) =>
                      applyOverride.mutate({
                        txId: tx.plaid_id,
                        payload: { detailed: opt.value || null },
                      })
                    }
                    ariaLabel="Item"
                  />
                </td>
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

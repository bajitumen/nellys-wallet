import { Fragment, useState } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { Page } from "../components/Page";
import { EmptyState } from "../components/EmptyState";
import { MonthPickerCard, type MonthOption } from "../components/MonthPicker";
import { KebabMenu, type KebabAction } from "../components/KebabMenu";
import { StackedBar } from "../components/StackedBar";
import { SourceFilter } from "../components/SourceFilter";
import { IconCaretDown } from "../components/icons";
import { useToast } from "../components/Toast";
import { TxFilters, applyTxFilters, type FilterColumn } from "../components/TxFilters";
import { AnimatedCount, AnimatedUsd } from "../components/AnimatedNumber";
import { SplitDialog } from "../components/SplitDialog";
import { clientCurrentMonth, scrollToTransactions } from "../lib/scrollToTransactions";
import { useSorted } from "../lib/useSorted";
import {
  RuleModal, type ExistingRule, type Primary, type RuleMatchOptions,
} from "../components/RuleModal";
import { InlineDropdown } from "../components/InlineDropdown";
import { ApiError, getJson, postJson } from "../lib/api";
import { formatUsd, shortDate } from "../lib/format";

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

export default function SpendingPage() {
  const [searchParams] = useSearchParams();
  const month = searchParams.get("month") || clientCurrentMonth();
  const source = searchParams.get("source") || undefined;
  const cats = searchParams.getAll("category");

  const q = useQuery<SpendingData, ApiError>({
    queryKey: ["spending", month, source],
    queryFn: () => {
      const p = new URLSearchParams();
      if (month) p.set("month", month);
      if (source) p.set("source", source);
      // Note: ?category=... is still sent so the server can echo back
      // categories_filter (used for the breakdown highlight), but the row
      // filter is applied client-side. Including cats in the queryKey would
      // refetch the full month's data on every chip click for no payload
      // difference.
      cats.forEach((c) => p.append("category", c));
      const qs = p.toString();
      return getJson<SpendingData>(`/api/spending${qs ? `?${qs}` : ""}`);
    },
    retry: false,
    placeholderData: keepPreviousData,
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
  data, toggleCategoryFilter, toggleItemFilter,
}: {
  data: SpendingData;
  toggleCategoryFilter: (code: string) => void;
  toggleItemFilter: (code: string) => void;
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

  type CatSortKey = "name" | "count" | "pct" | "budget" | "total" | "diff";
  const { sorted: sortedCategories, headerProps: catHeader } = useSorted<Category, CatSortKey>(
    data.categories,
    { key: "total", dir: "desc" },
    {
      name: (c) => c.name.toLowerCase(),
      count: (c) => c.count,
      pct: (c) => (data.total > 0 ? c.total / data.total : 0),
      budget: (c) => c.budget,
      total: (c) => c.total,
      diff: (c) => c.budget - c.total,
    },
  );

  return (
    <table className="category-table">
      <thead>
        <tr>
          <th />
          <th {...catHeader("name")}>Category</th>
          <th className="num col-hide-mobile" {...catHeader("count")}>Transactions</th>
          <th className="num col-hide-mobile" {...catHeader("pct")}>% of Total</th>
          {showBudget && (
            <th className="num col-hide-mobile" {...catHeader("budget")}>Budget</th>
          )}
          <th className="num" {...catHeader("total")}>Spent</th>
          {showBudget && (
            <th className="num col-hide-mobile" {...catHeader("diff")}>Difference</th>
          )}
        </tr>
      </thead>
      <tbody>
        {sortedCategories.map((c) => {
          const pct = data.total > 0 ? (c.total / data.total) * 100 : 0;
          const diff = c.budget - c.total;
          const overspent = c.budget > 0 && diff < 0;
          const active = data.categories_filter.includes(c.code);
          const hasSubitems = c.subitems.length > 0;
          const isExpanded = expanded.has(c.code);
          return (
            <Fragment key={c.code}>
              <tr
                className={`category-row${active ? " active" : ""}`}
                style={{ cursor: "pointer" }}
                tabIndex={0}
                role="button"
                aria-pressed={active}
                onClick={() => toggleCategoryFilter(c.code)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    toggleCategoryFilter(c.code);
                  }
                }}
              >
                <td className="cat-dot-col">
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
                <td>{c.name}</td>
                <td className="num col-hide-mobile muted">{c.count}</td>
                <td className="num col-hide-mobile muted">{pct.toFixed(0)}%</td>
                {showBudget && (
                  <td className="num col-hide-mobile muted">
                    {c.budget > 0 ? formatUsd(c.budget) : "—"}
                  </td>
                )}
                <td className="num">{formatUsd(c.total)}</td>
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
                c.subitems.map((s) => {
                    const subPct = data.total > 0 ? (s.total / data.total) * 100 : 0;
                    const subDiff = s.budget - s.total;
                    return (
                      <tr
                        key={s.code}
                        className="subcategory-row"
                        style={{ cursor: "pointer" }}
                        tabIndex={0}
                        role="button"
                        onClick={() => toggleItemFilter(s.code)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter" || e.key === " ") {
                            e.preventDefault();
                            toggleItemFilter(s.code);
                          }
                        }}
                      >
                        <td />
                        <td className="subcat-label">{s.name}</td>
                        <td className="num col-hide-mobile muted">{s.count}</td>
                        <td className="num col-hide-mobile muted">{subPct.toFixed(0)}%</td>
                        {showBudget && (
                          <td className="num col-hide-mobile muted">
                            {s.budget > 0 ? formatUsd(s.budget) : "—"}
                          </td>
                        )}
                        <td className="num">{formatUsd(s.total)}</td>
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
  const [splitTx, setSplitTx] = useState<Tx | null>(null);

  // Multi-column filter (matches the original tx-filters.js). category/source/
  // item/date all flow through URL params; chips render via TxFilters above
  // the table; category_filter mirrors `?category=` for the chart highlight.
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
      key: "category", label: "Category", urlParam: "category",
      getValue: (tx) => tx.category_raw,
      getLabel: (tx) => tx.category,
    },
    {
      key: "item", label: "Item", urlParam: "f_item",
      getValue: (tx) => tx.detailed_raw || "",
      getLabel: (tx) => tx.detailed_label || "(none)",
    },
  ];
  const filteredTransactions = applyTxFilters(data.transactions, filterColumns, searchParams);
  // Derive the active category set from the URL so the highlight tracks chip
  // changes without needing the server to re-echo categories_filter (which
  // would require a refetch).
  const activeCategoryCodes = searchParams.getAll("category");
  const dataWithLiveFilter = { ...data, categories_filter: activeCategoryCodes };
  // Sum magnitudes — refunds (negative spend) would otherwise net out of
  // the headline, making a filter toggle move the total by more than the
  // visible rows.
  const filteredTotal = filteredTransactions.reduce(
    (s, tx) => s + Math.abs(tx.amount), 0,
  );
  const filteredCount = filteredTransactions.length;
  const isFiltered = activeCategoryCodes.length > 0
    || filterColumns.some((c) => c.urlParam !== "category" && searchParams.getAll(c.urlParam).length > 0);

  type SortKey = "date" | "source" | "name" | "category" | "item" | "amount";
  const { sorted: sortedTransactions, headerProps: txHeader } = useSorted<Tx, SortKey>(
    filteredTransactions,
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

  function replaceFilter(urlParam: string, value: string) {
    // Original replaceFilter semantics: clicking a category row, subcategory
    // row, or breakdown segment clears every active filter and sets just this
    // one. Clicking the same value again clears it. Add-without-clearing is
    // the job of the + button in TxFilters.
    const p = new URLSearchParams(searchParams);
    const isOnlyActive =
      p.getAll(urlParam).length === 1
      && p.get(urlParam) === value
      && filterColumns.every((c) => c.urlParam === urlParam || p.getAll(c.urlParam).length === 0);
    for (const c of filterColumns) p.delete(c.urlParam);
    if (!isOnlyActive) p.append(urlParam, value);
    setSearchParams(p);
    scrollToTransactions();
  }
  function toggleCategoryFilter(code: string) {
    replaceFilter("category", code);
  }
  function toggleItemFilter(code: string) {
    replaceFilter("f_item", code);
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
    else if (id === "split") setSplitTx(tx);
    else if (id === "set-rule") {
      const existing = tx.rule_id ? data.rules_by_id[String(tx.rule_id)] || null : null;
      setEditingRule(existing);
      setModalTx(tx);
    }
  }

  return (
    <Page heading="Spending">
      <p className="subtitle">Grouped by category</p>

      {data.errors.length > 0 && (
        <div className="error-list">
          {data.errors.map((e, i) => (
            <p key={i} className="error">{e}</p>
          ))}
        </div>
      )}

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
          <div className="label">{isFiltered ? "Filtered Spent" : "Total Spent"}</div>
          <div className="value">
            <AnimatedUsd value={isFiltered ? filteredTotal : data.total} decimals={2} />
            {!isFiltered && data.prev_month_change_pct != null && (
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
          <div className="value"><AnimatedCount value={isFiltered ? filteredCount : data.count} /></div>
        </div>
        <div className="card">
          <div className="label">Daily Avg</div>
          <div className="value"><AnimatedUsd value={data.daily_avg} decimals={2} /></div>
        </div>
      </div>

      {data.total > 0 && (
        <div className="chart-card budget-summary-card">
          <div className="label">Breakdown</div>
          <StackedBar
            ariaLabel="Spending by category"
            segments={data.categories
              .filter((c) => c.total > 0)
              .map((c) => ({
                key: c.code,
                label: c.name,
                value: c.total,
                color: c.color,
                active: activeCategoryCodes.includes(c.code),
              }))}
            onToggle={toggleCategoryFilter}
          />
        </div>
      )}

      {data.total > 0 && (
        <CategoryTable
          data={dataWithLiveFilter}
          toggleCategoryFilter={toggleCategoryFilter}
          toggleItemFilter={toggleItemFilter}
        />
      )}

      <TxFilters rows={data.transactions} columns={filterColumns} />

      {filteredTransactions.length === 0 ? (
        <EmptyState
          headline={
            data.transactions.length === 0
              ? `No spending in ${data.month_label}.`
              : "No transactions match the active filters."
          }
          hint={
            data.transactions.length === 0
              ? "Try a different month from the dropdown above, or your linked accounts may still be preparing recent transactions."
              : "Remove a chip above to broaden the view."
          }
        />
      ) : (
        <table className="tx-table">
          <thead>
            <tr>
              <th {...txHeader("date")}>Date</th>
              <th {...txHeader("source")}>Source</th>
              <th {...txHeader("name")}>Description</th>
              <th className="col-hide-mobile" {...txHeader("category")}>Category</th>
              <th className="col-hide-mobile" {...txHeader("item")}>Item</th>
              <th className="num" {...txHeader("amount")}>Amount</th>
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

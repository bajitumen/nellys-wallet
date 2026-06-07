import { Fragment, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { Page } from "../components/Page";
import { EmptyState } from "../components/EmptyState";
import { MonthPickerCard, type MonthOption } from "../components/MonthPicker";
import { KebabMenu, type KebabAction } from "../components/KebabMenu";
import { FilterChips } from "../components/FilterChips";
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
  return (
    <table className="category-table">
      <thead>
        <tr>
          <th>Category</th>
          <th className="num">Spent</th>
          <th className="num col-hide-mobile">Budget</th>
          <th className="num col-hide-mobile">Remaining</th>
        </tr>
      </thead>
      <tbody>
        {data.categories.map((c) => {
          const remaining = c.budget - c.total;
          const overspent = c.budget > 0 && remaining < 0;
          const active = data.categories_filter.includes(c.code);
          const hasSubitems = c.subitems.some((s) => s.total > 0 || s.budget > 0);
          const isExpanded = expanded.has(c.code);
          return (
            <Fragment key={c.code}>
              <tr
                className={`category-row${active ? " active" : ""}`}
                style={{ cursor: "pointer" }}
              >
                <td onClick={() => toggleCategoryFilter(c.code)}>
                  <span className="cat-dot" style={{ background: c.color }} />
                  {c.name}
                  <span className="muted" style={{ marginLeft: "0.5rem" }}>
                    ({c.count})
                  </span>
                  {hasSubitems && (
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        toggleExpand(c.code);
                      }}
                      style={{
                        background: "transparent",
                        border: "none",
                        color: "var(--text-mid)",
                        cursor: "pointer",
                        marginLeft: "0.4rem",
                      }}
                      aria-label={isExpanded ? "Collapse" : "Expand"}
                    >
                      {isExpanded ? "▾" : "▸"}
                    </button>
                  )}
                </td>
                <td className="num" onClick={() => toggleCategoryFilter(c.code)}>
                  {formatUsd(c.total)}
                </td>
                <td className="num col-hide-mobile muted">
                  {c.budget > 0 ? formatUsd(c.budget) : "—"}
                </td>
                <td
                  className={`num col-hide-mobile${
                    overspent ? " spend-bad" : c.budget > 0 ? " spend-good" : ""
                  }`}
                >
                  {c.budget > 0 ? formatUsd(remaining) : "—"}
                </td>
              </tr>
              {isExpanded &&
                c.subitems
                  .filter((s) => s.total > 0 || s.budget > 0)
                  .map((s) => (
                    <tr key={s.code} className="subcategory-row">
                      <td className="subcat-label">{s.name}</td>
                      <td className="num">{s.total > 0 ? formatUsd(s.total) : ""}</td>
                      <td className="num col-hide-mobile muted">
                        {s.budget > 0 ? formatUsd(s.budget) : "—"}
                      </td>
                      <td className="num col-hide-mobile muted">
                        {s.budget > 0 ? formatUsd(s.budget - s.total) : "—"}
                      </td>
                    </tr>
                  ))}
            </Fragment>
          );
        })}
      </tbody>
    </table>
  );
}

function SpendingView({ data }: { data: SpendingData }) {
  const qc = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const [modalTx, setModalTx] = useState<Tx | null>(null);
  const [editingRule, setEditingRule] = useState<ExistingRule | null>(null);

  const { sorted: sortedTransactions, sort, toggle } = useSorted<
    Tx,
    "date" | "name" | "category" | "amount"
  >(
    data.transactions,
    { key: "date", dir: "desc" },
    {
      date: (tx) => tx.date,
      name: (tx) => tx.name.toLowerCase(),
      category: (tx) => tx.category.toLowerCase(),
      amount: (tx) => tx.amount,
    },
  );
  const arrow = (key: "date" | "name" | "category" | "amount") =>
    sort.key === key ? (sort.dir === "asc" ? " ↑" : " ↓") : "";

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

      {data.categories.length > 0 && (
        <FilterChips
          ariaLabel="Filter by category"
          options={data.categories.map((c) => ({ value: c.code, label: c.name }))}
          selected={data.categories_filter}
          onToggle={toggleCategoryFilter}
        />
      )}

      {data.categories.length > 0 && (
        <CategoryTable
          data={data}
          toggleCategoryFilter={toggleCategoryFilter}
        />
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
              <th onClick={() => toggle("date")} style={{ cursor: "pointer" }}>
                Date{arrow("date")}
              </th>
              <th>Source</th>
              <th onClick={() => toggle("name")} style={{ cursor: "pointer" }}>
                Description{arrow("name")}
              </th>
              <th
                className="col-hide-mobile"
                onClick={() => toggle("category")}
                style={{ cursor: "pointer" }}
              >
                Category{arrow("category")}
              </th>
              <th className="col-hide-mobile">Item</th>
              <th className="num" onClick={() => toggle("amount")} style={{ cursor: "pointer" }}>
                Amount{arrow("amount")}
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

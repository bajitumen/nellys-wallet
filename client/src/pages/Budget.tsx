import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { Page } from "../components/Page";
import { EmptyState } from "../components/EmptyState";
import { MonthPickerCard, type MonthOption } from "../components/MonthPicker";
import { useToast } from "../components/Toast";
import { AnimatedUsd } from "../components/AnimatedNumber";
import { StackedBar } from "../components/StackedBar";
import { ApiError, getJson, postJson } from "../lib/api";
import { clientCurrentMonth, scrollToAnchor } from "../lib/scrollToTransactions";

type Subitem = { code: string; label: string; amount: number; actual: number };
type Group = {
  primary: string;
  primary_label: string;
  color: string;
  total: number;
  actual_total: number;
  subitems: Subitem[];
};
type BudgetData = {
  groups: Group[];
  month_options: MonthOption[];
  current_month: string;
  month_label: string;
  total_spent: number;
};

function formatUsd(n: number): string {
  return n.toLocaleString("en-US", { style: "currency", currency: "USD" });
}

export default function BudgetPage() {
  const [searchParams] = useSearchParams();
  const month = searchParams.get("month") || clientCurrentMonth();

  const q = useQuery<BudgetData, ApiError>({
    queryKey: ["budget", month],
    queryFn: () => getJson<BudgetData>(`/api/budget${month ? `?month=${month}` : ""}`),
    retry: false,
  });

  if (q.isLoading) {
    return (
      <Page heading="Budget">
        <p className="muted">Loading…</p>
      </Page>
    );
  }
  if (q.error?.status === 401) {
    return (
      <Page heading="Budget">
        <EmptyState headline="Sign in to see Budget." />
      </Page>
    );
  }
  if (q.error || !q.data) {
    return (
      <Page heading="Budget">
        <EmptyState headline="Could not load Budget." hint={q.error?.message} />
      </Page>
    );
  }
  return <BudgetView data={q.data} />;
}

function BudgetView({ data }: { data: BudgetData }) {
  const totalBudget = data.groups.reduce((sum, g) => sum + g.total, 0);
  const difference = totalBudget - data.total_spent;
  return (
    <Page heading="Budget">
      <p className="subtitle">Monthly spending targets by sub-category</p>
      <div className="totals" style={{ gridTemplateColumns: "repeat(4, 1fr)" }}>
        <MonthPickerCard
          label={data.month_label}
          options={data.month_options}
          currentValue={data.current_month}
        />
        <div className="card">
          <div className="label">Total Budget</div>
          <div className="value"><AnimatedUsd value={totalBudget} decimals={2} /></div>
        </div>
        <div className="card">
          <div className="label">Total Spent</div>
          <div className="value"><AnimatedUsd value={data.total_spent} decimals={2} /></div>
        </div>
        <div className={`card${difference < 0 ? " credit" : difference > 0 ? " net" : ""}`}>
          <div className="label">Difference</div>
          <div className="value"><AnimatedUsd value={difference} decimals={2} /></div>
        </div>
      </div>

      {totalBudget > 0 && (
        <div className="chart-card budget-summary-card">
          <div className="label">Budget breakdown</div>
          <StackedBar
            ariaLabel="Budget by category"
            segments={data.groups
              .filter((g) => g.total > 0)
              .map((g) => ({
                key: g.primary,
                label: g.primary_label,
                value: g.total,
                color: g.color,
              }))}
            onToggle={(primary) => scrollToAnchor(`budget-group-${primary}`)}
          />
        </div>
      )}

      {data.groups.length === 0 ? (
        <EmptyState headline="No budget categories yet." />
      ) : (
        <div className="budget-columns">
          {data.groups.map((g) => (
            <BudgetGroup key={g.primary} group={g} />
          ))}
        </div>
      )}
    </Page>
  );
}

function BudgetGroup({ group }: { group: Group }) {
  return (
    <div className="budget-group" id={`budget-group-${group.primary}`}>
      <div className="budget-group-header">
        <span className="budget-group-name">
          <span className="cat-dot" style={{ background: group.color }} />
          {group.primary_label}
        </span>
        <span className="budget-group-amounts">
          <span className="budget-group-spent">{formatUsd(group.actual_total)}</span>
          <span className="budget-group-divider">/</span>
          <span className="budget-group-total">{formatUsd(group.total)}</span>
        </span>
      </div>
      <table className="budget-table">
        <tbody>
          {group.subitems.map((s) => (
            <BudgetRow key={s.code} sub={s} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function BudgetRow({ sub }: { sub: Subitem }) {
  const qc = useQueryClient();
  const toast = useToast();
  const initialValue = sub.amount ? sub.amount.toFixed(2) : "";
  const [value, setValue] = useState<string>(initialValue);
  // Rows are keyed by sub.code (stable across months); without this reset
  // the prior month's value rides through onBlur and POSTs to the new month.
  useEffect(() => {
    setValue(initialValue);
  }, [initialValue]);
  const save = useMutation({
    mutationFn: (amount: string) =>
      postJson(`/budget/${encodeURIComponent(sub.code)}`, { amount }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["budget"] }),
    onError: (e: Error) => toast.error(`Could not save budget for ${sub.label}: ${e.message}`),
  });

  function onBlur() {
    // Skip the POST when the user tabbed through without touching the field.
    // Every save busts the spending cache + triggers a month recompute, and
    // an erroring server would stack a toast per untouched input on tab-out.
    if (value === initialValue) return;
    save.mutate(value);
  }

  const actualClass = sub.actual
    ? sub.amount && sub.actual <= sub.amount
      ? "spend-good"
      : "spend-bad"
    : "";

  return (
    <tr>
      <td className="budget-sub-label">{sub.label}</td>
      <td className={`budget-sub-actual ${actualClass}`.trim()}>
        {sub.actual ? formatUsd(sub.actual) : ""}
      </td>
      <td className="budget-sub-amount">
        <span className="budget-input-wrap">
          <span className="prefix">$</span>
          <input
            type="number"
            min="0"
            step="0.01"
            placeholder="0.00"
            inputMode="decimal"
            className="budget-input numeric-input"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onBlur={onBlur}
          />
        </span>
      </td>
    </tr>
  );
}

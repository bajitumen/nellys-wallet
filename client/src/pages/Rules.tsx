import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { Page } from "../components/Page";
import { EmptyState } from "../components/EmptyState";
import { ConfirmDialog } from "../components/ConfirmDialog";
import {
  RuleModal, type ExistingRule, type Primary, type RuleMatchOptions,
} from "../components/RuleModal";
import { IconPencil, IconTrash } from "../components/icons";
import { useToast } from "../components/Toast";
import { ApiError, deleteJson, getJson } from "../lib/api";

type ConditionLabel = { scope_label: string; op_label: string; match_value: string };
type RuleRow = {
  id: number;
  conditions: ConditionLabel[];
  conditions_logic: "all" | "any";
  action_label: string;
};
type RulesData = {
  active_tab: "spending" | "income" | "both";
  tab_rules: RuleRow[];
  has_rules: boolean;
  primaries: Primary[];
  rule_match_options: RuleMatchOptions;
  rules_by_id: Record<string, ExistingRule>;
};

const TABS: { id: "spending" | "income" | "both"; label: string }[] = [
  { id: "spending", label: "Spending" },
  { id: "income", label: "Income" },
  { id: "both", label: "Both" },
];

export default function RulesPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const tab = (searchParams.get("tab") as RulesData["active_tab"]) || "spending";
  const validTab = TABS.some((t) => t.id === tab) ? tab : "spending";

  const q = useQuery<RulesData, ApiError>({
    queryKey: ["rules", validTab],
    queryFn: () => getJson<RulesData>(`/api/rules?tab=${validTab}`),
    retry: false,
  });

  function selectTab(next: "spending" | "income" | "both") {
    const p = new URLSearchParams(searchParams);
    p.set("tab", next);
    setSearchParams(p);
  }

  if (q.isLoading) {
    return (
      <Page heading="Rules">
        <p className="muted">Loading…</p>
      </Page>
    );
  }
  if (q.error?.status === 401) {
    return (
      <Page heading="Rules">
        <EmptyState headline="Sign in to see Rules." />
      </Page>
    );
  }
  if (q.error || !q.data) {
    return (
      <Page heading="Rules">
        <EmptyState headline="Could not load Rules." hint={q.error?.message} />
      </Page>
    );
  }
  return <RulesView data={q.data} activeTab={validTab} onSelectTab={selectTab} />;
}

function RulesView({
  data, activeTab, onSelectTab,
}: {
  data: RulesData;
  activeTab: "spending" | "income" | "both";
  onSelectTab: (t: "spending" | "income" | "both") => void;
}) {
  const qc = useQueryClient();
  const toast = useToast();
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<ExistingRule | null>(null);
  const [pendingDeleteId, setPendingDeleteId] = useState<number | null>(null);

  const del = useMutation({
    mutationFn: (id: number) => deleteJson(`/rules/${id}`),
    onSuccess: () => {
      // Every data surface shows rule-affected rows; refetch them all so
      // the deleted rule's effects disappear immediately instead of leaving
      // stale "Edit rule" affordances pointing at a now-missing rule.
      for (const key of ["rules", "spending", "income", "overview", "budget"] as const) {
        qc.invalidateQueries({ queryKey: [key] });
      }
    },
    onError: (err: Error) => toast.error(`Failed to delete rule: ${err.message}`),
  });

  function openAdd() {
    setEditing(null);
    setModalOpen(true);
  }
  function openEdit(id: number) {
    const r = data.rules_by_id[String(id)];
    if (!r) return;
    setEditing(r);
    setModalOpen(true);
  }

  const addScope = activeTab === "both" ? "all" : activeTab;

  return (
    <Page heading="Rules">
      <p className="subtitle">Automatic actions applied to matching transactions on sync</p>

      <nav className="page-tabs rules-tabs" role="tablist">
        {TABS.map((t) => (
          <a
            key={t.id}
            className={`page-tab rules-tab${activeTab === t.id ? " active" : ""}`}
            href={`?tab=${t.id}`}
            role="tab"
            aria-selected={activeTab === t.id}
            onClick={(e) => {
              e.preventDefault();
              onSelectTab(t.id);
            }}
          >
            {t.label}
          </a>
        ))}
      </nav>

      <table className="rules-table" data-page-scope={activeTab}>
        <tbody>
          {data.tab_rules.length === 0 ? (
            <tr className="rule-empty">
              <td colSpan={2}>No rules yet</td>
            </tr>
          ) : (
            data.tab_rules.map((r) => (
              <tr key={r.id} data-rule-id={r.id}>
                <td className="rule-sentence">
                  <span className="rule-conn">If</span>
                  {r.conditions.map((c, i) => (
                    <span key={i}>
                      {i > 0 && (
                        <span className="rule-conn">
                          {r.conditions_logic === "all" ? "and" : "or"}
                        </span>
                      )}{" "}
                      <strong>{c.scope_label}</strong>{" "}
                      <span className="rule-conn">{c.op_label}</span>{" "}
                      <strong>{c.match_value}</strong>{" "}
                    </span>
                  ))}
                  <span className="rule-conn">then</span>{" "}
                  <strong>{r.action_label}</strong>
                </td>
                <td className="rule-actions">
                  <button
                    type="button"
                    className="rule-edit"
                    aria-label="Edit rule"
                    onClick={() => openEdit(r.id)}
                  >
                    <IconPencil />
                  </button>
                  <button
                    type="button"
                    className="rule-delete"
                    aria-label="Delete rule"
                    onClick={() => setPendingDeleteId(r.id)}
                  >
                    <IconTrash />
                  </button>
                </td>
              </tr>
            ))
          )}
          <tr className="rule-add-row">
            <td colSpan={2}>
              <button
                type="button"
                className="rule-add-inline"
                aria-label="Add new rule"
                onClick={openAdd}
              >
                <span className="rule-add-label">Add new rule</span>
                <span className="rule-add-plus">+</span>
              </button>
            </td>
          </tr>
        </tbody>
      </table>

      <RuleModal
        open={modalOpen}
        options={data.rule_match_options}
        primaries={data.primaries}
        pageScope={addScope as "all" | "spending" | "income"}
        editingRule={editing}
        onClose={() => setModalOpen(false)}
        onSaved={() => {
          setModalOpen(false);
          for (const key of ["rules", "spending", "income", "overview", "budget"] as const) {
            qc.invalidateQueries({ queryKey: [key] });
          }
        }}
      />

      <ConfirmDialog
        open={pendingDeleteId !== null}
        title="Delete this rule?"
        confirmLabel="Delete"
        danger
        message="Past transactions touched by this rule will be reconciled, and future transactions will stop matching."
        onConfirm={() => {
          if (pendingDeleteId) del.mutate(pendingDeleteId);
          setPendingDeleteId(null);
        }}
        onCancel={() => setPendingDeleteId(null)}
      />
    </Page>
  );
}

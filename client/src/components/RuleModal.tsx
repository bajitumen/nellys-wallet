import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { useMutation } from "@tanstack/react-query";
import { InlineDropdown, type DropdownOption } from "./InlineDropdown";
import { ConfirmDialog } from "./ConfirmDialog";
import { postJson } from "../lib/api";

export type MatchOption = { field: string; value: string; label: string };
export type RuleMatchOptions = {
  merchant: MatchOption[];
  category: MatchOption[];
  item: MatchOption[];
  source: MatchOption[];
};
export type Primary = { code: string; label: string };

export type ExistingRule = {
  id: number;
  conditions: { match_field: string; match_op: string; match_value: string }[];
  conditions_logic: "all" | "any";
  action: string;
  action_value: string | null;
  scope: "all" | "spending" | "income";
};

type Condition = {
  field: "merchant" | "category" | "item" | "source";
  op: "equals" | "not_equals";
  value: { value: string; label: string; _field: string } | null;
};

type Props = {
  open: boolean;
  options: RuleMatchOptions;
  primaries: Primary[];
  pageScope?: "all" | "spending" | "income";
  editingRule?: ExistingRule | null;
  rowMerchant?: string;
  rowCategoryRaw?: string;
  rowDetailedRaw?: string;
  rowSource?: string;
  onClose: () => void;
  onSaved: () => void;
};

const FIELD_OPTS: DropdownOption[] = [
  { value: "merchant", label: "Merchant" },
  { value: "category", label: "Category" },
  { value: "item", label: "Item" },
  { value: "source", label: "Source" },
];
const OP_OPTS: DropdownOption[] = [
  { value: "equals", label: "equals" },
  { value: "not_equals", label: "does not equal" },
];
const LOGIC_OPTS: DropdownOption[] = [
  { value: "all", label: "All" },
  { value: "any", label: "Any" },
];
const ACTION_OPTS: DropdownOption[] = [
  { value: "dismiss", label: "Dismiss" },
  { value: "split", label: "Split" },
  { value: "set_category", label: "Recategorize" },
];
const APPLY_TO_OPTS: DropdownOption[] = [
  { value: "spending", label: "Spending" },
  { value: "income", label: "Income" },
  { value: "all", label: "Both" },
];

function fieldKeyFromMatchField(mf: string): Condition["field"] {
  if (mf === "pfc_primary") return "category";
  if (mf === "pfc_detailed") return "item";
  if (mf === "source") return "source";
  return "merchant";
}

export function RuleModal(props: Props) {
  const {
    open, options, primaries, pageScope = "all", editingRule, rowMerchant,
    rowCategoryRaw, rowDetailedRaw, rowSource, onClose, onSaved,
  } = props;

  function defaultCondition(field: Condition["field"]): Condition {
    const list = options[field] || [];
    const picks: Record<Condition["field"], (o: MatchOption) => boolean> = {
      merchant: (o) => o.value === rowMerchant,
      category: (o) => o.value === rowCategoryRaw,
      item: (o) => o.value === rowDetailedRaw,
      source: (o) => o.value === rowSource,
    };
    const found = list.find(picks[field]) || list[0] || null;
    return {
      field,
      op: "equals",
      value: found
        ? { value: found.value, label: found.label, _field: found.field }
        : null,
    };
  }

  function conditionsFromRule(r: ExistingRule): Condition[] {
    return r.conditions.map((c) => {
      const key = fieldKeyFromMatchField(c.match_field);
      const list = options[key] || [];
      const found = list.find((o) => o.value === c.match_value);
      const label = found ? found.label : c.match_value;
      return {
        field: key,
        op: c.match_op as "equals" | "not_equals",
        value: { value: c.match_value, label, _field: c.match_field },
      };
    });
  }

  function initialState() {
    if (editingRule) {
      const conds = conditionsFromRule(editingRule);
      const action = editingRule.action;
      let splitMode: "pct" | "dollar" = "pct";
      let splitPct = "50";
      let splitAmt = "";
      let setCategory = primaries[0]?.code || "";
      let normalizedAction = action;
      if (action === "split") splitPct = String(editingRule.action_value ?? "50");
      else if (action === "split_dollar") {
        splitMode = "dollar";
        splitAmt = String(editingRule.action_value ?? "");
        normalizedAction = "split";
      } else if (action === "set_category") {
        setCategory = editingRule.action_value || setCategory;
      }
      return {
        conditions: conds.length ? conds : [defaultCondition("merchant")],
        conditionsLogic: editingRule.conditions_logic,
        action: normalizedAction,
        splitPct,
        splitAmt,
        splitMode,
        setCategory,
        pageScope: editingRule.scope,
        ruleId: editingRule.id as number | null,
      };
    }
    return {
      conditions: [defaultCondition("merchant")],
      conditionsLogic: "all" as "all" | "any",
      action: "dismiss",
      splitPct: "50",
      splitAmt: "",
      splitMode: "pct" as "pct" | "dollar",
      setCategory: primaries[0]?.code || "",
      pageScope,
      ruleId: null as number | null,
    };
  }

  const [state, setState] = useState(initialState);
  const [pending, setPending] = useState<null | {
    payload: Record<string, unknown>;
    matchCount: number;
  }>(null);

  useEffect(() => {
    if (open) setState(initialState());
  }, [open, editingRule?.id]);

  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  const save = useMutation({
    mutationFn: (payload: Record<string, unknown>) =>
      postJson<{ ok: boolean; warning?: string }>("/rules", payload),
    onSuccess: (data) => {
      if (data.warning) alert(data.warning);
      onSaved();
    },
    onError: (err: Error) => alert(`Failed to save rule: ${err.message}`),
  });

  function valueOptsFor(field: Condition["field"]): DropdownOption[] {
    return (options[field] || []).map((o) => ({ value: o.value, label: o.label }));
  }

  function setCondition(idx: number, updater: (c: Condition) => Condition) {
    setState((s) => ({
      ...s,
      conditions: s.conditions.map((c, i) => (i === idx ? updater(c) : c)),
    }));
  }

  function buildPayload(): Record<string, unknown> | null {
    if (state.conditions.some((c) => !c.value)) {
      alert("Pick a value for every condition.");
      return null;
    }
    const payload: Record<string, unknown> = {
      conditions: state.conditions.map((c) => ({
        match_field: c.value!._field,
        match_op: c.op,
        match_value: c.value!.value,
      })),
      conditions_logic: state.conditionsLogic,
      action: state.action,
      scope: state.pageScope,
    };
    if (state.ruleId) payload.rule_id = state.ruleId;
    if (state.action === "split") {
      if (state.splitMode === "dollar" && state.splitAmt !== "") {
        payload.action = "split_dollar";
        payload.action_value = state.splitAmt;
      } else if (state.splitPct !== "") {
        payload.action_value = state.splitPct;
      } else {
        alert("Enter a percentage or a dollar amount.");
        return null;
      }
    }
    if (state.action === "set_category") payload.action_value = state.setCategory;
    return payload;
  }

  async function onSubmit() {
    const payload = buildPayload();
    if (!payload) return;
    const conds = payload.conditions as { match_op: string }[];
    const risky =
      payload.action === "dismiss" && conds.some((c) => c.match_op === "not_equals");
    if (risky) {
      try {
        const preview = await postJson<{ matches: number }>("/rules/preview", payload);
        setPending({ payload, matchCount: preview.matches });
      } catch (e) {
        alert(`Failed to preview: ${(e as Error).message}`);
      }
      return;
    }
    save.mutate(payload);
  }

  const primaryOpts: DropdownOption[] = primaries.map((p) => ({
    value: p.code, label: p.label,
  }));

  if (!open) return null;

  return createPortal(
    <>
      <div
        className="rule-modal-backdrop"
        onClick={(e) => {
          if (e.target === e.currentTarget) onClose();
        }}
      >
        <div className="rule-modal" role="dialog" aria-modal="true" aria-labelledby="rule-modal-title">
          <h2 id="rule-modal-title">
            {editingRule ? "Edit rule" : "Set rule"}
            <span className="rule-modal-scope-tag">: </span>
            <InlineDropdown
              className="inline-dropdown rule-modal-dd rule-modal-dd-applyto"
              options={APPLY_TO_OPTS}
              value={state.pageScope}
              onChange={(opt) =>
                setState((s) => ({
                  ...s, pageScope: opt.value as "all" | "spending" | "income",
                }))
              }
            />
          </h2>

          <fieldset className="rule-modal-conditions">
            <legend className="rule-modal-conditions-legend">
              <span>If</span>
              <InlineDropdown
                className="inline-dropdown rule-modal-dd rule-modal-dd-logic"
                options={LOGIC_OPTS}
                value={state.conditionsLogic}
                onChange={(opt) =>
                  setState((s) => ({
                    ...s, conditionsLogic: opt.value as "all" | "any",
                  }))
                }
              />
              <span>of the following conditions are met</span>
            </legend>
            <div className="rule-modal-conditions-list">
              {state.conditions.map((c, idx) => (
                <div key={idx} className="rule-modal-condition-row">
                  <InlineDropdown
                    className="inline-dropdown rule-modal-dd rule-modal-dd-field"
                    options={FIELD_OPTS}
                    value={c.field}
                    onChange={(opt) =>
                      setCondition(idx, () => {
                        const nextField = opt.value as Condition["field"];
                        return defaultCondition(nextField);
                      })
                    }
                  />
                  <InlineDropdown
                    className="inline-dropdown rule-modal-dd rule-modal-dd-op"
                    options={OP_OPTS}
                    value={c.op}
                    onChange={(opt) =>
                      setCondition(idx, (cc) => ({
                        ...cc, op: opt.value as Condition["op"],
                      }))
                    }
                  />
                  <InlineDropdown
                    className="inline-dropdown rule-modal-dd rule-modal-dd-value"
                    options={valueOptsFor(c.field)}
                    value={c.value?.value ?? ""}
                    onChange={(opt) => {
                      const found = options[c.field].find((o) => o.value === opt.value);
                      if (!found) return;
                      setCondition(idx, (cc) => ({
                        ...cc,
                        value: { value: opt.value, label: opt.label, _field: found.field },
                      }));
                    }}
                  />
                  <button
                    type="button"
                    className="rule-modal-cond-add"
                    aria-label="Add condition"
                    onClick={() =>
                      setState((s) => ({
                        ...s,
                        conditions: [
                          ...s.conditions.slice(0, idx + 1),
                          defaultCondition("merchant"),
                          ...s.conditions.slice(idx + 1),
                        ],
                      }))
                    }
                  >
                    +
                  </button>
                  <button
                    type="button"
                    className="rule-modal-cond-remove"
                    aria-label="Remove condition"
                    disabled={state.conditions.length === 1}
                    onClick={() =>
                      setState((s) => ({
                        ...s,
                        conditions: s.conditions.filter((_, i) => i !== idx),
                      }))
                    }
                  >
                    −
                  </button>
                </div>
              ))}
            </div>
          </fieldset>

          <div className="rule-modal-section">
            <div className="rule-modal-section-label">Then</div>
            <div className="rule-modal-row">
              <InlineDropdown
                className="inline-dropdown rule-modal-dd"
                options={ACTION_OPTS}
                value={state.action}
                onChange={(opt) =>
                  setState((s) => ({ ...s, action: opt.value }))
                }
              />
              {state.action === "split" && (
                <span className="rule-modal-extra" data-extra="split">
                  <span>so my share is</span>
                  <input
                    type="number"
                    className="rule-modal-pct"
                    min="1"
                    max="100"
                    step="1"
                    value={state.splitPct}
                    onChange={(e) =>
                      setState((s) => ({
                        ...s, splitPct: e.target.value, splitMode: "pct", splitAmt: "",
                      }))
                    }
                  />
                  <span>%</span>
                  <span>or</span>
                  <span className="rule-modal-amt-prefix">$</span>
                  <input
                    type="number"
                    className="rule-modal-amt"
                    min="0.01"
                    step="0.01"
                    placeholder="0"
                    value={state.splitAmt}
                    onChange={(e) =>
                      setState((s) => ({
                        ...s, splitAmt: e.target.value, splitMode: "dollar", splitPct: "",
                      }))
                    }
                  />
                </span>
              )}
              {state.action === "set_category" && (
                <span className="rule-modal-extra" data-extra="recat">
                  <span>to</span>
                  <InlineDropdown
                    className="inline-dropdown rule-modal-dd rule-modal-dd-cat"
                    options={primaryOpts}
                    value={state.setCategory}
                    onChange={(opt) =>
                      setState((s) => ({ ...s, setCategory: opt.value }))
                    }
                  />
                </span>
              )}
            </div>
          </div>

          <div className="rule-modal-actions">
            <button type="button" className="rule-modal-cancel" onClick={onClose}>
              Cancel
            </button>
            <button
              type="button"
              className="rule-modal-save primary"
              disabled={save.isPending}
              onClick={onSubmit}
            >
              {save.isPending ? "Saving…" : "Save rule"}
            </button>
          </div>
        </div>
      </div>
      <ConfirmDialog
        open={!!pending}
        title="Dismiss many transactions?"
        confirmLabel="Dismiss"
        danger
        message={
          pending
            ? `This rule will dismiss ${pending.matchCount} transaction${
                pending.matchCount === 1 ? "" : "s"
              }. Continue?`
            : ""
        }
        onConfirm={() => {
          if (pending) save.mutate(pending.payload);
          setPending(null);
        }}
        onCancel={() => setPending(null)}
      />
    </>,
    document.body,
  );
}

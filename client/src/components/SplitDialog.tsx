import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useModal } from "../lib/useModal";
import { formatUsd } from "../lib/format";

type Props = {
  open: boolean;
  amount: number;
  onClose: () => void;
  onSave: (amount: number, splitPercentage: number) => void;
};

export function SplitDialog({ open, amount, onClose, onSave }: Props) {
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const containerRef = useModal(open);

  useEffect(() => {
    if (!open) return;
    setValue("");
    setError(null);
    setTimeout(() => inputRef.current?.focus(), 0);
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const parsed = parseFloat(value);
    if (!Number.isFinite(parsed)) {
      setError("Enter a numeric amount.");
      return;
    }
    if (parsed <= 0) {
      setError("Amount must be greater than 0.");
      return;
    }
    if (parsed > Math.abs(amount)) {
      setError(`Amount can't exceed ${formatUsd(Math.abs(amount))}.`);
      return;
    }
    setError(null);
    const pct = (parsed / Math.abs(amount)) * 100;
    onSave(parsed, Math.round(pct * 100) / 100);
  }

  return createPortal(
    <div className="rule-modal-backdrop" onClick={onClose}>
      <div
        ref={containerRef as React.RefObject<HTMLDivElement>}
        className="rule-modal split-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="Split transaction"
        style={{ maxWidth: 380, padding: "1.1rem" }}
        onClick={(e) => e.stopPropagation()}
      >
        <form className="tx-form" onSubmit={submit}>
          <label className="split-dialog-label">Your share of {formatUsd(Math.abs(amount))}</label>
          <div className="tx-form-row">
            <label style={{ margin: 0 }}>$ you owe</label>
            <input
              ref={inputRef}
              type="number"
              name="amount"
              min={0}
              step={0.01}
              placeholder="25.00"
              value={value}
              onChange={(e) => setValue(e.target.value)}
            />
          </div>
          {error && <div className="split-dialog-error" role="alert">{error}</div>}
          <div className="tx-form-actions">
            <button type="button" className="cancel" onClick={onClose}>Cancel</button>
            <button type="submit" className="primary">Save</button>
          </div>
        </form>
      </div>
    </div>,
    document.body,
  );
}

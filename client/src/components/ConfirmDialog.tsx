import { useEffect } from "react";
import { createPortal } from "react-dom";
import { useModal } from "../lib/useModal";

type Props = {
  open: boolean;
  title?: string;
  message: string;
  confirmLabel?: string;
  danger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
};

export function ConfirmDialog({
  open,
  title = "Are you sure?",
  message,
  confirmLabel = "OK",
  danger = false,
  onConfirm,
  onCancel,
}: Props) {
  const containerRef = useModal(open);
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.preventDefault();
        onCancel();
      } else if (e.key === "Enter") {
        e.preventDefault();
        onConfirm();
      }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onConfirm, onCancel]);

  if (!open) return null;
  return createPortal(
    <div
      className="confirm-modal-backdrop"
      onClick={(e) => {
        if (e.target === e.currentTarget) onCancel();
      }}
    >
      <div
        ref={containerRef as React.RefObject<HTMLDivElement>}
        className="confirm-modal" role="alertdialog" aria-modal="true"
      >
        <h2 className="confirm-modal-title">{title}</h2>
        <p className="confirm-modal-msg">{message}</p>
        <div className="confirm-modal-actions">
          <button type="button" className="confirm-modal-cancel" onClick={onCancel}>
            Cancel
          </button>
          <button
            type="button"
            className={`confirm-modal-ok ${danger ? "danger" : "primary"}`}
            onClick={onConfirm}
            autoFocus
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

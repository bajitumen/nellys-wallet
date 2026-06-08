import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

type Variant = "error" | "warning" | "success" | "info";
type ToastItem = { id: number; variant: Variant; message: string };

type Push = (variant: Variant, message: string) => void;

type Ctx = {
  error: (message: string) => void;
  warning: (message: string) => void;
  success: (message: string) => void;
  info: (message: string) => void;
};

const ToastContext = createContext<Ctx | null>(null);

export function useToast(): Ctx {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used within ToastProvider");
  return ctx;
}

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const nextId = useRef(1);

  const push = useCallback<Push>((variant, message) => {
    const id = nextId.current++;
    setItems((prev) => [...prev, { id, variant, message }]);
    window.setTimeout(() => {
      setItems((prev) => prev.filter((t) => t.id !== id));
    }, variant === "error" ? 6000 : 3500);
  }, []);

  const value: Ctx = {
    error: (m) => push("error", m),
    warning: (m) => push("warning", m),
    success: (m) => push("success", m),
    info: (m) => push("info", m),
  };

  return (
    <ToastContext.Provider value={value}>
      {children}
      {createPortal(
        <div className="toast-stack" role="status" aria-live="polite">
          {items.map((t) => (
            <ToastCard key={t.id} item={t} onDismiss={() =>
              setItems((prev) => prev.filter((x) => x.id !== t.id))
            } />
          ))}
        </div>,
        document.body,
      )}
    </ToastContext.Provider>
  );
}

function ToastCard({ item, onDismiss }: { item: ToastItem; onDismiss: () => void }) {
  useEffect(() => {
    // ensure layout settles before fade-in
  }, []);
  return (
    <div className={`toast toast-${item.variant}`}>
      <span className="toast-message">{item.message}</span>
      <button
        type="button"
        className="toast-dismiss"
        aria-label="Dismiss"
        onClick={onDismiss}
      >
        ×
      </button>
    </div>
  );
}

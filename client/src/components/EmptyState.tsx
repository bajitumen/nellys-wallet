import type { ReactNode } from "react";

export function EmptyState({ headline, hint }: { headline: ReactNode; hint?: ReactNode }) {
  return (
    <div className="empty">
      <p>{headline}</p>
      {hint && <p style={{ marginTop: "0.5rem", fontSize: "0.85rem" }}>{hint}</p>}
    </div>
  );
}

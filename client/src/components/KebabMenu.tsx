import { useEffect, useRef, useState } from "react";
import { IconDotsThree } from "./icons";

export type KebabAction =
  | { id: "dismiss"; label: "Dismiss" }
  | { id: "restore"; label: "Restore" }
  | { id: "set-rule"; label: string }
  | { id: "split"; label: "Split" }
  | { id: "reset"; label: "Reset to original" };

type Props = {
  actions: KebabAction[];
  onPick: (id: KebabAction["id"]) => void;
  ariaLabel?: string;
};

export function KebabMenu({ actions, onPick, ariaLabel = "Transaction actions" }: Props) {
  const [open, setOpen] = useState(false);
  const [upward, setUpward] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onDocClick(e: MouseEvent) {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("click", onDocClick);
    return () => document.removeEventListener("click", onDocClick);
  }, [open]);

  useEffect(() => {
    if (!open || !menuRef.current) return;
    const rect = menuRef.current.getBoundingClientRect();
    setUpward(rect.bottom > window.innerHeight - 8);
  }, [open]);

  return (
    <div ref={wrapRef} style={{ position: "relative", display: "inline-block" }}>
      <button
        className="kebab"
        type="button"
        aria-label={ariaLabel}
        onClick={(e) => {
          e.stopPropagation();
          setOpen((o) => !o);
        }}
      >
        <IconDotsThree />
      </button>
      {open && (
        <div ref={menuRef} className={`tx-menu${upward ? " tx-menu-up" : ""}`}>
          {actions.map((a) => (
            <button
              key={a.id}
              type="button"
              className="tx-menu-item"
              onClick={(e) => {
                e.stopPropagation();
                setOpen(false);
                onPick(a.id);
              }}
            >
              {a.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

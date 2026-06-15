import { useEffect, useRef } from "react";

const FOCUSABLE = [
  "a[href]", "area[href]", "input:not([disabled])", "select:not([disabled])",
  "textarea:not([disabled])", "button:not([disabled])", "iframe", "object",
  "embed", "[contenteditable]", '[tabindex]:not([tabindex="-1"])',
].join(",");

export function useModal(open: boolean) {
  const containerRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;
    document.body.classList.add("modal-open");
    const prevActive = document.activeElement as HTMLElement | null;
    const root = containerRef.current;
    const first = root?.querySelector<HTMLElement>(FOCUSABLE);
    first?.focus();

    function onKey(e: KeyboardEvent) {
      if (e.key !== "Tab" || !root) return;
      const nodes = Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE))
        .filter((el) => el.offsetParent !== null || el === document.activeElement);
      if (nodes.length === 0) return;
      const firstEl = nodes[0];
      const lastEl = nodes[nodes.length - 1];
      const active = document.activeElement as HTMLElement | null;
      if (e.shiftKey && active === firstEl) {
        e.preventDefault();
        lastEl.focus();
      } else if (!e.shiftKey && active === lastEl) {
        e.preventDefault();
        firstEl.focus();
      }
    }
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.classList.remove("modal-open");
      prevActive?.focus?.();
    };
  }, [open]);

  return containerRef;
}

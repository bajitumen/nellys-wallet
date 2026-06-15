import {
  createContext, useCallback, useContext, useEffect, useLayoutEffect,
  useMemo, useRef, useState,
} from "react";
import { createPortal } from "react-dom";
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

// Shared singleton state — opening one menu closes all others. Without this,
// every kebab keeps its own boolean and any number can stack open at once.
type Ctx = {
  openId: string | null;
  setOpen: (id: string | null) => void;
};
const KebabCtx = createContext<Ctx | null>(null);

export function KebabMenuProvider({ children }: { children: React.ReactNode }) {
  const [openId, setOpenId] = useState<string | null>(null);
  const value = useMemo<Ctx>(
    () => ({ openId, setOpen: setOpenId }),
    [openId],
  );
  return <KebabCtx.Provider value={value}>{children}</KebabCtx.Provider>;
}

let _nextKebabId = 0;
function nextId(): string {
  _nextKebabId += 1;
  return `k${_nextKebabId}`;
}

const MENU_MARGIN = 8;

export function KebabMenu({ actions, onPick, ariaLabel = "Transaction actions" }: Props) {
  const id = useMemo(nextId, []);
  const ctx = useContext(KebabCtx);
  const fallback = useState<string | null>(null);
  const openId = ctx ? ctx.openId : fallback[0];
  const setOpen = ctx ? ctx.setOpen : fallback[1];
  const open = openId === id;

  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);

  const close = useCallback(() => setOpen(null), [setOpen]);

  useEffect(() => {
    if (!open) return;
    function onDocClick(e: MouseEvent) {
      const t = e.target as Node;
      if (menuRef.current?.contains(t)) return;
      if (triggerRef.current?.contains(t)) return;
      close();
    }
    function onScroll() { close(); }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") close();
    }
    document.addEventListener("click", onDocClick);
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", onScroll);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("click", onDocClick);
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", onScroll);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, close]);

  // Measure trigger + menu once mounted; flip both axes so the menu never
  // overflows the viewport on mobile or near the bottom of the page.
  useLayoutEffect(() => {
    if (!open) {
      setPos(null);
      return;
    }
    const trigger = triggerRef.current;
    const menu = menuRef.current;
    if (!trigger || !menu) return;
    const t = trigger.getBoundingClientRect();
    const m = menu.getBoundingClientRect();
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    // Default: align right edge of menu to right edge of trigger, drop down.
    let left = t.right - m.width;
    let top = t.bottom + 4;
    // Vertical flip if it would spill past the bottom.
    if (top + m.height > vh - MENU_MARGIN) top = t.top - m.height - 4;
    // Clamp horizontally so the menu always sits inside the viewport.
    if (left + m.width > vw - MENU_MARGIN) left = vw - m.width - MENU_MARGIN;
    if (left < MENU_MARGIN) left = MENU_MARGIN;
    // Clamp vertically too (very short viewports).
    if (top < MENU_MARGIN) top = MENU_MARGIN;
    setPos({ top, left });
  }, [open]);

  return (
    <>
      <button
        ref={triggerRef}
        className="kebab"
        type="button"
        aria-label={ariaLabel}
        aria-expanded={open}
        onClick={(e) => {
          e.stopPropagation();
          setOpen(open ? null : id);
        }}
      >
        <IconDotsThree />
      </button>
      {open && createPortal(
        <div
          ref={menuRef}
          className="tx-menu"
          role="menu"
          style={{
            position: "fixed",
            top: pos?.top ?? -9999,
            left: pos?.left ?? -9999,
            visibility: pos ? "visible" : "hidden",
            zIndex: 10000,
          }}
        >
          {actions.map((a) => (
            <button
              key={a.id}
              type="button"
              role="menuitem"
              className="tx-menu-item"
              onClick={(e) => {
                e.stopPropagation();
                close();
                onPick(a.id);
              }}
            >
              {a.label}
            </button>
          ))}
        </div>,
        document.body,
      )}
    </>
  );
}

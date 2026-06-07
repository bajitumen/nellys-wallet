import { useEffect, useMemo, useRef, useState } from "react";
import { IconCaretDown } from "./icons";

export type DropdownOption = {
  value: string;
  label: string;
  menuLabel?: string;
  indent?: boolean;
};

type Props = {
  options: DropdownOption[];
  value: string;
  onChange: (opt: DropdownOption) => void;
  placeholder?: string;
  className?: string;
  triggerClassName?: string;
  ariaLabel?: string;
};

export function InlineDropdown({
  options,
  value,
  onChange,
  placeholder = "—",
  className = "inline-dropdown",
  triggerClassName = "inline-dropdown-trigger",
  ariaLabel,
}: Props) {
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const wrapRef = useRef<HTMLDivElement>(null);
  const current = options.find((o) => o.value === value);
  const visibleOptions = useMemo(() => {
    if (!typed) return options;
    const q = typed.toLowerCase();
    return options.filter((o) =>
      String(o.menuLabel ?? o.label).toLowerCase().startsWith(q),
    );
  }, [options, typed]);

  useEffect(() => {
    if (!open) {
      setTyped("");
      return;
    }
    function onDocClick(e: MouseEvent) {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
      if (e.key === "Escape") {
        e.preventDefault();
        setOpen(false);
      } else if (e.key === "Enter") {
        e.preventDefault();
        const first = visibleOptions[0];
        if (first) {
          onChange(first);
          setOpen(false);
        }
      } else if (e.key === "Backspace") {
        if (typed === "") return;
        e.preventDefault();
        setTyped((p) => p.slice(0, -1));
      } else if (e.key.length === 1 && !e.metaKey && !e.ctrlKey && !e.altKey) {
        e.preventDefault();
        setTyped((p) => p + e.key);
      }
    }
    document.addEventListener("click", onDocClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("click", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, typed, visibleOptions, onChange]);

  const triggerLabel = typed !== "" ? typed : current ? current.label : placeholder;

  return (
    <div className={className} ref={wrapRef}>
      <button
        type="button"
        className={triggerClassName}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={ariaLabel}
        onClick={(e) => {
          e.stopPropagation();
          setOpen((o) => !o);
        }}
      >
        <span className="inline-dropdown-label">{triggerLabel}</span>
        <IconCaretDown />
      </button>
      {open && (
        <div className="inline-dropdown-menu">
          {options.map((opt) => {
            const visible = visibleOptions.includes(opt);
            return (
              <button
                key={opt.value}
                type="button"
                hidden={!visible}
                className={`inline-dropdown-option${
                  opt.indent ? " inline-dropdown-option-indent" : ""
                }${opt.value === value ? " active" : ""}`}
                onClick={(e) => {
                  e.stopPropagation();
                  onChange(opt);
                  setOpen(false);
                }}
              >
                {opt.menuLabel ?? opt.label}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

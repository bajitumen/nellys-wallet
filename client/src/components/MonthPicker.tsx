import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { IconCaretDown } from "./icons";

export type MonthOption = { value: string; label: string };

type Props = {
  label: string;
  options: MonthOption[];
  currentValue: string;
};

export function MonthPickerCard({ label, options, currentValue }: Props) {
  const [open, setOpen] = useState(false);
  const [searchParams, setSearchParams] = useSearchParams();
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onDocClick(e: MouseEvent) {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("click", onDocClick);
    return () => document.removeEventListener("click", onDocClick);
  }, []);

  function pick(value: string) {
    const next = new URLSearchParams(searchParams);
    next.set("month", value);
    setSearchParams(next, { replace: true });
    setOpen(false);
  }

  return (
    <div className="card month-card" ref={ref}>
      <div className="label">Month</div>
      <div className="value">
        <button
          className="month-trigger"
          type="button"
          aria-haspopup="listbox"
          aria-expanded={open}
          onClick={() => setOpen((o) => !o)}
        >
          <span className="month-label">{label}</span>
          <IconCaretDown />
        </button>
      </div>
      <div className="month-menu" role="listbox" hidden={!open}>
        {options.map((opt) => (
          <a
            key={opt.value}
            href="#"
            onClick={(e) => {
              e.preventDefault();
              pick(opt.value);
            }}
            className={opt.value === currentValue ? "active" : undefined}
          >
            {opt.label}
          </a>
        ))}
      </div>
    </div>
  );
}

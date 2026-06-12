import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { IconPlus } from "./icons";

export type FilterColumn<T> = {
  key: string;
  label: string;
  urlParam: string;
  getValue: (row: T) => string;
  getLabel?: (row: T) => string;
};

type Props<T> = {
  rows: T[];
  columns: FilterColumn<T>[];
};

type MenuState =
  | { kind: "closed" }
  | { kind: "columns" }
  | { kind: "values"; columnKey: string };

export function applyTxFilters<T>(
  rows: T[], columns: FilterColumn<T>[], searchParams: URLSearchParams,
): T[] {
  return columns.reduce((acc, col) => {
    const active = searchParams.getAll(col.urlParam);
    if (active.length === 0) return acc;
    return acc.filter((row) => active.includes(col.getValue(row)));
  }, rows);
}

function uniqueValues<T>(rows: T[], col: FilterColumn<T>): { value: string; label: string }[] {
  const seen = new Map<string, string>();
  for (const row of rows) {
    const v = col.getValue(row);
    if (v === "" || v == null) continue;
    if (!seen.has(v)) seen.set(v, (col.getLabel ?? col.getValue)(row));
  }
  return Array.from(seen, ([value, label]) => ({ value, label }))
    .sort((a, b) => a.label.localeCompare(b.label));
}

export function TxFilters<T>({ rows, columns }: Props<T>) {
  const [searchParams, setSearchParams] = useSearchParams();
  const [menu, setMenu] = useState<MenuState>({ kind: "closed" });
  const [openUp, setOpenUp] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const wrapRef = useRef<HTMLDivElement | null>(null);

  // Click outside closes the menu.
  useEffect(() => {
    if (menu.kind === "closed") return;
    function onDocClick(e: MouseEvent) {
      if (!wrapRef.current?.contains(e.target as Node)) {
        setMenu({ kind: "closed" });
      }
    }
    document.addEventListener("click", onDocClick);
    return () => document.removeEventListener("click", onDocClick);
  }, [menu]);

  // Flip the menu upward if it would spill below the viewport.
  useLayoutEffect(() => {
    if (menu.kind === "closed" || !menuRef.current) return;
    const rect = menuRef.current.getBoundingClientRect();
    setOpenUp(rect.bottom > window.innerHeight - 8);
  }, [menu]);

  function removeChip(urlParam: string, value: string) {
    const p = new URLSearchParams(searchParams);
    const keep = p.getAll(urlParam).filter((v) => v !== value);
    p.delete(urlParam);
    keep.forEach((v) => p.append(urlParam, v));
    setSearchParams(p);
  }

  function addFilter(urlParam: string, value: string) {
    const p = new URLSearchParams(searchParams);
    if (!p.getAll(urlParam).includes(value)) p.append(urlParam, value);
    setSearchParams(p);
    setMenu({ kind: "closed" });
  }

  const chips: { col: FilterColumn<T>; value: string; label: string }[] = [];
  for (const col of columns) {
    const active = searchParams.getAll(col.urlParam);
    if (active.length === 0) continue;
    // Build a value→label index from the (unfiltered) row set so removing a
    // chip whose row is now hidden by another column still shows a label.
    const labelIndex = new Map<string, string>();
    for (const r of rows) {
      const v = col.getValue(r);
      if (!labelIndex.has(v)) labelIndex.set(v, (col.getLabel ?? col.getValue)(r));
    }
    for (const v of active) {
      chips.push({ col, value: v, label: labelIndex.get(v) ?? v });
    }
  }

  function availableColumns(): FilterColumn<T>[] {
    return columns.filter((col) => {
      const taken = new Set(searchParams.getAll(col.urlParam));
      return uniqueValues(rows, col).some((u) => !taken.has(u.value));
    });
  }

  return (
    <div className="tx-header" id="transactions">
      <h2 className="tx-header-title">Transactions</h2>
      <div className="tx-filters" ref={wrapRef}>
        {chips.map((c) => (
          <button
            key={`${c.col.key}:${c.value}`}
            type="button"
            className="filter-chip"
            onClick={() => removeChip(c.col.urlParam, c.value)}
            aria-label={`Remove filter ${c.col.label}: ${c.label}`}
          >
            {c.col.label}: {c.label} <span className="filter-chip-x">×</span>
          </button>
        ))}
        <button
          type="button"
          id="filter-add"
          className="filter-add"
          aria-label="Add filter"
          aria-haspopup="listbox"
          aria-expanded={menu.kind !== "closed"}
          onClick={(e) => {
            e.stopPropagation();
            setMenu(menu.kind === "closed" ? { kind: "columns" } : { kind: "closed" });
          }}
        >
          <IconPlus />
        </button>
        {menu.kind !== "closed" && (
          <div
            ref={menuRef}
            className={`filter-menu${openUp ? " filter-menu-up" : ""}`}
            role="listbox"
          >
            {menu.kind === "columns" && (() => {
              const opts = availableColumns();
              if (opts.length === 0) {
                return <span className="filter-menu-empty">No filters available</span>;
              }
              return opts.map((col) => (
                <button
                  key={col.key}
                  type="button"
                  className="filter-menu-col"
                  onClick={(e) => {
                    e.stopPropagation();
                    setMenu({ kind: "values", columnKey: col.key });
                  }}
                >
                  {col.label}
                </button>
              ));
            })()}
            {menu.kind === "values" && (() => {
              const col = columns.find((c) => c.key === menu.columnKey);
              if (!col) return null;
              const taken = new Set(searchParams.getAll(col.urlParam));
              const values = uniqueValues(rows, col).filter((u) => !taken.has(u.value));
              if (values.length === 0) {
                return <span className="filter-menu-empty">No values available</span>;
              }
              return values.map((v) => (
                <button
                  key={v.value}
                  type="button"
                  className="filter-menu-val"
                  onClick={(e) => {
                    e.stopPropagation();
                    addFilter(col.urlParam, v.value);
                  }}
                >
                  {v.label}
                </button>
              ));
            })()}
          </div>
        )}
      </div>
    </div>
  );
}

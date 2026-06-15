import { useMemo, useRef, useState } from "react";

export type SortDir = "asc" | "desc";
export type SortState<K extends string> = { key: K; dir: SortDir };

export function useSorted<T, K extends string>(
  rows: T[],
  initial: SortState<K>,
  accessors: Record<K, (row: T) => string | number>,
) {
  const [sort, setSort] = useState<SortState<K>>(initial);

  // Refresh accessors each render so a closure over up-stream values (e.g.
  // a pct accessor over data.total) doesn't sort on a stale snapshot. The
  // ref is read inside the memo, so updating it doesn't bust the memo.
  const accessorsRef = useRef(accessors);
  accessorsRef.current = accessors;

  const sorted = useMemo(() => {
    const accessor = accessorsRef.current[sort.key];
    const out = [...rows].sort((a, b) => {
      const av = accessor(a);
      const bv = accessor(b);
      if (av === bv) return 0;
      return (av < bv ? -1 : 1) * (sort.dir === "asc" ? 1 : -1);
    });
    return out;
  }, [rows, sort]);

  function toggle(key: K) {
    setSort((prev) =>
      prev.key === key
        ? { key, dir: prev.dir === "asc" ? "desc" : "asc" }
        : { key, dir: "desc" },
    );
  }

  // Spread on every sortable <th>. data-sort/data-dir drive the existing CSS
  // arrows; aria-sort + tabIndex + Enter/Space handler give keyboard users
  // the same affordance.
  function headerProps(key: K) {
    const active = sort.key === key;
    return {
      "data-sort": "true" as const,
      "data-dir": active ? sort.dir : undefined,
      "aria-sort": (active ? (sort.dir === "asc" ? "ascending" : "descending") : "none") as
        "ascending" | "descending" | "none",
      tabIndex: 0,
      onClick: () => toggle(key),
      onKeyDown: (e: React.KeyboardEvent) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          toggle(key);
        }
      },
    };
  }
  return { sorted, sort, toggle, headerProps };
}

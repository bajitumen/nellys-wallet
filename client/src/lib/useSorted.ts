import { useMemo, useRef, useState } from "react";

export type SortDir = "asc" | "desc";
export type SortState<K extends string> = { key: K; dir: SortDir };

export function useSorted<T, K extends string>(
  rows: T[],
  initial: SortState<K>,
  accessors: Record<K, (row: T) => string | number>,
) {
  const [sort, setSort] = useState<SortState<K>>(initial);

  // Pin the accessors object on first render: callers pass a fresh literal
  // each render, which would otherwise bust the memo and re-sort every time.
  const accessorsRef = useRef(accessors);

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
  return { sorted, sort, toggle };
}

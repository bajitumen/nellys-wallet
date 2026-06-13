export function scrollToAnchor(id = "transactions"): void {
  // setTimeout 0 (not rAF) — must run after useSearchParams' state
  // propagates through QueryClient subscribers.
  setTimeout(() => {
    const el = document.getElementById(id);
    if (!el) return;
    const top = el.getBoundingClientRect().top + window.scrollY - 12;
    window.scrollTo({ top, behavior: "smooth" });
  }, 0);
}

export function scrollToTransactions(): void {
  scrollToAnchor("transactions");
}

export function clientCurrentMonth(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

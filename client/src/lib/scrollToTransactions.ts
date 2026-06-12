// Smooth-scrolls the page to a given anchor id (default `#transactions`,
// rendered by TxFilters). Used after a category/payer/budget click on
// Spending / Income / Budget to mirror the `_anchor=...` reload behavior
// of the original Jinja pages.
export function scrollToAnchor(id = "transactions"): void {
  // Defer past React's commit so the element has its final layout. rAF alone
  // sometimes fires before useSearchParams' state propagates through the
  // QueryClient subscribers; setTimeout 0 sits after that microtask drain.
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

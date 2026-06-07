if (window.NWAnimate) window.NWAnimate.wireMonthMenu();

(function() {
  if (!window.setupTxFilters) return;

  window.setupTxFilters({
    emptyMessageId: 'filter-empty',
    columns: [
      { key: 'date', label: 'Date', dataAttr: 'date', urlParam: 'f_date',
        getLabel: function(row) { return txCellText(row, 0); } },
      { key: 'source', label: 'Source', dataAttr: 'source', urlParam: 'f_source' },
      { key: 'payer', label: 'Payer', dataAttr: 'payer', urlParam: 'f_payer' },
    ],
    replaceTriggers: [
      { selector: 'tr.category-row' },
      { selector: 'a.stacked-bar-segment' },
    ],
  });
})();

function refreshSpendingMain() {
  return window.NWAnimate.refreshMain();
}

if (window.NWAnimate) window.NWAnimate.wireMonthMenu();

(function() {
  function asValueOptions(items) {
    return (items || []).map(function(i) { return { value: i.code, label: i.label }; });
  }
  var TAXONOMY = window.PFC_TAXONOMY || {};
  var PRIMARIES = asValueOptions(window.PFC_PRIMARIES);

  document.addEventListener('click', function(e) {
    var catTrigger = e.target.closest('.cat-trigger');
    if (catTrigger) {
      e.stopPropagation();
      if (catTrigger.getAttribute('aria-expanded') === 'true') {
        window.closeInlineDropdowns();
        return;
      }
      window.openInlineDropdown(catTrigger, PRIMARIES, catTrigger.dataset.value, function(opt) {
        window.applyOverride(catTrigger.dataset.txId, {
          category: opt.value, detailed: null,
        }, 'Failed to save category');
      });
      return;
    }

    var itemTrigger = e.target.closest('.item-trigger');
    if (itemTrigger) {
      e.stopPropagation();
      if (itemTrigger.getAttribute('aria-expanded') === 'true') {
        window.closeInlineDropdowns();
        return;
      }
      var primary = itemTrigger.dataset.primary;
      var itemOptions = [{value: '', label: '—'}].concat(asValueOptions(TAXONOMY[primary]));
      window.openInlineDropdown(itemTrigger, itemOptions, itemTrigger.dataset.value, function(opt) {
        itemTrigger.querySelector('.inline-dropdown-label').textContent = opt.label;
        itemTrigger.dataset.value = opt.value;
        itemTrigger.closest('td').dataset.value = opt.value ? opt.label : '';
        window.applyOverride(itemTrigger.dataset.txId, {
          detailed: opt.value || null,
        }, 'Failed to save item');
      });
    }
  });
})();

(function() {
  if (!window.setupTxFilters) return;

  function cellText(row, idx) {
    var c = row.children[idx];
    return c ? c.textContent.trim() : '';
  }
  function dropdownLabel(row, triggerClass) {
    var t = row.querySelector('.' + triggerClass + ' .inline-dropdown-label');
    return t ? t.textContent.trim() : '';
  }

  window._txFilters = window.setupTxFilters({
    emptyMessageId: 'filter-empty',
    columns: [
      { key: 'date', label: 'Date', dataAttr: 'date', urlParam: 'f_date',
        getLabel: function(row) { return cellText(row, 0); } },
      { key: 'source', label: 'Source', dataAttr: 'source', urlParam: 'f_source' },
      { key: 'category', label: 'Category', dataAttr: 'categoryRaw', urlParam: 'category',
        getLabel: function(row) { return dropdownLabel(row, 'cat-trigger'); } },
      { key: 'item', label: 'Item', dataAttr: 'item', urlParam: 'f_item',
        getLabel: function(row) {
          var l = dropdownLabel(row, 'item-trigger');
          return (l === '' || l === '—') ? '(none)' : l;
        } },
    ],
    replaceTriggers: [
      { selector: 'tr.category-row', ignoreInside: 'a, button, .inline-dropdown, .kebab, input' },
      { selector: 'tr.subcategory-row' },
      { selector: 'a.stacked-bar-segment' },
    ],
  });
})();

(function() {
  document.addEventListener('click', function(e) {
    var cell = e.target.closest('.cat-dot-col');
    if (!cell) return;
    var toggle = cell.querySelector('.subcat-toggle');
    if (!toggle || toggle.classList.contains('subcat-toggle-empty')) return;
    e.stopPropagation();
    var row = toggle.closest('tr.category-row');
    if (!row) return;
    var primary = row.dataset.filterValue;
    var expanded = toggle.getAttribute('aria-expanded') === 'true';
    toggle.setAttribute('aria-expanded', String(!expanded));
    document.querySelectorAll(
      'tr.subcategory-row[data-parent="' + primary + '"]'
    ).forEach(function(sr) { sr.hidden = expanded; });
  });
})();

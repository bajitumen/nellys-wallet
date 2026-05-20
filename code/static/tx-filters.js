window.setupTxFilters = function(config) {
  var filtersSelector = config.filtersSelector || '.tx-filters';
  function getFiltersContainer() { return document.querySelector(filtersSelector); }
  if (!getFiltersContainer()) return null;

  var COLUMNS = config.columns;
  var rowSelector = config.rowSelector || 'tr.tx-row';
  var emptyMessageId = config.emptyMessageId;
  var replaceTriggers = config.replaceTriggers || [];

  var state = {};
  COLUMNS.forEach(function(c) { state[c.key] = new Set(); });

  var initialParams = new URL(window.location.href).searchParams;
  COLUMNS.forEach(function(c) {
    initialParams.getAll(c.urlParam).forEach(function(v) { state[c.key].add(v); });
  });

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function(c) {
      return { '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[c];
    });
  }

  function rowValue(row, col) { return row.dataset[col.dataAttr] || ''; }

  function rowLabel(row, col) {
    if (col.getLabel) {
      var custom = col.getLabel(row);
      if (custom != null && custom !== '') return custom;
    }
    var raw = rowValue(row, col);
    return raw || '(none)';
  }

  function rowMatches(row, excludeKey) {
    for (var i = 0; i < COLUMNS.length; i++) {
      var col = COLUMNS[i];
      if (col.key === excludeKey) continue;
      var set = state[col.key];
      if (set.size === 0) continue;
      if (!set.has(rowValue(row, col))) return false;
    }
    return true;
  }

  function applyRowVisibility() {
    var visible = 0;
    document.querySelectorAll(rowSelector).forEach(function(row) {
      var show = rowMatches(row);
      row.style.display = show ? '' : 'none';
      if (show) visible++;
    });
    if (emptyMessageId) {
      var msg = document.getElementById(emptyMessageId);
      if (msg) {
        var anyChips = COLUMNS.some(function(c) { return state[c.key].size > 0; });
        msg.hidden = !(anyChips && visible === 0);
      }
    }
  }

  function updateUrl() {
    var url = new URL(window.location.href);
    COLUMNS.forEach(function(c) {
      url.searchParams.delete(c.urlParam);
      state[c.key].forEach(function(v) { url.searchParams.append(c.urlParam, v); });
    });
    history.replaceState({}, '', url.pathname + (url.search || ''));
  }

  function uniqueValues(col, excludeKey) {
    var seen = new Map();
    document.querySelectorAll(rowSelector).forEach(function(row) {
      if (excludeKey !== undefined && !rowMatches(row, excludeKey)) return;
      var v = rowValue(row, col);
      if (!seen.has(v)) seen.set(v, rowLabel(row, col));
    });
    return Array.from(seen.entries())
      .map(function(e) { return { value: e[0], label: e[1] }; })
      .sort(function(a, b) { return a.label.localeCompare(b.label); });
  }

  function labelForValue(colKey, value) {
    var col = COLUMNS.find(function(c) { return c.key === colKey; });
    var rows = document.querySelectorAll(rowSelector);
    for (var i = 0; i < rows.length; i++) {
      if (rowValue(rows[i], col) === value) return rowLabel(rows[i], col);
    }
    return value || '(none)';
  }

  function renderChips() {
    var fc = getFiltersContainer();
    if (!fc) return;
    fc.querySelectorAll('.filter-chip').forEach(function(el) { el.remove(); });
    var plusBtn = document.getElementById('filter-add');
    COLUMNS.forEach(function(col) {
      state[col.key].forEach(function(value) {
        var chip = document.createElement('button');
        chip.type = 'button';
        chip.className = 'filter-chip';
        chip.dataset.column = col.key;
        chip.dataset.value = value;
        chip.innerHTML = col.label + ': ' + escapeHtml(labelForValue(col.key, value)) +
                         ' <span class="filter-chip-x">×</span>';
        fc.insertBefore(chip, plusBtn);
      });
    });
  }

  function syncReplaceMarkers() {
    document.querySelectorAll('[data-filter-column][data-filter-value]').forEach(function(el) {
      var colKey = el.dataset.filterColumn;
      var val = el.dataset.filterValue;
      if (state[colKey]) {
        el.classList.toggle('active', state[colKey].has(val));
      }
    });
  }

  function commit() {
    applyRowVisibility();
    updateUrl();
    renderChips();
    syncReplaceMarkers();
  }

  function addFilter(colKey, value) { state[colKey].add(value); commit(); }
  function removeFilter(colKey, value) { state[colKey].delete(value); commit(); }
  function replaceFilter(colKey, value) {
    COLUMNS.forEach(function(c) { state[c.key].clear(); });
    state[colKey].add(value);
    commit();
    var target = document.getElementById('transactions');
    if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  function buildColumnPicker() {
    var items = COLUMNS.map(function(col) {
      var unique = uniqueValues(col, col.key);
      var remaining = unique.filter(function(u) { return !state[col.key].has(u.value); });
      if (unique.length <= 1 || remaining.length === 0) return '';
      return '<a class="filter-menu-col" data-column="' + col.key + '">' +
             col.label + '</a>';
    }).filter(function(x) { return x; });
    if (items.length === 0) return '<span class="filter-menu-empty">No filters available</span>';
    return items.join('');
  }

  function buildValuePicker(colKey) {
    var col = COLUMNS.find(function(c) { return c.key === colKey; });
    var values = uniqueValues(col, colKey).filter(function(v) { return !state[colKey].has(v.value); });
    return values.map(function(v) {
      return '<a class="filter-menu-val" data-column="' + colKey +
             '" data-value="' + escapeHtml(v.value) + '">' + escapeHtml(v.label) + '</a>';
    }).join('');
  }

  function openMenu(html) {
    var menu = document.getElementById('filter-menu');
    menu.innerHTML = html;
    menu.hidden = false;
    menu.classList.remove('filter-menu-up');
    var rect = menu.getBoundingClientRect();
    if (rect.bottom > window.innerHeight - 8) menu.classList.add('filter-menu-up');
    var plus = document.getElementById('filter-add');
    if (plus) plus.setAttribute('aria-expanded', 'true');
  }

  function closeMenu() {
    var menu = document.getElementById('filter-menu');
    if (menu) menu.hidden = true;
    var plus = document.getElementById('filter-add');
    if (plus) plus.setAttribute('aria-expanded', 'false');
  }

  // stopPropagation in menu-changing branches: openMenu replaces innerHTML,
  // detaching the clicked node — without it, the outside-click branch below
  // would close the menu we just opened.
  document.addEventListener('click', function(e) {
    if (e.target.closest(filtersSelector)) {
      var chip = e.target.closest('.filter-chip[data-column]');
      if (chip) { e.preventDefault(); e.stopPropagation(); removeFilter(chip.dataset.column, chip.dataset.value); return; }

      var colItem = e.target.closest('.filter-menu-col');
      if (colItem) { e.preventDefault(); e.stopPropagation(); openMenu(buildValuePicker(colItem.dataset.column)); return; }

      var valItem = e.target.closest('.filter-menu-val');
      if (valItem) {
        e.preventDefault();
        e.stopPropagation();
        addFilter(valItem.dataset.column, valItem.dataset.value);
        closeMenu();
        return;
      }

      var plus = e.target.closest('#filter-add');
      if (plus) {
        e.stopPropagation();
        var menu = document.getElementById('filter-menu');
        if (menu.hidden) openMenu(buildColumnPicker());
        else closeMenu();
        return;
      }
    }

    for (var i = 0; i < replaceTriggers.length; i++) {
      var trig = replaceTriggers[i];
      var el = e.target.closest(trig.selector);
      if (!el) continue;
      if (!el.dataset.filterColumn || el.dataset.filterValue === undefined) continue;
      if (trig.ignoreInside && e.target.closest(trig.ignoreInside)) return;
      e.preventDefault();
      replaceFilter(el.dataset.filterColumn, el.dataset.filterValue);
      return;
    }
    if (!e.target.closest('#filter-menu') && !e.target.closest('#filter-add')) {
      closeMenu();
    }
  });

  commit();

  return {
    addFilter: addFilter,
    removeFilter: removeFilter,
    replaceFilter: replaceFilter,
    refresh: commit,
  };
};

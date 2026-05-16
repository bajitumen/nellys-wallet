// Income page JS: sortable tables. Month-picker dropdown and stacked-bar
// tooltip both live in layout.js now (used by every page that has them).

// ---------------------------------------------------------------------------
// Sortable tables
// ---------------------------------------------------------------------------
(function() {
  function cellValue(cell) {
    return cell.dataset.value !== undefined ? cell.dataset.value : cell.textContent.trim();
  }
  function compare(a, b, type) {
    if (type === 'number') return (parseFloat(a) || 0) - (parseFloat(b) || 0);
    if (type === 'date') return new Date(a) - new Date(b);
    return a.localeCompare(b);
  }
  function nextDir(th) {
    if (th.dataset.dir === 'asc') return 'desc';
    if (th.dataset.dir === 'desc') return 'asc';
    return th.dataset.sort === 'string' ? 'asc' : 'desc';
  }
  function sortBy(table, colIdx, type, dir) {
    var tbody = table.querySelector('tbody');
    var rows = Array.prototype.slice.call(tbody.children);
    rows.sort(function(r1, r2) {
      var c = compare(cellValue(r1.children[colIdx]), cellValue(r2.children[colIdx]), type);
      return dir === 'asc' ? c : -c;
    });
    rows.forEach(function(r) { tbody.appendChild(r); });
  }
  document.querySelectorAll('table.sortable-table').forEach(function(table) {
    var ths = Array.prototype.slice.call(table.querySelectorAll('thead th'));
    ths.forEach(function(th, idx) {
      if (!th.dataset.sort) return;
      th.classList.add('sortable');
      if (th.dataset.dir) th.classList.add('sort-' + th.dataset.dir);
      th.addEventListener('click', function() {
        var dir = nextDir(th);
        ths.forEach(function(t) {
          t.classList.remove('sort-asc', 'sort-desc');
          if (t !== th) delete t.dataset.dir;
        });
        th.dataset.dir = dir;
        th.classList.add('sort-' + dir);
        sortBy(table, idx, th.dataset.sort, dir);
      });
    });
  });
})();

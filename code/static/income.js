// Income page JS: month picker, stacked-bar tooltip, sortable tables.
// These three modules are duplicated from spending.js because they're
// page-agnostic — if a third page needs them, factor into a shared file.

// ---------------------------------------------------------------------------
// Month picker dropdown
// ---------------------------------------------------------------------------
(function() {
  var trigger = document.getElementById('month-trigger');
  var menu = document.getElementById('month-menu');
  if (!trigger || !menu) return;
  trigger.addEventListener('click', function(e) {
    e.stopPropagation();
    var open = !menu.hidden;
    menu.hidden = open;
    trigger.setAttribute('aria-expanded', String(!open));
  });
  document.addEventListener('click', function(e) {
    if (!menu.hidden && !e.target.closest('.month-menu')) {
      menu.hidden = true;
      trigger.setAttribute('aria-expanded', 'false');
    }
  });
})();

// ---------------------------------------------------------------------------
// Stacked-bar hover tooltip
// ---------------------------------------------------------------------------
(function() {
  var bar = document.querySelector('.stacked-bar');
  if (!bar) return;
  var tooltip = document.createElement('div');
  tooltip.className = 'bar-tooltip';
  document.body.appendChild(tooltip);

  function show(seg) {
    tooltip.textContent = seg.dataset.tooltip;
    var rect = seg.getBoundingClientRect();
    tooltip.style.left = (rect.left + rect.width / 2) + 'px';
    tooltip.style.top = rect.top + 'px';
    tooltip.classList.add('visible');
  }
  function hide() { tooltip.classList.remove('visible'); }

  bar.addEventListener('mouseover', function(e) {
    var seg = e.target.closest('.stacked-bar-segment');
    if (seg) show(seg);
  });
  bar.addEventListener('mouseleave', hide);
  window.addEventListener('scroll', hide, { passive: true });
})();

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

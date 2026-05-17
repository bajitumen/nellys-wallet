window.csrfFetch = function(url, options) {
  options = options || {};
  options.headers = Object.assign({}, options.headers, {
    'X-CSRFToken': document.querySelector('meta[name="csrf-token"]').content,
  });
  return fetch(url, options);
};

// Capture-phase so per-page blur handlers see the already-formatted value.
document.addEventListener('blur', function(e) {
  var el = e.target;
  if (!el.classList || !el.classList.contains('numeric-input')) return;
  if (el.value === '') return;
  var v = parseFloat(el.value);
  if (!isNaN(v)) el.value = v.toFixed(2);
}, true);

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

window.closeInlineDropdowns = function() {
  document.querySelectorAll('.inline-dropdown-menu').forEach(function(m) { m.remove(); });
  document.querySelectorAll('.inline-dropdown-trigger[aria-expanded="true"]')
    .forEach(function(t) { t.setAttribute('aria-expanded', 'false'); });
};

window.openInlineDropdown = function(trigger, options, currentValue, onSelect) {
  window.closeInlineDropdowns();
  var menu = document.createElement('div');
  menu.className = 'inline-dropdown-menu';
  options.forEach(function(opt) {
    var item = document.createElement('button');
    item.type = 'button';
    item.className = 'inline-dropdown-option';
    item.textContent = opt.label;
    if (opt.value === currentValue) item.classList.add('active');
    item.addEventListener('click', function(e) {
      e.stopPropagation();
      onSelect(opt);
      window.closeInlineDropdowns();
    });
    menu.appendChild(item);
  });
  trigger.parentElement.appendChild(menu);
  trigger.setAttribute('aria-expanded', 'true');
  var rect = menu.getBoundingClientRect();
  if (rect.bottom > window.innerHeight - 8) {
    menu.classList.add('inline-dropdown-menu-up');
  }
};

document.addEventListener('click', function(e) {
  if (!e.target.closest('.inline-dropdown-menu') &&
      !e.target.closest('.inline-dropdown-trigger')) {
    window.closeInlineDropdowns();
  }
});

(function() {
  var bars = document.querySelectorAll('.stacked-bar');
  if (!bars.length) return;
  var tooltip = document.createElement('div');
  tooltip.className = 'bar-tooltip';
  document.body.appendChild(tooltip);
  function show(seg) {
    if (!seg.dataset.tooltip) return;
    tooltip.textContent = seg.dataset.tooltip;
    var rect = seg.getBoundingClientRect();
    tooltip.style.top = rect.top + 'px';
    tooltip.classList.add('visible');
    // Measure after .visible so offsetWidth is real, then clamp into viewport.
    var tipW = tooltip.offsetWidth;
    var desired = rect.left + rect.width / 2;
    var minX = tipW / 2 + 8;
    var maxX = window.innerWidth - tipW / 2 - 8;
    tooltip.style.left = Math.max(minX, Math.min(desired, maxX)) + 'px';
  }
  function hide() { tooltip.classList.remove('visible'); }
  bars.forEach(function(bar) {
    bar.addEventListener('mouseover', function(e) {
      var seg = e.target.closest('.stacked-bar-segment');
      if (seg && seg.offsetWidth > 0) show(seg);
    });
    bar.addEventListener('mouseleave', hide);
  });
  window.addEventListener('scroll', hide, { passive: true });
})();

(function() {
  var toggle = document.getElementById('sidebar-toggle');
  var sidebar = document.getElementById('sidebar');
  var backdrop = document.getElementById('sidebar-backdrop');
  if (!toggle || !sidebar || !backdrop) return;
  function setOpen(open) {
    sidebar.classList.toggle('open', open);
    backdrop.classList.toggle('visible', open);
    toggle.setAttribute('aria-expanded', String(open));
    document.body.classList.toggle('sidebar-open', open);
  }
  toggle.addEventListener('click', function() {
    setOpen(!sidebar.classList.contains('open'));
  });
  backdrop.addEventListener('click', function() { setOpen(false); });
  sidebar.querySelectorAll('nav a').forEach(function(a) {
    a.addEventListener('click', function() { setOpen(false); });
  });
  document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape' && sidebar.classList.contains('open')) setOpen(false);
  });
})();

document.querySelector('.theme-toggle').addEventListener('click', function() {
  var html = document.documentElement;
  var isDark = html.getAttribute('data-theme') === 'dark';
  if (isDark) {
    html.removeAttribute('data-theme');
    localStorage.setItem('theme', 'light');
  } else {
    html.setAttribute('data-theme', 'dark');
    localStorage.setItem('theme', 'dark');
  }
});

(function() {
  var btn = document.getElementById('refresh-btn');
  if (!btn) return;
  btn.addEventListener('click', function() {
    if (btn.classList.contains('busy')) return;
    btn.classList.add('busy');
    csrfFetch('/sync', { method: 'POST' })
      .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, data: d }; }); })
      .then(function(res) {
        if (res.ok && res.data.ok) {
          window.location.reload();
        } else {
          alert('Sync failed: ' + (res.data.error || 'unknown error'));
          btn.classList.remove('busy');
        }
      })
      .catch(function(e) {
        alert('Sync error: ' + e.message);
        btn.classList.remove('busy');
      });
  });
})();

(function() {
  var btn = document.getElementById('add-account-btn');
  if (!btn) return;
  btn.addEventListener('click', function() {
    btn.disabled = true;
    csrfFetch('/link/token', { method: 'POST' })
      .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, data: d }; }); })
      .then(function(res) {
        if (!res.ok || !res.data.link_token) {
          alert('Could not start Plaid Link: ' + (res.data.error || 'unknown error'));
          btn.disabled = false;
          return;
        }
        var handler = Plaid.create({
          token: res.data.link_token,
          onSuccess: function(public_token) {
            csrfFetch('/link/exchange', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ public_token: public_token }),
            })
              .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, data: d }; }); })
              .then(function(res) {
                if (!res.ok) {
                  alert('Failed to save account: ' + (res.data.error || 'unknown error'));
                  btn.disabled = false;
                  return;
                }
                window.location.reload();
              });
          },
          onExit: function() { btn.disabled = false; },
        });
        handler.open();
      })
      .catch(function(e) {
        alert('Error: ' + e.message);
        btn.disabled = false;
      });
  });
})();

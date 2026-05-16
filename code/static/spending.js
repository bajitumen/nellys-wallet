// All Spending-page JS: inline Category/Item dropdowns, kebab menu (Split,
// Reset), month picker, stacked-bar tooltip, sortable tables.
//
// Reads server-injected data from window.PFC_TAXONOMY and PFC_PRIMARIES;
// see spending.html for the inline <script> that defines them.

function spendingPostOverride(txId, payload) {
  return csrfFetch('/transactions/' + encodeURIComponent(txId) + '/override', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

// ---------------------------------------------------------------------------
// Inline Category + Item dropdowns
// ---------------------------------------------------------------------------
(function() {
  var TAXONOMY = window.PFC_TAXONOMY || {};
  var PRIMARIES = window.PFC_PRIMARIES || [];

  function closeAllInlineMenus() {
    document.querySelectorAll('.inline-dropdown-menu').forEach(function(m) { m.remove(); });
    document.querySelectorAll('.inline-dropdown-trigger[aria-expanded="true"]')
      .forEach(function(t) { t.setAttribute('aria-expanded', 'false'); });
  }

  function positionInlineMenu(menu) {
    menu.classList.remove('inline-dropdown-menu-up');
    var rect = menu.getBoundingClientRect();
    if (rect.bottom > window.innerHeight - 8) {
      menu.classList.add('inline-dropdown-menu-up');
    }
  }

  function openMenu(trigger, options, currentValue, onSelect) {
    closeAllInlineMenus();
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
        closeAllInlineMenus();
      });
      menu.appendChild(item);
    });
    trigger.parentElement.appendChild(menu);
    trigger.setAttribute('aria-expanded', 'true');
    positionInlineMenu(menu);
  }

  document.addEventListener('click', function(e) {
    var catTrigger = e.target.closest('.cat-trigger');
    if (catTrigger) {
      e.stopPropagation();
      if (catTrigger.getAttribute('aria-expanded') === 'true') {
        closeAllInlineMenus();
        return;
      }
      openMenu(catTrigger, PRIMARIES, catTrigger.dataset.value, function(opt) {
        spendingPostOverride(catTrigger.dataset.txId, {
          category: opt.value, detailed: null,
        }).then(function(r) {
          if (r.ok) window.location.reload();
          else alert('Failed to save category');
        });
      });
      return;
    }

    var itemTrigger = e.target.closest('.item-trigger');
    if (itemTrigger) {
      e.stopPropagation();
      if (itemTrigger.getAttribute('aria-expanded') === 'true') {
        closeAllInlineMenus();
        return;
      }
      var primary = itemTrigger.dataset.primary;
      var itemOptions = [{value: '', label: '—'}].concat(TAXONOMY[primary] || []);
      openMenu(itemTrigger, itemOptions, itemTrigger.dataset.value, function(opt) {
        itemTrigger.querySelector('.inline-dropdown-label').textContent = opt.label;
        itemTrigger.dataset.value = opt.value;
        itemTrigger.closest('td').dataset.value = opt.value ? opt.label : '';
        spendingPostOverride(itemTrigger.dataset.txId, {
          detailed: opt.value || null,
        }).then(function(r) {
          if (!r.ok) alert('Failed to save item');
        });
      });
      return;
    }

    if (!e.target.closest('.inline-dropdown-menu')) closeAllInlineMenus();
  });
})();

// ---------------------------------------------------------------------------
// Kebab menu: Split, Reset
// ---------------------------------------------------------------------------
(function() {
  function closeAllMenus(except) {
    document.querySelectorAll('.tx-menu').forEach(function(m) {
      if (m !== except) m.remove();
    });
  }

  function buildMenu() {
    var menu = document.createElement('div');
    menu.className = 'tx-menu';
    menu.innerHTML =
      '<button class="tx-menu-item" data-action="split">Split</button>' +
      '<button class="tx-menu-item" data-action="dismiss">Dismiss</button>' +
      '<button class="tx-menu-item" data-action="reset">Reset to original</button>';
    return menu;
  }

  function buildSplitForm(amount) {
    return (
      '<form class="tx-form" data-form-type="split">' +
        '<label>Your share of $' + amount.toFixed(2) + '</label>' +
        '<div class="tx-form-row">' +
          '<label style="margin:0">$ you owe</label>' +
          '<input type="number" name="amount" min="0" step="0.01" placeholder="25.00" autofocus>' +
        '</div>' +
        '<div class="tx-form-actions">' +
          '<button type="button" class="cancel">Cancel</button>' +
          '<button type="submit" class="primary">Save</button>' +
        '</div>' +
      '</form>'
    );
  }

  function positionMenu(menu) {
    menu.classList.remove('tx-menu-up');
    var rect = menu.getBoundingClientRect();
    if (rect.bottom > window.innerHeight - 8) menu.classList.add('tx-menu-up');
  }

  document.addEventListener('click', function(e) {
    var kebab = e.target.closest('.kebab');
    if (kebab) {
      e.stopPropagation();
      var existing = kebab.parentElement.querySelector('.tx-menu');
      closeAllMenus(existing);
      if (existing) {
        existing.remove();
      } else {
        var menu = buildMenu();
        kebab.parentElement.appendChild(menu);
        positionMenu(menu);
      }
      return;
    }
    if (!e.target.closest('.tx-menu')) closeAllMenus();
  });

  document.addEventListener('click', function(e) {
    var item = e.target.closest('.tx-menu-item');
    if (!item) return;
    e.stopPropagation();
    var menu = item.closest('.tx-menu');
    var kebab = menu.parentElement.querySelector('.kebab');
    var txId = kebab.dataset.txId;
    var amount = parseFloat(kebab.dataset.amount);
    var action = item.dataset.action;

    if (action === 'split') {
      menu.innerHTML = buildSplitForm(amount);
      positionMenu(menu);
    } else if (action === 'dismiss') {
      spendingPostOverride(txId, { dismiss: true }).then(function(r) {
        if (r.ok) window.location.reload();
        else alert('Failed to dismiss');
      });
    } else if (action === 'reset') {
      spendingPostOverride(txId, { clear: true }).then(function(r) {
        if (r.ok) window.location.reload();
        else alert('Failed to reset');
      });
    }
  });

  document.addEventListener('click', function(e) {
    if (e.target.matches('.tx-form .cancel')) {
      e.stopPropagation();
      var menu = e.target.closest('.tx-menu');
      if (menu) menu.remove();
    }
  });

  document.addEventListener('submit', function(e) {
    var form = e.target.closest('.tx-form');
    if (!form) return;
    e.preventDefault();
    var menu = form.closest('.tx-menu');
    var kebab = menu.parentElement.querySelector('.kebab');
    var txId = kebab.dataset.txId;
    var origAmount = parseFloat(kebab.dataset.amount);
    var payload = {};

    if (form.dataset.formType === 'split') {
      var amt = parseFloat(form.querySelector('input[name="amount"]').value);
      if (isNaN(amt) || amt <= 0) {
        alert('Enter a dollar amount.');
        return;
      }
      if (amt > origAmount) {
        alert('Your share cannot exceed the original amount of $' + origAmount.toFixed(2) + '.');
        return;
      }
      payload.amount = amt;
      payload.split_percentage = null;
    }

    spendingPostOverride(txId, payload).then(function(r) {
      if (r.ok) window.location.reload();
      else alert('Failed to save');
    });
  });
})();

// Month-picker dropdown and stacked-bar tooltip live in layout.js now.

// ---------------------------------------------------------------------------
// Sortable tables: each <th data-sort="number|date|string"> becomes a click
// target; cells may carry a data-value attribute holding the raw value.
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
